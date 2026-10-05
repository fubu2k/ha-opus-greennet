"""Bounded MQTT request/response operations for the OPUS bridge."""

from __future__ import annotations

import asyncio
import heapq
import itertools
import json
import logging
from collections.abc import Callable
from math import isfinite
from typing import Any

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

REQUEST_TIMEOUT = 10.0
# HA processes wildcard subscriptions before exact response topics. Large
# retained device snapshots need more time than a single gateway operation.
SUBSCRIPTION_TIMEOUT = 30.0

# Queue priorities: lower values are dispatched first. User-initiated control
# commands must never wait behind background status queries/refreshes.
PRIORITY_COMMAND = 0
PRIORITY_QUERY = 10

_LOGGER = logging.getLogger(__name__)


def request_error(key: str, device_id: str, reason: str = "") -> HomeAssistantError:
    """Create a translated transport error without logging sensitive payloads."""
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders={"device_id": device_id, "reason": reason},
    )


def decode_response(payload: str | bytes, *, require_status: bool = False) -> dict:
    """Validate an OPUS object and reject a negative protocol acknowledgement."""
    try:
        data = json.loads(payload)
    except (ValueError, TypeError, UnicodeDecodeError) as err:
        raise ValueError("The gateway returned invalid JSON") from err
    if not isinstance(data, dict) or not data:
        raise ValueError("The gateway returned an invalid response object")
    header = data.get("header")
    if header is not None or require_status:
        if not isinstance(header, dict):
            raise ValueError("The gateway response is missing its status")
        status = header.get("httpStatus")
        if isinstance(status, bool) or (
            isinstance(status, float) and not status.is_integer()
        ):
            raise ValueError("The gateway returned an invalid status")
        try:
            status = int(status)
        except (TypeError, ValueError, OverflowError) as err:
            raise ValueError("The gateway returned an invalid status") from err
        if status not in (200, 201):
            raise ValueError(f"The gateway returned status {status}")
    return data


async def async_wait_for_subscriptions(hass: HomeAssistant, topics: list[str]) -> None:
    """Wait for broker subscription completion before publishing a request."""
    pending = set(topics)
    ready = asyncio.get_running_loop().create_future()
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
                    hass, topic, 1, lambda topic=topic: subscription_done(topic)
                )
            )
        if pending:
            async with asyncio.timeout(SUBSCRIPTION_TIMEOUT):
                await ready
    except TimeoutError:
        _LOGGER.warning(
            "Timed out after %.0fs waiting for OPUS MQTT subscriptions: %s",
            SUBSCRIPTION_TIMEOUT,
            ", ".join(sorted(pending)),
        )
        raise
    finally:
        for cancel in cancellations:
            cancel()


class PriorityLock:
    """An asyncio lock whose waiters are woken by priority, then FIFO."""

    def __init__(self) -> None:
        self._locked = False
        self._waiters: list[tuple[int, int, asyncio.Future]] = []
        self._counter = itertools.count()

    def locked(self) -> bool:
        """Return whether the lock is currently held."""
        return self._locked

    async def acquire(self, priority: int = PRIORITY_COMMAND) -> None:
        """Acquire the lock, queueing by priority while it is held."""
        if not self._locked and not self._waiters:
            self._locked = True
            return
        future = asyncio.get_running_loop().create_future()
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
    """Own subscriptions and serialize requests without protocol request IDs."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._closed = False
        self._generation = 0
        self._device_generations: dict[str, int] = {}
        self._locks: dict[str, PriorityLock] = {}
        self._waiters: dict[asyncio.Future, str] = {}
        self._cleanups: set[Callable[[], None]] = set()

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
        for future, owner in self._waiters.copy().items():
            if owner == device_id and not future.done():
                future.set_exception(request_error("request_cancelled", device_id))

    @callback
    def async_close(self) -> None:
        """Prevent new operations and cancel current response waits."""
        self._closed = True
        self.async_cancel_pending()

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
    ) -> dict[str, Any]:
        """Subscribe, await SUBACK, publish, and validate one fresh response."""
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
            )
        finally:
            lock.release()

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
    ) -> dict[str, Any]:
        """Run one request while owning its answer-topic queue slot."""
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

        loop = asyncio.get_running_loop()
        subscribed = loop.create_future()
        response = loop.create_future()
        self._waiters[subscribed] = device_id
        self._waiters[response] = device_id
        sent = False
        cancellations: list[Callable[[], None]] = []

        @callback
        def handle_response(msg: ReceiveMessage) -> None:
            if not sent or response.done() or getattr(msg, "retain", False):
                return
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
            # Subscription setup has its own bound. The acknowledgement
            # deadline must not be consumed by queueing or SUBACK latency.
            async with asyncio.timeout(SUBSCRIPTION_TIMEOUT):
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
            if before_publish is not None:
                before_publish()
            sent = True
            await mqtt.async_publish(self.hass, topic, payload, qos=1, retain=False)
            # The acknowledgement timeout starts exactly once the request
            # has actually been published, never at creation or enqueue.
            async with asyncio.timeout(REQUEST_TIMEOUT):
                result = await response
            if device_generation != self._device_generations.get(device_id, 0):
                raise request_error("request_cancelled", device_id)
            return result
        except TimeoutError as err:
            raise request_error("request_timeout", device_id) from err
        finally:
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


def gateway_uptime_value(data: dict[str, Any]) -> str:
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
    manager: MQTTRequestManager, eag_id: str
) -> dict[str, Any]:
    """Probe a working MQTT endpoint without requiring optional system info."""
    data = await manager.async_request(
        TOPIC_GET_SYSTEM_UPTIME.format(base=TOPIC_BASE, eag_id=eag_id),
        TOPIC_GET_ANSWER_SYSTEM_UPTIME.format(base=TOPIC_BASE, eag_id=eag_id),
        eag_id,
        require_status=True,
    )
    try:
        gateway_uptime_value(data)
    except ValueError as err:
        raise request_error("request_rejected", eag_id, str(err)) from err
    return data


async def async_probe_gateway(hass: HomeAssistant, eag_id: str) -> dict[str, Any]:
    """Verify the selected gateway using its fresh MQTT uptime response."""
    manager = MQTTRequestManager(hass)
    try:
        return await async_get_gateway_uptime(manager, eag_id)
    except HomeAssistantError as err:
        if err.translation_key == "mqtt_unavailable":
            raise
        raise request_error("gateway_unavailable", eag_id) from err
    finally:
        manager.async_close()
