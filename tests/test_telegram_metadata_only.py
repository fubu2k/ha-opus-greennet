"""State semantics D3: a ``from`` telegram without a usable function.

``_handle_telegram`` returns before the device is touched when no function is
left. That also drops the telegram's ``dbm`` and timestamp. This file pins the
behaviour so a change is a decision, not an accident. Decision (E): kept as it
is. No such telegram was ever observed on hardware (phase 2, D5 runs and the
25 Oct 2026 observations: every telegram carried a function), the 30-minute
device list refresh restores ``lastSeen`` and ``dbm``, and
``test_ignores_incomplete_functions`` already pins the silence.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from custom_components.opus_greennet.coordinator import OpusGreenNetCoordinator
from custom_components.opus_greennet.enocean_device import EnOceanDevice

MODULE = "custom_components.opus_greennet.coordinator"
TIMESTAMP = "2026-10-26T08:00:00.000+0100"


@pytest.fixture
def coord() -> OpusGreenNetCoordinator:
    coordinator = OpusGreenNetCoordinator(MagicMock(), MagicMock(), "AABB0011")
    coordinator._gateway_available = True
    return coordinator


def _telegram(functions: list[object]) -> dict[str, object]:
    return {
        "deviceId": "DEV1",
        "direction": "from",
        "timestamp": TIMESTAMP,
        "telegramInfo": {"rorg": "D2", "dbm": -48},
        "functions": functions,
    }


@pytest.mark.parametrize(
    "functions",
    [[], [{"key": "switch"}], [{"key": "switch", "value": None}], ["junk"]],
    ids=["empty", "no-value", "null-value", "not-an-object"],
)
def test_signal_and_time_of_a_telegram_without_function_are_not_taken(
    coord, functions
) -> None:
    device = EnOceanDevice("DEV1", "Switch", [{"eep": "D2-01-01"}])
    device.dbm = -70
    device.last_seen = "2026-10-26T07:00:00.000+0100"
    coord.devices["DEV1"] = device

    with patch(f"{MODULE}.async_dispatcher_send") as dispatch:
        coord._handle_telegram(_telegram(functions))

    assert device.dbm == -70
    assert device.last_seen == "2026-10-26T07:00:00.000+0100"
    dispatch.assert_not_called()


def test_the_same_telegram_with_a_function_updates_signal_and_time(coord) -> None:
    """Control: with a usable function both are taken."""
    device = EnOceanDevice("DEV1", "Switch", [{"eep": "D2-01-01"}])
    device.dbm = -70
    coord.devices["DEV1"] = device

    with patch(f"{MODULE}.async_dispatcher_send"):
        coord._handle_telegram(_telegram([{"key": "switch", "value": "on"}]))

    assert device.dbm == -48
    assert device.last_seen == TIMESTAMP
