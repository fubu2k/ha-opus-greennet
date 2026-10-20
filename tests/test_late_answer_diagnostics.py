"""Late-answer diagnostics (plan phase 12, option M6).

The request manager only observes: a published request that ended without its
answer is remembered for its answer topic; an answer that no request takes
settles the oldest remembered one; an answer taken while one is still open is
counted as "possibly late" for the request's owner. These tests pin the
counters and that nothing else changes: no publish, no delay, no retry, no
different result (A1, A3), no availability change (A2), retained answers are
ignored (A4).
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.opus_greennet import mqtt_transport
from custom_components.opus_greennet.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from custom_components.opus_greennet.mqtt_transport import (
    LATE_ANSWER_DIAGNOSTIC_WINDOW,
    MQTTRequestManager,
    async_get_gateway_uptime,
)
from tests.test_coordinator_transport import broker as broker
from tests.test_coordinator_transport import (
    connected_coordinator as connected_coordinator,
)
from tests.test_late_responses import (
    ANSWER,
    ANSWER_2,
    EAG_ID,
    UPTIME_ANSWER,
    ack,
    marker,
    start,
    time_out,
    until,
)
from tests.test_late_responses import manager as manager

CLEAN = {"unanswered_requests": 0, "possibly_late_answers": 0}


def counters(manager: MQTTRequestManager, owner: str = "DEV1") -> dict[str, int]:
    return manager.late_answer_diagnostics(owner)


def routed_answer(manager: MQTTRequestManager, status: int, value: str, **kw):
    """An answer through the persistent wildcard listener (route table)."""
    manager.async_route(
        SimpleNamespace(
            topic=ANSWER,
            payload=json.dumps(ack(status, value)),
            retain=kw.get("retain", False),
        )
    )


@pytest.fixture
def clock(monkeypatch):
    """Controllable monotonic clock for the diagnostic window."""
    now = [1000.0]
    monkeypatch.setattr(mqtt_transport, "monotonic", lambda: now[0])
    return now


# ── Counters ──────────────────────────────────────────────────────────────


async def test_answered_requests_leave_no_counters(manager, broker):
    """No false positives in the normal case, any number of requests."""
    for index in range(5):
        request = start(manager, f"R{index}")
        await until(lambda: len(broker.published) == index + 1)
        broker.receive(ANSWER, ack(200, f"R{index}"))
        assert marker(await request) == f"R{index}"
    assert counters(manager) == CLEAN
    broker.assert_clean()


async def test_timeout_after_publish_opens_one_entry(manager, broker):
    await time_out(manager, broker, "A")
    assert counters(manager) == {"unanswered_requests": 1, "possibly_late_answers": 0}
    assert counters(manager, "DEV2") == CLEAN


async def test_answer_taken_while_open_is_counted_and_result_unchanged(manager, broker):
    """L1 stays as it is; it is only made visible."""
    await time_out(manager, broker, "A")
    request_b = start(manager, "B")
    await until(lambda: len(broker.published) == 2, rounds=20)  # no delay (A3)
    broker.receive(ANSWER, ack(200, "A-late"))
    assert marker(await request_b) == "A-late"  # same result as without M6
    # One answer is still in transit (B's own): the entry stays open.
    assert counters(manager) == {"unanswered_requests": 1, "possibly_late_answers": 1}
    assert len(broker.published) == 2  # no retry, no extra request (A1)
    broker.assert_clean()


async def test_answer_before_next_publish_settles_the_open_entry(manager, broker):
    """L2: the stray answer is dropped and settles A; B is not flagged."""
    await time_out(manager, broker, "A")
    broker.auto_ack = False
    request_b = start(manager, "B")
    await until(lambda: broker.acknowledgements.get(ANSWER))
    broker.receive(ANSWER, ack(200, "A-late"))
    assert counters(manager)["unanswered_requests"] == 0

    for acknowledge in list(broker.acknowledgements[ANSWER]):
        acknowledge()
    await until(lambda: len(broker.published) == 2)
    broker.receive(ANSWER, ack(200, "B"))
    assert marker(await request_b) == "B"
    assert counters(manager) == CLEAN
    broker.assert_clean()


async def test_unowned_routed_answer_settles_the_open_entry(manager, broker):
    """With the persistent listener, a late answer without owner is observed."""
    manager.async_add_route_pattern(ANSWER)
    await time_out(manager, broker, "A")
    assert counters(manager)["unanswered_requests"] == 1

    routed_answer(manager, 200, "A-late", retain=True)  # retained: ignored (A4)
    assert counters(manager)["unanswered_requests"] == 1
    routed_answer(manager, 200, "A-late")
    assert counters(manager) == CLEAN

    request_b = start(manager, "B")
    await until(lambda: len(broker.published) == 2)
    routed_answer(manager, 200, "B")
    assert marker(await request_b) == "B"
    assert counters(manager) == CLEAN
    broker.assert_clean()


async def test_second_answer_while_still_listening_settles_the_entry(manager, broker):
    """A-late and B's own answer back to back: B flagged, nothing left open."""
    await time_out(manager, broker, "A")
    request_b = start(manager, "B")
    await until(lambda: len(broker.published) == 2)
    broker.receive(ANSWER, ack(200, "A-late"))
    broker.receive(ANSWER, ack(200, "B"))  # before B's task resumed
    assert marker(await request_b) == "A-late"
    assert counters(manager) == {"unanswered_requests": 0, "possibly_late_answers": 1}
    broker.assert_clean()


