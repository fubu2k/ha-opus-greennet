"""Typed REST/JSON boundaries keep the existing wire contract.

These tests pin the behaviour of the typed boundary helpers and parsers:
valid string and numeric representations, channel zero, ``notAvailable``,
proprietary HeatArea profiles and unknown additive fields are accepted
exactly as before, while wrong container/field types, booleans in numeric
fields, NaN/Infinity and malformed JSON are rejected per field.

They are unit tests plus a few requests against the in-process fake gateway.
They do not verify real OPUS hardware.
"""

from __future__ import annotations

import json
import math
from collections.abc import AsyncIterator
from unittest.mock import MagicMock

import aiohttp
import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.opus_greennet import protocol
from custom_components.opus_greennet.api import (
    OpusGatewayClient,
    OpusResponseError,
    decode_response,
    response_status,
)
from custom_components.opus_greennet.coordinator import OpusGreenNetCoordinator
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from custom_components.opus_greennet.wire import (
    as_list,
    as_object,
    object_items,
    parse_json,
    string_key,
)
from tests.fake_gateway import FakeGateway


@pytest.fixture
def coord(monkeypatch: pytest.MonkeyPatch) -> OpusGreenNetCoordinator:
    monkeypatch.setattr(
        "custom_components.opus_greennet.coordinator.async_call_later", MagicMock()
    )
    monkeypatch.setattr(
        "custom_components.opus_greennet.coordinator.async_dispatcher_send",
        MagicMock(),
    )
    return OpusGreenNetCoordinator(MagicMock(), MagicMock(), "AABB0011")


@pytest.fixture
async def gateway_coord(
    coord: OpusGreenNetCoordinator, gateway: FakeGateway
) -> AsyncIterator[OpusGreenNetCoordinator]:
    """Return the coordinator talking HTTP to the fake gateway."""
    async with aiohttp.ClientSession() as session:
        coord.client = OpusGatewayClient(
            session,
            gateway.host,
            gateway.port,
            gateway.username,
            gateway.password,
        )
        coord._gateway_available = True  # noqa: SLF001
        yield coord


def apply(eep: str, *functions: dict[str, object]) -> EnOceanDevice:
    device = EnOceanDevice("DEV", "Device", [{"eep": eep}])
    device.update_from_telegram({"functions": list(functions)})
    return device


def from_device(functions: object, **fields: object) -> dict[str, object]:
    """Return a ``from`` telegram object as the event stream nests it."""
    return {"deviceId": "DEV", "direction": "from", "functions": functions, **fields}


# ── Wire helpers ────────────────────────────────────────────────────────────


def test_parse_json_keeps_non_finite_literals_for_field_validation():
    # json.loads accepts NaN/Infinity; per-field parsers reject them later.
    decoded = parse_json('{"v": NaN}')
    assert isinstance(decoded, dict)
    assert isinstance(decoded["v"], float)
    assert math.isnan(decoded["v"])
    assert parse_json('[1, 2.5, "x", true, null]') == [1, 2.5, "x", True, None]
    with pytest.raises(json.JSONDecodeError):
        parse_json("{")


def test_parse_json_reports_deep_nesting_as_invalid_json():
    # The decoder raises RecursionError; callers only map ValueError.
    with pytest.raises(ValueError, match="nested too deeply"):
        parse_json("[" * 200_000 + "]" * 200_000)


def test_container_helpers_reject_wrong_container_types():
    assert as_object({"a": 1}) == {"a": 1}
    assert as_object([("a", 1)]) is None
    assert as_object("{}") is None
    assert as_object({1: "x"}) is None
    assert as_list([1]) == [1]
    assert as_list({"0": 1}) is None
    assert object_items([{"a": 1}, "x", None, 5, [], {"b": 2}]) == [
        {"a": 1},
        {"b": 2},
    ]
    assert object_items({"0": {"a": 1}}) == []


def test_string_key_only_returns_strings():
    data = {"s": "notAvailable", "n": 5, "b": True, "none": None}
    assert string_key(data, "s") == "notAvailable"
    assert string_key(data, "n") is None
    assert string_key(data, "b") is None
    assert string_key(data, "missing") is None


# ── Acknowledgement status contract ─────────────────────────────────────────


