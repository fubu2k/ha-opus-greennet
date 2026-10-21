"""HOPPE SecureConnect AutoLock (D2-06-40): stored unlock answer and requests."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.opus_greennet import api, protocol
from custom_components.opus_greennet.binary_sensor import BOOLEAN_CHANNEL_STATES
from custom_components.opus_greennet.coordinator import CommandDelivery
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from custom_components.opus_greennet.lock import OpusGreenNetAutoLock

LOCK_MODULE = "custom_components.opus_greennet.lock"


@pytest.fixture(autouse=True)
def no_settling() -> Iterator[None]:
    """Send lock requests without the settle delay unless a test sets one."""
    with patch(f"{LOCK_MODULE}.LOCK_SETTLE_SECONDS", 0):
        yield


def _handle() -> EnOceanDevice:
    return EnOceanDevice("HANDLE", "Handle", [{"eep": "D2-06-40"}])


def _lock(delivery: CommandDelivery = CommandDelivery.DEFERRED):
    coordinator = MagicMock(available=True)
    coordinator.async_set_handle_unlock = AsyncMock(return_value=delivery)
    device = _handle()
    entity = OpusGreenNetAutoLock(coordinator, "AABB0011", "gw", device)
    entity.async_write_ha_state = MagicMock()
    return entity, coordinator, device


def _update(device: EnOceanDevice, key: str, value: str) -> None:
    device.update_from_telegram({"functions": [{"key": key, "value": value}]})


@pytest.mark.parametrize("delivery", list(CommandDelivery))
async def test_lock_and_unlock_set_the_stored_answer(delivery) -> None:
    entity, coordinator, device = _lock(delivery)
    assert entity.is_locked is None
    await entity.async_unlock()
    assert entity.is_locked is False
    await entity.async_lock()
    assert entity.is_locked is True
    assert [c.kwargs for c in coordinator.async_set_handle_unlock.await_args_list] == [
        {"allowed": True},
        {"allowed": False},
    ]
    assert device.channels[0].unlock_allowed is False
    # Each request: once when it starts settling, once when it is sent.
    assert entity.async_write_ha_state.call_count == 4


async def test_newer_report_wins_over_the_command() -> None:
    entity, coordinator, device = _lock()

    async def answer(*_args, **_kwargs):
        _update(device, "unlock", "allowed")
        return CommandDelivery.DEFERRED

    coordinator.async_set_handle_unlock = AsyncMock(side_effect=answer)
    await entity.async_lock()
    assert entity.is_locked is False


async def test_quick_changes_send_only_the_last_request() -> None:
    entity, coordinator, _ = _lock()
    with patch(f"{LOCK_MODULE}.LOCK_SETTLE_SECONDS", 0.05):
        first = asyncio.create_task(entity.async_lock())
        await asyncio.sleep(0)
        assert (entity.is_locking, entity.is_unlocking) == (True, False)
        second = asyncio.create_task(entity.async_unlock())
        await asyncio.sleep(0)
        assert (entity.is_locking, entity.is_unlocking) == (False, True)
        await asyncio.gather(first, second)
    coordinator.async_set_handle_unlock.assert_awaited_once_with("HANDLE", allowed=True)
    assert (entity.is_locked, entity.is_unlocking) == (False, False)


async def test_failed_request_ends_settling() -> None:
    entity, coordinator, _ = _lock()
    coordinator.async_set_handle_unlock = AsyncMock(
        side_effect=HomeAssistantError("rejected")
    )
    with pytest.raises(HomeAssistantError):
        await entity.async_unlock()
    assert entity.is_unlocking is False
    assert entity.is_locked is None


def test_lock_follows_the_answer_not_the_bolt() -> None:
    entity, _, device = _lock()
    assert entity.extra_state_attributes is None
    _update(device, "lock", "locked")
    assert entity.is_locked is None
    assert entity.extra_state_attributes == {"bolt_locked": True}
    _update(device, "unlock", "notAllowed")
    _update(device, "unlock", "requested")
    assert entity.is_locked is True
    assert entity.extra_state_attributes == {
        "bolt_locked": True,
        "unlock_requested": True,
    }
    # The answer does not change the handle's request state.
    _update(device, "unlock", "allowed")
    assert device.channels[0].unlock_request is True
    assert entity.is_locked is False


@pytest.mark.parametrize(
    ("bolt", "expected"), [(None, None), (True, False), (False, True)]
)
def test_lock_bolt_sensor_is_on_when_unlocked(bolt, expected) -> None:
    device = _handle()
    channel = device.get_or_create_channel(0)
    channel.is_locked = bolt
    assert BOOLEAN_CHANNEL_STATES["bolt_unlocked"](channel) is expected


def test_target_state_of_the_device_list_is_read() -> None:
    functions = protocol.device_state_functions(
        {
            "eeps": [{"eep": "D2-06-40"}],
            "states": [{"key": "lock", "value": "locked"}],
            "targetState": [
                {"key": "unlock", "value": "notAllowed"},
                {"key": "other", "value": "x"},
                {"key": "unlock"},
                "bad",
            ],
        }
    )
    assert {"key": "unlock", "value": "notAllowed"} in functions
    assert not [f for f in functions if f.get("key") == "other"]
    device = _handle()
    device.update_from_telegram({"functions": functions})
    assert device.channels[0].unlock_allowed is False
    assert protocol.device_state_functions({"targetState": "bad"}) == []


@pytest.mark.parametrize("status", [200, 201, 202])
def test_stored_target_state_is_an_accepted_answer(status: int) -> None:
    document = api.decode_response(f'{{"header": {{"httpStatus": {status}}}}}')
    assert api.response_status(document) == status


def test_other_statuses_stay_errors() -> None:
    with pytest.raises(api.OpusResponseError):
        api.decode_response('{"header": {"httpStatus": 203}}')


async def test_coordinator_sends_the_unlock_answer(coordinator) -> None:
    coordinator.devices["HANDLE"] = _handle()
    await coordinator.async_set_handle_unlock("HANDLE", allowed=True)
    await coordinator.async_set_handle_unlock("HANDLE", allowed=False)
    assert [call.args for call in coordinator.async_send_command.await_args_list] == [
        ("HANDLE", [{"key": "unlock", "value": "allowed"}]),
        ("HANDLE", [{"key": "unlock", "value": "notAllowed"}]),
    ]
