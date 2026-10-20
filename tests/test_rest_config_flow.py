"""Config flow of the REST transport: user, reauth and reconfigure (phase 14).

Every error a REST form can show is followed by a corrected submission in the
same flow (quality-scale rule config-flow-test-coverage). The MQTT steps keep
their own tests in ``test_config_flow_recovery.py`` and ``test_discovery.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.opus_greennet.const import DOMAIN
from tests.fake_gateway import FakeGateway, device_object
from tests.ha_helpers import (
    configure_bridge,
    configure_rest_bridge,
    setup_rest_bridge,
    start_user_flow,
)


def _rest_data(gateway: FakeGateway, **overrides: Any) -> dict[str, Any]:
    return {"eag_id": gateway.eurid, "transport": "rest", **gateway.config, **overrides}


async def _add_rest_entry(
    hass: HomeAssistant, gateway: FakeGateway, **overrides: Any
) -> ConfigEntry:
    """Add a REST entry as the config flow stores it, then set it up."""
    data = _rest_data(gateway, **overrides)
    entry = ConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title=f"Opus GreenNet ({data['eag_id']})",
        data=data,
        source=config_entries.SOURCE_USER,
        options={},
        unique_id=data["eag_id"],
        discovery_keys={},
        subentries_data=None,
    )
    await hass.config_entries.async_add(entry)
    await hass.async_block_till_done()
    return entry


async def _start_reconfigure(hass: HomeAssistant, entry: ConfigEntry, step: str):
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "reconfigure"
    assert result["menu_options"] == ["reconfigure_rest", "reconfigure_mqtt"]
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": step}
    )


# ── User step ────────────────────────────────────────────────────────────────


async def test_user_step_is_the_rest_form(hass: HomeAssistant) -> None:
    """Phase R4.3: no transport menu; the REST form opens directly."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert [str(key) for key in result["data_schema"].schema] == [
        "host",
        "port",
        "username",
        "password",
    ]
    assert not hass.config_entries.async_entries(DOMAIN)


def test_translations_offer_no_manual_mqtt_step() -> None:
    """The user step is a form in every translation; no MQTT step is left."""
    base = Path(__file__).parent.parent / "custom_components" / "opus_greennet"
    for path in (
        base / "strings.json",
        base / "translations" / "en.json",
        base / "translations" / "de.json",
    ):
        config = json.loads(path.read_text(encoding="utf-8"))["config"]
        user = config["step"]["user"]
        assert "menu_options" not in user, path.name
        assert list(user["data"]) == ["host", "port", "username", "password"]
        assert "mqtt_bridge" not in config["step"], path.name
        assert "rest_api" not in config["step"], path.name
        assert list(config["step"]["discovery_confirm"]["data"]) == list(
            user["data"]
        ), path.name


