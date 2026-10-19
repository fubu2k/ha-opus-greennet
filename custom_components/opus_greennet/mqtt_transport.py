"""Bounded MQTT request/response operations for the OPUS bridge."""

from __future__ import annotations

import asyncio
import heapq
import itertools
import logging
from collections import deque
from collections.abc import Callable
from functools import partial
from math import isfinite
from time import monotonic

from homeassistant.components import mqtt
from homeassistant.components.mqtt import ReceiveMessage
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .const import (
    DOMAIN,
    TOPIC_BASE,
    TOPIC_GET_ANSWER_SYSTEM_UPTIME,
    TOPIC_GET_SYSTEM_UPTIME,
)
from .wire import JsonObject, as_object, parse_json

REQUEST_TIMEOUT = 10.0
# HA processes wildcard subscriptions before exact response topics. Large
# retained device snapshots need more time than a single gateway operation.
SUBSCRIPTION_TIMEOUT = 30.0

# During setup and every resync the broker replays the gateway's retained
# snapshot (tens of thousands of stream/# topics). SUBACKs and answers are
# delayed by that replay, so these phases use deliberately wider bounds.
SETUP_REQUEST_TIMEOUT = 30.0
SETUP_SUBSCRIPTION_TIMEOUT = 180.0

# Optional pause between two gateway requests (seconds). Strict one-in-flight
# serialization already paces the gateway; raise this (e.g. 0.05-0.1) only if
# a firmware needs extra breathing room for its EnOcean radio telegrams.
MIN_SEND_INTERVAL = 0.0

# Queue priorities: lower values are dispatched first. User-initiated control
# commands must never wait behind status queries, and delayed (background)
# reconciliation queries must never compete with either of them.
PRIORITY_COMMAND = 0
PRIORITY_QUERY = 10
PRIORITY_BACKGROUND = 20

# Late-answer diagnostics (phase 12, option M6). OPUS answers carry no request
# ID, so a request that ended without its answer (timeout or cancellation
# after publish) leaves an answer that may still arrive and be taken by the
# next request on the same answer topic. Such requests are remembered for this
# long. The window only bounds the diagnostic flag; it never delays, retries,
# drops or re-routes anything (guardrails A1 and A3).
LATE_ANSWER_DIAGNOSTIC_WINDOW = 120.0

_LOGGER = logging.getLogger(__name__)


def request_error(key: str, device_id: str, reason: str = "") -> HomeAssistantError:
    """Create a translated transport error without logging sensitive payloads."""
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders={"device_id": device_id, "reason": reason},
    )


def decode_response(
    payload: str | bytes | bytearray, *, require_status: bool = False
) -> JsonObject:
    """Validate an OPUS object and reject a negative protocol acknowledgement."""
    try:
        decoded = parse_json(payload)
    except (ValueError, TypeError, UnicodeDecodeError) as err:
        raise ValueError("The gateway returned invalid JSON") from err
    data = as_object(decoded)
    if not data:
        raise ValueError("The gateway returned an invalid response object")
    if data.get("header") is not None or require_status:
        status = response_status(data)
        if status not in (200, 201):
            raise ValueError(f"The gateway returned status {status}")
    return data


def response_status(data: JsonObject) -> int:
    """Return ``header.httpStatus`` as an integer.

    Accepted representations are an int, an integral float or a numeric
    string (for example 200, 200.0 or "200"). Booleans, fractional or
    non-finite floats and other types raise ValueError.
    """
    header = as_object(data.get("header"))
    if header is None:
        raise ValueError("The gateway response is missing its status")
    raw_status = header.get("httpStatus")
    if isinstance(raw_status, bool) or not isinstance(raw_status, int | float | str):
        raise ValueError("The gateway returned an invalid status")
    if isinstance(raw_status, float) and not raw_status.is_integer():
        raise ValueError("The gateway returned an invalid status")
    try:
        return int(raw_status)
    except (ValueError, OverflowError) as err:
        raise ValueError("The gateway returned an invalid status") from err


def topic_matches(pattern: str, topic: str) -> bool:
    """Return whether an MQTT topic filter (with + and #) matches a topic."""
    pattern_parts = pattern.split("/")
    topic_parts = topic.split("/")
    for index, part in enumerate(pattern_parts):
        if part == "#":
            return True
        if index >= len(topic_parts):
            return False
        if part not in ("+", topic_parts[index]):
            return False
    return len(pattern_parts) == len(topic_parts)