async def test_retained_answer_neither_settles_nor_flags(manager, broker):
    await time_out(manager, broker, "A")
    request_b = start(manager, "B")
    await until(lambda: len(broker.published) == 2)
    broker.receive(ANSWER, ack(200, "A-retained"), retain=True)
    await asyncio.sleep(0)
    assert not request_b.done()
    assert counters(manager) == {"unanswered_requests": 1, "possibly_late_answers": 0}
    broker.receive(ANSWER, ack(200, "B"))
    await request_b
    assert counters(manager)["possibly_late_answers"] == 1
    broker.assert_clean()


async def test_counters_never_cross_answer_topics(manager, broker):
    await time_out(manager, broker, "A")
    request_b = start(manager, "B", device="DEV2")
    await until(lambda: len(broker.published) == 2)
    broker.receive(ANSWER_2, ack(200, "B"))
    assert marker(await request_b) == "B"
    assert counters(manager, "DEV2") == CLEAN
    assert counters(manager)["unanswered_requests"] == 1
    broker.assert_clean()


async def test_entries_expire_after_the_diagnostic_window(manager, broker, clock):
    await time_out(manager, broker, "A")
    assert counters(manager)["unanswered_requests"] == 1
    clock[0] += LATE_ANSWER_DIAGNOSTIC_WINDOW + 1
    assert counters(manager) == CLEAN

    request_b = start(manager, "B")
    await until(lambda: len(broker.published) == 2)
    broker.receive(ANSWER, ack(200, "B"))
    await request_b
    assert counters(manager) == CLEAN  # an expired entry flags nothing
    broker.assert_clean()


async def test_expiry_keeps_newer_entries(manager, broker, clock):
    await time_out(manager, broker, "A1")
    clock[0] += LATE_ANSWER_DIAGNOSTIC_WINDOW / 2
    await time_out(manager, broker, "A2")
    assert counters(manager)["unanswered_requests"] == 2
    clock[0] += LATE_ANSWER_DIAGNOSTIC_WINDOW / 2 + 1
    assert counters(manager)["unanswered_requests"] == 1


async def test_unowned_answer_settles_only_the_oldest_entry(manager, broker, clock):
    manager.async_add_route_pattern(ANSWER)
    await time_out(manager, broker, "A1")
    clock[0] += 1
    await time_out(manager, broker, "A2")
    routed_answer(manager, 200, "A1-late")
    assert counters(manager)["unanswered_requests"] == 1
    # The remaining entry is the newer one: it outlives A1's expiry time.
    clock[0] += LATE_ANSWER_DIAGNOSTIC_WINDOW - 0.5
    assert counters(manager)["unanswered_requests"] == 1


async def test_failed_publish_opens_no_entry(manager, broker, monkeypatch):
    async def failing_publish(*args, **kwargs):
        raise HomeAssistantError("broker down")

    monkeypatch.setattr(
        "custom_components.opus_greennet.mqtt_transport.mqtt.async_publish",
        failing_publish,
    )
    with pytest.raises(HomeAssistantError):
        await start(manager, "A")
    assert counters(manager) == CLEAN
    broker.assert_clean()


async def test_cancelled_caller_opens_an_entry(manager, broker):
    request_a = start(manager, "A")
    await until(lambda: len(broker.published) == 1)
    request_a.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request_a
    assert counters(manager)["unanswered_requests"] == 1


async def test_reconnect_keeps_open_entries(manager, broker):
    """L5: the answer can still arrive after a reconnect."""
    request_a = start(manager, "A")
    await until(lambda: len(broker.published) == 1)
    manager.async_cancel_pending("gateway_unavailable")
    with pytest.raises(HomeAssistantError):
        await request_a
    assert counters(manager)["unanswered_requests"] == 1


