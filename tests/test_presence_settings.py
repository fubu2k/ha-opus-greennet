"""Settings of the A5-07-03 presence detectors, shown read only.

The gateway lists three configuration parameters of the detector in its
cached device list: the alive cycle (minutes), the time until the PIR reports
"off" and the cycle of the ReCom readiness (both seconds). They are shown as
diagnostic sensors and read again on request. Nothing in the integration
writes them.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import EntityCategory

from custom_components.opus_greennet.button import (
    OpusGreenNetReadSettingsButton,
    has_settings,
)
from custom_components.opus_greennet.const import DEVICE_SETTINGS
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from custom_components.opus_greennet.number import (
    DEVICE_TIMES,
    PRESENCE_SETTINGS,
)
from custom_components.opus_greennet.sensor import OpusGreenNetSettingSensor
from tests.fake_gateway import device_object
from tests.test_coordinator_commands import (
    connected_coordinator as connected_coordinator,
)
from tests.test_coordinator_commands import radio as radio
from tests.test_setting_sensors import EAG_ID, _created

DETECTOR = "A5-07-03"
CONFIG_PATH = "/devices/DEV1/configuration"
# What the gateway's cache holds for a detector (values, units and key names as
# read from a gateway; the descriptions are left out here).
CACHED = {"aliveCycle": 1.0, "pirOffTime": 60.0, "recomReadyTime": 14400.0}


def _configuration(**values: object) -> dict:
    return {
        "parameters": [{"key": key, "value": value} for key, value in values.items()]
    }


def _list(eep: str = DETECTOR, **values: object) -> dict:
    return {
        "devices": [device_object("DEV1", eep, configuration=_configuration(**values))]
    }


def _document(**values: object) -> dict:
    return {"header": {"httpStatus": 200}, "configuration": _configuration(**values)}


def _detector() -> EnOceanDevice:
    return EnOceanDevice("DEV1", "Detector", [{"eep": DETECTOR}])


@pytest.fixture
def detector(connected_coordinator):
    return connected_coordinator.devices.setdefault("DEV1", _detector())


# ── what the cached device list shows ──────────────────────────────────────


def test_the_cached_list_shows_the_three_settings(connected_coordinator) -> None:
    connected_coordinator._apply_device_list(_list(**CACHED), authoritative=False)

    channel = connected_coordinator.devices["DEV1"].channels[0]
    assert channel.alive_cycle == 1.0
    assert channel.pir_off_time == 60.0
    assert channel.recom_ready_time == 14400.0


def test_a_missing_setting_keeps_its_value(connected_coordinator, detector) -> None:
    coord = connected_coordinator
    coord._apply_device_list(_list(**CACHED), authoritative=False)

    coord._apply_device_list(_list(pirOffTime=30.0), authoritative=False)

    channel = detector.channels[0]
    assert channel.pir_off_time == 30.0
    # A list without a key is no deletion.
    assert channel.alive_cycle == 1.0
    assert channel.recom_ready_time == 14400.0


@pytest.mark.parametrize(
    ("key", "attribute", "bad"),
    [
        ("aliveCycle", "alive_cycle", 1441.0),
        ("pirOffTime", "pir_off_time", 3601.0),
        ("recomReadyTime", "recom_ready_time", 655351.0),
        ("pirOffTime", "pir_off_time", -1.0),
        ("pirOffTime", "pir_off_time", "soon"),
    ],
)
def test_a_value_outside_the_catalog_keeps_the_known_one(
    connected_coordinator, detector, key, attribute, bad
) -> None:
    coord = connected_coordinator
    coord._apply_device_list(_list(**CACHED), authoritative=False)

    coord._apply_device_list(_list(**{key: bad}), authoritative=False)

    assert getattr(detector.channels[0], attribute) == CACHED[key]


def test_the_settings_are_no_state(connected_coordinator, detector) -> None:
    """Reading a setting is no state update: it must not look like feedback."""
    coord = connected_coordinator
    coord._apply_device_list(_list(**CACHED), authoritative=False)
    revision = detector.channels[0].state_revision

    coord._apply_device_list(_list(pirOffTime=120.0), authoritative=False)

    assert detector.channels[0].pir_off_time == 120.0
    assert detector.channels[0].state_revision == revision


def test_a_detector_telegram_never_changes_a_setting(detector) -> None:
    detector.update_from_telegram(
        {"functions": [{"key": "motionDetected", "value": "true"}]}
    )

    channel = detector.channels[0]
    assert (channel.alive_cycle, channel.pir_off_time, channel.recom_ready_time) == (
        None,
        None,
        None,
    )


# ── the sensors ────────────────────────────────────────────────────────────


async def test_a_detector_gets_three_read_only_sensors() -> None:
    created = await _created(DETECTOR)

    assert {entity.unique_id for entity in created} == {
        f"{EAG_ID}_DEV1_alive_cycle_setting",
        f"{EAG_ID}_DEV1_pir_off_time_setting",
        f"{EAG_ID}_DEV1_recom_ready_time_setting",
    }
    for entity in created:
        assert entity.entity_registry_enabled_default is True
        assert entity.entity_category is EntityCategory.DIAGNOSTIC
        assert not hasattr(entity, "async_set_native_value")


async def test_the_older_presence_detector_gets_none() -> None:
    """Nothing is known about the settings of an A5-07-01."""
    assert await _created("A5-07-01") == []


def test_the_sensors_show_the_value_in_the_unit_of_the_gateway() -> None:
    device = _detector()
    channel = device.get_or_create_channel()
    channel.alive_cycle = 1
    channel.pir_off_time = 60
    channel.recom_ready_time = 14400
    sensors = {
        setting.parameter: OpusGreenNetSettingSensor(
            MagicMock(), EAG_ID, "gateway", device, setting
        )
        for setting in PRESENCE_SETTINGS
    }

    assert {
        key: (sensor.native_value, sensor.native_unit_of_measurement)
        for key, sensor in sensors.items()
    } == {
        "aliveCycle": (1, "min"),
        "pirOffTime": (60, "s"),
        "recomReadyTime": (14400, "s"),
    }
    assert all(s.device_class is SensorDeviceClass.DURATION for s in sensors.values())


def test_a_sensor_without_a_known_value_is_unknown() -> None:
    sensor = OpusGreenNetSettingSensor(
        MagicMock(), EAG_ID, "gateway", _detector(), PRESENCE_SETTINGS[0]
    )

    assert sensor.native_value is None


def test_the_ranges_are_the_ones_of_the_data_model() -> None:
    for setting in PRESENCE_SETTINGS:
        attribute, minimum, maximum = DEVICE_SETTINGS[setting.parameter]
        assert (setting.attribute, setting.minimum, setting.maximum) == (
            attribute,
            minimum,
            maximum,
        )


# ── nothing writes them ────────────────────────────────────────────────────


def test_no_number_entity_exists_for_a_detector() -> None:
    device = _detector()

    assert not set(PRESENCE_SETTINGS) & set(DEVICE_TIMES)
    assert not any(time.applies_to(device) for time in DEVICE_TIMES)


# ── reading them on request ────────────────────────────────────────────────


def test_the_detector_has_the_read_settings_button_enabled_by_default() -> None:
    device = _detector()
    button = OpusGreenNetReadSettingsButton(MagicMock(), EAG_ID, "gateway", device)

    assert has_settings(device) is True
    assert button.entity_registry_enabled_default is True
    assert button.unique_id == f"{EAG_ID}_DEV1_read_settings"


async def test_one_read_shows_what_the_detector_reports(
    radio, connected_coordinator, detector
) -> None:
    coord = connected_coordinator
    coord._apply_device_list(_list(**CACHED), authoritative=False)
    radio.response = _document(pirOffTime=120.0, recomReadyTime=3600.0)

    await coord.async_reread_device_settings("DEV1")

    assert radio.sent == [("GET", CONFIG_PATH, None)]
    channel = detector.channels[0]
    assert channel.pir_off_time == 120.0
    assert channel.recom_ready_time == 3600.0
    # What the detector does not report keeps its value.
    assert channel.alive_cycle == 1.0
