"""Regression inputs reported in issues #42–#44 for IQ-DOT firmware 1.21.30."""

import asyncio
import json

import pytest

from custom_components.opus_greennet.coordinator import OpusGreenNetCoordinator
from tests.ha_helpers import configure_bridge, wait_for_entity


@pytest.mark.parametrize("eep", ["A5-07-01", "A5-07-03"])
async def test_presence_reported_payloads_reach_entities(hass, mqtt_transport, eep):
    fields = {
        "motionDetected": True,
        "illumination": "200",
        "supplyVoltage": "3",
        "batteryLevel": "85%",
    }
    mqtt_transport.devices = [
        {"deviceId": "SMS", "eeps": [{"eep": eep}], "states": fields}
    ]
    await configure_bridge(hass)
    motion = await wait_for_entity(hass, "binary_sensor", "AABB0011_SMS_motion")
    illumination = await wait_for_entity(hass, "sensor", "AABB0011_SMS_illuminance")
    battery = await wait_for_entity(hass, "sensor", "AABB0011_SMS_battery_level")
    voltage = await wait_for_entity(hass, "sensor", "AABB0011_SMS_supply_voltage")
    assert hass.states.get(motion).state == "on"
    assert float(hass.states.get(illumination).state) == 200
    assert float(hass.states.get(battery).state) == 85
    assert float(hass.states.get(voltage).state) == 3
    # Indexed deltas resolve the original gateway keys, including value-first fragments.
    for index, (key, value) in enumerate(
        {
            "motionDetected": False,
            "illumination": "125",
            "batteryLevel": " 82.5 % ",
        }.items()
    ):
        for field, payload in (("value", value), ("key", key)):
            mqtt_transport.receive(
                f"EnOcean/AABB0011/stream/device/SMS/states/{index}/{field}",
                json.dumps(payload),
            )
    await asyncio.sleep(0.04)
    await hass.async_block_till_done()
    assert hass.states.get(motion).state == "off"
    assert float(hass.states.get(illumination).state) == 125
    assert float(hass.states.get(battery).state) == 82.5
    # Flat state maps are filtered by KNOWN_STATE_KEYS on the plural stream.
    mqtt_transport.receive(
        "EnOcean/AABB0011/stream/devices/SMS/states/motionDetected", "true"
    )
    mqtt_transport.receive(
        "EnOcean/AABB0011/stream/devices/SMS/states/illumination", "90"
    )
    # This message must use the scalar fast path, including JSON quotes and %.
    mqtt_transport.receive("EnOcean/AABB0011/stream/device/SMS/batteryLevel", '"79%"')
    await hass.async_block_till_done()
    assert hass.states.get(motion).state == "on"
    assert float(hass.states.get(illumination).state) == 90
    assert float(hass.states.get(battery).state) == 79
    mqtt_transport.receive(
        "EnOcean/AABB0011/stream/telegram/SMS/from",
        {
            "functions": [
                {"key": "motionDetected", "value": False},
                {"key": "illumination", "value": "notAvailable"},
            ]
        },
    )
    mqtt_transport.receive(
        "EnOcean/AABB0011/stream/device/SMS/batteryLevel", '"notAvailable"'
    )
    await hass.async_block_till_done()
    assert hass.states.get(motion).state == "off"
    assert hass.states.get(illumination).state == "unknown"
    assert hass.states.get(battery).state == "unknown"


@pytest.mark.parametrize("stream", ["device", "devices"])
async def test_rwm_original_alarm_key_and_boolean_battery(hass, mqtt_transport, stream):
    mqtt_transport.devices = [
        {
            "deviceId": "RWM",
            "eeps": [{"eep": "F6-05-02"}],
            # Explicit keys take precedence over the positional fallback.
            "transmitModes": [
                {"key": "batteryLow", "value": False},
                {"key": "alarm", "value": "off"},
            ],
        }
    ]
    await configure_bridge(hass)
    smoke = await wait_for_entity(hass, "binary_sensor", "AABB0011_RWM_smoke_alarm")
    battery = await wait_for_entity(hass, "binary_sensor", "AABB0011_RWM_battery_low")
    assert hass.states.get(smoke).state == "off"
    assert hass.states.get(battery).state == "off"
    for index, value in [(0, "true"), (1, "on")]:
        mqtt_transport.receive(
            f"EnOcean/AABB0011/stream/{stream}/RWM/transmitModes/{index}/value", value
        )
    await asyncio.sleep(0.04)
    await hass.async_block_till_done()
    assert hass.states.get(smoke).state == "on"
    assert hass.states.get(battery).state == "on"
    mqtt_transport.receive("EnOcean/AABB0011/stream/devices/RWM/states/alarm", "off")
    await hass.async_block_till_done()
    assert hass.states.get(smoke).state == "off"
    mqtt_transport.receive(
        "EnOcean/AABB0011/stream/telegram/RWM/from",
        {
            "functions": [
                {"key": "alarm", "value": "invalid"},
                {"key": "batteryLow", "value": "notAvailable"},
            ]
        },
    )
    await hass.async_block_till_done()
    assert hass.states.get(smoke).state == "unknown"
    assert hass.states.get(battery).state == "unknown"