@pytest.mark.parametrize("status", [200, 201, 200.0, "200", "201", " 200 "], ids=repr)
def test_valid_status_representations_are_accepted(status):
    payload = json.dumps({"header": {"httpStatus": status}, "extra": {"x": 1}})
    data = decode_response(payload, require_status=True)
    assert data["extra"] == {"x": 1}
    assert response_status(data) in (200, 201)


@pytest.mark.parametrize(
    ("raw", "error"),
    [
        ('{"header": {"httpStatus": true}}', "invalid status"),
        ('{"header": {"httpStatus": null}}', "invalid status"),
        ('{"header": {"httpStatus": "200.0"}}', "invalid status"),
        ('{"header": {"httpStatus": 200.5}}', "invalid status"),
        ('{"header": {"httpStatus": NaN}}', "invalid status"),
        ('{"header": {"httpStatus": Infinity}}', "invalid status"),
        ('{"header": {"httpStatus": [200]}}', "invalid status"),
        ('{"header": {"httpStatus": 400}}', "status 400"),
        ('{"header": "200"}', "missing its status"),
        ('{"header": {}}', "invalid status"),
        ("[]", "invalid response object"),
        ("{}", "invalid response object"),
        ('"text"', "invalid response object"),
        ("{", "invalid JSON"),
        ("", "invalid JSON"),
    ],
)
def test_invalid_status_container_and_json_are_rejected(raw, error):
    with pytest.raises(OpusResponseError, match=error):
        decode_response(raw, require_status=True)


def test_status_is_optional_only_when_not_required():
    assert decode_response('{"device": {"deviceId": "D"}}', require_status=False) == {
        "device": {"deviceId": "D"}
    }
    assert decode_response('{"header": {"httpStatus": 201}}') == {
        "header": {"httpStatus": 201}
    }
    # A present header is always checked, even when the status is optional.
    with pytest.raises(OpusResponseError, match="status 500"):
        decode_response('{"header": {"httpStatus": 500}}', require_status=False)
    with pytest.raises(OpusResponseError, match="missing its status"):
        decode_response('{"a": 1}')
    # The client decodes response bodies with errors="replace".
    with pytest.raises(OpusResponseError, match="invalid JSON"):
        decode_response("\ufffd")


# ── Valid fixtures of all supported profile families ────────────────────────


@pytest.mark.parametrize(
    ("eep", "function", "attribute", "expected"),
    [
        ("D2-01-00", {"key": "switch", "value": "on"}, "is_on", True),
        ("D2-01-04", {"key": "switch", "value": "off"}, "is_on", False),
        ("D2-01-02", {"key": "dimValue", "value": "50"}, "brightness", 50),
        ("A5-38-08", {"key": "dimValue", "value": 50.0}, "brightness", 50),
        ("D2-01-01", {"key": "power", "value": "12.5"}, "power", 12.5),
        ("D2-05-00", {"key": "position", "value": "30"}, "position", 30),
        ("D2-05-00", {"key": "angle", "value": 20}, "angle", 20),
        ("D2-05-01", {"key": "position", "value": "0"}, "position", 0),
        ("D1-4B-05", {"key": "temperature", "value": "21.5"}, "temperature", 21.5),
        (
            "D1-4B-06",
            {"key": "temperatureSetpoint", "value": 22},
            "temperature_setpoint",
            22.0,
        ),
        (
            "D1-4B-07",
            {"key": "heaterMode", "value": "heating"},
            "heater_mode",
            "heating",
        ),
        (
            "F6-02-01",
            {"key": "buttonA0", "value": "pressed"},
            "last_button",
            "buttonA0",
        ),
        (
            "F6-03-01",
            {"key": "buttonBI", "value": "released"},
            "last_button_action",
            "released",
        ),
        ("F6-05-02", {"key": "smokeAlarm", "value": "on"}, "smoke_alarm", True),
        ("A5-07-01", {"key": "motionDetected", "value": True}, "motion", True),
        ("A5-07-03", {"key": "illumination", "value": "200"}, "illuminance", 200.0),
        (
            "A5-07-01",
            {"key": "batteryLevel", "value": " 82.5 % "},
            "battery_level",
            82.5,
        ),
        (
            "D2-06-40",
            {"key": "handleState", "value": "tilted"},
            "handle_state",
            "tilted",
        ),
        ("F6-10-00", {"key": "handle", "value": "closed"}, "handle_state", "closed"),
        ("D2-03-10", {"key": "handle", "value": "open"}, "handle_state", "open"),
        (
            "F6-05-01",
            {"key": "liquidDetected", "value": "true"},
            "liquid_detected",
            True,
        ),
    ],
)
def test_valid_profile_fixture_updates_domain_model(eep, function, attribute, expected):
    device = apply(eep, function)
    assert getattr(device.channels[0], attribute) == expected