async def async_wait_for_subscriptions(
    hass: HomeAssistant,
    topics: list[str],
    timeout: float | None = None,
    qos: int = 1,
    *,
    required: bool = True,
) -> None:
    """Wait for broker subscription completion before publishing a request.

    ``qos`` must match the QoS used for the subscription: Home Assistant only
    fires the subscription-done callback for a matching topic AND QoS pair, so
    waiting with QoS 1 for a QoS 0 subscription never resolves. ``required``
    marks waits whose timeout is advisory: the caller continues without the
    SUBACK instead of failing the whole operation.
    """
    if timeout is None:
        timeout = SUBSCRIPTION_TIMEOUT
    pending = set(topics)
    ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    cancellations: list[Callable[[], None]] = []

    @callback
    def subscription_done(topic: str) -> None:
        pending.discard(topic)
        if not pending and not ready.done():
            ready.set_result(None)

    try:
        for topic in pending.copy():
            cancellations.append(
                mqtt.async_on_subscribe_done(
                    hass, topic, qos, partial(subscription_done, topic)
                )
            )
        if pending:
            async with asyncio.timeout(timeout):
                await ready
    except TimeoutError:
        message = (
            "Timed out after %.0fs waiting for OPUS MQTT subscriptions: %s",
            timeout,
            ", ".join(sorted(pending)),
        )
        if required:
            _LOGGER.warning(*message)
            raise
        _LOGGER.debug(*message)
    finally:
        for cancel in cancellations:
            cancel()


# Futures owned by one request: SUBACK completion and the decoded answer.
type _RequestFuture = asyncio.Future[None] | asyncio.Future[JsonObject]


class PriorityLock:
    """An asyncio lock whose waiters are woken by priority, then FIFO."""

    def __init__(self) -> None:
        self._locked = False
        self._waiters: list[tuple[int, int, asyncio.Future[None]]] = []
        self._counter = itertools.count()

    def locked(self) -> bool:
        """Return whether the lock is currently held."""
        return self._locked

    async def acquire(self, priority: int = PRIORITY_COMMAND) -> None:
        """Acquire the lock, queueing by priority while it is held."""
        if not self._locked and not self._waiters:
            self._locked = True
            return
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        entry = (priority, next(self._counter), future)
        heapq.heappush(self._waiters, entry)
        try:
            await future
        except BaseException:
            if future.done() and not future.cancelled():
                # Ownership was handed over just before cancellation.
                self.release()
            else:
                try:
                    self._waiters.remove(entry)
                    heapq.heapify(self._waiters)
                except ValueError:
                    pass
            raise

    def release(self) -> None:
        """Hand the lock to the highest-priority waiter, if any."""
        while self._waiters:
            _, _, future = heapq.heappop(self._waiters)
            if not future.done():
                # Ownership transfers directly; the lock stays held.
                future.set_result(None)
                return
        self._locked = False


