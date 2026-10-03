"""Estimated cover travel is separate from command echoes and confirmed state."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.opus_greennet.cover import OpusGreenNetCover
from tests.ha_helpers import configure_bridge, wait_for_entity
from tests.test_coordinator_transport import broker as broker
from tests.test_coordinator_transport import (
    connected_coordinator as connected_coordinator,
)
from tests.test_reconciliation_feedback import (
    ANSWER_TOPIC,
    MQTT_SHAPES,
    receive_feedback,
)
from tests.test_reconciliation_feedback import (
    feedback_coordinator as feedback_coordinator,
)
from tests.test_reconciliation_feedback import (
    timers as timers,
)


@pytest.fixture
def motion(feedback_coordinator, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(
        "custom_components.opus_greennet.coordinator.monotonic", lambda: clock[0]
    )
    channel = feedback_coordinator.devices["DEV1"].get_or_create_channel()
    channel.vertical_movement_time = 25
    channel.position = 100
    return feedback_coordinator, channel, clock


def entity_for(coord):
    entity = OpusGreenNetCover(coord, "AABB0011", "gateway", coord.devices["DEV1"])
    entity.async_write_ha_state = MagicMock()
    return entity


async def test_full_travel_keeps_reported_position_until_fallback(motion, timers):
    coord, channel, _ = motion
    entity = entity_for(coord)
    await entity.async_open_cover()
    assert channel.position == 100
    assert entity.is_opening is True
    assert entity.state == "opening"
    assert [delay for delay, _, _ in timers] == [30, 40, 50]
    timers[0][1](None)
    assert entity.is_opening is False
    assert entity.state == "open"
    assert channel.position == 0
    assert coord._pending_reconciliation_fields["DEV1", 0] == {"position"}
    assert not timers[1][2].called
    assert not timers[2][2].called


async def test_repeated_target_keeps_reported_position_and_original_deadline(motion):
    coord, channel, clock = motion
    entity = entity_for(coord)
    await entity.async_open_cover()
    original = coord._cover_movements["DEV1", 0]
    clock[0] += 5
    await entity.async_open_cover()
    assert channel.position == 100
    assert entity.state == "opening"
    assert coord._cover_movements["DEV1", 0] is original
    assert original.ends_at == 130


@pytest.mark.parametrize(
    "start,target,direction,delays",
    [
        (None, 0, "opening", [30, 40, 50]),
        (None, 100, "closing", [30, 40, 50]),
        (80, 20, "opening", [20, 30, 40]),
        (20, 80, "closing", [20, 30, 40]),
    ],
)
async def test_partial_and_unknown_start_travel(
    motion, timers, start, target, direction, delays
):
    coord, channel, _ = motion
    channel.position = start
    await coord.async_set_cover_position("DEV1", target)
    assert channel.movement == direction
    assert channel.position == start
    assert [delay for delay, _, _ in timers] == delays


async def test_retarget_uses_elapsed_estimate_and_cancels_old_timer(motion, timers):
    coord, channel, clock = motion
    await coord.async_set_cover_position("DEV1", 0)
    old = timers[:]
    clock[0] += 12.5
    await coord.async_set_cover_position("DEV1", 100)
    assert channel.movement == "closing"
    assert all(cancel.called for _, _, cancel in old)
    assert [delay for delay, _, _ in timers[3:]] == [17.5, 27.5, 37.5]
    replacement = coord._cover_movements["DEV1", 0]
    old[0][1](None)
    assert coord._cover_movements["DEV1", 0] is replacement
    assert channel.movement == "closing"


async def test_late_outbound_echo_does_not_finish_or_extend_movement(motion, timers):
    coord, channel, clock = motion
    await coord.async_set_cover_position("DEV1", 0)
    movement = coord._cover_movements["DEV1", 0]
    clock[0] += 3
    coord._handle_telegram_property_message(
        SimpleNamespace(
            topic="EnOcean/AABB0011/stream/telegram/DEV1/to",
            payload=json.dumps(
                {"direction": "to", "functions": [{"key": "position", "value": 0}]}
            ),
            retain=False,
        )
    )
    assert channel.position == 100
    assert coord._cover_movements["DEV1", 0] is movement
    assert movement.ends_at == 130
    assert [delay for delay, _, _ in timers[-2:]] == [37, 47]


@pytest.mark.parametrize("shape", MQTT_SHAPES)
async def test_numeric_feedback_ends_estimate_and_reconciliation(motion, timers, shape):
    coord, channel, _ = motion
    await coord.async_set_cover_position("DEV1", 0)
    pending = timers[:]
    receive_feedback(coord, [{"key": "position", "value": 40}], shape=shape)
    assert channel.position == 40
    assert channel.movement is None
    assert not coord._cover_movements
    assert not coord._pending_reconciliation_queries
    assert all(cancel.called for _, _, cancel in pending)


@pytest.mark.parametrize(
    "value", ["unknown", "notAvailable", None, True, -1, 101, float("nan")]
)
async def test_invalid_feedback_does_not_end_estimate(motion, timers, value):
    coord, channel, _ = motion
    await coord.async_set_cover_position("DEV1", 0)
    receive_feedback(coord, [{"key": "position", "value": value}])
    assert channel.movement == "opening"
    assert not timers[0][2].called
    assert not timers[1][2].called


async def test_feedback_before_ack_prevents_new_delayed_queries(motion, broker, timers):
    coord, channel, _ = motion
    broker.auto_respond = False
    task = asyncio.create_task(coord.async_set_cover_position("DEV1", 0))
    await asyncio.sleep(0)
    receive_feedback(coord, [{"key": "position", "value": 0}])
    broker.receive(ANSWER_TOPIC, {"header": {"httpStatus": 200}})
    await task
    assert channel.movement is None
    assert len(timers) == 1
    assert timers[0][2].called


async def test_stop_clears_motion_before_ack_and_preserves_stop_protocol(
    motion, broker, timers
):
    coord, channel, _ = motion
    await coord.async_set_cover_position("DEV1", 0)
    old = timers[:]
    broker.auto_respond = False
    task = asyncio.create_task(coord.async_stop_cover("DEV1"))
    await asyncio.sleep(0)
    assert channel.movement is None
    assert channel.position == 100  # Stop does not invent a final position.
    assert all(cancel.called for _, _, cancel in old)
    assert json.loads(broker.published[-1][1])["state"]["functions"] == [
        {"key": "stop", "value": "true"}
    ]
    broker.receive(ANSWER_TOPIC, {"header": {"httpStatus": 200}})
    await task
    assert [delay for delay, _, _ in timers[-2:]] == [5, 20]


async def test_rejected_command_does_not_clear_another_channels_motion(
    motion, broker, timers
):
    coord, channel, _ = motion
    other = coord.devices["DEV1"].get_or_create_channel(1)
    other.position = 100
    other.vertical_movement_time = 25
    await coord.async_set_cover_position("DEV1", 0, 1)
    broker.response = {"header": {"httpStatus": 400}}
    with pytest.raises(HomeAssistantError):
        await coord.async_set_cover_position("DEV1", 0, 0)
    assert channel.movement is None
    assert other.movement == "opening"
    assert ("DEV1", 0) not in coord._cover_movements
    assert ("DEV1", 1) in coord._cover_movements
    assert timers[-1][2].called


@pytest.mark.parametrize("operation", ["disconnect", "remove", "unload"])
async def test_cleanup_cancels_motion_and_queries(motion, timers, operation):
    coord, channel, _ = motion
    await coord.async_set_cover_position("DEV1", 0)
    if operation == "disconnect":
        coord._handle_bridge_status(SimpleNamespace(payload="0"))
    elif operation == "remove":
        coord.async_forget_device("DEV1")
    else:
        await coord.async_unload()
    assert not coord._cover_movements
    assert not coord._pending_reconciliation_queries
    assert channel.movement is None
    assert all(cancel.called for _, _, cancel in timers)


@pytest.mark.parametrize("configured", [None, 0])
async def test_unconfigured_covers_keep_existing_behavior(motion, timers, configured):
    coord, channel, _ = motion
    channel.vertical_movement_time = configured
    entity = entity_for(coord)
    await entity.async_open_cover()
    assert channel.position == 0
    assert entity.is_opening is None
    assert [delay for delay, _, _ in timers] == [5, 20]


@pytest.mark.parametrize(
    "value", [None, True, -1, float("nan"), float("inf"), "noChange"]
)
def test_invalid_travel_time_keeps_known_configuration(make_device, value):
    device = make_device("D2-05-00")
    channel = device.get_or_create_channel()
    channel.vertical_movement_time = 25
    device.update_from_telegram(
        {"functions": [{"key": "verticalMovementTime", "value": value}]}
    )
    assert channel.vertical_movement_time == 25


async def test_real_ha_cover_shows_motion_and_accepts_configuration_deltas(
    hass, mqtt_transport
):
    mqtt_transport.devices = [
        {
            "deviceId": "COVER",
            "eeps": [{"eep": "D2-05-00"}],
            "states": {"position": 100},
            "configuration": {
                "parameters": [{"key": "verticalMovementTime", "value": 25}]
            },
        }
    ]
    entry = (await configure_bridge(hass))["result"]
    entity = await wait_for_entity(hass, "cover", "AABB0011_COVER")
    channel = entry.runtime_data.coordinator.devices["COVER"].channels[0]
    assert channel.vertical_movement_time == 25
    await hass.services.async_call(
        "cover", "open_cover", {"entity_id": entity}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(entity).state == "opening"
    assert hass.states.get(entity).attributes["current_position"] == 0
    mqtt_transport.receive(
        "EnOcean/AABB0011/stream/telegram/COVER/from",
        {"functions": [{"key": "position", "value": 0}]},
    )
    await hass.async_block_till_done()
    assert hass.states.get(entity).state == "open"
    mqtt_transport.receive(
        "EnOcean/AABB0011/stream/device/COVER/configuration/parameters/0/value", "40"
    )
    await asyncio.sleep(0.04)
    await hass.async_block_till_done()
    assert channel.vertical_movement_time == 40


async def test_retarget_cancels_old_queries_before_ack(motion, broker, timers):
    coord, channel, clock = motion
    await coord.async_set_cover_position("DEV1", 0)
    old = timers[:]
    clock[0] += 10
    broker.auto_respond = False
    task = asyncio.create_task(coord.async_set_cover_position("DEV1", 100))
    await asyncio.sleep(0)
    assert channel.movement == "closing"
    assert all(cancel.called for _, _, cancel in old)
    broker.receive(ANSWER_TOPIC, {"header": {"httpStatus": 200}})
    await task
    assert [delay for delay, _, _ in timers[-2:]] == [25, 35]


async def test_queued_target_starts_only_when_published(motion, broker, timers):
    coord, channel, clock = motion
    broker.auto_respond = False
    first = asyncio.create_task(coord.async_set_cover_position("DEV1", 0))
    await asyncio.sleep(0)
    second = asyncio.create_task(coord.async_set_cover_position("DEV1", 100))
    await asyncio.sleep(0)
    assert channel.movement == "opening"
    assert len(broker.published) == 1
    clock[0] += 10
    broker.receive(ANSWER_TOPIC, {"header": {"httpStatus": 200}})
    await first
    await asyncio.sleep(0)
    assert len(broker.published) == 2
    assert channel.movement == "closing"
    assert coord._cover_movements["DEV1", 0].started_at == 110
    broker.receive(ANSWER_TOPIC, {"header": {"httpStatus": 200}})
    await second


@pytest.mark.parametrize("failure", ["cancel", "timeout"])
async def test_failed_request_clears_estimate(
    motion, broker, timers, monkeypatch, failure
):
    coord, channel, _ = motion
    monkeypatch.setattr(
        "custom_components.opus_greennet.mqtt_transport.REQUEST_TIMEOUT", 0.01
    )
    broker.auto_respond = False
    task = asyncio.create_task(coord.async_set_cover_position("DEV1", 0))
    await asyncio.sleep(0)
    assert channel.movement == "opening"
    if failure == "cancel":
        task.cancel()
    with pytest.raises((asyncio.CancelledError, HomeAssistantError)):
        await task
    assert channel.movement is None
    assert not coord._cover_movements
    assert not coord._pending_reconciliation_queries
    assert timers[0][2].called
    broker.assert_clean()


async def test_feedback_and_native_commands_are_channel_scoped(motion, timers):
    coord, channel, _ = motion
    other = coord.devices["DEV1"].get_or_create_channel(1)
    other.position = 100
    other.vertical_movement_time = 40
    await coord.async_set_cover_position("DEV1", 0)
    coord._handle_telegram_property_message(
        SimpleNamespace(
            topic="EnOcean/AABB0011/stream/telegram/DEV1/to",
            payload=json.dumps(
                {
                    "functions": [
                        {"key": "channel", "value": 1},
                        {"key": "position", "value": 0},
                    ]
                }
            ),
            retain=False,
        )
    )
    assert other.movement == "opening"
    assert other.position == 100
    assert [delay for delay, _, _ in timers[-3:]] == [45, 55, 65]
    receive_feedback(
        coord,
        [
            {"key": "channel", "value": 1},
            {"key": "position", "value": 0},
        ],
    )
    assert other.movement is None
    assert channel.movement == "opening"
    assert ("DEV1", 0) in coord._pending_reconciliation_queries
    assert ("DEV1", 1) not in coord._pending_reconciliation_queries


async def test_delayed_queries_publish_after_fallback(motion, broker, timers):
    from tests.test_reconciliation_feedback import assert_status_queries, fire_timer

    coord, channel, clock = motion
    await coord.async_set_cover_position("DEV1", 0)
    movement, first, second = timers[:]
    clock[0] = 130
    movement[1](None)
    assert channel.movement is None
    assert_status_queries(broker, 0)
    clock[0] = 140
    await fire_timer(coord, first)
    assert_status_queries(broker, 1)
    clock[0] = 150
    await fire_timer(coord, second)
    assert_status_queries(broker, 2)


def test_configured_travel_is_channel_scoped_and_ignores_default_values(motion):
    coord, channel, _ = motion
    device = coord.devices["DEV1"]
    functions = coord._configuration_state_functions(
        {
            "configuration": {
                "parameters": [
                    {"key": "verticalMovementTime", "defaultValue": 90},
                    {"key": "channel", "value": 1},
                    {"key": "verticalMovementTime", "value": "40"},
                    {"key": "channel", "value": 2},
                    {"key": "verticalMovementTime", "value": "12.5"},
                ]
            }
        }
    )
    device.update_from_telegram({"functions": functions})
    assert channel.vertical_movement_time == 25
    assert device.channels[1].vertical_movement_time == 40
    assert device.channels[2].vertical_movement_time == 12.5