@pytest.mark.parametrize(
    ("eep", "heat_area_type", "step"),
    [
        ("D1-4B-05", "valve", 0.5),
        ("D1-4B-06", "cositherm", 0.1),
        ("D1-4B-07", "electro", 0.1),
    ],
)
def test_proprietary_heat_area_profiles_are_preserved(eep, heat_area_type, step):
    device = EnOceanDevice("HA", "Heat area", [{"eep": eep}])
    assert device.is_climate
    assert device.heat_area_type == heat_area_type
    assert device.setpoint_step == step


@pytest.mark.parametrize("value", ["50", 50, 50.0, " 50 "], ids=repr)
def test_equivalent_numeric_representations(value):
    assert (
        apply("D2-01-02", {"key": "dimValue", "value": value}).channels[0].brightness
        == 50
    )


@pytest.mark.parametrize("channel", [0, "0", 0.0], ids=repr)
def test_channel_zero_representations_select_channel_zero(channel):
    device = apply("D2-01-04", {"key": "switch", "value": "on", "channel": channel})
    assert set(device.channels) == {0}
    assert device.channels[0].is_on is True


def test_channel_selector_function_and_explicit_channel():
    device = apply(
        "D2-01-04",
        {"key": "channel", "value": "1"},
        {"key": "switch", "value": "on"},
    )
    assert device.channels[1].is_on is True
    device = apply("D2-01-04", {"key": "switch", "value": "off", "channel": 1})
    assert device.channels[1].is_on is False


@pytest.mark.parametrize("channel", [-1, "x", True, 1.5, None, [], {}], ids=repr)
def test_malformed_channel_is_ignored_instead_of_channel_zero(channel):
    device = apply("D2-01-04", {"key": "switch", "value": "on", "channel": channel})
    assert device.channels == {}


# ── Wrong types, booleans, non-finite numbers ───────────────────────────────


@pytest.mark.parametrize(
    "value",
    [True, False, None, float("nan"), float("inf"), "NaN", "Infinity", [], {}, "x"],
    ids=repr,
)
def test_invalid_numeric_values_keep_previous_reading(value):
    device = apply("D2-01-02", {"key": "dimValue", "value": "40"})
    device.update_from_telegram({"functions": [{"key": "dimValue", "value": value}]})
    assert device.channels[0].brightness == 40


@pytest.mark.parametrize(
    "value", [True, "NaN", "-Infinity", float("nan"), [], {"v": 1}], ids=repr
)
def test_invalid_temperature_is_rejected(value):
    device = apply("D1-4B-05", {"key": "temperature", "value": "21.5"})
    device.update_from_telegram({"functions": [{"key": "temperature", "value": value}]})
    assert device.channels[0].temperature == 21.5


@pytest.mark.parametrize("functions", [None, "x", 5, [], ["str", None, 5]], ids=repr)
def test_wrong_function_containers_are_ignored(functions):
    device = EnOceanDevice("DEV", "Device", [{"eep": "D2-01-00"}])
    device.update_from_telegram({"functions": functions})
    assert device.channels == {}


def test_single_function_object_and_mixed_list_are_tolerated():
    device = EnOceanDevice("DEV", "Device", [{"eep": "D2-01-00"}])
    device.update_from_telegram({"functions": {"key": "switch", "value": "on"}})
    assert device.channels[0].is_on is True
    device.update_from_telegram(
        {"functions": ["junk", None, {"key": "switch", "value": "off"}]}
    )
    assert device.channels[0].is_on is False


