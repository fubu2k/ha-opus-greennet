"""The radio journal: what was sent to each device and how the gateway answered.

The journal is memory only. It is filled while a request owns the radio slot
and read by the diagnostics download; nothing in the integration decides on it.
An acknowledgement (``ok``) is not the device's state: only a ``feedback``
entry says that the device reported what a command was waiting for.
"""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.opus_greennet.api import (
    OpusApiError,
    OpusAuthError,
    OpusConnectionError,
    OpusTimeoutError,
)
from custom_components.opus_greennet.coordinator import (
    JOURNAL_VALUE_COUNT,
    JOURNAL_VALUE_LENGTH,
    RADIO_JOURNAL_SIZE,
    _journal_values,
)
from custom_components.opus_greennet.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from tests.fake_gateway import FakeGateway, device_object
from tests.ha_helpers import setup_bridge
from tests.test_coordinator_commands import (
    OK,
)
from tests.test_coordinator_commands import (
    connected_coordinator as connected_coordinator,
)
from tests.test_coordinator_commands import radio as radio

CONFIG_PATH = "/devices/DEV1/configuration"
NO_RESPONSE = {
    "header": {"httpStatus": 504, "message": "no response from device"},
}
MODULE = "custom_components.opus_greennet.coordinator"


def _blind() -> EnOceanDevice:
    return EnOceanDevice("DEV1", "Blind", [{"eep": "D2-05-02"}])


def _document(**values: object) -> dict:
    return {
        "header": {"httpStatus": 200},
        "configuration": {
            "parameters": [
                {"key": key, "value": value} for key, value in values.items()
            ]
        },
    }


# ── values of one entry ────────────────────────────────────────────────────


def test_values_keep_scalars_and_shorten_text() -> None:
    items = [
        {"key": "position", "value": "40"},
        {"key": "verticalMovementTime", "value": 25.42},
        {"key": "flag", "value": True},
        {"key": "empty", "value": None},
        {"key": "long", "value": "x" * (JOURNAL_VALUE_LENGTH + 30)},
        {"key": "nested", "value": {"a": 1}},
        {"key": "list", "value": [1]},
    ]
    assert _journal_values(items) == {
        "position": "40",
        "verticalMovementTime": 25.42,
        "flag": True,
        "empty": None,
        "long": "x" * JOURNAL_VALUE_LENGTH,
        "nested": "<dict>",
        "list": "<list>",
    }


@pytest.mark.parametrize(
    "items", [None, "x", 3, {"key": "a"}, ["x", None, {"value": 1}, {"key": 5}]]
)
def test_malformed_values_are_skipped(items: object) -> None:
    assert _journal_values(items) == {}


def test_the_number_of_values_is_bounded() -> None:
    items = [{"key": f"k{index}", "value": index} for index in range(100)]
    assert len(_journal_values(items)) == JOURNAL_VALUE_COUNT


# ── configuration write and read-back ──────────────────────────────────────


