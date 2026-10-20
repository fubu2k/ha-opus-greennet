"""Phase R2: REST-only operation without the MQTT integration.

The REST transport, the platforms and the shared modules must not depend on
the MQTT transport (plan ``Implementations-Plan-Rest-API.md``, couplings
K1-K6, decisions E2-E4). The MQTT transport keeps every name it exported
before; re-exports are no behaviour change (decision D3, section 0.1).

These are unit and Home Assistant integration tests against a simulated
gateway. They do not verify real OPUS hardware.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from types import MappingProxyType
from typing import get_protocol_members
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries, loader
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError

import custom_components.opus_greennet as package
from custom_components.opus_greennet import (
    config_flow,
    coordinator,
    domain,
    mqtt_transport,
    rest_coordinator,
)
from custom_components.opus_greennet.const import DOMAIN
from custom_components.opus_greennet.discovery import DISCOVERY_TOPIC_FILTER
from custom_components.opus_greennet.transport import OpusCoordinator
from tests.fake_gateway import FakeGateway, device_object, telegram
from tests.ha_helpers import (
    setup_rest_bridge,
    start_user_flow,
    wait_for_entity,
    wait_until,
)

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = ROOT / "custom_components" / "opus_greennet"
MANIFEST = json.loads((PACKAGE_DIR / "manifest.json").read_text(encoding="utf-8"))
MQTT_COMPONENT = "homeassistant.components.mqtt"
# Integration modules that belong to the MQTT transport only.
MQTT_TRANSPORT_MODULES = frozenset({"coordinator", "mqtt_transport", "discovery"})
# Modules that a REST-only installation imports when an entry is set up.
REST_SAFE_MODULES = (
    "custom_components.opus_greennet",
    "custom_components.opus_greennet.domain",
    "custom_components.opus_greennet.transport",
    "custom_components.opus_greennet.rest_coordinator",
    "custom_components.opus_greennet.api",
    "custom_components.opus_greennet.protocol",
    "custom_components.opus_greennet.entity",
    "custom_components.opus_greennet.diagnostics",
    "custom_components.opus_greennet.config_flow",
    "custom_components.opus_greennet.light",
    "custom_components.opus_greennet.switch",
    "custom_components.opus_greennet.cover",
    "custom_components.opus_greennet.climate",
    "custom_components.opus_greennet.sensor",
    "custom_components.opus_greennet.binary_sensor",
    "custom_components.opus_greennet.event",
    "custom_components.opus_greennet.lock",
)
SWITCH = "050BF714"


def _modules_loaded_by(*modules: str) -> list[str]:
    """Import ``modules`` in a fresh interpreter; return the MQTT modules loaded."""
    code = (
        "import importlib, json, sys\n"
        f"for name in {list(modules)!r}:\n"
        "    importlib.import_module(name)\n"
        f"print(json.dumps(sorted(m for m in sys.modules if m == {MQTT_COMPONENT!r}"
        f" or m.startswith({MQTT_COMPONENT + '.'!r}))))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    loaded: list[str] = json.loads(result.stdout.strip().splitlines()[-1])
    return loaded


def _import_targets(node: ast.Import | ast.ImportFrom) -> set[str]:
    """Return the integration modules and HA components an import statement names."""
    if isinstance(node, ast.Import):
        return {alias.name for alias in node.names}
    if node.level == 0:
        base = node.module or ""
        return {base, *(f"{base}.{alias.name}" for alias in node.names)}
    if node.module is None:
        return {alias.name for alias in node.names}
    return {node.module.split(".")[0]}


def _imports(path: Path, *, top_level_only: bool) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = tree.body if top_level_only else list(ast.walk(tree))
    found: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Import | ast.ImportFrom):
            found |= _import_targets(node)
    return found


def _is_mqtt_import(target: str) -> bool:
    return (
        target in MQTT_TRANSPORT_MODULES
        or target == MQTT_COMPONENT
        or target.startswith(MQTT_COMPONENT + ".")
    )


def _mqtt_entry(eag_id: str = "AABB0011") -> config_entries.ConfigEntry:
    """An MQTT entry as stored before phase 14 (no transport key)."""
    return config_entries.ConfigEntry(
        data={"eag_id": eag_id},
        discovery_keys=MappingProxyType({}),
        domain=DOMAIN,
        minor_version=1,
        options={},
        source=config_entries.SOURCE_USER,
        subentries_data=None,
        title=f"Opus GreenNet ({eag_id})",
        unique_id=eag_id,
        version=1,
    )


# ── Import boundary (K2, K3, K4) ─────────────────────────────────────────────


def test_rest_modules_do_not_import_the_mqtt_component() -> None:
    """A REST-only installation never imports Home Assistant's MQTT module."""
    assert _modules_loaded_by(*REST_SAFE_MODULES) == []


