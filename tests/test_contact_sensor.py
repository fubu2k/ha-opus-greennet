"""D5-00-01 contact readings from gateway device lists and live telegrams."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.opus_greennet import protocol
from custom_components.opus_greennet.const import DOMAIN, EEP_MAPPINGS, KNOWN_STATE_KEYS
from tests.fake_gateway import FakeGateway, device_object, telegram
from tests.ha_helpers import setup_bridge, wait_for_entity, wait_until

EEP = "D5-00-01"


def test_contact_profile_is_discovered(make_device):
    device = make_device(EEP)
    assert EEP_MAPPINGS[EEP][0] == "binary_sensor"
    assert "contact" in KNOWN_STATE_KEYS
    assert device.is_contact_sensor
    assert not device.is_climate


@pytest.mark.parametrize("value,expected", [("open", True), ("closed", False)])
def test_contact_state_matches_the_gateway(make_device, value, expected):
    device = make_device(EEP)
    device.update_from_telegram({"functions": [{"key": "contact", "value": value}]})
    assert device.channels[0].contact is expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        "unknown",
        "notAvailable",
        "on",
        "off",
        "true",
        "false",
        True,
        False,
        0,
        1,
        [],
        {},
    ],
)
def test_unrecognized_contact_values_become_unknown(make_device, value):
    device = make_device(EEP)
    device.update_from_telegram({"functions": [{"key": "contact", "value": "open"}]})
    device.update_from_telegram({"functions": [{"key": "contact", "value": value}]})
    assert device.channels[0].contact is None


@pytest.mark.parametrize("eep", ["F6-05-01", "F6-10-00", "D1-4B-05", "D2-01-01"])
def test_contact_state_is_scoped_to_its_profile(make_device, eep):
    device = make_device(eep)
    device.update_from_telegram({"functions": [{"key": "contact", "value": "open"}]})
    assert device.channels[0].contact is None


@pytest.mark.parametrize(
    "states,expected",
    [
        ([{"key": "contact", "value": "closed"}], False),
        ([{"key": "contact", "value": "open"}], True),
        ([{"key": "contact", "value": "notAvailable"}], None),
    ],
)
def test_gateway_snapshot_applies_contact_state(make_device, states, expected):
    device = make_device(EEP)
    functions = protocol.device_state_functions(
        {"eeps": [{"eep": EEP}], "states": states}
    )
    device.update_from_telegram({"functions": functions})
    assert device.channels[0].contact is expected


@pytest.mark.parametrize("initial", [None, "closed", "open", "unknown"])
async def test_contact_entity_tracks_live_telegrams(
    hass: HomeAssistant, gateway: FakeGateway, initial: str | None
) -> None:
    gateway.devices = [
        device_object(
            "CONTACT1",
            EEP,
            friendly_id="Window",
            states=None if initial is None else [{"key": "contact", "value": initial}],
            batteryLevel=None,
            transmitModes=[{"key": "contact", "transmitOnEvent": True}],
        )
    ]
    await setup_bridge(hass, gateway)
    contact = await wait_for_entity(hass, "binary_sensor", "AABB0011_CONTACT1_contact")
    expected = {"open": "on", "closed": "off"}.get(initial, "unknown")
    state = hass.states.get(contact)
    assert state.state == expected
    assert state.attributes["device_class"] == "opening"
    registry = er.async_get(hass)
    entries = [
        entry
        for entry in registry.entities.values()
        if entry.platform == DOMAIN and entry.unique_id.startswith("AABB0011_CONTACT1_")
    ]
    assert [
        entry.entity_id for entry in entries if entry.domain == "binary_sensor"
    ] == [contact]
    assert not any("battery" in entry.unique_id for entry in entries)

    for value, expected in [
        ("open", "on"),
        ("closed", "off"),
        ("notAvailable", "unknown"),
    ]:
        await gateway.push(
            telegram(
                "CONTACT1",
                [{"key": "contact", "value": value}],
                telegramInfo={"rorg": "D5", "dbm": -70},
            )
        )
        await wait_until(lambda: hass.states.get(contact).state == expected)
    await gateway.stop()
    await wait_until(lambda: hass.states.get(contact).state == "unavailable")


async def test_contact_id_survives_reload_and_device_stream_updates(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [
        device_object("CONTACT1", EEP, states=[{"key": "contact", "value": "closed"}])
    ]
    entry = await setup_bridge(hass, gateway)
    contact = await wait_for_entity(hass, "binary_sensor", "AABB0011_CONTACT1_contact")
    assert hass.states.get(contact).state == "off"
    assert await hass.config_entries.async_reload(entry.entry_id)
    assert (
        await wait_for_entity(hass, "binary_sensor", "AABB0011_CONTACT1_contact")
        == contact
    )
    await wait_until(lambda: hass.states.get(contact).state == "off")
    await gateway.wait_for_stream()
    # A new stream starts with the current device list (full resync).
    gateway.devices = [
        device_object("CONTACT1", EEP, states=[{"key": "contact", "value": "open"}])
    ]
    await gateway.close_streams()
    await wait_until(lambda: hass.states.get(contact).state == "on")
    assert (
        er.async_get(hass).async_get_entity_id(
            "binary_sensor", DOMAIN, "AABB0011_CONTACT1_contact"
        )
        == contact
    )
