"""Manual child removal, rediscovery, and isolated request cancellation."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.opus_greennet import async_remove_config_entry_device
from custom_components.opus_greennet.const import DOMAIN
from tests.fake_gateway import FakeGateway, device_object
from tests.ha_helpers import configure_bridge, setup_bridge, wait_for_entity, wait_until


@pytest.mark.parametrize("rediscover", [False, True])
@pytest.mark.parametrize(
    "eep,domain,suffix,fields,expected",
    [
        (
            "D1-4B-05",
            "sensor",
            "humidity",
            {"states": [{"key": "humidity", "value": 50}]},
            "50.0",
        ),
        (
            "F6-05-02",
            "binary_sensor",
            "smoke_alarm",
            {"states": [{"key": "smokeAlarm", "value": "idle"}]},
            "off",
        ),
        ("A5-07-03", "sensor", "battery_level", {"batteryLevel": "85%"}, "85.0"),
        (
            "D2-06-40",
            "lock",
            "autolock",
            {
                "states": [{"key": "lock", "value": "locked"}],
                "targetState": [{"key": "unlock", "value": "notAllowed"}],
            },
            "locked",
        ),
    ],
)
async def test_remove_child_reload_and_rediscovery(
    hass: HomeAssistant,
    gateway: FakeGateway,
    rediscover: bool,
    eep: str,
    domain: str,
    suffix: str,
    fields: dict[str, Any],
    expected: str,
) -> None:
    old = device_object("OLD", eep, **fields)
    replacement = device_object("NEW", eep, **fields)
    gateway.devices = [old, replacement]
    entry = await setup_bridge(hass, gateway)
    removed_entity = await wait_for_entity(hass, domain, f"AABB0011_OLD_{suffix}")
    replacement_entity = await wait_for_entity(hass, domain, f"AABB0011_NEW_{suffix}")
    assert hass.states.get(removed_entity).state == expected
    registry = dr.async_get(hass)
    child = registry.async_get_device_by_identifier(
        (DOMAIN, "AABB0011_OLD"), entry.entry_id
    )
    gateway_device = registry.async_get(entry.runtime_data.gateway_device_id)
    assert not await async_remove_config_entry_device(hass, entry, gateway_device)
    before = list(gateway.requests)
    assert await async_remove_config_entry_device(hass, entry, child)
    registry.async_remove_device(child.id)
    await hass.async_block_till_done()
    # Removal is local only: nothing is sent to (or unpaired on) the gateway.
    assert gateway.requests == before
    assert hass.states.get(removed_entity) is None
    assert hass.states.get(replacement_entity) is not None
    assert "OLD" not in entry.runtime_data.coordinator.devices
    assert "NEW" in entry.runtime_data.coordinator.devices

    if rediscover:
        # The gateway still lists the child: the next complete device list
        # (sent at the start of every stream) discovers it again.
        await gateway.push(gateway.devices_document())
        await wait_for_entity(hass, domain, f"AABB0011_OLD_{suffix}")
        entities = [
            e
            for e in er.async_get(hass).entities.values()
            if e.unique_id == f"AABB0011_OLD_{suffix}"
        ]
        assert len(entities) == 1
        await wait_until(
            lambda: (
                hass.states.get(entities[0].entity_id) is not None
                and hass.states.get(entities[0].entity_id).state == expected
            )
        )
    else:
        gateway.devices = [replacement]
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert (
        registry.async_get_device_by_identifier(
            (DOMAIN, "AABB0011_OLD"), entry.entry_id
        )
        is not None
    ) == rediscover
    assert hass.states.get(replacement_entity) is not None


async def test_removal_rejects_foreign_identifiers(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    entry = (await configure_bridge(hass, gateway))["result"]
    await hass.async_block_till_done()
    for identifiers, entries in [
        ({(DOMAIN, "OTHER_CHILD")}, {entry.entry_id}),
        ({(DOMAIN, "AABB0011_CHILD")}, {"other-entry"}),
        ({("foreign", "AABB0011_CHILD")}, {entry.entry_id}),
    ]:
        assert not await async_remove_config_entry_device(
            hass,
            entry,
            SimpleNamespace(
                identifiers=identifiers, config_entry_id=next(iter(entries))
            ),
        )


def test_forget_cancels_only_child_work(coordinator, make_device):
    old = make_device("D2-01-00", device_id="OLD")
    new = make_device("D2-01-00", device_id="NEW")
    coordinator.devices.update(OLD=old, NEW=new)
    cancellations = {}
    for device_id in ("OLD", "NEW"):
        cancel = MagicMock()
        cancellations[device_id] = cancel
        coordinator._pending_reconciliation_queries[device_id, 0] = [cancel]
        coordinator._pending_reconciliation_fields[device_id, 0] = {"switch"}
        coordinator._reconcile_needed.add((device_id, 0))
        coordinator._feedback_revisions[device_id, 0, "switch"] = 3
    coordinator.async_forget_device("OLD")
    cancellations["OLD"].assert_called_once()
    cancellations["NEW"].assert_not_called()
    assert "OLD" not in coordinator.devices
    assert list(coordinator._pending_reconciliation_queries) == [("NEW", 0)]
    assert list(coordinator._pending_reconciliation_fields) == [("NEW", 0)]
    assert coordinator._reconcile_needed == {("NEW", 0)}
    assert list(coordinator._feedback_revisions) == [("NEW", 0, "switch")]
    # Requests already queued for the forgotten child are dropped.
    assert coordinator._device_generations == {"OLD": 1}


async def test_removal_cancels_active_and_queued_requests_only_for_child(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [
        device_object("OLD", "D2-01-00"),
        device_object("NEW", "D2-01-00"),
    ]
    entry = await setup_bridge(hass, gateway)
    coordinator = entry.runtime_data.coordinator
    release = asyncio.Event()

    async def hold(method: str, path: str, body: object) -> None:
        if method == "PUT" and path == "/devices/OLD/state":
            await release.wait()

    gateway.on_request = hold
    first = asyncio.create_task(coordinator.async_turn_on("OLD"))
    await wait_until(lambda: gateway.requests_to("PUT", "/devices/OLD/state"))
    queued = asyncio.create_task(coordinator.async_turn_on("OLD"))
    other = asyncio.create_task(coordinator.async_turn_on("NEW"))
    for _ in range(10):
        await asyncio.sleep(0)
    coordinator.async_forget_device("OLD")
    release.set()
    for task in (first, queued):
        with pytest.raises(HomeAssistantError) as err:
            await task
        assert err.value.translation_key == "request_cancelled"
    await other
    assert len(gateway.requests_to("PUT", "/devices/OLD/state")) == 1
    assert gateway.requests_to("PUT", "/devices/NEW/state") == [
        {"state": {"functions": [{"key": "switch", "value": "on"}]}}
    ]


async def test_unloaded_entry_removes_a_stale_child_without_the_coordinator(
    hass: HomeAssistant,
) -> None:
    """A registry-only device can go while the entry is not loaded."""
    coordinator = MagicMock()
    entry = SimpleNamespace(
        entry_id="entry-id",
        data={"eag_id": "AABB0011"},
        state=ConfigEntryState.NOT_LOADED,
        runtime_data=SimpleNamespace(coordinator=coordinator),
    )
    device = SimpleNamespace(
        identifiers={(DOMAIN, "AABB0011_CHILD")}, config_entry_id="entry-id"
    )
    assert await async_remove_config_entry_device(hass, entry, device)
    coordinator.async_forget_device.assert_not_called()
