"""Only usable device feedback can cancel delayed command status checks."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from custom_components.opus_greennet.enocean_device import EnOceanDevice
from tests.fake_gateway import telegram
from tests.test_rest_commands import (
    connected_coordinator as connected_coordinator,
)
from tests.test_rest_commands import radio as radio

STATE_PATH = "/devices/DEV1/state"


@pytest.fixture
def timers(monkeypatch):
    """Capture real scheduled callbacks and their cancellation handles."""
    scheduled = []

    def call_later(hass, delay, callback):
        cancel = MagicMock()
        scheduled.append((delay, callback, cancel))
        return cancel

    monkeypatch.setattr(
        "custom_components.opus_greennet.rest_coordinator.async_call_later", call_later
    )
    return scheduled


@pytest.fixture
async def feedback_coordinator(connected_coordinator, timers):
    """Keep the production queue, state parsers and timer callbacks."""
    connected_coordinator.devices["DEV1"] = EnOceanDevice(
        "DEV1", "Cover", [{"eep": "D2-05-02"}]
    )
    return connected_coordinator


def receive_feedback(coordinator, functions):
    """Deliver a device report as the event stream does."""
    coordinator._handle_document(telegram("DEV1", functions))


def query_timers(timers):
    """Return the scheduled 5 s and 20 s status checks."""
    return [timer for timer in timers if timer[0] in (5, 20)]


async def fire_timer(coordinator, timer):
    """Run an uncancelled callback and its owned request task to completion."""
    _, callback, cancel = timer
    assert not cancel.called
    callback(None)
    if coordinator._tasks:
        await asyncio.gather(*coordinator._tasks)


def assert_status_queries(radio, count, *, channel=0):
    """Inspect the sent requests rather than mocked helper calls."""
    queries = []
    for method, path, body in radio.sent:
        functions = body["state"]["functions"]
        if {"key": "query", "value": "status"} in functions:
            assert (method, path) == ("PUT", STATE_PATH)
            expected = [{"key": "query", "value": "status"}]
            if channel:
                expected.insert(0, {"key": "channel", "value": str(channel)})
            assert functions == expected
            queries.append(functions)
    assert len(queries) == count


@pytest.mark.parametrize(
    "value",
    [
        "unknown",
        "invalid",
        "notAvailable",
        None,
        float("nan"),
        float("inf"),
        float("-inf"),
        True,
        False,
        -1,
        101,
        50.5,
    ],
)
async def test_unusable_report_before_ack_and_between_checks_preserves_queries(
    radio, feedback_coordinator, timers, value
):
    """Interim or malformed positions cannot suppress either 5s or 20s checks."""
    coord = feedback_coordinator
    radio.auto_respond = False
    task = asyncio.create_task(coord.async_set_cover_position("DEV1", 77))
    try:
        await radio.wait_sent(1)
        assert not task.done()
        receive_feedback(coord, [{"key": "position", "value": value}])
        radio.respond()
        await task
        pending = query_timers(timers)
        assert [delay for delay, _, _ in pending] == [5, 20]

        radio.auto_respond = True
        await fire_timer(coord, pending[0])
        assert_status_queries(radio, 1)
        receive_feedback(coord, [{"key": "position", "value": value}])
        await fire_timer(coord, pending[1])
        assert_status_queries(radio, 2)
        assert len(query_timers(timers)) == 2
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_live_report_requires_numeric_position_feedback(
    radio, feedback_coordinator, timers
):
    """An interim stream report keeps the checks; a numeric one confirms."""
    coord = feedback_coordinator
    await coord.async_set_cover_position("DEV1", 77)
    pending = query_timers(timers)
    receive_feedback(coord, [{"key": "position", "value": "unknown"}])
    await fire_timer(coord, pending[0])
    assert_status_queries(radio, 1)
    receive_feedback(coord, [{"key": "position", "value": 77}])
    assert all(cancel.called for _, _, cancel in pending)
    assert coord.devices["DEV1"].channels[0].position == 77
    assert_status_queries(radio, 1)


@pytest.mark.parametrize(
    ("key", "command_value", "invalid_value"),
    [
        ("dimValue", 10, -1),
        ("dimValue", 10, 101),
        ("dimValue", 10, True),
        ("angle", 30, -1),
        ("angle", 30, 101),
        ("temperatureSetpoint", 21, -1),
        ("temperatureSetpoint", 21, 41),
        ("temperatureSetpoint", 21, True),
        ("switch", "on", "unknown"),
        ("switch", "on", True),
        ("heaterMode", "heating", "notAvailable"),
        ("heaterMode", "heating", True),
    ],
)
async def test_invalid_actuator_fields_cannot_cancel_checks(
    radio, feedback_coordinator, timers, key, command_value, invalid_value
):
    """Numeric bounds and explicit enum values apply to every command field."""
    coord = feedback_coordinator
    await coord.async_send_command("DEV1", [{"key": key, "value": command_value}])
    receive_feedback(coord, [{"key": key, "value": invalid_value}])
    pending = query_timers(timers)
    assert [delay for delay, _, _ in pending] == [5, 20]
    for timer in pending:
        await fire_timer(coord, timer)
    assert_status_queries(radio, 2)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("position", 0),
        ("position", 100),
        ("dimValue", 0),
        ("dimValue", 100),
        ("angle", 0),
        ("angle", 100),
        ("temperatureSetpoint", 0),
        ("temperatureSetpoint", 40),
        ("temperatureSetpoint", 20.5),
        ("switch", "off"),
        ("heaterMode", "autoOff"),
    ],
)
async def test_valid_actuator_feedback_before_ack_avoids_redundant_queries(
    radio, feedback_coordinator, timers, key, value
):
    """Usable reports, including zero and boundary values, confirm a pending PUT."""
    coord = feedback_coordinator
    radio.auto_respond = False
    task = asyncio.create_task(
        coord.async_send_command("DEV1", [{"key": key, "value": value}])
    )
    try:
        await radio.wait_sent(1)
        receive_feedback(coord, [{"key": key, "value": value}])
        radio.respond()
        await task
        assert query_timers(timers) == []
        assert_status_queries(radio, 0)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_feedback_confirms_only_its_own_channel_and_field(
    radio, feedback_coordinator, timers
):
    """An angle report cannot confirm position, nor a different channel's value."""
    coord = feedback_coordinator
    await coord.async_send_command(
        "DEV1",
        [
            {"key": "position", "value": 77, "channel": 0},
            {"key": "angle", "value": 30, "channel": 0},
            {"key": "position", "value": 66, "channel": 1},
        ],
    )
    zero, one = query_timers(timers)[:2], query_timers(timers)[2:]
    assert len(zero) == len(one) == 2
    receive_feedback(
        coord,
        [
            {"key": "position", "value": "unknown", "channel": 0},
            {"key": "angle", "value": 30, "channel": 0},
        ],
    )
    assert all(not cancel.called for _, _, cancel in [*zero, *one])
    receive_feedback(coord, [{"key": "position", "value": 66, "channel": 1}])
    assert all(cancel.called for _, _, cancel in one)
    assert all(not cancel.called for _, _, cancel in zero)
    await fire_timer(coord, zero[0])
    assert_status_queries(radio, 1)
    receive_feedback(coord, [{"key": "position", "value": 77, "channel": 0}])
    assert all(cancel.called for _, _, cancel in zero)