def test_mqtt_transport_still_imports_the_mqtt_component() -> None:
    """Control: the check detects MQTT when the MQTT coordinator is imported."""
    assert MQTT_COMPONENT in _modules_loaded_by(
        "custom_components.opus_greennet.coordinator"
    )


@pytest.mark.parametrize(
    "module",
    [
        "domain",
        "transport",
        "rest_coordinator",
        "api",
        "protocol",
        "wire",
        "const",
        "enocean_device",
        "entity",
        "diagnostics",
        "binary_sensor_descriptions",
        "light",
        "switch",
        "cover",
        "climate",
        "sensor",
        "binary_sensor",
        "event",
        "lock",
    ],
)
def test_shared_and_rest_modules_have_no_mqtt_import(module: str) -> None:
    """Not even a function-level or TYPE_CHECKING import of the MQTT transport."""
    imports = _imports(PACKAGE_DIR / f"{module}.py", top_level_only=False)
    assert sorted(target for target in imports if _is_mqtt_import(target)) == []


@pytest.mark.parametrize("module", ["__init__", "config_flow"])
def test_setup_and_flow_import_mqtt_only_on_demand(module: str) -> None:
    """``__init__`` and the config flow load the MQTT transport only when used."""
    top_level = _imports(PACKAGE_DIR / f"{module}.py", top_level_only=True)
    anywhere = _imports(PACKAGE_DIR / f"{module}.py", top_level_only=False)
    assert sorted(target for target in top_level if _is_mqtt_import(target)) == []
    # The MQTT path still exists; it is only imported on demand.
    assert any(_is_mqtt_import(target) for target in anywhere)


# ── Transport-neutral domain module (E2) and re-exports ──────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "ACK_STATUS_DEFERRED",
        "GATEWAY_HEALTH_INTERVAL",
        "OUTBOUND_STATE_RECONCILIATION_DELAYS",
        "SIGNAL_AVAILABILITY_UPDATE",
        "SIGNAL_DEVICE_DISCOVERED",
        "SIGNAL_DEVICE_REMOVED",
        "SIGNAL_DEVICE_STATE_UPDATE",
        "SUPERSEDED",
        "CommandDelivery",
        "CoverMovement",
    ],
)
def test_coordinator_reexports_the_domain_names(name: str) -> None:
    """Existing imports from ``coordinator`` get the very same objects."""
    assert getattr(coordinator, name) is getattr(domain, name)
    assert getattr(rest_coordinator, name) is getattr(domain, name)


def test_mqtt_transport_reexports_request_error() -> None:
    assert mqtt_transport.request_error is domain.request_error
    assert coordinator.request_error is domain.request_error


def test_domain_values_are_unchanged() -> None:
    """Moving the definitions changes no value (MQTT guardrails A1-A3)."""
    assert domain.SIGNAL_DEVICE_REMOVED == "opus_greennet_device_removed"
    assert domain.SIGNAL_DEVICE_DISCOVERED == "opus_greennet_device_discovered"
    assert domain.SIGNAL_DEVICE_STATE_UPDATE == "opus_greennet_device_state_update"
    assert domain.SIGNAL_AVAILABILITY_UPDATE == "opus_greennet_availability_update"
    assert domain.ACK_STATUS_DEFERRED == 201
    assert domain.SUPERSEDED == "superseded"
    assert domain.GATEWAY_HEALTH_INTERVAL == timedelta(seconds=60)
    assert domain.OUTBOUND_STATE_RECONCILIATION_DELAYS == (5, 20)
    assert [member.value for member in domain.CommandDelivery] == ["sent", "deferred"]
    movement = domain.CoverMovement(None, 100, started_at=10.0, travel=20.0)
    assert movement.ends_at == 35.0  # travel + 5 s grace
    assert (movement.cancel, movement.accepted, movement.elapsed) == (
        None,
        True,
        False,
    )


def test_request_error_keeps_its_translation_contract() -> None:
    error = domain.request_error("request_rejected", "DEV1", "status 404")
    assert isinstance(error, HomeAssistantError)
    assert error.translation_domain == DOMAIN
    assert error.translation_key == "request_rejected"
    assert error.translation_placeholders == {
        "device_id": "DEV1",
        "reason": "status 404",
    }


# ── Coordinator protocol (E3, K6) ────────────────────────────────────────────


