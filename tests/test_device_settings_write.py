"""Writing device settings: grid, read-back, serialisation and stale lists."""

from __future__ import annotations

import asyncio
import math

import pytest
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er

from custom_components.opus_greennet.api import OpusTimeoutError
from custom_components.opus_greennet.coordinator import (
    SETTING_HOLD_SECONDS,
    snap_to_step,
)
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from custom_components.opus_greennet.number import DEVICE_TIMES
from tests.fake_gateway import FakeGateway, device_object
from tests.ha_helpers import setup_bridge, wait_for_entity
from tests.test_coordinator_commands import (
    connected_coordinator as connected_coordinator,
)
from tests.test_coordinator_commands import radio as radio

STEPS = {
    time.parameter: {"step": time.step, "minimum": time.minimum}
    for time in DEVICE_TIMES
}
CONFIG_PATH = "/devices/DEV1/configuration"


def _document(**values: object) -> dict:
    return {
        "header": {"httpStatus": 200},
        "configuration": {
            "parameters": [
                {"key": key, "value": value} for key, value in values.items()
            ]
        },
    }


def _switch() -> EnOceanDevice:
    return EnOceanDevice("DEV1", "Switch", [{"eep": "D2-01-01"}])


def _written(radio) -> list[object]:
    return [
        body["configuration"]["parameters"][0]["value"]
        for method, _, body in radio.sent
        if method == "PUT"
    ]


# ── grid ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "step", "minimum", "expected"),
    [
        (2.57, 0.1, 0, 2.6),
        (0.0500001, 0.01, 0, 0.05),
        (0.4567891, 0.001, 0.2, 0.457),
        (600.0, 1, 60, 600),
        (600.4, 1, 60, 600),
        (29.0, 1, 0, 29),
    ],
)
def test_values_are_snapped_to_the_parameter_grid(value, step, minimum, expected):
    snapped = snap_to_step(value, step, minimum)
    assert snapped == pytest.approx(expected)
    # Whole values of whole steps go out as integers.
    assert isinstance(snapped, int) is isinstance(expected, int)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
async def test_a_value_that_is_no_number_is_never_sent(
    radio, connected_coordinator, value
) -> None:
    connected_coordinator.devices["DEV1"] = _switch()
    with pytest.raises(ServiceValidationError) as error:
        await connected_coordinator.async_set_device_parameter(
            "DEV1", "timer", value, **STEPS["timer"]
        )
    assert error.value.translation_key == "invalid_parameter_value"
    assert radio.sent == []


async def test_every_configuration_write_is_checked_for_valid_json(
    radio, connected_coordinator
) -> None:
    with pytest.raises(ServiceValidationError) as error:
        await connected_coordinator.async_set_device_configuration(
            "DEV1", {"configuration": {"parameters": [{"key": "x", "value": math.nan}]}}
        )
    assert error.value.translation_key == "invalid_configuration"
    assert radio.sent == []


