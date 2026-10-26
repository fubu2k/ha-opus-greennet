"""Telegram semantics: device reports, outbound commands and their effects.

Unit tests drive ``_handle_telegram``/``_handle_document`` with a mocked
``hass`` (dispatcher and timers patched); end-to-end tests push telegrams
through the fake gateway's event stream.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.opus_greennet.coordinator import (
    OUTBOUND_STATE_RECONCILIATION_DELAYS,
    SIGNAL_DEVICE_STATE_UPDATE,
    OpusGreenNetCoordinator,
)
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from tests.fake_gateway import FakeGateway, device_object, telegram
from tests.ha_helpers import setup_bridge, wait_for_entity, wait_until

MODULE = "custom_components.opus_greennet.coordinator"
STATE_SIGNAL = f"{SIGNAL_DEVICE_STATE_UPDATE}_AABB0011_DEV1"
TIMESTAMP = "2026-10-19T18:06:03.990+0200"


@pytest.fixture
def coord() -> OpusGreenNetCoordinator:
    """A coordinator with mocked hass whose background tasks are recorded."""
    coordinator = OpusGreenNetCoordinator(MagicMock(), MagicMock(), "AABB0011")
    coordinator._gateway_available = True
    coordinator.tasks = []

    def create_task(coroutine, name):
        coroutine.close()
        coordinator.tasks.append(name)
        return MagicMock()

    coordinator._create_task = create_task
    return coordinator


@pytest.fixture
def dispatch() -> Iterator[MagicMock]:
    with patch(f"{MODULE}.async_dispatcher_send") as mock:
        yield mock


@pytest.fixture
def call_later() -> Iterator[MagicMock]:
    with patch(
        f"{MODULE}.async_call_later", side_effect=lambda *args: MagicMock()
    ) as mock:
        yield mock


def _device(coord: OpusGreenNetCoordinator, eep: str, device_id: str = "DEV1"):
    device = EnOceanDevice(device_id, f"Device {device_id}", [{"eep": eep}])
    coord.devices[device_id] = device
    return device


def _telegram(functions, direction: str = "from", device_id: str = "DEV1", **fields):
    return {
        "deviceId": device_id,
        "friendlyId": "Device",
        "timestamp": TIMESTAMP,
        "direction": direction,
        "functions": functions,
        **fields,
    }


# ── Device reports (direction "from") ───────────────────────────────────────


def test_from_telegram_updates_state_and_metadata(coord, dispatch) -> None:
    device = _device(coord, "D2-01-02")
    coord._handle_telegram(
        _telegram(
            [{"key": "switch", "value": "on"}],
            telegramInfo={"rorg": "D2", "dbm": -61},
        )
    )
    assert device.channels[0].is_on is True
    assert device.last_update_source == "telegram/from"
    assert device.last_update_received_monotonic is not None
    assert device.last_update_dispatched_monotonic is not None
    assert device.last_seen == TIMESTAMP
    assert device.dbm == -61
    dispatch.assert_called_once_with(coord.hass, STATE_SIGNAL, device)


@pytest.mark.parametrize("info", [None, {}, {"dbm": "invalid"}, "junk"])
def test_missing_signal_keeps_previous_metadata(coord, dispatch, info) -> None:
    device = _device(coord, "D2-01-01")
    device.dbm = -70
    message = _telegram([{"key": "switch", "value": "on"}], telegramInfo=info)
    del message["timestamp"]
    device.last_seen = "earlier"
    coord._handle_telegram(message)
    assert device.dbm == -70
    assert device.last_seen == "earlier"


def test_from_telegram_applies_liquid_detection(coord, dispatch) -> None:
    device = _device(coord, "F6-05-01", "LEAK1")
    coord._handle_telegram(
        _telegram([{"key": "liquidDetected", "value": True}], device_id="LEAK1")
    )
    assert device.channels[0].liquid_detected is True
    assert device.last_update_source == "telegram/from"


@pytest.mark.parametrize("direction", ["from", "to"])
def test_dimmer_telegram_updates_state(coord, dispatch, call_later, direction):
    device = _device(coord, "D2-01-02")
    coord._handle_telegram(_telegram([{"key": "dimValue", "value": "50"}], direction))
    assert device.channels[0].brightness == 50
    assert device.channels[0].is_on is True
    assert device.last_update_source == f"telegram/{direction}"


def test_functions_single_object_form(coord, dispatch) -> None:
    """``functions`` may be one object instead of a list."""
    device = _device(coord, "D2-01-02")
    coord._handle_telegram(_telegram({"key": "dimValue", "value": "75"}))
    assert device.channels[0].brightness == 75
    dispatch.assert_called_once()


def test_interim_invalid_dim_value_keeps_state_and_error(coord, dispatch) -> None:
    """Dimmers report ``dimValue: "invalid"`` before the final value."""
    device = _device(coord, "D2-01-03")
    device.update_from_telegram({"functions": [{"key": "dimValue", "value": 30}]})
    device.last_command_error = "status 500: busy"
    cancel = MagicMock()
    coord._pending_reconciliation_queries[("DEV1", 0)] = [cancel]
    coord._pending_reconciliation_fields[("DEV1", 0)] = {"dimValue"}

    coord._handle_telegram(_telegram([{"key": "dimValue", "value": "invalid"}]))
    assert device.channels[0].brightness == 30
    assert device.last_command_error == "status 500: busy"
    cancel.assert_not_called()

    coord._handle_telegram(_telegram([{"key": "dimValue", "value": 80.0}]))
    assert device.channels[0].brightness == 80
    assert device.last_command_error is None
    cancel.assert_called_once()
    assert ("DEV1", 0) not in coord._pending_reconciliation_queries


def test_confirmed_from_cancels_pending_reconciliation(coord, dispatch) -> None:
    device = _device(coord, "D2-01-02")
    device.update_from_telegram({"functions": [{"key": "switch", "value": "on"}]})
    early, late = MagicMock(), MagicMock()
    coord._pending_reconciliation_queries[("DEV1", 0)] = [early, late]
    coord._reconcile_needed.add(("DEV1", 0))

    coord._handle_telegram(_telegram([{"key": "switch", "value": "off"}]))

    assert device.channels[0].is_on is False
    early.assert_called_once()
    late.assert_called_once()
    assert ("DEV1", 0) not in coord._pending_reconciliation_queries
    assert ("DEV1", 0) not in coord._reconcile_needed


def test_partial_feedback_keeps_reconciliation_for_missing_fields(
    coord, dispatch
) -> None:
    _device(coord, "D2-05-00")
    cancel = MagicMock()
    coord._pending_reconciliation_queries[("DEV1", 0)] = [cancel]
    coord._pending_reconciliation_fields[("DEV1", 0)] = {"position", "angle"}
    coord._handle_telegram(_telegram([{"key": "position", "value": 40}]))
    cancel.assert_not_called()
    assert coord._pending_reconciliation_fields[("DEV1", 0)] == {"angle"}
    coord._handle_telegram(_telegram([{"key": "angle", "value": 10}]))
    cancel.assert_called_once()


def test_embedded_channel_report_cancels_only_matching_reconciliation(
    coord, dispatch
) -> None:
    device = _device(coord, "D2-01-11")
    zero, one = MagicMock(), MagicMock()
    coord._pending_reconciliation_queries = {("DEV1", 0): [zero], ("DEV1", 1): [one]}
    coord._handle_telegram(_telegram([{"key": "switch", "value": "on", "channel": 1}]))
    assert device.channels[1].is_on is True
    assert 0 not in device.channels
    one.assert_called_once()
    zero.assert_not_called()


def test_consecutive_channel_telegrams_do_not_overwrite_each_other(
    coord, dispatch
) -> None:
    device = _device(coord, "D2-01-11")
    for channel in ("0", "1"):
        coord._handle_telegram(
            _telegram(
                [{"key": "channel", "value": channel}, {"key": "switch", "value": "on"}]
            )
        )
    assert device.channels[0].is_on is True
    assert device.channels[1].is_on is True
    assert dispatch.call_count == 2


def test_fast_press_release_keeps_both_events(coord, dispatch) -> None:
    device = _device(coord, "F6-02-01")
    events = []
    dispatch.side_effect = lambda hass, signal, dev: events.append(
        (dev.channels[0].last_button, dev.channels[0].last_button_action)
    )
    for action in ("pressed", "released"):
        coord._handle_telegram(_telegram([{"key": "buttonA0", "value": action}]))
    assert events == [("buttonA0", "pressed"), ("buttonA0", "released")]
    # The occurrence is consumed by the dispatch and never replayed.
    assert device.channels[0].last_button is None
    assert device.channels[0].last_button_action is None


def test_ignores_incomplete_functions(coord, dispatch) -> None:
    device = _device(coord, "D2-01-02")
    coord._handle_telegram(
        _telegram([{"key": "switch"}, {"value": "on"}, {"key": 5, "value": "on"}])
    )
    assert device.channels == {}
    dispatch.assert_not_called()


def test_applies_only_complete_functions(coord, dispatch) -> None:
    device = _device(coord, "D2-01-02")
    coord._handle_telegram(
        _telegram(
            [
                {"key": "localControl"},
                {"key": "switch", "value": "on"},
                {"value": "off"},
                {"key": "dimValue", "value": None},
                "junk",
            ]
        )
    )
    assert device.channels[0].is_on is True
    dispatch.assert_called_once()


@pytest.mark.parametrize(
    "message",
    [
        {},
        {"direction": "from", "functions": [{"key": "switch", "value": "on"}]},
        {"deviceId": 7, "direction": "from", "functions": []},
        _telegram([{"key": "switch", "value": "on"}], direction="sideways"),
        {"deviceId": "DEV1", "functions": [{"key": "switch", "value": "on"}]},
    ],
)
def test_telegram_without_device_or_direction_is_ignored(
    coord, dispatch, message
) -> None:
    device = _device(coord, "D2-01-02")
    coord._handle_telegram(message)
    assert device.channels == {}
    dispatch.assert_not_called()
    assert coord.tasks == []


@pytest.mark.parametrize(
    "document",
    [
        {"header": {"content": "telegram"}, "telegram": None},
        {"header": {"content": "telegram"}, "telegram": []},
        {"header": {"content": "telegram"}, "telegram": "not json"},
        {"header": {"content": "device"}, "device": ["DEV1"]},
        {"header": "telegram", "telegram": _telegram([])},
        {},
    ],
)
def test_invalid_documents_do_not_create_devices(coord, dispatch, document) -> None:
    coord._handle_document(document)
    assert coord.devices == {}
    assert coord.tasks == []
    dispatch.assert_not_called()


def test_telegram_document_is_applied(coord, dispatch) -> None:
    device = _device(coord, "D2-01-01")
    coord._handle_document(telegram("DEV1", [{"key": "switch", "value": "on"}]))
    assert device.channels[0].is_on is True


# ── Unknown devices ──────────────────────────────────────────────────────────


def test_unknown_device_is_looked_up_without_placeholder(coord, dispatch) -> None:
    coord._handle_telegram(_telegram([{"key": "switch", "value": "on"}], device_id="N"))
    coord._handle_telegram(_telegram([{"key": "switch", "value": "on"}], device_id="N"))
    assert coord.devices == {}
    assert coord.tasks == ["device lookup"]
    dispatch.assert_not_called()

    # Telegrams without usable state never trigger a lookup.
    coord._handle_telegram(_telegram([{"key": "switch"}], device_id="M"))
    coord._handle_telegram(
        _telegram([{"key": "query", "value": "status"}], "to", device_id="M")
    )
    assert coord.tasks == ["device lookup"]


def test_unknown_device_lookup_is_retried_after_interval(coord, dispatch) -> None:
    report = _telegram([{"key": "switch", "value": "on"}], device_id="N")
    with patch(f"{MODULE}.monotonic", return_value=1000.0):
        coord._handle_telegram(report)
    with patch(f"{MODULE}.monotonic", return_value=1000.0 + 3599):
        coord._handle_telegram(report)
    with patch(f"{MODULE}.monotonic", return_value=1000.0 + 3600):
        coord._handle_telegram(report)
    assert coord.tasks == ["device lookup", "device lookup"]


async def test_unknown_device_lookup_applies_the_gateway_object(dispatch) -> None:
    coord = OpusGreenNetCoordinator(MagicMock(), MagicMock(), "AABB0011")
    coord.client.async_get = AsyncMock(
        return_value={
            "header": {"httpStatus": 200, "content": "device"},
            "device": device_object(
                "NEW1", "D2-01-01", states=[{"key": "switch", "value": "on"}]
            ),
        }
    )
    await coord._async_lookup_device("NEW1")
    coord.client.async_get.assert_awaited_once_with("/devices/NEW1")
    assert coord.devices["NEW1"].channels[0].is_on is True
    assert dispatch.call_args.args[1] == "opus_greennet_device_discovered_AABB0011"

    # An answer without a device object changes nothing.
    coord.client.async_get = AsyncMock(return_value={"header": {"httpStatus": 200}})
    await coord._async_lookup_device("NEW2")
    assert "NEW2" not in coord.devices


async def test_device_discovered_later_resets_lookup_backoff(coord, dispatch) -> None:
    report = _telegram([{"key": "switch", "value": "on"}], device_id="N")
    coord._handle_telegram(report)
    assert "N" in coord._unknown_devices
    coord._apply_device_object(device_object("N", "D2-01-01"), authoritative=True)
    assert "N" not in coord._unknown_devices
    coord._handle_telegram(report)
    assert coord.devices["N"].channels[0].is_on is True


# ── Outbound commands (direction "to") ──────────────────────────────────────


def test_outbound_command_is_applied_and_reconciled(coord, dispatch) -> None:
    device = _device(coord, "D2-01-02")
    early, late = MagicMock(), MagicMock()
    with patch(f"{MODULE}.async_call_later", side_effect=[early, late]) as call_later:
        coord._handle_telegram(
            _telegram([{"key": "switch", "value": "on"}], direction="to")
        )
    assert device.channels[0].is_on is True
    assert device.last_update_source == "telegram/to"
    assert [call.args[1] for call in call_later.call_args_list] == list(
        OUTBOUND_STATE_RECONCILIATION_DELAYS
    )
    assert coord._pending_reconciliation_queries[("DEV1", 0)] == [early, late]
    assert coord._pending_reconciliation_fields[("DEV1", 0)] == {"switch"}


def test_outbound_command_updates_selected_channel(coord, dispatch, call_later):
    device = _device(coord, "D2-01-11")
    coord._handle_telegram(
        _telegram(
            [{"key": "channel", "value": "1"}, {"key": "switch", "value": "on"}],
            direction="to",
        )
    )
    assert 0 not in device.channels
    assert device.channels[1].is_on is True
    assert ("DEV1", 1) in coord._pending_reconciliation_queries
    assert ("DEV1", 0) not in coord._pending_reconciliation_queries


def test_outbound_query_does_not_change_state(coord, dispatch, call_later) -> None:
    """The real gateway streams its HomeKit status sweeps as ``to`` queries."""
    device = _device(coord, "D2-01-02")
    coord._handle_telegram(
        _telegram(
            [{"key": "query", "value": "status", "meaning": "Query status"}],
            direction="to",
            telegramInfo={"rorg": "D2", "data": "0300", "status": "0", "dbm": 0},
        )
    )
    assert device.channels == {}
    dispatch.assert_not_called()
    call_later.assert_not_called()


def test_outbound_command_keeps_only_target_state(coord, dispatch, call_later):
    device = _device(coord, "D2-01-02")
    device.update_from_telegram({"functions": [{"key": "localControl", "value": "on"}]})
    applied = []
    original = device.update_from_telegram
    device.update_from_telegram = lambda data: (
        applied.append(data["functions"]),
        original(data),
    )
    coord._handle_telegram(
        _telegram(
            [
                {"key": "dimValue", "value": "20"},
                {"key": "query", "value": "status"},
                {"key": "lockingMode", "value": "unblock"},
            ],
            direction="to",
        )
    )
    # The blind locking mode is a target state of D2-05 commands.
    assert applied == [
        [
            {"key": "dimValue", "value": "20"},
            {"key": "lockingMode", "value": "unblock"},
        ]
    ]
    assert device.channels[0].brightness == 20


def test_outbound_command_does_not_clear_command_error(coord, dispatch, call_later):
    device = _device(coord, "D2-01-01")
    device.last_command_error = "status 400: rejected"
    coord._handle_telegram(_telegram([{"key": "switch", "value": "on"}], "to"))
    assert device.last_command_error == "status 400: rejected"


def test_from_feedback_clears_command_error(coord, dispatch) -> None:
    device = _device(coord, "D2-01-01")
    device.last_command_error = "status 400: rejected"
    coord._handle_telegram(_telegram([{"key": "localControl", "value": "on"}]))
    assert device.last_command_error == "status 400: rejected"
    coord._handle_telegram(_telegram([{"key": "switch", "value": "off"}]))
    assert device.last_command_error is None


def test_own_unacknowledged_echo_is_ignored_but_reports_apply(
    coord, dispatch, call_later
) -> None:
    device = _device(coord, "D2-01-11")
    device.get_or_create_channel().is_on = False
    coord._command_waiters["DEV1"] = 1
    coord._handle_telegram(_telegram([{"key": "switch", "value": "on"}], "to"))
    assert device.channels[0].is_on is False
    call_later.assert_not_called()
    # Device reports are never suppressed.
    coord._handle_telegram(_telegram([{"key": "switch", "value": "on"}]))
    assert device.channels[0].is_on is True
    # Other devices' outbound telegrams are still applied.
    other = _device(coord, "D2-01-01", "DEV2")
    coord._handle_telegram(
        _telegram([{"key": "switch", "value": "on"}], "to", device_id="DEV2")
    )
    assert other.channels[0].is_on is True


def test_outbound_telegram_keeps_received_signal_strength(
    coord, dispatch, call_later
) -> None:
    device = _device(coord, "D2-01-01")
    device.dbm = -79
    coord._handle_telegram(
        _telegram(
            [{"key": "switch", "value": "on"}],
            direction="to",
            telegramInfo={"rorg": "D2", "data": "0100", "status": "0", "dbm": 0},
        )
    )
    assert device.channels[0].is_on is True
    assert device.dbm == -79


# ── Rotation metadata versus cover movement ─────────────────────────────────


@pytest.fixture
def cover(coord) -> tuple[EnOceanDevice, MagicMock]:
    """A cover awaiting movement confirmation with useful live state."""
    device = _device(coord, "D2-05-00")
    device.update_from_telegram(
        {"functions": [{"key": "position", "value": 20}, {"key": "angle", "value": 45}]}
    )
    device.last_command_error = "previous movement failed"
    cancel = MagicMock()
    coord._pending_reconciliation_queries[("DEV1", 0)] = [cancel]
    return device, cancel


@pytest.mark.parametrize("direction", ["from", "to"])
@pytest.mark.parametrize("rotation_value", ["noRotation", "noChange"])
def test_rotation_telegram_does_not_confirm_or_retry_movement(
    coord, cover, dispatch, call_later, direction, rotation_value
) -> None:
    device, cancel = cover
    device.channels[0].rotation_time = 0
    coord._handle_telegram(
        _telegram([{"key": "rotationTime", "value": rotation_value}], direction)
    )
    assert device.channels[0].rotation_time == 0
    assert device.last_command_error == "previous movement failed"
    assert coord._pending_reconciliation_queries[("DEV1", 0)] == [cancel]
    cancel.assert_not_called()
    call_later.assert_not_called()


@pytest.mark.parametrize("direction", ["from", "to"])
def test_mixed_rotation_and_position_telegram_still_reconciles_movement(
    coord, cover, dispatch, direction
) -> None:
    device, cancel = cover
    with patch(f"{MODULE}.async_call_later", return_value=MagicMock()) as call_later:
        coord._handle_telegram(
            _telegram(
                [
                    {"key": "rotationTime", "value": 0},
                    {"key": "position", "value": 70},
                ],
                direction,
            )
        )
    assert device.channels[0].rotation_time == 0
    # Without a movement time there is no travel estimate: applied directly.
    assert device.channels[0].position == 70
    cancel.assert_called_once()
    if direction == "to":
        assert [call.args[1] for call in call_later.call_args_list] == list(
            OUTBOUND_STATE_RECONCILIATION_DELAYS
        )
        assert device.last_command_error == "previous movement failed"
    else:
        call_later.assert_not_called()
        assert ("DEV1", 0) not in coord._pending_reconciliation_queries
        assert device.last_command_error is None


def test_outbound_cover_position_starts_travel_estimate(coord, dispatch) -> None:
    device = _device(coord, "D2-05-00")
    device.update_from_telegram(
        {
            "functions": [
                {"key": "position", "value": 0},
                {"key": "verticalMovementTime", "value": 40.0},
            ]
        }
    )
    with patch(
        f"{MODULE}.async_call_later", side_effect=lambda *args: MagicMock()
    ) as call_later:
        coord._handle_telegram(_telegram([{"key": "position", "value": "50"}], "to"))
    channel = device.channels[0]
    # The position follows the estimate, not the command target.
    assert channel.position == 0
    assert channel.movement == "closing"
    assert ("DEV1", 0) in coord._cover_movements
    delays = [call.args[1] for call in call_later.call_args_list]
    # Travel (half of 40 s) + grace, then status checks after the estimate.
    assert delays[0] == pytest.approx(25.0)
    assert delays[1:] == pytest.approx([35.0, 45.0], abs=0.5)

    # Position feedback ends the estimate.
    coord._handle_telegram(_telegram([{"key": "position", "value": 50}]))
    assert channel.position == 50
    assert channel.movement is None
    assert ("DEV1", 0) not in coord._cover_movements


@pytest.mark.parametrize(
    "function",
    [
        {"key": "position", "value": "unknown"},
        {"key": "position", "value": 50, "channel": -1},
    ],
)
def test_outbound_cover_command_without_usable_target(
    coord, dispatch, call_later, function
) -> None:
    device = _device(coord, "D2-05-00")
    device.update_from_telegram(
        {
            "functions": [
                {"key": "position", "value": 10},
                {"key": "verticalMovementTime", "value": 40.0},
            ]
        }
    )
    coord._handle_telegram(_telegram([function], "to"))
    assert coord._cover_movements == {}
    assert device.channels[0].position == 10
    assert device.channels[0].movement is None
    call_later.assert_not_called()


def test_outbound_stop_ends_travel_estimate(coord, dispatch, call_later) -> None:
    device = _device(coord, "D2-05-00")
    device.update_from_telegram(
        {"functions": [{"key": "verticalMovementTime", "value": 40.0}]}
    )
    coord._handle_telegram(_telegram([{"key": "position", "value": 100}], "to"))
    assert device.channels[0].movement == "closing"
    coord._handle_telegram(_telegram([{"key": "stop", "value": "true"}], "to"))
    assert device.channels[0].movement is None
    assert ("DEV1", 0) not in coord._cover_movements
    # Stop expects final position and tilt readings.
    assert coord._pending_reconciliation_fields[("DEV1", 0)] == {"position", "angle"}


# ── Debug logging ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("timestamp", "latency"),
    [(TIMESTAMP, "gateway_to_ha_ms="), ("not a time", "gateway_to_ha_ms=unknown")],
)
def test_dispatch_debug_log_reports_latency(
    coord, dispatch, caplog, timestamp, latency
) -> None:
    _device(coord, "D2-01-01")
    message = _telegram([{"key": "switch", "value": "on"}])
    message["timestamp"] = timestamp
    with caplog.at_level(logging.DEBUG, logger=MODULE):
        coord._handle_telegram(message)
    assert "OPUS telegram/from Device DEV1 (DEV1)" in caplog.text
    assert latency in caplog.text


# ── Command payloads ────────────────────────────────────────────────────────


@pytest.fixture
def commands() -> OpusGreenNetCoordinator:
    coordinator = OpusGreenNetCoordinator(MagicMock(), MagicMock(), "AABB0011")
    coordinator.async_send_command = AsyncMock()
    return coordinator


@pytest.mark.parametrize(
    ("call", "functions"),
    [
        (lambda c: c.async_turn_on("DEV1"), [{"key": "switch", "value": "on"}]),
        (
            lambda c: c.async_turn_on("DEV1", is_dimmable=True),
            [{"key": "dimValue", "value": "100"}],
        ),
        (
            lambda c: c.async_turn_on("DEV1", brightness=50, is_dimmable=True),
            [{"key": "dimValue", "value": "50"}],
        ),
        (lambda c: c.async_turn_off("DEV1"), [{"key": "switch", "value": "off"}]),
        (
            lambda c: c.async_turn_off("DEV1", is_dimmable=True),
            [{"key": "dimValue", "value": "0"}],
        ),
        (
            lambda c: c.async_turn_on("DEV1", channel=2),
            [{"key": "channel", "value": "2"}, {"key": "switch", "value": "on"}],
        ),
        (lambda c: c.async_stop_cover("DEV1"), [{"key": "stop", "value": "true"}]),
        (
            lambda c: c.async_set_cover_tilt("DEV1", 45),
            [{"key": "angle", "value": "45"}],
        ),
    ],
)
async def test_command_payloads(commands, call, functions) -> None:
    await call(commands)
    commands.async_send_command.assert_awaited_once_with("DEV1", functions)


async def test_status_query_payload(commands) -> None:
    await commands.async_query_device_status("DEV1")
    commands.async_send_command.assert_awaited_once_with(
        "DEV1",
        [{"key": "query", "value": "status"}],
        background=False,
        still_needed=None,
    )


# ── End to end through the event stream ─────────────────────────────────────


async def test_rocker_press_and_release_fire_events(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [device_object("ROCKER01", "F6-02-01")]
    entry = await setup_bridge(hass, gateway)
    entity_id = await wait_for_entity(hass, "event", "AABB0011_ROCKER01")
    events: list[str] = []

    def record(event) -> None:
        new_state = event.data["new_state"]
        if event.data["entity_id"] == entity_id and new_state is not None:
            events.append(new_state.attributes["event_type"])

    hass.bus.async_listen("state_changed", record)
    for action in ("pressed", "released"):
        await gateway.push(telegram("ROCKER01", [{"key": "buttonA0", "value": action}]))
    await wait_until(lambda: len(events) == 2)
    assert events == ["buttonA0_pressed", "buttonA0_released"]
    device = entry.runtime_data.coordinator.devices["ROCKER01"]
    assert device.channels[0].last_button is None


async def test_integrated_rocker_telegrams_leave_actuator_state(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    """Actuators with integrated rockers stream buttonA0 telegrams too."""
    gateway.devices = [
        device_object(
            "050BF714",
            ["D2-01-01", "F6-03-01"],
            states=[{"key": "switch", "value": "on"}],
        )
    ]
    await setup_bridge(hass, gateway)
    entity_id = await wait_for_entity(hass, "switch", "AABB0011_050BF714")
    for action in ("pressed", "released"):
        await gateway.push(telegram("050BF714", [{"key": "buttonA0", "value": action}]))
    await gateway.push(telegram("050BF714", [{"key": "switch", "value": "off"}]))
    await wait_until(lambda: hass.states.get(entity_id).state == "off")


async def test_per_function_channel_updates_one_entity(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [
        device_object(
            "0507BF73",
            ["D2-01-11", "F6-03-01"],
            states=[
                {"key": "switch", "channel": 1, "value": "off"},
                {"key": "switch", "channel": 0, "value": "off"},
            ],
        )
    ]
    await setup_bridge(hass, gateway)
    first = await wait_for_entity(hass, "switch", "AABB0011_0507BF73_ch0")
    second = await wait_for_entity(hass, "switch", "AABB0011_0507BF73_ch1")
    await gateway.push(
        telegram("0507BF73", [{"key": "switch", "value": "on", "channel": 1}])
    )
    await wait_until(lambda: hass.states.get(second).state == "on")
    assert hass.states.get(first).state == "off"


async def test_dimmer_interim_value_and_single_object_functions(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [
        device_object("DIM1", "D2-01-03", states=[{"key": "dimValue", "value": 0.0}])
    ]
    await setup_bridge(hass, gateway)
    entity_id = await wait_for_entity(hass, "light", "AABB0011_DIM1")
    assert hass.states.get(entity_id).state == "off"
    await gateway.push(telegram("DIM1", [{"key": "dimValue", "value": "invalid"}]))
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "off"
    await gateway.push(telegram("DIM1", {"key": "dimValue", "value": 100.0}))
    await wait_until(lambda: hass.states.get(entity_id).state == "on")
    assert hass.states.get(entity_id).attributes["brightness"] == 255


async def test_homekit_command_is_applied_then_verified(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    """A command from another controller updates the entity and is checked."""
    gateway.devices = [
        device_object(
            "050BF714", "D2-01-01", states=[{"key": "switch", "value": "off"}]
        )
    ]
    entry = await setup_bridge(hass, gateway)
    entity_id = await wait_for_entity(hass, "switch", "AABB0011_050BF714")
    timers = []
    with patch(
        f"{MODULE}.async_call_later",
        side_effect=lambda hass, delay, action: timers.append(action) or MagicMock(),
    ):
        await gateway.push(
            telegram(
                "050BF714",
                [{"key": "switch", "value": "on"}],
                direction="to",
                telegramInfo={"rorg": "D2", "dbm": 0},
            )
        )
        await wait_until(lambda: hass.states.get(entity_id).state == "on")
    assert len(timers) == len(OUTBOUND_STATE_RECONCILIATION_DELAYS)
    timers[0](None)
    await wait_until(
        lambda: (
            gateway.requests_to("PUT", "/devices/050BF714/state")
            == [{"state": {"functions": [{"key": "query", "value": "status"}]}}]
        )
    )
    # The device's report confirms the command; the later check is dropped.
    await gateway.push(telegram("050BF714", [{"key": "switch", "value": "on"}]))
    coordinator = entry.runtime_data.coordinator
    assert ("050BF714", 0) not in coordinator._pending_reconciliation_queries


# ── Outbound status queries (hardware: D5, 25 Oct 2026) ─────────────────────
# The gateway streams the status request that was sent to an actuator as a `to`
# telegram whose only function is `query` (value `status`). It describes no
# target state and must change nothing.


def test_outbound_status_query_changes_nothing(coord, dispatch, call_later) -> None:
    device = _device(coord, "D2-01-02")
    before = copy.deepcopy(device.channels)
    coord._handle_telegram(
        _telegram([{"key": "query", "value": "status"}], direction="to")
    )
    assert device.channels == before
    assert device.last_update_source != "telegram/to"
    dispatch.assert_not_called()
    call_later.assert_not_called()
    assert coord._pending_reconciliation_queries == {}
    assert coord.tasks == []


def test_outbound_status_query_for_an_unknown_device_starts_no_lookup(
    coord, dispatch
) -> None:
    coord._handle_telegram(
        _telegram(
            [{"key": "query", "value": "status"}], direction="to", device_id="NEW"
        )
    )
    assert coord.tasks == []
    assert "NEW" not in coord._unknown_devices


def test_a_state_in_the_same_outbound_telegram_is_still_applied(
    coord, dispatch, call_later
) -> None:
    device = _device(coord, "D2-01-02")
    coord._handle_telegram(
        _telegram(
            [{"key": "query", "value": "status"}, {"key": "switch", "value": "on"}],
            direction="to",
        )
    )
    assert device.channels[0].is_on is True
    assert device.last_update_source == "telegram/to"