async def test_user_flow_creates_rest_entry_from_gateway_identity(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.eurid = "aabb0011"
    result = await configure_rest_bridge(hass, gateway, host=f" {gateway.host} ")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Opus GreenNet (AABB0011)"
    assert result["data"] == {
        "eag_id": "AABB0011",
        "transport": "rest",
        "host": gateway.host,
        "port": gateway.port,
        "username": "admin",
        "password": gateway.password,
    }
    assert result["result"].unique_id == "AABB0011"
    await hass.async_block_till_done()
    assert result["result"].state is ConfigEntryState.LOADED
    # Validation reads only the gateway identity: no device request.
    assert [path for _, path, _ in gateway.requests][0] == "/system/info"


async def test_user_flow_form_defaults(hass: HomeAssistant) -> None:
    result = await start_user_flow(hass)
    schema = result["data_schema"].schema
    defaults = {str(key): key.default() for key in schema if callable(key.default)}
    assert defaults == {"port": 8080, "username": "admin"}


async def test_user_flow_recovers_from_wrong_password(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    result = await configure_rest_bridge(hass, gateway, password="wrong")
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], gateway.config
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_recovers_from_unreachable_gateway(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    config = gateway.config
    await gateway.stop()
    result = await start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], config)
    assert result["errors"] == {"base": "cannot_connect_gateway"}
    await gateway.start()
    result = await hass.config_entries.flow.async_configure(result["flow_id"], config)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_recovers_from_device_without_eurid(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.system_info = {"eurid": "not-hex"}
    result = await configure_rest_bridge(hass, gateway)
    assert result["errors"] == {"base": "unsupported_gateway"}
    gateway.system_info = {}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], gateway.config
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_recovers_from_unexpected_error(
    hass: HomeAssistant, gateway: FakeGateway, caplog: pytest.LogCaptureFixture
) -> None:
    with patch(
        "custom_components.opus_greennet.config_flow.async_read_gateway_id",
        side_effect=RuntimeError("boom"),
    ):
        result = await configure_rest_bridge(hass, gateway)
    assert result["errors"] == {"base": "unknown"}
    assert "Unexpected exception" in caplog.text
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], gateway.config
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_adding_known_rest_gateway_is_aborted_unchanged(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    entry = await setup_rest_bridge(hass, gateway)
    result = await configure_rest_bridge(hass, gateway, host="localhost")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data["host"] == gateway.host


async def test_adding_mqtt_gateway_over_rest_keeps_its_transport(
    hass: HomeAssistant, gateway: FakeGateway, mqtt_transport
) -> None:
    """A REST submission never silently moves an MQTT entry (guardrail A5)."""
    assert (await configure_bridge(hass))["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    result = await configure_rest_bridge(hass, gateway)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data == {"eag_id": "AABB0011"}


# ── Reauthentication ─────────────────────────────────────────────────────────


async def test_rejected_credentials_start_reauth_and_recover(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [device_object("DEV1", "D2-01-01")]
    entry = await _add_rest_entry(hass, gateway, password="outdated")
    assert entry.state is ConfigEntryState.SETUP_ERROR

    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]
    flow_id = flows[0]["flow_id"]
    form = await hass.config_entries.flow.async_configure(flow_id)
    assert form["step_id"] == "reauth_confirm"
    assert form["description_placeholders"]["eag_id"] == gateway.eurid

    result = await hass.config_entries.flow.async_configure(
        flow_id, {**gateway.config, "password": "still wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}
    result = await hass.config_entries.flow.async_configure(flow_id, gateway.config)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()
    assert entry.data == _rest_data(gateway)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.coordinator.get_device("DEV1") is not None


async def test_reauth_prefills_the_stored_connection(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    """The stored password is suggested, never shown as a default value."""
    entry = await _add_rest_entry(hass, gateway, password="outdated")
    flow_id = hass.config_entries.flow.async_progress_by_handler(DOMAIN)[0]["flow_id"]
    form = await hass.config_entries.flow.async_configure(flow_id)
    password = next(key for key in form["data_schema"].schema if str(key) == "password")
    assert password.description == {"suggested_value": "outdated"}
    assert entry.data["password"] == "outdated"


async def test_different_gateway_at_stored_address_starts_reauth(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    entry = await _add_rest_entry(hass, gateway, eag_id="11223344")
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flow_id = hass.config_entries.flow.async_progress_by_handler(DOMAIN)[0]["flow_id"]
    result = await hass.config_entries.flow.async_configure(flow_id, gateway.config)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "wrong_gateway"}
    assert entry.data["host"] == gateway.host


# ── Reconfiguration ──────────────────────────────────────────────────────────


async def test_reconfigure_changes_the_rest_connection(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    entry = await setup_rest_bridge(hass, gateway)
    result = await _start_reconfigure(hass, entry, "reconfigure_rest")
    assert result["step_id"] == "reconfigure_rest"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**gateway.config, "host": "localhost"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.data["host"] == "localhost"
    assert entry.data["transport"] == "rest"
    assert entry.state is ConfigEntryState.LOADED


async def test_reconfigure_rest_recovers_from_a_different_gateway(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    entry = await setup_rest_bridge(hass, gateway)
    gateway.eurid = "11223344"
    result = await _start_reconfigure(hass, entry, "reconfigure_rest")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], gateway.config
    )
    assert result["errors"] == {"base": "wrong_gateway"}
    gateway.eurid = "AABB0011"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], gateway.config
    )
    assert result["reason"] == "reconfigure_successful"


async def test_reconfigure_mqtt_aborts_without_mqtt(
    hass: HomeAssistant, gateway: FakeGateway, mqtt_transport
) -> None:
    entry = await setup_rest_bridge(hass, gateway)
    with patch(
        "homeassistant.components.mqtt.async_wait_for_mqtt_client",
        new=AsyncMock(return_value=False),
    ):
        result = await _start_reconfigure(hass, entry, "reconfigure_mqtt")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "mqtt_not_configured"
    assert entry.data["transport"] == "rest"


async def test_reconfigure_mqtt_recovers_from_every_error(
    hass: HomeAssistant, gateway: FakeGateway, mqtt_transport
) -> None:
    entry = await setup_rest_bridge(hass, gateway)
    result = await _start_reconfigure(hass, entry, "reconfigure_mqtt")
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure_mqtt"
    assert result["description_placeholders"] == {"eag_id": "AABB0011"}
    flow_id = result["flow_id"]

    mqtt_transport.connected = False
    result = await hass.config_entries.flow.async_configure(flow_id, {})
    assert result["errors"] == {"base": "cannot_connect"}
    mqtt_transport.connected = True

    mqtt_transport.reply = False
    with patch(
        "custom_components.opus_greennet.mqtt_transport.SETUP_REQUEST_TIMEOUT", 0.01
    ):
        result = await hass.config_entries.flow.async_configure(flow_id, {})
    assert result["errors"] == {"base": "gateway_unavailable"}
    mqtt_transport.reply = True

    with patch(
        "custom_components.opus_greennet.config_flow.async_probe_gateway",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        result = await hass.config_entries.flow.async_configure(flow_id, {})
    assert result["errors"] == {"base": "unknown"}
    assert entry.data["transport"] == "rest"

    result = await hass.config_entries.flow.async_configure(flow_id, {})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    # The REST address and credentials are removed with the transport.
    assert entry.data == {"eag_id": "AABB0011"}
