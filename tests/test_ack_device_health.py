"""ACK 201 (SoftSmartAck) semantics and per-device health (plan phase 11).

``httpStatus`` 200 means the gateway transmitted the telegram; 201 means it
queued the telegram for deferred delivery. Neither is device feedback. These
tests drive the real entities, coordinator, request manager and parsers
against the deterministic broker and pin:

* a deferred command applies no estimate, starts no cover travel estimate and
  schedules no status query (A1: fewer requests, never more);
* the deferred values stay visible (diagnostics) until a matching live device
  report or the gateway's outbound telegram shows delivery;
* the outbound telegram of the eventual delivery uses the existing
  bridge-native path with its usual two checks, so the per-command query
  budget never exceeds the one of a transmitted command;
* device-level warnings, rejections and timeouts never change gateway
  availability (A2).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.opus_greennet.climate import OpusGreenNetClimate
from custom_components.opus_greennet.coordinator import (
    ACK_STATUS_DEFERRED,
    CommandDelivery,
    OpusGreenNetCoordinator,
)
from custom_components.opus_greennet.cover import OpusGreenNetCover
from custom_components.opus_greennet.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from custom_components.opus_greennet.switch import OpusGreenNetSwitch
from tests.test_coordinator_transport import broker as broker
from tests.test_coordinator_transport import (
    connected_coordinator as connected_coordinator,
)
from tests.test_reconciliation_feedback import timers as timers
from tests.test_state_ownership import (
    COVER_SEED,
    EAG_ID,
    STATE_SIGNAL,
    SUCCESS_CASES,
    add_device,
    add_entity,
    channel_state,
    feedback,
    published_functions,
    query_delays,
)
from tests.test_state_ownership import coord as coord
from tests.test_state_ownership import dispatched as dispatched

DEFERRED = {"header": {"httpStatus": ACK_STATUS_DEFERRED}}
SENT = {"header": {"httpStatus": 200}}


def markers_for(functions: list[dict], channel_id: int) -> dict[int, dict[str, str]]:
    """The deferred values expected for one published command."""
    return {
        channel_id: {
            function["key"]: function["value"]
            for function in functions
            if function["key"] != "channel"
        }
    }


def outbound_echo(coord, functions: list[dict]) -> None:
    """The gateway's own telegram when it finally transmits a queued command."""
    coord._handle_telegram_property_message(
        SimpleNamespace(
            topic=f"EnOcean/{EAG_ID}/stream/telegram/DEV1/to",
            payload=json.dumps({"functions": functions}),
            retain=False,
        )
    )


def retained_snapshot(coord, key: str, value: str) -> None:
    coord._handle_device_property_message(
        SimpleNamespace(
            topic=f"EnOcean/{EAG_ID}/stream/devices/DEV1/states/{key}",
            payload=value,
            retain=True,
        )
    )


def active_query_timers(timers) -> list:
    return [timer for timer in timers if timer[0] in (5, 20) and not timer[2].called]


# ── Deferred commands: no estimate, no query ──────────────────────────────


@pytest.mark.parametrize(
    "entity_class,eep,seed,channel_id,action,kwargs,functions,changes",
    [case[1:] for case in SUCCESS_CASES],
    ids=[case[0] for case in SUCCESS_CASES],
)
async def test_deferred_command_keeps_reported_state_and_sends_no_query(
    coord,
    broker,
    timers,
    dispatched,
    entity_class,
    eep,
    seed,
    channel_id,
    action,
    kwargs,
    functions,
    changes,
):
    """O1: a 201 is not treated like 200; the last reported state stays."""
    broker.response = DEFERRED
    device = add_device(coord, eep, seed)
    entity = add_entity(coord, dispatched, entity_class, channel_id)
    before = {cid: channel_state(device, cid) for cid in device.channels}

    await getattr(entity, action)(**kwargs)

    assert published_functions(broker) == [functions]
    assert {cid: channel_state(device, cid) for cid in device.channels} == before
    entity.async_write_ha_state.assert_not_called()
    assert dispatched.signals == []
    assert query_delays(timers) == []
    assert len(broker.published) == 1
    assert device.deferred_commands == markers_for(functions, channel_id)
    assert device.last_command_error is None
    assert coord.available is True
    broker.assert_clean()


