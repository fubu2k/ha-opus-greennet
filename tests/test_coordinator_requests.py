"""Coordinator requests: queueing, acknowledgements and error mapping."""

from __future__ import annotations

import asyncio
import logging

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.opus_greennet.const import DOMAIN
from custom_components.opus_greennet.coordinator import (
    SUPERSEDED,
    OpusGreenNetCoordinator,
)
from tests.fake_gateway import FakeGateway, device_object, telegram
from tests.ha_helpers import setup_bridge, wait_until

SWITCH = "050BF714"
TWO_CHANNEL = "0507BF73"


@pytest.fixture
async def coordinator(
    hass: HomeAssistant, gateway: FakeGateway
) -> OpusGreenNetCoordinator:
    gateway.devices = [
        device_object(SWITCH, "D2-01-01", states=[{"key": "switch", "value": "off"}]),
        device_object(TWO_CHANNEL, "D2-01-11"),
    ]
    entry = await setup_bridge(hass, gateway)
    return entry.runtime_data.coordinator


def _puts(gateway: FakeGateway) -> list[tuple[str, list[dict]]]:
    return [
        (path, body["state"]["functions"])
        for method, path, body in gateway.requests
        if method == "PUT" and path.endswith("/state")
    ]


async def test_commands_overtake_queued_queries(
    gateway: FakeGateway, coordinator: OpusGreenNetCoordinator
) -> None:
    release = asyncio.Event()

    async def hold_first_put(method: str, path: str, body: object) -> None:
        if method == "PUT" and not release.is_set():
            await release.wait()

    gateway.on_request = hold_first_put
    first = asyncio.create_task(coordinator.async_turn_on(SWITCH))
    await wait_until(lambda: len(_puts(gateway)) == 1)
    background = asyncio.create_task(
        coordinator.async_query_device_status(SWITCH, background=True)
    )
    query = asyncio.create_task(coordinator.async_query_device_status(SWITCH))
    command = asyncio.create_task(coordinator.async_turn_off(SWITCH))
    await asyncio.sleep(0.05)
    release.set()
    await asyncio.gather(first, background, query, command)
    assert [functions for _, functions in _puts(gateway)] == [
        [{"key": "switch", "value": "on"}],
        [{"key": "switch", "value": "off"}],
        [{"key": "query", "value": "status"}],
        [{"key": "query", "value": "status"}],
    ]


async def test_multichannel_commands_name_their_channel(
    gateway: FakeGateway, coordinator: OpusGreenNetCoordinator
) -> None:
    await coordinator.async_turn_on(TWO_CHANNEL, 0)
    await coordinator.async_set_cover_position(SWITCH, 40)
    await coordinator.async_set_cover_tilt(SWITCH, 30, channel=1)
    await coordinator.async_set_climate_setpoint(SWITCH, 21.5)
    await coordinator.async_set_climate_mode(SWITCH, "off")
    assert _puts(gateway) == [
        (
            f"/devices/{TWO_CHANNEL}/state",
            [{"key": "channel", "value": "0"}, {"key": "switch", "value": "on"}],
        ),
        (f"/devices/{SWITCH}/state", [{"key": "position", "value": "40"}]),
        (
            f"/devices/{SWITCH}/state",
            [{"key": "channel", "value": "1"}, {"key": "angle", "value": "30"}],
        ),
        (f"/devices/{SWITCH}/state", [{"key": "temperatureSetpoint", "value": "21.5"}]),
        (f"/devices/{SWITCH}/state", [{"key": "heaterMode", "value": "off"}]),
    ]


async def test_rejected_command_is_reported_and_cleared_by_success(
    gateway: FakeGateway, coordinator: OpusGreenNetCoordinator
) -> None:
    gateway.status[f"/devices/{SWITCH}/state"] = 400
    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_turn_on(SWITCH)
    assert err.value.translation_key == "request_rejected"
    assert err.value.translation_placeholders["reason"] == "status 400: forced failure"
    assert coordinator.devices[SWITCH].last_command_error == (
        "status 400: forced failure"
    )

    del gateway.status[f"/devices/{SWITCH}/state"]
    await coordinator.async_turn_on(SWITCH)
    assert coordinator.devices[SWITCH].last_command_error is None


