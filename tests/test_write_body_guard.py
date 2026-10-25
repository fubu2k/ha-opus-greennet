"""A request body composed by a caller is checked before any request is queued.

``set_device_configuration`` forwards a caller-supplied object to the device
over the radio. ``json.dumps`` writes ``NaN`` as if it were JSON and raises
``TypeError`` for other types, and neither is mapped by the client, so the body
is checked at the service boundary (phase 6).
"""

from __future__ import annotations

import datetime
import json

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.opus_greennet.const import DOMAIN
from custom_components.opus_greennet.wire import (
    MAX_BODY_DEPTH,
    MAX_BODY_SIZE,
    json_body_problem,
)
from tests.fake_gateway import FakeGateway
from tests.test_service_actions import (
    CONFIGURATION_PATH,
    DEVICE_ID,
    _setup_entry,
    answering_gateway,  # noqa: F401  (fixture)
)

NOT_JSON = "the value contains data that is not valid JSON"


def _nested(levels: int) -> dict[str, object]:
    """Return an object with ``levels`` nested containers."""
    value: dict[str, object] = {}
    for _ in range(levels - 1):
        value = {"a": value}
    return value


def _circular() -> dict[str, object]:
    value: dict[str, object] = {}
    value["self"] = value
    return value


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"configuration": {"parameters": {"verticalMovementTime": 45.5}}},
        {"list": [1, "two", 3.0, None, True, {"nested": []}]},
        {"unicode": "Büro ☀", "negative": -1, "zero": 0.0},
        _nested(MAX_BODY_DEPTH),
        {"text": "x" * (MAX_BODY_SIZE - 20)},
    ],
)
def test_encodable_bodies_are_accepted(value: object) -> None:
    assert json_body_problem(value) is None
    json.dumps(value, allow_nan=False)


@pytest.mark.parametrize(
    "value",
    [
        {"a": float("nan")},
        {"a": [float("inf")]},
        {"a": -float("inf")},
        {1: "integer key"},
        {"a": {1, 2}},
        {"a": (1, 2)},
        {"a": datetime.datetime(2026, 10, 25)},
        {"a": b"bytes"},
        {"a": {"b": object()}},
        {"a": 10**5000},
    ],
)
def test_bodies_json_cannot_encode_are_refused(value: object) -> None:
    assert json_body_problem(value) == NOT_JSON


@pytest.mark.parametrize(
    "value",
    [
        _nested(MAX_BODY_DEPTH + 1),
        _nested(500),
        [[[[[[[[[[[[[[[[[1]]]]]]]]]]]]]]]]],
        _circular(),
    ],
)
def test_too_deep_or_circular_bodies_are_refused(value: object) -> None:
    assert json_body_problem(value) == "the value is nested too deeply"


def test_oversized_bodies_are_refused() -> None:
    assert json_body_problem({"text": "x" * MAX_BODY_SIZE}) == (
        "the value is too large"
    )
    assert json_body_problem({str(index): index for index in range(20000)}) == (
        "the value is too large"
    )


def test_the_problem_text_never_repeats_the_value() -> None:
    secret = "s3cr3t-value"
    problem = json_body_problem({secret: {1, 2}, "k": secret})
    assert problem is not None
    assert secret not in problem


@pytest.mark.parametrize(
    ("configuration", "reason"),
    [
        ({"a": float("nan")}, NOT_JSON),
        ({"a": {1, 2}}, NOT_JSON),
        ({"a": datetime.datetime(2026, 10, 25)}, NOT_JSON),
        (_nested(MAX_BODY_DEPTH + 1), "the value is nested too deeply"),
        ({"text": "x" * MAX_BODY_SIZE}, "the value is too large"),
    ],
)
async def test_set_device_configuration_refuses_before_any_request(
    hass: HomeAssistant,
    answering_gateway: FakeGateway,  # noqa: F811
    configuration: dict[str, object],
    reason: str,
) -> None:
    await _setup_entry(hass, answering_gateway)

    with pytest.raises(ServiceValidationError) as error:
        await hass.services.async_call(
            DOMAIN,
            "set_device_configuration",
            {"device_id": DEVICE_ID, "configuration": configuration},
            blocking=True,
        )

    assert error.value.translation_key == "invalid_configuration"
    assert error.value.translation_placeholders == {
        "device_id": DEVICE_ID,
        "reason": reason,
    }
    assert answering_gateway.requests_to("PUT", CONFIGURATION_PATH) == []

    # The refusal leaves the queue and the service usable.
    await hass.services.async_call(
        DOMAIN,
        "set_device_configuration",
        {"device_id": DEVICE_ID, "configuration": {"b": 2}},
        blocking=True,
    )
    assert answering_gateway.requests_to("PUT", CONFIGURATION_PATH) == [{"b": 2}]
