"""Routing (off/on), repeater mode of D2-01 switches and whole-number display."""

from __future__ import annotations

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import UnitOfTime
from homeassistant.exceptions import HomeAssistantError

from custom_components.opus_greennet import protocol
from custom_components.opus_greennet.const import ROUTING_ON_SECONDS
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from custom_components.opus_greennet.number import DEVICE_TIMES
from custom_components.opus_greennet.sensor import OpusGreenNetSettingSensor
from tests.test_coordinator_commands import (
    connected_coordinator as connected_coordinator,
)
from tests.test_coordinator_commands import radio as radio

CONFIG_PATH = "/devices/DEV1/configuration"
REPEATER_PATH = "/devices/DEV1/repeaterState"


def _switch() -> EnOceanDevice:
    return EnOceanDevice("DEV1", "Switch", [{"eep": "D2-01-01"}])


def _document(**values: object) -> dict:
    return {
        "header": {"httpStatus": 200},
        "configuration": {
            "parameters": [{"key": k, "value": v} for k, v in values.items()]
        },
    }


# ── protocol and model ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("off", "off"),
        (0.3, "on"),
        (2.55, "on"),
        ("1.5", "on"),
        (0, None),
        (-1, None),
        ("on", "on"),
        (None, None),
        (True, None),
    ],
)
def test_routing_is_reported_as_off_or_on(value, expected) -> None:
    assert EnOceanDevice.routing_reading(value) == expected
    assert protocol.configuration_routing(_document(routingTime=value)) == expected


def test_a_reported_routing_time_is_stored_as_on() -> None:
    device = _switch()
    device.update_from_telegram(
        {"functions": [{"channel": 0, "key": "routingTime", "value": 0.5}]}
    )
    assert device.channels[0].routing == "on"
    # An unusable value keeps the known one.
    device.update_from_telegram(
        {"functions": [{"channel": 0, "key": "routingTime", "value": "x"}]}
    )
    assert device.channels[0].routing == "on"


def test_the_configuration_read_normalises_routing() -> None:
    functions = protocol.configuration_state_functions(
        _document(routingTime=0.3, timer=300)
    )
    assert {"channel": 0, "key": "routingTime", "value": "on"} in functions


def test_repeater_state_function() -> None:
    assert protocol.repeater_state_function({"repeaterState": "level_2"}) == {
        "channel": 0,
        "key": "repeaterState",
        "value": "level_2",
    }
    assert protocol.repeater_state_function({"repeaterState": ""}) is None
    assert protocol.repeater_state_function({}) is None


# ── writing routing ────────────────────────────────────────────────────────


