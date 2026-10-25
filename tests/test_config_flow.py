"""Tests for the Opus GreenNet config flow (user, reauth and reconfigure)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData

from custom_components.opus_greennet.config_flow import _is_host
from custom_components.opus_greennet.const import DOMAIN
from tests.fake_gateway import FakeGateway, device_object
from tests.ha_helpers import configure_bridge, setup_bridge


async def _entry_with_legacy_mqtt_data(hass: HomeAssistant, eag_id: str):
    """Add an entry as the former MQTT transport created it."""
    entry = config_entries.ConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title=f"Opus GreenNet ({eag_id})",
        data={"eag_id": eag_id},
        source=config_entries.SOURCE_USER,
        options={},
        unique_id=eag_id,
        discovery_keys={},
        subentries_data=None,
    )
    await hass.config_entries.async_add(entry)
    await hass.async_block_till_done()
    return entry


async def test_user_flow_creates_entry_from_gateway_identity(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.eurid = "aabb0011"
    result = await configure_bridge(hass, gateway, host=f" {gateway.host} ")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Opus GreenNet (AABB0011)"
    assert result["data"] == {
        "eag_id": "AABB0011",
        "host": gateway.host,
        "port": gateway.port,
        "username": "admin",
        "password": gateway.password,
    }
    assert result["result"].unique_id == "AABB0011"
    await hass.async_block_till_done()
    assert result["result"].state is ConfigEntryState.LOADED


async def test_user_flow_form_defaults(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    schema = result["data_schema"].schema
    defaults = {str(key): key.default() for key in schema if callable(key.default)}
    assert defaults == {"port": 8080, "username": "admin"}


async def test_user_flow_wrong_password(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    result = await configure_bridge(hass, gateway, password="wrong")
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_unreachable_gateway(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    config = gateway.config
    await gateway.stop()
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], config)
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_device_without_eurid(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.system_info = {"eurid": "not-hex"}
    result = await configure_bridge(hass, gateway)
    assert result["errors"] == {"base": "unsupported_gateway"}


async def test_user_flow_unexpected_error(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    with patch(
        "custom_components.opus_greennet.config_flow.async_read_gateway_id",
        side_effect=RuntimeError("boom"),
    ):
        result = await configure_bridge(hass, gateway)
    assert result["errors"] == {"base": "unknown"}


async def test_adding_known_gateway_updates_its_connection(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    entry = await setup_bridge(hass, gateway)
    result = await configure_bridge(hass, gateway, host="localhost")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data["host"] == "localhost"


async def test_legacy_mqtt_entry_asks_for_the_gateway_address(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    """Entries of the former MQTT transport keep all devices after reauth."""
    gateway.devices = [device_object("DEV1", "D2-01-01")]
    entry = await _entry_with_legacy_mqtt_data(hass, gateway.eurid)
    assert entry.state is ConfigEntryState.SETUP_ERROR

    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]
    flow_id = flows[0]["flow_id"]
    form = await hass.config_entries.flow.async_configure(flow_id)
    assert form["step_id"] == "reauth_confirm"
    assert form["description_placeholders"]["eag_id"] == gateway.eurid
    password = next(key for key in form["data_schema"].schema if str(key) == "password")
    # The vendor default password (the EURID) is suggested.
    assert password.description == {"suggested_value": gateway.eurid}

    result = await hass.config_entries.flow.async_configure(flow_id, gateway.config)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()
    assert entry.data == {"eag_id": gateway.eurid, **gateway.config}
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.coordinator.get_device("DEV1") is not None


async def test_reauth_rejects_a_different_gateway(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    entry = await _entry_with_legacy_mqtt_data(hass, "11223344")
    flow_id = hass.config_entries.flow.async_progress_by_handler(DOMAIN)[0]["flow_id"]
    result = await hass.config_entries.flow.async_configure(flow_id, gateway.config)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "wrong_gateway"}
    assert "host" not in entry.data


async def test_reauth_reports_connection_errors(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    await _entry_with_legacy_mqtt_data(hass, gateway.eurid)
    flow_id = hass.config_entries.flow.async_progress_by_handler(DOMAIN)[0]["flow_id"]
    result = await hass.config_entries.flow.async_configure(
        flow_id, {**gateway.config, "password": "wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}


async def test_reconfigure_changes_the_connection(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    entry = await setup_bridge(hass, gateway)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**gateway.config, "host": "localhost"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.data["host"] == "localhost"
    assert entry.state is ConfigEntryState.LOADED


async def test_reconfigure_rejects_a_different_gateway(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    entry = await setup_bridge(hass, gateway)
    gateway.eurid = "11223344"
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], gateway.config
    )
    assert result["errors"] == {"base": "wrong_gateway"}


@pytest.mark.parametrize(
    "host",
    [
        "http://192.168.1.10",
        "192.168.1.10:8080",
        "192.168.1.10/api",
        "admin:pw@192.168.1.10",
        "bad host",
        "-gateway.local",
        "a" * 64 + ".local",
        "[not-an-ip]",
        "",
        "   ",
    ],
)
async def test_user_flow_rejects_a_url_instead_of_a_host(
    hass: HomeAssistant, gateway: FakeGateway, host: str
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**gateway.config, "host": host}
    )
    assert result["errors"] == {"host": "invalid_host"}
    assert gateway.requests == []


@pytest.mark.parametrize("host", ["::1", "[::1]", "localhost"])
async def test_user_flow_accepts_host_names_and_ipv6(
    hass: HomeAssistant, gateway: FakeGateway, host: str
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**gateway.config, "host": host, "port": 1}
    )
    # Valid hosts reach the connection attempt (nothing listens on port 1).
    assert result["errors"] == {"base": "cannot_connect"}


@pytest.mark.parametrize(
    "host", ["192.168.1.10", "opusshgw.local", "opusshgw.local.", "gw-1.example.org"]
)
def test_host_names_are_accepted(host: str) -> None:
    assert _is_host(host)


INVALID_HOSTS = ["http://192.168.1.10", "", "   "]


@pytest.mark.parametrize("host", INVALID_HOSTS)
async def test_reauth_rejects_an_invalid_host_and_recovers(
    hass: HomeAssistant, gateway: FakeGateway, host: str
) -> None:
    entry = await _entry_with_legacy_mqtt_data(hass, gateway.eurid)
    flow_id = hass.config_entries.flow.async_progress_by_handler(DOMAIN)[0]["flow_id"]
    with patch(
        "custom_components.opus_greennet.config_flow.async_read_gateway_id"
    ) as read_gateway_id:
        result = await hass.config_entries.flow.async_configure(
            flow_id, {**gateway.config, "host": host}
        )
    # The host is rejected before any connection attempt.
    read_gateway_id.assert_not_called()
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"host": "invalid_host"}
    assert "host" not in entry.data

    result = await hass.config_entries.flow.async_configure(flow_id, gateway.config)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()
    assert entry.data["host"] == gateway.config["host"]


@pytest.mark.parametrize("host", INVALID_HOSTS)
async def test_reconfigure_rejects_an_invalid_host_and_recovers(
    hass: HomeAssistant, gateway: FakeGateway, host: str
) -> None:
    entry = await setup_bridge(hass, gateway)
    original_host = entry.data["host"]
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    with patch(
        "custom_components.opus_greennet.config_flow.async_read_gateway_id"
    ) as read_gateway_id:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**gateway.config, "host": host}
        )
    # The host is rejected before any connection attempt.
    read_gateway_id.assert_not_called()
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"host": "invalid_host"}
    assert entry.data["host"] == original_host

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**gateway.config, "host": "localhost"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.data["host"] == "localhost"


@pytest.mark.parametrize("port", [0, 65536, -1, True, False, 80.5, "abc", "", None])
async def test_user_flow_rejects_an_invalid_port_and_recovers(
    hass: HomeAssistant, gateway: FakeGateway, port: object
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    # The form schema rejects the port before the flow step runs.
    with pytest.raises(InvalidData) as error:
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {**gateway.config, "port": port}
        )
    assert set(error.value.schema_errors) == {"port"}
    assert gateway.requests == []

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], gateway.config
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["port"] == gateway.port
    await hass.async_block_till_done()