@pytest.mark.parametrize("eep", ["D2-06-40", "F6-10-00", "D2-03-10"])
async def test_hoppe_original_keys_reach_position_entities(hass, mqtt_transport, eep):
    mqtt_transport.devices = [
        {
            "deviceId": "HANDLE",
            "eeps": [{"eep": eep}],
            "states": {
                "handle": "closed",
                "lock": "unlocked",
                "unlock": "notRequested",
                "mechanics": "ok",
            },
        }
    ]
    entry = (await configure_bridge(hass))["result"]
    position = await wait_for_entity(hass, "sensor", "AABB0011_HANDLE_handle_state")
    tilt = await wait_for_entity(hass, "binary_sensor", "AABB0011_HANDLE_handle_tilt")
    assert hass.states.get(position).state == "closed"
    if eep == "D2-06-40":
        request = await wait_for_entity(
            hass, "sensor", "AABB0011_HANDLE_unlock_request"
        )
        assert hass.states.get(request).state == "notRequested"
        assert hass.states.get(request).attributes["device_class"] == "enum"
    for index, (key, value) in enumerate(
        {"handle": "tilt", "unlock": "requested", "mechanics": "error"}.items()
    ):
        for field, payload in (("key", key), ("value", value)):
            mqtt_transport.receive(
                f"EnOcean/AABB0011/stream/device/HANDLE/states/{index}/{field}", payload
            )
    await asyncio.sleep(0.04)
    await hass.async_block_till_done()
    assert hass.states.get(position).state == "tilted"
    assert hass.states.get(tilt).state == "on"
    channel = entry.runtime_data.coordinator.devices["HANDLE"].channels[0]
    if eep == "D2-06-40":
        lock = await wait_for_entity(hass, "lock", "AABB0011_HANDLE_autolock")
        fault = await wait_for_entity(
            hass, "binary_sensor", "AABB0011_HANDLE_mechanics_fault"
        )
        assert hass.states.get(lock).state == "unlocked"
        assert channel.unlock_request is True
        assert hass.states.get(request).state == "requested"
        assert hass.states.get(fault).state == "on"
    else:
        assert channel.unlock_request is None
        assert channel.mechanics_fault is None
    mqtt_transport.receive(
        "EnOcean/AABB0011/stream/devices/HANDLE/states/handle", "open"
    )
    await hass.async_block_till_done()
    assert hass.states.get(position).state == "open"


def test_rwm_fallback_preserves_slots_and_explicit_keys():
    modes = [
        {"key": "", "value": "on"},
        {"key": None, "value": True},
        None,
        {"key": "other", "value": "on"},
    ]
    resolved = OpusGreenNetCoordinator._rwm_transmit_modes(modes)
    assert [
        entry.get("key") if isinstance(entry, dict) else entry for entry in resolved
    ] == ["smokeAlarm", "batteryLow", None, "other"]
    assert modes[0]["key"] == ""  # Source snapshot is not mutated.
    assert OpusGreenNetCoordinator._rwm_transmit_modes([{"key": "other"}]) == [
        {"key": "other"}
    ]


@pytest.mark.parametrize(
    "key,eep,attr",
    [
        ("alarm", "F6-05-02", "smoke_alarm"),
        ("batteryLow", "F6-05-02", "battery_low"),
        ("motionDetected", "A5-07-03", "motion"),
        ("unlock", "D2-06-40", "unlock_request"),
        ("mechanics", "D2-06-40", "mechanics_fault"),
    ],
)
@pytest.mark.parametrize("value", [None, 0, 1, [], {}, "unknown", "notAvailable"])
def test_unrecognized_boolean_values_stay_unknown(make_device, key, eep, attr, value):
    device = make_device(eep)
    device.update_from_telegram({"functions": [{"key": key, "value": value}]})
    assert getattr(device.channels[0], attr) is None


@pytest.mark.parametrize("value", ["0%", "100%", " 85.5 % "])
def test_battery_percentage_model(make_device, value):
    device = make_device("A5-07-03")
    device.update_from_telegram(
        {"functions": [{"key": "batteryLevel", "value": value}]}
    )
    assert device.channels[0].battery_level == float(
        value.strip().removesuffix("%").strip()
    )


@pytest.mark.parametrize("value", ["101%", "-1%", "nan%", "inf%", "85%%", True, "bad"])
def test_bad_battery_percentage_does_not_replace_valid_state(
    make_device, coordinator, value
):
    device = make_device("A5-07-03")
    coordinator.devices[device.device_id] = device
    device.get_or_create_channel().battery_level = 75
    device.update_from_telegram(
        {"functions": [{"key": "batteryLevel", "value": value}]}
    )
    assert device.channels[0].battery_level == 75
    assert coordinator._apply_scalar_telemetry(
        device.device_id, "batteryLevel", json.dumps(value), 1
    )
    assert device.channels[0].battery_level == 75
