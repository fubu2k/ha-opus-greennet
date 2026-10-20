"""Phase 10: field-level freshness of retained replays (implementation plan s.12).

Within one uninterrupted, fully subscribed connection session every gateway
publication reaches the coordinator live (MQTT-3.3.1-9: established
subscriptions receive retain=0). A retained replay of a field that already
received a live value in that session therefore cannot be newer and must not
overwrite it. Everything else keeps its pre-phase-10 behaviour: the first
snapshot, unrelated fields, replays after an interruption and replays before
the session is armed still apply.

The tests marked "reproducer" failed on main e4bb5e9 (state was overwritten
by the stale replay or the fresh snapshot answer was suppressed).
"""

from __future__ import annotations

import asyncio
import json
from time import monotonic
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.opus_greennet import coordinator as coordinator_module
from custom_components.opus_greennet.coordinator import OpusGreenNetCoordinator
from tests.test_coordinator_transport import UPTIME_RESPONSE
from tests.test_coordinator_transport import broker as broker
from tests.test_coordinator_transport import (
    connected_coordinator as connected_coordinator,
)

BASE = "EnOcean/AABB0011"
SWITCH_MODEL = {
    "deviceId": "DEV1",
    "friendlyId": "Switch",
    "eeps": [{"eep": "D2-01-01"}],
    "states": [{"key": "switch", "value": "off"}],
}
TWO_CHANNEL_MODEL = {
    "deviceId": "DEV2",
    "friendlyId": "Two channels",
    "eeps": [{"eep": "D2-01-11"}],
    "states": [
        {"key": "channel", "value": 0},
        {"key": "switch", "value": "off"},
        {"key": "channel", "value": 1},
        {"key": "switch", "value": "off"},
    ],
}


@pytest.fixture
def coord(monkeypatch):
    coordinator = OpusGreenNetCoordinator(MagicMock(), "AABB0011")
    monkeypatch.setattr(f"{coordinator_module.__name__}.async_call_later", MagicMock())
    monkeypatch.setattr(
        f"{coordinator_module.__name__}.async_dispatcher_send", MagicMock()
    )
    return coordinator


def msg(topic: str, payload: object, *, retain: bool = False) -> SimpleNamespace:
    if not isinstance(payload, str | bytes):
        payload = json.dumps(payload)
    return SimpleNamespace(topic=f"{BASE}/{topic}", payload=payload, retain=retain)


def known(coord: OpusGreenNetCoordinator, model: dict) -> None:
    """Create a device from its cached device model, as discovery does."""
    device_id = model["deviceId"]
    coord._device_data[device_id] = json.loads(json.dumps(model))
    coord._create_device_from_data(device_id, coord._device_data[device_id])


def arm(coord: OpusGreenNetCoordinator) -> None:
    """All stream wildcards subscribed and the health probe answered."""
    coord._stream_topics = [
        pattern.format(base="EnOcean", eag_id=coord.eag_id)
        for pattern, _, _ in coord._stream_subscriptions()
    ]
    coord._arm_replay_guard()
    assert coord._replay_guard_armed


def live_delta(coord, device_id: str, path: str, value: str) -> None:
    coord._handle_device_stream_message(msg(f"stream/device/{device_id}/{path}", value))
    coord._finalize_device_stream(device_id)


def replay(coord, topic: str, value: str) -> None:
    """Deliver one retained replay and finish any delta window it opened."""
    if topic.startswith("stream/devices/"):
        coord._handle_device_property_message(msg(topic, value, retain=True))
    else:
        coord._handle_device_stream_message(msg(topic, value, retain=True))
    device_id = topic.split("/")[2]
    coord._finalize_device_stream(device_id)


def answer(coord, value: str) -> None:
    """Answer this session's own get/devices request (never retained)."""
    states = [{"key": "switch", "value": value}]
    coord._handle_get_answer_devices(
        msg(
            "getAnswer/devices",
            {
                "header": {"httpStatus": 200},
                "devices": [dict(SWITCH_MODEL, states=states)],
            },
        )
    )


def is_on(coord, device_id: str = "DEV1", channel: int = 0) -> bool | None:
    return coord.devices[device_id].channels[channel].is_on


# ── Reproducers: stale replay after a live value in the same session ──────


@pytest.mark.parametrize(
    "replay_topic",
    [
        "stream/devices/DEV1/states/switch",
        "stream/devices/DEV1/states/0/value",
        "stream/device/DEV1/states/0/value",
    ],
)
def test_retained_replay_cannot_overwrite_live_delta(coord, replay_topic) -> None:
    """Reproducer: live delta first, then an old retained snapshot field."""
    known(coord, SWITCH_MODEL)
    arm(coord)
    live_delta(coord, "DEV1", "states/0/value", "on")
    assert is_on(coord) is True

    replay(coord, replay_topic, "off")

    assert is_on(coord) is True
    # The stale value does not enter the cached device model either, so a
    # later rediscovery cannot reapply it.
    assert coord._device_data["DEV1"]["states"][0]["value"] == "on"