@pytest.mark.parametrize(
    "function",
    [
        {"key": "rotationTime", "value": 0},
        {"key": "localControl", "value": "on"},
        {"key": "lockingMode", "value": "unblock"},
        {"key": "stop", "value": "true"},
        {"key": "channel", "value": 0},
    ],
)
async def test_metadata_or_stop_receipt_does_not_confirm_position(
    radio, feedback_coordinator, timers, function
):
    """A received stop or configuration flag still leaves the position unknown."""
    coord = feedback_coordinator
    await coord.async_stop_cover("DEV1")
    receive_feedback(coord, [function])
    pending = query_timers(timers)
    assert [delay for delay, _, _ in pending] == [5, 20]
    for timer in pending:
        await fire_timer(coord, timer)
    assert_status_queries(radio, 2)


@pytest.mark.parametrize("rotation_time", [0, 1])
async def test_stop_waits_for_angle_only_when_target_channel_supports_tilt(
    radio, feedback_coordinator, timers, rotation_time
):
    """A shutter needs position; a tiltable channel also needs usable angle."""
    coord = feedback_coordinator
    device = coord.devices["DEV1"]
    device.get_or_create_channel(0).rotation_time = 1 - rotation_time
    device.get_or_create_channel(1).rotation_time = rotation_time
    await coord.async_stop_cover("DEV1", channel=1)
    pending = query_timers(timers)
    receive_feedback(
        coord,
        [
            {"key": "position", "value": 77, "channel": 1},
            {"key": "angle", "value": "unknown", "channel": 1},
        ],
    )
    if rotation_time == 0:
        assert all(cancel.called for _, _, cancel in pending)
        assert_status_queries(radio, 0)
    else:
        for timer in pending:
            await fire_timer(coord, timer)
        assert_status_queries(radio, 2, channel=1)
        receive_feedback(coord, [{"key": "angle", "value": 30, "channel": 1}])
        assert all(cancel.called for _, _, cancel in pending)


async def test_later_tilt_command_keeps_unconfirmed_position_checks(
    radio, feedback_coordinator, timers
):
    """Resetting timers for a second field must retain the first field's checks."""
    coord = feedback_coordinator
    await coord.async_set_cover_position("DEV1", 77)
    await coord.async_set_cover_tilt("DEV1", 30)
    previous, current = query_timers(timers)[:2], query_timers(timers)[2:]
    assert len(previous) == len(current) == 2
    assert all(cancel.called for _, _, cancel in previous)
    receive_feedback(
        coord,
        [{"key": "position", "value": "unknown"}, {"key": "angle", "value": 30}],
    )
    for timer in current:
        await fire_timer(coord, timer)
    assert_status_queries(radio, 2)
    receive_feedback(coord, [{"key": "position", "value": 77}])
    assert all(cancel.called for _, _, cancel in current)


async def test_first_queued_commands_feedback_cannot_confirm_second_command(
    radio, feedback_coordinator, timers
):
    """Each command begins its feedback window only when it is actually sent."""
    coord = feedback_coordinator
    radio.auto_respond = False
    first = asyncio.create_task(coord.async_set_cover_position("DEV1", 77, channel=1))
    second = asyncio.create_task(coord.async_set_cover_position("DEV1", 78, channel=1))
    try:
        await radio.wait_sent(1)
        receive_feedback(coord, [{"key": "position", "value": 77, "channel": 1}])
        radio.respond()
        await first
        await radio.wait_sent(2)
        assert not second.done()
        assert query_timers(timers) == []

        radio.respond()
        await second
        pending = query_timers(timers)
        assert [delay for delay, _, _ in pending] == [5, 20]
        radio.auto_respond = True
        for timer in pending:
            await fire_timer(coord, timer)
        assert_status_queries(radio, 2, channel=1)
    finally:
        for task in (first, second):
            if not task.done():
                task.cancel()
        await asyncio.gather(first, second, return_exceptions=True)
