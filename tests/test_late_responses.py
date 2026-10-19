"""Late-response ambiguity without a wire request identifier (plan phase 12).

OPUS answers on ``putAnswer``/``getAnswer`` topics carry no request ID and do
not echo the request. The integration therefore serializes requests per answer
topic (one owner at a time) and globally (one request in flight). These tests
reproduce what happens when request A times out (or is cancelled) and its
answer arrives later, and pin the current behaviour as a documented limitation:

* a late answer that arrives before the next request on the same topic is
  published is discarded by the request manager;
* a late answer that arrives after the next request B was published is taken
  as B's answer; B's own answer then has no owner (or is taken by C);
* local generations, cancellation and reconnect cannot label an old wire
  answer;
* answers never cross answer topics, retained answers are never taken (A4);
* no cooldown, delay or retry follows a timeout (A3), and no request is added
  in any scenario (A1);
* gateway availability never changes (A2).

Phase 12 is an investigation: none of these tests changes production code.
Mitigation options are evaluated in docs/quality/phases/phase-12.md.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.opus_greennet import mqtt_transport
from custom_components.opus_greennet.mqtt_transport import (
    MQTTRequestManager,
    async_get_gateway_uptime,
)
from custom_components.opus_greennet.switch import OpusGreenNetSwitch
from tests.test_coordinator_transport import broker as broker
from tests.test_coordinator_transport import (
    connected_coordinator as connected_coordinator,
)
from tests.test_reconciliation_feedback import timers as timers
from tests.test_state_ownership import (
    EAG_ID,
    add_device,
    add_entity,
    published_functions,
    query_delays,
    wait_for_publish,
)
from tests.test_state_ownership import coord as coord
from tests.test_state_ownership import dispatched as dispatched

PUT_TOPIC = f"EnOcean/{EAG_ID}/put/devices/DEV1/state"
ANSWER = f"EnOcean/{EAG_ID}/putAnswer/devices/DEV1/state"
PUT_TOPIC_2 = f"EnOcean/{EAG_ID}/put/devices/DEV2/state"
ANSWER_2 = f"EnOcean/{EAG_ID}/putAnswer/devices/DEV2/state"
UPTIME_TOPIC = f"EnOcean/{EAG_ID}/get/config/system/uptime"
UPTIME_ANSWER = f"EnOcean/{EAG_ID}/getAnswer/config/system/uptime"
TIMEOUT_KEY = "custom_components.opus_greennet.mqtt_transport.REQUEST_TIMEOUT"


def ack(status: int, marker: str) -> dict:
    """An OPUS answer. ``marker`` only lets the test see which answer was taken.

    The real header carries ``gateway`` and ``timestamp`` but nothing that
    identifies the request; the integration does not interpret them.
    """
    return {"header": {"httpStatus": status, "timestamp": marker}}


def marker(result: dict) -> str:
    return result["header"]["timestamp"]


async def until(condition, rounds: int = 200) -> None:
    """Yield to the event loop until ``condition()`` holds (no wall-clock wait)."""
    for _ in range(rounds):
        if condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition not reached")


def start(manager: MQTTRequestManager, marker_value: str, *, device: str = "DEV1"):
    """Start one state request; the payload marker is never read by the gateway."""
    topic, answer = (PUT_TOPIC, ANSWER) if device == "DEV1" else (PUT_TOPIC_2, ANSWER_2)
    return asyncio.create_task(
        manager.async_request(
            topic,
            answer,
            device,
            json.dumps({"request": marker_value}),
            require_status=True,
            timeout=5.0,
        )
    )


async def time_out(manager: MQTTRequestManager, broker, marker_value: str) -> None:
    """Request A: published, never answered within its (shortened) deadline."""
    published = len(broker.published)
    with pytest.raises(HomeAssistantError) as raised:
        await manager.async_request(
            PUT_TOPIC,
            ANSWER,
            "DEV1",
            json.dumps({"request": marker_value}),
            require_status=True,
            timeout=0.01,
        )
    assert raised.value.translation_key == "request_timeout"
    assert len(broker.published) == published + 1


@pytest.fixture
def manager(broker):
    broker.auto_respond = False
    manager = MQTTRequestManager(object())
    yield manager
    manager.async_close()


# ── Request manager: the reproduced ambiguity ─────────────────────────────


def test_timeout_values_and_pacing_are_unchanged() -> None:
    """Phase 12 changes no production timing (A3, separate design decision)."""
    assert mqtt_transport.REQUEST_TIMEOUT == 10.0
    assert mqtt_transport.SUBSCRIPTION_TIMEOUT == 30.0
    assert mqtt_transport.SETUP_REQUEST_TIMEOUT == 30.0
    assert mqtt_transport.SETUP_SUBSCRIPTION_TIMEOUT == 180.0
    assert mqtt_transport.MIN_SEND_INTERVAL == 0.0


async def test_late_answer_before_next_publish_is_discarded(manager, broker):
    """B is not published yet (SUBACK pending): A's late answer has no owner."""
    await time_out(manager, broker, "A")
    broker.auto_ack = False
    request_b = start(manager, "B")
    await until(lambda: broker.acknowledgements.get(ANSWER))
    assert len(broker.published) == 1

    broker.receive(ANSWER, ack(200, "A-late"))
    await asyncio.sleep(0)
    assert not request_b.done()

    for acknowledge in list(broker.acknowledgements[ANSWER]):
        acknowledge()
    await until(lambda: len(broker.published) == 2)
    broker.receive(ANSWER, ack(200, "B"))
    assert marker(await request_b) == "B"
    broker.assert_clean()


