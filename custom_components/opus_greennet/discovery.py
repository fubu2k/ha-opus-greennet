"""Find OPUS gateways in the local network.

OPUS gateways announce the service ``_enocean._tcp`` over mDNS (instance
``opusshgw``, port 80 for the web interface). The announcement carries no
EURID, so a candidate is identified by reading ``/system/info`` of its REST
API. With the vendor default credentials (user ``admin``, password = EURID)
an entry of the former MQTT transport, which only knows the EURID, can find
its gateway without asking for the address.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from homeassistant.components import zeroconf as ha_zeroconf
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from zeroconf import IPVersion, ServiceStateChange, Zeroconf
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo

from .api import OpusApiError, OpusAuthError, OpusGatewayClient, OpusResponseError
from .const import DEFAULT_PORT, DEFAULT_USERNAME
from .wire import as_object

_LOGGER = logging.getLogger(__name__)

ZEROCONF_TYPE: Final = "_enocean._tcp.local."
# Time to collect announcements; answers from the shared cache arrive at once.
BROWSE_SECONDS: Final = 3.0
RESOLVE_TIMEOUT_MS: Final = 3000

# The EAG identifier is the gateway's EURID: eight hexadecimal characters.
EAG_ID_PATTERN = re.compile(r"[0-9A-Fa-f]{8}")


async def async_read_gateway_id(hass: HomeAssistant, data: Mapping[str, Any]) -> str:
    """Connect to the gateway and return its EAG identifier (EURID).

    Raises ``OpusApiError`` subclasses for connection, credential and protocol
    failures.
    """
    client = OpusGatewayClient(
        async_get_clientsession(hass),
        data[CONF_HOST].strip(),
        data[CONF_PORT],
        data[CONF_USERNAME],
        data[CONF_PASSWORD],
    )
    document = await client.async_get("/system/info")
    info = as_object(document.get("systemInfo")) or {}
    eurid = info.get("eurid")
    if not isinstance(eurid, str) or not EAG_ID_PATTERN.fullmatch(eurid):
        raise OpusResponseError("The gateway did not report its EURID")
    return eurid.upper()


def default_connection(host: str, eag_id: str) -> dict[str, Any]:
    """Return the connection data with the vendor default credentials."""
    return {
        CONF_HOST: host,
        CONF_PORT: DEFAULT_PORT,
        CONF_USERNAME: DEFAULT_USERNAME,
        CONF_PASSWORD: eag_id,
    }


async def async_browse_gateway_hosts(hass: HomeAssistant) -> list[str]:
    """Return the IPv4 addresses of all devices announcing ``_enocean._tcp``.

    Empty when Home Assistant's zeroconf integration is not running.
    """
    if "zeroconf" not in hass.config.components:
        return []
    aiozc = await ha_zeroconf.async_get_async_instance(hass)
    names: set[str] = set()

    def _on_change(
        zeroconf: Zeroconf,
        service_type: str,
        name: str,
        state_change: ServiceStateChange,
    ) -> None:
        if state_change is not ServiceStateChange.Removed:
            names.add(name)

    browser = AsyncServiceBrowser(
        aiozc.zeroconf, [ZEROCONF_TYPE], handlers=[_on_change]
    )
    try:
        await asyncio.sleep(BROWSE_SECONDS)
    finally:
        await browser.async_cancel()

    hosts: list[str] = []
    for name in sorted(names):
        info = AsyncServiceInfo(ZEROCONF_TYPE, name)
        if not await info.async_request(aiozc.zeroconf, RESOLVE_TIMEOUT_MS):
            continue
        for address in info.parsed_addresses(IPVersion.V4Only):
            if address not in hosts:
                hosts.append(address)
    return hosts


@dataclass(frozen=True, slots=True)
class LocatedGateway:
    """A gateway address found for a known EURID."""

    host: str
    # False: the only candidate rejected the default credentials, so its
    # identity is unconfirmed; the user still has to enter the password.
    verified: bool


async def async_locate_gateway(
    hass: HomeAssistant, eag_id: str
) -> LocatedGateway | None:
    """Find the address of gateway ``eag_id`` in the local network."""
    hosts = await async_browse_gateway_hosts(hass)
    rejected: list[str] = []
    for host in hosts:
        try:
            found = await async_read_gateway_id(hass, default_connection(host, eag_id))
        except OpusAuthError:
            rejected.append(host)
            continue
        except OpusApiError as err:
            _LOGGER.debug("Gateway candidate %s did not answer: %s", host, err)
            continue
        if found == eag_id:
            return LocatedGateway(host, verified=True)
    if len(hosts) == 1 and rejected:
        return LocatedGateway(rejected[0], verified=False)
    return None