@pytest.mark.parametrize(
    ("status", "delivery"),
    [(200, CommandDelivery.SENT), (ACK_STATUS_DEFERRED, CommandDelivery.DEFERRED)],
)
async def test_send_command_reports_gateway_delivery(
    coord, broker, timers, status, delivery
):
    broker.response = {"header": {"httpStatus": status}}
    add_device(coord, "D2-01-01")

    assert await coord.async_turn_on("DEV1") is delivery
    assert await coord.async_query_device_status("DEV1") is delivery


async def test_transmitted_command_keeps_existing_behaviour(
    coord, broker, timers, dispatched
):
    """200 keeps the estimate and the 5/20 s checks, and records no marker."""
    device = add_device(coord, "D2-01-01", {0: {"is_on": False}})
    entity = add_entity(coord, dispatched, OpusGreenNetSwitch)

    await entity.async_turn_on()

    assert device.channels[0].is_on is True
    entity.async_write_ha_state.assert_called_once()
    assert query_delays(timers) == [5, 20]
    assert device.deferred_commands == {}


@pytest.mark.parametrize("channel_id", [0, 1])
async def test_deferred_stop_schedules_no_status_check(
    coord, broker, timers, dispatched, channel_id
):
    """Stop's final position is only requested once the gateway transmitted it."""
    broker.response = DEFERRED
    device = add_device(coord, "D2-05-00", COVER_SEED)
    entity = add_entity(coord, dispatched, OpusGreenNetCover, channel_id)
    before = {cid: channel_state(device, cid) for cid in COVER_SEED}

    await entity.async_stop_cover()

    assert {cid: channel_state(device, cid) for cid in COVER_SEED} == before
    assert query_delays(timers) == []
    assert device.deferred_commands == {channel_id: {"stop": "true"}}
    entity.async_write_ha_state.assert_not_called()


async def test_deferred_cover_command_starts_no_travel_estimate(
    coord, broker, timers, dispatched, monkeypatch
):
    monkeypatch.setattr(
        "custom_components.opus_greennet.coordinator.monotonic", lambda: 100.0
    )
    broker.response = DEFERRED
    device = add_device(
        coord, "D2-05-00", {0: {"position": 100, "vertical_movement_time": 20}}
    )
    entity = add_entity(coord, dispatched, OpusGreenNetCover)

    await entity.async_open_cover()

    channel = device.channels[0]
    assert channel.position == 100
    assert channel.movement is None
    assert entity.is_opening is False
    assert coord._cover_movements == {}
    # The travel timer was started before publish and is cancelled again.
    assert [delay for delay, _, _ in timers] == [25.0]
    assert all(cancel.called for _, _, cancel in timers)
    assert dispatched.signals == [STATE_SIGNAL, STATE_SIGNAL]
    assert device.deferred_commands == {0: {"position": "0"}}


async def test_deferred_movement_restores_checks_of_earlier_accepted_command(
    coord, broker, timers, dispatched
):
    """Earlier unconfirmed fields keep two checks; the deferred one adds none."""
    add_device(
        coord,
        "D2-05-00",
        {0: {"position": 100, "angle": 10, "vertical_movement_time": 20}},
    )
    await coord.async_set_cover_tilt("DEV1", 40)
    assert len(active_query_timers(timers)) == 2

    broker.response = DEFERRED
    await coord.async_set_cover_position("DEV1", 0)

    active = active_query_timers(timers)
    assert [delay for delay, _, _ in active] == [5, 20]
    assert coord._pending_reconciliation_fields[("DEV1", 0)] == {"angle"}


async def test_timeout_and_rejection_record_no_deferred_marker(
    coord, broker, timers, monkeypatch
):
    device = add_device(coord, "D2-01-01")
    broker.response = {"header": {"httpStatus": 400}}
    with pytest.raises(HomeAssistantError):
        await coord.async_turn_on("DEV1")
    monkeypatch.setattr(
        "custom_components.opus_greennet.mqtt_transport.REQUEST_TIMEOUT", 0.01
    )
    broker.auto_respond = False
    with pytest.raises(HomeAssistantError) as raised:
        await coord.async_turn_on("DEV1")
    assert raised.value.translation_key == "request_timeout"
    assert device.deferred_commands == {}
    assert query_delays(timers) == []
    assert coord.available is True
    broker.assert_clean()