@pytest.mark.parametrize("late_status", [200, 201])
async def test_late_answer_after_next_publish_is_taken_as_its_answer(
    manager, broker, late_status
):
    """The reproduced ambiguity: B resolves with A's late acknowledgement."""
    await time_out(manager, broker, "A")
    request_b = start(manager, "B")
    # No cooldown and no retry: B is published within a few loop iterations.
    await until(lambda: len(broker.published) == 2, rounds=20)
    assert [json.loads(p)["request"] for _, p, _ in broker.published] == ["A", "B"]

    broker.receive(ANSWER, ack(late_status, "A-late"))
    result = await request_b
    assert marker(result) == "A-late"
    assert result["header"]["httpStatus"] == late_status

    # B's own answer arrives afterwards and has no owner: it is dropped.
    broker.receive(ANSWER, ack(400, "B"))
    assert len(broker.published) == 2
    broker.assert_clean()


async def test_late_rejection_fails_the_next_request(manager, broker):
    """A's late negative answer is reported as B's rejection."""
    await time_out(manager, broker, "A")
    request_b = start(manager, "B")
    await until(lambda: len(broker.published) == 2)

    broker.receive(ANSWER, ack(500, "A-late"))
    with pytest.raises(HomeAssistantError) as raised:
        await request_b
    assert raised.value.translation_key == "request_rejected"

    broker.receive(ANSWER, ack(200, "B"))  # the gateway accepted B; nobody listens
    assert len(broker.published) == 2
    broker.assert_clean()


async def test_shift_continues_while_requests_overlap_and_ends_on_a_gap(
    manager, broker
):
    """One unclaimed answer shifts attribution until it meets an idle topic."""
    await time_out(manager, broker, "A")
    request_b = start(manager, "B")
    await until(lambda: len(broker.published) == 2)
    broker.receive(ANSWER, ack(200, "A-late"))
    assert marker(await request_b) == "A-late"

    request_c = start(manager, "C")
    await until(lambda: len(broker.published) == 3)
    broker.receive(ANSWER, ack(200, "B"))
    assert marker(await request_c) == "B"

    # No request owns the topic now: C's answer is dropped, alignment returns.
    broker.receive(ANSWER, ack(200, "C"))
    request_d = start(manager, "D")
    await until(lambda: len(broker.published) == 4)
    broker.receive(ANSWER, ack(200, "D"))
    assert marker(await request_d) == "D"
    broker.assert_clean()


async def test_late_answer_never_crosses_answer_topics(manager, broker):
    """The global send queue is shared, attribution is not: DEV2 is unaffected."""
    await time_out(manager, broker, "A")
    request_b = start(manager, "B", device="DEV2")
    await until(lambda: len(broker.published) == 2)

    broker.receive(ANSWER, ack(500, "A-late"))
    await asyncio.sleep(0)
    assert not request_b.done()

    broker.receive(ANSWER_2, ack(200, "B"))
    assert marker(await request_b) == "B"
    broker.assert_clean()


async def test_retained_late_answer_is_never_taken(manager, broker):
    """A4: a retained answer is not a reply to the request in flight."""
    await time_out(manager, broker, "A")
    request_b = start(manager, "B")
    await until(lambda: len(broker.published) == 2)

    broker.receive(ANSWER, ack(500, "A-retained"), retain=True)
    await asyncio.sleep(0)
    assert not request_b.done()

    broker.receive(ANSWER, ack(200, "B"))
    assert marker(await request_b) == "B"
    broker.assert_clean()


