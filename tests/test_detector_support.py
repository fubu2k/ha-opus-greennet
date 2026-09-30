"""Sensor profile regressions using MQTT messages and real HA entities."""

import asyncio

import pytest
from homeassistant.helpers import entity_registry as er

from custom_components.opus_greennet.const import DOMAIN
from custom_components.opus_greennet.coordinator import OpusGreenNetCoordinator
from tests.ha_helpers import configure_bridge, wait_for_entity


@pytest.mark.parametrize("stream", ["device", "devices"])
async def test_rwm_discovery_and_live_value_only_updates(hass, mqtt_transport, stream):
    mqtt_transport.devices = [
        {
            "deviceId": "RWM1",
            "friendlyId": "Smoke detector",
            "eeps": [{"eep": "F6-05-02"}],
            "transmitModes": [{"value": "idle"}, {"value": "ok"}],
        }
    ]
    await configure_bridge(hass)
    smoke = await wait_for_entity(hass, "binary_sensor", "AABB0011_RWM1_smoke_alarm")
    battery = await wait_for_entity(hass, "binary_sensor", "AABB0011_RWM1_battery_low")
    assert hass.states.get(smoke).state == "off"
    assert hass.states.get(battery).state == "off"
    for index, value in [(0, "alarm"), (1, "low")]:
        mqtt_transport.receive(
            f"EnOcean/AABB0011/stream/{stream}/RWM1/transmitModes/{index}/value", value
        )
    await asyncio.sleep(0.05)
    await hass.async_block_till_done()
    assert hass.states.get(smoke).state == "on"
    assert hass.states.get(battery).state == "on"
    mqtt_transport.receive(
        f"EnOcean/AABB0011/stream/{stream}/RWM1/transmitModes/0/value", "notAvailable"
    )
    await asyncio.sleep(0.05)
    await hass.async_block_till_done()
    assert hass.states.get(smoke).state == "unknown"
    await mqtt_transport.set_connected(False)
    assert hass.states.get(battery).state == "unavailable"
    registry = er.async_get(hass)
    assert (
        len(
            [
                e
                for e in registry.entities.values()
                if e.platform == DOMAIN and e.unique_id.endswith("smoke_alarm")
            ]
        )
        == 1
    )


def test_rwm_fallback_is_profile_scoped():
    data = {"transmitModes": [{"value": "alarm"}, {"value": "low"}]}
    assert OpusGreenNetCoordinator._device_state_functions(data) == []
    data["eeps"] = [{"eep": "F6-05-02"}]
    assert OpusGreenNetCoordinator._device_state_functions(data) == [
        {"key": "smokeAlarm", "value": "alarm"},
        {"key": "batteryLow", "value": "low"},
    ]


@pytest.mark.parametrize(
    "value,expected",
    [("alarm", True), ("idle", False), (None, None), ("garbage", None)],
)
def test_smoke_states_are_explicit(make_device, value, expected):
    device = make_device("F6-05-02")
    device.update_from_telegram({"functions": [{"key": "smokeAlarm", "value": value}]})
    assert device.channels[0].smoke_alarm is expected


@pytest.mark.parametrize("eep", ["A5-07-01", "A5-07-03"])
async def test_presence_indexed_states_and_telemetry(hass, mqtt_transport, eep):
    mqtt_transport.devices = [{"deviceId": "SMS", "eeps": [{"eep": eep}]}]
    await configure_bridge(hass)
    motion = await wait_for_entity(hass, "binary_sensor", "AABB0011_SMS_motion")
    values = {}
    for suffix in ("illuminance", "supply_voltage", "battery_level"):
        values[suffix] = await wait_for_entity(hass, "sensor", f"AABB0011_SMS_{suffix}")
        assert hass.states.get(values[suffix]).state == "unknown"
    assert hass.states.get(motion).state == "unknown"
    for index, (key, value) in enumerate(
        [
            ("motionDetector", "detected"),
            ("illuminance", "200"),
            ("supplyVoltage", "3.2"),
            ("batteryLevel", "85"),
        ]
    ):
        for field, payload in (("value", value), ("key", key)):
            mqtt_transport.receive(
                f"EnOcean/AABB0011/stream/device/SMS/states/{index}/{field}", payload
            )
    await asyncio.sleep(0.05)
    await hass.async_block_till_done()
    assert hass.states.get(motion).state == "on"
    for suffix, value, unit in [
        ("illuminance", 200, "lx"),
        ("supply_voltage", 3.2, "V"),
        ("battery_level", 85, "%"),
    ]:
        state = hass.states.get(values[suffix])
        assert float(state.state) == value
        assert state.attributes["unit_of_measurement"] == unit
    mqtt_transport.receive(
        "EnOcean/AABB0011/stream/device/SMS/states/0/value", "noMotion"
    )
    mqtt_transport.receive("EnOcean/AABB0011/stream/device/SMS/batteryLevel", "82")
    await asyncio.sleep(0.05)
    await hass.async_block_till_done()
    assert hass.states.get(motion).state == "off"
    assert float(hass.states.get(values["battery_level"]).state) == 82


@pytest.mark.parametrize(
    "key,attr",
    [
        ("illuminance", "illuminance"),
        ("supplyVoltage", "supply_voltage"),
        ("batteryLevel", "battery_level"),
    ],
)
@pytest.mark.parametrize("value", ["nan", "inf", "-1", True, [], "bad"])
def test_presence_invalid_measurements_are_unknown(make_device, key, attr, value):
    device = make_device("A5-07-03")
    device.update_from_telegram({"functions": [{"key": key, "value": value}]})
    assert getattr(device.channels[0], attr) is None
