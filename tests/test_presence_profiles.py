"""Regression coverage for both OPUS SMS presence EEP variants."""

import pytest

from custom_components.opus_greennet.const import EEP_MAPPINGS
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from custom_components.opus_greennet.entity_descriptions import descriptions_for


@pytest.mark.parametrize("eep", ["A5-07-01", "A5-07-03"])
def test_sms_presence_profiles_share_entities_and_state(eep: str) -> None:
    """Both EEPs use the existing presence parser and entity descriptions."""
    device = EnOceanDevice(
        device_id="01234567", friendly_id="Presence", eeps=[{"eep": eep}]
    )
    assert device.primary_eep == eep
    assert EEP_MAPPINGS[eep] == EEP_MAPPINGS["A5-07-03"]
    assert device.entity_type == "binary_sensor"

    binary_descriptions = descriptions_for(device, "binary_sensor")
    sensor_descriptions = descriptions_for(device, "sensor")
    assert {description.key for description in binary_descriptions} == {"motion"}
    assert {description.key for description in sensor_descriptions} == {
        "signal_strength", "illumination", "supply_voltage", "battery_level"
    }

    device.battery_level = device.parse_battery_level("72%")
    device.update_from_telegram(
        {"functions": [
            {"key": "motionDetected", "value": "true"},
            {"key": "illumination", "value": "120"},
            {"key": "supplyVoltage", "value": "3.1"},
        ]}
    )
    values = {description.key: description.value_fn(device)
              for description in (*binary_descriptions, *sensor_descriptions)}
    assert values == {
        "motion": True,
        "signal_strength": None,
        "illumination": 120.0,
        "supply_voltage": 3.1,
        "battery_level": 72,
    }