# ── Delivery and confirmation of a deferred command ───────────────────────


async def test_matching_live_report_confirms_deferred_value(coord, broker, timers):
    broker.response = DEFERRED
    device = add_device(coord, "D2-01-01", {0: {"is_on": False}})
    await coord.async_turn_on("DEV1")

    feedback(coord, [{"key": "switch", "value": "off"}])
    assert device.deferred_commands == {0: {"switch": "on"}}

    feedback(coord, [{"key": "switch", "value": "on"}])
    assert device.deferred_commands == {}
    assert device.channels[0].is_on is True
    assert query_delays(timers) == []


async def test_numeric_report_confirms_deferred_setpoint(coord, broker, timers):
    broker.response = DEFERRED
    device = add_device(coord, "D1-4B-06")
    await coord.async_set_climate_setpoint("DEV1", 21.5)
    assert device.deferred_commands == {0: {"temperatureSetpoint": "21.5"}}

    feedback(coord, [{"key": "temperatureSetpoint", "value": 21.5}])

    assert device.deferred_commands == {}
    assert query_delays(timers) == []


async def test_retained_value_cannot_confirm_deferred_command(coord, broker, timers):
    broker.response = DEFERRED
    device = add_device(coord, "D2-01-01", {0: {"is_on": False}})
    await coord.async_turn_on("DEV1")

    retained_snapshot(coord, "switch", "on")

    assert device.deferred_commands == {0: {"switch": "on"}}


async def test_outbound_telegram_marks_delivery_with_usual_check_budget(
    coord, broker, timers, dispatched
):
    """When the queued telegram is sent, the existing two checks follow once."""
    broker.response = DEFERRED
    device = add_device(coord, "D2-01-01", {0: {"is_on": False}})
    await coord.async_turn_on("DEV1")
    assert query_delays(timers) == []

    outbound_echo(coord, [{"key": "switch", "value": "on"}])

    assert device.deferred_commands == {}
    assert device.channels[0].is_on is True
    # Same budget as a transmitted command: two checks, scheduled only now.
    assert query_delays(timers) == [5, 20]
    assert dispatched.signals == [STATE_SIGNAL]


async def test_outbound_telegram_with_other_value_keeps_marker(coord, broker, timers):
    broker.response = DEFERRED
    device = add_device(coord, "D2-01-01", {0: {"is_on": False}})
    await coord.async_turn_on("DEV1")

    outbound_echo(coord, [{"key": "switch", "value": "off"}])

    assert device.deferred_commands == {0: {"switch": "on"}}


async def test_transmitted_command_supersedes_deferred_marker(coord, broker, timers):
    device = add_device(coord, "D2-01-04", {0: {}, 1: {}})
    broker.response = DEFERRED
    await coord.async_turn_on("DEV1", 0)
    await coord.async_turn_on("DEV1", 1)
    assert device.deferred_commands == {0: {"switch": "on"}, 1: {"switch": "on"}}

    broker.response = SENT
    await coord.async_turn_off("DEV1", 0)

    assert device.deferred_commands == {1: {"switch": "on"}}


async def test_forgetting_device_drops_its_deferred_markers(coord, broker, timers):
    broker.response = DEFERRED
    add_device(coord, "D2-01-01")
    await coord.async_turn_on("DEV1")

    coord.async_forget_device("DEV1")
    device = EnOceanDevice("DEV1", "Re-added", [{"eep": "D2-01-01"}])
    coord.devices["DEV1"] = device

    assert device.deferred_commands == {}


async def test_diagnostics_list_deferred_values() -> None:
    coordinator = MagicMock()
    coordinator.eag_id = "AABB0011"
    coordinator.gateway_info = {}
    coordinator.gateway_uptime = None
    coordinator.available = True
    device = EnOceanDevice("DEV1", "Switch", [{"eep": "D2-01-01"}])
    device.deferred_commands[0] = {"switch": "on"}
    coordinator.devices = {"DEV1": device}
    entry = SimpleNamespace(
        entry_id="entry-id",
        title="Bridge",
        data={},
        runtime_data=SimpleNamespace(coordinator=coordinator),
    )

    result = await async_get_config_entry_diagnostics(MagicMock(), entry)

    assert result["devices"][0]["deferred_commands"] == {0: {"switch": "on"}}


