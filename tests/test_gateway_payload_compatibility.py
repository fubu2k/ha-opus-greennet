"""Regression inputs reported in issues #42–#44 for IQ-DOT firmware 1.21.30.

The payloads now arrive as REST device objects (setup, stream resync,
periodic refresh) and as stream telegrams.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.opus_greennet.const import DOMAIN
from custom_components.opus_greennet.coordinator import OpusGreenNetCoordinator
from tests.fake_gateway import FakeGateway, device_object, telegram
from tests.ha_helpers import setup_bridge, wait_for_entity, wait_until


def _states(fields: dict) -> list[dict]:
    return [{"key": key, "value": value} for key, value in fields.items()]


# A5-07-01 reports no illumination (EEP profile); test_detector_support covers
# its other readings.
@pytest.mark.parametrize("eep", ["A5-07-03"])
async def test_presence_reported_payloads_reach_entities(
    hass: HomeAssistant, gateway: FakeGateway, eep: str
) -> None:
    states = {"motionDetected": True, "illumination": "200", "supplyVoltage": "3"}
    gateway.devices = [
        device_object("SMS", eep, states=_states(states), batteryLevel="85%")
    ]
    entry = await setup_bridge(hass, gateway)
    coordinator = entry.runtime_data.coordinator
    motion = await wait_for_entity(hass, "binary_sensor", "AABB0011_SMS_motion")
    illumination = await wait_for_entity(hass, "sensor", "AABB0011_SMS_illuminance")
    battery = await wait_for_entity(hass, "sensor", "AABB0011_SMS_battery_level")
    voltage = await wait_for_entity(hass, "sensor", "AABB0011_SMS_supply_voltage")
    assert hass.states.get(motion).state == "on"
    assert float(hass.states.get(illumination).state) == 200
    assert float(hass.states.get(battery).state) == 85
    assert float(hass.states.get(voltage).state) == 3

    # Telegrams report the original gateway keys.
    await gateway.push(
        telegram(
            "SMS",
            [
                {"key": "motionDetected", "value": False},
                {"key": "illumination", "value": "125"},
            ],
        )
    )
    await wait_until(lambda: hass.states.get(motion).state == "off")
    assert float(hass.states.get(illumination).state) == 125

    # Battery level is not streamed; the periodic refresh delivers it.
    gateway.devices = [
        device_object("SMS", eep, states=_states(states), batteryLevel=" 82.5 % ")
    ]
    await coordinator._async_refresh_device_list()
    await hass.async_block_till_done()
    assert float(hass.states.get(battery).state) == 82.5
    # ... without replaying the listed (older) motion state.
    assert hass.states.get(motion).state == "off"

    await gateway.push(
        telegram(
            "SMS",
            [
                {"key": "motionDetected", "value": "true"},
                {"key": "illumination", "value": 90},
            ],
        )
    )
    await wait_until(lambda: hass.states.get(motion).state == "on")
    assert float(hass.states.get(illumination).state) == 90

    await gateway.push(
        telegram(
            "SMS",
            [
                {"key": "motionDetected", "value": False},
                {"key": "illumination", "value": "notAvailable"},
            ],
        )
    )
    gateway.devices = [device_object("SMS", eep, batteryLevel="notAvailable")]
    await coordinator._async_refresh_device_list()
    await hass.async_block_till_done()
    assert hass.states.get(motion).state == "off"
    assert hass.states.get(illumination).state == "unknown"
    assert hass.states.get(battery).state == "unknown"


@pytest.mark.parametrize("delivery", ["device", "devices"])
async def test_rwm_original_alarm_key_and_boolean_battery(
    hass: HomeAssistant, gateway: FakeGateway, delivery: str
) -> None:
    gateway.devices = [
        device_object(
            "RWM",
            "F6-05-02",
            # Explicit keys take precedence over the positional fallback.
            transmitModes=[
                {"key": "batteryLow", "value": False},
                {"key": "alarm", "value": "off"},
            ],
        )
    ]
    await setup_bridge(hass, gateway)
    smoke = await wait_for_entity(hass, "binary_sensor", "AABB0011_RWM_smoke_alarm")
    battery = await wait_for_entity(hass, "binary_sensor", "AABB0011_RWM_battery_low")
    assert hass.states.get(smoke).state == "off"
    assert hass.states.get(battery).state == "off"

    # Value-only slots: index 0 is the smoke alarm, index 1 the battery.
    rwm = device_object(
        "RWM", "F6-05-02", transmitModes=[{"key": "", "value": "on"}, {"value": True}]
    )
    if delivery == "devices":
        gateway.devices = [rwm]
        await gateway.close_streams()
    else:
        await gateway.push({"header": {"content": "device"}, "device": rwm})
    await wait_until(lambda: hass.states.get(smoke).state == "on")
    assert hass.states.get(battery).state == "on"

    await gateway.push(telegram("RWM", [{"key": "alarm", "value": "off"}]))
    await wait_until(lambda: hass.states.get(smoke).state == "off")

    await gateway.push(
        telegram(
            "RWM",
            [
                {"key": "alarm", "value": "invalid"},
                {"key": "batteryLow", "value": "notAvailable"},
            ],
        )
    )
    await wait_until(lambda: hass.states.get(smoke).state == "unknown")
    assert hass.states.get(battery).state == "unknown"


@pytest.mark.parametrize("eep", ["D2-06-40", "F6-10-00", "D2-03-10"])
async def test_hoppe_original_keys_reach_position_entities(
    hass: HomeAssistant, gateway: FakeGateway, eep: str
) -> None:
    states = {
        "handle": "closed",
        "lock": "unlocked",
        "unlock": "notRequested",
        "mechanics": "ok",
    }
    entry = await setup_bridge(hass, gateway)
    if eep == "D2-06-40":
        # Unlock request and mechanics fault are disabled for NEW registry
        # entries. Simulate an existing installation in which the user keeps
        # them enabled, so their pushed states remain observable here.
        registry = er.async_get(hass)
        for domain, suffix in (
            ("sensor", "unlock_request"),
            ("binary_sensor", "mechanics_fault"),
        ):
            registry.async_get_or_create(
                domain, DOMAIN, f"AABB0011_HANDLE_{suffix}", config_entry=entry
            )
    gateway.devices = [device_object("HANDLE", eep, states=_states(states))]
    await gateway.push(gateway.devices_document())
    position = await wait_for_entity(hass, "sensor", "AABB0011_HANDLE_handle_state")
    tilt = await wait_for_entity(hass, "binary_sensor", "AABB0011_HANDLE_handle_tilt")
    assert hass.states.get(position).state == "closed"
    if eep == "D2-06-40":
        request = await wait_for_entity(
            hass, "sensor", "AABB0011_HANDLE_unlock_request"
        )
        assert hass.states.get(request).state == "not_requested"
        assert hass.states.get(request).attributes["device_class"] == "enum"

    await gateway.push(
        telegram(
            "HANDLE",
            [
                {"key": "handle", "value": "tilt"},
                {"key": "unlock", "value": "requested"},
                {"key": "mechanics", "value": "error"},
            ],
        )
    )
    await wait_until(lambda: hass.states.get(position).state == "tilted")
    assert hass.states.get(tilt).state == "on"
    channel = entry.runtime_data.coordinator.devices["HANDLE"].channels[0]
    if eep == "D2-06-40":
        bolt = await wait_for_entity(hass, "binary_sensor", "AABB0011_HANDLE_lock_bolt")
        fault = await wait_for_entity(
            hass, "binary_sensor", "AABB0011_HANDLE_mechanics_fault"
        )
        # LOCK device class: on = unlocked.
        assert hass.states.get(bolt).state == "on"
        assert channel.unlock_request is True
        assert hass.states.get(request).state == "requested"
        assert hass.states.get(fault).state == "on"
    else:
        assert channel.unlock_request is None
        assert channel.mechanics_fault is None

    await gateway.push(
        {
            "header": {"content": "device"},
            "device": device_object(
                "HANDLE", eep, states=_states({**states, "handle": "open"})
            ),
        }
    )
    await wait_until(lambda: hass.states.get(position).state == "open")


@pytest.mark.parametrize(
    "key,eep,attr",
    [
        ("alarm", "F6-05-02", "smoke_alarm"),
        ("batteryLow", "F6-05-02", "battery_low"),
        ("motionDetected", "A5-07-03", "motion"),
        ("unlock", "D2-06-40", "unlock_request"),
        ("mechanics", "D2-06-40", "mechanics_fault"),
    ],
)
@pytest.mark.parametrize("value", [None, 0, 1, [], {}, "unknown", "notAvailable"])
def test_unrecognized_boolean_values_stay_unknown(make_device, key, eep, attr, value):
    device = make_device(eep)
    device.update_from_telegram({"functions": [{"key": key, "value": value}]})
    assert getattr(device.channels[0], attr) is None


@pytest.mark.parametrize("value", ["0%", "100%", " 85.5 % ", "96%"])
def test_battery_percentage_model(make_device, value):
    device = make_device("A5-07-03")
    device.update_from_telegram(
        {"functions": [{"key": "batteryLevel", "value": value}]}
    )
    assert device.channels[0].battery_level == float(
        value.strip().removesuffix("%").strip()
    )


@pytest.mark.parametrize(
    "value", ["101%", "127%", "-1%", "nan%", "inf%", "85%%", True, "bad", None]
)
@pytest.mark.parametrize("authoritative", [True, False])
def test_bad_battery_percentage_does_not_replace_valid_state(
    make_device, value, authoritative
):
    coordinator = OpusGreenNetCoordinator(MagicMock(), MagicMock(), "AABB0011")
    device = make_device("A5-07-03")
    coordinator.devices[device.device_id] = device
    device.get_or_create_channel().battery_level = 75
    device.update_from_telegram(
        {"functions": [{"key": "batteryLevel", "value": value}]}
    )
    assert device.channels[0].battery_level == 75
    with patch("custom_components.opus_greennet.coordinator.async_dispatcher_send"):
        coordinator._apply_device_object(
            {
                "deviceId": device.device_id,
                "eeps": [{"eep": "A5-07-03"}],
                "batteryLevel": value,
            },
            authoritative=authoritative,
        )
    assert coordinator.devices[device.device_id].channels[0].battery_level == 75
