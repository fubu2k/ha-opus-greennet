"""Handle position and protected AutoLock behavior in Home Assistant."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.opus_greennet.const import DOMAIN
from tests.fake_gateway import FakeGateway, device_object, telegram
from tests.ha_helpers import setup_bridge, wait_for_entity, wait_until


@pytest.mark.parametrize("eep", ["D2-06-40", "F6-10-00", "D2-03-10"])
async def test_handle_positions_and_lock_safety(
    hass: HomeAssistant, gateway: FakeGateway, eep: str
) -> None:
    gateway.devices = [device_object("HANDLE", eep)]
    await setup_bridge(hass, gateway)
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
        await gateway.push(telegram("HANDLE", [{"key": "handleState", "value": value}]))
        expected_state = (
            "unknown"
            if expected is None
            else "tilted"
            if expected == "tilt"
            else expected
        )
        await wait_until(lambda: hass.states.get(state).state == expected_state)
        for position, entity_id in positions.items():
            assert hass.states.get(entity_id).state == (
                "unknown"
                if expected is None
                else "on"
                if expected == position
                else "off"
            )
    if eep == "D2-06-40":
        lock = await wait_for_entity(hass, "lock", "AABB0011_HANDLE_autolock")
        assert hass.states.get(lock).state == "unknown"
        await gateway.push(
            telegram("HANDLE", [{"key": "handleState", "value": "closed"}])
        )
        await wait_until(lambda: hass.states.get(state).state == "closed")
        assert hass.states.get(lock).state == "unknown"
        # The reported lock-bolt is a binary sensor (on = unlocked); the lock
        # entity is the gateway's stored answer to the unlock request.
        bolt = await wait_for_entity(hass, "binary_sensor", "AABB0011_HANDLE_lock_bolt")
        for value, expected in (("unlocked", "on"), ("locked", "off")):
            await gateway.push(
                telegram(
                    "HANDLE",
                    [
                        {"key": "handleState", "value": "closed"},
                        {"key": "lock", "value": value},
                    ],
                )
            )
            await wait_until(lambda: hass.states.get(bolt).state == expected)
        assert hass.states.get(lock).state == "unknown"
        # The gateway stores the answer for the sleeping handle (status 202).
        gateway.responses["/devices/HANDLE/state"] = {"header": {"httpStatus": 202}}
        await hass.services.async_call(
            "lock", "unlock", {"entity_id": lock}, blocking=True
        )
        assert gateway.requests_to("PUT", "/devices/HANDLE/state")[-1] == {
            "state": {"functions": [{"key": "unlock", "value": "allowed"}]}
        }
        assert hass.states.get(lock).state == "unlocked"
        await hass.services.async_call(
            "lock", "lock", {"entity_id": lock}, blocking=True
        )
        assert gateway.requests_to("PUT", "/devices/HANDLE/state")[-1] == {
            "state": {"functions": [{"key": "unlock", "value": "notAllowed"}]}
        }
        assert hass.states.get(lock).state == "locked"
        # The answer the gateway sends the handle ("to") updates it as well.
        await gateway.push(
            telegram("HANDLE", [{"key": "unlock", "value": "allowed"}], direction="to")
        )
        await wait_until(lambda: hass.states.get(lock).state == "unlocked")
    else:
        assert (
            er.async_get(hass).async_get_entity_id(
                "lock", DOMAIN, "AABB0011_HANDLE_autolock"
            )
            is None
        )
    await gateway.stop()
    for entity_id in positions.values():
        await wait_until(lambda: hass.states.get(entity_id).state == "unavailable")


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
