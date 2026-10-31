"""Settings combinations the OPUS config tool warns about.

The tool warns when a blind's maximum holding time is shorter than its drive
time, when the drive time is shorter than the rotation time, when the cleaning
mode time of a switch is shorter than its run-on time and when the switch-off
warning is longer than the run-on time. The integration only logs a warning
after a confirmed write; nothing is blocked, repeated or changed.
"""

from __future__ import annotations

import logging

import pytest

from custom_components.opus_greennet.enocean_device import EnOceanDevice
from tests.test_coordinator_commands import (
    connected_coordinator as connected_coordinator,
)
from tests.test_coordinator_commands import radio as radio

BLIND = "D2-05-02"
SWITCH = "D2-01-01"
LOGGER = "custom_components.opus_greennet.coordinator"


def _blind(
    *, holding: float | None, travel: float | None, rotation: float | None
) -> EnOceanDevice:
    device = EnOceanDevice("DEV1", "Blind", [{"eep": BLIND}])
    channel = device.get_or_create_channel()
    channel.max_holding_time = holding
    channel.vertical_movement_time = travel
    channel.rotation_time = rotation
    return device


def _switch(**values: float | None) -> EnOceanDevice:
    device = EnOceanDevice("DEV1", "Switch", [{"eep": SWITCH}])
    channel = device.get_or_create_channel()
    for name, value in values.items():
        setattr(channel, name, value)
    return device


@pytest.mark.parametrize(
    ("holding", "travel", "rotation", "expected"),
    [
        (29, 25.5, 0, []),
        (29, 25.5, 1.2, []),
        (25, 25.5, 0, ["holding_time_shorter_than_travel_time"]),
        (25.5, 25.5, 0, []),  # equal is fine
        (40, 25.5, 30, ["travel_time_shorter_than_rotation_time"]),
        (
            20,
            25.5,
            30,
            [
                "holding_time_shorter_than_travel_time",
                "travel_time_shorter_than_rotation_time",
            ],
        ),
        (None, 25.5, 30, ["travel_time_shorter_than_rotation_time"]),
        (29, None, 1, []),  # an unknown value is never compared
        (None, None, None, []),
    ],
)
def test_blind_conflicts(holding, travel, rotation, expected) -> None:
    device = _blind(holding=holding, travel=travel, rotation=rotation)
    assert device.setting_conflicts() == expected


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({"timer": 300, "timer_long": 3600, "switch_off_warning": 30}, []),
        (
            {"timer": 300, "timer_long": 120, "switch_off_warning": 30},
            ["cleaning_time_shorter_than_run_on_time"],
        ),
        (
            {"timer": 300, "timer_long": 3600, "switch_off_warning": 400},
            ["switch_off_warning_longer_than_run_on_time"],
        ),
        ({"timer": 300, "timer_long": 3600, "switch_off_warning": 0}, []),
        ({"timer": None, "timer_long": 3600, "switch_off_warning": 30}, []),
    ],
)
def test_switch_conflicts(values, expected) -> None:
    assert _switch(**values).setting_conflicts() == expected


def test_a_key_limits_the_result_to_its_own_combinations() -> None:
    device = _blind(holding=20, travel=25.5, rotation=30)
    assert device.setting_conflicts("maxHoldingTime") == [
        "holding_time_shorter_than_travel_time"
    ]
    assert device.setting_conflicts("rotationTime") == [
        "travel_time_shorter_than_rotation_time"
    ]
    assert device.setting_conflicts("verticalMovementTime") == [
        "holding_time_shorter_than_travel_time",
        "travel_time_shorter_than_rotation_time",
    ]
    assert device.setting_conflicts("ledColor") == []


def _document(**values: object) -> dict:
    return {
        "header": {"httpStatus": 200},
        "configuration": {
            "parameters": [
                {"key": key, "value": value} for key, value in values.items()
            ]
        },
    }


@pytest.fixture
def blind(connected_coordinator):
    device = connected_coordinator.devices["DEV1"] = _blind(
        holding=29, travel=25.51, rotation=0
    )
    return device


async def test_a_confirmed_write_that_conflicts_is_kept_and_logged(
    radio, connected_coordinator, blind, caplog
) -> None:
    radio.response = _document(maxHoldingTime=20.0)

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        await connected_coordinator.async_set_device_parameter(
            "DEV1", "maxHoldingTime", 20, step=1, minimum=0
        )

    assert blind.channels[0].max_holding_time == 20.0
    assert [r.getMessage() for r in caplog.records] == [
        "Device DEV1: after writing maxHoldingTime the maximum holding time is "
        "shorter than the vertical movement time (the OPUS config tool warns "
        "about this); the value was kept"
    ]


async def test_a_write_without_a_conflict_logs_nothing(
    radio, connected_coordinator, blind, caplog
) -> None:
    radio.response = _document(maxHoldingTime=33.0)

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        await connected_coordinator.async_set_device_parameter(
            "DEV1", "maxHoldingTime", 33, step=1, minimum=0
        )

    assert caplog.records == []


async def test_a_write_does_not_report_a_conflict_it_did_not_cause(
    radio, connected_coordinator, blind, caplog
) -> None:
    """A combination that was already wrong stays quiet when another one is written."""
    blind.channels[0].max_holding_time = 20.0  # already shorter than the travel time
    radio.response = _document(rotationTime=0.0)

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        await connected_coordinator.async_set_device_parameter(
            "DEV1", "rotationTime", 0, step=0.01, minimum=0
        )

    assert caplog.records == []
