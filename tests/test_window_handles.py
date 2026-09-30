"""Handle position and protected AutoLock behavior in Home Assistant."""

import asyncio

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from custom_components.opus_greennet.const import DOMAIN
from tests.ha_helpers import configure_bridge, wait_for_entity


@pytest.mark.parametrize("eep", ["D2-06-40", "F6-10-00", "D2-03-10"])
async def test_handle_positions_and_lock_safety(hass, mqtt_transport, eep):
    mqtt_transport.devices = [{"deviceId": "HANDLE", "eeps": [{"eep": eep}]}]
    await configure_bridge(hass)
    state = await wait_for_entity(hass, "sensor", "AABB0011_HANDLE_handle_state")
    positions = {}
    for suffix in ("open", "tilt", "closed"):
        positions[suffix] = await wait_for_entity(
            hass, "binary_sensor", f"AABB0011_HANDLE_handle_{suffix}"
        )
        assert hass.states.get(positions[suffix]).state == "unknown"
    assert "device_class" not in hass.states.get(positions["closed"]).attributes
    for value, expected in [
        ("open", "open"),
        ("tilt", "tilt"),
        ("closed", "closed"),
        ("tilted", "tilt"),
        ("invalid", None),
    ]:
        mqtt_transport.receive(
            "EnOcean/AABB0011/stream/device/HANDLE/states/0/key", "handleState"
        )
        mqtt_transport.receive(
            "EnOcean/AABB0011/stream/device/HANDLE/states/0/value", value
        )
        await asyncio.sleep(0.03)
        await hass.async_block_till_done()
        for position, entity_id in positions.items():
            assert hass.states.get(entity_id).state == (
                "unknown"
                if expected is None
                else "on"
                if expected == position
                else "off"
            )
        expected_state = (
            "unknown"
            if expected is None
            else "tilted"
            if expected == "tilt"
            else expected
        )
        assert hass.states.get(state).state == expected_state
    if eep == "D2-06-40":
        lock = await wait_for_entity(hass, "lock", "AABB0011_HANDLE_autolock")
        assert hass.states.get(lock).state == "unknown"
        mqtt_transport.receive(
            "EnOcean/AABB0011/stream/device/HANDLE/states/0/value", "closed"
        )
        await asyncio.sleep(0.03)
        await hass.async_block_till_done()
        assert hass.states.get(lock).state == "unknown"
        for value in ("unlocked", "locked"):
            mqtt_transport.receive(
                "EnOcean/AABB0011/stream/device/HANDLE/states/1/key", "lock"
            )
            mqtt_transport.receive(
                "EnOcean/AABB0011/stream/device/HANDLE/states/1/value", value
            )
            await asyncio.sleep(0.03)
            await hass.async_block_till_done()
            assert hass.states.get(lock).state == value
        before = list(mqtt_transport.published)
        for service in ("lock", "unlock"):
            with pytest.raises(HomeAssistantError) as error:
                await hass.services.async_call(
                    "lock", service, {"entity_id": lock}, blocking=True
                )
            assert error.value.translation_key == "autolock_read_only"
        assert mqtt_transport.published == before
    else:
        assert (
            er.async_get(hass).async_get_entity_id(
                "lock", DOMAIN, "AABB0011_HANDLE_autolock"
            )
            is None
        )
    await mqtt_transport.set_connected(False)
    for entity_id in positions.values():
        assert hass.states.get(entity_id).state == "unavailable"


@pytest.mark.parametrize(
    "value,expected",
    [
        (True, True),
        (False, False),
        ("true", True),
        ("false", False),
        ("bad", None),
        (None, None),
    ],
)
def test_handle_boolean_diagnostics(make_device, value, expected):
    device = make_device("D2-06-40")
    device.update_from_telegram(
        {
            "functions": [
                {"key": "unlockRequest", "value": value},
                {"key": "mechanicsFault", "value": value},
            ]
        }
    )
    assert device.channels[0].unlock_request is expected
    assert device.channels[0].mechanics_fault is expected
