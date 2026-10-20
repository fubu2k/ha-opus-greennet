"""A command the gateway queues for a sleeping device (201) gets no estimate.

REST transport. Ported from upstream ``kegelmeier/ha-opus-greennet``
``feat/rest-beta-101`` (``tests/test_soft_smart_ack.py``), which ported this
fork's phase 11 semantics to its REST coordinator. The behaviour itself is
unchanged here; these cases pin it on the REST path, next to the MQTT cases
in ``test_ack_device_health.py``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.opus_greennet.domain import CommandDelivery
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from custom_components.opus_greennet.light import OpusGreenNetLight
from custom_components.opus_greennet.switch import OpusGreenNetSwitch
from tests.test_rest_commands import (
    connected_coordinator as connected_coordinator,
)
from tests.test_rest_commands import radio as radio
from tests.test_rest_cover_movement import entity_for
from tests.test_rest_reconciliation_feedback import (
    feedback_coordinator as feedback_coordinator,
)
from tests.test_rest_reconciliation_feedback import query_timers
from tests.test_rest_reconciliation_feedback import timers as timers

DEFERRED = {"header": {"httpStatus": 201}}


async def test_acknowledgement_status_sets_the_delivery(feedback_coordinator, radio):
    assert (
        await feedback_coordinator.async_set_cover_position("DEV1", 0)
        is CommandDelivery.SENT
    )
    radio.response = DEFERRED
    assert (
        await feedback_coordinator.async_set_cover_position("DEV1", 100)
        is CommandDelivery.DEFERRED
    )


async def test_deferred_cover_command_keeps_the_reported_position(
    feedback_coordinator, radio, timers
):
    channel = feedback_coordinator.devices["DEV1"].get_or_create_channel()
    channel.position = 100
    channel.vertical_movement_time = 25
    entity = entity_for(feedback_coordinator)
    radio.response = DEFERRED

    await entity.async_open_cover()

    assert channel.position == 100
    assert channel.movement is None
    assert entity.state == "closed"
    assert not feedback_coordinator._cover_movements
    # The travel timer started before sending is cancelled; no status checks.
    assert all(cancel.called for _, _, cancel in timers)
    assert not query_timers(timers)
    assert not feedback_coordinator._pending_reconciliation_queries


async def test_deferred_stop_schedules_no_status_checks(
    feedback_coordinator, radio, timers
):
    radio.response = DEFERRED
    await feedback_coordinator.async_stop_cover("DEV1")
    assert not query_timers(timers)
    assert not feedback_coordinator._pending_reconciliation_queries


async def test_deferred_switch_command_schedules_no_status_checks(
    connected_coordinator, radio, timers
):
    device = EnOceanDevice("SW1", "Switch", [{"eep": "D2-01-01"}])
    device.get_or_create_channel().is_on = False
    connected_coordinator.devices["SW1"] = device
    radio.response = DEFERRED

    delivery = await connected_coordinator.async_turn_on("SW1")

    assert delivery is CommandDelivery.DEFERRED
    assert device.channels[0].is_on is False
    assert not query_timers(timers)


def _entity(cls, eep: str):
    device = EnOceanDevice("DEV2", "Device", [{"eep": eep}])
    device.get_or_create_channel().is_on = False
    device.channels[0].brightness = 0
    coordinator = MagicMock()
    coordinator.async_turn_on = AsyncMock(return_value=CommandDelivery.DEFERRED)
    coordinator.async_turn_off = AsyncMock(return_value=CommandDelivery.DEFERRED)
    entity = cls(coordinator, "AABB0011", "gateway", device, 0)
    entity.async_write_ha_state = MagicMock()
    return entity, device.channels[0]


@pytest.mark.parametrize(
    ("cls", "eep"),
    [(OpusGreenNetSwitch, "D2-01-01"), (OpusGreenNetLight, "D2-01-03")],
)
async def test_deferred_on_off_keeps_the_reported_state(cls, eep):
    entity, channel = _entity(cls, eep)

    await entity.async_turn_on()
    assert channel.is_on is False

    channel.is_on = True
    channel.brightness = 40
    await entity.async_turn_off()
    assert channel.is_on is True
    assert channel.brightness == 40
    entity.async_write_ha_state.assert_not_called()
