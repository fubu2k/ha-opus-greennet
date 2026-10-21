"""HOPPE SecureConnect AutoLock (D2-06-40) control and unlock requests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.opus_greennet.coordinator import CommandDelivery
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from custom_components.opus_greennet.lock import (
    LOCK_DEFERRED_PENDING_SECONDS,
    LOCK_PENDING_SECONDS,
    OpusGreenNetAutoLock,
)

LOCK_MODULE = "custom_components.opus_greennet.lock"


def _handle() -> EnOceanDevice:
    return EnOceanDevice("HANDLE", "Handle", [{"eep": "D2-06-40"}])


def _lock(delivery: CommandDelivery = CommandDelivery.SENT):
    coordinator = MagicMock(available=True)
    coordinator.async_set_handle_unlock = AsyncMock(return_value=delivery)
    device = _handle()
    entity = OpusGreenNetAutoLock(coordinator, "AABB0011", "gw", device)
    entity.hass = MagicMock()
    entity.async_write_ha_state = MagicMock()
    return entity, coordinator, device


@pytest.mark.parametrize(
    ("delivery", "seconds"),
    [
        (CommandDelivery.SENT, LOCK_PENDING_SECONDS),
        (CommandDelivery.DEFERRED, LOCK_DEFERRED_PENDING_SECONDS),
    ],
)
async def test_unlock_waits_for_the_handle_report(delivery, seconds) -> None:
    entity, coordinator, device = _lock(delivery)
    cancel = MagicMock()
    with patch(f"{LOCK_MODULE}.async_call_later", return_value=cancel) as later:
        await entity.async_unlock()
    coordinator.async_set_handle_unlock.assert_awaited_once_with("HANDLE", allowed=True)
    assert later.call_args.args[1] == seconds
    assert (entity.is_unlocking, entity.is_locking) == (True, False)
    assert entity.is_locked is None

    # A report that does not match keeps waiting; the target ends it.
    device.update_from_telegram({"functions": [{"key": "lock", "value": "locked"}]})
    with patch.object(OpusGreenNetAutoLock, "_visible_projection", return_value=None):
        entity._handle_state_update(device)  # noqa: SLF001
        assert entity.is_unlocking is True
        device.update_from_telegram(
            {"functions": [{"key": "lock", "value": "unlocked"}]}
        )
        entity._handle_state_update(device)  # noqa: SLF001
    assert entity.is_unlocking is False
    assert entity.is_locked is False
    cancel.assert_called_once()


async def test_lock_command_and_timeout() -> None:
    entity, coordinator, _ = _lock()
    with patch(f"{LOCK_MODULE}.async_call_later") as later:
        await entity.async_lock()
        coordinator.async_set_handle_unlock.assert_awaited_once_with(
            "HANDLE", allowed=False
        )
        assert entity.is_locking is True
        # Without a report the transition ends after the timeout.
        later.call_args.args[2](None)
    assert entity.is_locking is False
    assert entity.async_write_ha_state.call_count == 2


async def test_command_for_the_reported_state_needs_no_transition() -> None:
    entity, _, device = _lock()
    device.update_from_telegram({"functions": [{"key": "lock", "value": "locked"}]})
    with patch(f"{LOCK_MODULE}.async_call_later") as later:
        await entity.async_lock()
    later.assert_not_called()
    assert entity.is_locking is False
    assert entity.is_locked is True


async def test_removal_cancels_the_pending_timer() -> None:
    entity, _, _ = _lock()
    cancel = MagicMock()
    with patch(f"{LOCK_MODULE}.async_call_later", return_value=cancel):
        await entity.async_unlock()
    with patch(
        "homeassistant.helpers.entity.Entity.async_will_remove_from_hass",
        AsyncMock(),
    ):
        await entity.async_will_remove_from_hass()
    cancel.assert_called_once()
    assert entity.is_unlocking is False


def test_unlock_request_attribute() -> None:
    entity, _, device = _lock()
    assert entity.extra_state_attributes is None
    device.update_from_telegram(
        {"functions": [{"key": "unlock", "value": "requested"}]}
    )
    assert entity.extra_state_attributes == {"unlock_requested": True}


@pytest.mark.parametrize("answer", ["allowed", "notAllowed"])
def test_controller_answer_keeps_the_request_state(answer: str) -> None:
    device = _handle()
    device.update_from_telegram(
        {"functions": [{"key": "unlock", "value": "requested"}]}
    )
    # The answer of HA or another controller is no request of the handle.
    device.update_from_telegram({"functions": [{"key": "unlock", "value": answer}]})
    assert device.channels[0].unlock_request is True


async def test_coordinator_sends_the_unlock_answer(coordinator) -> None:
    coordinator.devices["HANDLE"] = _handle()
    await coordinator.async_set_handle_unlock("HANDLE", allowed=True)
    await coordinator.async_set_handle_unlock("HANDLE", allowed=False)
    assert [call.args for call in coordinator.async_send_command.await_args_list] == [
        ("HANDLE", [{"key": "unlock", "value": "allowed"}]),
        ("HANDLE", [{"key": "unlock", "value": "notAllowed"}]),
    ]