async def test_own_answer_after_local_cancellation_settles_itself(manager, broker):
    """The answer reaches the cancelled request before its cleanup ran."""
    request_a = start(manager, "A")
    await until(lambda: len(broker.published) == 1)
    manager.async_cancel_device("DEV1")
    broker.receive(ANSWER, ack(200, "A"))  # still subscribed at this point
    with pytest.raises(HomeAssistantError) as raised:
        await request_a
    assert raised.value.translation_key == "request_cancelled"
    assert counters(manager) == CLEAN
    broker.assert_clean()


async def test_device_removal_resets_counts_but_keeps_open_entries(manager, broker):
    await time_out(manager, broker, "A")
    request_b = start(manager, "B")
    await until(lambda: len(broker.published) == 2)
    broker.receive(ANSWER, ack(200, "A-late"))
    await request_b
    assert counters(manager)["possibly_late_answers"] == 1

    manager.async_cancel_device("DEV1")
    assert counters(manager) == {"unanswered_requests": 1, "possibly_late_answers": 0}


async def test_close_clears_and_records_nothing_afterwards(broker):
    broker.auto_respond = False
    manager = MQTTRequestManager(object())
    await time_out(manager, broker, "A")
    request_b = start(manager, "B")
    await until(lambda: len(broker.published) == 2)
    manager.async_close()
    with pytest.raises(HomeAssistantError):
        await request_b
    assert counters(manager) == CLEAN
    broker.assert_clean()


async def test_gateway_uptime_counters_use_the_gateway_owner(manager, broker):
    with pytest.raises(HomeAssistantError):
        await async_get_gateway_uptime(manager, EAG_ID, timeout=0.01)
    probe = asyncio.create_task(async_get_gateway_uptime(manager, EAG_ID, timeout=5))
    await until(lambda: len(broker.published) == 2)
    broker.receive(
        UPTIME_ANSWER,
        {"header": {"httpStatus": 200}, "systemUptimeResponse": {"uptime": 100}},
    )
    await probe
    assert counters(manager, EAG_ID) == {
        "unanswered_requests": 1,
        "possibly_late_answers": 1,
    }
    assert counters(manager) == CLEAN
    broker.assert_clean()


# ── Coordinator and diagnostics ───────────────────────────────────────────


async def test_coordinator_reports_counters_in_diagnostics(
    broker, connected_coordinator, monkeypatch
):
    """Visible per device and for the gateway; availability unchanged (A2)."""
    coord = connected_coordinator
    coord._register_answer_routes()
    coord.devices["DEV1"] = EnOceanDevice("DEV1", "Switch", [{"eep": "D2-01-01"}])
    broker.auto_respond = False
    monkeypatch.setattr(
        "custom_components.opus_greennet.mqtt_transport.REQUEST_TIMEOUT", 0.01
    )
    try:
        with pytest.raises(HomeAssistantError) as raised:
            await coord.async_turn_on("DEV1")
        assert raised.value.translation_key == "request_timeout"
        monkeypatch.setattr(
            "custom_components.opus_greennet.mqtt_transport.REQUEST_TIMEOUT", 5.0
        )
        task = asyncio.create_task(coord.async_turn_off("DEV1"))
        await until(lambda: len(broker.published) == 2)
        coord._handle_put_answer_state(
            SimpleNamespace(
                topic=ANSWER, payload=json.dumps(ack(200, "A-late")), retain=False
            )
        )
        await task
        assert coord.late_answer_diagnostics("DEV1") == {
            "unanswered_requests": 1,
            "possibly_late_answers": 1,
        }

        # B's own answer arrives with no owner and settles the open entry.
        coord._handle_put_answer_state(
            SimpleNamespace(
                topic=ANSWER, payload=json.dumps(ack(200, "B")), retain=False
            )
        )
        entry = SimpleNamespace(
            entry_id="entry-id",
            title="Bridge",
            data={},
            runtime_data=SimpleNamespace(coordinator=coord),
        )
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        assert result["devices"][0]["late_answers"] == {
            "unanswered_requests": 0,
            "possibly_late_answers": 1,
        }
        assert result["gateway"]["late_answers"] == {
            "unanswered_requests": 0,
            "possibly_late_answers": 0,
        }
        assert coord.available is True
        assert len(broker.published) == 2
    finally:
        await coord.async_unload()
    broker.assert_clean()
