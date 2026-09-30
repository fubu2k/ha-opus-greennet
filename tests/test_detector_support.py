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
