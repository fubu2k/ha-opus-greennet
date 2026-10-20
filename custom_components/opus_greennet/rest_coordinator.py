"""Coordinator for one OPUS gateway on the REST transport (phase 14).

Config entries choose their transport (``const.CONF_TRANSPORT``). Entries
without it, i.e. every entry created before phase 14, keep the MQTT
coordinator in ``coordinator.py`` unchanged. This coordinator talks to the
gateway's EnOcean over IP REST API directly and offers the same public
interface to the platforms, so entities, identities and services do not
depend on the transport.

Lifecycle:

* Setup reads the gateway identity, its uptime and the complete device list,
  so every entity can be created with current state before the platforms
  start.
* A background task keeps ``GET /devices/stream`` open. Each new stream
  begins with the complete device list (a full resynchronisation) and then
  delivers every telegram the moment the gateway handles it.
* A health probe reads the uptime every minute (the same interval as the
  MQTT transport). It detects an unreachable gateway even while the stream is
  silent, and a gateway restart (falling uptime) whose old stream connection
  could otherwise stay half open. Gateway health never confirms device state.
* Commands are HTTP PUTs. The gateway acknowledges them synchronously; one
  radio request is in flight at a time, user commands first. HTTP correlates
  every answer with its request, so the late-answer ambiguity of the MQTT
  transport (protocol contract section 7.2) does not exist here.
* ACK 201 (SoftSmartAck) keeps the semantics of phase 11: deferred delivery,
  no estimate, no travel estimate and no status query for that command.
"""

from __future__ import annotations

import asyncio
import heapq
import itertools
import logging
from collections.abc import Callable, Coroutine, Sequence
from datetime import datetime, timedelta
from time import monotonic, time
from typing import Final

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from . import protocol
from .api import (
    RECOM_TIMEOUT,
    OpusApiError,
    OpusAuthError,
    OpusConnectionError,
    OpusGatewayClient,
    OpusResponseError,
    OpusTimeoutError,
    gateway_uptime_value,
    response_status,
)
from .const import (
    COVER_MOVEMENT_GRACE_SECONDS,
    COVER_RECONCILIATION_DELAYS,
    DOMAIN,
    KEY_CHANNEL,
    KEY_DIMMER,
    KEY_POSITION,
    KEY_QUERY,
    KEY_RAMPING_MODE,
    KEY_STOP,
    RAMPING_MODE_FADE,
    RAMPING_MODE_SWITCH,
)
from .domain import (
    ACK_STATUS_DEFERRED,
    GATEWAY_HEALTH_INTERVAL,
    OUTBOUND_STATE_RECONCILIATION_DELAYS,
    SIGNAL_AVAILABILITY_UPDATE,
    SIGNAL_DEVICE_DISCOVERED,
    SIGNAL_DEVICE_REMOVED,
    SIGNAL_DEVICE_STATE_UPDATE,
    SUPERSEDED,
    CommandDelivery,
    CoverMovement,
    request_error,
)
from .enocean_device import EnOceanDevice
from .wire import JsonObject, WireFunction, WireMapping, as_object, object_items

_LOGGER = logging.getLogger(__name__)

# The stream reports live changes; the periodic device list additionally
# refreshes slowly changing metadata (battery level, signal, movement times),
# which the gateway does not stream, and picks up newly paired devices. It is
# a read of the gateway's device model and causes no radio telegram (protocol
# contract section 12.2).
DEVICE_LIST_REFRESH_INTERVAL = timedelta(minutes=30)
# Pause before the next stream attempt after consecutive failures (seconds).
# A stream that ended after it had connected is reopened immediately.
STREAM_RETRY_DELAYS = (1.0, 2.0, 5.0, 10.0, 30.0, 60.0)
# A telegram from a device missing in the device list triggers one lookup of
# that device; failed lookups are retried after this many seconds.
UNKNOWN_DEVICE_RETRY_SECONDS = 3600.0
# The gateway echoes every command it sends as a "to" telegram. The echo of
# this integration's own transmitted command can arrive just after the HTTP
# acknowledgement; within this time an echo with the same content is not
# mistaken for a command from another controller.
OWN_COMMAND_ECHO_SECONDS = 2.0

# Radio queue priorities: lower values are dispatched first. User-initiated
# control commands must never wait behind status queries, and delayed
# (background) reconciliation queries must never compete with either of them.
# The values match the MQTT transport (``mqtt_transport``); moved here from
# ``api.py`` in phase R3 because scheduling is coordinator policy, not HTTP.
PRIORITY_COMMAND: Final = 0
PRIORITY_QUERY: Final = 10
PRIORITY_BACKGROUND: Final = 20


class PriorityLock:
    """An asyncio lock whose waiters are woken by priority, then FIFO.

    It serialises the requests that may reach a radio device (state PUTs and
    ReCom), so exactly one of them is in flight per gateway.
    """

    def __init__(self) -> None:
        self._locked = False
        self._waiters: list[tuple[int, int, asyncio.Future[None]]] = []
        self._counter = itertools.count()

    @property
    def queue_depth(self) -> int:
        """Return the number of waiters."""
        return len(self._waiters)

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
                self._waiters.remove(entry)
                heapq.heapify(self._waiters)
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


class GatewayMismatchError(OpusApiError):
    """The configured address answers with a different gateway identity."""


