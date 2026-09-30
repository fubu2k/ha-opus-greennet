"""Manual child removal, rediscovery, and isolated request cancellation."""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.opus_greennet import async_remove_config_entry_device
from custom_components.opus_greennet.const import DOMAIN
from custom_components.opus_greennet.mqtt_transport import MQTTRequestManager
from tests.ha_helpers import configure_bridge, wait_for_entity


@pytest.mark.parametrize("rediscover", [False, True])
async def test_remove_child_reload_and_rediscovery(hass, mqtt_transport, rediscover):
    old = {"deviceId": "OLD", "eeps": [{"eep": "D1-4B-05"}], "states": {"humidity": 50}}
    replacement = {"deviceId": "NEW", "eeps": [{"eep": "D2-01-00"}]}
    mqtt_transport.devices = [old, replacement]
    entry = (await configure_bridge(hass))["result"]
    humidity = await wait_for_entity(hass, "sensor", "AABB0011_OLD_humidity")
    switch = await wait_for_entity(hass, "switch", "AABB0011_NEW")
    registry = dr.async_get(hass)
    child = registry.async_get_device_by_identifier(
        (DOMAIN, "AABB0011_OLD"), entry.entry_id
    )
    gateway = registry.async_get(entry.runtime_data.gateway_device_id)
    assert not await async_remove_config_entry_device(hass, entry, gateway)
    before = list(mqtt_transport.published)
    assert await async_remove_config_entry_device(hass, entry, child)
    registry.async_remove_device(child.id)
    await hass.async_block_till_done()
    assert mqtt_transport.published == before
    assert hass.states.get(humidity) is None
    assert hass.states.get(switch) is not None
    assert "OLD" not in entry.runtime_data.coordinator.devices
    assert "NEW" in entry.runtime_data.coordinator.devices

    if rediscover:
        mqtt_transport.receive("EnOcean/AABB0011/getAnswer/devices", {"devices": [old]})
        await wait_for_entity(hass, "sensor", "AABB0011_OLD_humidity")
        entities = [
            e
            for e in er.async_get(hass).entities.values()
            if e.unique_id == "AABB0011_OLD_humidity"
        ]
        assert len(entities) == 1
        assert float(hass.states.get(entities[0].entity_id).state) == 50
    else:
        mqtt_transport.devices = [replacement]
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert (
        registry.async_get_device_by_identifier(
            (DOMAIN, "AABB0011_OLD"), entry.entry_id
        )
        is not None
    ) == rediscover
    assert hass.states.get(switch) is not None


async def test_removal_rejects_foreign_identifiers(hass, mqtt_transport):
    entry = (await configure_bridge(hass))["result"]
    await hass.async_block_till_done()
    for identifiers, entries in [
        ({(DOMAIN, "OTHER_CHILD")}, {entry.entry_id}),
        ({(DOMAIN, "AABB0011_CHILD")}, {"other-entry"}),
        ({("foreign", "AABB0011_CHILD")}, {entry.entry_id}),
    ]:
        assert not await async_remove_config_entry_device(
            hass,
            entry,
            SimpleNamespace(
                identifiers=identifiers, config_entry_id=next(iter(entries))
            ),
        )


def test_forget_cancels_only_child_buffers(coordinator, make_device):
    old = make_device("D2-01-00", device_id="OLD")
    new = make_device("D2-01-00", device_id="NEW")
    coordinator.devices.update(OLD=old, NEW=new)
    cancellations = []
    for buffers, timers in [
        (coordinator._telegram_data, coordinator._pending_telegrams),
        (coordinator._device_stream_data, coordinator._pending_device_streams),
    ]:
        buffers.update(OLD={"value": 1}, NEW={"value": 2})
        old_cancel, new_cancel = MagicMock(), MagicMock()
        timers.update(OLD=old_cancel, NEW=new_cancel)
        cancellations.append((old_cancel, new_cancel))
    coordinator.async_forget_device("OLD")
    for old_cancel, new_cancel in cancellations:
        old_cancel.assert_called_once()
        new_cancel.assert_not_called()
    assert "NEW" in coordinator._telegram_data
    assert "OLD" not in coordinator._telegram_data


async def test_removal_cancels_active_and_queued_requests_only_for_child(
    hass, mqtt_transport
):
    mqtt_transport.reply = False
    manager = MQTTRequestManager(hass)

    async def request(device):
        return await manager.async_request(f"get/{device}", f"answer/{device}", device)

    first = asyncio.create_task(request("OLD"))
    queued = asyncio.create_task(request("OLD"))
    other = asyncio.create_task(request("NEW"))
    for _ in range(10):
        await asyncio.sleep(0)
    manager.async_cancel_device("OLD")
    for task in (first, queued):
        with pytest.raises(HomeAssistantError):
            await task
    assert not other.done()
    mqtt_transport.receive("answer/NEW", {"ok": True})
    assert await other == {"ok": True}
    assert sum(topic == "get/OLD" for topic, _ in mqtt_transport.published) == 1
    assert mqtt_transport.subscriptions == []
