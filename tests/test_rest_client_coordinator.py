"""REST client hardening as seen by the coordinator (REST plan phase R3).

The coordinator is set up through the real config flow against the
in-process ``FakeGateway``; only HTTP status lines and delays are forced.
Report: ``docs/quality/phases/phase-17.md``.
"""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from custom_components.opus_greennet import api, rest_coordinator
from custom_components.opus_greennet.domain import CommandDelivery
from custom_components.opus_greennet.rest_coordinator import (
    PRIORITY_BACKGROUND,
    PRIORITY_COMMAND,
    PRIORITY_QUERY,
    OpusGreenNetRestCoordinator,
    PriorityLock,
)
from tests.fake_gateway import FakeGateway, device_object
from tests.ha_helpers import setup_rest_bridge, wait_until

STATE = "/devices/DEV1/state"
SWITCH_ON = {"state": {"functions": [{"key": "switch", "value": "on"}]}}


@pytest.fixture
async def bridge(
    hass: HomeAssistant, gateway: FakeGateway
) -> OpusGreenNetRestCoordinator:
    gateway.devices = [
        device_object("DEV1", "D2-01-01", states=[{"key": "switch", "value": "off"}])
    ]
    entry = await setup_rest_bridge(hass, gateway)
    return entry.runtime_data.coordinator


async def _probe(coordinator: OpusGreenNetRestCoordinator) -> None:
    """Run one health probe as the minute timer does."""
    coordinator._async_health_tick(dt_util.utcnow())  # noqa: SLF001
    assert coordinator._probe_task is not None  # noqa: SLF001
    await coordinator._probe_task  # noqa: SLF001


def test_priority_lock_and_priorities_live_in_the_rest_coordinator() -> None:
    """B-6: the queue moved without changing its priorities (0 < 10 < 20)."""
    assert (PRIORITY_COMMAND, PRIORITY_QUERY, PRIORITY_BACKGROUND) == (0, 10, 20)
    assert PriorityLock.__module__ == rest_coordinator.__name__
    assert not hasattr(api, "PriorityLock")
    assert not hasattr(api, "PRIORITY_COMMAND")


async def test_coordinator_uses_the_moved_queue(bridge) -> None:
    assert isinstance(bridge._radio_queue, PriorityLock)  # noqa: SLF001


async def test_http_error_with_json_success_fails_the_command(
    gateway: FakeGateway, bridge: OpusGreenNetRestCoordinator
) -> None:
    """B-1 reproducer: before R3 this HTTP 500 counted as an acknowledgement."""
    gateway.http_status[STATE] = 500
    with pytest.raises(HomeAssistantError) as raised:
        await bridge.async_turn_on("DEV1")
    assert raised.value.translation_key == "request_rejected"
    reason = (raised.value.translation_placeholders or {}).get("reason")
    assert reason == "HTTP status 500, gateway status 200"
    assert bridge.devices["DEV1"].last_command_error == reason
    # No optimistic state, no retry, the gateway stays available.
    assert bridge.devices["DEV1"].channels[0].is_on is not True
    assert bridge.available
    await asyncio.sleep(0.1)
    assert gateway.requests_to("PUT", STATE) == [SWITCH_ON]


async def test_http_success_keeps_json_status_semantics(
    gateway: FakeGateway, bridge: OpusGreenNetRestCoordinator
) -> None:
    """HTTP 2xx with JSON 201 stays SoftSmartAck (deferred, phase 11)."""
    gateway.responses[STATE] = {"header": {"httpStatus": 201}}
    assert await bridge.async_turn_on("DEV1") is CommandDelivery.DEFERRED
    gateway.responses[STATE] = {"header": {"httpStatus": 200}}
    gateway.http_status[STATE] = 201
    assert await bridge.async_turn_on("DEV1") is CommandDelivery.SENT


async def test_command_timeout_is_never_repeated(
    gateway: FakeGateway,
    bridge: OpusGreenNetRestCoordinator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A PUT that timed out may have reached the actuator: no second PUT."""
    original = bridge.client.async_put

    async def short_put(path, body, *, timeout=0.05):
        return await original(path, body, timeout=0.05)

    monkeypatch.setattr(bridge.client, "async_put", short_put)
    gateway.delays[STATE] = 0.3
    with pytest.raises(HomeAssistantError) as raised:
        await bridge.async_turn_on("DEV1")
    assert raised.value.translation_key == "request_timeout"
    assert bridge.available
    await asyncio.sleep(0.5)
    assert gateway.requests_to("PUT", STATE) == [SWITCH_ON]
    await wait_until(lambda: not bridge._radio_queue.locked())  # noqa: SLF001


async def test_health_probe_rejects_http_error_with_json_success(
    gateway: FakeGateway, bridge: OpusGreenNetRestCoordinator
) -> None:
    """An uptime answer with HTTP 500 is a failed probe even if its body is valid."""
    streams = len(gateway.requests_to("GET", "/devices/stream"))
    gateway.http_status["/system/uptime"] = 500
    await _probe(bridge)
    assert not bridge.available
    # Unchanged recovery path: the stream is replaced and reconnects.
    await wait_until(
        lambda: len(gateway.requests_to("GET", "/devices/stream")) > streams
    )
    await wait_until(lambda: bridge.available)
    del gateway.http_status["/system/uptime"]
    await _probe(bridge)
    assert bridge.available