def test_coordinator_protocol_lists_the_platform_interface() -> None:
    assert get_protocol_members(OpusCoordinator) == {
        "available",
        "devices",
        "eag_id",
        "gateway_info",
        "gateway_uptime",
        "get_device",
        "late_answer_diagnostics",
        "async_forget_device",
        "async_unload",
        "async_turn_on",
        "async_turn_off",
        "async_set_cover_position",
        "async_set_cover_tilt",
        "async_stop_cover",
        "async_set_climate_setpoint",
        "async_set_climate_mode",
        "async_get_device_configuration",
        "async_set_device_configuration",
        "async_get_device_parameters",
    }


@pytest.mark.parametrize("fixture", ["coordinator", "rest_coordinator"])
def test_both_coordinators_provide_the_protocol(
    fixture: str, request: pytest.FixtureRequest
) -> None:
    instance = request.getfixturevalue(fixture)
    missing = sorted(
        member
        for member in get_protocol_members(OpusCoordinator)
        if not hasattr(instance, member)
    )
    assert missing == []


# ── Lazy MQTT imports in setup and config flow (E4, K4) ──────────────────────


def test_package_resolves_the_mqtt_coordinator_on_demand() -> None:
    assert package.OpusGreenNetCoordinator is coordinator.OpusGreenNetCoordinator
    with pytest.raises(AttributeError, match="no_such_name"):
        _ = package.no_such_name


def test_config_flow_resolves_mqtt_on_demand() -> None:
    from homeassistant.components import mqtt

    assert config_flow.mqtt is mqtt
    with pytest.raises(AttributeError, match="no_such_name"):
        _ = config_flow.no_such_name


async def test_config_flow_probe_delegates_to_the_mqtt_transport(
    hass: HomeAssistant,
) -> None:
    """The flow's probe is the unchanged MQTT probe, imported on first use."""
    answer = {"systemUptimeResponse": {"uptime": "42"}}
    with patch(
        "custom_components.opus_greennet.mqtt_transport.async_probe_gateway",
        AsyncMock(return_value=answer),
    ) as probe:
        assert await config_flow.async_probe_gateway(hass, "AABB0011") == answer
    probe.assert_awaited_once_with(hass, "AABB0011")


# ── REST-only installation (K1, K5) ──────────────────────────────────────────


async def test_rest_entry_loads_controls_and_unloads_without_mqtt(
    hass_without_mqtt: HomeAssistant, gateway: FakeGateway
) -> None:
    hass = hass_without_mqtt
    gateway.devices = [
        device_object(SWITCH, "D2-01-01", states=[{"key": "switch", "value": "off"}])
    ]
    entry = await setup_rest_bridge(hass, gateway)
    assert entry.state is ConfigEntryState.LOADED
    assert "mqtt" not in hass.config.components

    entity_id = await wait_for_entity(hass, "switch", f"AABB0011_{SWITCH}")
    await gateway.push(telegram(SWITCH, [{"key": "switch", "value": "on"}]))
    await wait_until(lambda: hass.states.get(entity_id).state == "on")
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": entity_id}, blocking=True
    )
    assert gateway.requests_to("PUT", f"/devices/{SWITCH}/state") == [
        {"state": {"functions": [{"key": "switch", "value": "off"}]}}
    ]

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert "mqtt" not in hass.config.components


def test_manifest_loads_mqtt_after_but_does_not_require_it() -> None:
    """O-1: MQTT is set up first when configured, but is not a requirement."""
    assert "dependencies" not in MANIFEST
    assert MANIFEST["after_dependencies"] == ["mqtt"]
    # MQTT discovery stays declared exactly as before.
    assert MANIFEST["mqtt"] == [DISCOVERY_TOPIC_FILTER]


async def test_mqtt_discovery_registration_comes_from_the_manifest(
    hass_without_mqtt: HomeAssistant,
) -> None:
    """The MQTT integration subscribes discovery topics from the manifest key.

    Home Assistant reads the ``mqtt`` key independently of ``dependencies``,
    so moving MQTT to ``after_dependencies`` keeps discovery registered.
    """
    topics = await loader.async_get_mqtt(hass_without_mqtt)
    assert topics[DOMAIN] == [DISCOVERY_TOPIC_FILTER]


async def test_mqtt_entry_without_mqtt_integration_retries_setup(
    hass_without_mqtt: HomeAssistant,
) -> None:
    """Without the MQTT integration an MQTT entry waits, as before (no crash)."""
    hass = hass_without_mqtt
    entry = _mqtt_entry()
    await hass.config_entries.async_add(entry)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert "mqtt" not in hass.config.components


async def test_mqtt_flow_without_mqtt_integration_aborts(
    hass_without_mqtt: HomeAssistant,
) -> None:
    result = await start_user_flow(hass_without_mqtt, "mqtt_bridge")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "mqtt_not_configured"
    assert "mqtt" not in hass_without_mqtt.config.components