@pytest.mark.parametrize(
    ("info", "expected"),
    [
        ({"dbm": -70}, -70),
        ({"dbm": "-70"}, -70),
        ({"dbm": -70.0}, -70),
        ({"dbm": -70.5}, -60),
        ({"dbm": True}, -60),
        ({"dbm": "NaN"}, -60),
        ("not an object", -60),
        ([], -60),
    ],
    ids=repr,
)
def test_telegram_info_dbm_is_validated(info, expected):
    device = EnOceanDevice("DEV", "Device", [{"eep": "D2-01-00"}], dbm=-60)
    device.update_from_telegram({"functions": [], "telegramInfo": info})
    assert device.dbm == expected


@pytest.mark.parametrize(
    ("timestamp", "expected"), [("T1", "T1"), (5, ""), (["T"], "")]
)
def test_timestamp_must_be_text(timestamp, expected):
    device = EnOceanDevice("DEV", "Device", [{"eep": "D2-01-00"}])
    device.update_from_telegram({"functions": [], "timestamp": timestamp})
    assert device.last_seen == expected


# ── notAvailable and unknown additive fields ────────────────────────────────


@pytest.mark.parametrize(
    ("eep", "key", "attribute", "initial", "expected"),
    [
        # Contract (protocol-contract.md section 4): measurements clear...
        ("D1-4B-05", "temperature", "temperature", "21.5", None),
        ("D1-4B-06", "humidity", "humidity", "40", None),
        ("A5-07-01", "batteryLevel", "battery_level", "80", None),
        ("A5-07-01", "illumination", "illuminance", "200", None),
        # ...while actuator states ignore notAvailable and keep the reading.
        ("D2-05-00", "position", "position", "30", 30),
        ("D2-01-02", "dimValue", "brightness", "40", 40),
        ("D2-01-00", "switch", "is_on", "on", True),
    ],
)
def test_not_available_follows_the_existing_contract(
    eep, key, attribute, initial, expected
):
    device = apply(eep, {"key": key, "value": initial})
    device.update_from_telegram({"functions": [{"key": key, "value": "notAvailable"}]})
    assert getattr(device.channels[0], attribute) == expected


def test_unknown_additive_fields_are_ignored_not_rejected():
    device = EnOceanDevice("DEV", "Device", [{"eep": "D2-01-02", "future": 1}])
    device.update_from_telegram(
        {
            "functions": [
                {"key": "dimValue", "value": "60", "unit": "%", "meta": {"x": [1]}},
                {"key": "futureKey", "value": {"nested": True}},
            ],
            "telegramInfo": {"dbm": -55, "repeater": 2},
            "futureTopLevel": [1, 2],
        }
    )
    assert device.channels[0].brightness == 60
    assert device.dbm == -55
    assert device.primary_eep == "D2-01-02"


def test_primary_eep_requires_text():
    assert EnOceanDevice("D", "N", [{"eep": 5}]).primary_eep is None
    assert EnOceanDevice("D", "N", []).primary_eep is None
    assert EnOceanDevice("D", "N", [{"eep": 5}]).heat_area_type is None


def test_from_device_object_tolerates_wrapper_and_wrong_field_types():
    device = EnOceanDevice.from_device_object(
        {
            "device": {
                "deviceId": "X",
                "friendlyId": 5,
                "eeps": [{"eep": "D1-4B-06"}, "junk"],
                "dbm": "-40",
                "manufacturer": None,
                "unknown": {"a": 1},
            }
        }
    )
    assert device.device_id == "X"
    assert device.friendly_id == ""
    assert device.eeps == [{"eep": "D1-4B-06"}]
    assert device.dbm == -40
    assert device.manufacturer == ""
    assert device.heat_area_type == "cositherm"


# ── Coordinator REST boundaries ─────────────────────────────────────────────


def test_device_object_keeps_string_and_numeric_representations(coord):
    coord._apply_device_object(  # noqa: SLF001
        {
            "deviceId": "DEV",
            "eeps": [{"eep": "D2-05-00"}, "junk"],
            "friendlyId": 123,
            "manufacturer": "OPUS",
            "dbm": "-61",
            "states": [
                {"key": "position", "value": "notAvailable"},
                {"key": "angle", "value": "40"},
            ],
            "futureField": "kept",
        },
        authoritative=True,
    )
    device = coord.devices["DEV"]
    # A numeric name is rendered as text again.
    assert device.friendly_id == "123"
    assert device.manufacturer == "OPUS"
    assert device.eeps == [{"eep": "D2-05-00"}]
    assert device.dbm == -61
    assert device.channels[0].position is None
    assert device.channels[0].angle == 40


