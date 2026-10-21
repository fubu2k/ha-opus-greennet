"""Pure helpers that read OPUS protocol documents.

Everything here is a side-effect free function of gateway JSON. The
coordinator owns state and timing; these helpers only extract function lists
and the fields a command or a report refers to.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from types import MappingProxyType
from typing import TYPE_CHECKING

from .const import (
    BUTTON_KEYS,
    KEY_ANGLE,
    KEY_CHANNEL,
    KEY_DIMMER,
    KEY_HEATER_MODE,
    KEY_LOCKING_MODE,
    KEY_POSITION,
    KEY_ROTATION_TIME,
    KEY_STOP,
    KEY_SWITCH,
    KEY_TEMPERATURE_SETPOINT,
    KEY_UNLOCK,
    KEY_VERTICAL_MOVEMENT_TIME,
    KNOWN_STATE_KEYS,
    LOCKING_MODES,
    RWM_EEP,
)
from .enocean_device import DeviceProfile, EnOceanDevice
from .wire import WireFunction, WireMapping, as_object, object_items

if TYPE_CHECKING:
    from .wire import JsonValue

# Keys whose reported values can confirm an actuator command.
FEEDBACK_KEYS = frozenset(
    {KEY_DIMMER, KEY_POSITION, KEY_ANGLE, KEY_TEMPERATURE_SETPOINT}
)
# Keys of a device list entry that describe configuration, not live state.
CONFIGURATION_KEYS = frozenset(
    {KEY_CHANNEL, KEY_ROTATION_TIME, KEY_VERTICAL_MOVEMENT_TIME, "batteryLevel"}
)
# The RWM documents value-only slots in transmitModes: index 0 is the smoke
# alarm, index 1 the battery state.
RWM_TRANSMIT_MODE_KEYS = ("smokeAlarm", "batteryLow")


def metadata_text(value: object) -> str:
    """Return a device metadata field as text.

    Strings pass through unchanged and numbers are rendered back to text, so
    a numeric name such as ``friendlyId = 123`` survives. Other types (null,
    booleans, containers) are not names and fall back to an empty string.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)
    return ""


def telegram_timestamp(value: object) -> float | None:
    """Return a gateway timestamp in epoch seconds.

    The gateway writes ``2026-10-19T18:06:03.990+0200``.
    """
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%f%z").timestamp()
    except ValueError:
        return None


def device_profile(document: WireMapping) -> DeviceProfile | None:
    """Read the capabilities of a ``/devices/<id>/profile`` document.

    The gateway lists the function groups of every announced EEP together.
    Each group has a ``direction`` (``to``: accepted commands, ``from``:
    reports) and ``functions`` with a ``key`` and allowed ``values`` (enums or
    a ``range``). Returns None when the document has no usable group.
    """
    profile = as_object(document.get("profile"))
    if profile is None:
        return None
    to_keys: set[str] = set()
    from_keys: set[str] = set()
    channels: int | None = None
    maxima: dict[str, float] = {}
    groups = object_items(profile.get("functionGroups"))
    for group in groups:
        direction = group.get("direction")
        if direction not in ("to", "from", "both"):
            continue
        for function in object_items(group.get("functions")):
            key = function.get("key")
            if not isinstance(key, str) or not key:
                continue
            if direction in ("to", "both"):
                to_keys.add(key)
            if direction in ("from", "both"):
                from_keys.add(key)
            for value in object_items(function.get("values")):
                limits = as_object(value.get("range"))
                if limits is None:
                    continue
                maximum = EnOceanDevice._parse_number(limits.get("max"), minimum=0)
                if maximum is None:
                    continue
                if key == KEY_CHANNEL and direction in ("to", "both"):
                    if float(maximum).is_integer():
                        channels = max(channels or 0, int(maximum) + 1)
                elif direction in ("from", "both"):
                    maxima[key] = float(maximum)
    if not to_keys and not from_keys:
        return None
    return DeviceProfile(
        to_keys=frozenset(to_keys),
        from_keys=frozenset(from_keys),
        channels=channels,
        from_maxima=MappingProxyType(maxima),
    )