def test_retained_replay_cannot_overwrite_live_telegram(coord) -> None:
    """Reproducer: a live radio telegram is newer than any in-session replay."""
    known(coord, SWITCH_MODEL)
    arm(coord)
    coord._handle_telegram_property_message(
        msg(
            "stream/telegram/DEV1/from",
            {"functions": [{"key": "switch", "value": "on"}]},
        )
    )
    assert is_on(coord) is True

    replay(coord, "stream/devices/DEV1/states/0/value", "off")

    assert is_on(coord) is True


def test_protection_is_per_channel(coord) -> None:
    """Only the live channel is protected; the other channel still updates."""
    known(coord, TWO_CHANNEL_MODEL)
    arm(coord)
    coord._handle_telegram_property_message(
        msg(
            "stream/telegram/DEV2/from",
            {
                "functions": [
                    {"key": "channel", "value": "1"},
                    {"key": "switch", "value": "on"},
                ]
            },
        )
    )
    assert is_on(coord, "DEV2", 1) is True

    replay(coord, "stream/devices/DEV2/states/1/value", "on")  # channel 0
    replay(coord, "stream/devices/DEV2/states/3/value", "off")  # channel 1

    assert is_on(coord, "DEV2", 0) is True
    assert is_on(coord, "DEV2", 1) is True


def test_unrelated_and_unknown_fields_still_apply(coord) -> None:
    """A live switch value protects only the switch field."""
    model = dict(
        SWITCH_MODEL,
        states=[
            {"key": "switch", "value": "off"},
            {"key": "localControl", "value": "off"},
            {"key": "vendorSpecific", "value": "a"},
        ],
    )
    known(coord, model)
    arm(coord)
    live_delta(coord, "DEV1", "states/0/value", "on")

    replay(coord, "stream/devices/DEV1/states/1/value", "on")
    replay(coord, "stream/devices/DEV1/states/2/value", "b")

    channel = coord.devices["DEV1"].channels[0]
    assert channel.local_control is True
    assert coord._device_data["DEV1"]["states"][2]["value"] == "b"
    assert coord.devices["DEV1"].last_update_source == "stream/device (retained)"


def test_live_message_turns_a_replay_window_live(coord) -> None:
    """A delta window counts as live as soon as one live message joins it."""
    model = dict(
        SWITCH_MODEL,
        states=[
            {"key": "switch", "value": "off"},
            {"key": "localControl", "value": "off"},
        ],
    )
    known(coord, model)
    coord._handle_device_property_message(
        msg("stream/devices/DEV1/states/1/value", "on", retain=True)
    )
    assert coord._device_stream_replay_windows == {"DEV1"}
    coord._handle_device_stream_message(msg("stream/device/DEV1/states/0/value", "on"))
    coord._finalize_device_stream("DEV1")

    device = coord.devices["DEV1"]
    assert device.last_update_source == "stream/device"
    assert device.last_update_received_monotonic is not None
    assert coord._device_stream_replay_windows == set()


def test_scalar_fast_path_is_protected_per_field(coord) -> None:
    known(coord, SWITCH_MODEL)
    arm(coord)
    coord._handle_device_stream_message(msg("stream/device/DEV1/batteryLevel", "80"))

    replay(coord, "stream/device/DEV1/batteryLevel", "20")
    replay(coord, "stream/device/DEV1/dbm", "-70")

    device = coord.devices["DEV1"]
    assert device.channels[0].battery_level == 80
    assert device.dbm == -70
    assert device.last_update_source == "stream/device (retained)"


def test_answer_to_own_request_is_not_overwritten_before_discovery(coord) -> None:
    """Reproducer: a replay between snapshot answer and discovery debounce."""
    known(coord, SWITCH_MODEL)
    arm(coord)
    coord._resync_requested_at = monotonic()
    answer(coord, "on")

    replay(coord, "stream/devices/DEV1/states/0/value", "off")
    coord._finalize_discovery()

    assert is_on(coord) is True
    assert coord._device_data["DEV1"]["states"][0]["value"] == "on"


# ── The resync rule compares only live updates (no arming needed) ─────────


