"""Gateway information: versions every 30 minutes, host name and address.

``/system/info`` is read at setup and then with the 30 minute refresh, never
with the health probe. The host name and IP of the gateway are resolved when
the diagnostics download is made; what cannot be resolved stays empty and
raises nothing.
"""

from __future__ import annotations

import ipaddress
import json
import socket
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.redact import REDACTED
from homeassistant.util import dt as dt_util

from custom_components.opus_greennet.api import OpusConnectionError
from custom_components.opus_greennet.coordinator import OpusGreenNetCoordinator
from custom_components.opus_greennet.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.opus_greennet.discovery import (
    async_gateway_address,
    async_reverse_lookup,
)
from tests.fake_gateway import FakeGateway
from tests.ha_helpers import setup_bridge

DISCOVERY = "custom_components.opus_greennet.discovery"
DIAGNOSTICS = "custom_components.opus_greennet.diagnostics"
MODULE = "custom_components.opus_greennet.coordinator"


def _info_requests(gateway: FakeGateway) -> int:
    return len(gateway.requests_to("GET", "/system/info"))


# ── coordinator: refresh with the 30 minute tick ───────────────────────────


async def test_versions_are_read_again_with_the_refresh_tick(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.system_info = {
        "version": "FW 1",
        "appVersion": "2.15.1.0",
        "apiVersion": "2.6.12.0",
        "receiveCounter": 10,
        "averageDbm": -60,
    }
    entry = await setup_bridge(hass, gateway)
    coordinator = entry.runtime_data.coordinator
    assert coordinator.gateway_info["version"] == "FW 1"
    first_read = coordinator.gateway_info_read_at
    assert first_read is not None
    before = _info_requests(gateway)

    gateway.system_info = {
        "version": "FW 2",
        "appVersion": "2.16.0.0",
        "apiVersion": "2.7.0.0",
        "receiveCounter": 99,
        "averageDbm": -55,
    }
    coordinator._async_refresh_tick(dt_util.utcnow())  # noqa: SLF001
    await coordinator._refresh_task  # noqa: SLF001

    assert _info_requests(gateway) == before + 1
    info = coordinator.gateway_info
    assert (info["version"], info["appVersion"], info["apiVersion"]) == (
        "FW 2",
        "2.16.0.0",
        "2.7.0.0",
    )
    assert (info["receiveCounter"], info["averageDbm"]) == (99, -55)
    assert coordinator.gateway_info_read_at is not None


async def test_the_health_probe_does_not_read_the_information(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    entry = await setup_bridge(hass, gateway)
    coordinator = entry.runtime_data.coordinator
    before = _info_requests(gateway)
    uptime_before = len(gateway.requests_to("GET", "/system/uptime"))
    await coordinator._async_probe()  # noqa: SLF001
    assert len(gateway.requests_to("GET", "/system/uptime")) == uptime_before + 1
    assert _info_requests(gateway) == before


@pytest.fixture
def coord() -> OpusGreenNetCoordinator:
    coordinator = OpusGreenNetCoordinator(MagicMock(), MagicMock(), "AABB0011")
    coordinator._gateway_available = True  # noqa: SLF001
    coordinator.gateway_info = {"eurid": "AABB0011", "version": "FW 1", "x": 1}
    return coordinator


async def test_a_failed_read_keeps_the_last_information(coord) -> None:
    coord.client.async_get = AsyncMock(side_effect=OpusConnectionError("down"))
    await coord._async_refresh_system_info()  # noqa: SLF001
    assert coord.gateway_info == {"eurid": "AABB0011", "version": "FW 1", "x": 1}
    assert coord.gateway_info_read_at is None


async def test_missing_fields_are_no_deletion(coord) -> None:
    coord.client.async_get = AsyncMock(
        return_value={"systemInfo": {"eurid": "AABB0011", "version": "FW 2"}}
    )
    await coord._async_refresh_system_info()  # noqa: SLF001
    assert coord.gateway_info == {"eurid": "AABB0011", "version": "FW 2", "x": 1}


async def test_an_empty_reply_changes_nothing(coord) -> None:
    coord.client.async_get = AsyncMock(return_value={"header": {}})
    await coord._async_refresh_system_info()  # noqa: SLF001
    assert coord.gateway_info["version"] == "FW 1"
    assert coord.gateway_info_read_at is None


async def test_a_reply_of_another_gateway_is_ignored(coord) -> None:
    coord.client.async_get = AsyncMock(
        return_value={"systemInfo": {"eurid": "11223344", "version": "other"}}
    )
    await coord._async_refresh_system_info()  # noqa: SLF001
    assert coord.gateway_info["version"] == "FW 1"
    assert coord.gateway_info_read_at is None


async def test_an_unavailable_gateway_is_not_asked(coord) -> None:
    coord._gateway_available = False  # noqa: SLF001
    coord.client.async_get = AsyncMock()
    await coord._async_refresh_system_info()  # noqa: SLF001
    coord.client.async_get.assert_not_called()


async def test_a_rejected_login_starts_the_auth_failure_handling(coord) -> None:
    from custom_components.opus_greennet.api import OpusAuthError

    coord.client.async_get = AsyncMock(side_effect=OpusAuthError("401"))
    with patch.object(coord, "_handle_auth_failure") as handler:
        await coord._async_refresh_system_info()  # noqa: SLF001
    handler.assert_called_once_with()


# ── discovery: host name and address ───────────────────────────────────────


async def test_a_reverse_name_is_returned() -> None:
    loop = MagicMock()
    loop.getnameinfo = AsyncMock(return_value=("opus.home", "0"))
    with patch(f"{DISCOVERY}.asyncio.get_running_loop", return_value=loop):
        name = await async_reverse_lookup(ipaddress.ip_address("192.0.2.10"))
    assert name == "opus.home"
    loop.getnameinfo.assert_awaited_once_with(("192.0.2.10", 0), socket.NI_NAMEREQD)


@pytest.mark.parametrize(
    "outcome",
    [
        socket.gaierror(socket.EAI_NONAME, "no name"),
        OSError("network down"),
        TimeoutError(),
        ValueError("bad"),
        ("192.0.2.10", "0"),  # only the numeric address came back
        ("", "0"),
    ],
)
async def test_an_unresolvable_name_stays_empty(outcome) -> None:
    loop = MagicMock()
    if isinstance(outcome, BaseException):
        loop.getnameinfo = AsyncMock(side_effect=outcome)
    else:
        loop.getnameinfo = AsyncMock(return_value=outcome)
    with patch(f"{DISCOVERY}.asyncio.get_running_loop", return_value=loop):
        assert await async_reverse_lookup(ipaddress.ip_address("192.0.2.10")) is None


# The resolver is patched in these tests; the session is not used.
HASS = MagicMock()


def _ips(*texts: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    return [ipaddress.ip_address(text) for text in texts]


async def test_a_configured_address_gets_its_reverse_name_and_other_family() -> None:
    with (
        patch(f"{DISCOVERY}.async_reverse_lookup", AsyncMock(return_value="gw.home")),
        patch(
            f"{DISCOVERY}.async_resolve_host",
            AsyncMock(return_value=_ips("192.0.2.10", "fe80::1")),
        ),
    ):
        assert await async_gateway_address(HASS, " 192.0.2.10 ") == (
            "gw.home",
            "192.0.2.10",
            "fe80::1",
        )


async def test_a_configured_address_without_a_name_has_only_its_family() -> None:
    with patch(f"{DISCOVERY}.async_reverse_lookup", AsyncMock(return_value=None)):
        assert await async_gateway_address(HASS, "192.0.2.10") == (
            None,
            "192.0.2.10",
            None,
        )
        assert await async_gateway_address(HASS, "[fe80::1]") == (None, None, "fe80::1")


async def test_a_configured_name_resolves_to_both_families() -> None:
    addresses = _ips("fe80::2", "192.0.2.10", "192.0.2.11")
    with patch(f"{DISCOVERY}.async_resolve_host", AsyncMock(return_value=addresses)):
        assert await async_gateway_address(HASS, "gw.home") == (
            "gw.home",
            "192.0.2.10",
            "fe80::2",
        )


async def test_a_configured_name_without_ipv6_leaves_it_empty() -> None:
    with patch(
        f"{DISCOVERY}.async_resolve_host", AsyncMock(return_value=_ips("192.0.2.10"))
    ):
        assert await async_gateway_address(HASS, "gw.home") == (
            "gw.home",
            "192.0.2.10",
            None,
        )


async def test_a_configured_name_that_does_not_resolve_has_no_address() -> None:
    with patch(f"{DISCOVERY}.async_resolve_host", AsyncMock(return_value=[])):
        assert await async_gateway_address(HASS, "gw.home") == ("gw.home", None, None)


async def test_a_blank_host_resolves_nothing() -> None:
    with patch(f"{DISCOVERY}.async_resolve_host", AsyncMock()) as resolve:
        assert await async_gateway_address(HASS, "  ") == (None, None, None)
    resolve.assert_not_called()


# ── diagnostics download ───────────────────────────────────────────────────


async def test_download_shows_versions_statistics_and_read_time(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.system_info = {
        "appVersion": "2.15.1.0",
        "apiVersion": "2.6.12.0",
        "receiveCounter": 2179,
        "averageDbm": -62,
    }
    entry = await setup_bridge(hass, gateway)
    result = await async_get_config_entry_diagnostics(hass, entry)
    block = result["gateway"]
    assert block["versions"] == {
        "firmware": "OPUS-GW test",
        "app": "2.15.1.0",
        "api": "2.6.12.0",
    }
    assert block["receive_counter"] == 2179
    assert block["average_dbm"] == -62
    assert isinstance(block["info_read_at"], str)


async def test_download_without_these_fields_has_empty_values(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    entry = await setup_bridge(hass, gateway)
    block = (await async_get_config_entry_diagnostics(hass, entry))["gateway"]
    assert block["versions"]["app"] is None
    assert block["versions"]["api"] is None
    assert block["receive_counter"] is None
    assert block["average_dbm"] is None


async def test_download_redacts_a_resolved_name_and_address(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    entry = await setup_bridge(hass, gateway)
    with patch(
        f"{DIAGNOSTICS}.async_gateway_address",
        AsyncMock(return_value=("gw.home", "192.0.2.10", "fe80::1")),
    ):
        result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["gateway"]["connection"] == {
        "hostname": REDACTED,
        "ipv4": REDACTED,
        "ipv6": REDACTED,
    }
    text = json.dumps(result)
    assert "gw.home" not in text
    assert "192.0.2.10" not in text
    assert "fe80::1" not in text


async def test_download_keeps_unresolved_fields_empty_without_an_error(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    entry = await setup_bridge(hass, gateway)
    with patch(
        f"{DIAGNOSTICS}.async_gateway_address",
        AsyncMock(return_value=(None, None, None)),
    ):
        result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["gateway"]["connection"] == {
        "hostname": None,
        "ipv4": None,
        "ipv6": None,
    }


async def test_download_resolves_the_configured_host_once(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    entry = await setup_bridge(hass, gateway)
    with patch(
        f"{DIAGNOSTICS}.async_gateway_address",
        AsyncMock(return_value=(None, None, None)),
    ) as resolve:
        await async_get_config_entry_diagnostics(hass, entry)
    resolve.assert_awaited_once_with(hass, entry.data["host"])