@pytest.mark.parametrize("key", ["request_cancelled", "gateway_unavailable"])
async def test_generation_bump_cannot_label_an_old_wire_answer(manager, broker, key):
    """Reconnect/cancel invalidates A locally, but its answer still reaches B."""
    request_a = start(manager, "A")
    await until(lambda: len(broker.published) == 1)
    manager.async_cancel_pending(key)
    with pytest.raises(HomeAssistantError) as raised:
        await request_a
    assert raised.value.translation_key == key

    request_b = start(manager, "B")
    await until(lambda: len(broker.published) == 2)
    broker.receive(ANSWER, ack(200, "A-late"))
    assert marker(await request_b) == "A-late"
    broker.assert_clean()


async def test_device_removal_cannot_label_an_old_wire_answer(manager, broker):
    """Removal and re-add of a child: its old answer reaches the new request."""
    request_a = start(manager, "A")
    await until(lambda: len(broker.published) == 1)
    manager.async_cancel_device("DEV1")
    with pytest.raises(HomeAssistantError) as raised:
        await request_a
    assert raised.value.translation_key == "request_cancelled"

    request_b = start(manager, "B")
    await until(lambda: len(broker.published) == 2)
    broker.receive(ANSWER, ack(500, "A-late"))
    with pytest.raises(HomeAssistantError) as raised:
        await request_b
    assert raised.value.translation_key == "request_rejected"
    broker.assert_clean()


async def test_cancelled_caller_leaves_an_unclaimed_answer_like_a_timeout(
    manager, broker
):
    """A caller cancelled after publish (e.g. HA stop) behaves like a timeout."""
    request_a = start(manager, "A")
    await until(lambda: len(broker.published) == 1)
    request_a.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request_a

    request_b = start(manager, "B")
    await until(lambda: len(broker.published) == 2)
    broker.receive(ANSWER, ack(201, "A-late"))
    assert marker(await request_b) == "A-late"
    broker.assert_clean()


async def test_late_uptime_answer_is_taken_by_the_next_health_probe(manager, broker):
    """Same mechanism on getAnswer/config/system/uptime (no device state, A2)."""
    with pytest.raises(HomeAssistantError) as raised:
        await async_get_gateway_uptime(manager, EAG_ID, timeout=0.01)
    assert raised.value.translation_key == "request_timeout"

    probe = asyncio.create_task(async_get_gateway_uptime(manager, EAG_ID, timeout=5))
    await until(lambda: len(broker.published) == 2)
    broker.receive(
        UPTIME_ANSWER,
        {"header": {"httpStatus": 200}, "systemUptimeResponse": {"uptime": 100}},
    )
    result = await probe
    assert result["systemUptimeResponse"]["uptime"] == 100  # the older answer
    broker.receive(
        UPTIME_ANSWER,
        {"header": {"httpStatus": 200}, "systemUptimeResponse": {"uptime": 160}},
    )
    assert [topic for topic, _, _ in broker.published] == [UPTIME_TOPIC] * 2
    broker.assert_clean()


async def test_answer_to_another_client_is_indistinguishable(manager, broker):
    """A command from MyOPUS or another MQTT client shares the answer topic."""
    request_b = start(manager, "B")
    await until(lambda: len(broker.published) == 1)
    broker.receive(ANSWER, ack(200, "foreign"))
    assert marker(await request_b) == "foreign"
    broker.assert_clean()


# ── Coordinator: production routing through the persistent wildcard ──────


def put_answer(coord, status: int, marker_value: str, *, retain: bool = False):
    """Deliver an answer exactly as the persistent putAnswer listener does."""
    coord._handle_put_answer_state(
        SimpleNamespace(
            topic=ANSWER,
            payload=json.dumps(ack(status, marker_value)),
            retain=retain,
        )
    )


def active_checks(timers) -> list:
    return [t for t in timers if t[0] in (5, 20) and not t[2].called]


@pytest.fixture
def routed(coord, broker, monkeypatch):
    """Answers reach requests via the route table, as after a real setup."""
    broker.auto_respond = False
    coord._register_answer_routes()
    monkeypatch.setattr(TIMEOUT_KEY, 0.01)
    return coord


async def switch_timeout(coord, broker, entity, monkeypatch) -> None:
    with pytest.raises(HomeAssistantError) as raised:
        await entity.async_turn_on()
    assert raised.value.translation_key == "request_timeout"
    assert len(published_functions(broker)) == 1
    monkeypatch.setattr(TIMEOUT_KEY, 5.0)


