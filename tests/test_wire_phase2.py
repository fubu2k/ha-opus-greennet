"""Phase 2: robustness of the JSON boundary."""

from __future__ import annotations

import pytest

from custom_components.opus_greennet.wire import (
    bounded_object_items,
    object_items,
    parse_json,
)


def test_parse_json_reports_deep_nesting_as_value_error() -> None:
    document = "[" * 200_000 + "]" * 200_000
    with pytest.raises(ValueError):
        parse_json(document)


def test_parse_json_still_decodes_normal_documents() -> None:
    assert parse_json('{"a": [1, 2, {"b": null}]}') == {"a": [1, 2, {"b": None}]}


def test_bounded_object_items_matches_object_items_below_limit() -> None:
    value = [{"a": 1}, 5, {"b": 2}, "x"]
    assert bounded_object_items(value) == object_items(value)


def test_bounded_object_items_caps_entries() -> None:
    value = [{"n": index} for index in range(10)]
    assert bounded_object_items(value, 3) == [{"n": 0}, {"n": 1}, {"n": 2}]


def test_bounded_object_items_ignores_non_lists() -> None:
    assert bounded_object_items({"a": 1}) == []
