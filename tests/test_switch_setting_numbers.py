"""Timers, switch-off warning and illumination threshold of D2-01 switches."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.number import NumberDeviceClass, NumberMode
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.opus_greennet import protocol
from custom_components.opus_greennet.const import (
    DEFAULT_PARAMETER_DECIMALS,
    DEVICE_SETTINGS,
    PARAMETER_DECIMALS,
)
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from custom_components.opus_greennet.number import (
    DEVICE_TIMES,
    SWITCH_SETTINGS,
    OpusGreenNetTimeNumber,
    async_setup_entry,
)
from tests.test_coordinator_commands import (
    connected_coordinator as connected_coordinator,
)
from tests.test_coordinator_commands import radio as radio

EAG_ID = "AABB0011"
CONFIG_PATH = "/devices/DEV1/configuration"
FIXTURES = Path(__file__).parent / "fixtures"

TIMER, TIMER_LONG, WARNING, SIGNAL, THRESHOLD = SWITCH_SETTINGS
KEYS = ("timer", "timerLong", "switchOffWarning", "switchOffWarningSignal")
KEYS += ("illuminationThreshold",)
TRANSLATION_KEYS = [
    "timer",
    "timer_long",
    "switch_off_warning",
    "switch_off_warning_signal",
    "illumination_threshold",
]


def _switch(eep: str = "D2-01-01", device_id: str = "DEV1") -> EnOceanDevice:
    return EnOceanDevice(device_id, "Switch", [{"eep": eep}])


def _document(**values: object) -> dict:
    return {
        "header": {"httpStatus": 200},
        "configuration": {
            "parameters": [
                {"key": key, "value": value} for key, value in values.items()
            ]
        },
    }


def _entry(coordinator) -> SimpleNamespace:
    return SimpleNamespace(
        data={"eag_id": EAG_ID},
        runtime_data=SimpleNamespace(
            coordinator=coordinator, gateway_device_id="gateway"
        ),
        async_on_unload=MagicMock(),
    )


async def _setup(hass: HomeAssistant, devices: dict) -> list:
    coordinator = MagicMock()
    coordinator.devices = devices
    added: list = []
    await async_setup_entry(
        hass, _entry(coordinator), lambda entities, *_a, **_k: added.extend(entities)
    )
    return added


# ── entities ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("eep", ["D2-01-01", "D2-01-11"])
async def test_setup_creates_one_set_per_catalog_switch(
    hass: HomeAssistant, eep: str
) -> None:
    """The settings belong to the device, also for 2 channels."""
    added = await _setup(hass, {"DEV1": _switch(eep)})
    assert [entity.unique_id for entity in added] == [
        f"{EAG_ID}_DEV1_{key}" for key in TRANSLATION_KEYS
    ]


async def test_no_switch_settings_for_other_devices(hass: HomeAssistant) -> None:
    """The parameter catalog was read from a D2-01-01 and a D2-01-11 only."""
    added = await _setup(hass, {"OTHER": _switch("D2-01-06", "OTHER")})
    assert added == []


async def test_dimmers_and_blinds_get_no_switch_settings(hass: HomeAssistant) -> None:
    added = await _setup(
        hass,
        {
            "DIM": EnOceanDevice("DIM", "Dimmer", [{"eep": "D2-01-03"}]),
            "BLIND": EnOceanDevice("BLIND", "Blind", [{"eep": "D2-05-02"}]),
        },
    )
    assert [entity.unique_id for entity in added] == [
        f"{EAG_ID}_DIM_ramping_time",
        f"{EAG_ID}_DIM_short_press_time",
        f"{EAG_ID}_DIM_long_press_time",
        f"{EAG_ID}_BLIND_vertical_movement_time",
        f"{EAG_ID}_BLIND_rotation_time",
        f"{EAG_ID}_BLIND_max_holding_time",
    ]


@pytest.mark.parametrize(
    ("setting", "key", "minimum", "maximum", "step", "unit", "device_class"),
    [
        (TIMER, "timer", 60, 7200, 1, "s", NumberDeviceClass.DURATION),
        (TIMER_LONG, "timer_long", 60, 18000, 1, "s", NumberDeviceClass.DURATION),
        (WARNING, "switch_off_warning", 0, 45, 1, "s", NumberDeviceClass.DURATION),
        (
            SIGNAL,
            "switch_off_warning_signal",
            0.2,
            2,
            0.001,
            "s",
            NumberDeviceClass.DURATION,
        ),
        (
            THRESHOLD,
            "illumination_threshold",
            0,
            1500,
            1,
            "lx",
            NumberDeviceClass.ILLUMINANCE,
        ),
    ],
)
def test_number_metadata_follows_the_gateway_parameter_catalog(
    setting, key, minimum, maximum, step, unit, device_class
) -> None:
    entity = OpusGreenNetTimeNumber(MagicMock(), EAG_ID, "gateway", _switch(), setting)
    assert entity.unique_id == f"{EAG_ID}_DEV1_{key}"
    assert entity.translation_key == key
    assert entity.entity_category is EntityCategory.CONFIG
    assert entity.entity_registry_enabled_default is False
    assert entity.device_class is device_class
    assert entity.mode is NumberMode.BOX
    assert (entity.native_min_value, entity.native_max_value) == (minimum, maximum)
    assert entity.native_step == step
    assert entity.native_unit_of_measurement == unit


def test_value_follows_the_channel_fields() -> None:
    device = _switch()
    numbers = [
        OpusGreenNetTimeNumber(MagicMock(), EAG_ID, "gateway", device, setting)
        for setting in SWITCH_SETTINGS
    ]
    assert [number.native_value for number in numbers] == [None] * 5
    channel = device.get_or_create_channel()
    assert [number.native_value for number in numbers] == [None] * 5
    channel.timer = 300.0
    channel.timer_long = 3600.0
    channel.switch_off_warning = 30.0
    channel.switch_off_warning_signal = 0.5
    channel.illumination_threshold = 80.0
    assert [number.native_value for number in numbers] == [300, 3600, 30, 0.5, 80]


@pytest.mark.parametrize(("setting", "key"), list(zip(SWITCH_SETTINGS, KEYS)))
async def test_setting_a_value_writes_the_gateway_parameter(setting, key) -> None:
    coordinator = MagicMock()
    coordinator.async_set_device_parameter = AsyncMock()
    entity = OpusGreenNetTimeNumber(coordinator, EAG_ID, "gateway", _switch(), setting)
    await entity.async_set_native_value(120)
    coordinator.async_set_device_parameter.assert_awaited_once_with("DEV1", key, 120)


def test_every_setting_has_its_model_range() -> None:
    """The entity limits and the limits of the data model are the same."""
    for setting in DEVICE_TIMES:
        if setting.parameter not in DEVICE_SETTINGS:
            continue
        attribute, minimum, maximum = DEVICE_SETTINGS[setting.parameter]
        assert (setting.attribute, setting.minimum, setting.maximum) == (
            attribute,
            minimum,
            maximum,
        )


def test_parameter_decimals_keep_every_step() -> None:
    """A value on the step must survive the rounding before the write."""
    for setting in DEVICE_TIMES:
        decimals = PARAMETER_DECIMALS.get(setting.parameter, DEFAULT_PARAMETER_DECIMALS)
        assert round(setting.step, decimals) == setting.step


# ── data model ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "eep"), [("d2_01_01", "D2-01-01"), ("d2_01_11", "D2-01-11")]
)
def test_settings_are_read_from_a_real_device_configuration(name, eep) -> None:
    data = json.loads((FIXTURES / f"configuration_{name}.json").read_text("utf8"))
    functions = protocol.configuration_state_functions(data)
    assert [(f["key"], f["value"]) for f in functions] == [
        ("timer", 300.0),
        ("timerLong", 3600.0),
        ("switchOffWarning", 30.0),
        ("switchOffWarningSignal", 0.5),
        ("illuminationThreshold", 80.0),
    ]
    # A refresh that is not authoritative carries the settings, nothing else.
    keys = {function["key"] for function in protocol.configuration_functions(data)}
    assert keys == set(KEYS)
    device = _switch(eep)
    device.update_from_telegram({"functions": functions})
    assert list(device.channels) == [0]
    channel = device.channels[0]
    assert (
        channel.timer,
        channel.timer_long,
        channel.switch_off_warning,
        channel.switch_off_warning_signal,
        channel.illumination_threshold,
    ) == (300.0, 3600.0, 30.0, 0.5, 80.0)


@pytest.mark.parametrize(
    ("key", "attribute", "value", "expected"),
    [
        ("timer", "timer", 60, 60.0),
        ("timer", "timer", "7200", 7200.0),
        ("timerLong", "timer_long", 18000, 18000.0),
        ("switchOffWarning", "switch_off_warning", 0, 0.0),
        ("switchOffWarning", "switch_off_warning", 45, 45.0),
        ("switchOffWarningSignal", "switch_off_warning_signal", 0.2, 0.2),
        ("switchOffWarningSignal", "switch_off_warning_signal", "2.0", 2.0),
        ("illuminationThreshold", "illumination_threshold", 0, 0.0),
        ("illuminationThreshold", "illumination_threshold", 1500, 1500.0),
    ],
)
def test_values_in_the_catalog_range_are_accepted(
    key, attribute, value, expected
) -> None:
    device = _switch()
    device.update_from_telegram({"functions": [{"key": key, "value": value}]})
    assert getattr(device.channels[0], attribute) == expected


@pytest.mark.parametrize(
    ("key", "attribute", "invalid"),
    [
        ("timer", "timer", 59),
        ("timer", "timer", 0),
        ("timer", "timer", 7201),
        ("timerLong", "timer_long", 18001),
        ("timerLong", "timer_long", 59.9),
        ("switchOffWarning", "switch_off_warning", 46),
        ("switchOffWarning", "switch_off_warning", -1),
        ("switchOffWarningSignal", "switch_off_warning_signal", 0.19),
        ("switchOffWarningSignal", "switch_off_warning_signal", 2.01),
        ("illuminationThreshold", "illumination_threshold", 1501),
        ("illuminationThreshold", "illumination_threshold", -1),
        ("timer", "timer", None),
        ("timer", "timer", True),
        ("timerLong", "timer_long", ""),
        ("switchOffWarning", "switch_off_warning", "off"),
        ("illuminationThreshold", "illumination_threshold", "noChange"),
        ("timer", "timer", float("nan")),
        ("switchOffWarningSignal", "switch_off_warning_signal", float("inf")),
    ],
)
def test_invalid_values_keep_the_known_one(key, attribute, invalid) -> None:
    device = _switch()
    setattr(device.get_or_create_channel(), attribute, 100.0)
    device.update_from_telegram({"functions": [{"key": key, "value": invalid}]})
    assert getattr(device.channels[0], attribute) == 100.0


@pytest.mark.parametrize("key", KEYS)
def test_a_foreign_command_never_sets_a_switch_setting(key) -> None:
    """Units of a command of another controller are not known; only reports count."""
    command = [{"key": key, "value": 120}]
    assert protocol.outbound_state_functions(command) == []
    assert not protocol.has_target_state(command)


def test_reading_a_setting_does_not_cancel_a_command_estimate() -> None:
    """A stored setting is no feedback; a switch report is."""
    device = _switch()
    device.get_or_create_channel()
    snapshot = device.snapshot_channel_state(0)

    device.update_from_telegram(
        {"functions": [{"key": key, "value": 100} for key in KEYS]}
    )
    assert device.channel_state_unchanged(snapshot)

    device.update_from_telegram({"functions": [{"key": "switch", "value": "on"}]})
    assert not device.channel_state_unchanged(snapshot)


# ── coordinator ────────────────────────────────────────────────────────────


async def test_warning_signal_keeps_three_decimals(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _switch()
    channel = device.get_or_create_channel()
    channel.switch_off_warning_signal = 0.5
    radio.response = _document(switchOffWarningSignal=0.457)

    await coord.async_set_device_parameter("DEV1", "switchOffWarningSignal", 0.4567891)

    assert radio.sent == [
        (
            "PUT",
            CONFIG_PATH,
            {
                "configuration": {
                    "parameters": [{"key": "switchOffWarningSignal", "value": 0.457}]
                }
            },
        ),
        ("GET", CONFIG_PATH, None),
    ]
    assert channel.switch_off_warning_signal == 0.457


async def test_a_read_back_one_step_off_is_not_confirmed(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _switch()
    channel = device.get_or_create_channel()
    channel.switch_off_warning_signal = 0.5
    radio.response = _document(switchOffWarningSignal=0.458)

    with pytest.raises(HomeAssistantError) as error:
        await coord.async_set_device_parameter("DEV1", "switchOffWarningSignal", 0.457)

    assert error.value.translation_key == "parameter_not_confirmed"
    assert channel.switch_off_warning_signal == 0.5
    assert [method for method, _, _ in radio.sent] == ["PUT", "GET"]


async def test_timer_write_is_adopted_after_the_read_back(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _switch("D2-01-11")
    channel = device.get_or_create_channel()
    channel.timer = 300.0
    radio.response = _document(timer=600.0)

    await coord.async_set_device_parameter("DEV1", "timer", 600)

    assert radio.sent[0] == (
        "PUT",
        CONFIG_PATH,
        {"configuration": {"parameters": [{"key": "timer", "value": 600}]}},
    )
    assert channel.timer == 600.0


async def test_unconfirmed_timer_keeps_the_previous_one(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _switch()
    channel = device.get_or_create_channel()
    channel.timer = 300.0
    radio.response = _document(timer=300.0)

    with pytest.raises(HomeAssistantError) as error:
        await coord.async_set_device_parameter("DEV1", "timer", 600)

    assert error.value.translation_key == "parameter_not_confirmed"
    assert channel.timer == 300.0
    assert [method for method, _, _ in radio.sent] == ["PUT", "GET"]
