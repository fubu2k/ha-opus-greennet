"""Config flow error recovery (quality-scale rule config-flow-test-coverage).

Since phase R4.3 the user step is the REST form. Every error it can show is
followed by a corrected submission in the same flow, which must create the
entry with the gateway's EURID as unique ID. MQTT is never offered here.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.opus_greennet.const import DOMAIN
from tests.fake_gateway import FakeGateway
from tests.ha_helpers import start_user_flow


async def _start(hass: HomeAssistant) -> str:
    result = await start_user_flow(hass)
    assert result["errors"] == {}
    return result["flow_id"]


def _assert_created(hass: HomeAssistant, result) -> None:
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Opus GreenNet (AABB0011)"
    assert result["data"]["eag_id"] == "AABB0011"
    assert result["data"]["transport"] == "rest"
    assert result["result"].unique_id == "AABB0011"
    assert [entry.unique_id for entry in hass.config_entries.async_entries(DOMAIN)] == [
        "AABB0011"
    ]


async def test_happy_path_sets_the_gateway_eurid_as_unique_id(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.eurid = "aabb0011"
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, gateway.config)
    _assert_created(hass, result)
    await hass.async_block_till_done()


@pytest.mark.parametrize(
    ("break_gateway", "error"),
    [
        (lambda gw: setattr(gw, "accept_auth", False), "invalid_auth"),
        (lambda gw: gw.status.__setitem__("/system/info", 500), "unsupported_gateway"),
        (
            lambda gw: setattr(gw, "system_info", {"eurid": "not-hex"}),
            "unsupported_gateway",
        ),
    ],
    ids=["rejected_credentials", "http_error", "no_eurid"],
)
async def test_recovers_from_gateway_errors(
    hass: HomeAssistant, gateway: FakeGateway, break_gateway, error: str
) -> None:
    flow_id = await _start(hass)
    break_gateway(gateway)
    result = await hass.config_entries.flow.async_configure(flow_id, gateway.config)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}
    assert hass.config_entries.async_entries(DOMAIN) == []
    # Restore a healthy gateway and submit again in the same flow.
    gateway.accept_auth = True
    gateway.status.clear()
    gateway.system_info = {}
    result = await hass.config_entries.flow.async_configure(flow_id, gateway.config)
    _assert_created(hass, result)
    await hass.async_block_till_done()


async def test_recovers_from_unreachable_gateway(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    flow_id = await _start(hass)
    config = gateway.config
    await gateway.stop()
    result = await hass.config_entries.flow.async_configure(flow_id, config)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect_gateway"}
    await gateway.start()
    result = await hass.config_entries.flow.async_configure(flow_id, config)
    _assert_created(hass, result)
    await hass.async_block_till_done()


async def test_recovers_from_unexpected_exception(
    hass: HomeAssistant, gateway: FakeGateway, caplog: pytest.LogCaptureFixture
) -> None:
    flow_id = await _start(hass)
    with patch(
        "custom_components.opus_greennet.config_flow.async_read_gateway_id",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        result = await hass.config_entries.flow.async_configure(flow_id, gateway.config)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}
    assert "Unexpected exception" in caplog.text
    result = await hass.config_entries.flow.async_configure(flow_id, gateway.config)
    _assert_created(hass, result)
    await hass.async_block_till_done()


async def test_second_entry_for_same_gateway_is_aborted(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    flow_id = await _start(hass)
    _assert_created(
        hass, await hass.config_entries.flow.async_configure(flow_id, gateway.config)
    )
    await hass.async_block_till_done()
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, gateway.config)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_user_step_needs_no_mqtt_integration(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    """Without MQTT the manual flow still works; it never waits for MQTT."""
    with patch(
        "homeassistant.components.mqtt.async_wait_for_mqtt_client",
        new=AsyncMock(return_value=False),
    ) as wait:
        flow_id = await _start(hass)
        result = await hass.config_entries.flow.async_configure(flow_id, gateway.config)
    _assert_created(hass, result)
    wait.assert_not_called()
    await hass.async_block_till_done()
