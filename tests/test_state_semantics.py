"""Boundaries of state input, freshness and entity writes.

These tests pin behaviour that a later notification optimisation must keep:
what counts as a device report, what only counts as freshness, and which
repeated values an entity may leave unwritten.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity

from custom_components.opus_greennet.coordinator import OpusGreenNetCoordinator
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from tests.fake_gateway import FakeGateway, device_object, telegram
from tests.ha_helpers import setup_bridge, wait_for_entity, wait_until

MODULE = "custom_components.opus_greennet.coordinator"
FIRST = "2026-10-19T18:06:03.990+0200"
SECOND = "2026-10-19T18:06:04.990+0200"
SWITCH = "050BF714"


@pytest.fixture
def coord() -> OpusGreenNetCoordinator:
    coordinator = OpusGreenNetCoordinator(MagicMock(), MagicMock(), "AABB0011")
    coordinator._gateway_available = True
    return coordinator


@pytest.fixture
def dispatch() -> Iterator[MagicMock]:
    with patch(f"{MODULE}.async_dispatcher_send") as mock:
        yield mock


def _device(coord: OpusGreenNetCoordinator, eep: str) -> EnOceanDevice:
    device = EnOceanDevice("DEV1", "Device DEV1", [{"eep": eep}])
    coord.devices["DEV1"] = device
    return device


def _report(functions, timestamp: str = FIRST, **fields):
    return {
        "deviceId": "DEV1",
        "direction": "from",
        "timestamp": timestamp,
        "functions": functions,
        **fields,
    }


# ── Input classes: missing, null, unknown, invalid, additive ───────────────


@pytest.mark.parametrize("value", ["unknown", "abc", "", 101, -1, True, 40.5], ids=repr)
def test_unusable_position_keeps_the_reading_but_counts_as_an_update(
    coord, dispatch, value
) -> None:
    """A present but unusable value is no reading, yet the report arrived."""
    device = _device(coord, "D2-05-02")
    channel = device.get_or_create_channel(0)
    channel.position = 40
    revision = channel.state_revision

    coord._handle_telegram(_report([{"key": "position", "value": value}]))

    assert channel.position == 40
    assert channel.state_revision == revision + 1
    assert device.last_seen == FIRST
    dispatch.assert_called_once()


@pytest.mark.parametrize(
    "function",
    [{"key": "position"}, {"key": "position", "value": None}],
    ids=["missing", "null"],
)
def test_missing_or_null_value_is_not_a_report(coord, dispatch, function) -> None:
    """Without a value there is nothing to apply, to confirm or to announce."""
    device = _device(coord, "D2-05-02")
    channel = device.get_or_create_channel(0)
    channel.position = 40
    revision = channel.state_revision

    coord._handle_telegram(_report([function]))

    assert channel.position == 40
    assert channel.state_revision == revision
    assert device.last_seen == ""
    dispatch.assert_not_called()


def test_additive_unknown_function_neither_blocks_nor_changes_known_state(
    coord, dispatch
) -> None:
    device = _device(coord, "D2-05-02")
    channel = device.get_or_create_channel(0)
    channel.position = 40

    coord._handle_telegram(
        _report(
            [
                {"key": "futureFeature", "value": "x"},
                {"key": "position", "value": "60"},
            ]
        )
    )

    assert channel.position == 60
    dispatch.assert_called_once()

    coord._handle_telegram(_report([{"key": "futureFeature", "value": "y"}]))

    assert channel.position == 60
    assert dispatch.call_count == 2


# ── Freshness is not a value ───────────────────────────────────────────────


def test_identical_reports_stay_separate_updates(coord, dispatch) -> None:
    """Equal values do not make a report redundant: time and signal advance."""
    device = _device(coord, "D2-01-02")
    coord._handle_telegram(
        _report(
            [{"key": "dimValue", "value": "50"}],
            FIRST,
            telegramInfo={"dbm": -61},
        )
    )
    channel = device.channels[0]
    revision = channel.state_revision

    coord._handle_telegram(
        _report(
            [{"key": "dimValue", "value": "50"}],
            SECOND,
            telegramInfo={"dbm": -55},
        )
    )

    assert channel.brightness == 50
    assert channel.state_revision == revision + 1
    assert device.last_seen == SECOND
    assert device.dbm == -55
    assert dispatch.call_count == 2


def test_any_report_on_a_channel_supersedes_a_pending_estimate(coord, dispatch) -> None:
    """The estimate guard is per channel: it yields to every newer report.

    This is deliberately conservative. A coalescing layer must not make the
    guard coarser, and a finer guard needs its own decision.
    """
    device = _device(coord, "D2-01-02")
    device.update_from_telegram({"functions": [{"key": "switch", "value": "off"}]})
    snapshot = device.snapshot_channel_state(0)

    coord._handle_telegram(_report([{"key": "power", "value": 12.5}]))

    assert not device.apply_switch_estimate(snapshot, True)
    assert device.channels[0].is_on is False


# ── Entity writes after a coordinator dispatch ─────────────────────────────


@pytest.fixture
def writes() -> Iterator[dict[str, int]]:
    counts: dict[str, int] = {}
    original = Entity._async_write_ha_state

    def counting(self):
        counts[self.entity_id] = counts.get(self.entity_id, 0) + 1
        return original(self)

    with patch.object(Entity, "_async_write_ha_state", counting):
        yield counts


async def test_repeated_value_is_not_rewritten_but_a_change_is(
    hass: HomeAssistant, gateway: FakeGateway, writes: dict[str, int]
) -> None:
    """The only suppression today: same visible state, same attributes.

    The coordinator still processes the report (see
    ``test_identical_reports_stay_separate_updates``); only the entity skips the
    write. Home Assistant's ``last_reported`` therefore stays where it was.
    """
    gateway.devices = [
        device_object(SWITCH, "D2-01-01", states=[{"key": "switch", "value": "off"}])
    ]
    entry = await setup_bridge(hass, gateway)
    coordinator = entry.runtime_data.coordinator
    switch = await wait_for_entity(hass, "switch", f"AABB0011_{SWITCH}")

    await gateway.push(telegram(SWITCH, [{"key": "switch", "value": "on"}]))
    await wait_until(lambda: hass.states.get(switch).state == "on")
    await hass.async_block_till_done()
    written = writes[switch]
    reported = hass.states.get(switch).last_reported

    await gateway.push(
        telegram(
            SWITCH,
            [{"key": "switch", "value": "on"}],
            timestamp="2026-10-19T18:00:02.000+0200",
        )
    )
    await wait_until(
        lambda: coordinator.devices[SWITCH].last_seen == "2026-10-19T18:00:02.000+0200"
    )
    await hass.async_block_till_done()
    assert writes[switch] == written
    assert hass.states.get(switch).last_reported == reported

    await gateway.push(telegram(SWITCH, [{"key": "switch", "value": "off"}]))
    await wait_until(lambda: hass.states.get(switch).state == "off")
    assert writes[switch] > written