class MQTTRequestManager:
    """Own subscriptions and serialize requests without protocol request IDs.

    Two lock levels exist:

    * a per-answer-topic lock, because OPUS answers carry no request ID and
      only one local operation may own an answer topic at a time, and
    * one global send lock (the "global send queue"). The gateway handles
      requests strictly sequentially; publishing 10+ requests within one
      millisecond made the tail exceed its acknowledgement deadline. Exactly
      one request is in flight at any time, dispatched by priority, then FIFO.

    Answer subscriptions are prepared before entering the global queue, so
    SUBACK latency never blocks the in-flight slot, and the acknowledgement
    deadline starts only after the request was physically published.

    Late-answer diagnostics (phase 12, option M6) only observe: a request that
    was published but ended without an answer is remembered for its answer
    topic, an answer that no request takes settles the oldest such entry, and
    an answer taken while an entry is still open is counted as possibly late
    for the request's owner. Nothing is delayed, retried, dropped or re-routed.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._closed = False
        self._generation = 0
        self._device_generations: dict[str, int] = {}
        self._locks: dict[str, PriorityLock] = {}
        self._send_lock = PriorityLock()
        self._last_send_done = 0.0
        self._waiters: dict[_RequestFuture, str] = {}
        self._cleanups: set[Callable[[], None]] = set()
        # Wildcard filters already subscribed (and SUBACKed) by the owner.
        # Answers on matching topics are routed in-process instead of
        # creating one broker subscription per request.
        self._route_patterns: list[str] = []
        self._routes: dict[str, Callable[[ReceiveMessage], None]] = {}
        # Late-answer diagnostics: per answer topic, (monotonic time, owner)
        # of published requests that ended without an answer, oldest first;
        # per owner, the number of answers taken while one was still open.
        self._unanswered: dict[str, deque[tuple[float, str]]] = {}
        self._possibly_late: dict[str, int] = {}

    @property
    def queue_depth(self) -> int:
        """Return the number of requests waiting for the in-flight slot."""
        return len(self._send_lock._waiters)  # noqa: SLF001

    @callback
    def async_add_route_pattern(self, pattern: str) -> None:
        """Declare an active persistent subscription usable for answer routing."""
        if pattern not in self._route_patterns:
            self._route_patterns.append(pattern)

    @callback
    def async_clear_route_patterns(self) -> None:
        """Forget persistent routes, e.g. while the broker resubscribes."""
        self._route_patterns.clear()

    @callback
    def async_route(self, msg: ReceiveMessage) -> None:
        """Feed a message from a persistent subscription to its pending request."""
        topic = getattr(msg, "topic", "")
        if handler := self._routes.get(topic):
            handler(msg)
        elif not getattr(msg, "retain", False):
            # No request owns the topic: this answer belongs to none of ours.
            self._record_unowned_answer(topic)

    def _open_unanswered(self, answer_topic: str) -> deque[tuple[float, str]] | None:
        """Return the unexpired unanswered requests of a topic, if any."""
        entries = self._unanswered.get(answer_topic)
        if entries is None:
            return None
        expired_before = monotonic() - LATE_ANSWER_DIAGNOSTIC_WINDOW
        while entries and entries[0][0] < expired_before:
            entries.popleft()
        if not entries:
            del self._unanswered[answer_topic]
            return None
        return entries

    @callback
    def _record_unanswered(self, answer_topic: str, owner: str) -> None:
        """Remember a published request that ended without its answer."""
        self._unanswered.setdefault(answer_topic, deque()).append((monotonic(), owner))
        _LOGGER.debug(
            "OPUS request for %s ended without an answer; a late answer may "
            "still arrive and be taken by the next request to the same endpoint",
            owner,
        )

    @callback
    def _record_unowned_answer(self, answer_topic: str) -> None:
        """An answer no request took settles the oldest unanswered request."""
        if (entries := self._open_unanswered(answer_topic)) is not None:
            entries.popleft()
            if not entries:
                del self._unanswered[answer_topic]

    @callback
    def _record_taken_answer(self, answer_topic: str, owner: str) -> None:
        """Count an answer taken while an earlier one may still be in transit."""
        if (entries := self._open_unanswered(answer_topic)) is None:
            return
        self._possibly_late[owner] = self._possibly_late.get(owner, 0) + 1
        _LOGGER.debug(
            "OPUS answer for %s may belong to an earlier request that ended "
            "without its answer (%d open); OPUS answers carry no request ID",
            owner,
            len(entries),
        )

    def late_answer_diagnostics(self, owner: str) -> dict[str, int]:
        """Return the late-answer counters of one device or gateway."""
        unanswered = 0
        for topic in list(self._unanswered):
            if (entries := self._open_unanswered(topic)) is not None:
                unanswered += sum(
                    1 for _, entry_owner in entries if entry_owner == owner
                )
        return {
            "unanswered_requests": unanswered,
            "possibly_late_answers": self._possibly_late.get(owner, 0),
        }

    def _is_routed(self, answer_topic: str) -> bool:
        return any(topic_matches(p, answer_topic) for p in self._route_patterns)

    def _own_cleanup(self, cleanup: Callable[[], None]) -> Callable[[], None]:
        """Make cleanup idempotent and immediately available to unload."""

        @callback
        def cancel() -> None:
            if cancel in self._cleanups:
                self._cleanups.remove(cancel)
                cleanup()

        self._cleanups.add(cancel)
        return cancel

    @callback
    def async_cancel_pending(self, key: str = "request_cancelled") -> None:
        """Fail active requests and immediately remove temporary subscriptions."""
        self._generation += 1
        for future, device_id in self._waiters.copy().items():
            if not future.done():
                future.set_exception(request_error(key, device_id))
        for cancel in list(self._cleanups):
            cancel()

    @callback
    def async_cancel_device(self, device_id: str) -> None:
        """Invalidate active and queued requests for a removed child only."""
        self._device_generations[device_id] = (
            self._device_generations.get(device_id, 0) + 1
        )
        # A re-added child starts with fresh counters. Its open unanswered
        # requests stay: their answers can still arrive (phase 12, L5).
        self._possibly_late.pop(device_id, None)
        for future, owner in self._waiters.copy().items():
            if owner == device_id and not future.done():
                future.set_exception(request_error("request_cancelled", device_id))

    @callback
    def async_close(self) -> None:
        """Prevent new operations and cancel current response waits."""
        self._closed = True
        self.async_cancel_pending()
        self._unanswered.clear()
        self._possibly_late.clear()

    async def async_request(
        self,
        topic: str,
        answer_topic: str,
        device_id: str,
        payload: str = "",
        *,
        require_status: bool = False,
        is_available: Callable[[], bool] | None = None,
        before_publish: Callable[[], None] | None = None,
        priority: int = PRIORITY_COMMAND,
        timeout: float | None = None,
        subscribe_timeout: float | None = None,
    ) -> JsonObject:
        """Subscribe, await SUBACK, queue globally, publish, validate one answer."""
        # The OPUS response has no request ID. Only one local operation may use
        # an answer topic at a time; a late response after timeout remains an
        # inherent protocol limitation and is never treated as device state.
        lock = self._locks.setdefault(answer_topic, PriorityLock())
        generation = self._generation
        device_generation = self._device_generations.get(device_id, 0)
        await lock.acquire(priority)
        try:
            return await self._async_request_locked(
                topic,
                answer_topic,
                device_id,
                payload,
                generation=generation,
                device_generation=device_generation,
                require_status=require_status,
                is_available=is_available,
                before_publish=before_publish,
                priority=priority,
                timeout=REQUEST_TIMEOUT if timeout is None else timeout,
                subscribe_timeout=(
                    SUBSCRIPTION_TIMEOUT
                    if subscribe_timeout is None
                    else subscribe_timeout
                ),
            )
        finally:
            lock.release()
            if not lock.locked() and self._locks.get(answer_topic) is lock:
                # No owner and no waiter: drop the idle lock so the mapping
                # does not grow with every answer topic ever used.
                del self._locks[answer_topic]

    async def _async_request_locked(
        self,
        topic: str,
        answer_topic: str,
        device_id: str,
        payload: str,
        *,
        generation: int,
        device_generation: int,
        require_status: bool,
        is_available: Callable[[], bool] | None,
        before_publish: Callable[[], None] | None,
        priority: int,
        timeout: float,
        subscribe_timeout: float,
    ) -> JsonObject:
        """Run one request while owning its answer-topic queue slot."""

        def check_still_valid() -> None:
            if (
                self._closed
                or generation != self._generation
                or device_generation != self._device_generations.get(device_id, 0)
            ):
                raise request_error("request_cancelled", device_id)
            if not mqtt.is_connected(self.hass):
                raise request_error("mqtt_unavailable", device_id)
            if is_available is not None and not is_available():
                raise request_error("gateway_unavailable", device_id)

        check_still_valid()

        loop = asyncio.get_running_loop()
        subscribed: asyncio.Future[None] = loop.create_future()
        response: asyncio.Future[JsonObject] = loop.create_future()
        self._waiters[subscribed] = device_id
        self._waiters[response] = device_id
        sent = False
        answered = False
        publish_failed = False
        send_slot = False
        routed = False
        cancellations: list[Callable[[], None]] = []

        @callback
        def handle_response(msg: ReceiveMessage) -> None:
            nonlocal answered
            if getattr(msg, "retain", False):
                return
            if not sent:
                # Published before this request: never taken (phase 12, L2).
                self._record_unowned_answer(answer_topic)
                return
            if response.done():
                if answered:
                    # A second answer while this request still listens.
                    self._record_unowned_answer(answer_topic)
                else:
                    # Arrived after a local cancellation: it settles this one.
                    answered = True
                return
            answered = True
            self._record_taken_answer(answer_topic, device_id)
            try:
                data = decode_response(msg.payload, require_status=require_status)
            except ValueError as err:
                response.set_exception(
                    request_error("request_rejected", device_id, str(err))
                )
            else:
                response.set_result(data)

        @callback
        def subscription_done() -> None:
            if not subscribed.done():
                subscribed.set_result(None)

        try:
            if self._is_routed(answer_topic):
                # A persistent wildcard subscription already covers this
                # answer topic: no extra SUBSCRIBE/SUBACK round trip needed.
                self._routes[answer_topic] = handle_response
                routed = True
            else:
                # Subscription setup has its own bound. The acknowledgement
                # deadline must not be consumed by queueing or SUBACK latency.
                async with asyncio.timeout(subscribe_timeout):
                    cancellations.append(
                        self._own_cleanup(
                            await mqtt.async_subscribe(
                                self.hass, answer_topic, handle_response, qos=1
                            )
                        )
                    )
                    cancellations.append(
                        self._own_cleanup(
                            mqtt.async_on_subscribe_done(
                                self.hass, answer_topic, 1, subscription_done
                            )
                        )
                    )
                    await subscribed
            check_still_valid()

            # Global send queue: wait (by priority) for the single in-flight
            # slot. Waiting here never counts towards the request deadline.
            queued_at = asyncio.get_running_loop().time()
            await self._send_lock.acquire(priority)
            send_slot = True
            waited = asyncio.get_running_loop().time() - queued_at
            if waited > 1.0:
                _LOGGER.debug(
                    "OPUS request for %s waited %.2fs in the send queue "
                    "(priority %d, %d still queued)",
                    device_id,
                    waited,
                    priority,
                    self.queue_depth,
                )
            gap = MIN_SEND_INTERVAL - (
                asyncio.get_running_loop().time() - self._last_send_done
            )
            if MIN_SEND_INTERVAL > 0 and gap > 0:
                await asyncio.sleep(gap)
            check_still_valid()
            if before_publish is not None:
                before_publish()
            sent = True
            try:
                await mqtt.async_publish(self.hass, topic, payload, qos=1, retain=False)
            except HomeAssistantError:
                # Nothing reached the broker: no answer can follow.
                publish_failed = True
                raise
            # The acknowledgement timeout starts exactly once the request
            # has actually been published, never at creation or enqueue.
            async with asyncio.timeout(timeout):
                result = await response
            if device_generation != self._device_generations.get(device_id, 0):
                raise request_error("request_cancelled", device_id)
            return result
        except TimeoutError as err:
            raise request_error("request_timeout", device_id) from err
        finally:
            if sent and not answered and not publish_failed and not self._closed:
                # Timeout or cancellation after publish: the answer may still
                # come. Diagnostics only; the next request is not delayed (A3).
                self._record_unanswered(answer_topic, device_id)
            if send_slot:
                self._last_send_done = asyncio.get_running_loop().time()
                self._send_lock.release()
            if routed and self._routes.get(answer_topic) is handle_response:
                del self._routes[answer_topic]
            for cancel in cancellations:
                cancel()
            for future in (subscribed, response):
                self._waiters.pop(future, None)
                if future.done() and not future.cancelled():
                    # Unload may have failed both futures while we were
                    # awaiting only one; always retrieve both exceptions.
                    future.exception()
                elif not future.done():
                    future.cancel()


def gateway_uptime_value(data: JsonObject) -> str:
    """Read and validate the gateway's system uptime response."""
    response = data.get("systemUptimeResponse")
    value = response.get("uptime") if isinstance(response, dict) else None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("The gateway returned an invalid uptime")
    try:
        uptime = float(value)
    except (ValueError, OverflowError) as err:
        raise ValueError("The gateway returned an invalid uptime") from err
    if not isfinite(uptime) or uptime < 0:
        raise ValueError("The gateway returned an invalid uptime")
    return str(value)


