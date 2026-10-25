"""Typed OPUS wire data and validation helpers for the JSON boundary.

Wire data is what the gateway sends: JSON documents from its REST API. It is
kept separate from the normalised domain model in ``enocean_device``
(``EnOceanDevice``/``EnOceanChannel``).

The helpers only check container and field types. They never reinterpret
values: valid string representations ("on", "50", "notAvailable", proprietary
HeatArea profiles, channel "0") pass through unchanged, and unknown additive
fields are preserved for the existing parsers to ignore.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from math import isfinite
from typing import Final

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]
type JsonList = list[JsonValue]


def parse_json(text: str | bytes | bytearray) -> JsonValue:
    """Decode JSON text.

    ``json.loads`` only produces ``dict``/``list``/``str``/``int``/``float``/
    ``bool``/``None``, so its untyped result is a ``JsonValue`` by
    construction. NaN and Infinity literals are accepted here; the domain
    parsers reject non-finite numbers per field. A document nested too
    deeply for the decoder is reported as ``ValueError`` like any other
    invalid document.
    """
    try:
        value: JsonValue = json.loads(text)
    except RecursionError as err:
        raise ValueError("The JSON document is nested too deeply") from err
    return value


# Bounds for a request body that a caller (not the integration) composes. The
# gateway documents no limit; these are generous for any configuration document
# and exist so a malformed or runaway value fails before it reaches the radio.
MAX_BODY_DEPTH: Final = 16
MAX_BODY_SIZE: Final = 64 * 1024


def json_body_problem(value: object) -> str | None:
    """Return why ``value`` cannot be sent as a JSON body, or None if it can.

    Only the encoding is checked, never the meaning: keys must be strings,
    values must be JSON types with finite numbers, and nesting depth and
    encoded size are bounded. ``json.dumps`` would otherwise write ``NaN`` as
    if it were JSON or raise ``TypeError`` for other types. The returned text
    never contains the value, so it is safe to show and to log.
    """
    if not _json_encodable(value, 0):
        return "the value contains data that is not valid JSON"
    if _nesting_depth(value) > MAX_BODY_DEPTH:
        return "the value is nested too deeply"
    try:
        size = len(json.dumps(value, allow_nan=False))
    except ValueError:
        return "the value contains data that is not valid JSON"
    if size > MAX_BODY_SIZE:
        return "the value is too large"
    return None


def _json_encodable(value: object, depth: int) -> bool:
    """Check types, keys and numbers, giving up beyond the depth bound."""
    if depth > MAX_BODY_DEPTH:
        # Too deep is reported by the caller; stop before recursing further.
        return True
    if value is None or isinstance(value, str | int):
        return True
    if isinstance(value, float):
        return isfinite(value)
    if isinstance(value, list):
        return all(_json_encodable(item, depth + 1) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _json_encodable(item, depth + 1)
            for key, item in value.items()
        )
    return False


def _nesting_depth(value: object, depth: int = 1) -> int:
    """Return the nesting depth, stopping once it exceeds the bound."""
    if depth > MAX_BODY_DEPTH:
        return depth
    children: list[object]
    if isinstance(value, list):
        children = list(value)
    elif isinstance(value, dict):
        children = list(value.values())
    else:
        return depth - 1
    return max((_nesting_depth(child, depth + 1) for child in children), default=depth)


def as_object(value: object) -> JsonObject | None:
    """Return ``value`` as a JSON object, or None for any other container."""
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return value
    return None


def as_list(value: object) -> JsonList | None:
    """Return ``value`` as a JSON list, or None for any other container."""
    return value if isinstance(value, list) else None


def object_items(value: object) -> list[JsonObject]:
    """Return the JSON objects of a list, dropping entries of other types."""
    items = as_list(value)
    if items is None:
        return []
    return [item for item in (as_object(entry) for entry in items) if item is not None]


def string_key(mapping: Mapping[str, object], key: str) -> str | None:
    """Return a string field, or None when missing or of another type."""
    value = mapping.get(key)
    return value if isinstance(value, str) else None


# Read-only views. Mapping/Sequence are covariant, so freshly built
# ``dict[str, object]`` functions can be passed without copying.
type WireMapping = Mapping[str, object]
type WireFunction = Mapping[str, object]
