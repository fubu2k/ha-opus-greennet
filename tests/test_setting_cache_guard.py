"""A device list must not bring back the value from before a confirmed write.

The device list carries the gateway's cached copy of the configuration. A write
over the radio is confirmed by reading the configuration back from the device,
but nothing shows that the gateway refreshes its cache by that. A list that
arrives afterwards (the 30-minute refresh) may therefore repeat the old value.
"""

from __future__ import annotations

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.opus_greennet.const import SETTING_CACHE_GUARD_SECONDS
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from tests.fake_gateway import device_object
from tests.test_coordinator_commands import (
    connected_coordinator as connected_coordinator,
)
from tests.test_coordinator_commands import radio as radio

BLIND = "D2-05-02"
SWITCH = "D2-01-01"


def _configuration(**values: object) -> dict:
    return {
        "parameters": [{"key": key, "value": value} for key, value in values.items()]
    }


def _list(eep: str = BLIND, **values: object) -> dict:
    """A device list as the periodic refresh fetches it."""
    return {
        "devices": [device_object("DEV1", eep, configuration=_configuration(**values))]
    }


def _document(**values: object) -> dict:
    return {"header": {"httpStatus": 200}, "configuration": _configuration(**values)}


def _refresh(coord, eep: str = BLIND, **values: object) -> None:
    coord._apply_device_list(_list(eep, **values), authoritative=False)


@pytest.fixture
def blind(connected_coordinator):
    """A blind whose holding time Home Assistant knows from the cached list."""
    device = connected_coordinator.devices["DEV1"] = EnOceanDevice(
        "DEV1", "Blind", [{"eep": BLIND}]
    )
    device.get_or_create_channel().max_holding_time = 12.0
    return device


async def _write_holding_time(coord, radio, value: float = 29) -> None:
    radio.response = _document(maxHoldingTime=float(value))
    await coord.async_set_device_parameter(
        "DEV1", "maxHoldingTime", value, step=1, minimum=0
    )


async def test_a_cached_list_does_not_undo_a_confirmed_write(
    radio, connected_coordinator, blind
) -> None:
    coord = connected_coordinator
    await _write_holding_time(coord, radio)
    assert blind.channels[0].max_holding_time == 29.0

    _refresh(coord, maxHoldingTime=12.0)

    assert blind.channels[0].max_holding_time == 29.0


async def test_the_old_value_stays_ignored_in_every_later_list(
    radio, connected_coordinator, blind
) -> None:
    coord = connected_coordinator
    await _write_holding_time(coord, radio)

    for _ in range(3):
        _refresh(coord, maxHoldingTime=12.0)

    assert blind.channels[0].max_holding_time == 29.0


async def test_a_list_with_the_written_value_ends_the_protection(
    radio, connected_coordinator, blind
) -> None:
    """The cache caught up: afterwards a change made elsewhere is taken over."""
    coord = connected_coordinator
    await _write_holding_time(coord, radio)

    _refresh(coord, maxHoldingTime=29.0)
    assert "DEV1" not in coord._setting_guards
    _refresh(coord, maxHoldingTime=12.0)

    assert blind.channels[0].max_holding_time == 12.0


async def test_a_third_value_is_a_change_made_elsewhere(
    radio, connected_coordinator, blind
) -> None:
    coord = connected_coordinator
    await _write_holding_time(coord, radio)

    _refresh(coord, maxHoldingTime=40.0)

    assert blind.channels[0].max_holding_time == 40.0
    assert "DEV1" not in coord._setting_guards


async def test_the_protection_ends_after_the_maximum_age(
    radio, connected_coordinator, blind
) -> None:
    coord = connected_coordinator
    await _write_holding_time(coord, radio)
    coord._setting_guards["DEV1"]["maxHoldingTime"].at -= (
        SETTING_CACHE_GUARD_SECONDS + 1
    )

    _refresh(coord, maxHoldingTime=12.0)

    assert blind.channels[0].max_holding_time == 12.0


async def test_other_settings_of_the_same_list_are_still_applied(
    radio, connected_coordinator, blind
) -> None:
    coord = connected_coordinator
    await _write_holding_time(coord, radio)

    _refresh(coord, maxHoldingTime=12.0, verticalMovementTime=25.51)

    assert blind.channels[0].max_holding_time == 29.0
    assert blind.channels[0].vertical_movement_time == 25.51


async def test_several_writes_in_a_row_keep_every_older_value_protected(
    radio, connected_coordinator, blind
) -> None:
    coord = connected_coordinator
    await _write_holding_time(coord, radio, 29)
    await _write_holding_time(coord, radio, 40)

    _refresh(coord, maxHoldingTime=12.0)
    assert blind.channels[0].max_holding_time == 40.0
    _refresh(coord, maxHoldingTime=29.0)
    assert blind.channels[0].max_holding_time == 40.0


