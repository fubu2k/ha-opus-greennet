"""Signal quality rated from the signal strength of a device."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import EntityCategory

from custom_components.opus_greennet.enocean_device import EnOceanDevice
from custom_components.opus_greennet.sensor import OpusGreenNetSignalQualitySensor


def _sensor(dbm: int | None) -> OpusGreenNetSignalQualitySensor:
    device = EnOceanDevice("DEV1", "Device", [{"eep": "D2-01-01"}], dbm=dbm)
    return OpusGreenNetSignalQualitySensor(MagicMock(), "AABB0011", "gw", device)


@pytest.mark.parametrize(
    ("dbm", "quality"),
    [
        (-40, "good"),
        (-74, "good"),
        (-75, "sufficient"),
        (-80, "sufficient"),
        (-85, "sufficient"),
        (-86, "poor"),
        (-95, "poor"),
        (None, None),
    ],
)
def test_quality_follows_the_signal_strength(dbm, quality) -> None:
    assert _sensor(dbm).native_value == quality


def test_quality_sensor_metadata() -> None:
    sensor = _sensor(-60)
    assert sensor.unique_id == "AABB0011_DEV1_signal_quality"
    assert sensor.translation_key == "signal_quality"
    assert sensor.device_class is SensorDeviceClass.ENUM
    assert sensor.options == ["good", "sufficient", "poor"]
    assert sensor.entity_category is EntityCategory.DIAGNOSTIC
    assert sensor.entity_registry_enabled_default is True
