"""Config flow for Opus GreenNet Bridge integration."""

from __future__ import annotations

import ipaddress
import logging
import re
from collections.abc import Mapping
from typing import Any

import probatio as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import (
    OpusApiError,
    OpusAuthError,
    OpusConnectionError,
    OpusResponseError,
)
from .const import CONF_EAG_ID, DEFAULT_PORT, DEFAULT_USERNAME, DOMAIN
from .discovery import (
    async_read_gateway_id,
    async_resolve_host,
    default_connection,
    is_local_address,
)

_LOGGER = logging.getLogger(__name__)

PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))

_HOSTNAME_LABEL = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")


def _is_host(host: str) -> bool:
    """Return whether ``host`` is a bare IP address or host name.

    URLs, paths, credentials and ports are rejected: they would end up in the
    request URL instead of selecting the gateway.
    """
    literal = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        ipaddress.ip_address(literal)
    except ValueError:
        pass
    else:
        return True
    labels = host.removesuffix(".").split(".")
    return len(host) <= 253 and all(_HOSTNAME_LABEL.match(label) for label in labels)


def _connection_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    """Return the connection form, prefilled with ``defaults``."""
    return vol.Schema(
        {
            vol.Required(
                CONF_HOST, default=defaults.get(CONF_HOST, vol.UNDEFINED)
            ): str,
            vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): (
                cv.port
            ),
            vol.Required(
                CONF_USERNAME, default=defaults.get(CONF_USERNAME, DEFAULT_USERNAME)
            ): str,
            vol.Required(
                CONF_PASSWORD,
                description={"suggested_value": defaults.get(CONF_PASSWORD)},
            ): PASSWORD_SELECTOR,
        }
    )


def _connection_data(user_input: Mapping[str, Any]) -> dict[str, Any]:
    return {
        CONF_HOST: user_input[CONF_HOST].strip(),
        CONF_PORT: user_input[CONF_PORT],
        CONF_USERNAME: user_input[CONF_USERNAME],
        CONF_PASSWORD: user_input[CONF_PASSWORD],
    }


class OpusGreenNetConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Opus GreenNet Bridge."""

    VERSION = 1

    _discovered_host: str | None = None

    async def _async_validate(
        self, user_input: Mapping[str, Any], errors: dict[str, str]
    ) -> str | None:
        """Return the gateway's EAG identifier, or None with ``errors`` set."""
        host = user_input[CONF_HOST].strip()
        if not _is_host(host):
            errors[CONF_HOST] = "invalid_host"
            return None
        # The gateway has no TLS: its credentials go only to the local network.
        # Every address of a host name counts, and a name that does not resolve
        # is as unreachable as before (cannot_connect).
        addresses = await async_resolve_host(host)
        if not addresses:
            errors["base"] = "cannot_connect"
            return None
        if not all(is_local_address(address) for address in addresses):
            errors[CONF_HOST] = "host_not_local"
            return None
        try:
            return await async_read_gateway_id(self.hass, user_input)
        except OpusAuthError:
            errors["base"] = "invalid_auth"
        except OpusConnectionError:
            errors["base"] = "cannot_connect"
        except OpusResponseError:
            errors["base"] = "unsupported_gateway"
        except Exception:
            _LOGGER.exception("Unexpected exception")
            errors["base"] = "unknown"
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a gateway by its address."""
        errors: dict[str, str] = {}
        if user_input is not None:
            eag_id = await self._async_validate(user_input, errors)
            if eag_id is not None:
                data = _connection_data(user_input)
                await self.async_set_unique_id(eag_id)
                # Adding a known gateway again updates its address (this also
                # completes the move of an entry from the former MQTT setup).
                self._abort_if_unique_id_configured(updates=data)
                return self.async_create_entry(
                    title=f"Opus GreenNet ({eag_id})",
                    data={CONF_EAG_ID: eag_id, **data},
                )
        defaults: dict[str, Any] = {}
        if self._discovered_host is not None:
            defaults[CONF_HOST] = self._discovered_host
        return self.async_show_form(
            step_id="user",
            data_schema=_connection_schema({**defaults, **(user_input or {})}),
            errors=errors,
        )

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Handle a gateway announced in the local network.

        A configured gateway is recognised by its EURID: an entry without an
        address (former MQTT setup) or with an outdated address is updated and
        reloaded. Any other gateway is offered with its address filled in.
        """
        if discovery_info.ip_address.version != 4:
            return self.async_abort(reason="not_ipv4_address")
        if not is_local_address(discovery_info.ip_address):
            # An announcement cannot make Home Assistant send credentials to
            # an address outside the local network.
            return self.async_abort(reason="not_local_address")
        host = discovery_info.host
        for entry in self._async_current_entries(include_ignore=False):
            if entry.data.get(CONF_HOST) == host:
                return self.async_abort(reason="already_configured")
        for entry in self._async_current_entries(include_ignore=False):
            if await self._async_adopt_address(entry.entry_id, host):
                return self.async_abort(reason="already_configured")
        # The announcement carries no EURID; its serial number keeps an
        # ignored gateway ignored. The entry itself is keyed by the EURID.
        serial = discovery_info.properties.get("serial")
        if serial:
            await self.async_set_unique_id(f"serial-{serial}")
            self._abort_if_unique_id_configured()
        self._discovered_host = host
        self.context["title_placeholders"] = {"host": host}
        return await self.async_step_user()

    async def _async_adopt_address(self, entry_id: str, host: str) -> bool:
        """Point a configured entry to ``host`` if its gateway answers there."""
        entry = self.hass.config_entries.async_get_known_entry(entry_id)
        eag_id = entry.data[CONF_EAG_ID]
        # Entries of the former MQTT setup have no credentials yet: they get
        # the vendor defaults (password = EURID).
        connection = {
            **default_connection(host, eag_id),
            **{
                key: entry.data[key]
                for key in (CONF_PORT, CONF_USERNAME, CONF_PASSWORD)
                if key in entry.data
            },
        }
        try:
            found = await async_read_gateway_id(self.hass, connection)
        except OpusApiError:
            return False
        if found != eag_id:
            return False
        _LOGGER.info("OPUS gateway %s found at a new address", eag_id)
        self.hass.config_entries.async_update_entry(
            entry, data={**entry.data, **connection}
        )
        self.hass.config_entries.async_schedule_reload(entry.entry_id)
        return True

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Ask for the address and credentials of a configured gateway."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Verify that the entered address answers as the configured gateway."""
        entry = self._get_reauth_entry()
        return await self._async_step_connection(
            "reauth_confirm", entry.data, user_input
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the address or credentials of a configured gateway."""
        entry = self._get_reconfigure_entry()
        return await self._async_step_connection("reconfigure", entry.data, user_input)

    async def _async_step_connection(
        self,
        step_id: str,
        entry_data: Mapping[str, Any],
        user_input: dict[str, Any] | None,
    ) -> ConfigFlowResult:
        eag_id = entry_data[CONF_EAG_ID]
        errors: dict[str, str] = {}
        if user_input is not None:
            found = await self._async_validate(user_input, errors)
            if found is not None and found != eag_id:
                errors["base"] = "wrong_gateway"
            elif found is not None:
                entry = (
                    self._get_reauth_entry()
                    if step_id == "reauth_confirm"
                    else self._get_reconfigure_entry()
                )
                return self.async_update_reload_and_abort(
                    entry, data_updates=_connection_data(user_input)
                )
        # The vendor default password is the gateway's EURID.
        defaults = {CONF_PASSWORD: eag_id, **entry_data, **(user_input or {})}
        return self.async_show_form(
            step_id=step_id,
            data_schema=_connection_schema(defaults),
            errors=errors,
            description_placeholders={"eag_id": eag_id},
        )
