"""Icon translations (quality-scale rule ``icon-translations``).

``icons.json`` maps raw entity states to icons by translation key. These tests
check the mapping against the entities the platforms actually create, keep the
section 11 semantics of the implementation plan (neutral fallbacks, a request
is not an unlock) and prove that icons are pure metadata: no Python icon
property, no state or device-class change and no MQTT traffic.

The MDI catalogue of the pinned frontend is not part of the ``homeassistant``
wheel. CI checks every identifier with ``script/check_icons.py`` against the
reference core commit, and hassfest at that commit validates the file schema.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.icon import async_get_icons

from custom_components.opus_greennet.binary_sensor import (
    OpusGreenNetHandlePositionSensor,
    OpusGreenNetStateBinarySensor,
)
from custom_components.opus_greennet.const import DOMAIN
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from custom_components.opus_greennet.sensor import OpusGreenNetHandleSensor

INTEGRATION = Path(__file__).resolve().parents[1] / "custom_components/opus_greennet"
ICONS = json.loads((INTEGRATION / "icons.json").read_text(encoding="utf-8"))
STRINGS = json.loads((INTEGRATION / "strings.json").read_text(encoding="utf-8"))
ENTITY_ICONS = ICONS["entity"]
MDI = re.compile(r"mdi:[a-z0-9]+(?:-[a-z0-9]+)*")

# Icons that would assert a closed window, a lock, an unlock or a healthy
# mechanism. None of them may be shown for missing data.
ASSERTIVE_ICONS = {
    "mdi:window-closed",
    "mdi:window-closed-variant",
    "mdi:lock",
    "mdi:lock-outline",
    "mdi:lock-open",
    "mdi:lock-open-variant",
    "mdi:lock-open-outline",
    "mdi:check-circle",
    "mdi:check-circle-outline",
}


def _handle(eep: str = "D2-06-40") -> EnOceanDevice:
    return EnOceanDevice(device_id="HANDLE", friendly_id="Handle", eeps=[{"eep": eep}])


def _leaves(value: object, path: str = ""):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _leaves(item, f"{path}.{key}" if path else key)
    else:
        yield path, value


def test_icons_file_contains_only_mdi_identifiers() -> None:
    leaves = dict(_leaves(ICONS))
    assert leaves
    for path, icon in leaves.items():
        assert isinstance(icon, str) and MDI.fullmatch(icon), path


def test_every_mapped_entity_exists_with_a_translation() -> None:
    assert set(ENTITY_ICONS) <= set(STRINGS["entity"])
    for platform, keys in ENTITY_ICONS.items():
        for key, definition in keys.items():
            assert key in STRINGS["entity"][platform], (platform, key)
            # Every mapping needs a neutral default for unmapped states.
            assert "default" in definition, (platform, key)
            assert set(definition) <= {"default", "state"}, (platform, key)
            for state, icon in definition.get("state", {}).items():
                assert icon != definition["default"], (platform, key, state)


def test_service_icons_cover_exactly_the_services() -> None:
    services = yaml.safe_load((INTEGRATION / "services.yaml").read_text())
    assert set(ICONS["services"]) == set(services)


def test_sensor_states_match_the_entities() -> None:
    """Keys are the raw enum states, never translated labels."""
    device = _handle()
    for key in ("handle_state", "unlock_request"):
        entity = OpusGreenNetHandleSensor(MagicMock(), "AABB0011", "gw", device, key)
        assert entity.translation_key == key
        assert entity.device_class is SensorDeviceClass.ENUM
        assert set(ENTITY_ICONS["sensor"][key]["state"]) == set(entity.options)
        assert set(entity.options) == set(STRINGS["entity"]["sensor"][key]["state"])


def test_binary_sensor_states_and_classes_are_unchanged() -> None:
    device = _handle()
    closed = OpusGreenNetHandlePositionSensor(
        MagicMock(), "AABB0011", "gw", device, "closed"
    )
    # ON means closed, so WINDOW (ON = open) must stay unused for this entity.
    assert closed.translation_key == "handle_closed"
    assert closed.device_class is None
    assert set(ENTITY_ICONS["binary_sensor"]["handle_closed"]["state"]) == {
        "on",
        "off",
    }
    fault = OpusGreenNetStateBinarySensor(
        MagicMock(),
        "AABB0011",
        "gw",
        device,
        "mechanics_fault",
        "mechanics_fault",
        BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        enabled_by_default=False,
    )
    assert fault.translation_key == "mechanics_fault"
    assert fault.device_class is BinarySensorDeviceClass.PROBLEM
    assert fault.entity_category is EntityCategory.DIAGNOSTIC
    assert fault.entity_registry_enabled_default is False
    assert set(ENTITY_ICONS["binary_sensor"]["mechanics_fault"]["state"]) == {"on"}
    # Entities that keep their device-class icons are deliberately not mapped.
    for position in ("open", "tilted"):
        entity = OpusGreenNetHandlePositionSensor(
            MagicMock(), "AABB0011", "gw", device, position
        )
        assert entity.device_class is BinarySensorDeviceClass.WINDOW
        assert entity.translation_key not in ENTITY_ICONS["binary_sensor"]


@pytest.mark.parametrize(
    ("platform", "key"),
    [
        ("sensor", "handle_state"),
        ("sensor", "unlock_request"),
        ("binary_sensor", "handle_closed"),
        ("binary_sensor", "mechanics_fault"),
    ],
)
def test_missing_data_uses_a_neutral_fallback(platform: str, key: str) -> None:
    """Unknown and unavailable states fall back to ``default``."""
    definition = ENTITY_ICONS[platform][key]
    assert definition["default"] not in ASSERTIVE_ICONS
    assert definition["default"] not in definition["state"].values()


def test_unlock_request_never_shows_an_unlocked_lock() -> None:
    icons = ENTITY_ICONS["sensor"]["unlock_request"]
    for icon in (icons["default"], *icons["state"].values()):
        assert not icon.startswith("mdi:lock-open"), icon
    assert icons["state"]["requested"] != icons["state"]["not_requested"]


def test_mechanics_fault_icons_follow_the_states() -> None:
    icons = ENTITY_ICONS["binary_sensor"]["mechanics_fault"]
    # Only an explicit fault gets the alert icon. "off" and unknown share the
    # neutral cog: neither is presented as a confirmed healthy mechanism.
    assert icons["state"]["on"] == "mdi:alert-circle"
    assert icons["default"] == "mdi:cog-outline"
    # Replaced candidate of plan section 11 (not in frontend 20260930.4).
    assert "mdi:cog-alert" not in dict(_leaves(ICONS)).values()


def test_handle_icons_distinguish_the_reported_positions() -> None:
    icons = ENTITY_ICONS["sensor"]["handle_state"]["state"]
    assert len(set(icons.values())) == 3
    assert (
        icons["closed"] == ENTITY_ICONS["binary_sensor"]["handle_closed"]["state"]["on"]
    )


def test_no_python_icon_overrides() -> None:
    """Icons come only from icons.json; code never computes or sets one."""
    for path in INTEGRATION.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                assert node.name != "icon", path.name
            if isinstance(node, ast.Assign | ast.AnnAssign):
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                for target in targets:
                    name = getattr(target, "attr", getattr(target, "id", ""))
                    assert name != "_attr_icon", path.name
            if isinstance(node, ast.keyword):
                assert node.arg != "icon", path.name


async def test_home_assistant_loads_the_icons(hass: HomeAssistant, gateway) -> None:
    """Loading icons is a local file read and sends nothing to the gateway."""
    entity_icons = await async_get_icons(hass, "entity", integrations=[DOMAIN])
    assert entity_icons[DOMAIN] == ENTITY_ICONS
    service_icons = await async_get_icons(hass, "services", integrations=[DOMAIN])
    assert service_icons[DOMAIN] == ICONS["services"]
    assert gateway.requests == []
