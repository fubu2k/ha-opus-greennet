"""Phase 13 (F13-1): rocker event entities use EventDeviceClass.BUTTON.

The device class is registry and frontend metadata only. Unique ID, event
types, enablement, entity ID and user customisations stay unchanged, and the
class causes no request to the device (guardrails A1 and A5).
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from homeassistant.components.event import EventDeviceClass
from homeassistant.helpers import entity_registry as er

from custom_components.opus_greennet.const import DOMAIN
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from custom_components.opus_greennet.event import EVENT_TYPES, OpusGreenNetEvent
from tests.ha_helpers import MQTTTransport, configure_bridge, wait_for_entity

UNIQUE_ID = "AABB0011_ROCKER1"
ROCKER = {"deviceId": "ROCKER1", "friendlyId": "Test", "eeps": [{"eep": "F6-02-01"}]}


def _device_requests(transport: MQTTTransport, start: int = 0) -> list[str]:
    """Return topics addressed to the rocker itself (potential radio traffic)."""
    return [
        topic
        for topic, _payload in transport.published[start:]
        if "/devices/ROCKER1" in topic
    ]


@pytest.mark.parametrize("eep", ["F6-02-01", "F6-03-01"])
def test_rocker_event_is_a_button_with_unchanged_metadata(eep: str) -> None:
    entity = OpusGreenNetEvent(
        coordinator=MagicMock(),
        eag_id="AABB0011",
        gateway_device_id="gateway-device-id",
        device=EnOceanDevice(
            device_id="ROCKER1", friendly_id="Rocker", eeps=[{"eep": eep}]
        ),
    )

    assert entity.device_class is EventDeviceClass.BUTTON
    assert entity.unique_id == UNIQUE_ID
    assert entity.event_types == EVENT_TYPES
    assert entity.entity_category is None
    assert entity.entity_registry_enabled_default is True


async def test_new_rocker_reports_the_button_class_without_requests(
    hass, mqtt_transport
):
    mqtt_transport.devices = [ROCKER]
    await configure_bridge(hass)
    entity_id = await wait_for_entity(hass, "event", UNIQUE_ID)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    assert entry.unique_id == UNIQUE_ID
    assert entry.disabled_by is None
    assert entry.original_device_class == EventDeviceClass.BUTTON
    state = hass.states.get(entity_id)
    assert state.attributes["device_class"] == EventDeviceClass.BUTTON
    assert _device_requests(mqtt_transport) == []


@pytest.mark.parametrize("variant", ["enabled", "user_disabled"])
async def test_existing_rocker_entry_keeps_user_settings(hass, mqtt_transport, variant):
    config_entry = (await configure_bridge(hass))["result"]
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    disabled_by = er.RegistryEntryDisabler.USER if variant == "user_disabled" else None
    original = registry.async_get_or_create(
        "event",
        DOMAIN,
        UNIQUE_ID,
        config_entry=config_entry,
        suggested_object_id="kept_rocker",
        disabled_by=disabled_by,
    )
    registry.async_update_entity(original.entity_id, name="My rocker", icon="mdi:star")

    start = len(mqtt_transport.published)
    mqtt_transport.devices = [ROCKER]
    mqtt_transport.receive(
        "EnOcean/AABB0011/getAnswer/devices",
        {"header": {"httpStatus": 200}, "devices": mqtt_transport.devices},
    )
    entity_id = await wait_for_entity(hass, "event", UNIQUE_ID)
    if variant == "enabled":
        # The pre-created entry exists already; wait until the entity is added.
        async with asyncio.timeout(6):
            while hass.states.get(entity_id) is None:
                await asyncio.sleep(0.01)
    await hass.async_block_till_done()

    assert entity_id == original.entity_id
    current = registry.async_get(entity_id)
    assert current.unique_id == UNIQUE_ID
    assert current.disabled_by == disabled_by
    assert current.name == "My rocker"
    assert current.icon == "mdi:star"
    assert [e for e in registry.entities.values() if e.unique_id == UNIQUE_ID] == [
        current
    ]
    if variant == "enabled":
        state = hass.states.get(entity_id)
        assert state.attributes["device_class"] == EventDeviceClass.BUTTON
        assert state.attributes["icon"] == "mdi:star"
        assert current.original_device_class == EventDeviceClass.BUTTON
    else:
        assert hass.states.get(entity_id) is None
    assert _device_requests(mqtt_transport, start) == []
