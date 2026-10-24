"""Detector profiles (RWM smoke, presence) end to end over the REST stream."""

from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.opus_greennet import protocol
from custom_components.opus_greennet.const import DOMAIN
from tests.fake_gateway import FakeGateway, device_object, telegram
from tests.ha_helpers import setup_bridge, wait_for_entity, wait_until

RWM_EEP = "F6-05-02"


async def _refresh_device_list(entry: ConfigEntry) -> None:
    """Run the periodic (non-authoritative) device list refresh once."""
    coordinator = entry.runtime_data.coordinator
    coordinator._async_refresh_tick(dt_util.utcnow())  # noqa: SLF001
    assert coordinator._refresh_task is not None  # noqa: SLF001
    await coordinator._refresh_task  # noqa: SLF001


def _rwm(smoke: object, battery: object) -> dict:
    """Return the RWM as the gateway lists it: value-only transmitModes slots."""
    return device_object(
        "RWM1",
        RWM_EEP,
        friendly_id="Smoke detector",
        transmitModes=[{"value": smoke}, {"value": battery}],
    )


@pytest.mark.parametrize("source", ["telegram", "device list"])
async def test_rwm_discovery_and_live_value_only_updates(
    hass: HomeAssistant, gateway: FakeGateway, source: str
) -> None:
    gateway.devices = [_rwm("idle", "ok")]
    await setup_bridge(hass, gateway)
    smoke = await wait_for_entity(hass, "binary_sensor", "AABB0011_RWM1_smoke_alarm")
    battery = await wait_for_entity(hass, "binary_sensor", "AABB0011_RWM1_battery_low")
    assert hass.states.get(smoke).state == "off"
    assert hass.states.get(battery).state == "off"

    async def report(smoke_value: str, battery_value: str) -> None:
        if source == "telegram":
            await gateway.push(
                telegram(
                    "RWM1",
                    [
                        {"key": "smokeAlarm", "value": smoke_value},
                        {"key": "batteryLow", "value": battery_value},
                    ],
                )
            )
        else:
            # A new stream starts with the complete (authoritative) device list.
            gateway.devices = [_rwm(smoke_value, battery_value)]
            await gateway.close_streams()

    await report("alarm", "low")
    await wait_until(
        lambda: (
            (hass.states.get(smoke).state, hass.states.get(battery).state)
            == ("on", "on")
        )
    )
    await report("notAvailable", "low")
    await wait_until(lambda: hass.states.get(smoke).state == "unknown")
    assert hass.states.get(battery).state == "on"
    await gateway.stop()
    await wait_until(lambda: hass.states.get(battery).state == "unavailable")
    registry = er.async_get(hass)
    assert (
        len(
            [
                e
                for e in registry.entities.values()
                if e.platform == DOMAIN and e.unique_id.endswith("smoke_alarm")
            ]
        )
        == 1
    )


def test_rwm_fallback_is_profile_scoped():
    data = {"transmitModes": [{"value": "alarm"}, {"value": "low"}]}
    assert protocol.device_state_functions(data) == []
    data["eeps"] = [{"eep": RWM_EEP}]
    assert protocol.device_state_functions(data) == [
        {"key": "smokeAlarm", "value": "alarm"},
        {"key": "batteryLow", "value": "low"},
    ]


@pytest.mark.parametrize(
    "value,expected",
    [("alarm", True), ("idle", False), (None, None), ("garbage", None)],
)
def test_smoke_states_are_explicit(make_device, value, expected):
    device = make_device("F6-05-02")
    device.update_from_telegram({"functions": [{"key": "smokeAlarm", "value": value}]})
    assert device.channels[0].smoke_alarm is expected


@pytest.mark.parametrize("eep", ["A5-07-01", "A5-07-03"])
async def test_presence_states_and_telemetry(
    hass: HomeAssistant, gateway: FakeGateway, eep: str
) -> None:
    gateway.devices = [device_object("SMS", eep)]
    entry = await setup_bridge(hass, gateway)
    motion = await wait_for_entity(hass, "binary_sensor", "AABB0011_SMS_motion")
    values = {}
    # Only A5-07-03 reports illumination (EEP profile); A5-07-01 has no sensor.
    readings = ("illuminance",) if eep == "A5-07-03" else ()
    for suffix in (*readings, "supply_voltage", "battery_level"):
        values[suffix] = await wait_for_entity(hass, "sensor", f"AABB0011_SMS_{suffix}")
        assert hass.states.get(values[suffix]).state == "unknown"
    assert hass.states.get(motion).state == "unknown"

    await gateway.push(
        telegram(
            "SMS",
            [
                {"key": "motionDetector", "value": "detected"},
                {"key": "illuminance", "value": "200"},
                {"key": "supplyVoltage", "value": "3.2"},
            ],
        )
    )
    await wait_until(lambda: hass.states.get(motion).state == "on")
    # The battery level arrives with the periodic device list.
    gateway.devices = [device_object("SMS", eep, batteryLevel="85%")]
    await _refresh_device_list(entry)
    await hass.async_block_till_done()
    for suffix, value, unit in [
        ("illuminance", 200, "lx"),
        ("supply_voltage", 3.2, "V"),
        ("battery_level", 85, "%"),
    ]:
        if suffix not in values:
            continue
        state = hass.states.get(values[suffix])
        assert float(state.state) == value
        assert state.attributes["unit_of_measurement"] == unit

    await gateway.push(
        telegram("SMS", [{"key": "motionDetector", "value": "noMotion"}])
    )
    gateway.devices = [device_object("SMS", eep, batteryLevel="82")]
    await _refresh_device_list(entry)
    await wait_until(lambda: hass.states.get(motion).state == "off")
    assert float(hass.states.get(values["battery_level"]).state) == 82
    # The metadata refresh keeps the live readings from the stream.
    assert hass.states.get(motion).state == "off"
    if "illuminance" in values:
        assert float(hass.states.get(values["illuminance"]).state) == 200


@pytest.mark.parametrize(
    "key,attr",
    [
        ("illuminance", "illuminance"),
        ("supplyVoltage", "supply_voltage"),
        ("batteryLevel", "battery_level"),
    ],
)
@pytest.mark.parametrize("value", ["nan", "inf", "-1", True, [], "bad"])
def test_presence_invalid_measurements_are_unknown(make_device, key, attr, value):
    device = make_device("A5-07-03")
    device.update_from_telegram({"functions": [{"key": key, "value": value}]})
    assert getattr(device.channels[0], attr) is None
