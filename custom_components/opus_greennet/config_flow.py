"""Config flow for Opus GreenNet Bridge integration.

New gateways are added through the gateway's EnOcean over IP REST API only
(phase R4.3): the user step asks for address and credentials, and the gateway
reports its EAG identifier (EURID). A gateway announced on the MQTT broker
(MQTT discovery) is offered for setup with the same REST form; the address
must answer as the announced gateway.

Entries that already use the MQTT bridge keep it (guardrail A5). The
reconfigure flow switches an existing entry between REST and MQTT without
changing its devices or entities; it is the only way to choose MQTT.

The EAG identifier is the unique ID with both transports, so one gateway can
only be configured once.

The MQTT steps import Home Assistant's MQTT integration and the MQTT transport
only when they run (phase R2, plan E4). Home Assistant loads this module with
every entry, so a REST-only installation never imports MQTT.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

import probatio as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.importlib import async_import_module
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.service_info.mqtt import MqttServiceInfo

from .api import (
    OpusAuthError,
    OpusConnectionError,
    OpusGatewayClient,
    OpusResponseError,
)
from .const import (
    CONF_EAG_ID,
    CONF_TRANSPORT,
    DEFAULT_PORT,
    DEFAULT_USERNAME,
    DOMAIN,
    TRANSPORT_REST,
)
from .wire import JsonObject, as_object

_LOGGER = logging.getLogger(__name__)

# The EAG identifier is the gateway's EURID: eight hexadecimal characters.
EAG_ID_PATTERN = re.compile(r"[0-9A-Fa-f]{8}")
PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))

STEP_RECONFIGURE_REST = "reconfigure_rest"
STEP_RECONFIGURE_MQTT = "reconfigure_mqtt"

# Imported by the MQTT steps on first use, in the executor.
_MQTT_MODULES = (
    "homeassistant.components.mqtt",
    f"{__package__}.mqtt_transport",
    f"{__package__}.discovery",
)


def __getattr__(name: str) -> object:
    """Resolve ``mqtt`` on first use (PEP 562).

    ``mqtt`` stays a module attribute as before; Home Assistant's MQTT
    integration is imported only when it is actually used.
    """
    if name == "mqtt":
        from homeassistant.components import mqtt

        return mqtt
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


async def _async_import_mqtt(hass: HomeAssistant) -> None:
    """Import the MQTT integration and transport for an MQTT step.

    ``async_import_module`` imports in the executor once and then returns the
    cached module, so the event loop never waits for an MQTT import.
    """
    for module in _MQTT_MODULES:
        await async_import_module(hass, module)


async def async_probe_gateway(hass: HomeAssistant, eag_id: str) -> JsonObject:
    """Probe an MQTT-bridged gateway with the unchanged MQTT transport probe."""
    await _async_import_mqtt(hass)
    from .mqtt_transport import async_probe_gateway as async_probe_mqtt_gateway

    return await async_probe_mqtt_gateway(hass, eag_id)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Check that an MQTT-bridged gateway answers (reconfigure step).

    ``data`` holds the EAG identifier (key ``eag_id``).
    """
    await _async_import_mqtt(hass)
    from homeassistant.components import mqtt

    from .discovery import normalize_eag_id

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