def test_retained_replay_does_not_suppress_resync_answer(coord) -> None:
    """Reproducer: a replay after the resync request no longer counts as live."""
    known(coord, SWITCH_MODEL)
    coord._resync_requested_at = monotonic()

    replay(coord, "stream/devices/DEV1/states/switch", "off")
    device = coord.devices["DEV1"]
    assert device.last_update_source == "stream/devices (retained)"
    assert device.last_update_received_monotonic is None

    answer(coord, "on")
    coord._finalize_discovery()

    assert is_on(coord) is True


def test_live_update_after_resync_request_still_wins(coord) -> None:
    """Existing rule unchanged: a live value after the request beats the answer."""
    known(coord, SWITCH_MODEL)
    coord._resync_requested_at = monotonic()
    coord._handle_device_property_message(
        msg("stream/devices/DEV1/states/switch", "on")
    )

    answer(coord, "off")
    coord._finalize_discovery()

    assert is_on(coord) is True


# ── Unchanged behaviour where the replay may be newer ─────────────────────


def test_first_snapshot_and_replays_before_arming_still_apply(coord) -> None:
    known(coord, SWITCH_MODEL)
    # Not armed: stream wildcards are still being subscribed (setup phase 2).
    live_delta(coord, "DEV1", "states/0/value", "on")
    assert coord._live_fields == {}

    replay(coord, "stream/devices/DEV1/states/0/value", "off")

    assert is_on(coord) is False


def test_first_snapshot_of_a_device_without_live_fields_applies(coord) -> None:
    known(coord, SWITCH_MODEL)
    arm(coord)

    replay(coord, "stream/devices/DEV1/states/0/value", "on")

    assert is_on(coord) is True


def test_retained_discovery_of_new_device_is_unchanged(coord) -> None:
    arm(coord)
    coord._handle_device_property_message(
        msg("stream/devices/NEW1/eeps/0/eep", "D2-01-01", retain=True)
    )
    coord._handle_device_property_message(
        msg("stream/devices/NEW1/states/switch", "on", retain=True)
    )
    coord._finalize_discovery()

    assert is_on(coord, "NEW1") is True


@pytest.mark.parametrize("interruption", ["broker", "bridge"])
def test_interruption_ends_the_session(coord, interruption) -> None:
    """Publications during an outage may be newer: replays apply afterwards."""
    known(coord, SWITCH_MODEL)
    arm(coord)
    live_delta(coord, "DEV1", "states/0/value", "on")
    assert coord._live_fields

    if interruption == "broker":
        coord._handle_connection_status(False)
    else:
        coord._handle_bridge_status(
            SimpleNamespace(topic="opus_greennet/AABB0011/bridge/status", payload="0")
        )

    assert coord._replay_guard_armed is False
    assert coord._live_fields == {}
    arm(coord)
    replay(coord, "stream/devices/DEV1/states/0/value", "off")
    assert is_on(coord) is False


def test_removed_device_starts_without_provenance(coord) -> None:
    known(coord, SWITCH_MODEL)
    arm(coord)
    live_delta(coord, "DEV1", "states/0/value", "on")

    coord._handle_device_property_message(
        msg("stream/devices/DEV1/states/switch", "on", retain=True)
    )
    coord._handle_device_stream_message(
        msg("stream/device/DEV1/states/9/value", "x", retain=True)
    )
    assert "DEV1" in coord._device_stream_replay_windows

    coord.async_forget_device("DEV1")

    assert "DEV1" not in coord._live_fields
    assert "DEV1" not in coord._device_stream_replay_windows
    known(coord, SWITCH_MODEL)
    replay(coord, "stream/devices/DEV1/states/0/value", "on")
    assert is_on(coord) is True


async def test_unload_clears_provenance(coord) -> None:
    known(coord, SWITCH_MODEL)
    arm(coord)
    live_delta(coord, "DEV1", "states/0/value", "on")

    await coord.async_unload()

    assert coord._replay_guard_armed is False
    assert coord._live_fields == {}


# ── Traffic and feedback parity (A1) ─────────────────────────────────────


def test_dropped_replay_keeps_previous_feedback_accounting(coord) -> None:
    """A1: a dropped replay still counts as feedback exactly as before.

    Previously the stale replay was applied and cancelled the pending
    reconciliation; it still cancels it, so no query is published that was
    not published before. Only the stale state value is no longer applied.
    """
    known(coord, SWITCH_MODEL)
    arm(coord)
    live_delta(coord, "DEV1", "states/0/value", "on")
    cancel = MagicMock()
    coord._pending_reconciliation_queries[("DEV1", 0)] = [cancel]
    coord._pending_reconciliation_fields[("DEV1", 0)] = {"switch"}
    coord.devices["DEV1"].last_command_error = "rejected"

    replay(coord, "stream/devices/DEV1/states/switch", "off")

    cancel.assert_called_once()
    assert ("DEV1", 0) not in coord._pending_reconciliation_queries
    assert coord.devices["DEV1"].last_command_error is None
    assert is_on(coord) is True