# ── Explicit device health stays separate from gateway health (A2) ────────


@pytest.mark.parametrize(
    "report",
    [
        [{"key": "actuatorNotResponding", "value": "error"}],
        [{"key": "missingTemperature", "value": "error"}],
        [{"key": "circuitInUse", "value": "error"}],
        [{"key": "heaterMode", "value": "error"}],
    ],
)
async def test_heat_area_subsystem_error_keeps_entities_available(
    coord, broker, timers, report
):
    """A subsystem error is shown on its own entities, never as an outage."""
    device = add_device(coord, "D1-4B-06")
    climate = OpusGreenNetClimate(coord, EAG_ID, "gateway", device)

    feedback(coord, report)

    assert coord.available is True
    assert climate.available is True
    assert broker.published == []
    assert query_delays(timers) == []


async def test_rejected_command_affects_only_its_device(coord, broker, timers):
    """A healthy gateway with one affected child keeps serving other devices."""
    device = add_device(coord, "D2-01-01")
    other = EnOceanDevice("DEV2", "Other", [{"eep": "D2-01-01"}])
    coord.devices["DEV2"] = other
    broker.responses["EnOcean/AABB0011/put/devices/DEV1/state"] = {
        "header": {"httpStatus": 500}
    }
    broker.responses["EnOcean/AABB0011/put/devices/DEV2/state"] = DEFERRED

    with pytest.raises(HomeAssistantError):
        await coord.async_turn_on("DEV1")
    # The rejection is also reported on the acknowledgement topic.
    coord._handle_put_answer_state(
        SimpleNamespace(
            topic="EnOcean/AABB0011/putAnswer/devices/DEV1/state",
            payload=json.dumps({"header": {"httpStatus": 500}}),
            retain=False,
        )
    )
    assert await coord.async_turn_on("DEV2") is CommandDelivery.DEFERRED

    assert device.last_command_error is not None
    assert other.last_command_error is None
    assert coord.available is True
    assert len(broker.published) == 2


# ── Helper contracts ──────────────────────────────────────────────────────


def test_command_values_skip_selectors_queries_and_malformed_entries():
    values = OpusGreenNetCoordinator._command_values_by_channel(
        [
            {"key": "channel", "value": "1"},
            {"key": "query", "value": "status"},
            {"key": "switch", "value": "on"},
            {"key": "stop", "value": "true"},
            {"key": "position", "value": 70, "channel": 0},
            {"key": "angle", "value": None},
            {"key": "angle", "value": 5, "channel": "bad"},
            {"key": "unknownKey", "value": "x"},
            {"key": 5, "value": "x"},
        ]
    )
    assert values == {1: {"switch": "on", "stop": "true"}, 0: {"position": 70}}


@pytest.mark.parametrize(
    ("key", "commanded", "reported", "matches"),
    [
        ("dimValue", "50", 50, True),
        ("dimValue", "50", "50.0", True),
        ("dimValue", "50", 49, False),
        ("dimValue", "50", True, False),
        ("temperatureSetpoint", "21.5", 21.5, True),
        ("switch", "on", "on", True),
        ("switch", "on", "off", False),
        ("stop", "true", True, True),
        ("stop", "true", False, False),
        ("heaterMode", "heating", None, False),
    ],
)
def test_wire_value_comparison(key, commanded, reported, matches):
    assert (
        OpusGreenNetCoordinator._wire_value_matches(key, commanded, reported) is matches
    )


async def test_report_for_other_channel_keeps_marker(coord, broker, timers):
    broker.response = DEFERRED
    device = add_device(coord, "D2-01-04", {0: {}, 1: {}})
    await coord.async_turn_on("DEV1", 0)

    feedback(
        coord,
        [{"key": "channel", "value": "1"}, {"key": "switch", "value": "on"}],
    )

    assert device.deferred_commands == {0: {"switch": "on"}}


async def test_deferred_command_for_unknown_device_is_reported(coord, broker, timers):
    broker.response = DEFERRED

    assert await coord.async_turn_on("UNKNOWN") is CommandDelivery.DEFERRED
    assert query_delays(timers) == []
