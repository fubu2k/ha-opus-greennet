"""End-to-end tests against an in-process OPUS gateway over real HTTP."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from tests.fake_gateway import FakeGateway, device_object, telegram
from tests.ha_helpers import setup_bridge, wait_for_entity, wait_until

SWITCH = "050BF714"


async def test_stream_telegram_updates_switch_and_command_is_put(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [
        device_object(SWITCH, "D2-01-01", states=[{"key": "switch", "value": "off"}])
    ]
    await setup_bridge(hass, gateway)
    entity_id = await wait_for_entity(hass, "switch", f"AABB0011_{SWITCH}")
    assert hass.states.get(entity_id).state == "off"

    await gateway.push(telegram(SWITCH, [{"key": "switch", "value": "on"}]))
    await wait_until(lambda: hass.states.get(entity_id).state == "on")

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": entity_id}, blocking=True
    )
    assert gateway.requests_to("PUT", f"/devices/{SWITCH}/state") == [
        {"state": {"functions": [{"key": "switch", "value": "off"}]}}
    ]
    assert hass.states.get(entity_id).state == "off"
