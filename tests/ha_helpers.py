"""A small MQTT boundary and public HA flow helper for integration tests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from time import monotonic
from typing import Any

from homeassistant import config_entries
from homeassistant.components import mqtt
from homeassistant.components.mqtt.const import MQTT_CONNECTION_STATE
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from paho.mqtt.client import topic_matches_sub

from custom_components.opus_greennet.const import DOMAIN
from tests.fake_gateway import FakeGateway


class MQTTTransport:
    """In-memory MQTT boundary with canned bridge responses and subscriptions."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.connected = True
        self.devices: list[dict] = []
        self.published: list[tuple[str, str]] = []
        self.subscriptions: list[tuple[str, Callable]] = []
        self.reply = True
        self.reply_info = True

    async def subscribe(self, hass, topic, callback, **kwargs) -> Callable:
        """Capture the real integration's topic subscriptions."""
        subscription = (topic, callback)
        self.subscriptions.append(subscription)

        def unsubscribe() -> None:
            self.subscriptions.remove(subscription)

        return unsubscribe

    def subscribed(self, hass, topic, qos, callback) -> Callable:
        """Simulate broker SUBACK without changing application behavior."""
        return self.hass.loop.call_soon(callback).cancel

    async def publish(self, hass, topic, payload, **kwargs) -> None:
        """Return protocol responses through the subscribed MQTT callbacks."""
        self.published.append((topic, payload))
        if not self.reply:
            return
        if topic.endswith("/get/config/system/uptime"):
            self.receive(
                topic.replace("/get/", "/getAnswer/"),
                {
                    "header": {"httpStatus": 200},
                    "systemUptimeResponse": {"uptime": 3600},
                },
            )
        elif topic.endswith("/get/config/system/info") and self.reply_info:
            self.receive(
                topic.replace("/get/", "/getAnswer/"),
                {"model": "GreenNet Bridge", "version": "test"},
            )
        elif topic.endswith("/get/devices"):
            self.receive(
                topic.replace("/get/", "/getAnswer/"),
                {"header": {"httpStatus": 200}, "devices": self.devices},
            )
        elif "/put/" in topic:
            self.receive(
                topic.replace("/put/", "/putAnswer/"),
                {"header": {"httpStatus": 200}},
            )

    def receive(self, topic: str, payload: object, *, retain: bool = False) -> None:
        """Deliver a message using Home Assistant's MQTT message type."""
        for subscribed_topic, callback in list(self.subscriptions):
            if topic_matches_sub(subscribed_topic, topic):
                callback(
                    mqtt.ReceiveMessage(
                        topic=topic,
                        payload=payload
                        if isinstance(payload, str | bytes)
                        else json.dumps(payload),
                        qos=1,
                        retain=retain,
                        subscribed_topic=subscribed_topic,
                        timestamp=monotonic(),
                    )
                )

    async def set_connected(self, connected: bool) -> None:
        """Emit MQTT's public connectivity signal."""
        self.connected = connected
        async_dispatcher_send(self.hass, MQTT_CONNECTION_STATE, connected)
        await self.hass.async_block_till_done()


async def start_user_flow(hass: HomeAssistant) -> dict:
    """Open the manual flow; it shows the REST form directly (phase R4.3)."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result


async def configure_bridge(hass: HomeAssistant, eag_id: str = "aabb0011") -> dict:
    """Add an MQTT entry the way the former manual MQTT step did.

    Since phase R4.3 new gateways are added over REST only; MQTT entries come
    from earlier versions or from the reconfigure flow. Tests that need such an
    entry get it with the former step's behaviour: the same normalisation, the
    same single gateway probe (``validate_input``, unchanged MQTT traffic), the
    same entry data and unique ID. A known gateway aborts before the probe.
    """
    from custom_components.opus_greennet.config_flow import validate_input
    from custom_components.opus_greennet.discovery import normalize_eag_id

    normalized = normalize_eag_id(eag_id)
    known = {entry.unique_id for entry in hass.config_entries.async_entries(DOMAIN)}
    if normalized is not None and normalized in known:
        return {"type": FlowResultType.ABORT, "reason": "already_configured"}
    info = await validate_input(hass, {"eag_id": eag_id})
    entry = ConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title=info["title"],
        data={"eag_id": info["eag_id"]},
        source=config_entries.SOURCE_USER,
        options={},
        unique_id=info["eag_id"],
        discovery_keys={},
        subentries_data=None,
    )
    await hass.config_entries.async_add(entry)
    return {
        "type": FlowResultType.CREATE_ENTRY,
        "title": entry.title,
        "data": dict(entry.data),
        "result": entry,
    }


async def configure_rest_bridge(
    hass: HomeAssistant, gateway: FakeGateway, **overrides: Any
) -> dict[str, Any]:
    """Submit a REST gateway through the actual config flow manager."""
    result = await start_user_flow(hass)
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {**gateway.config, **overrides}
    )


async def setup_rest_bridge(hass: HomeAssistant, gateway: FakeGateway) -> ConfigEntry:
    """Configure a REST gateway, wait for its event stream and return the entry."""
    result = await configure_rest_bridge(hass, gateway)
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    await hass.async_block_till_done()
    await gateway.wait_for_stream()
    await hass.async_block_till_done()
    return hass.config_entries.async_entries(DOMAIN)[0]


async def wait_for_entity(
    hass: HomeAssistant, domain: str, unique_id: str, timeout: float = 6
) -> str:
    """Wait for discovery and entity setup to finish in the real HA registry."""
    registry = er.async_get(hass)
    async with asyncio.timeout(timeout):
        while (
            entity_id := registry.async_get_entity_id(domain, DOMAIN, unique_id)
        ) is None:
            await asyncio.sleep(0.01)
        await hass.async_block_till_done()
    return entity_id


async def wait_until(predicate: Any, timeout: float = 5) -> None:
    """Poll a condition while the event loop keeps running."""
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)
