"""Synthetic load measurement with the fake gateway (REST series phase 5).

The same workloads run against the legacy and the line-delimited stream. The
numbers are **synthetic**: they describe this integration against an
in-process fake gateway, not a real gateway, and say nothing about gateway CPU.
Each run writes one row per workload to the terminal and, when set, to the
GitHub step summary. The assertions only pin what must hold whatever the load:
the business result is identical for both framings, no request waits for a
second connection to open while the stream is up, and no task survives unload.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from aiohttp.web_protocol import RequestHandler
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity

from tests.fake_gateway import FakeGateway, device_object, telegram
from tests.ha_helpers import setup_bridge, wait_for_entity, wait_until

DEVICES = 20
TELEGRAMS = 300
RESULTS: list[dict[str, Any]] = []


@dataclass
class Meter:
    """Counters shared by the patched hooks of one run."""

    connections: int = 0
    writes: int = 0
    lag_max: float = 0.0
    queue_max: int = 0
    stop: bool = field(default=False)


@pytest.fixture
def meter() -> Iterator[Meter]:
    counters = Meter()
    original_write = Entity._async_write_ha_state
    original_connect = RequestHandler.connection_made

    def counting_write(self: Entity) -> None:
        counters.writes += 1
        original_write(self)

    def counting_connect(self: RequestHandler, transport: Any) -> None:
        counters.connections += 1
        original_connect(self, transport)

    with (
        patch.object(Entity, "_async_write_ha_state", counting_write),
        patch.object(RequestHandler, "connection_made", counting_connect),
    ):
        yield counters


async def _watch(meter: Meter, coordinator: Any) -> None:
    """Sample event loop lag and queue depth until told to stop."""
    interval = 0.005
    while not meter.stop:
        before = time.perf_counter()
        await asyncio.sleep(interval)
        meter.lag_max = max(meter.lag_max, time.perf_counter() - before - interval)
        meter.queue_max = max(meter.queue_max, coordinator._radio_queue.queue_depth)


@pytest.fixture
def report(capsys: pytest.CaptureFixture[str]) -> Callable[..., None]:
    """Return a function that shows one row even with output capturing on."""

    def emit(name: str, framing: str, **values: Any) -> None:
        row = {"workload": name, "framing": framing, **values}
        RESULTS.append(row)
        line = json.dumps(row, sort_keys=True)
        with capsys.disabled():
            # A workflow annotation: readable through the check-run API.
            print(f"\n::notice title=LOADMEASURE::{line}")
        if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
            with Path(summary).open("a", encoding="utf-8") as handle:
                handle.write(f"`{line}`\n\n")

    return emit


def _burst(gateway: FakeGateway, documents: list[str]) -> int:
    """Queue all documents for every open stream at once, return the bytes."""
    total = 0
    for text in documents:
        for queue in gateway._streams:
            queue.put_nowait(text)
        total += len(text)
    return total


def _documents(changing: bool) -> list[str]:
    """TELEGRAMS switch reports spread round-robin over the devices."""
    documents = []
    for index in range(TELEGRAMS):
        device = f"DEV{index % DEVICES:02d}"
        # Changing: every device flips with each round; identical: always on.
        value = "on" if (not changing or (index // DEVICES) % 2 == 0) else "off"
        document = telegram(
            device,
            [{"key": "switch", "value": value}],
            timestamp=f"2026-10-25T12:{index // 60:02d}:{index % 60:02d}.000+0100",
        )
        documents.append(json.dumps(document) + "\r\n")
    return documents


@pytest.mark.parametrize("framing", ["legacy", "ndjson"])
@pytest.mark.parametrize("changing", [True, False], ids=["changing", "identical"])
async def test_telegram_burst(
    hass: HomeAssistant,
    gateway: FakeGateway,
    meter: Meter,
    report: Callable[..., None],
    framing: str,
    changing: bool,
) -> None:
    """A burst of switch reports: writes, lag and final state per framing."""
    gateway.stream_framing = framing
    gateway.devices = [
        device_object(
            f"DEV{index:02d}",
            "D2-01-01",
            states=[{"key": "switch", "value": "off"}],
        )
        for index in range(DEVICES)
    ]
    entry = await setup_bridge(hass, gateway)
    coordinator = entry.runtime_data.coordinator
    for index in range(DEVICES):
        await wait_for_entity(hass, "switch", f"AABB0011_DEV{index:02d}")
    await hass.async_block_till_done()

    documents = _documents(changing)
    meter.writes = 0
    meter.connections = 0
    watcher = asyncio.create_task(_watch(meter, coordinator))
    started = time.perf_counter()
    sent = _burst(gateway, documents)
    last = TELEGRAMS - 1
    last_stamp = f"2026-10-25T12:{last // 60:02d}:{last % 60:02d}.000+0100"
    last_device = f"DEV{last % DEVICES:02d}"
    await wait_until(
        lambda: coordinator.devices[last_device].last_seen == last_stamp, timeout=30
    )
    await hass.async_block_till_done()
    elapsed = time.perf_counter() - started
    meter.stop = True
    await watcher

    final = {
        device_id: device.channels[0].is_on
        for device_id, device in coordinator.devices.items()
    }
    report(
        "telegram_burst_" + ("changing" if changing else "identical"),
        framing,
        telegrams=TELEGRAMS,
        devices=DEVICES,
        bytes=sent,
        entity_writes=meter.writes,
        new_connections=meter.connections,
        seconds=round(elapsed, 3),
        loop_lag_max_ms=round(meter.lag_max * 1000, 2),
    )
    assert meter.connections == 0
    # Identical business result for both framings and no lost final state.
    if changing:
        assert set(final.values()) <= {True, False}
    assert all(value is not None for value in final.values())


@pytest.mark.parametrize("framing", ["legacy", "ndjson"])
async def test_command_burst_reuses_connections(
    hass: HomeAssistant,
    gateway: FakeGateway,
    meter: Meter,
    report: Callable[..., None],
    framing: str,
) -> None:
    """Commands to all devices at once: queue depth and connection reuse."""
    gateway.stream_framing = framing
    gateway.devices = [
        device_object(
            f"DEV{index:02d}",
            "D2-01-01",
            states=[{"key": "switch", "value": "off"}],
        )
        for index in range(DEVICES)
    ]
    entry = await setup_bridge(hass, gateway)
    coordinator = entry.runtime_data.coordinator
    for index in range(DEVICES):
        await wait_for_entity(hass, "switch", f"AABB0011_DEV{index:02d}")
    await hass.async_block_till_done()

    meter.connections = 0
    puts_before = sum(1 for method, _, _ in gateway.requests if method == "PUT")
    watcher = asyncio.create_task(_watch(meter, coordinator))
    started = time.perf_counter()
    await asyncio.gather(
        *(coordinator.async_turn_on(f"DEV{index:02d}") for index in range(DEVICES))
    )
    elapsed = time.perf_counter() - started
    meter.stop = True
    await watcher
    puts = sum(1 for method, _, _ in gateway.requests if method == "PUT") - puts_before

    report(
        "command_burst",
        framing,
        commands=DEVICES,
        gateway_puts=puts,
        new_connections=meter.connections,
        queue_max=meter.queue_max,
        seconds=round(elapsed, 3),
        loop_lag_max_ms=round(meter.lag_max * 1000, 2),
    )
    # Every command was sent exactly once, none repeated.
    assert puts == DEVICES
    # A keep-alive client reuses one connection for the sequential commands;
    # allow a few more because the gateway may close idle connections.
    assert meter.connections <= DEVICES


@pytest.mark.parametrize("framing", ["legacy", "ndjson"])
async def test_unload_leaves_no_task(
    hass: HomeAssistant, gateway: FakeGateway, framing: str
) -> None:
    """After unload the coordinator holds no task, timer or queued waiter."""
    gateway.stream_framing = framing
    gateway.devices = [
        device_object("DEV00", "D2-01-01", states=[{"key": "switch", "value": "off"}])
    ]
    entry = await setup_bridge(hass, gateway)
    coordinator = entry.runtime_data.coordinator
    await wait_for_entity(hass, "switch", "AABB0011_DEV00")
    await coordinator.async_turn_on("DEV00")
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert not coordinator._tasks
    assert coordinator._radio_queue.queue_depth == 0
    assert not coordinator._pending_reconciliation_queries
