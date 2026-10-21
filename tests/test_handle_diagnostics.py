"""Phase 6: handle unlock-request and mechanics-fault diagnostic defaults.

Both entities are diagnostics and disabled for NEW registry entries only.
Existing entries keep their enablement, entity ID and customisations, the
normal handle entities are unchanged, and no setting sends a device request.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.opus_greennet.binary_sensor import (
    OpusGreenNetHandlePositionSensor,
    OpusGreenNetStateBinarySensor,
)
from custom_components.opus_greennet.const import DOMAIN
from custom_components.opus_greennet.sensor import OpusGreenNetHandleSensor
from tests.fake_gateway import FakeGateway, telegram
from tests.ha_helpers import setup_bridge, wait_for_entity

PREFIX = "AABB0011_HANDLE"
HANDLE_STATES = {
    "handle": "closed",
    "lock": "locked",
    "unlock": "notRequested",
    "mechanics": "ok",
}
DIAGNOSTICS = (
    ("sensor", "unlock_request"),
    ("binary_sensor", "mechanics_fault"),
)
NORMAL_ENTITIES = (
    ("sensor", "handle_state", "closed"),
    ("binary_sensor", "handle_open", "off"),
    ("binary_sensor", "handle_tilt", "off"),
    ("binary_sensor", "handle_closed", "on"),
)


def _handle(eep: str) -> dict:
    return {
        "deviceId": "HANDLE",
        "eeps": [{"eep": eep}],
        "states": [
            {"key": key, "value": value} for key, value in HANDLE_STATES.items()
        ],
    }


def _device_requests(gateway: FakeGateway, start: int = 0) -> list[str]:
    """Return requests addressed to the handle itself (potential radio traffic)."""
    return [
        path
        for _method, path, _body in gateway.requests[start:]
        if path.startswith("/devices/HANDLE")
        # The profile read at setup is gateway data, not radio traffic.
        and not path.endswith("/profile")
    ]


async def _wait_for_state(hass: HomeAssistant, entity_id: str) -> str:
    async with asyncio.timeout(6):
        while (state := hass.states.get(entity_id)) is None:
            await asyncio.sleep(0.01)
    await hass.async_block_till_done()
    return state.state


def test_entity_metadata_is_limited_to_the_two_diagnostics(make_device) -> None:
    coordinator = MagicMock()
    device = make_device("D2-06-40", device_id="HANDLE")
    unlock = OpusGreenNetHandleSensor(
        coordinator, "AABB0011", "gw", device, "unlock_request"
    )
    position = OpusGreenNetHandleSensor(
        coordinator, "AABB0011", "gw", device, "handle_state"
    )
    fault = OpusGreenNetStateBinarySensor(
        coordinator,
        "AABB0011",
        "gw",
        device,
        "mechanics_fault",
        "mechanics_fault",
        BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        enabled_by_default=False,
    )
    motion = OpusGreenNetStateBinarySensor(
        coordinator,
        "AABB0011",
        "gw",
        make_device("A5-07-03", device_id="SMS"),
        "motion",
        "motion",
        BinarySensorDeviceClass.MOTION,
    )
    opened = OpusGreenNetHandlePositionSensor(
        coordinator, "AABB0011", "gw", device, "open"
    )

    for diagnostic in (unlock, fault):
        assert diagnostic.entity_category is EntityCategory.DIAGNOSTIC
        assert diagnostic.entity_registry_enabled_default is False
    # Identities and state semantics are unchanged.
    assert unlock.unique_id == f"{PREFIX}_unlock_request"
    assert unlock.options == ["requested", "not_requested"]
    assert fault.unique_id == f"{PREFIX}_mechanics_fault"
    assert fault.device_class is BinarySensorDeviceClass.PROBLEM
    # Normal handle entities and other boolean sensors keep their defaults.
    for normal in (position, motion, opened):
        assert normal.entity_category is None
        assert normal.entity_registry_enabled_default is True


@pytest.mark.parametrize("eep", ["D2-06-40", "F6-10-00", "D2-03-10"])
async def test_new_handle_registers_disabled_diagnostics(hass, gateway, eep):
    gateway.devices = [_handle(eep)]
    await setup_bridge(hass, gateway)
    registry = er.async_get(hass)

    for domain, suffix, expected in NORMAL_ENTITIES:
        entity_id = await wait_for_entity(hass, domain, f"{PREFIX}_{suffix}")
        entry = registry.async_get(entity_id)
        assert entry.disabled_by is None
        assert entry.entity_category is None
        assert hass.states.get(entity_id).state == expected

    if eep == "D2-06-40":
        lock = await wait_for_entity(hass, "lock", f"{PREFIX}_autolock")
        assert registry.async_get(lock).disabled_by is None
        assert registry.async_get(lock).entity_category is None
        assert hass.states.get(lock).state == "locked"
        for domain, suffix in DIAGNOSTICS:
            entity_id = await wait_for_entity(hass, domain, f"{PREFIX}_{suffix}")
            entry = registry.async_get(entity_id)
            assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
            assert entry.entity_category is EntityCategory.DIAGNOSTIC
            # The entry stays in the registry but is not loaded.
            assert hass.states.get(entity_id) is None
    else:
        # Unsupported profiles still get no AutoLock diagnostics.
        for domain, suffix in DIAGNOSTICS:
            assert (
                registry.async_get_entity_id(domain, DOMAIN, f"{PREFIX}_{suffix}")
                is None
            )

    assert _device_requests(gateway) == []


@pytest.mark.parametrize("variant", ["enabled", "user_disabled"])
async def test_existing_diagnostic_entries_keep_user_settings(hass, gateway, variant):
    entry = await setup_bridge(hass, gateway)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    disabled_by = er.RegistryEntryDisabler.USER if variant == "user_disabled" else None
    existing = {}
    for domain, suffix in DIAGNOSTICS:
        original = registry.async_get_or_create(
            domain,
            DOMAIN,
            f"{PREFIX}_{suffix}",
            config_entry=entry,
            suggested_object_id=f"kept_{suffix}",
            disabled_by=disabled_by,
        )
        registry.async_update_entity(
            original.entity_id, name=f"My {suffix}", icon="mdi:star"
        )
        existing[suffix] = original.entity_id

    start = len(gateway.requests)
    gateway.devices = [_handle("D2-06-40")]
    await gateway.push(gateway.devices_document())
    # Pre-created entries already exist, so wait for the normal entities of the
    # same discovery batches before inspecting the updated diagnostic entries.
    await wait_for_entity(hass, "sensor", f"{PREFIX}_handle_state")
    await wait_for_entity(hass, "binary_sensor", f"{PREFIX}_handle_closed")

    for domain, suffix in DIAGNOSTICS:
        entity_id = await wait_for_entity(hass, domain, f"{PREFIX}_{suffix}")
        assert entity_id == existing[suffix]
        current = registry.async_get(entity_id)
        assert current.unique_id == f"{PREFIX}_{suffix}"
        assert current.disabled_by == disabled_by
        assert current.name == f"My {suffix}"
        assert current.icon == "mdi:star"
        assert current.entity_category is EntityCategory.DIAGNOSTIC
        assert [
            e for e in registry.entities.values() if e.unique_id == current.unique_id
        ] == [current]

    if variant == "enabled":
        unlock = existing["unlock_request"]
        fault = existing["mechanics_fault"]
        assert await _wait_for_state(hass, unlock) == "not_requested"
        assert await _wait_for_state(hass, fault) == "off"
        # An unrecognised mechanics value is unknown, never a healthy "off".
        await gateway.push(
            telegram("HANDLE", [{"key": "mechanics", "value": "invalid"}])
        )
        await hass.async_block_till_done()
        assert hass.states.get(fault).state == "unknown"
    else:
        for entity_id in existing.values():
            assert hass.states.get(entity_id) is None

    assert _device_requests(gateway, start) == []


async def test_activating_diagnostics_sends_no_device_request(hass, gateway):
    gateway.devices = [_handle("D2-06-40")]
    entry = await setup_bridge(hass, gateway)
    registry = er.async_get(hass)
    entity_ids = [
        await wait_for_entity(hass, domain, f"{PREFIX}_{suffix}")
        for domain, suffix in DIAGNOSTICS
    ]
    start = len(gateway.requests)

    # User activation for troubleshooting; HA reloads the entry to load them.
    for entity_id in entity_ids:
        registry.async_update_entity(entity_id, disabled_by=None)
    assert await hass.config_entries.async_reload(entry.entry_id)
    coordinator = entry.runtime_data.coordinator
    async with asyncio.timeout(5):
        while not coordinator._stream_connected:  # noqa: SLF001
            await asyncio.sleep(0.01)

    unlock, fault = entity_ids
    assert await _wait_for_state(hass, unlock) == "not_requested"
    assert await _wait_for_state(hass, fault) == "off"
    for entity_id in entity_ids:
        assert registry.async_get(entity_id).entity_category is (
            EntityCategory.DIAGNOSTIC
        )
    # Pushed updates reach the activated entities without any device request.
    await gateway.push(telegram("HANDLE", [{"key": "unlock", "value": "requested"}]))
    await gateway.push(telegram("HANDLE", [{"key": "mechanics", "value": "error"}]))
    await hass.async_block_till_done()
    assert hass.states.get(unlock).state == "requested"
    assert hass.states.get(fault).state == "on"
    assert _device_requests(gateway, start) == []
