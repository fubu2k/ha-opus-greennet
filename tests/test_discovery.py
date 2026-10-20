"""MQTT discovery of OPUS gateways (quality-scale rule ``discovery``).

A device-model publication only identifies a candidate gateway. These tests
assert that discovery shares the unique ID with manual setup, never publishes,
and creates exactly one entry per gateway, including duplicate and racing
announcements. Since phase R4.3 a discovered gateway is set up over its REST
API: the confirm step asks for the address (the password suggests the EURID)
and accepts it only if it answers as the announced gateway.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries, loader
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.discovery_flow import DiscoveryKey
from homeassistant.helpers.service_info.mqtt import MqttServiceInfo
from paho.mqtt.client import topic_matches_sub

from custom_components.opus_greennet.const import DOMAIN
from custom_components.opus_greennet.discovery import (
    DISCOVERY_TOPIC_FILTER,
    eag_id_from_discovery,
    normalize_eag_id,
)
from tests.fake_gateway import FakeGateway
from tests.ha_helpers import configure_bridge, configure_rest_bridge

MANIFEST = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "custom_components/opus_greennet/manifest.json"
    ).read_text(encoding="utf-8")
)
TOPIC = "EnOcean/AABB0011/stream/devices/DEV1/friendlyId"


def _info(
    topic: str = TOPIC, payload: str | bytes = "Living room", *, retain: bool = True
) -> MqttServiceInfo:
    return MqttServiceInfo(
        topic=topic,
        payload=payload,
        qos=0,
        retain=retain,
        subscribed_topic=DISCOVERY_TOPIC_FILTER,
        timestamp=0.0,
    )


async def _discover(hass: HomeAssistant, info: MqttServiceInfo | None = None):
    info = info or _info()
    return await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_MQTT,
            "discovery_key": DiscoveryKey(domain="mqtt", key=info.topic, version=1),
        },
        data=info,
    )


def _flows(hass: HomeAssistant) -> list:
    return hass.config_entries.flow.async_progress_by_handler(DOMAIN)


def _entry(
    unique_id: str,
    *,
    source: str = config_entries.SOURCE_USER,
    disabled: bool = False,
) -> config_entries.ConfigEntry:
    """A registry-only entry that Home Assistant does not set up."""
    return config_entries.ConfigEntry(
        data={} if source == config_entries.SOURCE_IGNORE else {"eag_id": unique_id},
        discovery_keys=MappingProxyType({}),
        disabled_by=config_entries.ConfigEntryDisabler.USER if disabled else None,
        domain=DOMAIN,
        minor_version=1,
        options={},
        source=source,
        subentries_data=None,
        title=f"Opus GreenNet ({unique_id})",
        unique_id=unique_id,
        version=1,
    )


# ── Candidate validation (no Home Assistant flow needed) ─────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("AABB0011", "AABB0011"),
        (" aabb0011 ", "AABB0011"),
        ("AABB001", None),
        ("AABB00111", None),
        ("AABB00G1", None),
        ("", None),
    ],
)
def test_normalize_eag_id(value: str, expected: str | None) -> None:
    assert normalize_eag_id(value) == expected


@pytest.mark.parametrize(
    ("topic", "payload", "expected"),
    [
        (TOPIC, "Living room", "AABB0011"),
        (TOPIC, b"123", "AABB0011"),
        (TOPIC, "", None),
        (TOPIC, b"", None),
        # Topics are case sensitive; the entry subscribes in upper case.
        ("EnOcean/aabb0011/stream/devices/DEV1/friendlyId", "x", None),
        ("EnOcean/AABB001/stream/devices/DEV1/friendlyId", "x", None),
        ("EnOcean/AABB00G1/stream/devices/DEV1/friendlyId", "x", None),
        ("EnOcean/AABB0011/stream/device/DEV1/friendlyId", "x", None),
        ("EnOcean/AABB0011/stream/devices/DEV1/states/switch", "on", None),
        ("EnOcean/AABB0011/stream/devices/DEV1/x/friendlyId", "x", None),
        ("Other/AABB0011/stream/devices/DEV1/friendlyId", "x", None),
    ],
)
def test_candidate_validation(topic: str, payload, expected: str | None) -> None:
    assert eag_id_from_discovery(topic, payload) == expected


def test_manifest_filter_is_the_validated_filter() -> None:
    assert MANIFEST["mqtt"] == [DISCOVERY_TOPIC_FILTER]
    # Only the narrow device-model leaf matches, never the retained snapshot's
    # state topics or the live delta stream.
    assert topic_matches_sub(DISCOVERY_TOPIC_FILTER, TOPIC)
    for topic in (
        "EnOcean/AABB0011/stream/devices/DEV1/states/switch",
        "EnOcean/AABB0011/stream/device/DEV1/friendlyId",
        "EnOcean/AABB0011/stream/telegram/DEV1/from",
        "EnOcean/AABB0011/getAnswer/config/system/uptime",
    ):
        assert not topic_matches_sub(DISCOVERY_TOPIC_FILTER, topic), topic


async def test_home_assistant_loads_the_manifest_filter(hass: HomeAssistant) -> None:
    mqtt_topics = await loader.async_get_mqtt(hass)
    assert mqtt_topics[DOMAIN] == [DISCOVERY_TOPIC_FILTER]


# ── Config flow ──────────────────────────────────────────────────────────────


async def _confirm(hass: HomeAssistant, flow_id: str, gateway: FakeGateway, **kw):
    """Submit the REST address of the discovered gateway."""
    return await hass.config_entries.flow.async_configure(
        flow_id, {**gateway.config, **kw}
    )


async def test_discovery_asks_for_the_rest_address_and_creates_one_entry(
    hass: HomeAssistant, mqtt_transport, gateway: FakeGateway
) -> None:
    result = await _discover(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "discovery_confirm"
    assert result["description_placeholders"] == {"eag_id": "AABB0011"}
    assert result["errors"] == {}
    schema = result["data_schema"].schema
    assert [str(key) for key in schema] == ["host", "port", "username", "password"]
    defaults = {str(key): key.default() for key in schema if callable(key.default)}
    assert defaults == {"port": 8080, "username": "admin"}
    suggested = {
        str(key): (key.description or {}).get("suggested_value") for key in schema
    }
    # The vendor default password is the announced EURID.
    assert suggested["password"] == "AABB0011"
    (flow,) = _flows(hass)
    assert flow["context"]["unique_id"] == "AABB0011"
    assert flow["context"]["title_placeholders"] == {"eag_id": "AABB0011"}
    # A retained announcement is a candidate only: nothing is published.
    assert mqtt_transport.published == []

    result = await _confirm(hass, result["flow_id"], gateway)
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
    entry = result["result"]
    assert entry.unique_id == "AABB0011"
    assert entry.source == config_entries.SOURCE_MQTT
    assert entry.discovery_keys == {
        "mqtt": (DiscoveryKey(domain="mqtt", key=TOPIC, version=1),)
    }
    # Validation reads only the gateway identity; nothing goes over MQTT.
    assert [path for _, path, _ in gateway.requests][0] == "/system/info"
    assert mqtt_transport.published == []
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    assert _flows(hass) == []
    await hass.async_block_till_done()


async def test_discovery_rejects_an_address_of_another_gateway(
    hass: HomeAssistant, mqtt_transport, gateway: FakeGateway
) -> None:
    discovered = await _discover(
        hass, _info(topic="EnOcean/CCDD0022/stream/devices/DEV1/friendlyId")
    )
    result = await _confirm(hass, discovered["flow_id"], gateway)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "wrong_gateway"}
    assert hass.config_entries.async_entries(DOMAIN) == []
    gateway.eurid = "CCDD0022"
    result = await _confirm(hass, discovered["flow_id"], gateway)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == "CCDD0022"
    await hass.async_block_till_done()


async def test_invalid_announcement_is_ignored_without_publish(
    hass: HomeAssistant, mqtt_transport
) -> None:
    for info in (
        _info(payload=""),
        _info(topic="EnOcean/aabb0011/stream/devices/DEV1/friendlyId"),
        _info(topic="EnOcean/AABB0011/stream/devices/DEV1/states/switch"),
    ):
        result = await _discover(hass, info)
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "invalid_discovery_info"
    assert mqtt_transport.published == []
    assert _flows(hass) == []
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_configured_gateway_aborts_without_side_effects(
    hass: HomeAssistant, mqtt_transport
) -> None:
    entry = (await configure_bridge(hass))["result"]
    await hass.async_block_till_done()
    mqtt_transport.published.clear()
    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        for device in ("DEV1", "DEV2", "DEV3"):
            result = await _discover(
                hass,
                _info(topic=f"EnOcean/AABB0011/stream/devices/{device}/friendlyId"),
            )
            assert result["type"] is FlowResultType.ABORT
            assert result["reason"] == "already_configured"
    reload.assert_not_called()
    # Device announcements neither publish nor rewrite the entry's discovery keys.
    # An MQTT entry keeps its transport (A5): no REST form is offered for it.
    assert mqtt_transport.published == []
    assert entry.discovery_keys == {}
    assert entry.data == {"eag_id": "AABB0011"}
    assert hass.config_entries.async_entries(DOMAIN) == [entry]


@pytest.mark.parametrize(
    "existing",
    [
        {"source": config_entries.SOURCE_IGNORE},
        {"disabled": True},
    ],
    ids=["ignored", "disabled"],
)
async def test_ignored_or_disabled_gateway_is_not_offered_again(
    hass: HomeAssistant, mqtt_transport, existing
) -> None:
    await hass.config_entries.async_add(_entry("AABB0011", **existing))
    result = await _discover(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert mqtt_transport.published == []
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_announcements_of_one_gateway_create_one_flow(
    hass: HomeAssistant, mqtt_transport
) -> None:
    first = await _discover(hass)
    assert first["type"] is FlowResultType.FORM
    for device in ("DEV2", "DEV3"):
        result = await _discover(
            hass, _info(topic=f"EnOcean/AABB0011/stream/devices/{device}/friendlyId")
        )
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "already_in_progress"
    assert [flow["flow_id"] for flow in _flows(hass)] == [first["flow_id"]]
    assert mqtt_transport.published == []


async def test_multiple_gateways_are_offered_separately(
    hass: HomeAssistant, mqtt_transport
) -> None:
    for eag_id in ("AABB0011", "CCDD0022"):
        result = await _discover(
            hass, _info(topic=f"EnOcean/{eag_id}/stream/devices/DEV1/friendlyId")
        )
        assert result["type"] is FlowResultType.FORM
    assert sorted(flow["context"]["unique_id"] for flow in _flows(hass)) == [
        "AABB0011",
        "CCDD0022",
    ]
    assert mqtt_transport.published == []


async def test_manual_setup_during_pending_discovery_creates_no_duplicate(
    hass: HomeAssistant, mqtt_transport, gateway: FakeGateway
) -> None:
    discovered = await _discover(hass)
    result = await configure_rest_bridge(hass, gateway)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_in_progress"
    assert hass.config_entries.async_entries(DOMAIN) == []
    result = await _confirm(hass, discovered["flow_id"], gateway)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert [e.unique_id for e in hass.config_entries.async_entries(DOMAIN)] == [
        "AABB0011"
    ]
    assert mqtt_transport.published == []
    await hass.async_block_till_done()


async def test_entry_added_while_confirm_is_open_aborts_without_request(
    hass: HomeAssistant, mqtt_transport, gateway: FakeGateway
) -> None:
    discovered = await _discover(hass)
    await hass.config_entries.async_add(_entry("AABB0011", disabled=True))
    result = await _confirm(hass, discovered["flow_id"], gateway)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert gateway.requests == []
    assert mqtt_transport.published == []
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_confirm_recovers_from_wrong_password(
    hass: HomeAssistant, mqtt_transport, gateway: FakeGateway
) -> None:
    discovered = await _discover(hass)
    result = await _confirm(hass, discovered["flow_id"], gateway, password="wrong")
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "discovery_confirm"
    assert result["errors"] == {"base": "invalid_auth"}
    result = await _confirm(hass, discovered["flow_id"], gateway)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == "AABB0011"
    await hass.async_block_till_done()


async def test_confirm_recovers_from_unreachable_gateway(
    hass: HomeAssistant, mqtt_transport, gateway: FakeGateway
) -> None:
    """An announced gateway whose address cannot be reached creates no entry."""
    discovered = await _discover(hass)
    config = gateway.config
    await gateway.stop()
    result = await hass.config_entries.flow.async_configure(
        discovered["flow_id"], config
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect_gateway"}
    assert hass.config_entries.async_entries(DOMAIN) == []
    await gateway.start()
    result = await hass.config_entries.flow.async_configure(
        discovered["flow_id"], config
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()


async def test_confirm_recovers_from_unexpected_exception(
    hass: HomeAssistant,
    mqtt_transport,
    gateway: FakeGateway,
    caplog: pytest.LogCaptureFixture,
) -> None:
    discovered = await _discover(hass)
    with patch(
        "custom_components.opus_greennet.config_flow.async_read_gateway_id",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        result = await _confirm(hass, discovered["flow_id"], gateway)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}
    assert "Unexpected exception" in caplog.text
    result = await _confirm(hass, discovered["flow_id"], gateway)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()


async def test_discovered_entry_blocks_a_second_manual_entry(
    hass: HomeAssistant, mqtt_transport, gateway: FakeGateway
) -> None:
    discovered = await _discover(hass)
    await _confirm(hass, discovered["flow_id"], gateway)
    await hass.async_block_till_done()
    result = await configure_rest_bridge(hass, gateway)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert mqtt_transport.published == []
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
