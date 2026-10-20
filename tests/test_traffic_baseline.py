"""Phase 8: reproducible MQTT publish baseline per operation, endpoint and trigger.

These tests freeze the number of MQTT publications the integration emits in
equivalent scenarios, classified by endpoint scope:

* ``gateway_*``: gateway endpoints and the gateway inventory/cache.
* ``device_command`` / ``device_status_query``: ``put/devices/<DEV>/state``,
  which the gateway may turn into EnOcean radio communication.
* ``device_recom``: ReCom configuration, parameter and link-table endpoints.

MQTT publish counts are **not** radio transmission counts. The gateway decides
whether a request is answered from its cache or causes radio traffic, and
hidden firmware effects cannot be observed through an MQTT mock (guardrail A1,
implementation-plan.md section 3). A later phase must never redefine these
numbers upwards to hide additional traffic; an increase needs a reviewed
amendment of the plan.

Set ``OPUS_TRAFFIC_BASELINE_JSON`` to a file path to export the recorded
counts (CI uploads the file as an artifact of the fixed reference job).
"""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from homeassistant.util import dt as dt_util

from custom_components.opus_greennet.coordinator import (
    GATEWAY_HEALTH_INTERVAL,
    OUTBOUND_STATE_RECONCILIATION_DELAYS,
    RECONNECT_RESYNC_DEBOUNCE_SECONDS,
)
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from tests.test_coordinator_transport import UPTIME_RESPONSE
from tests.test_coordinator_transport import broker as broker
from tests.test_coordinator_transport import (
    connected_coordinator as connected_coordinator,
)
from tests.test_reconciliation_feedback import fire_timer, receive_feedback
from tests.test_reconciliation_feedback import timers as timers

EAG = "AABB0011"
PREFIX = f"EnOcean/{EAG}/"
STATUS_QUERY = {"key": "query", "value": "status"}
RECOM_ENDPOINTS = frozenset(
    {
        "configuration",
        "parameters",
        "linkTables",
        "linkTablesMetadata",
        "repeaterState",
    }
)
GATEWAY_SCOPES = frozenset({"gateway_uptime", "gateway_info", "gateway_inventory"})
RADIO_CAPABLE_SCOPES = frozenset(
    {"device_command", "device_status_query", "device_recom"}
)
IDLE_HEALTH_TICKS = 10
RECONNECT_BURST = 5

RECORDED: dict[str, dict[str, int]] = {}


def classify(topic: str, payload: object) -> str:
    """Return the endpoint scope of one published MQTT request."""
    path = topic.removeprefix(PREFIX)
    if path == "get/config/system/uptime":
        return "gateway_uptime"
    if path == "get/config/system/info":
        return "gateway_info"
    if path == "get/devices":
        return "gateway_inventory"
    parts = path.split("/")
    if len(parts) == 4 and parts[1] == "devices" and parts[0] in ("get", "put"):
        endpoint = parts[3]
        if parts[0] == "put" and endpoint == "state":
            functions = json.loads(str(payload))["state"]["functions"]
            if STATUS_QUERY in functions:
                return "device_status_query"
            return "device_command"
        if endpoint in RECOM_ENDPOINTS:
            return "device_recom"
        return f"device_{endpoint}"
    return f"other:{path}"


def counts(published: list[tuple[str, object, dict[str, object]]]) -> Counter[str]:
    """Count published requests per endpoint scope."""
    return Counter(classify(topic, payload) for topic, payload, _ in published)


def take(broker, scenario: str) -> Counter[str]:
    """Record and clear the publications of one scenario."""
    result = counts(broker.published)
    RECORDED[scenario] = dict(sorted(result.items()))
    broker.published.clear()
    return result


@pytest.fixture(scope="module", autouse=True)
def export_baseline():
    """Write the recorded counts once all scenarios of this module ran."""
    yield
    target = os.environ.get("OPUS_TRAFFIC_BASELINE_JSON")
    if not target:
        return
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "note": (
                    "MQTT publish counts from mocked transports. They are not "
                    "EnOcean radio transmission counts."
                ),
                "timing": {
                    "gateway_health_interval_s": (
                        GATEWAY_HEALTH_INTERVAL.total_seconds()
                    ),
                    "reconnect_resync_debounce_s": RECONNECT_RESYNC_DEBOUNCE_SECONDS,
                    "outbound_state_reconciliation_delays_s": list(
                        OUTBOUND_STATE_RECONCILIATION_DELAYS
                    ),
                },
                "scenarios": dict(sorted(RECORDED.items())),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


COMMAND = json.dumps({"state": {"functions": [{"key": "switch", "value": "on"}]}})
QUERY = json.dumps({"state": {"functions": [STATUS_QUERY]}})
CHANNEL_QUERY = json.dumps(
    {"state": {"functions": [{"key": "channel", "value": "1"}, STATUS_QUERY]}}
)


