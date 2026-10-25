"""Remaining statement and branch gaps of the coverage report.

Each test names the behaviour it protects; none of them changes a gate. They
reuse the fixtures and helpers of the suites that cover the neighbouring
behaviour.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send

from custom_components.opus_greennet import protocol
from custom_components.opus_greennet.api import OpusAuthError
from custom_components.opus_greennet.binary_sensor import (
    OpusGreenNetBaseBinarySensor,
    async_setup_entry,
)
from custom_components.opus_greennet.climate import OpusGreenNetClimate
from custom_components.opus_greennet.const import ROCKER_2_BUTTON_KEYS
from custom_components.opus_greennet.coordinator import (
    SIGNAL_DEVICE_DISCOVERED,
    SIGNAL_DEVICE_REMOVED,
    CoverMovement,
)
from custom_components.opus_greennet.diagnostics import (
    _redact_diagnostics,
    async_get_config_entry_diagnostics,
)
from custom_components.opus_greennet.enocean_device import (
    DeviceProfile,
    EnOceanDevice,
)
from custom_components.opus_greennet.sensor import (
    OpusGreenNetCoverRunSensor,
    OpusGreenNetHandleSensor,
    OpusGreenNetLastSeenSensor,
    OpusGreenNetSignalStrengthSensor,
)
from tests.fake_gateway import FakeGateway
from tests.ha_helpers import setup_bridge, wait_for_entity
from tests.test_autolock_control import LOCK_MODULE, _lock
from tests.test_binary_sensor_discovery import entry_for
from tests.test_coordinator_commands import (
    connected_coordinator as connected_coordinator,
)
from tests.test_coordinator_commands import radio as radio
from tests.test_coordinator_stream_backoff import coord as coord
from tests.test_cover_movement import motion as motion
from tests.test_entities import EAG_ID, GATEWAY_DEVICE_ID, _coordinator, _device
from tests.test_opus_motion_detector import motion_device, press
from tests.test_reconciliation_feedback import (
    feedback_coordinator as feedback_coordinator,
)
from tests.test_reconciliation_feedback import timers as timers
from tests.test_run_time_restore import (
    ENTITY_ID,
    RUN_ATTRIBUTES,
    UNIQUE_ID,
    _blind,
    _register,
    _restore,
)

MODULE = "custom_components.opus_greennet.coordinator"
DEFERRED = {"header": {"httpStatus": 201}}


# ── enocean_device.py ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("key", "attribute", "value"),
    [
        ("thermalMode", "thermal_mode", "heating"),
        ("thermalMode", "thermal_mode", "cooling"),
        ("temperatureOrigin", "temperature_origin", "external"),
        ("temperatureOrigin", "temperature_origin", "internal"),
        ("actuatorDeactivated", "actuator_deactivated", "info"),
        ("actuatorDeactivated", "actuator_deactivated", "reset"),
        ("circuitInUse", "circuit_in_use", "error"),
        ("circuitInUse", "circuit_in_use", "reset"),
    ],
)
def test_valid_state_enums_are_stored(
    make_device, make_telegram, key, attribute, value
) -> None:
    device = make_device("D1-4B-07")
    device.update_from_telegram(make_telegram([{"key": key, "value": value}]))
    assert getattr(device.channels[0], attribute) == value


def test_unknown_button_action_records_no_button(make_device, make_telegram) -> None:
    device = make_device("F6-02-01")
    device.update_from_telegram(
        make_telegram([{"key": "buttonA0", "value": "noChange"}])
    )
    channel = device.channels[0]
    assert channel.last_button is None
    assert channel.last_button_action is None


def test_motion_detector_ignores_presses_of_other_buttons() -> None:
    device = motion_device()
    press(device, "buttonB0")
    channel = device.channels[0]
    assert channel.last_button == "buttonB0"
    assert channel.motion is None


def test_profile_without_button_keys_falls_back_to_the_eep_buttons() -> None:
    device = EnOceanDevice("ROCKER", "Rocker", [{"eep": "F6-02-02"}])
    device.profile = DeviceProfile(from_keys=frozenset({"switch"}))
    assert device.button_keys == ROCKER_2_BUTTON_KEYS


# ── protocol.py ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "configuration",
    [
        {"parameters": "not a list"},
        {"parameters": None},
        {"parameters": ["not an object", 7, None]},
    ],
)
def test_last_vertical_movement_time_ignores_malformed_parameters(
    configuration,
) -> None:
    assert protocol.last_vertical_movement_times({"configuration": configuration}) == {}


def test_last_vertical_movement_time_skips_only_the_malformed_entry() -> None:
    data = {
        "configuration": {
            "parameters": [
                "not an object",
                {"key": "lastVerticalMovementTime", "value": 20.0},
            ]
        }
    }
    assert protocol.last_vertical_movement_times(data) == {0: 20.0}


# ── sensor.py ──────────────────────────────────────────────────────────────


def test_signal_strength_is_unknown_until_a_telegram_reports_it(make_device) -> None:
    device = make_device("D2-01-01")
    sensor = OpusGreenNetSignalStrengthSensor(MagicMock(), EAG_ID, "gw", device)
    assert sensor.native_value is None
    device.dbm = -70
    assert sensor.native_value == -70


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "2026-10-19T18:17:17.571+0200",
            datetime(
                2026, 10, 19, 18, 17, 17, 571000, tzinfo=timezone(timedelta(hours=2))
            ),
        ),
        ("2026-10-19T16:17:17Z", datetime(2026, 10, 19, 16, 17, 17, tzinfo=UTC)),
        ("", None),
        ("not a time", None),
        # No offset: the zone is not guessed, the value stays unknown.
        ("2026-10-19T18:17:17", None),
    ],
)
def test_last_seen_is_a_timezone_aware_time_or_unknown(
    make_device, raw: str, expected: datetime | None
) -> None:
    device = make_device("D2-01-01")
    sensor = OpusGreenNetLastSeenSensor(MagicMock(), EAG_ID, "gw", device)
    device.last_seen = raw
    assert sensor.native_value == expected
    assert sensor.unique_id == f"{EAG_ID}_{device.device_id}_last_seen"
    assert sensor.device_class is SensorDeviceClass.TIMESTAMP
    assert sensor.entity_category is EntityCategory.DIAGNOSTIC
    assert sensor.entity_registry_enabled_default is False
    assert sensor.translation_key == "last_seen"


def test_handle_sensors_report_only_what_the_handle_sent(make_device) -> None:
    device = make_device("D2-06-40", device_id="HANDLE")
    unlock = OpusGreenNetHandleSensor(
        MagicMock(), EAG_ID, "gw", device, "unlock_request"
    )
    unknown = OpusGreenNetHandleSensor(MagicMock(), EAG_ID, "gw", device, "other")
    assert unlock.native_value is None
    channel = device.get_or_create_channel()
    assert unlock.native_value is None
    assert unknown.native_value is None
    channel.unlock_request = True
    assert unlock.native_value == "requested"
    channel.unlock_request = False
    assert unlock.native_value == "not_requested"
    assert unknown.native_value is None


async def test_restored_run_time_without_a_stored_state_has_no_attributes(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    _register(hass)
    _restore(hass, 12.0, RUN_ATTRIBUTES)
    gateway.devices = [_blind()]
    with patch.object(
        OpusGreenNetCoverRunSensor,
        "async_get_last_state",
        AsyncMock(return_value=None),
    ):
        await setup_bridge(hass, gateway)
        assert await wait_for_entity(hass, "sensor", UNIQUE_ID) == ENTITY_ID
    state = hass.states.get(ENTITY_ID)
    assert state.state == "12.0"
    assert all(key not in state.attributes for key in RUN_ATTRIBUTES)


# ── binary_sensor.py ───────────────────────────────────────────────────────


async def test_binary_sensor_without_unique_id_is_rejected(
    hass: HomeAssistant, coordinator, make_device
) -> None:
    device = make_device("F6-05-01")
    coordinator.devices[device.device_id] = device
    entry = entry_for(coordinator)
    add = MagicMock()
    try:
        with (
            patch.object(OpusGreenNetBaseBinarySensor, "unique_id", None),
            pytest.raises(ValueError, match="unique ID"),
        ):
            await async_setup_entry(hass, entry, add)
    finally:
        for call in entry.async_on_unload.call_args_list:
            call.args[0]()
    add.assert_not_called()


async def test_late_removal_of_an_already_released_entity_changes_nothing(
    hass: HomeAssistant, coordinator, make_device
) -> None:
    """A removal callback may run after the device forgot the entity."""
    device = make_device("D1-4B-05")
    coordinator.devices[device.device_id] = device
    entry = entry_for(coordinator)
    background: list = []
    entry.async_create_background_task = MagicMock(
        side_effect=lambda _hass, coroutine, *_args, **_kwargs: background.append(
            coroutine
        )
    )
    removers: dict = {}
    add = MagicMock()
    with patch.object(
        OpusGreenNetBaseBinarySensor,
        "async_on_remove",
        lambda self, func: removers.__setitem__(self, func),
    ):
        await async_setup_entry(hass, entry, add)
    entities = add.call_args.args[0]
    assert len(entities) > 1
    live, released = entities[0], entities[1]
    # Only the first entity looks live; the others are released at once.
    live.hass = hass
    async_dispatcher_send(
        hass, f"{SIGNAL_DEVICE_REMOVED}_{coordinator.eag_id}", device.device_id
    )
    await hass.async_block_till_done()

    removers[released]()
    assert len(background) == 1
    await background.pop()

    # The live entity is still reserved: the device cannot be added again.
    async_dispatcher_send(
        hass, f"{SIGNAL_DEVICE_DISCOVERED}_{coordinator.eag_id}", device
    )
    await hass.async_block_till_done()
    assert add.call_count == 1

    # Once the last old entity is gone the reservation ends.
    live.hass = None
    removers[live]()
    await background.pop()
    async_dispatcher_send(
        hass, f"{SIGNAL_DEVICE_DISCOVERED}_{coordinator.eag_id}", device
    )
    await hass.async_block_till_done()
    assert add.call_count == 2
    for call in entry.async_on_unload.call_args_list:
        call.args[0]()


# ── climate.py ─────────────────────────────────────────────────────────────


async def test_climate_ignores_requests_it_cannot_map() -> None:
    coordinator = _coordinator()
    entity = OpusGreenNetClimate(
        coordinator, EAG_ID, GATEWAY_DEVICE_ID, _device("D1-4B-05")
    )
    await entity.async_set_temperature()
    await entity.async_set_hvac_mode(HVACMode.COOL)
    coordinator.async_set_climate_setpoint.assert_not_awaited()
    coordinator.async_set_climate_mode.assert_not_awaited()


# ── lock.py ────────────────────────────────────────────────────────────────


async def test_replaced_request_leaves_the_newer_request_settling() -> None:
    entity, coordinator, _ = _lock()
    started = asyncio.Event()
    release = asyncio.Event()

    async def send(*_args, **_kwargs) -> None:
        started.set()
        await release.wait()

    coordinator.async_set_handle_unlock = AsyncMock(side_effect=send)
    with patch(f"{LOCK_MODULE}.LOCK_SETTLE_SECONDS", 0):
        first = asyncio.create_task(entity.async_lock())
        await started.wait()
    with patch(f"{LOCK_MODULE}.LOCK_SETTLE_SECONDS", 0.05):
        second = asyncio.create_task(entity.async_unlock())
        await asyncio.sleep(0)
    release.set()
    await first
    # The older request finished while the newer one was still settling.
    assert entity.is_unlocking is True
    await second
    assert entity.is_unlocking is False


# ── diagnostics.py ─────────────────────────────────────────────────────────


def test_redaction_keeps_text_that_does_not_decode_to_a_container() -> None:
    with patch("custom_components.opus_greennet.diagnostics.parse_json", lambda _: 5):
        assert _redact_diagnostics({"raw": "{ looks like json"}, set()) == {
            "raw": "{ looks like json"
        }


async def test_diagnostics_without_a_position_origin_helper() -> None:
    device = EnOceanDevice("AABB1122", "Cover", [{"eep": "D2-05-02"}])
    coordinator = SimpleNamespace(
        eag_id="AABB0011",
        available=True,
        devices={device.device_id: device},
        gateway_info={},
        gateway_uptime=None,
        last_vertical_movement_times={"AABB1122": {0: 12.5}},
    )
    entry = SimpleNamespace(
        entry_id="entry-id",
        title="Bridge",
        data={"eag_id": "AABB0011"},
        runtime_data=SimpleNamespace(coordinator=coordinator),
    )

    result = await async_get_config_entry_diagnostics(MagicMock(), entry)

    diagnostic = result["devices"][0]
    assert diagnostic["last_vertical_movement_time"] == {0: 12.5}
    assert "position_origin" not in diagnostic


# ── coordinator.py: event stream ───────────────────────────────────────────


def _replace_reader_while_streaming(coord, error: Exception) -> None:
    """Make the stream fail after the reader was replaced by a newer one."""

    created: list[str] = []

    def create_task(coroutine, name):
        coroutine.close()
        created.append(name)
        task = MagicMock()
        task.done.return_value = False
        return task

    coord._create_task = create_task  # noqa: SLF001
    coord._start_stream()  # noqa: SLF001

    async def failing_stream():
        coord._restart_stream()  # noqa: SLF001
        raise error
        yield {}

    coord.client.async_stream = failing_stream


async def test_replaced_reader_ignores_an_unexpected_error(coord) -> None:
    _replace_reader_while_streaming(coord, RuntimeError("boom"))
    generation = coord._stream_generation  # noqa: SLF001
    sleep = AsyncMock()
    with patch(f"{MODULE}.asyncio.sleep", sleep):
        await coord._async_run_stream(generation)  # noqa: SLF001
    assert coord._gateway_available  # noqa: SLF001
    assert coord._stream_connected is False  # noqa: SLF001
    sleep.assert_not_called()


async def test_replaced_reader_ignores_rejected_credentials_mid_stream(coord) -> None:
    _replace_reader_while_streaming(coord, OpusAuthError("status 401"))
    on_auth_failed = MagicMock()
    coord._on_auth_failed = on_auth_failed  # noqa: SLF001
    generation = coord._stream_generation  # noqa: SLF001
    await coord._async_run_stream(generation)  # noqa: SLF001
    on_auth_failed.assert_not_called()
    assert coord._gateway_available  # noqa: SLF001


# ── coordinator.py: cover movement estimates ───────────────────────────────


def test_clearing_the_estimate_of_an_unknown_device_only_cancels_it(coord) -> None:
    cancel = MagicMock()
    coord._cover_movements[("GONE", 0)] = CoverMovement(  # noqa: SLF001
        100, 20, 0.0, 1.0, cancel=cancel
    )
    coord._clear_cover_movement("GONE", 0)  # noqa: SLF001
    cancel.assert_called_once_with()
    assert coord._cover_movements == {}  # noqa: SLF001


def test_finishing_the_estimate_of_an_unknown_device_changes_no_state(coord) -> None:
    movement = CoverMovement(100, 20, 0.0, 1.0)
    coord._cover_movements[("GONE", 0)] = movement  # noqa: SLF001
    coord._finish_cover_movement("GONE", 0, movement)  # noqa: SLF001
    assert movement.elapsed is True
    assert coord._cover_movements == {}  # noqa: SLF001
    assert coord._position_origins == {}  # noqa: SLF001


def test_retarget_without_a_known_start_position_uses_the_full_travel(coord) -> None:
    device = EnOceanDevice("COVER", "Cover", [{"eep": "D2-05-02"}])
    channel = device.get_or_create_channel()
    channel.vertical_movement_time = 25
    coord.devices["COVER"] = device
    coord._cover_movements[("COVER", 0)] = CoverMovement(  # noqa: SLF001
        None, 50, 0.0, 10.0
    )
    with patch(f"{MODULE}.async_call_later", return_value=MagicMock()):
        movement = coord._start_cover_movement(device, 0, 20)  # noqa: SLF001
    assert movement is not None
    assert movement.start_position is None
    assert movement.travel == 25
    assert coord._cover_movements[("COVER", 0)] is movement  # noqa: SLF001


# ── coordinator.py: commands ───────────────────────────────────────────────


async def test_query_with_a_malformed_channel_selector_is_still_sent(
    feedback_coordinator, radio
) -> None:
    functions = [
        {"key": "channel", "value": "not a channel"},
        {"key": "query", "value": "status"},
    ]
    await feedback_coordinator.async_send_command("DEV1", functions)
    assert radio.functions() == [functions]
    assert not feedback_coordinator._cover_runs  # noqa: SLF001


async def test_deferred_command_keeps_an_estimate_that_replaced_its_own(
    motion, radio, timers
) -> None:
    coord, _, _ = motion
    radio.auto_respond = False
    task = asyncio.create_task(coord.async_set_cover_position("DEV1", 0))
    await radio.wait_sent(1)
    replacement = CoverMovement(100, 50, 0.0, 10.0)
    coord._cover_movements[("DEV1", 0)] = replacement  # noqa: SLF001
    radio.respond(DEFERRED)
    await task
    assert coord._cover_movements[("DEV1", 0)] is replacement  # noqa: SLF001


async def test_rejected_command_keeps_an_estimate_that_replaced_its_own(
    motion, radio, timers
) -> None:
    coord, _, _ = motion
    radio.auto_respond = False
    task = asyncio.create_task(coord.async_set_cover_position("DEV1", 0))
    await radio.wait_sent(1)
    replacement = CoverMovement(100, 50, 0.0, 10.0)
    coord._cover_movements[("DEV1", 0)] = replacement  # noqa: SLF001
    radio.respond({"header": {"httpStatus": 400}})
    with pytest.raises(HomeAssistantError):
        await task
    assert coord._cover_movements[("DEV1", 0)] is replacement  # noqa: SLF001