def _connection_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    """Return the REST connection form, prefilled with ``defaults``."""
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
    """Connect to the gateway's REST API and return its EAG identifier (EURID).

    Reads only ``GET /system/info``, a gateway endpoint without radio traffic.
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
    """Return the stored REST connection data of a submitted form."""
    return {
        CONF_TRANSPORT: TRANSPORT_REST,
        CONF_HOST: user_input[CONF_HOST].strip(),
        CONF_PORT: user_input[CONF_PORT],
        CONF_USERNAME: user_input[CONF_USERNAME],
        CONF_PASSWORD: user_input[CONF_PASSWORD],
    }


class OpusGreenNetConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Opus GreenNet Bridge."""

    VERSION = 1

    _discovered_eag_id: str = ""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a gateway by the address of its REST API (phase R4.3).

        This is the only way to add a gateway manually. An existing gateway
        keeps its transport; switching is an explicit reconfigure choice.
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            eag_id = await self._async_validate_rest(user_input, errors)
            if eag_id is not None:
                await self.async_set_unique_id(eag_id)
                self._abort_if_unique_id_configured()
                return self._async_create_rest_entry(eag_id, user_input)
        return self.async_show_form(
            step_id="user",
            data_schema=_connection_schema(user_input or {}),
            errors=errors,
        )

    @callback
    def _async_create_rest_entry(
        self, eag_id: str, user_input: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Create a REST entry for a verified gateway."""
        return self.async_create_entry(
            title=f"Opus GreenNet ({eag_id})",
            data={CONF_EAG_ID: eag_id, **_connection_data(user_input)},
        )

    # ──────────────────────────────────────────────────────────────────────
    # MQTT discovery: an announced gateway is set up over REST
    # ──────────────────────────────────────────────────────────────────────

    async def async_step_mqtt(
        self, discovery_info: MqttServiceInfo
    ) -> ConfigFlowResult:
        """Handle a gateway candidate announced by an OPUS device-model topic.

        The message, retained or live, only identifies a candidate; it is not
        evidence that the gateway is reachable. This step publishes nothing.
        """
        await _async_import_mqtt(self.hass)
        from .discovery import eag_id_from_discovery

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
        """Ask for the REST address of a discovered gateway (phase R4.3).

        The announcement provides only the EAG identifier, so the address is
        entered here; the password suggests the vendor default (the EURID).
        The address is accepted only if it answers as the announced gateway.
        """
        errors: dict[str, str] = {}
        eag_id = self._discovered_eag_id

        if user_input is not None:
            # An entry for this gateway may have appeared while the form was open.
            self._abort_if_unique_id_configured()
            found = await self._async_validate_rest(user_input, errors)
            if found is not None and found != eag_id:
                errors["base"] = "wrong_gateway"
            elif found is not None:
                return self._async_create_rest_entry(eag_id, user_input)

        defaults = {CONF_PASSWORD: eag_id, **(user_input or {})}
        return self.async_show_form(
            step_id="discovery_confirm",
            data_schema=_connection_schema(defaults),
            description_placeholders={"eag_id": eag_id},
            errors=errors,
        )

    # ──────────────────────────────────────────────────────────────────────
    # REST transport
    # ──────────────────────────────────────────────────────────────────────

    async def _async_validate_rest(
        self, user_input: Mapping[str, Any], errors: dict[str, str]
    ) -> str | None:
        """Return the gateway's EAG identifier, or None with ``errors`` set."""
        try:
            return await async_read_gateway_id(self.hass, user_input)
        except OpusAuthError:
            errors["base"] = "invalid_auth"
        except OpusConnectionError:
            errors["base"] = "cannot_connect_gateway"
        except OpusResponseError:
            errors["base"] = "unsupported_gateway"
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception")
            errors["base"] = "unknown"
        return None

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Ask for the address and credentials of a REST gateway."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Verify that the entered address answers as the configured gateway."""
        return await self._async_step_connection(
            "reauth_confirm", self._get_reauth_entry().data, user_input
        )

    # ──────────────────────────────────────────────────────────────────────
    # Reconfiguration (both transports)
    # ──────────────────────────────────────────────────────────────────────

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the transport of a configured gateway."""
        return self.async_show_menu(
            step_id="reconfigure",
            menu_options=[STEP_RECONFIGURE_REST, STEP_RECONFIGURE_MQTT],
        )

    async def async_step_reconfigure_rest(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Use (or change) the REST address and credentials of a gateway."""
        return await self._async_step_connection(
            STEP_RECONFIGURE_REST, self._get_reconfigure_entry().data, user_input
        )

    async def async_step_reconfigure_mqtt(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reach a configured gateway through the MQTT bridge again."""
        entry = self._get_reconfigure_entry()
        eag_id = entry.data[CONF_EAG_ID]
        await _async_import_mqtt(self.hass)
        from homeassistant.components import mqtt

        if not await mqtt.async_wait_for_mqtt_client(self.hass):
            return self.async_abort(reason="mqtt_not_configured")
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await validate_input(self.hass, {CONF_EAG_ID: eag_id})
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except GatewayUnavailable:
                errors["base"] = "gateway_unavailable"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                # MQTT entries store only the EAG identifier: the REST
                # address and credentials are removed with the transport.
                return self.async_update_reload_and_abort(
                    entry, data={CONF_EAG_ID: eag_id}
                )
        return self.async_show_form(
            step_id=STEP_RECONFIGURE_MQTT,
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={"eag_id": eag_id},
        )

    async def _async_step_connection(
        self,
        step_id: str,
        entry_data: Mapping[str, Any],
        user_input: dict[str, Any] | None,
    ) -> ConfigFlowResult:
        """Validate a REST address for an existing entry and store it."""
        eag_id = entry_data[CONF_EAG_ID]
        errors: dict[str, str] = {}
        if user_input is not None:
            found = await self._async_validate_rest(user_input, errors)
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


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidEagId(HomeAssistantError):
    """Error to indicate the EAG ID is invalid."""


class GatewayUnavailable(HomeAssistantError):
    """The broker is connected but the specified gateway did not respond."""