async def test_routing_on_is_written_as_the_smallest_time_and_read_back(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _switch()
    device.get_or_create_channel().routing = "off"
    radio.response = _document(routingTime=ROUTING_ON_SECONDS)

    await coord.async_set_device_option("DEV1", "routingTime", "on")

    assert radio.sent == [
        (
            "PUT",
            CONFIG_PATH,
            {
                "configuration": {
                    "parameters": [{"key": "routingTime", "value": ROUTING_ON_SECONDS}]
                }
            },
        ),
        ("GET", CONFIG_PATH, None),
    ]
    assert device.channels[0].routing == "on"


async def test_routing_off_is_written_as_text(radio, connected_coordinator) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _switch()
    device.get_or_create_channel().routing = "on"
    radio.response = _document(routingTime="off")

    await coord.async_set_device_option("DEV1", "routingTime", "off")

    assert radio.sent[0][2]["configuration"]["parameters"] == [
        {"key": "routingTime", "value": "off"}
    ]
    assert device.channels[0].routing == "off"


async def test_routing_already_in_the_wanted_state_sends_nothing(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _switch()
    device.get_or_create_channel().routing = "off"

    await coord.async_set_device_option("DEV1", "routingTime", "off")

    assert radio.sent == []


async def test_unconfirmed_routing_keeps_the_previous_state(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _switch()
    device.get_or_create_channel().routing = "off"
    radio.response = _document(routingTime="off")

    with pytest.raises(HomeAssistantError) as error:
        await coord.async_set_device_option("DEV1", "routingTime", "on")

    assert error.value.translation_key == "parameter_not_confirmed"
    assert device.channels[0].routing == "off"
    assert [method for method, _, _ in radio.sent] == ["PUT", "GET"]


# ── repeater mode ──────────────────────────────────────────────────────────


async def test_repeater_mode_is_written_and_read_back(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _switch()
    device.get_or_create_channel().repeater = "off"
    radio.response = {"header": {"httpStatus": 200}, "repeaterState": "level_2"}

    await coord.async_set_device_option("DEV1", "repeaterState", "level_2")

    assert radio.sent == [
        ("PUT", REPEATER_PATH, {"repeaterState": "level_2"}),
        ("GET", REPEATER_PATH, None),
    ]
    assert device.channels[0].repeater == "level_2"


async def test_unconfirmed_repeater_mode_is_not_adopted_or_repeated(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _switch()
    device.get_or_create_channel().repeater = "off"
    radio.response = {"header": {"httpStatus": 200}, "repeaterState": "off"}

    with pytest.raises(HomeAssistantError) as error:
        await coord.async_set_device_option("DEV1", "repeaterState", "level_1")

    assert error.value.translation_key == "parameter_not_confirmed"
    assert device.channels[0].repeater == "off"
    assert [method for method, _, _ in radio.sent] == ["PUT", "GET"]


async def test_rejected_repeater_write_is_not_read_back(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    coord.devices["DEV1"] = _switch()
    radio.response = {"header": {"httpStatus": 400}}

    with pytest.raises(HomeAssistantError) as error:
        await coord.async_set_device_option("DEV1", "repeaterState", "level_1")

    assert error.value.translation_key == "parameter_write_failed"
    assert [method for method, _, _ in radio.sent] == ["PUT"]


async def test_read_settings_also_reads_routing_and_repeater_mode(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _switch()
    device.get_or_create_channel()
    responses = {
        CONFIG_PATH: _document(routingTime="off", timer=300),
        REPEATER_PATH: {"header": {"httpStatus": 200}, "repeaterState": "level_2"},
    }
    original = radio._request

    async def routed(method, path, body, timeout):
        radio.response = responses[path]
        return await original(method, path, body, timeout)

    radio._request = routed

    await coord.async_reread_device_settings("DEV1")

    assert [(m, p) for m, p, _ in radio.sent] == [
        ("GET", CONFIG_PATH),
        ("GET", REPEATER_PATH),
    ]
    channel = device.channels[0]
    assert (channel.routing, channel.repeater, channel.timer) == (
        "off",
        "level_2",
        300,
    )


async def test_a_failing_repeater_read_keeps_the_other_settings(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _switch()
    device.get_or_create_channel().repeater = "level_1"
    original = radio._request

    async def routed(method, path, body, timeout):
        radio.response = (
            {"header": {"httpStatus": 500}}
            if path == REPEATER_PATH
            else _document(timer=300)
        )
        return await original(method, path, body, timeout)

    radio._request = routed

    await coord.async_reread_device_settings("DEV1")

    assert device.channels[0].timer == 300
    assert device.channels[0].repeater == "level_1"


# ── display ────────────────────────────────────────────────────────────────


def _sensor(translation_key: str) -> OpusGreenNetSettingSensor:
    from unittest.mock import MagicMock

    time = next(t for t in DEVICE_TIMES if t.translation_key == translation_key)
    return OpusGreenNetSettingSensor(
        MagicMock(), "AABB0011", "gateway", _switch(), time
    )


@pytest.mark.parametrize(
    "key",
    ["timer", "timer_long", "switch_off_warning", "illumination_threshold"],
)
def test_switch_setting_sensors_show_whole_numbers(key: str) -> None:
    assert _sensor(key).suggested_display_precision == 0


def test_the_warning_signal_is_shown_in_whole_milliseconds() -> None:
    sensor = _sensor("switch_off_warning_signal")
    assert sensor.device_class is SensorDeviceClass.DURATION
    assert sensor.native_unit_of_measurement == UnitOfTime.SECONDS
    assert sensor.suggested_unit_of_measurement == UnitOfTime.MILLISECONDS
    assert sensor.suggested_display_precision == 0


def test_blind_setting_sensors_keep_their_decimals() -> None:
    time = next(
        t for t in DEVICE_TIMES if t.translation_key == "vertical_movement_time"
    )
    from unittest.mock import MagicMock

    blind = EnOceanDevice("DEV1", "Blind", [{"eep": "D2-05-02"}])
    sensor = OpusGreenNetSettingSensor(MagicMock(), "AABB0011", "gateway", blind, time)
    assert sensor.suggested_display_precision is None
