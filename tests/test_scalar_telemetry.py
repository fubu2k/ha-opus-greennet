"""Standalone telemetry bypasses multipart device state debounce."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.parametrize(
    "key,payload,expected",
    [
        ("batteryLevel", "85", 85),
        ("batteryLevel", "85%", 85),
        ("batteryLevel", b'" 82.5 % "', 82.5),
        ("batteryLevel", b'"82.5"', 82.5),
        ("dbm", "-61", -61),
        ("dbm", '"-70"', -70),
    ],
)
def test_scalar_updates_are_immediate(coordinator, make_device, key, payload, expected):
    device = make_device("A5-07-03")
    coordinator.devices[device.device_id] = device
    with (
        patch("custom_components.opus_greennet.coordinator.async_call_later") as timer,
        patch(
            "custom_components.opus_greennet.coordinator.async_dispatcher_send"
        ) as dispatch,
    ):
        message = SimpleNamespace(
            topic=f"EnOcean/AABB0011/stream/device/{device.device_id}/{key}",
            payload=payload,
        )
        coordinator._handle_device_stream_message(message)
        timer.assert_not_called()
        actual = device.dbm if key == "dbm" else device.channels[0].battery_level
        assert actual == expected
        dispatch.assert_called_once()
        coordinator._handle_device_stream_message(message)
        dispatch.assert_called_once()
    assert not coordinator._device_stream_data
    assert coordinator._device_data[device.device_id][key] == expected


@pytest.mark.parametrize(
    "key,value",
    [("batteryLevel", "101"), ("batteryLevel", "-1"), ("dbm", "nan"), ("dbm", "false")],
)
def test_invalid_scalar_is_ignored(coordinator, make_device, key, value):
    device = make_device("A5-07-03")
    coordinator.devices[device.device_id] = device
    assert coordinator._apply_scalar_telemetry(device.device_id, key, value, 1.0)
    assert device.dbm is None
    assert not device.channels


def test_scalar_preserves_structural_buffer_and_ignores_other_gateway(
    coordinator, make_device
):
    device = make_device("A5-07-03")
    coordinator.devices[device.device_id] = device
    pending = {"states": [{"key": "motionDetector", "value": "detected"}], "dbm": -80}
    coordinator._device_stream_data[device.device_id] = pending
    timer = MagicMock()
    coordinator._pending_device_streams[device.device_id] = timer
    coordinator._handle_device_stream_message(
        SimpleNamespace(
            topic=f"EnOcean/OTHER/stream/device/{device.device_id}/dbm", payload="-60"
        )
    )
    assert device.dbm is None
    coordinator._apply_scalar_telemetry(device.device_id, "dbm", "-60", 1.0)
    assert "states" in pending and "dbm" not in pending
    timer.assert_not_called()
    assert not coordinator._apply_scalar_telemetry("unknown", "dbm", "-50", 1.0)
