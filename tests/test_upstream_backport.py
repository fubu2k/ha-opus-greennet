"""Upstream v0.7.0b1 device support validated against the fork's guardrails.

The upstream feature tests (test_contact_sensor.py, test_opus_motion_detector.py,
test_temperature_humidity_sensor.py) cover parsing and basic entity behaviour.
This module checks that the backported profiles also follow the architecture
and guardrails of this fork:

- A1: discovery, live telemetry and pushed updates send no device request.
- A4: retained device answers cannot supply readings for the new profiles.
- A5: stable unique IDs, child-device assignment and enabled defaults; the
  inventory of existing profiles (HeatArea, rocker switches) is unchanged.
- Phase 3: a motion press is one rocker event; later updates never replay it.
- Phase 4: unchanged readings and signal-strength-only updates skip writes.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity

from custom_components.opus_greennet.const import DOMAIN
from tests.ha_helpers import MQTTTransport, configure_bridge, wait_for_entity

BASE = "EnOcean/AABB0011"
MOTION_EEPS = [
    {"eep": "F6-02-01", "version": 0.9, "direction": "from"},
    {"eep": "A5-04-01", "version": 0.9, "direction": "from"},
]

# Profile -> (gateway definition, expected (domain, suffix) entities).
PROFILES = {
    "A5-04-01": (
        {"deviceId": "THS1", "eeps": [{"eep": "A5-04-01"}]},
        [
            ("sensor", "temperature"),
            ("sensor", "humidity"),
            ("sensor", "battery_level"),
        ],
    ),
    "A5-04-02": (
        {"deviceId": "THS2", "eeps": [{"eep": "A5-04-02"}]},
        [
            ("sensor", "temperature"),
            ("sensor", "humidity"),
            ("sensor", "battery_level"),
        ],
    ),
    "A5-04-03": (
        {"deviceId": "THS3", "eeps": [{"eep": "A5-04-03"}]},
        [
            ("sensor", "temperature"),
            ("sensor", "humidity"),
            ("sensor", "battery_level"),
        ],
    ),
    "D5-00-01": (
        {"deviceId": "CONTACT", "eeps": [{"eep": "D5-00-01"}]},
        [("binary_sensor", "contact")],
    ),
    "motion": (
        {"deviceId": "MOT1", "eeps": MOTION_EEPS},
        [
            ("binary_sensor", "motion"),
            ("sensor", "temperature"),
            ("sensor", "humidity"),
            ("sensor", "battery_level"),
        ],
    ),
}


def _device_requests(transport: MQTTTransport, device_id: str, start: int = 0):
    """Return published topics addressed to one device (potential radio traffic)."""
    return [
        topic
        for topic, _payload in transport.published[start:]
        if f"/{device_id}" in topic
    ]


def _telegram(transport: MQTTTransport, device_id: str, functions: list) -> None:
    transport.receive(
        f"{BASE}/stream/telegram/{device_id}/from", json.dumps({"functions": functions})
    )


async def _settle(hass) -> None:
    await asyncio.sleep(0.05)
    await hass.async_block_till_done()


async def _finalize(hass, coordinator) -> None:
    """Advance the discovery debounce, then publish actual HA entity state."""
    if coordinator._discovery_timer:
        coordinator._discovery_timer()
    coordinator._finalize_discovery()
    await hass.async_block_till_done()


@pytest.fixture
def writes():
    counts: dict[str, int] = {}
    original = Entity._async_write_ha_state

    def counting(self):
        counts[self.entity_id] = counts.get(self.entity_id, 0) + 1
        return original(self)

    with patch.object(Entity, "_async_write_ha_state", counting):
        yield counts


@pytest.mark.parametrize("profile", list(PROFILES))
async def test_new_profiles_register_stable_enabled_entities(
    hass, mqtt_transport, profile
):
    definition, expected = PROFILES[profile]
    device_id = definition["deviceId"]
    mqtt_transport.devices = [dict(definition, eeps=list(definition["eeps"]))]
    entry = (await configure_bridge(hass))["result"]
    registry = er.async_get(hass)
    child = None
    for domain, suffix in expected:
        entity_id = await wait_for_entity(
            hass, domain, f"AABB0011_{device_id}_{suffix}"
        )
        registry_entry = registry.async_get(entity_id)
        # Ordinary readings: enabled, not diagnostic, owned by this entry.
        assert registry_entry.disabled_by is None
        assert registry_entry.entity_category is None
        assert registry_entry.config_entry_id == entry.entry_id
        child = child or registry_entry.device_id
        assert registry_entry.device_id == child
        assert registry_entry.translation_key == suffix
    device = dr.async_get(hass).async_get(child)
    assert (DOMAIN, f"AABB0011_{device_id}") in device.identifiers
    assert device.via_device_id == entry.runtime_data.gateway_device_id
    # Signal strength keeps its existing diagnostic, disabled-by-default policy.
    rssi = registry.async_get_entity_id(
        "sensor", DOMAIN, f"AABB0011_{device_id}_signal_strength"
    )
    assert rssi is not None
    assert registry.async_get(rssi).disabled_by is er.RegistryEntryDisabler.INTEGRATION
    # The OPUS motion detector keeps its rocker event; other profiles get none.
    event = registry.async_get_entity_id("event", DOMAIN, f"AABB0011_{device_id}")
    assert (event is not None) is (profile == "motion")
    # Profiles without SMS telemetry get no illuminance/voltage entities.
    for suffix in ("illuminance", "supply_voltage"):
        assert (
            registry.async_get_entity_id(
                "sensor", DOMAIN, f"AABB0011_{device_id}_{suffix}"
            )
            is None
        )


@pytest.mark.parametrize(
    "eep,unexpected",
    [
        ("D1-4B-05", "temperature"),
        ("D1-4B-06", "temperature"),
        ("D1-4B-07", "temperature"),
        ("F6-02-01", "motion"),
        ("F6-03-01", "motion"),
    ],
)
async def test_existing_profiles_keep_their_inventory(
    hass, mqtt_transport, eep, unexpected
):
    """A5: the new readers must not add entities to established profiles."""
    mqtt_transport.devices = [{"deviceId": "OLD", "eeps": [{"eep": eep}]}]
    await configure_bridge(hass)
    await wait_for_entity(hass, "sensor", "AABB0011_OLD_signal_strength")
    registry = er.async_get(hass)
    for domain in ("sensor", "binary_sensor"):
        assert (
            registry.async_get_entity_id(domain, DOMAIN, f"AABB0011_OLD_{unexpected}")
            is None
        )
    if eep.startswith("D1-4B"):
        # HeatArea keeps its established humidity identity (single entry).
        humidity = "AABB0011_OLD_humidity"
        assert registry.async_get_entity_id("sensor", DOMAIN, humidity) is not None
        assert (
            len([e for e in registry.entities.values() if e.unique_id == humidity]) == 1
        )
    else:
        assert registry.async_get_entity_id("event", DOMAIN, "AABB0011_OLD")


@pytest.mark.parametrize("profile", list(PROFILES))
async def test_discovery_and_live_updates_send_no_device_request(
    hass, mqtt_transport, profile
):
    """A1: the new profiles are push-only; nothing queries the device."""
    definition, expected = PROFILES[profile]
    device_id = definition["deviceId"]
    mqtt_transport.devices = [dict(definition, eeps=list(definition["eeps"]))]
    await configure_bridge(hass)
    for domain, suffix in expected:
        await wait_for_entity(hass, domain, f"AABB0011_{device_id}_{suffix}")
    functions = {
        "D5-00-01": [{"key": "contact", "value": "open"}],
        "motion": [{"key": "buttonAI", "value": "pressed"}],
    }.get(
        profile,
        [
            {"key": "temperature", "value": 21.5},
            {"key": "humidity", "value": 45},
        ],
    )
    _telegram(mqtt_transport, device_id, functions)
    mqtt_transport.receive(f"{BASE}/stream/device/{device_id}/dbm", "-70")
    mqtt_transport.receive(f"{BASE}/stream/device/{device_id}/batteryLevel", '"80%"')
    await _settle(hass)
    assert _device_requests(mqtt_transport, device_id) == []


async def test_motion_press_is_one_event_and_never_replayed(hass, mqtt_transport):
    """Phase 3: the motion telegram is a rocker press and must not be replayed."""
    mqtt_transport.devices = [{"deviceId": "MOT1", "eeps": MOTION_EEPS}]
    await configure_bridge(hass)
    motion = await wait_for_entity(hass, "binary_sensor", "AABB0011_MOT1_motion")
    event = await wait_for_entity(hass, "event", "AABB0011_MOT1")
    assert hass.states.get(motion).state == "unknown"
    assert hass.states.get(event).state == "unknown"

    _telegram(mqtt_transport, "MOT1", [{"key": "buttonAI", "value": "pressed"}])
    await _settle(hass)
    first = hass.states.get(event)
    assert first.attributes["event_type"] == "buttonAI_pressed"
    assert hass.states.get(motion).state == "on"

    # Later updates of the same device must neither replay the press nor
    # change the motion state.
    mqtt_transport.receive(f"{BASE}/stream/device/MOT1/dbm", "-65")
    mqtt_transport.receive(f"{BASE}/stream/device/MOT1/batteryLevel", '"57%"')
    _telegram(mqtt_transport, "MOT1", [{"key": "temperature", "value": 18.5}])
    await _settle(hass)
    assert hass.states.get(event).state == first.state
    assert hass.states.get(motion).state == "on"

    # A release carries no motion information.
    _telegram(mqtt_transport, "MOT1", [{"key": "buttonAI", "value": "released"}])
    await _settle(hass)
    assert hass.states.get(motion).state == "on"

    # A real follow-up press is a new event and ends the motion.
    _telegram(mqtt_transport, "MOT1", [{"key": "buttonA0", "value": "pressed"}])
    await _settle(hass)
    assert hass.states.get(event).attributes["event_type"] == "buttonA0_pressed"
    assert hass.states.get(event).state != first.state
    assert hass.states.get(motion).state == "off"

    # Gateway outage: unavailable, never "no motion".
    await mqtt_transport.set_connected(False)
    assert hass.states.get(motion).state == "unavailable"


@pytest.mark.parametrize(
    "device_id,eeps,suffixes,functions,changed",
    [
        (
            "THS3",
            [{"eep": "A5-04-03"}],
            [("sensor", "temperature"), ("sensor", "humidity")],
            [{"key": "temperature", "value": 21.5}, {"key": "humidity", "value": 45}],
            [{"key": "temperature", "value": 22.0}],
        ),
        (
            "CONTACT",
            [{"eep": "D5-00-01"}],
            [("binary_sensor", "contact")],
            [{"key": "contact", "value": "closed"}],
            [{"key": "contact", "value": "open"}],
        ),
    ],
)
async def test_unchanged_readings_skip_redundant_state_writes(
    hass, mqtt_transport, writes, device_id, eeps, suffixes, functions, changed
):
    """Phase 4: only visible changes are written to the state machine."""
    mqtt_transport.devices = [{"deviceId": device_id, "eeps": eeps}]
    await configure_bridge(hass)
    entity_ids = [
        await wait_for_entity(hass, domain, f"AABB0011_{device_id}_{suffix}")
        for domain, suffix in suffixes
    ]
    _telegram(mqtt_transport, device_id, functions)
    await _settle(hass)
    before = {entity_id: writes.get(entity_id, 0) for entity_id in entity_ids}

    # Identical readings and signal-strength-only updates change nothing visible.
    for dbm in ("-60", "-61", "-62"):
        _telegram(mqtt_transport, device_id, functions)
        mqtt_transport.receive(f"{BASE}/stream/device/{device_id}/dbm", dbm)
        await _settle(hass)
    assert {entity_id: writes.get(entity_id, 0) for entity_id in entity_ids} == before

    # A real change is written exactly for the affected entity.
    _telegram(mqtt_transport, device_id, changed)
    await _settle(hass)
    assert writes.get(entity_ids[0], 0) == before[entity_ids[0]] + 1
    for entity_id in entity_ids[1:]:
        assert writes.get(entity_id, 0) == before[entity_id]


@pytest.mark.parametrize(
    "definition,unique_id,key,fresh,retained,expected_fresh",
    [
        (
            {"deviceId": "CONTACT", "eeps": [{"eep": "D5-00-01"}]},
            "AABB0011_CONTACT_contact",
            "contact",
            "open",
            "closed",
            "on",
        ),
        (
            {"deviceId": "THS3", "eeps": [{"eep": "A5-04-03"}]},
            "AABB0011_THS3_temperature",
            "temperature",
            21.5,
            -5.0,
            "21.5",
        ),
    ],
)
async def test_retained_answers_cannot_supply_new_profile_readings(
    hass, mqtt_transport, definition, unique_id, key, fresh, retained, expected_fresh
):
    """A4: an old retained device answer must not overwrite a fresh reading."""
    mqtt_transport.devices = [dict(definition, states=[])]
    result = await configure_bridge(hass)
    await hass.async_block_till_done()
    coordinator = result["result"].runtime_data.coordinator
    await _finalize(hass, coordinator)
    domain = "binary_sensor" if key == "contact" else "sensor"
    entity_id = await wait_for_entity(hass, domain, unique_id)
    assert hass.states.get(entity_id).state == "unknown"

    def answer(value, retain=False):
        mqtt_transport.receive(
            f"{BASE}/getAnswer/devices",
            {
                "header": {"httpStatus": 200},
                "devices": [dict(definition, states=[{"key": key, "value": value}])],
            },
            retain=retain,
        )

    # A retained answer alone never supplies a reading.
    answer(retained, retain=True)
    await _finalize(hass, coordinator)
    assert hass.states.get(entity_id).state == "unknown"

    answer(fresh)
    await _finalize(hass, coordinator)
    assert hass.states.get(entity_id).state == expected_fresh

    answer(retained, retain=True)
    await _finalize(hass, coordinator)
    assert hass.states.get(entity_id).state == expected_fresh