async def test_writing_the_old_value_back_needs_no_protection(
    radio, connected_coordinator, blind
) -> None:
    coord = connected_coordinator
    await _write_holding_time(coord, radio, 29)
    await _write_holding_time(coord, radio, 12)

    # The only older value is 29; 12 is the written one and applies.
    _refresh(coord, maxHoldingTime=12.0)
    assert blind.channels[0].max_holding_time == 12.0


async def test_an_unchanged_write_sets_no_protection(
    radio, connected_coordinator, blind
) -> None:
    coord = connected_coordinator
    await _write_holding_time(coord, radio, 12)

    assert coord._setting_guards == {}


async def test_an_unknown_previous_value_sets_no_protection(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = EnOceanDevice("DEV1", "Blind", [{"eep": BLIND}])
    await _write_holding_time(coord, radio)

    assert device.channels[0].max_holding_time == 29.0
    assert coord._setting_guards == {}


async def test_an_unconfirmed_write_sets_no_protection(
    radio, connected_coordinator, blind
) -> None:
    coord = connected_coordinator
    radio.response = _document(maxHoldingTime=12.0)

    with pytest.raises(HomeAssistantError):
        await coord.async_set_device_parameter(
            "DEV1", "maxHoldingTime", 29, step=1, minimum=0
        )

    assert coord._setting_guards == {}
    _refresh(coord, maxHoldingTime=15.0)
    assert blind.channels[0].max_holding_time == 15.0


async def test_a_text_setting_is_protected_too(radio, connected_coordinator) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = EnOceanDevice("DEV1", "Switch", [{"eep": SWITCH}])
    device.get_or_create_channel().led_color = "off"
    radio.response = _document(ledColor="green")

    await coord.async_set_device_option("DEV1", "ledColor", "green")
    _refresh(coord, SWITCH, ledColor="off")

    assert device.channels[0].led_color == "green"

    _refresh(coord, SWITCH, ledColor="red")
    assert device.channels[0].led_color == "red"


async def test_no_rotation_counts_as_zero(radio, connected_coordinator) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = EnOceanDevice("DEV1", "Blind", [{"eep": BLIND}])
    device.get_or_create_channel().rotation_time = 0
    radio.response = _document(rotationTime=0.4)

    await coord.async_set_device_parameter(
        "DEV1", "rotationTime", 0.4, step=0.01, minimum=0
    )
    _refresh(coord, rotationTime="noRotation")

    assert device.channels[0].rotation_time == 0.4


async def test_an_authoritative_list_is_protected_as_well(
    radio, connected_coordinator, blind
) -> None:
    coord = connected_coordinator
    await _write_holding_time(coord, radio)

    coord._apply_device_list(_list(maxHoldingTime=12.0), authoritative=True)

    assert blind.channels[0].max_holding_time == 29.0


async def test_the_protection_is_per_device(
    radio, connected_coordinator, blind
) -> None:
    coord = connected_coordinator
    other = coord.devices["DEV2"] = EnOceanDevice("DEV2", "Blind 2", [{"eep": BLIND}])
    other.get_or_create_channel().max_holding_time = 12.0
    await _write_holding_time(coord, radio)

    coord._apply_device_list(
        {
            "devices": [
                device_object(
                    "DEV2", BLIND, configuration=_configuration(maxHoldingTime=15.0)
                )
            ]
        },
        authoritative=False,
    )

    assert other.channels[0].max_holding_time == 15.0
    assert blind.channels[0].max_holding_time == 29.0


async def test_forgetting_a_device_drops_its_protection(
    radio, connected_coordinator, blind
) -> None:
    coord = connected_coordinator
    await _write_holding_time(coord, radio)

    coord.async_forget_device("DEV1")

    assert coord._setting_guards == {}


def test_setting_reading_follows_the_channel_fields() -> None:
    device = EnOceanDevice("DEV1", "Blind", [{"eep": BLIND}])
    assert device.setting_reading("maxHoldingTime") is None
    channel = device.get_or_create_channel()
    channel.max_holding_time = 29.0
    channel.led_color = "green"
    channel.timer = 120.0
    assert device.setting_reading("maxHoldingTime") == 29.0
    assert device.setting_reading("ledColor") == "green"
    assert device.setting_reading("timer") == 120.0
    assert device.setting_reading("switch") is None
    assert device.setting_reading("rotationTime") is None
    channel.vertical_movement_time = 25.51
    assert device.setting_reading("verticalMovementTime") == 25.51
