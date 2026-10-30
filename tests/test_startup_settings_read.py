"""Reading the stored settings of switches and dimmers once after startup.

The gateway's cached copy of a device can be hours old (it is refreshed only
when the device is read). After startup each repeater-capable device is read
once, one after the other, with the lowest request priority. A failure is only
logged and never repeated.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from custom_components.opus_greennet.api import PRIORITY_BACKGROUND, OpusTimeoutError
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from tests.test_coordinator_commands import (
    connected_coordinator as connected_coordinator,
)
from tests.test_coordinator_commands import radio as radio


def _add(coord, device_id: str, eep: str) -> EnOceanDevice:
    device = coord.devices[device_id] = EnOceanDevice(
        device_id, device_id, [{"eep": eep}]
    )
    device.get_or_create_channel()
    return device


async def _run(coord) -> None:
    coord.async_start_settings_read()
    await asyncio.gather(*coord._tasks)


async def test_each_repeater_device_is_read_once_with_background_priority(
    connected_coordinator,
) -> None:
    coord = connected_coordinator
    _add(coord, "SW", "D2-01-01")
    _add(coord, "DIM", "D2-01-03")
    _add(coord, "BLIND", "D2-05-02")
    reread = AsyncMock()
    with patch.object(coord, "async_reread_device_settings", reread):
        await _run(coord)
    assert [call.args for call in reread.await_args_list] == [("SW",), ("DIM",)]
    assert all(
        call.kwargs == {"priority": PRIORITY_BACKGROUND}
        for call in reread.await_args_list
    )


async def test_the_values_of_the_device_replace_the_cached_ones(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    switch = _add(coord, "SW", "D2-01-01")
    switch.channels[0].routing = "off"
    radio.response = {
        "header": {"httpStatus": 200},
        "configuration": {"parameters": [{"key": "routingTime", "value": 0.3}]},
    }
    await _run(coord)
    assert [(m, p) for m, p, _ in radio.sent] == [
        ("GET", "/devices/SW/configuration"),
        ("GET", "/devices/SW/repeaterState"),
    ]
    assert switch.channels[0].routing == "on"


async def test_a_failed_device_does_not_stop_the_others_and_is_not_repeated(
    connected_coordinator,
) -> None:
    coord = connected_coordinator
    _add(coord, "SW", "D2-01-01")
    _add(coord, "DIM", "D2-01-03")
    reread = AsyncMock(side_effect=[coord_error(), None])
    with patch.object(coord, "async_reread_device_settings", reread):
        await _run(coord)
    assert reread.await_count == 2


def coord_error():
    from homeassistant.exceptions import HomeAssistantError

    return HomeAssistantError("timeout")


async def test_a_gateway_error_is_only_logged(radio, connected_coordinator) -> None:
    coord = connected_coordinator
    _add(coord, "SW", "D2-01-01")
    radio.response = OpusTimeoutError("no answer")
    await _run(coord)
    assert len(radio.sent) == 1


async def test_nothing_is_read_while_the_gateway_is_unavailable(
    connected_coordinator,
) -> None:
    coord = connected_coordinator
    _add(coord, "SW", "D2-01-01")
    coord._gateway_available = False
    reread = AsyncMock()
    with patch.object(coord, "async_reread_device_settings", reread):
        await _run(coord)
    reread.assert_not_awaited()
