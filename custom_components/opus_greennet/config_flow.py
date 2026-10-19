"""Config flow for Opus GreenNet Bridge integration."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

import probatio as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    OpusAuthError,
    OpusConnectionError,
    OpusGatewayClient,
    OpusResponseError,
)
from .const import CONF_EAG_ID, DEFAULT_PORT, DEFAULT_USERNAME, DOMAIN
from .wire import as_object

_LOGGER = logging.getLogger(__name__)

# The EAG identifier is the gateway's EURID: eight hexadecimal characters.
EAG_ID_PATTERN = re.compile(r"[0-9A-Fa-f]{8}")
PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


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

    async def _async_validate(
        self, user_input: Mapping[str, Any], errors: dict[str, str]
    ) -> str | None:
        """Return the gateway's EAG identifier, or None with ``errors`` set."""
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
        return self.async_show_form(
            step_id="user",
            data_schema=_connection_schema(user_input or {}),
            errors=errors,
        )

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