@pytest.mark.parametrize(
    ("path", "payload", "scope"),
    [
        ("get/config/system/uptime", "", "gateway_uptime"),
        ("get/config/system/info", "", "gateway_info"),
        ("get/devices", "", "gateway_inventory"),
        ("put/devices/DEV1/state", COMMAND, "device_command"),
        ("put/devices/DEV1/state", QUERY, "device_status_query"),
        ("put/devices/DEV1/state", CHANNEL_QUERY, "device_status_query"),
        ("get/devices/DEV1/configuration", "", "device_recom"),
        ("put/devices/DEV1/configuration", "{}", "device_recom"),
        ("get/devices/DEV1/parameters", "", "device_recom"),
        ("get/devices/DEV1/linkTables", "", "device_recom"),
        ("get/devices/DEV1/linkTablesMetadata", "", "device_recom"),
        ("get/devices/DEV1/repeaterState", "", "device_recom"),
        ("get/devices/DEV1/profile", "", "device_profile"),
    ],
)
def test_classification_of_every_documented_endpoint(path, payload, scope):
    assert classify(f"{PREFIX}{path}", payload) == scope


def test_frozen_timing_baseline():
    """A1/A3: intervals and reconciliation slots are part of the baseline."""
    assert GATEWAY_HEALTH_INTERVAL.total_seconds() == 60
    assert RECONNECT_RESYNC_DEBOUNCE_SECONDS == 60
    assert OUTBOUND_STATE_RECONCILIATION_DELAYS == (5, 20)


async def test_gateway_lifecycle_traffic(
    broker, connected_coordinator, timers, monkeypatch
):
    """Setup, idle health ticks and a reconnect burst touch only the gateway."""
    broker.response = UPTIME_RESPONSE
    module = "custom_components.opus_greennet.coordinator"
    monkeypatch.setattr(f"{module}.mqtt.async_subscribe_connection_status", MagicMock())
    monkeypatch.setattr(f"{module}.async_track_time_interval", MagicMock())
    coord = connected_coordinator
    try:
        assert await coord.async_setup() is True
        await coord._refresh_task
        assert take(broker, "setup") == Counter(
            {"gateway_uptime": 2, "gateway_inventory": 1, "gateway_info": 1}
        )

        # Ten minutes without any device activity: one probe per tick only.
        for _ in range(IDLE_HEALTH_TICKS):
            coord._async_health_tick(dt_util.utcnow())
            await coord._refresh_task
        assert take(broker, f"idle_{IDLE_HEALTH_TICKS}_health_ticks") == Counter(
            {"gateway_uptime": IDLE_HEALTH_TICKS}
        )

        # A flapping broker inside the debounce window requests one snapshot.
        for _ in range(RECONNECT_BURST):
            coord._handle_connection_status(False)
            coord._handle_connection_status(True)
            await coord._refresh_task
        assert coord.available is True
        assert take(broker, f"reconnect_burst_{RECONNECT_BURST}") == Counter(
            {
                "gateway_uptime": RECONNECT_BURST,
                "gateway_inventory": 1,
                "gateway_info": 1,
            }
        )
        # Gateway health never derives child-device or ReCom requests.
        for scenario in (
            "setup",
            f"idle_{IDLE_HEALTH_TICKS}_health_ticks",
            f"reconnect_burst_{RECONNECT_BURST}",
        ):
            assert set(RECORDED[scenario]) <= GATEWAY_SCOPES
            assert not set(RECORDED[scenario]) & RADIO_CAPABLE_SCOPES
    finally:
        await coord.async_unload()
    broker.assert_clean()


async def test_command_and_reconciliation_traffic(
    broker, connected_coordinator, timers
):
    """One command, then at most the two existing status checks."""
    coord = connected_coordinator
    coord.devices["DEV1"] = EnOceanDevice("DEV1", "Actuator", [{"eep": "D2-01-00"}])
    try:
        await coord.async_turn_on("DEV1")
        assert take(broker, "command_ack") == Counter({"device_command": 1})

        pending = [timer for timer in timers if timer[0] in (5, 20)]
        assert [delay for delay, _, _ in pending] == [5, 20]
        for timer in pending:
            await fire_timer(coord, timer)
        assert take(broker, "reconciliation_without_feedback") == Counter(
            {"device_status_query": 2}
        )

        before = len(timers)
        await coord.async_turn_off("DEV1")
        new = [timer for timer in timers[before:] if timer[0] in (5, 20)]
        assert [delay for delay, _, _ in new] == [5, 20]
        receive_feedback(coord, [{"key": "switch", "value": "off"}])
        assert all(cancel.called for _, _, cancel in new)
        assert take(broker, "command_with_device_feedback") == Counter(
            {"device_command": 1}
        )
    finally:
        await coord.async_unload()
    broker.assert_clean()


async def test_incoming_telegrams_publish_nothing(
    broker, connected_coordinator, timers
):
    """Local parsing of live and retained telegrams never publishes requests."""
    coord = connected_coordinator
    coord.devices["DEV1"] = EnOceanDevice("DEV1", "Actuator", [{"eep": "D2-01-00"}])
    try:
        receive_feedback(coord, [{"key": "switch", "value": "on"}])
        coord._handle_telegram_property_message(
            SimpleNamespace(
                topic=f"{PREFIX}stream/telegram/DEV1/from",
                payload=json.dumps({"functions": [{"key": "switch", "value": "off"}]}),
                retain=True,
            )
        )
        assert not [timer for timer in timers if timer[0] in (5, 20)]
        assert take(broker, "incoming_live_and_retained_telegrams") == Counter()
    finally:
        await coord.async_unload()
    broker.assert_clean()