async def test_an_off_grid_value_is_written_as_the_device_stores_it(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = EnOceanDevice(
        "DEV1", "Dimmer", [{"eep": "D2-01-03"}]
    )
    radio.response = _document(rampingTime=2.6)
    await coord.async_set_device_parameter(
        "DEV1", "rampingTime", 2.57, **STEPS["rampingTime"]
    )
    assert _written(radio) == [2.6]
    assert device.channels[0].ramping_time == 2.6


# ── read-back ──────────────────────────────────────────────────────────────


async def test_a_failed_read_back_keeps_the_previous_value(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _switch()
    device.get_or_create_channel().timer = 300.0
    answers = iter([{"header": {"httpStatus": 200}}, OpusTimeoutError("read-back")])

    async def answer(method, path, body, timeout):
        radio.sent.append((method, path, body))
        result = next(answers)
        if isinstance(result, Exception):
            raise result
        return result

    radio._request = answer  # noqa: SLF001
    with pytest.raises(HomeAssistantError) as error:
        await coord.async_set_device_parameter("DEV1", "timer", 600, **STEPS["timer"])
    assert error.value.translation_key == "parameter_not_confirmed"
    assert device.channels[0].timer == 300.0
    assert [method for method, _, _ in radio.sent] == ["PUT", "GET"]


async def test_writes_to_one_device_do_not_interleave(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    coord.devices["DEV1"] = _switch()
    radio.response = _document(timer=600.0)
    await asyncio.gather(
        coord.async_set_device_parameter("DEV1", "timer", 600, **STEPS["timer"]),
        coord.async_set_device_parameter("DEV1", "timer", 600, **STEPS["timer"]),
    )
    assert [method for method, _, _ in radio.sent] == ["PUT", "GET", "PUT", "GET"]


# ── device lists after a write ─────────────────────────────────────────────


def _list_timer(coord, value: float) -> None:
    coord._apply_device_object(  # noqa: SLF001
        {
            "deviceId": "DEV1",
            "eeps": [{"eep": "D2-01-01"}],
            "configuration": {"parameters": [{"key": "timer", "value": value}]},
        },
        authoritative=False,
    )


async def test_a_list_with_the_previous_value_does_not_undo_a_write(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _switch()
    device.eeps = [{"eep": "D2-01-01"}]
    device.get_or_create_channel().timer = 300.0
    radio.response = _document(timer=600.0)
    await coord.async_set_device_parameter("DEV1", "timer", 600, **STEPS["timer"])

    # The gateway still lists the old value (it reads devices at night).
    _list_timer(coord, 300.0)
    assert device.channels[0].timer == 600.0
    # Once it lists the written value, later changes apply again.
    _list_timer(coord, 600.0)
    _list_timer(coord, 900.0)
    assert device.channels[0].timer == 900.0


async def test_the_hold_on_a_written_value_expires(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _switch()
    device.eeps = [{"eep": "D2-01-01"}]
    radio.response = _document(timer=600.0)
    await coord.async_set_device_parameter("DEV1", "timer", 600, **STEPS["timer"])
    value, confirmed_at = coord._confirmed_settings[("DEV1", "timer")]  # noqa: SLF001
    coord._confirmed_settings[("DEV1", "timer")] = (  # noqa: SLF001
        value,
        confirmed_at - SETTING_HOLD_SECONDS - 1,
    )
    _list_timer(coord, 300.0)
    assert device.channels[0].timer == 300.0


# ── end to end ─────────────────────────────────────────────────────────────


async def _enable(hass: HomeAssistant, entry, domain: str, unique_id: str) -> str:
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(domain, "opus_greennet", unique_id)
    assert entity_id is not None
    assert (
        registry.async_get(entity_id).disabled_by
        is er.RegistryEntryDisabler.INTEGRATION
    )
    registry.async_update_entity(entity_id, disabled_by=None)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    return entity_id


async def test_settings_are_written_and_read_back_through_the_gateway(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [
        device_object(
            "SW1",
            "D2-01-01",
            states=[{"key": "switch", "value": "off"}],
            configuration={
                "parameters": [
                    {"key": "timer", "value": 300.0},
                    {"key": "ledColor", "value": "off"},
                ]
            },
        )
    ]
    entry = await setup_bridge(hass, gateway)
    eag_id = gateway.eurid
    await wait_for_entity(hass, "switch", f"{eag_id}_SW1")
    timer = await _enable(hass, entry, "number", f"{eag_id}_SW1_timer")
    await gateway.wait_for_stream()
    await hass.async_block_till_done()
    led = await _enable(hass, entry, "select", f"{eag_id}_SW1_led_color")
    await gateway.wait_for_stream()
    await hass.async_block_till_done()
    assert float(hass.states.get(timer).state) == 300.0
    assert hass.states.get(led).state == "off"

    await hass.services.async_call(
        "number", "set_value", {"entity_id": timer, "value": 600}, blocking=True
    )
    await hass.services.async_call(
        "select", "select_option", {"entity_id": led, "option": "green"}, blocking=True
    )

    writes = [
        body["configuration"]["parameters"]
        for body in (b for _, _, b in gateway.requests if b)
        if "configuration" in body
    ]
    assert writes == [
        [{"key": "timer", "value": 600}],
        [{"key": "ledColor", "value": "green"}],
    ]
    assert float(hass.states.get(timer).state) == 600.0
    assert hass.states.get(led).state == "green"


async def test_a_setting_the_device_never_reported_is_unavailable(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [
        device_object("SW1", "D2-01-01", states=[{"key": "switch", "value": "off"}])
    ]
    entry = await setup_bridge(hass, gateway)
    eag_id = gateway.eurid
    await wait_for_entity(hass, "switch", f"{eag_id}_SW1")
    timer = await _enable(hass, entry, "number", f"{eag_id}_SW1_timer")
    led = await _enable(hass, entry, "select", f"{eag_id}_SW1_led_color")
    await gateway.wait_for_stream()
    await hass.async_block_till_done()
    assert hass.states.get(timer).state == STATE_UNAVAILABLE
    assert hass.states.get(led).state == STATE_UNAVAILABLE
