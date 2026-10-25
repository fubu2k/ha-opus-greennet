"""Travel times of blinds as number entities."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.number import NumberDeviceClass, NumberMode
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.opus_greennet import protocol
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from custom_components.opus_greennet.number import (
    BLIND_TIMES,
    OpusGreenNetTimeNumber,
    async_setup_entry,
)
from tests.test_coordinator_commands import (
    connected_coordinator as connected_coordinator,
)
from tests.test_coordinator_commands import radio as radio

EAG_ID = "AABB0011"
CONFIG_PATH = "/devices/DEV1/configuration"


def _blind(eep: str = "D2-05-02") -> EnOceanDevice:
    return EnOceanDevice("DEV1", "Blind", [{"eep": eep}])


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


# ── protocol ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        (_document(verticalMovementTime=25.51), 25.51),
        (_document(verticalMovementTime="25.51"), 25.51),
        (_document(rotationTime=0), None),
        (_document(verticalMovementTime=-1), None),
        (_document(verticalMovementTime=True), None),
        (_document(verticalMovementTime="off"), None),
        ({"configuration": {"parameters": "x"}}, None),
        ({"configuration": {}}, None),
        ({"configuration": {"parameters": ["x", None]}}, None),
        ({}, None),
    ],
)
def test_configuration_parameter(document, expected) -> None:
    assert protocol.configuration_parameter(document, "verticalMovementTime") == (
        expected
    )


def test_configuration_parameter_reads_a_zero_value() -> None:
    document = _document(rotationTime=0.0)
    assert protocol.configuration_parameter(document, "rotationTime") == 0.0


# ── entities ───────────────────────────────────────────────────────────────


async def test_setup_creates_the_times_for_blinds_only(
    hass: HomeAssistant,
) -> None:
    coordinator = MagicMock()
    coordinator.devices = {
        "DEV1": _blind(),
        "SW": EnOceanDevice("SW", "Switch", [{"eep": "D2-01-01"}]),
    }
    added: list = []
    await async_setup_entry(
        hass, _entry(coordinator), lambda entities, *_a, **_k: added.extend(entities)
    )
    assert [entity.unique_id for entity in added] == [
        f"{EAG_ID}_DEV1_vertical_movement_time",
        f"{EAG_ID}_DEV1_rotation_time",
        f"{EAG_ID}_DEV1_max_holding_time",
    ]


async def test_max_holding_time_only_for_blinds_with_a_catalog(
    hass: HomeAssistant,
) -> None:
    """The parameter catalog was read from a D2-05-02 only."""
    coordinator = MagicMock()
    coordinator.devices = {"DEV1": _blind("D2-05-00")}
    added: list = []
    await async_setup_entry(
        hass, _entry(coordinator), lambda entities, *_a, **_k: added.extend(entities)
    )
    assert [entity.unique_id for entity in added] == [
        f"{EAG_ID}_DEV1_vertical_movement_time",
        f"{EAG_ID}_DEV1_rotation_time",
    ]


def test_number_metadata_follows_the_gateway_parameter_catalog() -> None:
    entity = OpusGreenNetTimeNumber(
        MagicMock(), EAG_ID, "gateway", _blind(), BLIND_TIMES[0]
    )
    assert entity.entity_category is EntityCategory.CONFIG
    assert entity.entity_registry_enabled_default is False
    assert entity.device_class is NumberDeviceClass.DURATION
    assert entity.mode is NumberMode.BOX
    assert (entity.native_min_value, entity.native_max_value) == (0, 655.35)
    assert entity.native_step == 0.01
    assert entity.native_unit_of_measurement == "s"


def test_max_holding_time_metadata_follows_the_gateway_parameter_catalog() -> None:
    entity = OpusGreenNetTimeNumber(
        MagicMock(), EAG_ID, "gateway", _blind(), BLIND_TIMES[2]
    )
    assert entity.unique_id == f"{EAG_ID}_DEV1_max_holding_time"
    assert entity.translation_key == "max_holding_time"
    assert entity.entity_category is EntityCategory.CONFIG
    assert entity.entity_registry_enabled_default is False
    assert entity.device_class is NumberDeviceClass.DURATION
    assert (entity.native_min_value, entity.native_max_value) == (0, 65535)
    assert entity.native_step == 1
    assert entity.native_unit_of_measurement == "s"
    # The travel times keep their own range.
    travel = OpusGreenNetTimeNumber(
        MagicMock(), EAG_ID, "gateway", _blind(), BLIND_TIMES[0]
    )
    assert (travel.native_max_value, travel.native_step) == (655.35, 0.01)


def test_value_follows_the_channel_fields() -> None:
    device = _blind()
    assert [time.parameter for time in BLIND_TIMES] == [
        "verticalMovementTime",
        "rotationTime",
        "maxHoldingTime",
    ]
    vertical, rotation, holding = (
        OpusGreenNetTimeNumber(MagicMock(), EAG_ID, "gateway", device, time)
        for time in BLIND_TIMES
    )
    assert vertical.native_value is None
    channel = device.get_or_create_channel()
    assert vertical.native_value is None
    assert holding.native_value is None
    channel.vertical_movement_time = 25.51
    channel.rotation_time = 0
    channel.max_holding_time = 29
    assert vertical.native_value == 25.51
    assert rotation.native_value == 0
    assert holding.native_value == 29


async def test_setting_a_value_writes_the_gateway_parameter() -> None:
    coordinator = MagicMock()
    coordinator.async_set_device_parameter = AsyncMock()
    entity = OpusGreenNetTimeNumber(
        coordinator, EAG_ID, "gateway", _blind(), BLIND_TIMES[1]
    )
    await entity.async_set_native_value(0.4)
    coordinator.async_set_device_parameter.assert_awaited_once_with(
        "DEV1", "rotationTime", 0.4
    )


# ── max holding time in the data model ─────────────────────────────────────


def test_max_holding_time_is_read_from_the_device_configuration() -> None:
    data = {
        "configuration": {
            "parameters": [
                {"key": "maxHoldingTime", "value": 29.0, "unit": "s"},
                {"key": "verticalMovementTime", "value": 25.42, "unit": "s"},
            ]
        }
    }
    functions = protocol.configuration_state_functions(data)
    assert {"channel": 0, "key": "maxHoldingTime", "value": 29.0, "unit": "s"} in (
        functions
    )
    # A refresh that is not authoritative still carries the setting.
    keys = {function["key"] for function in protocol.configuration_functions(data)}
    assert keys == {"maxHoldingTime", "verticalMovementTime"}
    device = _blind()
    device.update_from_telegram({"functions": functions})
    assert device.channels[0].max_holding_time == 29.0


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, 0.0), ("29.00", 29.0), (29, 29.0), (65535, 65535.0), ("12", 12.0)],
)
def test_max_holding_time_accepts_values_in_the_catalog_range(value, expected) -> None:
    device = _blind()
    device.update_from_telegram(
        {"functions": [{"key": "maxHoldingTime", "value": value}]}
    )
    assert device.channels[0].max_holding_time == expected


@pytest.mark.parametrize(
    "value",
    [None, True, "", "off", "noChange", -1, "-0.5", 65536, float("nan"), float("inf")],
)
def test_invalid_max_holding_time_keeps_the_known_value(value) -> None:
    device = _blind()
    device.get_or_create_channel().max_holding_time = 29.0
    device.update_from_telegram(
        {"functions": [{"key": "maxHoldingTime", "value": value}]}
    )
    assert device.channels[0].max_holding_time == 29.0


def test_a_foreign_command_never_sets_the_max_holding_time() -> None:
    """Units of a command of another controller are not known; only reports count."""
    command = [{"key": "maxHoldingTime", "value": 30}]
    assert protocol.outbound_state_functions(command) == []
    assert not protocol.has_target_state(protocol.outbound_state_functions(command))


# ── coordinator ────────────────────────────────────────────────────────────


async def test_max_holding_time_write_is_adopted_after_the_read_back(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _blind()
    channel = device.get_or_create_channel()
    channel.max_holding_time = 12.0
    radio.response = _document(maxHoldingTime=29.0)

    await coord.async_set_device_parameter("DEV1", "maxHoldingTime", 29)

    assert radio.sent == [
        (
            "PUT",
            CONFIG_PATH,
            {"configuration": {"parameters": [{"key": "maxHoldingTime", "value": 29}]}},
        ),
        ("GET", CONFIG_PATH, None),
    ]
    assert channel.max_holding_time == 29.0


async def test_unconfirmed_max_holding_time_keeps_the_previous_one(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _blind()
    channel = device.get_or_create_channel()
    channel.max_holding_time = 12.0
    radio.response = _document(maxHoldingTime=12.0)

    with pytest.raises(HomeAssistantError) as error:
        await coord.async_set_device_parameter("DEV1", "maxHoldingTime", 29)

    assert error.value.translation_key == "parameter_not_confirmed"
    assert channel.max_holding_time == 12.0
    assert [method for method, _, _ in radio.sent] == ["PUT", "GET"]


async def test_write_is_adopted_only_after_the_read_back(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _blind()
    channel = device.get_or_create_channel()
    channel.vertical_movement_time = 25.51
    radio.response = _document(verticalMovementTime=30.5)

    await coord.async_set_device_parameter("DEV1", "verticalMovementTime", 30.5)

    assert radio.sent == [
        (
            "PUT",
            CONFIG_PATH,
            {
                "configuration": {
                    "parameters": [{"key": "verticalMovementTime", "value": 30.5}]
                }
            },
        ),
        ("GET", CONFIG_PATH, None),
    ]
    assert channel.vertical_movement_time == 30.5


async def test_value_is_rounded_to_the_device_resolution(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _blind()
    radio.response = _document(rotationTime=0.41)
    await coord.async_set_device_parameter("DEV1", "rotationTime", 0.4099999)
    assert radio.sent[0][2]["configuration"]["parameters"][0]["value"] == 0.41
    assert device.channels[0].rotation_time == 0.41


@pytest.mark.parametrize(
    "stored", [_document(verticalMovementTime=25.51), _document(), {"header": {}}]
)
async def test_unconfirmed_value_keeps_the_previous_one(
    radio, connected_coordinator, stored
) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _blind()
    channel = device.get_or_create_channel()
    channel.vertical_movement_time = 25.51
    radio.response = stored | {"header": {"httpStatus": 200}}

    with pytest.raises(HomeAssistantError) as error:
        await coord.async_set_device_parameter("DEV1", "verticalMovementTime", 30.5)

    assert error.value.translation_key == "parameter_not_confirmed"
    assert channel.vertical_movement_time == 25.51
    # Neither the write nor the read-back is repeated.
    assert [method for method, _, _ in radio.sent] == ["PUT", "GET"]


async def test_rejected_write_is_not_read_back(radio, connected_coordinator) -> None:
    coord = connected_coordinator
    coord.devices["DEV1"] = _blind()
    radio.response = {"header": {"httpStatus": 400}}
    with pytest.raises(HomeAssistantError):
        await coord.async_set_device_parameter("DEV1", "verticalMovementTime", 30.5)
    assert [method for method, _, _ in radio.sent] == ["PUT"]


async def test_unknown_device_sends_nothing(radio, connected_coordinator) -> None:
    with pytest.raises(HomeAssistantError) as error:
        await connected_coordinator.async_set_device_parameter(
            "GONE", "verticalMovementTime", 30.5
        )
    assert error.value.translation_key == "device_not_found"
    assert radio.sent == []
