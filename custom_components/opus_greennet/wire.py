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
from typing import Final

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]
type JsonList = list[JsonValue]

# Upper bound for the entries read from one gateway list (devices, functions).
# A real installation has far fewer; the bound only stops hostile documents.
MAX_LIST_ITEMS: Final = 100_000


def parse_json(text: str | bytes | bytearray) -> JsonValue:
    """Decode JSON text.

    ``json.loads`` only produces ``dict``/``list``/``str``/``int``/``float``/
    ``bool``/``None``, so its untyped result is a ``JsonValue`` by
    construction. NaN and Infinity literals are accepted here; the domain
    parsers reject non-finite numbers per field.

    Extremely deep nesting makes the decoder raise ``RecursionError``. It is
    reported as ``ValueError`` like any other invalid document, so callers'
    existing error mapping applies.
    """
    try:
        value: JsonValue = json.loads(text)
    except RecursionError as err:
        raise ValueError("The JSON document is nested too deeply") from err
    return value


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


def bounded_object_items(
    value: object, limit: int = MAX_LIST_ITEMS
) -> list[JsonObject]:
    """Like ``object_items`` but reads at most ``limit`` list entries."""
    items = as_list(value)
    if items is None:
        return []
    return [
        item for item in (as_object(entry) for entry in items[:limit]) if item is not None
    ]


def string_key(mapping: Mapping[str, object], key: str) -> str | None:
    """Return a string field, or None when missing or of another type."""
    value = mapping.get(key)
    return value if isinstance(value, str) else None


# Read-only views. Mapping/Sequence are covariant, so freshly built
# ``dict[str, object]`` functions can be passed without copying.
type WireMapping = Mapping[str, object]
type WireFunction = Mapping[str, object]
