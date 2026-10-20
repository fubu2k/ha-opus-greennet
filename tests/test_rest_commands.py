"""Commands over the REST transport: acknowledgements, the radio queue, outages.

Most tests drive the real coordinator with only the gateway's HTTP client
replaced by ``FakeRadio``: every ``PUT`` returns the gateway's acknowledgement
document, which passes through the production ``decode_response``. Tests that
depend on real HTTP timing use the in-process ``FakeGateway`` instead.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.opus_greennet.api import (
    REQUEST_TIMEOUT,
    OpusTimeoutError,
    decode_response,
)
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from custom_components.opus_greennet.rest_coordinator import (
    PRIORITY_BACKGROUND,
    PRIORITY_COMMAND,
    PRIORITY_QUERY,
    OpusGreenNetRestCoordinator,
    PriorityLock,
)
from tests.fake_gateway import FakeGateway, device_object, telegram
from tests.ha_helpers import setup_rest_bridge, wait_until

EAG_ID = "AABB0011"
OK = {"header": {"httpStatus": 200}}


def state_path(device_id: str = "DEV1") -> str:
    return f"/devices/{device_id}/state"


class FakeRadio:
    """The gateway client as the coordinator uses it for radio requests.

    ``auto_respond`` acknowledges every request at once with ``response``.
    Otherwise each request waits until ``respond()`` answers the oldest one.
    A response is a gateway document (decoded like the real client does), a
    raw text, or an exception the client would raise.
    """

    def __init__(self) -> None:
        self.auto_respond = True
        self.response: object = OK
        # Overrides the client timeout of waiting requests (seconds).
        self.timeout: float | None = None
        self.sent: list[tuple[str, str, Any]] = []
        self._waiting: list[asyncio.Future[object]] = []
        self.host = "127.0.0.1"

    async def async_put(
        self,
        path: str,
        body: Mapping[str, object],
        *,
        timeout: float = REQUEST_TIMEOUT,
    ) -> dict[str, Any]:
        return await self._request("PUT", path, body, timeout)

    async def async_get(
        self, path: str, *, timeout: float = REQUEST_TIMEOUT
    ) -> dict[str, Any]:
        return await self._request("GET", path, None, timeout)

    async def _request(
        self, method: str, path: str, body: object, timeout: float
    ) -> dict[str, Any]:
        # Record the body as it travels over the wire.
        self.sent.append((method, path, json.loads(json.dumps(body))))
        if self.auto_respond:
            return self._decode(self.response)
        future: asyncio.Future[object] = asyncio.get_running_loop().create_future()
        self._waiting.append(future)
        try:
            async with asyncio.timeout(self.timeout or timeout):
                response = await future
        except TimeoutError as err:
            raise OpusTimeoutError(f"{method} {path} timed out") from err
        finally:
            if future in self._waiting:
                self._waiting.remove(future)
        return self._decode(response)

    @staticmethod
    def _decode(response: object) -> dict[str, Any]:
        if isinstance(response, BaseException):
            raise response
        text = response if isinstance(response, str) else json.dumps(response)
        return decode_response(text)

    @property
    def waiting(self) -> int:
        """Return the number of requests awaiting their acknowledgement."""
        return len(self._waiting)

    def respond(self, response: object = OK) -> None:
        """Answer the oldest request still waiting for its acknowledgement."""
        self._waiting[0].set_result(response)

    def functions(self, path: str | None = None) -> list[list[dict[str, Any]]]:
        """Return the state functions of every PUT (to ``path``) in order."""
        return [
            body["state"]["functions"]
            for method, sent_path, body in self.sent
            if method == "PUT"
            and sent_path.endswith("/state")
            and (path is None or sent_path == path)
        ]

    async def wait_sent(self, count: int) -> None:
        """Let queued requests reach the radio until ``count`` were sent."""
        for _ in range(100):
            if len(self.sent) >= count:
                return
            await asyncio.sleep(0)
        raise AssertionError(f"expected {count} requests, sent {self.sent}")


async def settle() -> None:
    """Let woken queue waiters run up to their next suspension point."""
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.fixture
def radio() -> FakeRadio:
    return FakeRadio()


@pytest.fixture
async def connected_coordinator(radio: FakeRadio):
    """A coordinator with an available gateway and the production queue."""
    hass = MagicMock()
    hass.async_create_background_task.side_effect = (
        lambda target, name, eager_start=True: asyncio.create_task(target)
    )
    coord = OpusGreenNetRestCoordinator(hass, radio, EAG_ID)  # type: ignore[arg-type]
    coord._gateway_available = True
    try:
        yield coord
    finally:
        await coord.async_unload()


@pytest.fixture
async def bridge(
    hass: HomeAssistant, gateway: FakeGateway
) -> OpusGreenNetRestCoordinator:
    """A coordinator set up through the config flow against the fake gateway."""
    gateway.devices = [
        device_object(
            f"DEV{index}", "D2-01-01", states=[{"key": "switch", "value": "off"}]
        )
        for index in range(1, 4)
    ]
    entry = await setup_rest_bridge(hass, gateway)
    return entry.runtime_data.coordinator


def short_timeout(
    monkeypatch: pytest.MonkeyPatch,
    coordinator: OpusGreenNetRestCoordinator,
    seconds: float,
) -> None:
    """Give every PUT of the real client a short timeout."""
    original = coordinator.client.async_put

    async def put(path, body, *, timeout=seconds):
        return await original(path, body, timeout=seconds)

    monkeypatch.setattr(coordinator.client, "async_put", put)


# ── Acknowledgements ──────────────────────────────────────────────────────


@pytest.mark.parametrize("status", [200, 201, "200", 200.0])
async def test_command_requires_accepted_gateway_ack(
    radio, connected_coordinator, status
):
    radio.response = {"header": {"httpStatus": status}}
    await connected_coordinator.async_turn_on("DEV1")
    assert radio.sent == [
        (
            "PUT",
            state_path(),
            {"state": {"functions": [{"key": "switch", "value": "on"}]}},
        )
    ]
    assert not connected_coordinator._radio_queue.locked()


@pytest.mark.parametrize(
    "response",
    [
        {"header": {"httpStatus": 400}},
        {"header": {"httpStatus": 500}},
        {"header": {"httpStatus": True}},
        {"header": {"httpStatus": 200.9}},
        {"wrong": "shape"},
        [],
        "not json",
    ],
)
async def test_rejected_or_malformed_ack_fails_action(hass, gateway, bridge, response):
    """The PUT response is the acknowledgement; anything but 200/201 rejects."""
    gateway.responses[state_path()] = response
    with pytest.raises(HomeAssistantError) as raised:
        await bridge.async_turn_on("DEV1")
    assert raised.value.translation_key == "request_rejected"
    assert bridge.devices["DEV1"].last_command_error
    assert bridge.available
    assert not bridge._radio_queue.locked()


async def test_configuration_write_requires_ack(radio, connected_coordinator):
    radio.response = {"header": {"httpStatus": 400}}
    with pytest.raises(HomeAssistantError) as raised:
        await connected_coordinator.async_set_device_configuration(
            "DEV1", {"repeater": 1}
        )
    assert raised.value.translation_key == "request_rejected"
    assert radio.sent == [("PUT", "/devices/DEV1/configuration", {"repeater": 1})]


async def test_unexpected_client_error_is_propagated_and_releases_queue(
    radio, connected_coordinator
):
    radio.response = RuntimeError("client failed")
    with pytest.raises(RuntimeError, match="client failed"):
        await connected_coordinator.async_turn_on("DEV1")
    assert not connected_coordinator._radio_queue.locked()
    assert not connected_coordinator._command_waiters

    radio.response = OK
    await connected_coordinator.async_turn_on("DEV2")
    assert radio.sent[-1][1] == state_path("DEV2")


# ── Offline and unloaded ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("condition", "translation_key"),
    [("unavailable", "gateway_unavailable"), ("unloaded", "request_cancelled")],
)
async def test_offline_commands_are_never_sent(
    radio, connected_coordinator, condition, translation_key
):
    if condition == "unavailable":
        connected_coordinator._set_gateway_available(False)
    else:
        await connected_coordinator.async_unload()
    for request in (
        connected_coordinator.async_turn_on("DEV1"),
        connected_coordinator.async_query_device_status("DEV1"),
        connected_coordinator.async_get_device_parameters("DEV1"),
    ):
        with pytest.raises(HomeAssistantError) as raised:
            await request
        assert raised.value.translation_key == translation_key
    assert radio.sent == []
    assert not connected_coordinator._command_waiters


@pytest.mark.parametrize(
    "operation", ["async_get_device_configuration", "async_get_device_parameters"]
)
async def test_unload_cancels_queued_request(radio, connected_coordinator, operation):
    radio.auto_respond = False
    busy = asyncio.create_task(connected_coordinator.async_turn_on("DEV0"))
    await radio.wait_sent(1)
    task = asyncio.create_task(getattr(connected_coordinator, operation)("DEV1"))
    await settle()
    await connected_coordinator.async_unload()
    radio.respond()
    for pending in (busy, task):
        with pytest.raises(HomeAssistantError) as raised:
            await pending
        assert raised.value.translation_key == "request_cancelled"
    assert len(radio.sent) == 1
    assert not connected_coordinator._radio_queue.locked()


async def test_unload_cancels_request_in_flight(
    radio, connected_coordinator, monkeypatch
):
    scheduled = []
    monkeypatch.setattr(
        "custom_components.opus_greennet.rest_coordinator.async_call_later",
        lambda hass, delay, callback: scheduled.append(delay) or MagicMock(),
    )
    connected_coordinator.devices["DEV1"] = EnOceanDevice(
        "DEV1", "Switch", [{"eep": "D2-01-01"}]
    )
    radio.auto_respond = False
    task = asyncio.create_task(connected_coordinator.async_turn_on("DEV1"))
    await radio.wait_sent(1)
    await connected_coordinator.async_unload()
    radio.respond()
    with pytest.raises(HomeAssistantError) as raised:
        await task
    assert raised.value.translation_key == "request_cancelled"
    assert scheduled == []


# ── Reconciliation after the acknowledgement ──────────────────────────────


async def test_accepted_local_command_reconciles_even_without_echo(
    radio, connected_coordinator, monkeypatch
):
    connected_coordinator.devices["DEV1"] = EnOceanDevice(
        "DEV1", "Switch", [{"eep": "D2-01-11"}]
    )
    schedule = MagicMock()
    monkeypatch.setattr(
        connected_coordinator, "_schedule_reconciliation_queries", schedule
    )
    await connected_coordinator.async_turn_on("DEV1", channel=1)
    schedule.assert_called_once_with("DEV1", 1, fields={"switch"})
    schedule.reset_mock()
    await connected_coordinator.async_query_device_status("DEV1", channel=1)
    schedule.assert_not_called()


async def test_report_before_ack_does_not_schedule_redundant_query(
    radio, connected_coordinator, monkeypatch
):
    connected_coordinator.devices["DEV1"] = EnOceanDevice(
        "DEV1", "Switch", [{"eep": "D2-01-11"}]
    )
    radio.auto_respond = False
    schedule = MagicMock()
    monkeypatch.setattr(
        connected_coordinator, "_schedule_reconciliation_queries", schedule
    )
    task = asyncio.create_task(connected_coordinator.async_turn_on("DEV1", channel=1))
    await radio.wait_sent(1)
    connected_coordinator._handle_telegram(
        telegram("DEV1", [{"key": "switch", "value": "on", "channel": 1}])["telegram"]
    )
    radio.respond()
    await task
    schedule.assert_not_called()
    assert connected_coordinator.devices["DEV1"].channels[1].is_on is True


# ── The single in-flight radio request ────────────────────────────────────


async def test_global_send_queue_keeps_one_request_in_flight(
    radio, connected_coordinator
):
    """Different devices share one in-flight slot, served in FIFO order."""
    radio.auto_respond = False
    tasks = [
        asyncio.create_task(connected_coordinator.async_turn_on(f"DEV{index}"))
        for index in range(10)
    ]
    for index in range(10):
        await settle()
        # Only the head of the queue has been sent so far.
        assert [path for _, path, _ in radio.sent] == [
            state_path(f"DEV{sent}") for sent in range(index + 1)
        ]
        assert radio.waiting == 1
        radio.respond()
    await asyncio.gather(*tasks)
    assert not connected_coordinator._radio_queue.locked()


async def test_request_timeout_starts_when_sent_not_when_queued(
    gateway, bridge, monkeypatch
):
    """A request queued longer than the timeout still gets its full timeout."""
    short_timeout(monkeypatch, bridge, 0.25)
    for index in range(1, 4):
        gateway.delays[state_path(f"DEV{index}")] = 0.15
    # The third command waits ~0.3 s in the queue, longer than its timeout.
    await asyncio.gather(*(bridge.async_turn_on(f"DEV{i}") for i in range(1, 4)))
    assert len(gateway.requests_to("PUT", state_path("DEV3"))) == 1


async def test_command_timeout_keeps_gateway_available(gateway, bridge, monkeypatch):
    """One unacknowledged command fails only its own entity action."""
    short_timeout(monkeypatch, bridge, 0.05)
    gateway.delays[state_path("DEV1")] = 0.3
    with pytest.raises(HomeAssistantError) as raised:
        await bridge.async_turn_on("DEV1")
    assert raised.value.translation_key == "request_timeout"
    assert bridge.available is True
    assert bridge.devices["DEV1"].last_command_error is None

    # Commands for other entities are still dispatched.
    await bridge.async_turn_on("DEV2")
    assert gateway.requests_to("PUT", state_path("DEV2")) == [
        {"state": {"functions": [{"key": "switch", "value": "on"}]}}
    ]
    await wait_until(lambda: not bridge._radio_queue.locked())


async def test_priority_lock_skips_already_cancelled_waiters() -> None:
    lock = PriorityLock()
    await lock.acquire()
    stale: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    stale.cancel()
    lock._waiters.append((0, -1, stale))
    lock.release()
    assert lock.locked() is False


# ── Priority lock (moved from api.py in phase R3) ─────────────────────────


async def test_priority_lock_orders_waiters_by_priority_then_fifo() -> None:
    lock = PriorityLock()
    order: list[str] = []
    await lock.acquire()

    async def waiter(name: str, priority: int) -> None:
        await lock.acquire(priority)
        order.append(name)
        lock.release()

    tasks = [
        asyncio.create_task(waiter("background", PRIORITY_BACKGROUND)),
        asyncio.create_task(waiter("query", PRIORITY_QUERY)),
        asyncio.create_task(waiter("command-1", PRIORITY_COMMAND)),
        asyncio.create_task(waiter("command-2", PRIORITY_COMMAND)),
    ]
    await asyncio.sleep(0)
    assert lock.queue_depth == 4
    lock.release()
    await asyncio.gather(*tasks)
    assert order == ["command-1", "command-2", "query", "background"]
    assert not lock.locked()


async def test_priority_lock_cancelled_waiter_leaves_queue() -> None:
    lock = PriorityLock()
    await lock.acquire()
    task = asyncio.create_task(lock.acquire())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert lock.queue_depth == 0
    lock.release()
    assert not lock.locked()


async def test_priority_lock_cancel_after_handover_releases() -> None:
    lock = PriorityLock()
    await lock.acquire()
    task = asyncio.create_task(lock.acquire())
    await asyncio.sleep(0)
    lock.release()  # hands ownership to the waiter
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not lock.locked()
