"""Battery level and signal strength (dbm) telemetry over the REST transport.

The gateway does not stream battery levels; they come from the device list
(``"batteryLevel": "96%"``), at setup, at the start of every stream and with
the periodic refresh. Signal strength arrives with every telegram
(``telegramInfo.dbm``) and with the device list (``dbm``).
"""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_STATE_CHANGED, EVENT_STATE_REPORTED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from tests.fake_gateway import FakeGateway, device_object, telegram
from tests.ha_helpers import setup_bridge, wait_for_entity, wait_until

DEVICE = "SMS1"
EEP = "A5-07-03"
BATTERY = f"AABB0011_{DEVICE}_battery_level"
SIGNAL = f"AABB0011_{DEVICE}_signal_strength"


def _sensor(**fields: Any) -> dict[str, Any]:
    return device_object(DEVICE, EEP, **fields)


async def _refresh_device_list(entry: ConfigEntry) -> None:
    """Run the periodic (non-authoritative) device list refresh once."""
    coordinator = entry.runtime_data.coordinator
    coordinator._async_refresh_tick(dt_util.utcnow())  # noqa: SLF001
    assert coordinator._refresh_task is not None  # noqa: SLF001
    await coordinator._refresh_task  # noqa: SLF001


async def _setup_with_signal_strength(
    hass: HomeAssistant, gateway: FakeGateway
) -> tuple[ConfigEntry, str]:
    """Set up the gateway with the (disabled by default) signal sensor enabled."""
    entry = await setup_bridge(hass, gateway)
    entity_id = await wait_for_entity(hass, "sensor", SIGNAL)
    registry = er.async_get(hass)
    assert registry.async_get(entity_id).disabled_by is not None
    registry.async_update_entity(entity_id, disabled_by=None)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await wait_until(lambda: entry.runtime_data.coordinator._stream_connected)  # noqa: SLF001
    await hass.async_block_till_done()
    await wait_until(lambda: hass.states.get(entity_id) is not None)
    return entry, entity_id


def _record_writes(hass: HomeAssistant, entity_ids: list[str]) -> list[str]:
    """Record every state write (changed or merely reported) of some entities."""
    writes: list[str] = []

    @callback
    def _filter(event_data: Any) -> bool:
        return event_data["entity_id"] in entity_ids

    @callback
    def _record(event: Event) -> None:
        writes.append(event.data["entity_id"])

    for event_type in (EVENT_STATE_CHANGED, EVENT_STATE_REPORTED):
        hass.bus.async_listen(event_type, _record, event_filter=_filter)
    return writes


def _number(hass: HomeAssistant, entity_id: str) -> float | None:
    state = hass.states.get(entity_id).state
    return None if state == "unknown" else float(state)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("85", 85),
        (85, 85),
        ("96%", 96),
        (" 82.5 % ", 82.5),
        ("82.5", 82.5),
        ("notAvailable", None),
    ],
)
async def test_battery_level_from_device_list(
    hass: HomeAssistant, gateway: FakeGateway, value: object, expected: float | None
) -> None:
    gateway.devices = [_sensor(batteryLevel=value)]
    entry = await setup_bridge(hass, gateway)
    battery = await wait_for_entity(hass, "sensor", BATTERY)
    assert _number(hass, battery) == expected
    assert hass.states.get(battery).attributes["unit_of_measurement"] == "%"

    # The periodic refresh updates the battery level in both directions,
    # including a known value becoming unavailable.
    gateway.devices = [_sensor(batteryLevel="50%")]
    await _refresh_device_list(entry)
    await wait_until(lambda: _number(hass, battery) == 50)
    gateway.devices = [_sensor(batteryLevel=value)]
    await _refresh_device_list(entry)
    await wait_until(lambda: _number(hass, battery) == expected)
    coordinator = entry.runtime_data.coordinator
    assert coordinator.devices[DEVICE].channels[0].battery_level == expected