async def async_get_gateway_uptime(
    manager: MQTTRequestManager,
    eag_id: str,
    *,
    priority: int = PRIORITY_QUERY,
    timeout: float | None = None,
    subscribe_timeout: float | None = None,
) -> JsonObject:
    """Probe a working MQTT endpoint without requiring optional system info."""
    data = await manager.async_request(
        TOPIC_GET_SYSTEM_UPTIME.format(base=TOPIC_BASE, eag_id=eag_id),
        TOPIC_GET_ANSWER_SYSTEM_UPTIME.format(base=TOPIC_BASE, eag_id=eag_id),
        eag_id,
        require_status=True,
        priority=priority,
        timeout=timeout,
        subscribe_timeout=subscribe_timeout,
    )
    try:
        gateway_uptime_value(data)
    except ValueError as err:
        raise request_error("request_rejected", eag_id, str(err)) from err
    return data


async def async_probe_gateway(hass: HomeAssistant, eag_id: str) -> JsonObject:
    """Verify the selected gateway using its fresh MQTT uptime response."""
    manager = MQTTRequestManager(hass)
    try:
        # The config flow may run while a large retained replay is in progress.
        return await async_get_gateway_uptime(
            manager,
            eag_id,
            priority=PRIORITY_COMMAND,
            timeout=SETUP_REQUEST_TIMEOUT,
            subscribe_timeout=SETUP_SUBSCRIPTION_TIMEOUT,
        )
    except HomeAssistantError as err:
        if err.translation_key == "mqtt_unavailable":
            raise
        raise request_error("gateway_unavailable", eag_id) from err
    finally:
        manager.async_close()
