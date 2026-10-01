"""Compatibility for community fork entity IDs and translated states."""

import json
import re
from pathlib import Path

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send

from custom_components.opus_greennet.const import DOMAIN
from custom_components.opus_greennet.coordinator import SIGNAL_DEVICE_DISCOVERED
from custom_components.opus_greennet.entity import migrate_legacy_entity_suffix
from tests.ha_helpers import configure_bridge, wait_for_entity


@pytest.mark.parametrize(
    "domain,eep,legacy_suffix,current_suffix,state",
    [
        ("sensor", "A5-07-03", "illumination", "illuminance", "120.0"),
        ("lock", "D2-06-40", "window_handle_lock", "autolock", "locked"),
    ],
)
@pytest.mark.parametrize("variant", ["legacy", "duplicate", "disabled", "upstream"])
async def test_migration_preserves_entity_id_and_settings(
    hass, mqtt_transport, domain, eep, legacy_suffix, current_suffix, state, variant
):
    entry = (await configure_bridge(hass))["result"]
    await hass.async_block_till_done()
    prefix = "AABB0011_MIGRATE"
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, prefix)}
    )
    registry = er.async_get(hass)
    original = registry.async_get_or_create(
        domain,
        DOMAIN,
        f"{prefix}_{current_suffix if variant == 'upstream' else legacy_suffix}",
        config_entry=entry,
        device_id=device.id,
        suggested_object_id="my_existing_dashboard_entity",
        disabled_by=er.RegistryEntryDisabler.USER if variant == "disabled" else None,
    )
    registry.async_update_entity(original.entity_id, name="My chosen name")
    if variant == "duplicate":
        duplicate = registry.async_get_or_create(
            domain,
            DOMAIN,
            f"{prefix}_{current_suffix}",
            config_entry=entry,
            device_id=device.id,
            suggested_object_id="unwanted_beta_duplicate",
        )
    mqtt_transport.devices = [
        {
            "deviceId": "MIGRATE",
            "eeps": [{"eep": eep}],
            "states": {"illumination": 120, "lock": "locked", "unlock": False},
        }
    ]
    mqtt_transport.receive(
        "EnOcean/AABB0011/getAnswer/devices",
        {"header": {"httpStatus": 200}, "devices": mqtt_transport.devices},
    )
    await wait_for_entity(hass, "sensor", f"{prefix}_signal_strength")
    entity_id = await wait_for_entity(hass, domain, f"{prefix}_{current_suffix}")
    assert entity_id == original.entity_id
    migrated = registry.async_get(entity_id)
    assert migrated.name == "My chosen name"
    assert migrated.disabled_by == original.disabled_by
    if variant != "disabled":
        assert hass.states.get(entity_id).state == state
    if variant == "duplicate":
        assert registry.async_get(duplicate.entity_id) is None
    assert (
        registry.async_get_entity_id(domain, DOMAIN, f"{prefix}_{legacy_suffix}")
        is None
    )
    # Repeated discovery must not create another entity or remove the survivor.
    async_dispatcher_send(
        hass,
        f"{SIGNAL_DEVICE_DISCOVERED}_AABB0011",
        entry.runtime_data.coordinator.devices["MIGRATE"],
    )
    await hass.async_block_till_done()
    assert (
        registry.async_get_entity_id(domain, DOMAIN, f"{prefix}_{current_suffix}")
        == entity_id
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    assert (
        await wait_for_entity(hass, domain, f"{prefix}_{current_suffix}") == entity_id
    )


@pytest.mark.parametrize("foreign", ["legacy_device", "current_device", "config_entry"])
async def test_migration_does_not_remove_unrelated_entries(
    hass, mqtt_transport, foreign
):
    entry = (await configure_bridge(hass))["result"]
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    expected = devices.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "AABB0011_TEST")}
    )
    other = devices.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "OTHER")}
    )
    legacy = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "AABB0011_TEST_illumination",
        config_entry=entry,
        device_id=other.id if foreign == "legacy_device" else expected.id,
    )
    current = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "AABB0011_TEST_illuminance",
        config_entry=entry,
        device_id=other.id if foreign == "current_device" else expected.id,
    )
    migrate_legacy_entity_suffix(
        hass,
        "different_entry" if foreign == "config_entry" else entry.entry_id,
        "sensor",
        "AABB0011_TEST",
        "illumination",
        "illuminance",
    )
    assert registry.async_get(legacy.entity_id) == legacy
    assert registry.async_get(current.entity_id) == current


def test_translations_have_matching_keys_and_placeholders():
    root = Path(__file__).parents[1] / "custom_components/opus_greennet"
    source = json.loads((root / "strings.json").read_text())

    def compare(original, translated):
        if isinstance(original, dict):
            assert original.keys() == translated.keys()
            for key in original:
                assert re.fullmatch(r"[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?", key)
                compare(original[key], translated[key])
        else:
            assert set(re.findall(r"\{[^}]+\}", original)) == set(
                re.findall(r"\{[^}]+\}", translated)
            )

    for filename in ("en.json", "de.json"):
        compare(source, json.loads((root / "translations" / filename).read_text()))