@pytest.mark.parametrize("name", [None, True, [], {"a": 1}, ""], ids=repr)
def test_device_object_without_usable_name_falls_back_to_the_id(coord, name):
    coord._apply_device_object(  # noqa: SLF001
        {"deviceId": "DEV", "friendlyId": name, "manufacturer": 5, "eeps": "x"},
        authoritative=True,
    )
    device = coord.devices["DEV"]
    assert device.friendly_id == "DEV"
    assert device.manufacturer == "5"
    assert device.eeps == []


@pytest.mark.parametrize("device_id", [None, 5, "", ["DEV"]], ids=repr)
def test_device_object_without_valid_id_is_ignored(coord, device_id):
    coord._apply_device_object(  # noqa: SLF001
        {"deviceId": device_id, "eeps": [{"eep": "D2-01-00"}]}, authoritative=True
    )
    assert coord.devices == {}


@pytest.mark.parametrize(
    "functions",
    [
        [{"key": "dimValue", "value": "50"}],
        [{"key": "dimValue", "value": 50}],
        [{"key": "dimValue", "value": 50.0}],
        {"key": "dimValue", "value": "50"},
        [{"key": "dimValue", "value": "50", "unit": "%", "meta": {"x": [1]}}],
        ["junk", None, 5, {"key": "dimValue", "value": "50"}],
    ],
    ids=repr,
)
def test_complete_telegram_representations(coord, functions):
    coord.devices["DEV"] = EnOceanDevice("DEV", "Light", [{"eep": "D2-01-02"}])
    coord._handle_telegram(from_device(functions, future=1))  # noqa: SLF001
    assert coord.devices["DEV"].channels[0].brightness == 50


def test_stream_document_carries_the_telegram_object(coord):
    coord.devices["DEV"] = EnOceanDevice("DEV", "Light", [{"eep": "D2-01-02"}])
    coord._handle_document(  # noqa: SLF001
        {
            "header": {"content": "telegram", "future": True},
            "telegram": from_device(
                [{"key": "dimValue", "value": "50"}], telegramInfo={"dbm": "-48"}
            ),
        }
    )
    assert coord.devices["DEV"].channels[0].brightness == 50
    assert coord.devices["DEV"].dbm == -48


@pytest.mark.parametrize(
    "telegram",
    [
        from_device([{"key": "dimValue", "value": "50"}], direction=None),
        from_device([{"key": "dimValue", "value": "50"}], direction="both"),
        from_device([{"key": "dimValue", "value": "50"}], deviceId=5),
        from_device("x"),
        from_device(None),
        from_device([]),
        from_device([{"key": 5, "value": "50"}]),
        from_device([{"key": "dimValue"}]),
        from_device([{"key": "dimValue", "value": None}]),
    ],
    ids=repr,
)
def test_malformed_telegram_is_ignored(coord, telegram):
    coord.devices["DEV"] = EnOceanDevice("DEV", "Light", [{"eep": "D2-01-02"}])
    coord._handle_telegram(telegram)  # noqa: SLF001
    assert coord.devices["DEV"].channels == {}
    assert coord.devices["DEV"].last_update_source == ""


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"header": "telegram"},
        {"header": {"content": "telegram"}, "telegram": 5},
        {"header": {"content": "telegram"}, "telegram": [from_device([])]},
        {"header": {"content": "device"}, "device": "DEV"},
        {"header": {"content": "devices"}, "devices": "x"},
        {"telegram": from_device([{"key": "dimValue", "value": "50"}])},
    ],
    ids=repr,
)
def test_malformed_stream_document_is_ignored(coord, document):
    coord.devices["DEV"] = EnOceanDevice("DEV", "Light", [{"eep": "D2-01-02"}])
    coord._handle_document(document)  # noqa: SLF001
    assert set(coord.devices) == {"DEV"}
    assert coord.devices["DEV"].channels == {}


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        ({"devices": [{"deviceId": "A1", "eeps": [{"eep": "D2-01-00"}]}]}, {"A1"}),
        (
            {"devices": [{"device": {"deviceId": "A2", "eeps": [{"eep": "D1"}]}}]},
            {"A2"},
        ),
        (
            {"devices": [{"deviceId": "A3", "eeps": [{"eep": "D2-05-00"}], "f": {}}]},
            {"A3"},
        ),
        ({"header": {"httpStatus": 200}, "devices": [{"deviceId": "A4"}]}, {"A4"}),
        ({"devices": "x"}, set()),
        ({"devices": {"0": {"deviceId": "A5"}}}, set()),
        ({"devices": [1, "x", None, {"device": "str"}, {"deviceId": 7}]}, set()),
        ({}, set()),
    ],
    ids=repr,
)
def test_device_list_validates_containers(coord, document, expected):
    coord._apply_device_list(document, authoritative=True)  # noqa: SLF001
    assert set(coord.devices) == expected


