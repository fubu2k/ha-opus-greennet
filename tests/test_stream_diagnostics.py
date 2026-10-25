"""Diagnostics of the event stream: framing, connects and queue depth.

The values come from the coordinator's cache. Reading them starts no stream,
sends no request and sends no radio telegram.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from homeassistant.core import HomeAssistant

from custom_components.opus_greennet.api import (
    OpusConnectionError,
    OpusGatewayClient,
    OpusResponseError,
    PriorityLock,
)
from custom_components.opus_greennet.coordinator import OpusGreenNetCoordinator
from custom_components.opus_greennet.diagnostics import (
    async_get_config_entry_diagnostics,
)
from tests.fake_gateway import FakeGateway, device_object
from tests.ha_helpers import setup_bridge, wait_until

MODULE = "custom_components.opus_greennet.coordinator"
DEVICES = {"header": {"content": "devices"}, "devices": []}


@pytest.fixture
async def session():
    async with aiohttp.ClientSession() as client_session:
        yield client_session


def _client(session: aiohttp.ClientSession, gateway: FakeGateway):
    config = gateway.config
    return OpusGatewayClient(
        session,
        config["host"],
        config["port"],
        config["username"],
        config["password"],
    )


# ── client: framing of the accepted stream ─────────────────────────────────


async def test_framing_is_unknown_until_a_stream_was_accepted(
    session: aiohttp.ClientSession, gateway: FakeGateway
) -> None:
    assert _client(session, gateway).stream_framing is None


@pytest.mark.parametrize(
    ("gateway_framing", "expected"),
    [("ndjson", "line"), ("legacy", "line"), ("reject", "plain")],
)
async def test_framing_names_the_request_the_gateway_accepted(
    session: aiohttp.ClientSession,
    gateway: FakeGateway,
    gateway_framing: str,
    expected: str,
) -> None:
    """A gateway that ignores the parameter still accepted the line request."""
    gateway.stream_framing = gateway_framing
    gateway.close_after_snapshot = True
    client = _client(session, gateway)
    assert [document async for document in client.async_stream()]
    assert client.stream_framing == expected


async def test_a_refused_request_does_not_change_the_framing_before_the_retry(
    session: aiohttp.ClientSession, gateway: FakeGateway
) -> None:
    gateway.status["/devices/stream"] = 503
    client = _client(session, gateway)
    with pytest.raises(OpusResponseError, match="status 503"):
        _ = [document async for document in client.async_stream()]
    assert client.stream_framing is None


# ── coordinator: counters and the diagnostic block ─────────────────────────


async def test_stream_diagnostics_count_attempts_and_connects(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [device_object("DEV1", "D2-01-01")]
    entry = await setup_bridge(hass, gateway)
    coordinator = entry.runtime_data.coordinator

    facts = coordinator.stream_diagnostics()
    depth = facts.pop("radio_queue_depth")
    assert isinstance(depth, int)
    assert facts == {
        "connected": True,
        "framing_requested": "line",
        "attempts": 1,
        "connects": 1,
        "reconnects": 0,
    }

    # The gateway ends the stream: the reader opens a second one.
    await gateway.close_streams()
    await wait_until(lambda: coordinator.stream_connects == 2, timeout=10)
    assert coordinator.stream_diagnostics()["reconnects"] == 1
    assert coordinator.stream_attempts >= 2


async def test_failed_attempts_count_but_are_no_connects() -> None:
    """An attempt that delivers no document is an attempt, not a connect."""
    coord = OpusGreenNetCoordinator(MagicMock(), MagicMock(), "AABB0011")
    coord.client.async_get = AsyncMock()
    attempts = 0

    def stream():
        nonlocal attempts
        attempts += 1
        failing = attempts <= 2

        async def play():
            if failing:
                raise OpusConnectionError("down")
            yield DEVICES

        return play()

    coord.client.async_stream = stream

    async def sleep(_delay: float) -> None:
        if attempts >= 3:
            coord._unloaded = True  # noqa: SLF001

    with (
        patch(f"{MODULE}.async_dispatcher_send"),
        patch(f"{MODULE}.asyncio.sleep", sleep),
        patch(f"{MODULE}.random.random", return_value=0.0),
    ):
        await coord._async_run_stream(coord._stream_generation)  # noqa: SLF001

    assert coord.stream_attempts == 3
    assert coord.stream_connects == 1
    assert coord.stream_diagnostics()["reconnects"] == 0


async def test_queue_depth_counts_waiting_radio_requests() -> None:
    coord = OpusGreenNetCoordinator(MagicMock(), MagicMock(), "AABB0011")
    coord.client.stream_framing = "line"
    queue = PriorityLock()
    coord._radio_queue = queue  # noqa: SLF001
    await queue.acquire()
    waiter = asyncio.create_task(queue.acquire())
    await asyncio.sleep(0)
    assert coord.stream_diagnostics()["radio_queue_depth"] == 1
    queue.release()
    await waiter
    queue.release()
    assert coord.stream_diagnostics()["radio_queue_depth"] == 0


# ── diagnostics download ───────────────────────────────────────────────────


async def test_download_contains_the_stream_block_and_stays_redacted(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [device_object("DEV1", "D2-01-01")]
    entry = await setup_bridge(hass, gateway)
    requests_before = len(gateway.requests)

    result = await async_get_config_entry_diagnostics(hass, entry)

    stream = result["gateway"]["stream"]
    assert set(stream) == {
        "connected",
        "framing_requested",
        "attempts",
        "connects",
        "reconnects",
        "radio_queue_depth",
    }
    assert stream["connected"] is True
    assert stream["framing_requested"] == "line"
    # Only the cache is read: no request, no new stream.
    assert len(gateway.requests) == requests_before
    assert gateway.stream_count == 1
    serialized = json.dumps(result)
    assert gateway.config["password"] not in serialized


async def test_download_without_a_stream_block_still_works() -> None:
    """A coordinator double without the method leaves the block out."""
    coordinator = MagicMock(spec=["eag_id", "available", "devices", "gateway_info"])
    coordinator.eag_id = "AABB0011"
    coordinator.available = True
    coordinator.devices = {}
    coordinator.gateway_info = {}
    coordinator.gateway_uptime = None
    entry = MagicMock()
    entry.entry_id = "e"
    entry.title = "t"
    entry.data = {}
    entry.runtime_data.coordinator = coordinator
    result = await async_get_config_entry_diagnostics(MagicMock(), entry)
    assert result["gateway"]["stream"] is None