@pytest.mark.parametrize("value", ["101", "-1", "nan", "inf", True, [], "full"])
async def test_invalid_battery_level_is_ignored(
    hass: HomeAssistant, gateway: FakeGateway, value: object
) -> None:
    gateway.devices = [_sensor(batteryLevel="50%")]
    entry = await setup_bridge(hass, gateway)
    battery = await wait_for_entity(hass, "sensor", BATTERY)
    gateway.devices = [_sensor(batteryLevel=value)]
    await _refresh_device_list(entry)
    await hass.async_block_till_done()
    assert _number(hass, battery) == 50


@pytest.mark.parametrize(
    "dbm,expected",
    [
        (-61, -61),
        ("-70", -70),
        (-70.0, -70),
        ("nan", -80),
        (False, -80),
        (None, -80),
        ("-61.5", -80),
    ],
)
async def test_dbm_from_telegram_info(
    hass: HomeAssistant, gateway: FakeGateway, dbm: object, expected: int
) -> None:
    gateway.devices = [_sensor(dbm=-80)]
    entry, signal = await _setup_with_signal_strength(hass, gateway)
    assert _number(hass, signal) == -80
    assert hass.states.get(signal).attributes["unit_of_measurement"] == "dBm"

    await gateway.push(
        telegram(
            DEVICE,
            [{"key": "motionDetector", "value": "detected"}],
            telegramInfo={"dbm": dbm, "rorg": "A5"},
        )
    )
    motion = er.async_get(hass).async_get_entity_id(
        "binary_sensor", "opus_greennet", f"AABB0011_{DEVICE}_motion"
    )
    await wait_until(lambda: hass.states.get(motion).state == "on")
    assert _number(hass, signal) == expected
    assert entry.runtime_data.coordinator.devices[DEVICE].dbm == expected


async def test_dbm_from_device_list(hass: HomeAssistant, gateway: FakeGateway) -> None:
    gateway.devices = [_sensor(dbm=-75)]
    entry, signal = await _setup_with_signal_strength(hass, gateway)
    assert _number(hass, signal) == -75

    gateway.devices = [_sensor(dbm=-68)]
    await _refresh_device_list(entry)
    await wait_until(lambda: _number(hass, signal) == -68)

    # A list without (or with an invalid) dbm keeps the last known value.
    for device in (_sensor(), _sensor(dbm=None), _sensor(dbm="nan")):
        gateway.devices = [device]
        await _refresh_device_list(entry)
        await hass.async_block_till_done()
        assert _number(hass, signal) == -68

    # The device list at the start of a stream applies dbm as well.
    gateway.devices = [_sensor(dbm=-59)]
    await gateway.push(gateway.devices_document())
    await wait_until(lambda: _number(hass, signal) == -59)


async def test_rssi_only_updates_do_not_rewrite_unchanged_entities(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    states = [
        {"key": "motionDetector", "value": "detected"},
        {"key": "illuminance", "value": 200},
    ]
    gateway.devices = [_sensor(states=states, batteryLevel="85%", dbm=-80)]
    entry, signal = await _setup_with_signal_strength(hass, gateway)
    registry = er.async_get(hass)
    others = [
        registry.async_get_entity_id(domain, "opus_greennet", unique_id)
        for domain, unique_id in (
            ("binary_sensor", f"AABB0011_{DEVICE}_motion"),
            ("sensor", f"AABB0011_{DEVICE}_illuminance"),
            ("sensor", BATTERY),
        )
    ]
    assert [hass.states.get(entity_id).state for entity_id in others] == [
        "on",
        "200.0",
        "85.0",
    ]
    writes = _record_writes(hass, [signal, *others])

    # A telegram repeating the current readings with a new signal strength.
    await gateway.push(telegram(DEVICE, states, telegramInfo={"dbm": -61}))
    await wait_until(lambda: _number(hass, signal) == -61)
    # The periodic refresh reporting only a new signal strength.
    gateway.devices = [_sensor(states=states, batteryLevel="85%", dbm=-55)]
    await _refresh_device_list(entry)
    await wait_until(lambda: _number(hass, signal) == -55)
    assert writes == [signal, signal]

    # Nothing changed at all: no entity is written.
    await _refresh_device_list(entry)
    await gateway.push(telegram(DEVICE, states, telegramInfo={"dbm": -55}))
    await hass.async_block_till_done()
    assert writes == [signal, signal]
