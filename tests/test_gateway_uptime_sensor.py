"""The gateway's start time, derived from its uptime, on its own device."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.opus_greennet.const import DOMAIN
from custom_components.opus_greennet.sensor import OpusGreenNetGatewayUptimeSensor
from tests.fake_gateway import FakeGateway
from tests.ha_helpers import setup_bridge, wait_for_entity

EAG_ID = "AABB0011"


def _sensor(uptime: str | None, available: bool = True):
    coordinator = SimpleNamespace(gateway_uptime=uptime, available=available)
    return OpusGreenNetGatewayUptimeSensor(coordinator, EAG_ID)  # type: ignore[arg-type]


def _near(value: datetime | None, expected: datetime) -> bool:
    return value is not None and abs(value - expected) < timedelta(seconds=5)


def test_the_sensor_describes_the_gateway() -> None:
    sensor = _sensor("93784")
    assert sensor.unique_id == f"{EAG_ID}_gateway_uptime"
    assert sensor.device_info == {"identifiers": {(DOMAIN, EAG_ID)}}
    assert sensor.entity_category is EntityCategory.DIAGNOSTIC
    assert sensor.device_class is SensorDeviceClass.TIMESTAMP
    assert sensor.should_poll is False
    assert sensor.extra_state_attributes is None


def test_the_start_time_changes_only_when_the_gateway_restarts() -> None:
    sensor = _sensor("93784")
    assert sensor._update_started() is True  # noqa: SLF001
    started = sensor.native_value
    assert _near(started, dt_util.utcnow() - timedelta(seconds=93784))

    # A later reading that only differs by network delay keeps the time.
    sensor._coordinator.gateway_uptime = "93754"  # noqa: SLF001
    assert sensor._update_started() is False  # noqa: SLF001
    assert sensor.native_value == started

    # A restart moves it.
    sensor._coordinator.gateway_uptime = "30"  # noqa: SLF001
    assert sensor._update_started() is True  # noqa: SLF001
    assert _near(sensor.native_value, dt_util.utcnow() - timedelta(seconds=30))


def test_without_a_reading_the_sensor_has_no_value() -> None:
    sensor = _sensor(None)
    assert sensor._update_started() is False  # noqa: SLF001
    assert sensor.native_value is None


@pytest.mark.parametrize("available", [True, False])
def test_the_sensor_follows_the_gateway_availability(available: bool) -> None:
    assert _sensor("1", available).available is available


async def _probe(entry) -> None:
    coordinator = entry.runtime_data.coordinator
    coordinator._async_health_tick(dt_util.utcnow())  # noqa: SLF001
    assert coordinator._probe_task is not None  # noqa: SLF001
    await coordinator._probe_task  # noqa: SLF001


async def test_the_sensor_writes_only_on_a_gateway_restart(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.uptime = 93784
    entry = await setup_bridge(hass, gateway)
    unique_id = f"{gateway.eurid}_gateway_uptime"
    entity_id = await wait_for_entity(hass, "sensor", unique_id)

    state = hass.states.get(entity_id)
    assert state is not None
    assert _near(
        dt_util.parse_datetime(state.state),
        dt_util.utcnow() - timedelta(seconds=93784),
    )
    registered = er.async_get(hass).async_get(entity_id)
    assert registered is not None
    assert registered.device_id == entry.runtime_data.gateway_device_id
    assert registered.entity_category is EntityCategory.DIAGNOSTIC

    # The next probe a minute later does not write a new state.
    gateway.uptime = 93844
    await _probe(entry)
    await hass.async_block_till_done()
    unchanged = hass.states.get(entity_id)
    assert unchanged is not None
    assert unchanged.last_updated == state.last_updated

    # A restart does.
    gateway.uptime = 30
    await _probe(entry)
    await hass.async_block_till_done()
    restarted = hass.states.get(entity_id)
    assert restarted is not None
    assert _near(
        dt_util.parse_datetime(restarted.state),
        dt_util.utcnow() - timedelta(seconds=30),
    )