@pytest.mark.parametrize(
    ("body", "accepted"),
    [
        ('{"header": {"httpStatus": 200}}', True),
        ('{"header": {"httpStatus": "201"}}', True),
        ('{"header": {"httpStatus": 200.0}, "future": 1}', True),
        ('{"header": {"httpStatus": true}}', False),
        ('{"header": {"httpStatus": 409}}', False),
        ('{"header": "x"}', False),
        ('{"a": 1}', False),
        ("not json", False),
    ],
)
async def test_put_response_status_contract(
    gateway_coord: OpusGreenNetCoordinator,
    gateway: FakeGateway,
    body: str,
    accepted: bool,
):
    """The HTTP response body of the state PUT is the acknowledgement."""
    device = EnOceanDevice("DEV", "Light", [{"eep": "D2-01-02"}])
    device.last_command_error = "previous"
    gateway_coord.devices["DEV"] = device
    gateway.responses["/devices/DEV/state"] = body
    command = gateway_coord.async_send_command(
        "DEV", [{"key": "dimValue", "value": "50"}]
    )
    if accepted:
        await command
        assert device.last_command_error is None
    else:
        with pytest.raises(HomeAssistantError) as error:
            await command
        assert error.value.translation_key == "request_rejected"
        assert device.last_command_error not in (None, "previous")
    assert gateway.requests_to("PUT", "/devices/DEV/state") == [
        {"state": {"functions": [{"key": "dimValue", "value": "50"}]}}
    ]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("-55", -55),
        (-55.0, -55),
        (" -55 ", -55),
        (-55.5, -70),
        ("NaN", -70),
        (True, -70),
        (None, -70),
        ("notAvailable", -70),
    ],
    ids=repr,
)
@pytest.mark.parametrize("authoritative", [True, False])
def test_device_list_dbm_boundary(coord, value, expected, authoritative):
    """Device lists only replace the signal with a valid reading."""
    coord.devices["DEV"] = EnOceanDevice("DEV", "Light", [{"eep": "D2-01-02"}], dbm=-70)
    coord._apply_device_object(  # noqa: SLF001
        {"deviceId": "DEV", "eeps": [{"eep": "D2-01-02"}], "dbm": value},
        authoritative=authoritative,
    )
    assert coord.devices["DEV"].dbm == expected


def test_protocol_helpers_reject_wrong_types():
    assert protocol.metadata_text(12.5) == "12.5"
    assert protocol.metadata_text(False) == ""
    assert protocol.telegram_timestamp("2026-10-19T18:06:03.990+0200") == (
        pytest.approx(1792426563.99)
    )
    assert protocol.telegram_timestamp("19.10.2026") is None
    assert protocol.telegram_timestamp(1792426563) is None
    assert protocol.channel_from_functions([{"key": "channel", "value": "x"}]) is None
    assert protocol.channel_from_functions([{"key": "channel", "value": 2.0}]) == 2
    assert protocol.function_channel({"channel": True}, 0) is None
    assert protocol.device_state_functions({"states": "x", "state": []}) == []
    assert protocol.configuration_state_functions({"configuration": []}) == []
    assert (
        protocol.configuration_state_functions({"configuration": {"parameters": "x"}})
        == []
    )