async def test_command_timeout_reports_unknown_outcome(
    gateway: FakeGateway,
    coordinator: OpusGreenNetCoordinator,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    gateway.delays[f"/devices/{SWITCH}/state"] = 1
    original = coordinator.client.async_put

    async def short_put(path, body, *, timeout=0.05):
        return await original(path, body, timeout=0.05)

    monkeypatch.setattr(coordinator.client, "async_put", short_put)
    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_query_device_status(SWITCH)
    assert err.value.translation_key == "request_timeout"
    assert "did not acknowledge a status query" in caplog.text


async def test_unreachable_gateway_fails_fast(
    gateway: FakeGateway, coordinator: OpusGreenNetCoordinator
) -> None:
    await gateway.stop()
    await wait_until(lambda: not coordinator.available)
    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_turn_on(SWITCH)
    assert err.value.translation_key == "gateway_unavailable"


async def test_connection_error_during_request(
    gateway: FakeGateway, coordinator: OpusGreenNetCoordinator
) -> None:
    async def drop(method: str, path: str, body: object) -> None:
        if method == "PUT":
            raise ConnectionResetError

    gateway.on_request = drop
    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_turn_on(SWITCH)
    assert err.value.translation_key in ("gateway_unavailable", "request_rejected")


async def test_rejected_credentials_start_reauth(
    hass: HomeAssistant, gateway: FakeGateway, coordinator: OpusGreenNetCoordinator
) -> None:
    gateway.accept_auth = False
    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_turn_on(SWITCH)
    assert err.value.translation_key == "request_rejected"
    assert not coordinator.available
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_superseded_background_check_is_not_sent(
    gateway: FakeGateway, coordinator: OpusGreenNetCoordinator
) -> None:
    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_query_device_status(
            SWITCH, background=True, still_needed=lambda: False
        )
    assert err.value.translation_placeholders["reason"] == SUPERSEDED
    assert _puts(gateway) == []


async def test_forgotten_device_drops_queued_request(
    gateway: FakeGateway, coordinator: OpusGreenNetCoordinator
) -> None:
    release = asyncio.Event()

    async def hold(method: str, path: str, body: object) -> None:
        if method == "PUT" and not release.is_set():
            await release.wait()

    gateway.on_request = hold
    first = asyncio.create_task(coordinator.async_turn_on(TWO_CHANNEL, 1))
    await wait_until(lambda: len(_puts(gateway)) == 1)
    queued = asyncio.create_task(coordinator.async_turn_on(SWITCH))
    await asyncio.sleep(0.01)
    coordinator.async_forget_device(SWITCH)
    release.set()
    await first
    with pytest.raises(HomeAssistantError) as err:
        await queued
    assert err.value.translation_key == "request_cancelled"
    assert len(_puts(gateway)) == 1


async def test_device_forgotten_while_request_in_flight(
    gateway: FakeGateway, coordinator: OpusGreenNetCoordinator
) -> None:
    release = asyncio.Event()

    async def hold(method: str, path: str, body: object) -> None:
        if method == "PUT":
            await release.wait()

    gateway.on_request = hold
    task = asyncio.create_task(coordinator.async_turn_on(SWITCH))
    await wait_until(lambda: len(_puts(gateway)) == 1)
    coordinator.async_forget_device(SWITCH)
    release.set()
    with pytest.raises(HomeAssistantError) as err:
        await task
    assert err.value.translation_key == "request_cancelled"


async def test_queue_wait_is_logged(
    gateway: FakeGateway,
    coordinator: OpusGreenNetCoordinator,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = iter([0.0, 5.0])
    monkeypatch.setattr(
        "custom_components.opus_greennet.coordinator.monotonic",
        lambda: next(clock, 10.0),
    )
    with caplog.at_level(logging.DEBUG):
        await coordinator.async_turn_on(SWITCH)
    assert "waited 5.00s in the queue" in caplog.text


async def test_own_outbound_echo_is_ignored_while_acknowledging(
    gateway: FakeGateway, coordinator: OpusGreenNetCoordinator
) -> None:
    async def echo(method: str, path: str, body: object) -> None:
        if method == "PUT":
            await gateway.push(
                telegram(SWITCH, [{"key": "switch", "value": "on"}], direction="to")
            )

    gateway.on_request = echo
    await coordinator.async_turn_on(SWITCH)
    # The entity applies its own estimate; the echo changed nothing.
    assert coordinator.devices[SWITCH].channels[0].is_on is False


async def test_recom_services(
    gateway: FakeGateway, coordinator: OpusGreenNetCoordinator
) -> None:
    configuration = await coordinator.async_get_device_configuration(SWITCH)
    assert configuration["configuration"]["deviceId"] == SWITCH
    parameters = await coordinator.async_get_device_parameters(SWITCH)
    assert parameters["parameters"] == []
    await coordinator.async_set_device_configuration(
        SWITCH, {"configuration": {"parameters": [{"key": "timer", "value": 60}]}}
    )
    assert gateway.requests[-1] == (
        "PUT",
        f"/devices/{SWITCH}/configuration",
        {"configuration": {"parameters": [{"key": "timer", "value": 60}]}},
    )
    gateway.status[f"/devices/{SWITCH}/parameters"] = 500
    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_get_device_parameters(SWITCH)
    assert err.value.translation_key == "request_rejected"