class OpusGreenNetRestCoordinator:
    """Own the REST connection to one gateway and the state of its devices."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: OpusGatewayClient,
        eag_id: str,
        *,
        on_auth_failed: Callable[[], None] | None = None,
    ) -> None:
        """Initialize the coordinator."""
        self.hass = hass
        self.client = client
        self.eag_id = eag_id
        self.devices: dict[str, EnOceanDevice] = {}
        self.gateway_info: JsonObject = {}
        self.gateway_uptime: str | None = None
        self._on_auth_failed = on_auth_failed
        self._auth_failed = False
        self._unloaded = False
        self._gateway_available = False
        self._stream_connected = False
        self._stream_task: asyncio.Task[None] | None = None
        self._probe_task: asyncio.Task[None] | None = None
        self._refresh_task: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._timers: list[Callable[[], None]] = []
        # One radio request in flight at a time, dispatched by priority.
        self._radio_queue = PriorityLock()
        self._device_generations: dict[str, int] = {}
        # Incremented whenever the gateway becomes unavailable: requests queued
        # before an outage are never sent after the gateway returns.
        self._outage_generation = 0
        self._unknown_devices: dict[str, float] = {}
        self._command_waiters: dict[str, int] = {}
        self._sent_commands: dict[str, list[tuple[float, frozenset[object]]]] = {}
        self._cover_movements: dict[tuple[str, int], CoverMovement] = {}
        self._pending_reconciliation_queries: dict[
            tuple[str, int], list[Callable[[], None]]
        ] = {}
        self._pending_reconciliation_fields: dict[tuple[str, int], set[str]] = {}
        self._feedback_revisions: dict[tuple[str, int, str], int] = {}
        self._reconciling: set[tuple[str, int]] = set()
        # Channels whose delayed status check is still wanted. A queued
        # background check is sent only while its channel is listed here;
        # feedback or a newer command removes it before the request is sent.
        self._reconcile_needed: set[tuple[str, int]] = set()

    # ──────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        """Return whether the gateway answers and its event stream is open."""
        return not self._unloaded and self._gateway_available

    async def async_setup(self) -> None:
        """Read identity, uptime and devices, then start the background work.

        Raises an ``OpusApiError`` when the gateway cannot be used yet.
        """
        info = await self.client.async_get("/system/info")
        self._apply_system_info(info)
        self.gateway_uptime = gateway_uptime_value(
            await self.client.async_get("/system/uptime")
        )
        self._apply_device_list(
            await self.client.async_get("/devices"), authoritative=True
        )
        self._set_gateway_available(True)
        self._timers.append(
            async_track_time_interval(
                self.hass, self._async_health_tick, GATEWAY_HEALTH_INTERVAL
            )
        )
        self._timers.append(
            async_track_time_interval(
                self.hass, self._async_refresh_tick, DEVICE_LIST_REFRESH_INTERVAL
            )
        )
        self._start_stream()

    async def async_unload(self) -> None:
        """Stop all background work and mark the gateway unavailable."""
        _LOGGER.debug("Unloading OPUS REST coordinator for EAG %s", self.eag_id)
        self._unloaded = True
        self._set_gateway_available(False)
        for cancel in self._timers:
            cancel()
        self._timers.clear()
        self._cancel_background_work()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()

    def _apply_system_info(self, document: JsonObject) -> None:
        """Store the gateway information and verify its identity."""
        info = as_object(document.get("systemInfo")) or {}
        eurid = info.get("eurid")
        if isinstance(eurid, str) and eurid.upper() != self.eag_id:
            raise GatewayMismatchError(
                f"The gateway at {self.client.host} reports EURID {eurid}"
            )
        self.gateway_info = info

    def _create_task(
        self, coroutine: Coroutine[object, object, None], name: str
    ) -> asyncio.Task[None]:
        """Own every background operation until unload."""
        task = self.hass.async_create_background_task(
            coroutine, f"{DOMAIN} {self.eag_id} {name}"
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    @callback
    def _set_gateway_available(self, available: bool) -> None:
        """Notify every entity when gateway health changes."""
        if self._gateway_available == available:
            return
        self._gateway_available = available
        if not available:
            self._outage_generation += 1
            self._cancel_background_work()
        if not self._unloaded:
            _LOGGER.info(
                "OPUS gateway %s is %s",
                self.eag_id,
                "available" if available else "unavailable",
            )
        async_dispatcher_send(self.hass, f"{SIGNAL_AVAILABILITY_UPDATE}_{self.eag_id}")

    @callback
    def _cancel_background_work(self) -> None:
        """Drop estimates and delayed checks that assume a working gateway."""
        for device_id, channel_id in list(self._cover_movements):
            self._clear_cover_movement(device_id, channel_id, notify=False)
        for device_id, channel_id in list(self._pending_reconciliation_queries):
            self._cancel_reconciliation_queries(device_id, channel_id)
        self._reconcile_needed.clear()

    @callback
    def _handle_auth_failure(self) -> None:
        """Stop using rejected credentials and ask the user for new ones once."""
        self._set_gateway_available(False)
        if self._auth_failed:
            return
        self._auth_failed = True
        _LOGGER.warning("OPUS gateway %s rejected the credentials", self.eag_id)
        if self._on_auth_failed is not None:
            self._on_auth_failed()

    # ──────────────────────────────────────────────────────────────────────
    # Event stream
    # ──────────────────────────────────────────────────────────────────────

    @callback
    def _start_stream(self) -> None:
        if self._unloaded or self._auth_failed:
            return
        self._stream_task = self._create_task(self._async_run_stream(), "stream")

    @callback
    def _restart_stream(self) -> None:
        """Replace the stream connection, e.g. after a gateway restart."""
        if self._stream_task is not None and not self._stream_task.done():
            self._stream_task.cancel()
        self._stream_connected = False
        self._start_stream()

    async def _async_run_stream(self) -> None:
        """Keep the event stream open until unload."""
        failures = 0
        while not self._unloaded:
            connected = False
            try:
                async for document in self.client.async_stream():
                    if not connected:
                        connected = True
                        failures = 0
                        self._stream_connected = True
                        _LOGGER.debug("OPUS event stream connected for %s", self.eag_id)
                    self._handle_document(document)
                    if not self._gateway_available:
                        self._set_gateway_available(True)
                _LOGGER.debug("OPUS event stream for %s ended", self.eag_id)
            except OpusAuthError:
                self._stream_connected = False
                self._handle_auth_failure()
                return
            except OpusApiError as err:
                _LOGGER.debug("OPUS event stream for %s failed: %s", self.eag_id, err)
            except Exception:
                _LOGGER.exception("Unexpected error in the OPUS event stream")
            self._stream_connected = False
            if connected:
                # Reconnect immediately; the next stream resynchronises state.
                continue
            self._set_gateway_available(False)
            delay = STREAM_RETRY_DELAYS[min(failures, len(STREAM_RETRY_DELAYS) - 1)]
            failures += 1
            await asyncio.sleep(delay)

    @callback
    def _handle_document(self, document: JsonObject) -> None:
        """Apply one event stream document."""
        header = as_object(document.get("header")) or {}
        content = header.get("content")
        if content == "devices":
            self._apply_device_list(document, authoritative=True)
        elif content == "device":
            if (data := as_object(document.get("device"))) is not None:
                # A single changed device is live feedback, unlike a list.
                if isinstance(device_id := data.get("deviceId"), str):
                    self._record_confirmed_feedback(
                        device_id, protocol.device_state_functions(data)
                    )
                self._apply_device_object(data, authoritative=True)
        elif content == "telegram":
            if (telegram := as_object(document.get("telegram"))) is not None:
                self._handle_telegram(telegram)
        else:
            _LOGGER.debug("Ignoring OPUS stream document of type %r", content)

    # ──────────────────────────────────────────────────────────────────────
    # Health probe and device list refresh
    # ──────────────────────────────────────────────────────────────────────

    @callback
    def _async_health_tick(self, _now: datetime) -> None:
        if self._probe_task is None or self._probe_task.done():
            self._probe_task = self._create_task(self._async_probe(), "health probe")

    async def _async_probe(self) -> None:
        """Check that the gateway answers and detect gateway restarts.

        The result describes the gateway only. It never confirms or changes
        the state of a device (guardrail A2).
        """
        try:
            uptime = gateway_uptime_value(await self.client.async_get("/system/uptime"))
        except OpusAuthError:
            self._handle_auth_failure()
            return
        except OpusApiError as err:
            _LOGGER.debug("OPUS gateway %s health probe failed: %s", self.eag_id, err)
            if self._gateway_available:
                self._set_gateway_available(False)
                # The open stream may be half open towards a rebooting gateway.
                self._restart_stream()
            return
        previous = self.gateway_uptime
        self.gateway_uptime = uptime
        if previous is not None and float(uptime) < float(previous):
            _LOGGER.info("OPUS gateway %s restarted; reopening its stream", self.eag_id)
            self._restart_stream()
        elif self._stream_connected and not self._gateway_available:
            self._set_gateway_available(True)

    @callback
    def _async_refresh_tick(self, _now: datetime) -> None:
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = self._create_task(
                self._async_refresh_device_list(), "device list refresh"
            )

    async def _async_refresh_device_list(self) -> None:
        """Refresh metadata and discover newly paired devices."""
        if not self.available:
            return
        try:
            document = await self.client.async_get("/devices")
        except OpusApiError as err:
            _LOGGER.debug("Could not refresh the OPUS device list: %s", err)
            return
        self._apply_device_list(document, authoritative=False)

    # ──────────────────────────────────────────────────────────────────────
    # Devices
    # ──────────────────────────────────────────────────────────────────────

    @callback
    def _apply_device_list(self, document: WireMapping, *, authoritative: bool) -> None:
        """Apply a complete device list.

        ``authoritative`` lists (setup and the start of every stream) carry
        the current state. A periodic refresh only updates metadata, because
        the stream may already have delivered newer state.
        """
        devices = object_items(document.get("devices"))
        for entry in devices:
            data = as_object(entry.get("device", entry))
            if data is not None:
                self._apply_device_object(data, authoritative=authoritative)
        _LOGGER.debug("Applied OPUS device list with %d devices", len(devices))

    @callback
    def _apply_device_object(self, data: JsonObject, *, authoritative: bool) -> None:
        """Create or refresh one device from its gateway object."""
        device_id = data.get("deviceId")
        if not isinstance(device_id, str) or not device_id:
            return
        self._unknown_devices.pop(device_id, None)
        existing = self.devices.get(device_id)
        dbm = EnOceanDevice._parse_integer(data.get("dbm"))
        device = EnOceanDevice(
            device_id=device_id,
            friendly_id=protocol.metadata_text(data.get("friendlyId")) or device_id,
            eeps=object_items(data.get("eeps")),
            manufacturer=protocol.metadata_text(data.get("manufacturer")),
            physical_device=protocol.metadata_text(data.get("physicalDevice")),
            first_seen=protocol.metadata_text(data.get("firstSeen")),
            last_seen=protocol.metadata_text(data.get("lastSeen")),
            dbm=dbm,
        )
        if existing is not None:
            device.channels = existing.channels
            # A device list refreshes metadata and state, never a previous
            # rocker occurrence.
            device.clear_button_occurrence()
            device.profile = existing.profile
            device.last_update_source = existing.last_update_source
            device.last_update_received_monotonic = (
                existing.last_update_received_monotonic
            )
            device.last_update_finalized_monotonic = (
                existing.last_update_finalized_monotonic
            )
            device.last_update_dispatched_monotonic = (
                existing.last_update_dispatched_monotonic
            )
            device.last_command_error = existing.last_command_error
            # Deferred (ACK 201) markers survive a device list: a snapshot is
            # not a report that the queued telegram was delivered (phase 11).
            device.deferred_commands = existing.deferred_commands
            if dbm is None:
                device.dbm = existing.dbm
            if not device.last_seen:
                device.last_seen = existing.last_seen
        functions = (
            protocol.device_state_functions(data)
            if authoritative or existing is None
            else protocol.configuration_functions(data)
        )
        if functions:
            device.update_from_telegram({"functions": functions})
            if authoritative or existing is None:
                device.last_update_source = "device list"
        self.devices[device_id] = device
        if existing is None or not existing.eeps:
            _LOGGER.info(
                "Device discovered: %s (%s) - EEPs: %s - Type: %s",
                device.friendly_id,
                device_id,
                [eep.get("eep") for eep in device.eeps],
                device.entity_type,
            )
            async_dispatcher_send(
                self.hass, f"{SIGNAL_DEVICE_DISCOVERED}_{self.eag_id}", device
            )
        else:
            self._dispatch_state(device)

    @callback
    def async_forget_device(self, device_id: str) -> None:
        """Discard only this child's state and queued work, without gateway writes."""
        for did, channel_id in list(self._cover_movements):
            if did == device_id:
                self._clear_cover_movement(did, channel_id, notify=False)
        self.devices.pop(device_id, None)
        self._cancel_reconciliation_queries(device_id)
        self._sent_commands.pop(device_id, None)
        for key in list(self._feedback_revisions):
            if key[0] == device_id:
                self._feedback_revisions.pop(key)
        # Requests already queued for this device are dropped before sending.
        self._device_generations[device_id] = (
            self._device_generations.get(device_id, 0) + 1
        )
        async_dispatcher_send(
            self.hass, f"{SIGNAL_DEVICE_REMOVED}_{self.eag_id}", device_id
        )

    @callback
    def _lookup_unknown_device(self, device_id: str) -> None:
        """Fetch a device that reported before it appeared in a device list."""
        now = monotonic()
        last = self._unknown_devices.get(device_id)
        if last is not None and now - last < UNKNOWN_DEVICE_RETRY_SECONDS:
            return
        self._unknown_devices[device_id] = now
        self._create_task(self._async_lookup_device(device_id), "device lookup")

    async def _async_lookup_device(self, device_id: str) -> None:
        try:
            document = await self.client.async_get(
                OpusGatewayClient.device_path(device_id)
            )
        except OpusApiError as err:
            _LOGGER.debug(
                "OPUS device %s is not known to the gateway: %s", device_id, err
            )
            return
        if (data := as_object(document.get("device"))) is not None:
            self._apply_device_object(data, authoritative=True)

    def get_device(self, device_id: str) -> EnOceanDevice | None:
        """Get a device by ID."""
        return self.devices.get(device_id)

    def get_devices_by_type(self, entity_type: str) -> list[EnOceanDevice]:
        """Get all devices of a specific entity type."""
        return [
            device
            for device in self.devices.values()
            if device.entity_type == entity_type
        ]

    def late_answer_diagnostics(self, owner: str) -> dict[str, int]:
        """Return the late-answer counters of the diagnostics schema.

        HTTP pairs every answer with its request, so a REST request can never
        receive an earlier request's answer. The counters keep the same keys
        as the MQTT transport (phase 12) and are always zero.
        """
        return {"unanswered_requests": 0, "possibly_late_answers": 0}

    @callback
    def _dispatch_state(self, device: EnOceanDevice) -> None:
        async_dispatcher_send(
            self.hass,
            f"{SIGNAL_DEVICE_STATE_UPDATE}_{self.eag_id}_{device.device_id}",
            device,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Telegrams
    # ──────────────────────────────────────────────────────────────────────

    @callback
    def _handle_telegram(self, telegram: JsonObject) -> None:
        """Apply one radio telegram.

        ``from`` telegrams are device reports. ``to`` telegrams are commands
        the gateway sent: from HomeKit, the gateway's own logic or this
        integration. Commands from elsewhere update state optimistically and
        are verified by delayed status queries; this integration's own
        transmitted commands are left to the entity, which applies its
        estimate after the acknowledgement.
        """
        received_at = monotonic()
        device_id = telegram.get("deviceId")
        direction = telegram.get("direction")
        if not isinstance(device_id, str) or direction not in ("from", "to"):
            return
        is_outbound = direction == "to"
        if is_outbound and self._command_waiters.get(device_id):
            return

        raw_functions = telegram.get("functions")
        single = as_object(raw_functions)
        functions: list[WireFunction] = [
            function
            for function in ([single] if single else object_items(raw_functions))
            if isinstance(function.get("key"), str)
            and function.get("value") is not None
        ]
        if is_outbound:
            functions = protocol.outbound_state_functions(functions)
            if not protocol.has_target_state(functions):
                return
            # The gateway transmitted the telegram now, e.g. a SoftSmartAck
            # delivery: that value is no longer queued (phase 11).
            self._clear_delivered_deferred(device_id, functions)
            if self._is_own_echo(device_id, functions, received_at):
                return
        if not functions:
            return

        device = self.devices.get(device_id)
        if device is None:
            self._lookup_unknown_device(device_id)
            return

        if not is_outbound:
            self._record_confirmed_feedback(device_id, functions)
        applied = functions
        if is_outbound:
            estimated = self._start_cover_commands(device, functions)
            default = protocol.channel_from_functions(functions)
            applied = [
                function
                for function in functions
                if not (
                    function.get("key") == KEY_POSITION
                    and protocol.function_channel(function, default) in estimated
                )
            ]
        update: dict[str, object] = {"functions": applied}
        if not is_outbound:
            # Only a received telegram tells when the device was last heard
            # and how strong it was; sent telegrams report dbm 0.
            update["timestamp"] = telegram.get("timestamp")
            update["telegramInfo"] = telegram.get("telegramInfo")
        device.update_from_telegram(update)
        if not is_outbound and protocol.has_operational_state(functions):
            device.last_command_error = None

        source = "telegram/to" if is_outbound else "telegram/from"
        self._mark_and_log_dispatch(device, source, received_at, telegram)
        self._dispatch_state(device)
        # Event entities consume button fields synchronously during the
        # dispatch above. Clear them so later dispatches cannot replay them.
        device.clear_button_occurrence()

        if is_outbound:
            for channel_id, fields in protocol.command_fields_by_channel(
                functions, device
            ).items():
                self._schedule_reconciliation_queries(
                    device_id, channel_id, fields=fields
                )

    def _is_own_echo(
        self, device_id: str, functions: Sequence[WireFunction], now: float
    ) -> bool:
        """Consume a recent own command whose echo these functions are."""
        sent = [
            entry for entry in self._sent_commands.get(device_id, []) if entry[0] > now
        ]
        signature = protocol.command_signature(functions)
        match = next((entry for entry in sent if entry[1] == signature), None)
        if match is not None:
            sent.remove(match)
        if sent:
            self._sent_commands[device_id] = sent
        else:
            self._sent_commands.pop(device_id, None)
        return match is not None

    def _mark_and_log_dispatch(
        self,
        device: EnOceanDevice,
        source: str,
        received_at: float,
        telegram: WireMapping,
    ) -> None:
        """Stamp a device update and log the gateway-to-HA latency."""
        device.last_update_source = source
        device.last_update_received_monotonic = received_at
        device.last_update_dispatched_monotonic = monotonic()
        if not _LOGGER.isEnabledFor(logging.DEBUG):
            return
        sent_at = protocol.telegram_timestamp(telegram.get("timestamp"))
        _LOGGER.debug(
            "OPUS %s %s (%s): %s gateway_to_ha_ms=%s",
            source,
            device.friendly_id,
            device.device_id,
            telegram.get("functions"),
            "unknown" if sent_at is None else f"{(time() - sent_at) * 1000:.0f}",
        )

    # ──────────────────────────────────────────────────────────────────────
    # Cover travel estimates
    # ──────────────────────────────────────────────────────────────────────

    def _clear_cover_movement(
        self, device_id: str, channel_id: int, *, notify: bool = True
    ) -> None:
        movement = self._cover_movements.pop((device_id, channel_id), None)
        if movement is None:
            return
        if movement.cancel is not None:
            movement.cancel()
        if (device := self.get_device(device_id)) is not None:
            device.get_or_create_channel(channel_id).movement = None
            if notify:
                self._dispatch_state(device)

    def _start_cover_movement(
        self, device: EnOceanDevice, channel_id: int, target: int
    ) -> CoverMovement | None:
        channel = device.get_or_create_channel(channel_id)
        key = (device.device_id, channel_id)
        previous = self._cover_movements.get(key)
        if previous is not None and previous.target == target:
            # An outbound echo must not extend the same movement's deadline.
            # A repeated local request must also suppress the entity's target
            # assignment after its acknowledgement, like the first request.
            channel.state_revision += 1
            return previous
        current: float | None = channel.position
        now = monotonic()
        if previous is not None:
            current = previous.start_position
            if current is not None:
                progress = min(
                    1.0, max(0.0, (now - previous.started_at) / previous.travel)
                )
                current += (previous.target - current) * progress
        self._clear_cover_movement(device.device_id, channel_id, notify=False)
        full_time = channel.vertical_movement_time
        if full_time is None or full_time <= 0:
            return None
        travel = (
            full_time if current is None else abs(target - current) / 100 * full_time
        )
        if travel == 0:
            self._dispatch_state(device)
            return None
        # A replacement must stop old status queries before waiting for its
        # acknowledgement. Keep other unconfirmed fields (e.g. tilt).
        pending = self._pending_reconciliation_fields.get(key, set())
        self._cancel_reconciliation_queries(device.device_id, channel_id)
        if pending:
            self._pending_reconciliation_fields[key] = pending
            self._pending_reconciliation_queries[key] = []
        channel.movement = (
            "closing"
            if (target > 50 if current is None else target > current)
            else "opening"
        )
        # Prevent an entity awaiting its acknowledgement from replacing the
        # last reported position with the target while the cover travels.
        channel.state_revision += 1
        movement = CoverMovement(current, target, now, travel)
        self._cover_movements[key] = movement

        @callback
        def finished(_now: datetime) -> None:
            self._finish_cover_movement(device.device_id, channel_id, movement)

        movement.cancel = async_call_later(
            self.hass, travel + COVER_MOVEMENT_GRACE_SECONDS, finished
        )
        self._dispatch_state(device)
        return movement

    def _finish_cover_movement(
        self, device_id: str, channel_id: int, movement: CoverMovement
    ) -> None:
        if self._cover_movements.get((device_id, channel_id)) is not movement:
            return
        movement.elapsed = True
        # Very short travel can end before the acknowledgement arrives. Keep
        # the estimate cancellable and never assume an unaccepted target.
        if not movement.accepted:
            return
        device = self.get_device(device_id)
        self._clear_cover_movement(device_id, channel_id, notify=False)
        if device is not None:
            channel = device.get_or_create_channel(channel_id)
            channel.position = movement.target  # Assumed, not physical feedback.
            channel.state_revision += 1
            self._dispatch_state(device)

    def _start_cover_commands(
        self, device: EnOceanDevice | None, functions: Sequence[WireFunction]
    ) -> dict[int, CoverMovement]:
        started: dict[int, CoverMovement] = {}
        if device is None or device.entity_type != "cover":
            return started
        default = protocol.channel_from_functions(functions)
        for function in functions:
            channel_id = protocol.function_channel(function, default)
            if channel_id is None:
                continue
            if function.get("key") == KEY_STOP and function.get("value") == "true":
                self._clear_cover_movement(device.device_id, channel_id)
                self._cancel_reconciliation_queries(device.device_id, channel_id)
            elif function.get("key") == KEY_POSITION:
                target = EnOceanDevice._parse_integer(
                    function.get("value"), minimum=0, maximum=100
                )
                if target is not None:
                    movement = self._start_cover_movement(device, channel_id, target)
                    if movement is not None:
                        started[channel_id] = movement
        return started

    # ──────────────────────────────────────────────────────────────────────
    # Feedback and delayed status checks
    # ──────────────────────────────────────────────────────────────────────

    def _record_confirmed_feedback(
        self, device_id: str, functions: Sequence[WireFunction]
    ) -> bool:
        """Confirm only reported fields on their own channel.

        Callers pass live reports only (``from`` telegrams and single device
        documents of the stream), so a report also settles deferred markers.
        """
        self._clear_delivered_deferred(device_id, functions)
        confirmed = protocol.confirmed_fields_by_channel(functions)
        for channel_id, fields in confirmed.items():
            if KEY_POSITION in fields:
                self._clear_cover_movement(device_id, channel_id, notify=False)
            for field in fields:
                key = (device_id, channel_id, field)
                self._feedback_revisions[key] = self._feedback_revisions.get(key, 0) + 1
            pending_key = (device_id, channel_id)
            expected = self._pending_reconciliation_fields.get(pending_key)
            if expected is not None:
                expected.difference_update(fields)
            if not expected:
                self._cancel_reconciliation_queries(device_id, channel_id)
        return bool(confirmed)

    def _cancel_reconciliation_queries(
        self, device_id: str, channel_id: int | None = None
    ) -> None:
        """Cancel delayed status queries for one device or channel."""
        keys = [
            key
            for key in self._pending_reconciliation_queries
            if key[0] == device_id and (channel_id is None or key[1] == channel_id)
        ]
        for key in keys:
            self._pending_reconciliation_fields.pop(key, None)
            for cancel in self._pending_reconciliation_queries.pop(key, []):
                cancel()
        # A check already waiting in the queue is superseded as well; it is
        # dropped before sending unless a later timer wants it again.
        self._reconcile_needed.difference_update(
            {
                key
                for key in self._reconcile_needed
                if key[0] == device_id and (channel_id is None or key[1] == channel_id)
            }
        )

    def _schedule_reconciliation_queries(
        self, device_id: str, channel_id: int, *, fields: set[str] | None = None
    ) -> None:
        """Schedule delayed status checks after an optimistic state update.

        Same delays and budget as the MQTT transport (guardrail A1): two
        checks, 5 and 20 s, or 10 and 20 s after a cover travel estimate.
        """
        key = (device_id, channel_id)
        # Position and tilt commands can overlap. A later command must not
        # discard an earlier field whose final reading is still unknown.
        expected = self._pending_reconciliation_fields.get(key, set()).union(
            fields or ()
        )
        self._cancel_reconciliation_queries(device_id, channel_id)
        self._pending_reconciliation_queries[key] = []
        if expected:
            self._pending_reconciliation_fields[key] = expected

        delays: tuple[float, ...] = OUTBOUND_STATE_RECONCILIATION_DELAYS
        if KEY_POSITION in expected and (movement := self._cover_movements.get(key)):
            remaining = max(0.0, movement.ends_at - monotonic())
            delays = tuple(remaining + delay for delay in COVER_RECONCILIATION_DELAYS)
        for delay in delays:

            @callback
            def query_callback(
                _now: datetime,
                did: str = device_id,
                channel: int = channel_id,
                seconds: float = delay,
            ) -> None:
                _LOGGER.debug(
                    "Querying OPUS status for %s channel %s %.0fs after command",
                    did,
                    channel,
                    seconds,
                )
                if not self.available:
                    return
                self._reconcile_needed.add((did, channel))
                if (did, channel) in self._reconciling:
                    # Coalesce: one queued background query per channel.
                    return
                self._create_task(
                    self._async_reconcile_status(did, channel), "status check"
                )

            self._pending_reconciliation_queries[key].append(
                async_call_later(self.hass, delay, query_callback)
            )

    async def _async_reconcile_status(self, device_id: str, channel_id: int) -> None:
        if self.get_device(device_id) is None:
            return
        key = (device_id, channel_id)
        self._reconciling.add(key)

        def still_needed() -> bool:
            # Evaluated once, immediately before sending. Consuming the flag
            # lets a timer firing during the in-flight query start a new one.
            if key not in self._reconcile_needed:
                return False
            self._reconcile_needed.discard(key)
            return True

        try:
            await self.async_query_device_status(
                device_id, channel_id, background=True, still_needed=still_needed
            )
        except HomeAssistantError as err:
            if (err.translation_placeholders or {}).get("reason") == SUPERSEDED:
                _LOGGER.debug(
                    "Dropped superseded OPUS status check for %s channel %s",
                    device_id,
                    channel_id,
                )
            else:
                _LOGGER.debug("Could not reconcile OPUS state for %s", device_id)
        finally:
            self._reconciling.discard(key)

    # ──────────────────────────────────────────────────────────────────────
    # Deferred delivery, ACK 201 (implementation plan, phase 11)
    # ──────────────────────────────────────────────────────────────────────

    def _record_command_delivery(
        self,
        device: EnOceanDevice,
        functions: Sequence[WireFunction],
        delivery: CommandDelivery,
        unconfirmed: dict[int, set[str]],
    ) -> None:
        """Track which commanded values are still queued at the gateway.

        A transmitted (200) command supersedes an older deferred marker for the
        same key. A deferred (201) command records its values, except fields
        the device already reported while the acknowledgement was awaited.
        """
        for channel_id, values in protocol.command_values_by_channel(functions).items():
            markers = device.deferred_commands.get(channel_id, {})
            if delivery is CommandDelivery.SENT:
                for key in values:
                    markers.pop(key, None)
            else:
                pending = unconfirmed.get(channel_id, set())
                for key, value in values.items():
                    if key == KEY_STOP or key in pending:
                        # Outbound values are always wire strings.
                        markers[key] = str(value)
            if markers:
                device.deferred_commands[channel_id] = markers
            else:
                device.deferred_commands.pop(channel_id, None)

    def _clear_delivered_deferred(
        self, device_id: str, functions: Sequence[WireFunction]
    ) -> None:
        """Clear deferred markers whose value the device or gateway reported.

        Called for live device reports and for the gateway's outbound telegram,
        which shows that a queued telegram was finally transmitted. A report
        with a different value leaves the marker in place.
        """
        device = self.devices.get(device_id)
        if device is None or not device.deferred_commands:
            return
        for channel_id, values in protocol.command_values_by_channel(functions).items():
            markers = device.deferred_commands.get(channel_id)
            if not markers:
                continue
            for key, reported in values.items():
                commanded = markers.get(key)
                if commanded is not None and protocol.wire_value_matches(
                    key, commanded, reported
                ):
                    markers.pop(key)
            if not markers:
                device.deferred_commands.pop(channel_id, None)

    def _handle_deferred_command(
        self,
        device: EnOceanDevice,
        functions: Sequence[WireFunction],
        unconfirmed: dict[int, set[str]],
        started_movements: dict[int, CoverMovement],
    ) -> None:
        """Apply SoftSmartAck semantics without assuming or polling.

        The telegram has not reached the device yet, so no travel estimate
        runs and no status query is scheduled for this command (A1). When the
        gateway finally transmits, its outbound telegram is not recorded as
        an own echo and follows the foreign-command path, which applies the
        values optimistically and schedules the usual two status checks.
        """
        device_id = device.device_id
        for channel_id, movement in started_movements.items():
            key = (device_id, channel_id)
            if self._cover_movements.get(key) is movement:
                self._clear_cover_movement(device_id, channel_id)
            # Starting the movement stopped the timers of fields still
            # unconfirmed from earlier accepted commands. Restore them; this
            # command's own fields are not added.
            if self._pending_reconciliation_fields.get(
                key
            ) and not self._pending_reconciliation_queries.get(key):
                self._schedule_reconciliation_queries(device_id, channel_id)
        self._record_command_delivery(
            device, functions, CommandDelivery.DEFERRED, unconfirmed
        )
        _LOGGER.debug(
            "OPUS gateway deferred delivery of a command to %s (SoftSmartAck); "
            "pending values: %s",
            device_id,
            device.deferred_commands,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Requests
    # ──────────────────────────────────────────────────────────────────────

    def _request_error(self, err: OpusApiError, device_id: str) -> HomeAssistantError:
        """Translate a transport failure for one request."""
        if isinstance(err, OpusAuthError):
            self._handle_auth_failure()
            return request_error("request_rejected", device_id, str(err))
        if isinstance(err, OpusTimeoutError):
            return request_error("request_timeout", device_id)
        if isinstance(err, OpusConnectionError):
            return request_error("gateway_unavailable", device_id)
        reason = err.reason if isinstance(err, OpusResponseError) else str(err)
        return request_error("request_rejected", device_id, reason)

    def _check_request_valid(self, device_id: str, generation: tuple[int, int]) -> None:
        outage, device_generation = generation
        if self._unloaded or device_generation != self._device_generations.get(
            device_id, 0
        ):
            raise request_error("request_cancelled", device_id)
        if not self.available or outage != self._outage_generation:
            raise request_error("gateway_unavailable", device_id)

    def _request_generation(self, device_id: str) -> tuple[int, int]:
        """Identify the device and connection state a request was queued for."""
        return (self._outage_generation, self._device_generations.get(device_id, 0))

    async def _async_radio_request(
        self,
        device_id: str,
        request: Callable[[], Coroutine[object, object, JsonObject]],
        *,
        priority: int,
        before_send: Callable[[], None] | None = None,
    ) -> JsonObject:
        """Run one radio request while owning the single in-flight slot.

        No request is ever repeated: a timeout or an error fails only this
        request and never changes gateway availability (A2, A3).
        """
        generation = self._request_generation(device_id)
        self._check_request_valid(device_id, generation)
        queued_at = monotonic()
        await self._radio_queue.acquire(priority)
        try:
            waited = monotonic() - queued_at
            if waited > 1.0:
                _LOGGER.debug(
                    "OPUS request for %s waited %.2fs in the queue "
                    "(priority %d, %d still queued)",
                    device_id,
                    waited,
                    priority,
                    self._radio_queue.queue_depth,
                )
            self._check_request_valid(device_id, generation)
            if before_send is not None:
                before_send()
            try:
                result = await request()
            except OpusApiError as err:
                raise self._request_error(err, device_id) from err
            # The gateway answered: only unload or a removed device make the
            # result meaningless. An outage declared meanwhile does not undo
            # an accepted request.
            if self._unloaded or generation[1] != self._device_generations.get(
                device_id, 0
            ):
                raise request_error("request_cancelled", device_id)
            return result
        finally:
            self._radio_queue.release()

    async def async_send_command(
        self,
        device_id: str,
        functions: Sequence[WireFunction],
        *,
        background: bool = False,
        still_needed: Callable[[], bool] | None = None,
    ) -> CommandDelivery:
        """Send state functions to a device and wait for the acknowledgement.

        ``background`` marks delayed reconciliation queries, which are only
        sent when no user command or interactive query is waiting.
        ``still_needed`` (background only) is checked right before sending; a
        superseded check is dropped without being sent.

        Returns how the gateway accepted the command. ``DEFERRED`` (201) means
        the telegram is queued: callers must not assume the commanded state.
        """
        device = self.get_device(device_id)
        fields_by_channel = protocol.command_fields_by_channel(functions, device)
        revisions: dict[tuple[int, str], int] = {}
        started_movements: dict[int, CoverMovement] = {}
        body = {"state": {"functions": list(functions)}}
        _LOGGER.debug("Sending command to OPUS device %s: %s", device_id, body)

        @callback
        def snapshot_feedback() -> None:
            # Runs while owning the request slot, immediately before sending.
            # Feedback for an earlier queued command cannot confirm this one.
            if background and still_needed is not None and not still_needed():
                raise request_error("request_cancelled", device_id, SUPERSEDED)
            started_movements.update(self._start_cover_commands(device, functions))
            for movement in started_movements.values():
                movement.accepted = False
            revisions.update(
                {
                    (channel_id, field): self._feedback_revisions.get(
                        (device_id, channel_id, field), 0
                    )
                    for channel_id, fields in fields_by_channel.items()
                    for field in fields
                }
            )

        is_query = any(function.get("key") == KEY_QUERY for function in functions)
        if background:
            priority = PRIORITY_BACKGROUND
        elif is_query:
            priority = PRIORITY_QUERY
        else:
            priority = PRIORITY_COMMAND

        delivery: CommandDelivery | None = None
        self._command_waiters[device_id] = self._command_waiters.get(device_id, 0) + 1
        try:
            try:
                response = await self._async_radio_request(
                    device_id,
                    lambda: self.client.async_put(
                        OpusGatewayClient.device_path(device_id, "state"), body
                    ),
                    priority=priority,
                    before_send=snapshot_feedback,
                )
            except HomeAssistantError as err:
                if err.translation_key == "request_rejected" and device is not None:
                    device.last_command_error = (
                        err.translation_placeholders or {}
                    ).get("reason") or "rejected"
                    self._cancel_reconciliation_queries(device_id)
                    self._dispatch_state(device)
                elif err.translation_key == "request_timeout":
                    # A missing acknowledgement only fails this entity action.
                    _LOGGER.warning(
                        "OPUS device %s did not acknowledge a %s within the timeout",
                        device_id,
                        "status query" if is_query else "command",
                    )
                raise
            # decode_response accepted only 200 or 201.
            delivery = (
                CommandDelivery.DEFERRED
                if response_status(response) == ACK_STATUS_DEFERRED
                else CommandDelivery.SENT
            )
            if device is not None:
                if device.last_command_error is not None:
                    device.last_command_error = None
                    self._dispatch_state(device)
                unconfirmed_by_channel: dict[int, set[str]] = {}
                for channel_id, fields in fields_by_channel.items():
                    unconfirmed = {
                        field
                        for field in fields
                        if self._feedback_revisions.get(
                            (device_id, channel_id, field), 0
                        )
                        == revisions[channel_id, field]
                    }
                    if unconfirmed:
                        unconfirmed_by_channel[channel_id] = unconfirmed
                if delivery is CommandDelivery.DEFERRED:
                    self._handle_deferred_command(
                        device, functions, unconfirmed_by_channel, started_movements
                    )
                    return delivery
                self._record_command_delivery(
                    device, functions, delivery, unconfirmed_by_channel
                )
                for channel_id, unconfirmed in unconfirmed_by_channel.items():
                    self._schedule_reconciliation_queries(
                        device_id, channel_id, fields=unconfirmed
                    )
                for channel_id, movement in started_movements.items():
                    movement.accepted = True
                    if movement.elapsed:
                        self._finish_cover_movement(device_id, channel_id, movement)
            return delivery
        except BaseException:
            for channel_id, movement in started_movements.items():
                if self._cover_movements.get((device_id, channel_id)) is movement:
                    self._clear_cover_movement(device_id, channel_id)
                    self._cancel_reconciliation_queries(device_id, channel_id)
            raise
        finally:
            if delivery is not CommandDelivery.DEFERRED:
                # A deferred command is transmitted later; its outbound
                # telegram is then a real delivery, not an echo to ignore.
                self._sent_commands.setdefault(device_id, []).append(
                    (
                        monotonic() + OWN_COMMAND_ECHO_SECONDS,
                        protocol.command_signature(functions),
                    )
                )
            remaining = self._command_waiters[device_id] - 1
            if remaining:
                self._command_waiters[device_id] = remaining
            else:
                self._command_waiters.pop(device_id, None)

    def _with_channel_if_needed(
        self,
        device_id: str,
        functions: Sequence[WireFunction],
        channel: int,
    ) -> list[WireFunction]:
        """Prepend the mandatory selector for multi-channel actuator commands."""
        device = self.get_device(device_id)
        if channel > 0 or (device is not None and device.channel_count > 1):
            return [{"key": KEY_CHANNEL, "value": str(channel)}, *functions]
        return list(functions)

    def _dim_functions(
        self, device_id: str, dim_value: int, *, transition: bool
    ) -> list[WireFunction]:
        """Return a dim command, with the ramping mode where the device has one."""
        functions: list[WireFunction] = [{"key": KEY_DIMMER, "value": str(dim_value)}]
        device = self.get_device(device_id)
        if device is not None and device.supports_ramping_mode:
            functions.append(
                {
                    "key": KEY_RAMPING_MODE,
                    "value": RAMPING_MODE_FADE if transition else RAMPING_MODE_SWITCH,
                }
            )
        return functions

    async def async_turn_on(
        self,
        device_id: str,
        channel: int = 0,
        brightness: int | None = None,
        is_dimmable: bool = False,
        *,
        transition: bool = False,
    ) -> CommandDelivery:
        """Turn on a switch or light.

        Dimmers use dimValue, not switch (OPUS reference section 5.3).
        ``transition`` keeps the dimmer's own fade instead of switching at once.
        """
        functions: list[WireFunction]
        if brightness is not None or is_dimmable:
            functions = self._dim_functions(
                device_id,
                100 if brightness is None else brightness,
                transition=transition,
            )
        else:
            functions = [{"key": "switch", "value": "on"}]
        return await self.async_send_command(
            device_id, self._with_channel_if_needed(device_id, functions, channel)
        )

    async def async_turn_off(
        self,
        device_id: str,
        channel: int = 0,
        is_dimmable: bool = False,
        *,
        transition: bool = False,
    ) -> CommandDelivery:
        """Turn off a switch or light (dimmers: dimValue 0)."""
        functions: list[WireFunction]
        if is_dimmable:
            functions = self._dim_functions(device_id, 0, transition=transition)
        else:
            functions = [{"key": "switch", "value": "off"}]
        return await self.async_send_command(
            device_id, self._with_channel_if_needed(device_id, functions, channel)
        )

    async def async_set_cover_position(
        self,
        device_id: str,
        position: int,
        channel: int = 0,
    ) -> CommandDelivery:
        """Set OPUS position (0 = open, 100 = closed)."""
        functions: list[WireFunction] = [{"key": "position", "value": str(position)}]
        return await self.async_send_command(
            device_id, self._with_channel_if_needed(device_id, functions, channel)
        )

    async def async_set_cover_tilt(
        self,
        device_id: str,
        tilt: int,
        channel: int = 0,
    ) -> CommandDelivery:
        """Set cover tilt angle."""
        functions: list[WireFunction] = [{"key": "angle", "value": str(tilt)}]
        return await self.async_send_command(
            device_id, self._with_channel_if_needed(device_id, functions, channel)
        )

    async def async_stop_cover(
        self, device_id: str, channel: int = 0
    ) -> CommandDelivery:
        """Stop cover movement."""
        self._clear_cover_movement(device_id, channel)
        self._cancel_reconciliation_queries(device_id, channel)
        functions: list[WireFunction] = [{"key": KEY_STOP, "value": "true"}]
        return await self.async_send_command(
            device_id, self._with_channel_if_needed(device_id, functions, channel)
        )

    async def async_query_device_status(
        self,
        device_id: str,
        channel: int = 0,
        *,
        background: bool = False,
        still_needed: Callable[[], bool] | None = None,
    ) -> CommandDelivery:
        """Query the current device status."""
        functions: list[WireFunction] = [{"key": KEY_QUERY, "value": "status"}]
        return await self.async_send_command(
            device_id,
            self._with_channel_if_needed(device_id, functions, channel),
            background=background,
            still_needed=still_needed,
        )

    async def async_set_climate_setpoint(
        self,
        device_id: str,
        temperature: float,
    ) -> CommandDelivery:
        """Set the temperature setpoint for a climate device."""
        return await self.async_send_command(
            device_id, [{"key": "temperatureSetpoint", "value": str(temperature)}]
        )

    async def async_set_climate_mode(
        self,
        device_id: str,
        mode: str,
    ) -> CommandDelivery:
        """Set the heater mode for a climate device."""
        return await self.async_send_command(
            device_id, [{"key": "heaterMode", "value": mode}]
        )

    async def async_query_climate_status(self, device_id: str) -> CommandDelivery:
        """Query the current status of a climate device."""
        return await self.async_query_device_status(device_id)

    # ──────────────────────────────────────────────────────────────────────
    # ReCom service requests (explicit user actions only)
    # ──────────────────────────────────────────────────────────────────────

    async def async_get_device_configuration(self, device_id: str) -> JsonObject:
        """Read the configuration stored on a device."""
        path = OpusGatewayClient.device_path(device_id, "configuration")
        return await self._async_radio_request(
            device_id,
            lambda: self.client.async_get(path, timeout=RECOM_TIMEOUT),
            priority=PRIORITY_QUERY,
        )

    async def async_set_device_configuration(
        self, device_id: str, config: WireMapping
    ) -> None:
        """Write configuration to a device and wait for the acknowledgement."""
        path = OpusGatewayClient.device_path(device_id, "configuration")
        await self._async_radio_request(
            device_id,
            lambda: self.client.async_put(path, config, timeout=RECOM_TIMEOUT),
            priority=PRIORITY_QUERY,
        )

    async def async_get_device_parameters(self, device_id: str) -> JsonObject:
        """Read the configurable parameters a device supports."""
        path = OpusGatewayClient.device_path(device_id, "parameters")
        return await self._async_radio_request(
            device_id,
            lambda: self.client.async_get(path, timeout=RECOM_TIMEOUT),
            priority=PRIORITY_QUERY,
        )
