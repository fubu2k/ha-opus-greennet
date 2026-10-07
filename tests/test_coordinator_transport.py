"""Exercise MQTT handshakes, acknowledgements, and lifecycle ownership."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.opus_greennet.coordinator import OpusGreenNetCoordinator
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from custom_components.opus_greennet.mqtt_transport import (
    MQTTRequestManager,
    async_probe_gateway,
    async_wait_for_subscriptions,
)

UPTIME_RESPONSE = {
    "header": {
        "httpStatus": 200,
        "content": "Uptime",
        "gateway": "OPUS-GW v1.0.0",
        "timestamp": "2026-01-01T12:00:00+0000",
    },
    "systemUptimeResponse": {"uptime": 3600},
}


class Broker:
    """A deterministic broker with separate SUBACK and application responses."""

    def __init__(self):
        self.connected = True
        self.auto_ack = True
        self.auto_respond = True
        self.response = {"header": {"httpStatus": 200}}
        self.responses = {}
        self.subscriptions = {}
        self.acknowledgements = {}
        self.published = []
        self.events = []

    async def subscribe(self, hass, topic, callback, qos=1):
        self.subscriptions.setdefault(topic, []).append(callback)
        self.events.append(("subscribe", topic))

        def unsubscribe():
            self.subscriptions[topic].remove(callback)

        return unsubscribe

    def on_subscribe_done(self, hass, topic, qos, callback):
        self.acknowledgements.setdefault(topic, []).append(callback)
        if self.auto_ack:
            callback()
            self.events.append(("suback", topic))

        def unsubscribe():
            self.acknowledgements[topic].remove(callback)

        return unsubscribe

    async def publish(self, hass, topic, payload, **kwargs):
        self.published.append((topic, payload, kwargs))
        self.events.append(("publish", topic))
        if self.auto_respond:
            response = self.responses.get(topic, self.response)
            if response is None:
                return
            answer = topic.replace("/get/", "/getAnswer/").replace(
                "/put/", "/putAnswer/"
            )
            self.receive(answer, response)

    def receive(self, topic, payload, *, retain=False):
        if not isinstance(payload, (str, bytes)):
            payload = json.dumps(payload)
        message = SimpleNamespace(topic=topic, payload=payload, retain=retain)
        for callback in list(self.subscriptions.get(topic, [])):
            callback(message)

    def assert_clean(self):
        assert all(not callbacks for callbacks in self.subscriptions.values())
        assert all(not callbacks for callbacks in self.acknowledgements.values())


@pytest.fixture
def broker(monkeypatch):
    broker = Broker()
    module = "custom_components.opus_greennet.mqtt_transport.mqtt"
    monkeypatch.setattr(f"{module}.is_connected", lambda hass: broker.connected)
    monkeypatch.setattr(f"{module}.async_subscribe", broker.subscribe)
    monkeypatch.setattr(f"{module}.async_on_subscribe_done", broker.on_subscribe_done)
    monkeypatch.setattr(f"{module}.async_publish", broker.publish)
    return broker


@pytest.fixture
def connected_coordinator(broker):
    hass = MagicMock()
    hass.async_create_task.side_effect = asyncio.create_task
    coord = OpusGreenNetCoordinator(hass, "AABB0011")
    coord._gateway_available = True
    return coord


@pytest.mark.parametrize("status", [200, 201])
async def test_command_requires_accepted_gateway_ack(
    broker, connected_coordinator, status
):
    broker.response = {"header": {"httpStatus": status}}
    await connected_coordinator.async_turn_on("DEV1")
    topic, payload, options = broker.published[0]
    assert topic == "EnOcean/AABB0011/put/devices/DEV1/state"
    assert json.loads(payload) == {
        "state": {"functions": [{"key": "switch", "value": "on"}]}
    }
    assert options == {"qos": 1, "retain": False}
    assert [event[0] for event in broker.events] == ["subscribe", "suback", "publish"]
    broker.assert_clean()


@pytest.mark.parametrize(
    "response",
    [
        {"header": {"httpStatus": 400}},
        {"header": {"httpStatus": 500}},
        {"header": {"httpStatus": True}},
        {"header": {"httpStatus": 200.9}},
        {"wrong": "shape"},
        [],
        "not json",
    ],
)
async def test_rejected_or_malformed_ack_fails_action(
    broker, connected_coordinator, response
):
    broker.response = response
    with pytest.raises(HomeAssistantError) as raised:
        await connected_coordinator.async_turn_on("DEV1")
    assert raised.value.translation_key == "request_rejected"
    broker.assert_clean()


async def test_subscription_handshake_precedes_command(broker, connected_coordinator):
    broker.auto_ack = False
    task = asyncio.create_task(connected_coordinator.async_turn_on("DEV1"))
    await asyncio.sleep(0)
    assert broker.published == []
    callbacks = broker.acknowledgements["EnOcean/AABB0011/putAnswer/devices/DEV1/state"]
    callbacks[0]()
    await task
    assert len(broker.published) == 1
    broker.assert_clean()


async def test_no_ack_times_out_without_subscription_leak(
    broker, connected_coordinator, monkeypatch
):
    monkeypatch.setattr(
        "custom_components.opus_greennet.mqtt_transport.REQUEST_TIMEOUT", 0.01
    )
    broker.auto_respond = False
    with pytest.raises(HomeAssistantError) as raised:
        await connected_coordinator.async_turn_on("DEV1")
    assert raised.value.translation_key == "request_timeout"
    broker.assert_clean()


async def test_retained_ack_does_not_complete_command(broker, connected_coordinator):
    broker.auto_respond = False
    task = asyncio.create_task(connected_coordinator.async_turn_on("DEV1"))
    await asyncio.sleep(0)
    answer = "EnOcean/AABB0011/putAnswer/devices/DEV1/state"
    broker.receive(answer, {"header": {"httpStatus": 200}}, retain=True)
    await asyncio.sleep(0)
    assert not task.done()
    broker.receive(answer, {"header": {"httpStatus": 200}})
    await task
    broker.assert_clean()


async def test_same_device_commands_are_serialized(broker, connected_coordinator):
    broker.auto_respond = False
    first = asyncio.create_task(connected_coordinator.async_turn_on("DEV1"))
    second = asyncio.create_task(connected_coordinator.async_turn_off("DEV1"))
    await asyncio.sleep(0)
    assert len(broker.published) == 1
    answer = "EnOcean/AABB0011/putAnswer/devices/DEV1/state"
    broker.receive(answer, {"header": {"httpStatus": 200}})
    await first
    await asyncio.sleep(0)
    assert len(broker.published) == 2
    assert not second.done()
    broker.receive(answer, {"header": {"httpStatus": 201}})
    await second
    broker.assert_clean()


async def test_bridge_loss_cancels_active_and_queued_commands(
    broker, connected_coordinator
):
    broker.auto_respond = False
    first = asyncio.create_task(connected_coordinator.async_turn_on("DEV1"))
    second = asyncio.create_task(connected_coordinator.async_turn_off("DEV1"))
    await asyncio.sleep(0)
    connected_coordinator._handle_bridge_status(SimpleNamespace(payload="0"))
    # Even if the bridge immediately recovers, an older queued operation must fail.
    connected_coordinator._bridge_connected = True
    connected_coordinator._gateway_available = True
    results = await asyncio.gather(first, second, return_exceptions=True)
    assert all(isinstance(result, HomeAssistantError) for result in results)
    assert len(broker.published) == 1
    broker.assert_clean()


@pytest.mark.parametrize(
    "operation",
    [
        "async_get_device_configuration",
        "async_get_device_parameters",
        "async_get_device_profile",
    ],
)
async def test_unload_cancels_request_and_subscription(
    broker, connected_coordinator, operation
):
    broker.auto_respond = False
    task = asyncio.create_task(getattr(connected_coordinator, operation)("DEV1"))
    await asyncio.sleep(0)
    await connected_coordinator.async_unload()
    with pytest.raises(HomeAssistantError) as raised:
        await task
    assert raised.value.translation_key == "request_cancelled"
    broker.assert_clean()


async def test_unload_during_suback_wait_cleans_both_waiters(
    broker, connected_coordinator
):
    broker.auto_ack = False
    task = asyncio.create_task(
        connected_coordinator.async_get_device_parameters("DEV1")
    )
    await asyncio.sleep(0)
    await connected_coordinator.async_unload()
    with pytest.raises(HomeAssistantError) as raised:
        await task
    assert raised.value.translation_key == "request_cancelled"
    assert not broker.published
    broker.assert_clean()


@pytest.mark.parametrize(
    "broker_connected,gateway_available", [(False, True), (True, False)]
)
async def test_offline_commands_are_never_published(
    broker, connected_coordinator, broker_connected, gateway_available
):
    broker.connected = broker_connected
    connected_coordinator._gateway_available = gateway_available
    with pytest.raises(HomeAssistantError):
        await connected_coordinator.async_turn_on("DEV1")
    assert not broker.published


async def test_configuration_write_requires_ack(broker, connected_coordinator):
    broker.response = {"header": {"httpStatus": 400}}
    with pytest.raises(HomeAssistantError) as raised:
        await connected_coordinator.async_set_device_configuration(
            "DEV1", {"repeater": 1}
        )
    assert raised.value.translation_key == "request_rejected"
    assert broker.published[0][0].endswith("/put/devices/DEV1/configuration")
    broker.assert_clean()


@pytest.mark.parametrize(
    "response", [[], "broken json", {"header": {"httpStatus": 404}}]
)
async def test_read_response_validation(broker, connected_coordinator, response):
    broker.response = response
    with pytest.raises(HomeAssistantError) as raised:
        await connected_coordinator.async_get_device_parameters("DEV1")
    assert raised.value.translation_key == "request_rejected"
    broker.assert_clean()


async def test_probe_verifies_gateway_and_cleans_subscription(broker):
    broker.response = UPTIME_RESPONSE
    result = await async_probe_gateway(MagicMock(), "AABB0011")
    assert result == broker.response
    assert broker.published[0][0] == "EnOcean/AABB0011/get/config/system/uptime"
    assert len(broker.published) == 1
    broker.assert_clean()


@pytest.mark.parametrize("uptime", [None, True, -1, "NaN", "inf", "invalid", {}])
async def test_probe_rejects_invalid_uptime_response(broker, uptime):
    broker.response = {
        "header": {"httpStatus": 200},
        "systemUptimeResponse": {"uptime": uptime},
    }
    with pytest.raises(HomeAssistantError) as raised:
        await async_probe_gateway(MagicMock(), "AABB0011")
    assert raised.value.translation_key == "gateway_unavailable"
    broker.assert_clean()


async def test_health_uses_uptime_without_waiting_for_optional_system_info(
    broker, connected_coordinator
):
    broker.responses = {
        "EnOcean/AABB0011/get/config/system/uptime": UPTIME_RESPONSE,
        "EnOcean/AABB0011/get/config/system/info": None,
    }
    await connected_coordinator._async_refresh_gateway(resync=True)
    assert connected_coordinator.available is True
    assert connected_coordinator.gateway_uptime == "3600"
    assert [entry[0] for entry in broker.published] == [
        "EnOcean/AABB0011/get/config/system/uptime",
        "EnOcean/AABB0011/get/devices",
        "EnOcean/AABB0011/get/config/system/info",
    ]
    # Regular health checks only use the responsive endpoint, so firmware that
    # omits system/info never incurs its former application-response timeout.
    broker.published.clear()
    await connected_coordinator._async_refresh_gateway(resync=False)
    assert [entry[0] for entry in broker.published] == [
        "EnOcean/AABB0011/get/config/system/uptime"
    ]
    await connected_coordinator.async_unload()
    broker.assert_clean()


async def test_startup_probes_before_retained_streams_and_discovers_after_suback(
    broker, connected_coordinator, monkeypatch
):
    broker.response = UPTIME_RESPONSE
    module = "custom_components.opus_greennet.coordinator"
    monkeypatch.setattr(f"{module}.mqtt.async_subscribe_connection_status", MagicMock())
    monkeypatch.setattr(f"{module}.async_track_time_interval", MagicMock())
    monkeypatch.setattr(f"{module}.async_call_later", MagicMock())

    assert await connected_coordinator.async_setup() is True
    # Setup returns after the probe; stream subscriptions run in phase 2.
    await connected_coordinator._refresh_task
    first_probe = broker.events.index(
        (
            "publish",
            "EnOcean/AABB0011/get/config/system/uptime",
        )
    )
    discovery = broker.events.index(("publish", "EnOcean/AABB0011/get/devices"))
    wildcard_subscriptions = [
        (index, topic)
        for index, (event, topic) in enumerate(broker.events)
        if event == "subscribe" and "#" in topic
    ]
    assert wildcard_subscriptions
    for index, topic in wildcard_subscriptions:
        # Retained-heavy filters start only after the setup probe answered.
        assert first_probe < index
        assert broker.events.index(("suback", topic)) < discovery
    # Heavy filters are staggered: each SUBACK precedes the next SUBSCRIBE.
    for (_, earlier), (later_index, _) in zip(
        wildcard_subscriptions, wildcard_subscriptions[1:], strict=False
    ):
        assert broker.events.index(("suback", earlier)) < later_index
    assert (
        sum(
            event == ("publish", "EnOcean/AABB0011/get/config/system/uptime")
            for event in broker.events
        )
        == 2
    )
    await connected_coordinator.async_unload()
    broker.assert_clean()


async def test_bulk_subscription_wait_has_independent_longer_deadline(
    broker, monkeypatch
):
    monkeypatch.setattr(
        "custom_components.opus_greennet.mqtt_transport.REQUEST_TIMEOUT", 0.01
    )
    monkeypatch.setattr(
        "custom_components.opus_greennet.mqtt_transport.SUBSCRIPTION_TIMEOUT", 0.1
    )
    broker.auto_ack = False
    task = asyncio.create_task(
        async_wait_for_subscriptions(MagicMock(), ["retained/#"])
    )
    await asyncio.sleep(0.025)
    assert not task.done()
    broker.acknowledgements["retained/#"][0]()
    await task
    broker.assert_clean()


async def test_bulk_subscription_timeout_remains_bounded_and_cleans_listeners(
    broker, monkeypatch
):
    monkeypatch.setattr(
        "custom_components.opus_greennet.mqtt_transport.SUBSCRIPTION_TIMEOUT", 0.01
    )
    broker.auto_ack = False
    with pytest.raises(TimeoutError):
        await async_wait_for_subscriptions(MagicMock(), ["retained/#"])
    broker.assert_clean()


async def test_reconnect_waiter_starts_after_ha_queues_resubscriptions(
    hass, broker, monkeypatch
):
    """HA synchronously announces connect before marking subscriptions pending."""
    coord = OpusGreenNetCoordinator(hass, "AABB0011")
    coord._started = True
    stream_topic = "EnOcean/AABB0011/stream/devices/#"
    coord._subscription_topics = [stream_topic]
    # Pretend phase 2 already subscribed the heavy stream filters; the
    # light control topics are (re)subscribed by the refresh itself.
    coord._stream_topics = [
        "EnOcean/AABB0011/getAnswer/devices/#",
        stream_topic,
        "EnOcean/AABB0011/stream/device/#",
        "EnOcean/AABB0011/stream/telegram/#",
    ]
    broker.response = UPTIME_RESPONSE
    pending = False
    waiting = []
    observations = []

    def subscription_done(hass, topic, qos, callback):
        observations.append((topic, pending))
        if pending:
            waiting.append(callback)
            return lambda: waiting.remove(callback)
        # Model async_on_subscribe_done's already-active fast path exactly:
        # readiness is queued now and does not recheck later pending flags.
        return hass.loop.call_soon(callback).cancel

    monkeypatch.setattr(
        "custom_components.opus_greennet.mqtt_transport.mqtt.async_on_subscribe_done",
        subscription_done,
    )
    coord._handle_connection_status(True)
    assert observations == []
    pending = True  # HA queues wildcard resubscriptions after the callback.
    await asyncio.sleep(0)
    assert observations
    assert all(is_pending for _, is_pending in observations)
    assert not broker.published

    pending = False
    for callback in waiting.copy():
        callback()
    await coord._refresh_task
    assert coord.available is True
    assert broker.published[0][0].endswith("/get/config/system/uptime")
    await coord.async_unload()
    broker.assert_clean()


def test_system_uptime_callback_extracts_response(connected_coordinator):
    connected_coordinator._handle_system_uptime(
        SimpleNamespace(
            payload=json.dumps(UPTIME_RESPONSE).encode(),
        )
    )
    assert connected_coordinator.gateway_uptime == "3600"


async def test_publish_exception_is_propagated_and_cleaned(
    broker, connected_coordinator, monkeypatch
):
    async def fail(*args, **kwargs):
        raise RuntimeError("MQTT publish failed")

    monkeypatch.setattr(
        "custom_components.opus_greennet.mqtt_transport.mqtt.async_publish", fail
    )
    with pytest.raises(RuntimeError, match="MQTT publish failed"):
        await connected_coordinator.async_turn_on("DEV1")
    broker.assert_clean()


async def test_closed_manager_does_not_resubscribe(broker):
    manager = MQTTRequestManager(MagicMock())
    manager.async_close()
    with pytest.raises(HomeAssistantError) as raised:
        await manager.async_request("request", "answer", "DEV1")
    assert raised.value.translation_key == "request_cancelled"
    assert not broker.events


async def test_accepted_local_command_reconciles_even_without_echo(
    broker, connected_coordinator, monkeypatch
):
    connected_coordinator.devices["DEV1"] = EnOceanDevice(
        "DEV1", "Switch", [{"eep": "D2-01-11"}]
    )
    schedule = MagicMock()
    monkeypatch.setattr(
        connected_coordinator, "_schedule_reconciliation_queries", schedule
    )
    await connected_coordinator.async_turn_on("DEV1", channel=1)
    schedule.assert_called_once_with("DEV1", 1, fields={"switch"})
    schedule.reset_mock()
    await connected_coordinator.async_query_device_status("DEV1", channel=1)
    schedule.assert_not_called()


async def test_report_before_ack_does_not_schedule_redundant_query(
    broker, connected_coordinator, monkeypatch
):
    device = EnOceanDevice("DEV1", "Switch", [{"eep": "D2-01-11"}])
    connected_coordinator.devices["DEV1"] = device
    broker.auto_respond = False
    schedule = MagicMock()
    monkeypatch.setattr(
        connected_coordinator, "_schedule_reconciliation_queries", schedule
    )
    task = asyncio.create_task(connected_coordinator.async_turn_on("DEV1", channel=1))
    await asyncio.sleep(0)
    connected_coordinator._handle_telegram_property_message(
        SimpleNamespace(
            topic="EnOcean/AABB0011/stream/telegram/DEV1/from",
            payload=json.dumps(
                {"functions": [{"key": "switch", "value": "on", "channel": 1}]}
            ),
            retain=False,
        )
    )
    broker.receive(
        "EnOcean/AABB0011/putAnswer/devices/DEV1/state", {"header": {"httpStatus": 200}}
    )
    await task
    schedule.assert_not_called()


async def test_fast_outage_after_suback_never_publishes_old_request(
    broker, connected_coordinator
):
    broker.auto_ack = False
    task = asyncio.create_task(connected_coordinator.async_turn_on("DEV1"))
    await asyncio.sleep(0)
    broker.acknowledgements["EnOcean/AABB0011/putAnswer/devices/DEV1/state"][0]()
    connected_coordinator._handle_bridge_status(SimpleNamespace(payload="0"))
    connected_coordinator._bridge_connected = True
    connected_coordinator._gateway_available = True
    with pytest.raises(HomeAssistantError):
        await task
    assert not broker.published
    broker.assert_clean()


async def test_ack_timeout_starts_at_publish_not_at_enqueue(
    broker, connected_coordinator, monkeypatch
):
    """A request queued longer than the ack timeout must still succeed."""
    monkeypatch.setattr(
        "custom_components.opus_greennet.mqtt_transport.REQUEST_TIMEOUT", 0.05
    )
    broker.auto_respond = False
    answer = "EnOcean/AABB0011/putAnswer/devices/DEV1/state"
    first = asyncio.create_task(connected_coordinator.async_turn_on("DEV1"))
    second = asyncio.create_task(connected_coordinator.async_turn_off("DEV1"))
    await asyncio.sleep(0.03)
    broker.receive(answer, {"header": {"httpStatus": 200}})
    await first
    # The second command has now been queued for longer than half the timeout,
    # but its deadline only starts once it is actually published.
    await asyncio.sleep(0.035)
    assert not second.done()
    assert len(broker.published) == 2
    broker.receive(answer, {"header": {"httpStatus": 200}})
    await second
    broker.assert_clean()


async def test_ack_timeout_excludes_suback_latency(
    broker, connected_coordinator, monkeypatch
):
    """Slow SUBACKs must not consume the acknowledgement deadline."""
    monkeypatch.setattr(
        "custom_components.opus_greennet.mqtt_transport.REQUEST_TIMEOUT", 0.05
    )
    broker.auto_ack = False
    task = asyncio.create_task(connected_coordinator.async_turn_on("DEV1"))
    await asyncio.sleep(0.08)
    assert not task.done()
    assert broker.published == []
    answer = "EnOcean/AABB0011/putAnswer/devices/DEV1/state"
    for acknowledge in list(broker.acknowledgements[answer]):
        acknowledge()
    await task
    assert len(broker.published) == 1
    broker.assert_clean()


async def test_commands_overtake_queued_status_queries(broker, connected_coordinator):
    broker.auto_respond = False
    answer = "EnOcean/AABB0011/putAnswer/devices/DEV1/state"
    busy = asyncio.create_task(connected_coordinator.async_turn_on("DEV1"))
    await asyncio.sleep(0)
    query = asyncio.create_task(connected_coordinator.async_query_device_status("DEV1"))
    await asyncio.sleep(0)
    command = asyncio.create_task(connected_coordinator.async_turn_off("DEV1"))
    await asyncio.sleep(0)
    assert len(broker.published) == 1

    broker.receive(answer, {"header": {"httpStatus": 200}})
    await busy
    await asyncio.sleep(0)
    # The later command is published before the earlier queued query.
    assert len(broker.published) == 2
    assert '"switch"' in broker.published[1][1]
    assert '"off"' in broker.published[1][1]
    broker.receive(answer, {"header": {"httpStatus": 200}})
    await command
    await asyncio.sleep(0)
    assert len(broker.published) == 3
    assert '"query"' in broker.published[2][1]
    broker.receive(answer, {"header": {"httpStatus": 200}})
    await query
    broker.assert_clean()


async def test_command_timeout_keeps_gateway_available(
    broker, connected_coordinator, monkeypatch
):
    """One unacknowledged command fails only its own entity action."""
    monkeypatch.setattr(
        "custom_components.opus_greennet.mqtt_transport.REQUEST_TIMEOUT", 0.01
    )
    broker.auto_respond = False
    with pytest.raises(HomeAssistantError) as raised:
        await connected_coordinator.async_turn_on("DEV1")
    assert raised.value.translation_key == "request_timeout"
    assert connected_coordinator._gateway_available is True
    assert connected_coordinator.available is True

    # Subsequent commands for other entities are still dispatched.
    broker.auto_respond = True
    await connected_coordinator.async_turn_on("DEV2")
    assert broker.published[-1][0].endswith("/devices/DEV2/state")
    broker.assert_clean()


async def test_mqtt_disconnect_still_marks_gateway_unavailable(connected_coordinator):
    connected_coordinator._handle_connection_status(False)
    assert connected_coordinator._gateway_available is False


async def test_global_send_queue_keeps_one_request_in_flight(
    broker, connected_coordinator
):
    """Different devices bypass endpoint locks but share one in-flight slot."""
    broker.auto_respond = False
    tasks = [
        asyncio.create_task(connected_coordinator.async_turn_on(f"DEV{index}"))
        for index in range(10)
    ]
    for index in range(10):
        for _ in range(5):
            await asyncio.sleep(0)
        # Only the head of the queue has been published so far.
        assert len(broker.published) == index + 1
        broker.receive(
            f"EnOcean/AABB0011/putAnswer/devices/DEV{index}/state",
            {"header": {"httpStatus": 200}},
        )
    await asyncio.gather(*tasks)
    broker.assert_clean()


async def test_background_queries_wait_behind_commands(broker, connected_coordinator):
    broker.auto_respond = False
    first = asyncio.create_task(connected_coordinator.async_turn_on("DEV1"))
    await asyncio.sleep(0)
    background = asyncio.create_task(
        connected_coordinator.async_query_device_status("DEV2", background=True)
    )
    command = asyncio.create_task(connected_coordinator.async_turn_on("DEV3"))
    for _ in range(5):
        await asyncio.sleep(0)
    broker.receive(
        "EnOcean/AABB0011/putAnswer/devices/DEV1/state",
        {"header": {"httpStatus": 200}},
    )
    for _ in range(5):
        await asyncio.sleep(0)
    assert broker.published[1][0].endswith("/DEV3/state")
    for device in ("DEV3", "DEV2"):
        broker.receive(
            f"EnOcean/AABB0011/putAnswer/devices/{device}/state",
            {"header": {"httpStatus": 200}},
        )
        for _ in range(5):
            await asyncio.sleep(0)
    await asyncio.gather(first, background, command)
    broker.assert_clean()


async def test_flapping_reconnects_do_not_retrigger_snapshot(
    broker, connected_coordinator
):
    """A reconnect may resync once, but flapping must not reflood the gateway."""
    broker.response = UPTIME_RESPONSE
    coord = connected_coordinator
    coord._started = True
    coord._subscription_topics = []
    coord._control_topics = []
    coord._stream_topics = []

    coord._handle_connection_status(True)
    await coord._refresh_task
    snapshots = [t for t, _, _ in broker.published if t.endswith("/get/devices")]
    assert len(snapshots) == 1

    # A second reconnect within the debounce window only probes health.
    coord._handle_connection_status(True)
    await coord._refresh_task
    snapshots = [t for t, _, _ in broker.published if t.endswith("/get/devices")]
    assert len(snapshots) == 1
    probes = [t for t, _, _ in broker.published if t.endswith("/uptime")]
    assert len(probes) == 2

    # After the debounce window, a reconnect requests a fresh snapshot again.
    coord._last_reconnect_at = asyncio.get_running_loop().time() - 61
    coord._handle_connection_status(True)
    await coord._refresh_task
    snapshots = [t for t, _, _ in broker.published if t.endswith("/get/devices")]
    assert len(snapshots) == 2
    await coord.async_unload()
    broker.assert_clean()
