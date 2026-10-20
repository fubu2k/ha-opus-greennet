"""Shared fixtures for Opus GreenNet tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from homeassistant import loader
from homeassistant.config_entries import ConfigEntries
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from custom_components.opus_greennet.coordinator import OpusGreenNetCoordinator
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from custom_components.opus_greennet.rest_coordinator import (
    OpusGreenNetRestCoordinator,
)
from tests.fake_gateway import FakeGateway
from tests.ha_helpers import MQTTTransport


class _PlainResolver(aiohttp.ThreadedResolver):
    """Home Assistant's resolver without zeroconf (no network component here)."""

    async def real_close(self) -> None:
        await self.close()


@pytest.fixture
async def hass(tmp_path: Path) -> AsyncIterator[HomeAssistant]:
    """Run real Home Assistant managers without starting external integrations.

    Import Home Assistant before the custom integration so its validation
    backend is initialized in the same order as a normal HA startup.
    """
    component_directory = Path(__file__).resolve().parents[1] / "custom_components"
    (tmp_path / "custom_components").symlink_to(component_directory)
    # The REST transport uses Home Assistant's shared aiohttp session; its
    # default resolver needs the network component, which is not started here.
    resolver = patch(
        "homeassistant.helpers.aiohttp_client._async_make_resolver",
        side_effect=lambda hass: _PlainResolver(),
    )
    resolver.start()
    hass = HomeAssistant(str(tmp_path))
    loader.async_setup(hass)
    hass.config_entries = ConfigEntries(hass, {})
    dr.async_setup(hass)
    await asyncio.gather(
        ar.async_load(hass, load_empty=True),
        dr.async_load(hass, load_empty=True),
        er.async_load(hass, load_empty=True),
        ir.async_load(hass, load_empty=True),
    )
    await hass.config_entries.async_initialize()
    # MQTT's network boundary is provided by each test's transport fixture.
    hass.config.components.add("mqtt")
    try:
        yield hass
    finally:
        for entry in hass.config_entries.async_entries("opus_greennet"):
            await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_stop(force=True)
        resolver.stop()


@pytest.fixture
async def hass_without_mqtt(hass: HomeAssistant) -> HomeAssistant:
    """Home Assistant without the MQTT integration (REST-only, plan K5).

    The shared ``hass`` fixture marks MQTT as set up for the MQTT tests. This
    fixture removes that mark again, so a test sees an installation in which
    the MQTT integration was never configured.
    """
    hass.config.components.remove("mqtt")
    return hass


@pytest.fixture
async def gateway() -> AsyncIterator[FakeGateway]:
    """Run a fake OPUS gateway serving the REST API on a local port."""
    fake = FakeGateway()
    await fake.start()
    try:
        yield fake
    finally:
        await fake.stop()


@pytest.fixture
def mqtt_transport(hass: HomeAssistant) -> Iterator[MQTTTransport]:
    """Replace only the MQTT client network boundary."""
    transport = MQTTTransport(hass)
    with (
        patch(
            "homeassistant.components.mqtt.async_wait_for_mqtt_client",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "homeassistant.components.mqtt.is_connected",
            side_effect=lambda hass: transport.connected,
        ),
        patch("homeassistant.components.mqtt.async_subscribe", transport.subscribe),
        patch(
            "homeassistant.components.mqtt.async_on_subscribe_done",
            transport.subscribed,
        ),
        patch("homeassistant.components.mqtt.async_publish", transport.publish),
    ):
        yield transport


@pytest.fixture
def make_device():
    """Factory fixture for creating EnOceanDevice instances."""

    def _make(
        eep: str,
        device_id: str = "AABB1122",
        friendly_id: str = "Test Device",
    ) -> EnOceanDevice:
        return EnOceanDevice(
            device_id=device_id,
            friendly_id=friendly_id,
            eeps=[{"eep": eep}],
        )

    return _make


@pytest.fixture
def make_telegram():
    """Factory fixture for creating telegram dicts."""

    def _make(
        functions: list[dict],
        timestamp: str | None = None,
        dbm: int | None = None,
    ) -> dict:
        telegram: dict = {"functions": functions}
        if timestamp:
            telegram["timestamp"] = timestamp
        if dbm is not None:
            telegram["telegramInfo"] = {"dbm": dbm}
        return telegram

    return _make


@pytest.fixture
def coordinator():
    """Create a coordinator with mocked hass and patched async_send_command."""
    hass = MagicMock()
    c = OpusGreenNetCoordinator(hass, "AABB0011")
    c.async_send_command = AsyncMock()
    return c


@pytest.fixture
def rest_coordinator():
    """Create a REST coordinator with mocked hass, client and async_send_command."""
    c = OpusGreenNetRestCoordinator(MagicMock(), MagicMock(), "AABB0011")
    c.async_send_command = AsyncMock()
    return c


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """TEMPORARY (phase R2 diagnosis): report failures as GitHub annotations."""
    import os
    import sys

    if os.environ.get("GITHUB_ACTIONS") != "true" or not report.failed:
        return
    text = str(report.longrepr)[-1800:]
    for old, new in (("%", "%25"), ("\r", "%0D"), ("\n", "%0A")):
        text = text.replace(old, new)
    sys.__stdout__.write(f"\n::error title={report.nodeid}::{text}\n")
    sys.__stdout__.flush()