def test_dropped_replay_for_unknown_device_records_no_feedback(coord) -> None:
    arm(coord)
    coord._live_fields["NEW1"] = {(0, "switch")}
    record = MagicMock(return_value=True)
    coord._record_confirmed_feedback = record

    replay(coord, "stream/devices/NEW1/states/switch", "off")

    record.assert_not_called()
    assert "NEW1" not in coord._device_data


# ── Arming follows the real refresh path ─────────────────────────────────


async def test_refresh_arms_after_stream_subscriptions(broker, connected_coordinator):
    broker.response = UPTIME_RESPONSE
    coord = connected_coordinator
    coord._started = True
    assert coord._replay_guard_armed is False

    await coord._async_refresh_gateway(resync=True)

    assert coord._replay_guard_armed is True
    await coord.async_unload()
    assert coord._replay_guard_armed is False
    broker.assert_clean()


def test_guard_stays_disarmed_while_stream_subscriptions_are_missing(coord) -> None:
    coord._stream_topics = []
    coord._arm_replay_guard()
    assert coord._replay_guard_armed is False


async def test_probe_failure_does_not_arm(broker, connected_coordinator) -> None:
    """The bridge dropping during the probe leaves the session unarmed."""
    broker.auto_respond = False
    coord = connected_coordinator
    coord._resync_requested_at = 1.0
    coord._stream_topics = [""] * len(coord._stream_subscriptions())
    refresh = asyncio.create_task(coord._async_refresh_gateway(resync=False))
    for _ in range(10):
        await asyncio.sleep(0)
    coord._bridge_connected = False
    broker.receive(f"{BASE}/getAnswer/config/system/uptime", UPTIME_RESPONSE)
    with pytest.raises(HomeAssistantError):
        await refresh
    assert coord._replay_guard_armed is False
    await coord.async_unload()


# ── Field resolution ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("batteryLevel", (0, "batteryLevel")),
        ("dbm", (0, "dbm")),
        ("states/switch", (0, "switch")),
        ("states/vendorSpecific", None),
        ("states/0/value", (0, "switch")),
        ("states/0/key", None),
        ("states/9/value", None),
        ("friendlyId", None),
        ("configuration/parameters/0/value", None),
    ],
)
def test_property_field_resolution(coord, path, expected) -> None:
    known(coord, SWITCH_MODEL)
    assert coord._property_field("DEV1", path) == expected


@pytest.mark.parametrize(
    ("states", "index", "expected"),
    [
        (TWO_CHANNEL_MODEL["states"], 3, (1, "switch")),
        (TWO_CHANNEL_MODEL["states"], 2, None),
        ([{"key": "buttonA0", "value": "pressed"}], 0, None),
        ([{"key": "switch", "value": "on", "channel": "bad"}], 0, None),
        ([{"key": "switch", "value": "on", "channel": 2}], 0, (2, "switch")),
        ([{"value": "on"}], 0, None),
        (["not-an-object", {"key": "switch", "value": "on"}], 1, (0, "switch")),
    ],
)
def test_indexed_field_resolution(coord, states, index, expected) -> None:
    coord._device_data["DEV9"] = {"deviceId": "DEV9", "states": states}
    assert coord._property_field("DEV9", f"states/{index}/value") == expected


def test_buffered_key_resolves_before_the_window_finalizes(coord) -> None:
    coord._device_stream_data["DEV9"] = {
        "deviceId": "DEV9",
        "states": [{"key": "dimValue"}],
    }
    assert coord._property_field("DEV9", "states/0/value") == (0, "dimValue")


def test_smoke_detector_value_only_slots_resolve(coord) -> None:
    known(
        coord,
        {
            "deviceId": "RWM1",
            "eeps": [{"eep": "F6-05-02"}],
            "transmitModes": [{"value": "off"}, {"value": "ok"}],
        },
    )
    assert coord._property_field("RWM1", "transmitModes/0/value") == (
        0,
        "smokeAlarm",
    )
    assert coord._property_field("RWM1", "transmitModes/1/value") == (
        0,
        "batteryLow",
    )


def test_function_fields_follow_feedback_channel_rules() -> None:
    fields = OpusGreenNetCoordinator._function_fields(
        [
            {"key": "channel", "value": "1"},
            {"key": "switch", "value": "on"},
            {"key": "dimValue", "value": "5", "channel": 0},
            {"key": "buttonA0", "value": "pressed"},
            {"key": 5, "value": "x"},
            {"key": "position", "value": "1", "channel": "bad"},
        ]
    )
    assert fields == {(1, "switch"), (0, "dimValue")}
