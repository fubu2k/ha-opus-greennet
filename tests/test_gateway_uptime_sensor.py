"""The gateway's uptime as days, hours, minutes and seconds on its own device."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.opus_greennet.const import DOMAIN
from custom_components.opus_greennet.sensor import (
    OpusGreenNetGatewayUptimeSensor,
    format_uptime,
)
from tests.fake_gateway import FakeGateway
from tests.ha_helpers import setup_bridge, wait_for_entity

EAG_ID = "AABB0011"


@pytest.mark.parametrize(
    ("uptime", "text"),
    [
        (None, None),
        ("0", "0 d 0 h 0 min 0 s"),
        ("59", "0 d 0 h 0 min 59 s"),
        ("60", "0 d 0 h 1 min 0 s"),
        ("3600", "0 d 1 h 0 min 0 s"),
        ("86399", "0 d 23 h 59 min 59 s"),
        ("86400", "1 d 0 h 0 min 0 s"),
        ("93784", "1 d 2 h 3 min 4 s"),
        ("93784.9", "1 d 2 h 3 min 4 s"),
        ("31536000", "365 d 0 h 0 min 0 s"),
    ],
)
def test_uptime_is_shown_in_days_hours_minutes_and_seconds(
    uptime: str | None, text: str | None
) -> None:
    assert format_uptime(uptime) == text


def _sensor(uptime: str | None, available: bool = True):
    coordinator = SimpleNamespace(gateway_uptime=uptime, available=available)
    return OpusGreenNetGatewayUptimeSensor(coordinator, EAG_ID)  # type: ignore[arg-type]


def test_the_sensor_describes_the_gateway() -> None:
    sensor = _sensor("93784")
    assert sensor.unique_id == f"{EAG_ID}_gateway_uptime"
    assert sensor.device_info == {"identifiers": {(DOMAIN, EAG_ID)}}
    assert sensor.entity_category is EntityCategory.DIAGNOSTIC
    assert sensor.should_poll is False
    assert sensor.native_value == "1 d 2 h 3 min 4 s"
    assert sensor.extra_state_attributes == {"uptime_seconds": 93784.0}


def test_without_a_reading_the_sensor_has_no_value() -> None:
    sensor = _sensor(None)
    assert sensor.native_value is None
    assert sensor.extra_state_attributes is None


@pytest.mark.parametrize("available", [True, False])
def test_the_sensor_follows_the_gateway_availability(available: bool) -> None:
    assert _sensor("1", available).available is available


async def test_each_health_probe_updates_the_sensor_on_the_gateway_device(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.uptime = 93784
    entry = await setup_bridge(hass, gateway)
    unique_id = f"{gateway.eurid}_gateway_uptime"
    entity_id = await wait_for_entity(hass, "sensor", unique_id)

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "1 d 2 h 3 min 4 s"
    assert state.attributes["uptime_seconds"] == 93784.0
    registered = er.async_get(hass).async_get(entity_id)
    assert registered is not None
    assert registered.device_id == entry.runtime_data.gateway_device_id
    assert registered.entity_category is EntityCategory.DIAGNOSTIC

    # The next reading is shown as soon as the probe has read it.
    gateway.uptime = 93844
    coordinator = entry.runtime_data.coordinator
    coordinator._async_health_tick(dt_util.utcnow())
    assert coordinator._probe_task is not None
    await coordinator._probe_task
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "1 d 2 h 4 min 4 s"
    assert state.attributes["uptime_seconds"] == 93844.0
