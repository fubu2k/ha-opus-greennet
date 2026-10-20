"""Transport selection and REST-specific behaviour (phase 14).

* Entries without ``transport`` keep the unchanged MQTT coordinator.
* REST entries use the REST coordinator; switching an entry between the
  transports keeps devices, entities, unique IDs and user settings (A5).
* ACK 201 keeps the phase 11 semantics on REST: no estimate, no travel
  estimate, no status query; the later outbound telegram is a delivery.
* The dimmer ramping mode is sent by the REST transport only.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.components.light import LightEntityFeature
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.redact import REDACTED

from custom_components.opus_greennet import async_setup_entry
from custom_components.opus_greennet.api import (
    OpusAuthError,
    OpusConnectionError,
)
from custom_components.opus_greennet.const import DOMAIN
from custom_components.opus_greennet.coordinator import (
    CommandDelivery,
    OpusGreenNetCoordinator,
)
from custom_components.opus_greennet.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from custom_components.opus_greennet.light import OpusGreenNetLight
from custom_components.opus_greennet.protocol import (
    command_values_by_channel,
    wire_value_matches,
)
from custom_components.opus_greennet.rest_coordinator import (
    GatewayMismatchError,
    OpusGreenNetRestCoordinator,
)
from custom_components.opus_greennet.transport import entry_transport
from tests.fake_gateway import FakeGateway, device_object, telegram
from tests.ha_helpers import (
    configure_bridge,
    setup_rest_bridge,
    wait_for_entity,
    wait_until,
)

REST = "custom_components.opus_greennet.rest_coordinator"
SWITCH = "AABB1122"


def _switch(state: str = "off") -> dict[str, Any]:
    return device_object(
        SWITCH,
        "D2-01-00",
        friendly_id="Desk switch",
        states=[{"key": "switch", "value": state}],
    )


async def _reconfigure(hass: HomeAssistant, entry: ConfigEntry, step: str):
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": step}
    )


# ── Selection ────────────────────────────────────────────────────────────────


def test_entries_without_transport_stay_on_mqtt() -> None:
    assert entry_transport({"eag_id": "AABB0011"}) == "mqtt"
    assert entry_transport({"eag_id": "AABB0011", "transport": "mqtt"}) == "mqtt"
    assert entry_transport({"eag_id": "AABB0011", "transport": "other"}) == "mqtt"
    assert entry_transport({"eag_id": "AABB0011", "transport": "rest"}) == "rest"


async def test_mqtt_entry_never_talks_to_the_rest_api(
    hass: HomeAssistant, gateway: FakeGateway, mqtt_transport
) -> None:
    mqtt_transport.devices = [_switch()]
    result = await configure_bridge(hass)
    await hass.async_block_till_done()
    entry = result["result"]
    assert entry.data == {"eag_id": "AABB0011"}
    assert type(entry.runtime_data.coordinator) is OpusGreenNetCoordinator
    await wait_for_entity(hass, "switch", f"AABB0011_{SWITCH}")
    assert gateway.requests == []
    gateway_device = dr.async_get(hass).async_get(entry.runtime_data.gateway_device_id)
    assert gateway_device.sw_version is None
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["gateway"]["transport"] == "mqtt"


async def test_rest_entry_uses_the_rest_coordinator_without_mqtt(
    hass: HomeAssistant, gateway: FakeGateway, mqtt_transport
) -> None:
    gateway.devices = [_switch("on")]
    entry = await setup_rest_bridge(hass, gateway)
    assert type(entry.runtime_data.coordinator) is OpusGreenNetRestCoordinator
    entity_id = await wait_for_entity(hass, "switch", f"AABB0011_{SWITCH}")
    assert hass.states.get(entity_id).state == "on"
    assert mqtt_transport.published == []
    assert mqtt_transport.subscriptions == []
    gateway_device = dr.async_get(hass).async_get(entry.runtime_data.gateway_device_id)
    assert gateway_device.sw_version == "OPUS-GW test"
    assert gateway_device.identifiers == {(DOMAIN, "AABB0011")}


async def test_switching_an_mqtt_entry_to_rest_and_back_keeps_entities(
    hass: HomeAssistant, gateway: FakeGateway, mqtt_transport
) -> None:
    """Identities, entity IDs and user settings survive both switches (A5)."""
    mqtt_transport.devices = [_switch()]
    result = await configure_bridge(hass)
    await hass.async_block_till_done()
    entry = result["result"]
    entity_id = await wait_for_entity(hass, "switch", f"AABB0011_{SWITCH}")
    registry = er.async_get(hass)
    registry.async_update_entity(entity_id, name="My desk")
    before = registry.async_get(entity_id)
    devices_before = {
        device.id
        for device in dr.async_entries_for_config_entry(
            dr.async_get(hass), entry.entry_id
        )
    }

    gateway.devices = [_switch("on")]
    result = await _reconfigure(hass, entry, "reconfigure_rest")
    assert result["step_id"] == "reconfigure_rest"
    schema = result["data_schema"].schema
    password = next(key for key in schema if str(key) == "password")
    # An MQTT entry has no password yet: the vendor default (EURID) is offered.
    assert password.description == {"suggested_value": "AABB0011"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], gateway.config
    )
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    await gateway.wait_for_stream()
    await hass.async_block_till_done()

    assert entry.data == {"eag_id": "AABB0011", "transport": "rest", **gateway.config}
    assert type(entry.runtime_data.coordinator) is OpusGreenNetRestCoordinator
    assert mqtt_transport.subscriptions == []
    after = registry.async_get(entity_id)
    assert (after.unique_id, after.name, after.disabled_by) == (
        before.unique_id,
        "My desk",
        None,
    )
    assert registry.async_get_entity_id("switch", DOMAIN, f"AABB0011_{SWITCH}") == (
        entity_id
    )
    await wait_until(lambda: hass.states.get(entity_id).state == "on")

    result = await _reconfigure(hass, entry, "reconfigure_mqtt")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.data == {"eag_id": "AABB0011"}
    assert type(entry.runtime_data.coordinator) is OpusGreenNetCoordinator
    # The unloaded REST coordinator sends nothing more.
    requests = len(gateway.requests)
    await hass.async_block_till_done()
    assert len(gateway.requests) == requests
    assert registry.async_get(entity_id).name == "My desk"
    assert {
        device.id
        for device in dr.async_entries_for_config_entry(
            dr.async_get(hass), entry.entry_id
        )
    } == devices_before


# ── Setup errors of REST entries ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (OpusAuthError("status 401"), ConfigEntryAuthFailed),
        (GatewayMismatchError("other gateway"), ConfigEntryAuthFailed),
        (OpusConnectionError("refused"), ConfigEntryNotReady),
        (ValueError("Bug"), ValueError),
    ],
)
async def test_failed_rest_setup_cleans_up(
    failure: Exception, expected: type[Exception]
) -> None:
    coordinator = MagicMock()
    coordinator.async_setup = AsyncMock(side_effect=failure)
    coordinator.async_unload = AsyncMock()
    entry = SimpleNamespace(
        data={
            "eag_id": "AABB0011",
            "transport": "rest",
            "host": "192.0.2.1",
            "password": "AABB0011",
        },
        async_start_reauth=MagicMock(),
    )
    with (
        patch(
            "custom_components.opus_greennet.OpusGreenNetRestCoordinator",
            return_value=coordinator,
        ) as factory,
        patch("custom_components.opus_greennet.async_get_clientsession"),
        pytest.raises(expected),
    ):
        await async_setup_entry(MagicMock(), entry)
    coordinator.async_unload.assert_awaited_once()
    client = factory.call_args.args[1]
    assert (client.host, client.port) == ("192.0.2.1", 8080)
    # A runtime credential rejection starts the reauth flow of this entry.
    factory.call_args.kwargs["on_auth_failed"]()
    entry.async_start_reauth.assert_called_once()


async def test_unreachable_rest_gateway_retries_setup(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    data = {"eag_id": gateway.eurid, "transport": "rest", **gateway.config}
    await gateway.stop()
    entry = ConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title="Opus GreenNet (AABB0011)",
        data=data,
        source=config_entries.SOURCE_USER,
        options={},
        unique_id=gateway.eurid,
        discovery_keys={},
        subentries_data=None,
    )
    await hass.config_entries.async_add(entry)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []


# ── ACK 201 on REST (phase 11 semantics) ─────────────────────────────────────


async def test_rest_deferred_ack_assumes_nothing_until_delivery(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [_switch()]
    entry = await setup_rest_bridge(hass, gateway)
    coordinator = entry.runtime_data.coordinator
    device = coordinator.devices[SWITCH]
    entity_id = await wait_for_entity(hass, "switch", f"AABB0011_{SWITCH}")
    path = f"/devices/{SWITCH}/state"
    gateway.responses[path] = {"header": {"httpStatus": 201}}

    with patch(f"{REST}.async_call_later") as later:
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": entity_id}, blocking=True
        )
        assert hass.states.get(entity_id).state == "off"
        assert device.deferred_commands == {0: {"switch": "on"}}
        later.assert_not_called()
        assert coordinator._sent_commands == {}

        # A device list is not a delivery report: the marker survives.
        await coordinator._async_refresh_device_list()
        assert coordinator.devices[SWITCH].deferred_commands == {0: {"switch": "on"}}

        # The gateway transmits later. Its outbound telegram is a delivery,
        # not the echo of a transmitted command: state and checks follow.
        await gateway.push(
            telegram(SWITCH, [{"key": "switch", "value": "on"}], direction="to")
        )
        await hass.async_block_till_done()
    assert coordinator.devices[SWITCH].deferred_commands == {}
    assert hass.states.get(entity_id).state == "on"
    assert later.call_count == 2


async def test_rest_report_or_transmitted_command_settles_deferred_markers(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [_switch("on")]
    entry = await setup_rest_bridge(hass, gateway)
    coordinator = entry.runtime_data.coordinator
    path = f"/devices/{SWITCH}/state"

    gateway.responses[path] = {"header": {"httpStatus": 201}}
    assert await coordinator.async_turn_off(SWITCH) is CommandDelivery.DEFERRED
    device = coordinator.devices[SWITCH]
    assert device.deferred_commands == {0: {"switch": "off"}}
    # A report with another value leaves the marker in place.
    await gateway.push(telegram(SWITCH, [{"key": "switch", "value": "on"}]))
    assert device.deferred_commands == {0: {"switch": "off"}}
    await gateway.push(telegram(SWITCH, [{"key": "switch", "value": "off"}]))
    assert device.deferred_commands == {}

    assert await coordinator.async_turn_on(SWITCH) is CommandDelivery.DEFERRED
    assert device.deferred_commands == {0: {"switch": "on"}}
    del gateway.responses[path]
    # A transmitted command supersedes the older deferred value.
    assert await coordinator.async_turn_off(SWITCH) is CommandDelivery.SENT
    assert device.deferred_commands == {}


async def test_rest_deferred_cover_command_starts_no_travel_estimate(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [
        device_object(
            "COVER1",
            "D2-05-00",
            states=[{"key": "position", "value": 0}],
            configuration={
                "parameters": [{"key": "verticalMovementTime", "value": 30}]
            },
        )
    ]
    entry = await setup_rest_bridge(hass, gateway)
    coordinator = entry.runtime_data.coordinator
    gateway.responses["/devices/COVER1/state"] = {"header": {"httpStatus": 201}}
    with patch(f"{REST}.async_call_later") as later:
        delivery = await coordinator.async_set_cover_position("COVER1", 100)
    assert delivery is CommandDelivery.DEFERRED
    assert coordinator._cover_movements == {}
    assert coordinator.devices["COVER1"].channels[0].movement is None
    assert coordinator.devices["COVER1"].deferred_commands == {0: {"position": "100"}}
    # The travel timer started before the acknowledgement was cancelled.
    later.return_value.assert_called_once()
    assert ("COVER1", 0) not in coordinator._pending_reconciliation_queries


async def test_rest_climate_status_query_is_an_explicit_request(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [device_object("HEAT1", "D1-4B-05")]
    entry = await setup_rest_bridge(hass, gateway)
    coordinator = entry.runtime_data.coordinator
    assert await coordinator.async_query_climate_status("HEAT1") is (
        CommandDelivery.SENT
    )
    assert gateway.requests_to("PUT", "/devices/HEAT1/state") == [
        {"state": {"functions": [{"key": "query", "value": "status"}]}}
    ]


def test_deferred_value_helpers() -> None:
    functions = [
        {"key": "channel", "value": "1"},
        {"key": "dimValue", "value": "40"},
        {"key": "query", "value": "status"},
        {"key": "rampingMode", "value": "switch"},
        {"key": "stop", "value": "true"},
        {"key": "position", "value": None},
        {"key": "angle", "value": "5", "channel": "x"},
    ]
    assert command_values_by_channel(functions) == {
        1: {"dimValue": "40", "stop": "true"}
    }
    assert wire_value_matches("dimValue", "40", 40.0)
    assert not wire_value_matches("dimValue", "40", "invalid")
    assert wire_value_matches("stop", "true", True)
    assert not wire_value_matches("switch", "on", 1)


# ── Dimmer ramping mode (REST only) ──────────────────────────────────────────


async def test_rest_dimmer_switches_at_once_and_fades_on_transition(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [
        device_object("DIM1", "D2-01-02", states=[{"key": "dimValue", "value": 0}]),
        device_object("GWD1", "A5-38-08", states=[{"key": "dimValue", "value": 0}]),
    ]
    await setup_rest_bridge(hass, gateway)
    dimmer = await wait_for_entity(hass, "light", "AABB0011_DIM1")
    gateway_dimmer = await wait_for_entity(hass, "light", "AABB0011_GWD1")
    assert hass.states.get(dimmer).attributes["supported_features"] == (
        LightEntityFeature.TRANSITION
    )
    assert not hass.states.get(gateway_dimmer).attributes.get("supported_features")

    calls = [
        ("turn_on", {"brightness": 255}),
        ("turn_on", {"transition": 2}),
        ("turn_off", {"transition": 2}),
        ("turn_off", {}),
    ]
    for service, data in calls:
        await hass.services.async_call(
            "light", service, {"entity_id": dimmer, **data}, blocking=True
        )
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": gateway_dimmer, "brightness": 128},
        blocking=True,
    )

    def put(value: str, mode: str) -> dict[str, Any]:
        return {
            "state": {
                "functions": [
                    {"key": "dimValue", "value": value},
                    {"key": "rampingMode", "value": mode},
                ]
            }
        }

    assert gateway.requests_to("PUT", "/devices/DIM1/state") == [
        put("100", "switch"),
        put("100", "rampingTime1"),
        put("0", "rampingTime1"),
        put("0", "switch"),
    ]
    # A5-38-08 gateway dimming has no ramping mode.
    assert gateway.requests_to("PUT", "/devices/GWD1/state") == [
        {"state": {"functions": [{"key": "dimValue", "value": "50"}]}}
    ]


def test_mqtt_dimmer_keeps_its_features_and_wire_values() -> None:
    coordinator = MagicMock(spec=OpusGreenNetCoordinator)
    device = EnOceanDevice("DIM1", "Dimmer", [{"eep": "D2-01-02"}])
    entity = OpusGreenNetLight(coordinator, "AABB0011", "gateway", device)
    assert not entity.supported_features & LightEntityFeature.TRANSITION
    assert device.supports_ramping_mode is True


# ── Diagnostics ──────────────────────────────────────────────────────────────


async def test_rest_diagnostics_redact_the_connection(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [_switch()]
    entry = await setup_rest_bridge(hass, gateway)
    result = await async_get_config_entry_diagnostics(hass, entry)
    data = result["entry"]["data"]
    assert (data["host"], data["username"], data["password"]) == (
        REDACTED,
        REDACTED,
        REDACTED,
    )
    assert data["port"] == gateway.port
    assert data["transport"] == "rest"
    assert result["gateway"]["transport"] == "rest"
    assert result["gateway"]["info"]["homekitPin"] == REDACTED
    clean = {"unanswered_requests": 0, "possibly_late_answers": 0}
    assert result["gateway"]["late_answers"] == clean
    assert result["devices"][0]["late_answers"] == clean
    assert result["devices"][0]["deferred_commands"] == {}
    assert "111-22-333" not in str(result)
