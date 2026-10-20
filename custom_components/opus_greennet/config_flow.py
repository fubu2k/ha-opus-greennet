"""Config flow for Opus GreenNet Bridge integration."""

from __future__ import annotations

import logging
from typing import Any

import probatio as vol
from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.service_info.mqtt import MqttServiceInfo

from .const import CONF_EAG_ID, DOMAIN
from .discovery import eag_id_from_discovery, normalize_eag_id
from .mqtt_transport import async_probe_gateway

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EAG_ID): str,
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """
    eag_id = normalize_eag_id(data[CONF_EAG_ID])

    # Validate EAG ID format
    if eag_id is None:
        raise InvalidEagId

    # Check if MQTT is available
    if not mqtt.is_connected(hass):
        raise CannotConnect

    try:
        await async_probe_gateway(hass, eag_id)
    except HomeAssistantError as err:
        raise GatewayUnavailable from err

    return {"title": f"Opus GreenNet ({eag_id})", "eag_id": eag_id}


class OpusGreenNetConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Opus GreenNet Bridge."""

    VERSION = 1

    _discovered_eag_id: str = ""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        # Check if MQTT integration is available
        if not await mqtt.async_wait_for_mqtt_client(self.hass):
            return self.async_abort(reason="mqtt_not_configured")

        if user_input is not None:
            eag_id = normalize_eag_id(user_input[CONF_EAG_ID])
            if eag_id is not None:
                await self.async_set_unique_id(eag_id)
                self._abort_if_unique_id_configured()
            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except GatewayUnavailable:
                errors["base"] = "gateway_unavailable"
            except InvalidEagId:
                errors[CONF_EAG_ID] = "invalid_eag_id"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=info["title"],
                    data={CONF_EAG_ID: info["eag_id"]},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_mqtt(
        self, discovery_info: MqttServiceInfo
    ) -> ConfigFlowResult:
        """Handle a gateway candidate announced by an OPUS device-model topic.

        The message, retained or live, only identifies a candidate; it is not
        evidence that the gateway is reachable. This step publishes nothing.
        """
        eag_id = eag_id_from_discovery(discovery_info.topic, discovery_info.payload)
        if eag_id is None:
            return self.async_abort(reason="invalid_discovery_info")
        # Every device of a gateway announces the same identifier. Known and
        # ignored gateways abort before a unique ID is set, so repeated device
        # messages neither rewrite the entry's discovery keys nor reload an
        # entry that is waiting to retry its setup.
        if eag_id in self._async_current_ids():
            return self.async_abort(reason="already_configured")
        # Further announcements of this gateway abort as already in progress.
        await self.async_set_unique_id(eag_id)
        self._abort_if_unique_id_configured()
        self._discovered_eag_id = eag_id
        self.context["title_placeholders"] = {"eag_id": eag_id}
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a discovered gateway, then run the same fresh check as setup."""
        errors: dict[str, str] = {}
        eag_id = self._discovered_eag_id

        if user_input is not None:
            # An entry for this gateway may have appeared while the form was open.
            self._abort_if_unique_id_configured()
            try:
                info = await validate_input(self.hass, {CONF_EAG_ID: eag_id})
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except GatewayUnavailable:
                errors["base"] = "gateway_unavailable"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=info["title"],
                    data={CONF_EAG_ID: info["eag_id"]},
                )

        self._set_confirm_only()
        return self.async_show_form(
            step_id="discovery_confirm",
            description_placeholders={"eag_id": eag_id},
            errors=errors,
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidEagId(HomeAssistantError):
    """Error to indicate the EAG ID is invalid."""


class GatewayUnavailable(HomeAssistantError):
    """The broker is connected but the specified gateway did not respond."""
