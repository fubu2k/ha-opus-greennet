"""Repair issue for the one-time removal of a duplicate beta entity.

Quality-scale rule ``repair-issues``: the only user-actionable case is a
removed duplicate entity whose references the user must update. Transient
broker, bridge or radio outages and quiet sensors never raise an issue.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.repairs import issue_handler
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from custom_components.opus_greennet.const import DOMAIN
from custom_components.opus_greennet.entity import (
    ISSUE_DUPLICATE_BETA_ENTITY,
    async_create_duplicate_beta_entity_issue,
    migrate_legacy_entity_suffix,
)
from tests.ha_helpers import configure_bridge

INTEGRATION = Path(__file__).resolve().parents[1] / "custom_components/opus_greennet"
PREFIX = "AABB0011_TEST"
ISSUE_ID = f"{ISSUE_DUPLICATE_BETA_ENTITY}_sensor.beta_duplicate"
PLACEHOLDERS = {
    "removed_entity_id": "sensor.beta_duplicate",
    "preserved_entity_id": "sensor.kept_illuminance",
}


async def _entry(hass: HomeAssistant):
    entry = (await configure_bridge(hass))["result"]
    await hass.async_block_till_done()
    return entry


def _registry_entries(hass: HomeAssistant, entry, *, legacy: bool, current: bool):
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, PREFIX)}
    )
    registry = er.async_get(hass)
    if legacy:
        registry.async_get_or_create(
            "sensor",
            DOMAIN,
            f"{PREFIX}_illumination",
            config_entry=entry,
            device_id=device.id,
            suggested_object_id="kept_illuminance",
        )
    if current:
        registry.async_get_or_create(
            "sensor",
            DOMAIN,
            f"{PREFIX}_illuminance",
            config_entry=entry,
            device_id=device.id,
            suggested_object_id="beta_duplicate",
        )


def _migrate(hass: HomeAssistant, entry) -> None:
    migrate_legacy_entity_suffix(
        hass, entry.entry_id, "sensor", PREFIX, "illumination", "illuminance"
    )


def _domain_issues(hass: HomeAssistant) -> dict[str, ir.IssueEntry]:
    return {
        issue_id: issue
        for (domain, issue_id), issue in ir.async_get(hass).issues.items()
        if domain == DOMAIN
    }


async def test_duplicate_removal_raises_one_persistent_fixable_issue(
    hass: HomeAssistant, mqtt_transport
) -> None:
    entry = await _entry(hass)
    _registry_entries(hass, entry, legacy=True, current=True)
    mqtt_transport.published.clear()

    _migrate(hass, entry)

    issues = _domain_issues(hass)
    assert list(issues) == [ISSUE_ID]
    issue = issues[ISSUE_ID]
    assert issue.is_fixable is True
    assert issue.is_persistent is True
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.translation_key == ISSUE_DUPLICATE_BETA_ENTITY
    assert issue.translation_placeholders == PLACEHOLDERS
    assert issue.data == {"config_entry_id": entry.entry_id}
    registry = er.async_get(hass)
    assert registry.async_get("sensor.beta_duplicate") is None
    assert (
        registry.async_get_entity_id("sensor", DOMAIN, f"{PREFIX}_illuminance")
        == "sensor.kept_illuminance"
    )

    # The removal happens once; later migrations and reloads add nothing.
    _migrate(hass, entry)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert list(_domain_issues(hass)) == [ISSUE_ID]
    # Raising the issue is local: no MQTT request was published for it.
    assert not [topic for topic, _ in mqtt_transport.published if "/put/" in topic]


@pytest.mark.parametrize(
    ("legacy", "current"),
    [(True, False), (False, True), (False, False)],
    ids=["rename_only", "upstream_only", "nothing"],
)
async def test_no_issue_without_a_removed_duplicate(
    hass: HomeAssistant, mqtt_transport, legacy: bool, current: bool
) -> None:
    entry = await _entry(hass)
    _registry_entries(hass, entry, legacy=legacy, current=current)
    _migrate(hass, entry)
    assert _domain_issues(hass) == {}


async def test_gateway_outage_raises_no_issue(
    hass: HomeAssistant, mqtt_transport
) -> None:
    """Transient MQTT and bridge outages are availability, not repairs."""
    await _entry(hass)
    await mqtt_transport.set_connected(False)
    await mqtt_transport.set_connected(True)
    assert _domain_issues(hass) == {}


async def test_issue_survives_a_restart(hass: HomeAssistant, mqtt_transport) -> None:
    entry = await _entry(hass)
    _registry_entries(hass, entry, legacy=True, current=True)
    _migrate(hass, entry)
    stored = {"issues": [i.to_json() for i in ir.async_get(hass).issues.values()]}

    restarted = ir.IssueRegistry(hass)
    with patch.object(restarted._store, "async_load", AsyncMock(return_value=stored)):
        await restarted.async_load()

    issue = restarted.async_get_issue(DOMAIN, ISSUE_ID)
    assert issue is not None
    assert issue.active is True
    assert issue.is_fixable is True
    assert issue.translation_placeholders == PLACEHOLDERS
    assert issue.data == {"config_entry_id": entry.entry_id}


async def test_confirming_the_repair_resolves_the_issue(
    hass: HomeAssistant, mqtt_transport
) -> None:
    entry = await _entry(hass)
    _registry_entries(hass, entry, legacy=True, current=True)
    _migrate(hass, entry)
    mqtt_transport.published.clear()
    hass.data["repairs"] = {}
    issue_handler.async_setup(hass)
    manager = hass.data["repairs"]["flow_manager"]

    result = await manager.async_init(DOMAIN, data={"issue_id": ISSUE_ID})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"
    assert result["description_placeholders"] == PLACEHOLDERS

    result = await manager.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert _domain_issues(hass) == {}
    assert mqtt_transport.published == []
    # Confirming changes no registry entry.
    assert er.async_get(hass).async_get("sensor.kept_illuminance") is not None


async def test_removing_the_entry_deletes_only_its_issues(
    hass: HomeAssistant, mqtt_transport
) -> None:
    entry = await _entry(hass)
    _registry_entries(hass, entry, legacy=True, current=True)
    _migrate(hass, entry)
    async_create_duplicate_beta_entity_issue(
        hass, "other-entry", "sensor.other_duplicate", "sensor.other_kept"
    )
    ir.async_create_issue(
        hass,
        DOMAIN,
        "without_data",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_DUPLICATE_BETA_ENTITY,
    )
    ir.async_create_issue(
        hass,
        "other_domain",
        ISSUE_ID,
        data={"config_entry_id": entry.entry_id},
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="other",
    )

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert sorted(_domain_issues(hass)) == [
        f"{ISSUE_DUPLICATE_BETA_ENTITY}_sensor.other_duplicate",
        "without_data",
    ]
    assert ir.async_get(hass).async_get_issue("other_domain", ISSUE_ID) is not None


def test_issue_strings_use_exactly_the_raised_placeholders() -> None:
    strings = json.loads((INTEGRATION / "strings.json").read_text(encoding="utf-8"))
    issue = strings["issues"][ISSUE_DUPLICATE_BETA_ENTITY]
    confirm = issue["fix_flow"]["step"]["confirm"]
    for text in (issue["title"], confirm["title"], confirm["description"]):
        placeholders = {part.split("}")[0] for part in text.split("{")[1:]}
        assert placeholders <= set(PLACEHOLDERS), text
    assert set(PLACEHOLDERS) <= {
        part.split("}")[0] for part in confirm["description"].split("{")[1:]
    }