def channel_from_functions(functions: Sequence[WireFunction]) -> int | None:
    """Return the telegram channel selector, defaulting to channel zero.

    A malformed selector yields None so callers can drop the functions instead
    of applying them to channel zero.
    """
    for function in functions:
        if function.get("key") != KEY_CHANNEL:
            continue
        value = EnOceanDevice._parse_number(
            function.get("value"), minimum=0, integer=True
        )
        return int(value) if value is not None else None
    return 0


def function_channel(function: WireFunction, default: int | None) -> int | None:
    """Return the channel a single function refers to."""
    channel = EnOceanDevice._parse_number(
        function.get("channel", default), minimum=0, integer=True
    )
    return int(channel) if channel is not None else None


def rwm_transmit_modes(modes: Sequence[object]) -> list[object]:
    """Resolve the RWM's documented value-only slots, scoped to F6-05-02."""
    resolved: list[object] = []
    for index, mode in enumerate(modes):
        if not isinstance(mode, dict):
            resolved.append(mode)
            continue
        entry = dict(mode)
        if index < len(RWM_TRANSMIT_MODE_KEYS) and entry.get("key") in (None, ""):
            entry["key"] = RWM_TRANSMIT_MODE_KEYS[index]
        resolved.append(entry)
    return resolved


def configuration_state_functions(data: WireMapping) -> list[WireFunction]:
    """Read reported cover movement times, never parameter defaults."""
    configuration = data.get("configuration")
    if not isinstance(configuration, dict):
        return []
    parameters = configuration.get("parameters")
    if not isinstance(parameters, list):
        return []
    channel: JsonValue = configuration.get("channel", 0)
    functions: list[WireFunction] = []
    for parameter in parameters:
        if not isinstance(parameter, dict):
            continue
        if parameter.get("key") == KEY_CHANNEL:
            channel = parameter.get("value")
        elif (
            parameter.get("key") in (KEY_ROTATION_TIME, KEY_VERTICAL_MOVEMENT_TIME)
            and "value" in parameter
        ):
            functions.append({"channel": channel, **parameter})
    return functions


# Duration of the last vertical run as the gateway's device list reports it
# (configuration parameter, seconds). Diagnostic only.
KEY_LAST_VERTICAL_MOVEMENT_TIME = "lastVerticalMovementTime"


def last_vertical_movement_times(data: WireMapping) -> dict[int, float]:
    """Return the last vertical run duration per channel from a device object.

    Only a finite, non-negative number counts; the key is optional and absent
    from the event stream's device list (no ``configuration`` block there).
    """
    configuration = data.get("configuration")
    if not isinstance(configuration, dict):
        return {}
    parameters = configuration.get("parameters")
    if not isinstance(parameters, list):
        return {}
    channel: object = configuration.get("channel", 0)
    durations: dict[int, float] = {}
    for parameter in parameters:
        if not isinstance(parameter, dict):
            continue
        key = parameter.get("key")
        if key == KEY_CHANNEL:
            channel = parameter.get("value")
        elif key == KEY_LAST_VERTICAL_MOVEMENT_TIME:
            value = EnOceanDevice._parse_number(parameter.get("value"), minimum=0)
            channel_id = EnOceanDevice._parse_integer(channel)
            if value is not None and channel_id is not None:
                durations[channel_id] = float(value)
    return durations


def device_state_functions(data: WireMapping) -> list[WireFunction]:
    """Read the state functions of one device object.

    Device objects carry their state as ``states`` (device list) or as
    ``state.functions`` (state endpoint). Button keys are never state: they
    describe a past occurrence, which only a live telegram may report.
    """
    state = data.get("state", {})
    sources: list[object] = (
        [state.get("functions", [])] if isinstance(state, dict) else []
    )
    sources.append(data.get("states", []))
    state_functions: list[WireFunction] = []
    for functions in sources:
        if not isinstance(functions, list):
            continue
        complete: list[WireFunction] = [
            function
            for function in functions
            if isinstance(function, dict)
            and isinstance(function.get("key"), str)
            and function["key"] not in BUTTON_KEYS
            and "value" in function
        ]
        if complete:
            state_functions = complete
            break
    modes = data.get("transmitModes", [])
    eeps = data.get("eeps", [])
    if (
        isinstance(eeps, list)
        and any(isinstance(eep, dict) and eep.get("eep") == RWM_EEP for eep in eeps)
        and isinstance(modes, list)
    ):
        modes = rwm_transmit_modes(modes)
    if isinstance(modes, list):
        state_functions.extend(
            function
            for function in modes
            if isinstance(function, dict)
            and function.get("key") in ("alarm", "smokeAlarm", "batteryLow")
            and "value" in function
        )
    if "batteryLevel" in data:
        state_functions.append({"key": "batteryLevel", "value": data["batteryLevel"]})
    # The answer the gateway stores for a sleeping device's next request
    # (HOPPE unlock permission) is listed as ``targetState``.
    targets = data.get("targetState")
    if isinstance(targets, list):
        state_functions.extend(
            target
            for target in targets
            if isinstance(target, dict)
            and target.get("key") == KEY_UNLOCK
            and "value" in target
        )
    return state_functions + configuration_state_functions(data)


