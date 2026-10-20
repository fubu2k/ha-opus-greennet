"""Cover stop uses the EEP command and reconciles the resulting position."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.opus_greennet.const import KEY_STOP, KNOWN_STATE_KEYS
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from tests.test_rest_commands import (
    connected_coordinator as connected_coordinator,
)
from tests.test_rest_commands import radio as radio

DEVICE_ID = "AABB1122"
STATE_PATH = f"/devices/{DEVICE_ID}/state"


@pytest.fixture
def stop_coordinator(connected_coordinator):
    """Use the real queue with only the gateway client replaced."""
    return connected_coordinator


@pytest.fixture
def reconciliation_timers(monkeypatch):
    """Capture delayed callbacks so their resulting requests can be exercised."""
    timers = []

    def call_later(hass, delay, callback):
        timers.append((delay, callback))
        return MagicMock()

    monkeypatch.setattr(
        "custom_components.opus_greennet.rest_coordinator.async_call_later", call_later
    )
    return timers


@pytest.mark.parametrize(
    ("eep", "channel", "status"),
    [("D2-05-00", 0, 200), ("D2-05-01", 1, 201), ("D2-05-02", 2, 200)],
)
async def test_stop_wire_command_waits_for_ack_then_queries_same_channel(
    radio, stop_coordinator, reconciliation_timers, eep, channel, status
):
    """The EEP's stop=true string gets two status checks only after acceptance."""
    device = EnOceanDevice(DEVICE_ID, "Cover", [{"eep": eep}])
    device_channel = device.get_or_create_channel(channel)
    device_channel.position = 42
    stop_coordinator.devices[DEVICE_ID] = device
    radio.auto_respond = False
    task = asyncio.create_task(stop_coordinator.async_stop_cover(DEVICE_ID, channel))
    try:
        await radio.wait_sent(1)
        selector = [{"key": "channel", "value": str(channel)}] if channel else []
        assert radio.sent == [
            (
                "PUT",
                STATE_PATH,
                {"state": {"functions": [*selector, {"key": "stop", "value": "true"}]}},
            )
        ]
        assert not task.done()
        assert not reconciliation_timers

        radio.respond({"header": {"httpStatus": status}})
        await task
        assert [delay for delay, _ in reconciliation_timers] == [5, 20]
        assert device_channel.position == 42
        assert KEY_STOP not in KNOWN_STATE_KEYS

        radio.auto_respond = True
        for _, callback in tuple(reconciliation_timers):
            callback(None)
            await asyncio.gather(*stop_coordinator._tasks)
        assert len(radio.sent) == 3
        for method, path, body in radio.sent[1:]:
            assert (method, path) == ("PUT", STATE_PATH)
            assert body == {
                "state": {"functions": [*selector, {"key": "query", "value": "status"}]}
            }
        assert len(reconciliation_timers) == 2
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("status", [400, 500])
async def test_rejected_stop_has_no_position_change_or_reconciliation(
    radio, stop_coordinator, reconciliation_timers, status
):
    """A rejected stop cannot claim success or create follow-up commands."""
    device = EnOceanDevice(DEVICE_ID, "Cover", [{"eep": "D2-05-02"}])
    channel = device.get_or_create_channel(1)
    channel.position = 42
    stop_coordinator.devices[DEVICE_ID] = device
    radio.response = {"header": {"httpStatus": status}}
    with pytest.raises(HomeAssistantError) as raised:
        await stop_coordinator.async_stop_cover(DEVICE_ID, 1)
    assert raised.value.translation_key == "request_rejected"
    assert channel.position == 42
    assert len(radio.sent) == 1
    assert not reconciliation_timers
    assert device.last_command_error == f"status {status}"
    assert not stop_coordinator._radio_queue.locked()


async def test_offline_stop_is_not_sent(radio, stop_coordinator, reconciliation_timers):
    """An unavailable gateway never receives a physical stop."""
    stop_coordinator._gateway_available = False
    with pytest.raises(HomeAssistantError) as raised:
        await stop_coordinator.async_stop_cover(DEVICE_ID, 1)
    assert raised.value.translation_key == "gateway_unavailable"
    assert not radio.sent
    assert not reconciliation_timers
