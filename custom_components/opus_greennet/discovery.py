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
import ipaddress
import logging
import re
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from homeassistant.components import network
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

type IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
type IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

# The gateway speaks plain HTTP with basic authentication (no TLS), so its
# credentials must never travel beyond the local network. Allowlist, not a
# "not global" test: Python's private/reserved flags also cover ranges that
# are no local network (::/8 would reject ::1, 2001::/23 contains Teredo).
LOCAL_NETWORKS: Final[tuple[IPNetwork, ...]] = tuple(
    ipaddress.ip_network(network)
    for network in (
        "10.0.0.0/8",  # RFC 1918
        "172.16.0.0/12",
        "192.168.0.0/16",
        "169.254.0.0/16",  # link-local
        "127.0.0.0/8",  # loopback (tunnels, tests)
        "100.64.0.0/10",  # carrier-grade NAT, also VPN meshes
        "::1/128",
        "fe80::/10",  # link-local
        "fc00::/7",  # unique local
    )
)
# Upper bound for resolving a host name before the connection attempt.
RESOLVE_HOST_SECONDS: Final = 5.0


def parse_host_literal(host: str) -> IPAddress | None:
    """Return ``host`` as an IP address, or None for a host name."""
    literal = host.strip()
    if literal.startswith("[") and literal.endswith("]"):
        literal = literal[1:-1]
    try:
        return ipaddress.ip_address(literal)
    except ValueError:
        return None


def is_local_address(
    address: IPAddress, networks: tuple[IPNetwork, ...] = LOCAL_NETWORKS
) -> bool:
    """Return whether ``address`` lies in one of ``networks``.

    An IPv4-mapped IPv6 address (``::ffff:192.168.1.5``) is judged by the IPv4
    address it carries.
    """
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return any(address in network for network in networks)


async def async_local_networks(hass: HomeAssistant) -> tuple[IPNetwork, ...]:
    """Return ``LOCAL_NETWORKS`` plus the networks Home Assistant is attached to.

    A gateway on the same link is local even with a global IPv6 address, as
    on dual-stack home networks.
    """
    networks = list(LOCAL_NETWORKS)
    if "network" in hass.config.components:
        for adapter in await network.async_get_adapters(hass):
            if not adapter["enabled"]:
                continue
            for configured in (*adapter["ipv4"], *adapter["ipv6"]):
                networks.append(
                    ipaddress.ip_network(
                        f"{configured['address']}/{configured['network_prefix']}",
                        strict=False,
                    )
                )
    return tuple(networks)


async def async_resolve_host(hass: HomeAssistant, host: str) -> list[IPAddress]:
    """Return every address ``host`` resolves to.

    The name is resolved like the connection resolves it (Home Assistant's
    shared session, including mDNS names), so the check and the connection
    agree. A host that is an IP address answers itself. The result is empty
    when the name does not resolve, the lookup takes longer than
    ``RESOLVE_HOST_SECONDS`` or it returns something that is no address, so the
    caller treats it as unreachable instead of trusting it.
    """
    literal = parse_host_literal(host)
    if literal is not None:
        return [literal]
    connector = async_get_clientsession(hass).connector
    try:
        async with asyncio.timeout(RESOLVE_HOST_SECONDS):
            resolve = getattr(connector, "async_resolve_host", None)
            if resolve is not None:
                return [
                    ipaddress.ip_address(str(found["host"]))
                    for found in await resolve(host.strip())
                ]
            entries = await asyncio.get_running_loop().getaddrinfo(
                host.strip(), None, type=socket.SOCK_STREAM
            )
        return [ipaddress.ip_address(str(entry[4][0])) for entry in entries]
    except OSError, TimeoutError, ValueError:
        return []


async def async_reverse_lookup(address: IPAddress) -> str | None:
    """Return the host name of ``address``, or None when there is none.

    A missing reverse entry, a timeout or an answer that is only the numeric
    address is no error: the caller keeps the field empty.
    """
    try:
        async with asyncio.timeout(RESOLVE_HOST_SECONDS):
            name, _service = await asyncio.get_running_loop().getnameinfo(
                (str(address), 0), socket.NI_NAMEREQD
            )
    except OSError, TimeoutError, ValueError:
        return None
    if not name or name == str(address):
        return None
    return name


async def async_gateway_address(host: str) -> tuple[str | None, str | None]:
    """Return ``(hostname, ip)`` of the configured gateway host for diagnostics.

    Never raises. A configured name is the host name and resolves to the first
    address; a configured address is the IP and gets its host name from a
    reverse lookup. Whatever cannot be resolved stays None.
    """
    literal = parse_host_literal(host)
    if literal is not None:
        return await async_reverse_lookup(literal), str(literal)
    name = host.strip() or None
    addresses = await async_resolve_host(host) if name else []
    return name, str(addresses[0]) if addresses else None


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

    networks = await async_local_networks(hass)
    hosts: list[str] = []
    for name in sorted(names):
        info = AsyncServiceInfo(ZEROCONF_TYPE, name)
        if not await info.async_request(aiozc.zeroconf, RESOLVE_TIMEOUT_MS):
            continue
        for address in info.parsed_addresses(IPVersion.V4Only):
            # Credentials are tried against announced addresses: a forged
            # announcement of a public address must not receive them.
            if not is_local_address(ipaddress.ip_address(address), networks):
                _LOGGER.debug("Ignoring announced gateway address outside the LAN")
                continue
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