def configuration_functions(data: WireMapping) -> list[WireFunction]:
    """Return only the configuration and battery functions of a device object."""
    return [
        function
        for function in device_state_functions(data)
        if function.get("key") in CONFIGURATION_KEYS
    ]


def confirmed_fields_by_channel(
    functions: Sequence[WireFunction],
) -> dict[int, set[str]]:
    """Validate the fields that can confirm an actuator command."""
    default = channel_from_functions(functions)
    confirmed: dict[int, set[str]] = {}
    for function in functions:
        key, value = function.get("key"), function.get("value")
        if key in FEEDBACK_KEYS:
            maximum = 40 if key == KEY_TEMPERATURE_SETPOINT else 100
            valid = (
                EnOceanDevice._parse_number(
                    value,
                    minimum=0,
                    maximum=maximum,
                    integer=key != KEY_TEMPERATURE_SETPOINT,
                )
                is not None
            )
        elif key == KEY_SWITCH:
            valid = value in ("on", "off")
        elif key == KEY_HEATER_MODE:
            valid = value in ("heating", "on", "off", "autoOff")
        elif key == KEY_LOCKING_MODE:
            # "noChange" in a position command expects no locking feedback.
            valid = value in LOCKING_MODES
        else:
            continue
        channel = function_channel(function, default)
        if valid and channel is not None and isinstance(key, str):
            confirmed.setdefault(channel, set()).add(key)
    return confirmed


def has_operational_state(functions: Sequence[WireFunction]) -> bool:
    """Require usable actuator feedback, not interim values or metadata."""
    return bool(confirmed_fields_by_channel(functions))


def command_fields_by_channel(
    functions: Sequence[WireFunction],
    device: EnOceanDevice | None = None,
) -> dict[int, set[str]]:
    """Return the fields a command expects feedback for, per channel.

    Stop needs final position/tilt feedback, never an estimated position.
    """
    expected = confirmed_fields_by_channel(functions)
    default = channel_from_functions(functions)
    for function in functions:
        if function.get("key") != KEY_STOP or function.get("value") != "true":
            continue
        channel = function_channel(function, default)
        if channel is None:
            continue
        fields = expected.setdefault(channel, set())
        fields.add(KEY_POSITION)
        if device is not None and device.supports_tilt_for_channel(channel):
            fields.add(KEY_ANGLE)
    return expected


def outbound_state_functions(
    functions: Sequence[WireFunction],
) -> list[WireFunction]:
    """Keep the parts of an outbound command that describe target state."""
    return [
        function
        for function in functions
        if function.get("key") in KNOWN_STATE_KEYS
        or function.get("key") in (KEY_CHANNEL, KEY_STOP)
    ]


def has_target_state(functions: Sequence[WireFunction]) -> bool:
    """Return whether an outbound command sets state or stops a cover."""
    return any(
        function.get("key") in KNOWN_STATE_KEYS or function.get("key") == KEY_STOP
        for function in functions
    )


def command_signature(functions: Sequence[WireFunction]) -> frozenset[object]:
    """Identify the target state of a command independent of value format.

    The gateway echoes ``"0"`` as ``0.0``; numbers are compared by value.
    """
    default = channel_from_functions(functions)
    signature: set[object] = set()
    for function in outbound_state_functions(functions):
        key = function.get("key")
        if key == KEY_CHANNEL:
            continue
        value = function.get("value")
        number = EnOceanDevice._parse_number(value)
        signature.add(
            (
                function_channel(function, default),
                key,
                number if number is not None else str(value),
            )
        )
    return frozenset(signature)
