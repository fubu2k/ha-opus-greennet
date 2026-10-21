"""Phase 6 metadata: SMS supply voltage diagnostic, whole-percent battery level.

The SMS presence detector supply voltage is a diagnostic entity that stays
enabled. Every OPUS battery level sensor suggests a display precision of 0.
Both are registry metadata only: existing entries keep their enablement, entity
ID and customisations, the reported state is not rounded, and nothing sends a
device request.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.components.sensor import SensorDeviceClass, async_rounded_state
from homeassistant.const import (
    LIGHT_LUX,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfRatio,
)
from homeassistant.helpers import entity_registry as er

from custom_components.opus_greennet.const import DOMAIN
from custom_components.opus_greennet.sensor import OpusGreenNetMeasurementSensor
from tests.fake_gateway import FakeGateway
from tests.ha_helpers import setup_bridge, wait_for_entity

SENSOR_OPTIONS = "sensor"
SMS_PREFIX = "AABB0011_SMS"
OPUS_MOTION_EEPS = [
    {"eep": "F6-02-01", "version": 0.9, "direction": "from"},
    {"eep": "A5-04-01", "version": 0.9, "direction": "from"},
]


def _device_requests(gateway: FakeGateway, device_id: str, start: int = 0):
    """Return requests addressed to the device itself (potential radio traffic)."""
    return [
        path
        for _method, path, _body in gateway.requests[start:]
        if path.startswith(f"/devices/{device_id}")
        # The profile read at setup is gateway data, not radio traffic.
        and not path.endswith("/profile")
    ]


def _sms(eep: str = "A5-07-03") -> dict:
    return {
        "deviceId": "SMS",
        "eeps": [{"eep": eep}],
        "batteryLevel": "87.5",
        "states": [
            {"key": "illuminance", "value": "200"},
            {"key": "supplyVoltage", "value": "3.2"},
        ],
    }


def _measurement(make_device, eep: str, suffix: str, **kwargs):
    device_class, unit = {
        "illuminance": (SensorDeviceClass.ILLUMINANCE, LIGHT_LUX),
        "supply_voltage": (SensorDeviceClass.VOLTAGE, UnitOfElectricPotential.VOLT),
        "battery_level": (SensorDeviceClass.BATTERY, UnitOfRatio.PERCENTAGE),
    }[suffix]
    return OpusGreenNetMeasurementSensor(
        MagicMock(),
        "AABB0011",
        "gw",
        make_device(eep, device_id="SMS"),
        suffix,
        device_class,
        unit,
        **kwargs,
    )


def test_measurement_metadata_is_scoped(make_device) -> None:
    voltage = _measurement(
        make_device,
        "A5-07-03",
        "supply_voltage",
        entity_category=EntityCategory.DIAGNOSTIC,
    )
    illuminance = _measurement(make_device, "A5-07-03", "illuminance")
    battery = _measurement(make_device, "A5-07-03", "battery_level")
    ths_battery = _measurement(make_device, "A5-04-01", "battery_level")

    # Supply voltage is a diagnostic that stays enabled for new entries.
    assert voltage.entity_category is EntityCategory.DIAGNOSTIC
    assert voltage.entity_registry_enabled_default is True
    assert voltage.unique_id == f"{SMS_PREFIX}_supply_voltage"
    assert voltage.device_class is SensorDeviceClass.VOLTAGE
    assert voltage.suggested_display_precision is None
    # Every battery level suggests whole percent; nothing else changes.
    for sensor in (battery, ths_battery):
        assert sensor.suggested_display_precision == 0
        assert sensor.entity_category is None
        assert sensor.entity_registry_enabled_default is True
        assert sensor.device_class is SensorDeviceClass.BATTERY
    assert illuminance.entity_category is None
    assert illuminance.suggested_display_precision is None


@pytest.mark.parametrize("eep", ["A5-07-01", "A5-07-03"])
async def test_new_sms_registers_voltage_diagnostic_and_whole_battery(
    hass, gateway, eep
):
    gateway.devices = [_sms(eep)]
    await setup_bridge(hass, gateway)
    registry = er.async_get(hass)

    voltage = await wait_for_entity(hass, "sensor", f"{SMS_PREFIX}_supply_voltage")
    battery = await wait_for_entity(hass, "sensor", f"{SMS_PREFIX}_battery_level")
    motion = await wait_for_entity(hass, "binary_sensor", f"{SMS_PREFIX}_motion")
    normal = [motion]
    if eep == "A5-07-03":
        # Only A5-07-03 reports illumination (EEP profile).
        normal.append(
            await wait_for_entity(hass, "sensor", f"{SMS_PREFIX}_illuminance")
        )
    else:
        assert (
            er.async_get(hass).async_get_entity_id(
                "sensor", DOMAIN, f"{SMS_PREFIX}_illuminance"
            )
            is None
        )

    voltage_entry = registry.async_get(voltage)
    assert voltage_entry.entity_category is EntityCategory.DIAGNOSTIC
    assert voltage_entry.disabled_by is None
    assert float(hass.states.get(voltage).state) == 3.2
    assert hass.states.get(voltage).attributes["unit_of_measurement"] == "V"

    battery_entry = registry.async_get(battery)
    assert battery_entry.entity_category is None
    assert battery_entry.options[SENSOR_OPTIONS]["suggested_display_precision"] == 0
    # Display only: the reported value is not rounded in the state machine.
    battery_state = hass.states.get(battery)
    assert float(battery_state.state) == 87.5
    assert async_rounded_state(hass, battery, battery_state) == "88"

    # Normal motion entities keep their metadata.
    for entity_id in normal:
        entry = registry.async_get(entity_id)
        assert entry.entity_category is None
        assert entry.disabled_by is None

    assert _device_requests(gateway, "SMS") == []


@pytest.mark.parametrize(
    ("device", "unique_id"),
    [
        pytest.param(
            {
                "deviceId": "THS1",
                "eeps": [{"eep": "A5-04-03"}],
                "batteryLevel": "90%",
            },
            "AABB0011_THS1_battery_level",
            id="temperature_humidity",
        ),
        pytest.param(
            {
                "deviceId": "MOT1",
                "eeps": [dict(eep) for eep in OPUS_MOTION_EEPS],
                "batteryLevel": "58%",
            },
            "AABB0011_MOT1_battery_level",
            id="opus_motion_detector",
        ),
    ],
)
async def test_every_battery_level_suggests_whole_percent(
    hass, gateway, device, unique_id
):
    gateway.devices = [device]
    await setup_bridge(hass, gateway)
    registry = er.async_get(hass)

    battery = await wait_for_entity(hass, "sensor", unique_id)
    entry = registry.async_get(battery)
    assert entry.options[SENSOR_OPTIONS]["suggested_display_precision"] == 0
    assert entry.entity_category is None
    assert entry.disabled_by is None
    assert _device_requests(gateway, device["deviceId"]) == []


@pytest.mark.parametrize("variant", ["enabled", "user_disabled"])
async def test_existing_sms_entries_keep_user_settings(hass, gateway, variant):
    entry = await setup_bridge(hass, gateway)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    disabled_by = er.RegistryEntryDisabler.USER if variant == "user_disabled" else None

    voltage = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{SMS_PREFIX}_supply_voltage",
        config_entry=entry,
        suggested_object_id="kept_supply_voltage",
        disabled_by=disabled_by,
    )
    registry.async_update_entity(voltage.entity_id, name="My voltage", icon="mdi:star")
    battery = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{SMS_PREFIX}_battery_level",
        config_entry=entry,
        suggested_object_id="kept_battery_level",
    )
    # A precision chosen by the user overrides the suggested whole percent.
    registry.async_update_entity_options(
        battery.entity_id, SENSOR_OPTIONS, {"display_precision": 1}
    )

    start = len(gateway.requests)
    gateway.devices = [_sms()]
    await gateway.push(gateway.devices_document())
    # Wait for an entity of the same discovery batch before inspecting entries.
    await wait_for_entity(hass, "sensor", f"{SMS_PREFIX}_illuminance")

    current = registry.async_get(voltage.entity_id)
    assert (
        registry.async_get_entity_id("sensor", DOMAIN, f"{SMS_PREFIX}_supply_voltage")
        == voltage.entity_id
    )
    assert current.disabled_by == disabled_by
    assert current.name == "My voltage"
    assert current.icon == "mdi:star"
    assert current.entity_category is EntityCategory.DIAGNOSTIC
    assert [
        e for e in registry.entities.values() if e.unique_id == current.unique_id
    ] == [current]

    battery_entry = registry.async_get(battery.entity_id)
    assert battery_entry.options[SENSOR_OPTIONS]["display_precision"] == 1
    assert battery_entry.options[SENSOR_OPTIONS]["suggested_display_precision"] == 0
    battery_state = hass.states.get(battery.entity_id)
    assert battery_state is not None
    assert async_rounded_state(hass, battery.entity_id, battery_state) == "87.5"

    if variant == "enabled":
        assert float(hass.states.get(voltage.entity_id).state) == 3.2
    else:
        assert hass.states.get(voltage.entity_id) is None

    assert _device_requests(gateway, "SMS", start) == []