async def test_a_configuration_write_and_its_read_back_are_recorded(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    coord.devices["DEV1"] = _blind()
    radio.response = _document(verticalMovementTime=25.42)

    await coord.async_set_device_parameter(
        "DEV1", "verticalMovementTime", 25.42, step=0.01, minimum=0
    )

    write, read = coord.radio_journal()
    assert (write["request"], write["outcome"], write["priority"]) == (
        "configuration_write",
        "ok",
        "query",
    )
    assert write["values"] == {"verticalMovementTime": 25.42}
    assert write["gateway_status"] == 200
    assert write["message"] is None
    assert (read["request"], read["outcome"]) == ("configuration_read", "ok")
    assert "values" not in read
    assert write["device_id"] == read["device_id"] == "DEV1"
    assert read["seq"] == write["seq"] + 1
    for entry in (write, read):
        assert entry["duration_ms"] >= 0
        assert entry["waited_ms"] >= 0
        assert entry["at"]


async def test_a_504_on_the_write_is_recorded_and_not_read_back(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    coord.devices["DEV1"] = _blind()
    radio.response = NO_RESPONSE

    with pytest.raises(HomeAssistantError):
        await coord.async_set_device_parameter(
            "DEV1", "verticalMovementTime", 25.0, step=0.01, minimum=0
        )

    (entry,) = coord.radio_journal()
    assert entry["request"] == "configuration_write"
    assert entry["outcome"] == "rejected"
    assert entry["gateway_status"] == 504
    assert entry["message"] == "status 504: no response from device"
    assert entry["values"] == {"verticalMovementTime": 25.0}
    assert [method for method, _, _ in radio.sent] == ["PUT"]


async def test_a_504_on_the_read_back_is_told_apart_from_the_write(
    radio, connected_coordinator
) -> None:
    """The write was accepted; the device then did not answer the read-back."""
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _blind()
    device.get_or_create_channel().vertical_movement_time = 25.51
    radio.auto_respond = False

    task = asyncio.create_task(
        coord.async_set_device_parameter(
            "DEV1", "verticalMovementTime", 25.42, step=0.01, minimum=0
        )
    )
    await radio.wait_sent(1)
    radio.respond(OK)
    await radio.wait_sent(2)
    radio.respond(NO_RESPONSE)
    with pytest.raises(HomeAssistantError):
        await task

    write, read = coord.radio_journal()
    assert (write["request"], write["outcome"]) == ("configuration_write", "ok")
    assert (read["request"], read["outcome"]) == ("configuration_read", "rejected")
    assert read["gateway_status"] == 504
    # Nothing is repeated, and the shown value is the previous one.
    assert [method for method, _, _ in radio.sent] == ["PUT", "GET"]
    assert device.channels[0].vertical_movement_time == 25.51


async def test_a_timeout_is_recorded(radio, connected_coordinator) -> None:
    coord = connected_coordinator
    radio.response = OpusTimeoutError("PUT /devices/DEV1/configuration timed out")

    with pytest.raises(HomeAssistantError) as error:
        await coord.async_set_device_configuration(
            "DEV1", {"configuration": {"parameters": [{"key": "a", "value": 1}]}}
        )

    assert error.value.translation_key == "request_timeout"
    (entry,) = coord.radio_journal()
    assert entry["outcome"] == "timeout"
    assert entry["gateway_status"] is None
    assert "timed out" in entry["message"]
    assert entry["values"] == {"a": 1}


@pytest.mark.parametrize(
    ("error", "outcome", "translation_key"),
    [
        (OpusConnectionError("PUT failed"), "connection_error", "gateway_unavailable"),
        (OpusAuthError("status 401"), "auth_rejected", "request_rejected"),
        (OpusApiError("unexpected"), "error", "request_rejected"),
    ],
)
async def test_other_failures_are_recorded_by_kind(
    radio, connected_coordinator, error, outcome, translation_key
) -> None:
    radio.response = error

    with pytest.raises(HomeAssistantError) as raised:
        await connected_coordinator.async_turn_on("DEV1")

    assert raised.value.translation_key == translation_key
    (entry,) = connected_coordinator.radio_journal()
    assert entry["outcome"] == outcome
    assert entry["message"] == str(error)


async def test_a_cancelled_request_is_recorded_as_unclear(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    radio.auto_respond = False
    task = asyncio.create_task(coord.async_turn_on("DEV1"))
    await radio.wait_sent(1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    (entry,) = coord.radio_journal()
    assert (entry["request"], entry["outcome"]) == ("command", "cancelled")
    assert [method for method, _, _ in radio.sent] == ["PUT"]


async def test_a_malformed_configuration_body_is_recorded_without_values(
    radio, connected_coordinator
) -> None:
    await connected_coordinator.async_set_device_configuration(
        "DEV1", {"configuration": "x"}
    )
    (entry,) = connected_coordinator.radio_journal()
    assert entry["request"] == "configuration_write"
    assert "values" not in entry


# ── commands and queries ───────────────────────────────────────────────────


async def test_a_command_and_a_status_query_are_recorded(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator

    await coord.async_turn_on("DEV1")
    await coord.async_query_device_status("DEV1")

    command, query = coord.radio_journal()
    assert (command["request"], command["priority"]) == ("command", "command")
    assert command["values"] == {"switch": "on"}
    assert command["outcome"] == "ok"
    assert (query["request"], query["priority"]) == ("status_query", "query")
    assert query["values"] == {"query": "status"}


async def test_a_command_stored_for_a_sleeping_device_is_deferred(
    radio, connected_coordinator
) -> None:
    radio.response = {"header": {"httpStatus": 201}}

    await connected_coordinator.async_turn_on("DEV1")

    (entry,) = connected_coordinator.radio_journal()
    assert entry["outcome"] == "deferred"
    assert entry["gateway_status"] == 201


async def test_a_rejected_command_keeps_the_gateway_text(
    radio, connected_coordinator
) -> None:
    radio.response = {"header": {"httpStatus": 400, "message": "no matching group"}}

    with pytest.raises(HomeAssistantError):
        await connected_coordinator.async_turn_on("DEV1")

    (entry,) = connected_coordinator.radio_journal()
    assert entry["outcome"] == "rejected"
    assert entry["gateway_status"] == 400
    assert entry["message"] == "status 400: no matching group"


async def test_a_request_that_is_never_sent_leaves_no_entry(
    radio, connected_coordinator
) -> None:
    connected_coordinator._set_gateway_available(False)

    with pytest.raises(HomeAssistantError):
        await connected_coordinator.async_turn_on("DEV1")

    assert radio.sent == []
    assert connected_coordinator.radio_journal() == []


async def test_reading_the_journal_sends_nothing_and_returns_a_copy(
    radio, connected_coordinator
) -> None:
    await connected_coordinator.async_turn_on("DEV1")
    sent = list(radio.sent)

    journal = connected_coordinator.radio_journal()
    journal[0]["outcome"] = "changed"
    journal.clear()

    assert radio.sent == sent
    assert connected_coordinator.radio_journal()[0]["outcome"] == "ok"


# ── feedback ───────────────────────────────────────────────────────────────


async def test_feedback_is_recorded_only_for_what_a_command_waits_for(
    connected_coordinator,
) -> None:
    coord = connected_coordinator
    functions = [{"key": "switch", "value": "on"}]

    coord._record_confirmed_feedback("DEV1", functions)
    assert coord.radio_journal() == []

    coord._pending_reconciliation_fields[("DEV1", 0)] = {"switch", "dimmer"}
    coord._record_confirmed_feedback("DEV1", functions)

    (entry,) = coord.radio_journal()
    assert entry["request"] == "feedback"
    assert entry["outcome"] == "confirmed"
    assert entry["channel"] == 0
    assert entry["fields"] == ["switch"]


# ── bounds ─────────────────────────────────────────────────────────────────


async def test_the_journal_drops_the_oldest_entries(connected_coordinator) -> None:
    coord = connected_coordinator

    for index in range(RADIO_JOURNAL_SIZE + 5):
        coord._journal("DEV1", "command", index=index)

    journal = coord.radio_journal()
    assert len(journal) == RADIO_JOURNAL_SIZE
    assert journal[0]["index"] == 5
    assert journal[-1]["seq"] == RADIO_JOURNAL_SIZE + 5


# ── debug log ──────────────────────────────────────────────────────────────


async def test_the_debug_log_names_request_outcome_and_gateway_text(
    radio, connected_coordinator, caplog: pytest.LogCaptureFixture
) -> None:
    connected_coordinator.devices["DEV1"] = _blind()
    radio.response = NO_RESPONSE
    caplog.set_level(logging.DEBUG, logger=MODULE)

    with pytest.raises(HomeAssistantError):
        await connected_coordinator.async_set_device_parameter(
            "DEV1", "verticalMovementTime", 25.0, step=0.01, minimum=0
        )

    # The write really went out; otherwise there is nothing to log.
    assert [method for method, _, _ in radio.sent] == ["PUT"]
    line = next(
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("OPUS radio request")
    )
    assert "request=configuration_write" in line
    assert "outcome=rejected" in line
    assert "gateway_status=504" in line
    assert "'status 504: no response from device'" in line
    assert "'verticalMovementTime': 25.0" in line


# ── diagnostics download ───────────────────────────────────────────────────


async def test_the_download_lists_the_journal_per_device_and_stays_redacted(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [
        device_object("DEV1", "D2-01-01"),
        device_object("DEV2", "D2-01-01"),
    ]
    entry = await setup_bridge(hass, gateway)
    coordinator = entry.runtime_data.coordinator
    gateway.responses["/devices/DEV1/state"] = {
        "header": {
            "httpStatus": 504,
            "message": f"no response from device DEV1 via {gateway.eurid}",
        }
    }

    with pytest.raises(HomeAssistantError):
        await coordinator.async_turn_on("DEV1")
    await coordinator.async_turn_on("DEV2")
    requests_before = len(gateway.requests)

    result = await async_get_config_entry_diagnostics(hass, entry)

    # Only the cache is read: no request.
    assert len(gateway.requests) == requests_before
    first, second = result["devices"]
    (failed,) = first["radio_journal"]
    (accepted,) = second["radio_journal"]
    assert (failed["request"], failed["outcome"], failed["gateway_status"]) == (
        "command",
        "rejected",
        504,
    )
    assert failed["values"] == {"switch": "on"}
    assert accepted["outcome"] == "ok"
    assert failed["seq"] < accepted["seq"]
    # The block already says which device it belongs to.
    assert "device_id" not in failed
    text = json.dumps(result)
    assert "DEV1" not in text
    assert "DEV2" not in text
    assert gateway.eurid not in text
    assert gateway.config["password"] not in text


async def test_the_download_without_a_journal_still_works() -> None:
    """A coordinator double without the method leaves the block out."""
    coordinator = MagicMock(spec=["eag_id", "available", "devices", "gateway_info"])
    coordinator.eag_id = "AABB0011"
    coordinator.available = True
    coordinator.devices = {"DEV1": _blind()}
    coordinator.gateway_info = {}
    coordinator.gateway_uptime = None
    entry = MagicMock()
    entry.entry_id = "e"
    entry.title = "t"
    entry.data = {}
    entry.runtime_data.coordinator = coordinator

    result = await async_get_config_entry_diagnostics(MagicMock(), entry)

    assert "radio_journal" not in result["devices"][0]