async def test_late_success_masks_rejection_of_next_command(
    routed, broker, timers, dispatched, monkeypatch
):
    """B is shown as accepted; B's own rejection then cancels B's checks.

    Residual risk L3 (phase 12 report): the estimate stays without a status
    query until the device reports. Traffic only decreases (A1).
    """
    coord = routed
    device = add_device(coord, "D2-01-01", {0: {"is_on": False}})
    entity = add_entity(coord, dispatched, OpusGreenNetSwitch)
    await switch_timeout(coord, broker, entity, monkeypatch)
    assert device.channels[0].is_on is False
    assert query_delays(timers) == []

    task = asyncio.create_task(entity.async_turn_on())  # the user repeats
    await wait_for_publish(broker, 2)
    put_answer(coord, 200, "A-late")
    await task
    assert device.channels[0].is_on is True
    entity.async_write_ha_state.assert_called_once()
    assert query_delays(timers) == [5, 20]
    assert len(active_checks(timers)) == 2

    put_answer(coord, 400, "B")
    assert device.last_command_error == "The gateway returned status 400"
    assert active_checks(timers) == []
    assert device.channels[0].is_on is True  # estimate kept, nothing re-checks it
    assert len(broker.published) == 2  # no retry, no extra query
    assert coord.available is True
    broker.assert_clean()


async def test_late_rejection_fails_next_command_the_gateway_accepted(
    routed, broker, timers, dispatched, monkeypatch
):
    """B raises although the gateway accepted it; no estimate, no checks."""
    coord = routed
    device = add_device(coord, "D2-01-01", {0: {"is_on": False}})
    entity = add_entity(coord, dispatched, OpusGreenNetSwitch)
    await switch_timeout(coord, broker, entity, monkeypatch)

    task = asyncio.create_task(entity.async_turn_on())
    await wait_for_publish(broker, 2)
    put_answer(coord, 400, "A-late")
    with pytest.raises(HomeAssistantError) as raised:
        await task
    assert raised.value.translation_key == "request_rejected"
    assert device.channels[0].is_on is False
    assert device.last_command_error == "The gateway returned status 400"

    put_answer(coord, 200, "B")  # B's real acknowledgement: device level only
    assert device.last_command_error is None
    assert device.channels[0].is_on is False  # only a device report corrects it
    assert query_delays(timers) == []
    assert len(broker.published) == 2
    assert coord.available is True
    broker.assert_clean()


async def test_late_answer_without_waiting_request_touches_only_device_marker(
    routed, broker, timers, dispatched, monkeypatch
):
    """No owner: the request manager drops it; the device marker follows it."""
    coord = routed
    monkeypatch.setattr(TIMEOUT_KEY, 5.0)
    device = add_device(coord, "D2-01-01", {0: {"is_on": False}})
    entity = add_entity(coord, dispatched, OpusGreenNetSwitch)

    # An accepted command X leaves its two checks pending.
    task = asyncio.create_task(entity.async_turn_on())
    await wait_for_publish(broker, 1)
    put_answer(coord, 200, "X")
    await task
    assert len(active_checks(timers)) == 2

    # A times out; its late rejection arrives while no request is waiting.
    monkeypatch.setattr(TIMEOUT_KEY, 0.01)
    with pytest.raises(HomeAssistantError):
        await entity.async_turn_off()
    put_answer(coord, 400, "A-late")

    assert device.last_command_error == "The gateway returned status 400"
    assert active_checks(timers) == []  # fewer checks, never more (A1)
    assert device.channels[0].is_on is True  # no state change from an answer
    assert len(broker.published) == 2
    assert coord.available is True

    put_answer(coord, 200, "late success")
    assert device.last_command_error is None
    broker.assert_clean()


async def test_retained_answer_reaches_neither_request_nor_device_marker(
    routed, broker, timers, dispatched, monkeypatch
):
    coord = routed
    device = add_device(coord, "D2-01-01", {0: {"is_on": False}})
    entity = add_entity(coord, dispatched, OpusGreenNetSwitch)
    await switch_timeout(coord, broker, entity, monkeypatch)

    task = asyncio.create_task(entity.async_turn_on())
    await wait_for_publish(broker, 2)
    put_answer(coord, 400, "A-retained", retain=True)
    await asyncio.sleep(0)
    assert not task.done()
    assert device.last_command_error is None

    put_answer(coord, 200, "B")
    await task
    assert device.channels[0].is_on is True
    assert len(broker.published) == 2
    broker.assert_clean()
