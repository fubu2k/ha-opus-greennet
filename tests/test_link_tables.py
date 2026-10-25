"""Link tables: the read-only ReCom action and the masking of the gateway.

``GET /devices/{id}/linkTables`` lists the wall switches a device listens to
(``inbound``) and the devices it reports to (``outbound``). The outbound list
names the gateway itself, and the gateway's EURID is also its default password,
so the answer never leaves the integration with that identifier in clear text.
Writing link tables is deliberately not offered.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.opus_greennet import protocol
from custom_components.opus_greennet.api import RECOM_TIMEOUT
from custom_components.opus_greennet.const import DOMAIN
from custom_components.opus_greennet.coordinator import (
    SIGNAL_DEVICE_STATE_UPDATE,
    OpusGreenNetCoordinator,
)
from custom_components.opus_greennet.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.opus_greennet.enocean_device import (
    EnOceanDevice,
    LinkedDevice,
    LinkTables,
)
from custom_components.opus_greennet.sensor import (
    OpusGreenNetWallSwitchLinksSensor,
)
from custom_components.opus_greennet.sensor import (
    async_setup_entry as async_setup_sensors,
)
from tests.fake_gateway import FakeGateway, device_object
from tests.test_service_actions import DEVICE_ID, _setup_entry

FIXTURE = Path(__file__).parent / "fixtures" / "link_tables_d2_01_01.json"
GATEWAY_ID = "AABB0011"
LINK_TABLES_PATH = f"/devices/{DEVICE_ID}/linkTables"
MODULE = "custom_components.opus_greennet.coordinator"


def _document(gateway_id: str = GATEWAY_ID) -> dict:
    """The recorded answer with the gateway placeholder replaced."""
    text = FIXTURE.read_text(encoding="utf8").replace("GATEWAY", gateway_id)
    return json.loads(text)


def _outbound_ids(document: dict) -> list[str]:
    return [entry["deviceId"] for entry in document["linkTables"]["outbound"]]


# ── Fixture ──────────────────────────────────────────────────────────────────


def test_fixture_is_anonymized() -> None:
    text = FIXTURE.read_text(encoding="utf8")
    document = json.loads(text)
    assert set(document["header"]) == {"httpStatus", "content"}
    assert document["header"]["content"] == "linkTables"
    assert not re.search(r"\b[0-9A-Fa-f]{8}\b", text)
    assert not re.search(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}|\d{4}-\d{2}-\d{2}", text)
    assert set(document["linkTables"]) == {"inbound", "outbound", "lastUpdateTime"}


# ── Masking ──────────────────────────────────────────────────────────────────


def test_only_the_gateway_identifier_is_masked() -> None:
    document = _document()
    masked = protocol.mask_gateway_id(document, GATEWAY_ID)
    assert isinstance(masked, dict)
    assert _outbound_ids(masked) == [protocol.MASKED_GATEWAY_ID, "OTHER"]
    inbound = masked["linkTables"]["inbound"]
    assert [entry["deviceId"] for entry in inbound] == ["WALL1", "WALL2"]
    # Everything else is passed on unchanged.
    expected = _document()
    expected["linkTables"]["outbound"][0]["deviceId"] = protocol.MASKED_GATEWAY_ID
    assert masked == expected


def test_masking_ignores_case_and_leaves_the_input_untouched() -> None:
    document = _document(GATEWAY_ID.lower())
    snapshot = copy.deepcopy(document)
    masked = protocol.mask_gateway_id(document, GATEWAY_ID)
    assert isinstance(masked, dict)
    assert _outbound_ids(masked)[0] == protocol.MASKED_GATEWAY_ID
    assert document == snapshot
    assert masked is not document


def test_masking_replaces_nothing_but_a_whole_value() -> None:
    document = {
        "deviceId": f"x{GATEWAY_ID}x",
        "other": [GATEWAY_ID, 1, 2.5, True, None, {"deviceId": GATEWAY_ID}],
    }
    masked = protocol.mask_gateway_id(document, GATEWAY_ID)
    assert masked == {
        "deviceId": f"x{GATEWAY_ID}x",
        "other": [
            protocol.MASKED_GATEWAY_ID,
            1,
            2.5,
            True,
            None,
            {"deviceId": protocol.MASKED_GATEWAY_ID},
        ],
    }


def test_masking_without_a_gateway_identifier_changes_nothing() -> None:
    document = _document()
    assert protocol.mask_gateway_id(document, "") == document


# ── Coordinator ──────────────────────────────────────────────────────────────


@pytest.fixture
def coord() -> Iterator[OpusGreenNetCoordinator]:
    coordinator = OpusGreenNetCoordinator(MagicMock(), MagicMock(), GATEWAY_ID)
    coordinator._gateway_available = True
    coordinator.client.async_get = AsyncMock(return_value=_document())
    coordinator.client.async_put = AsyncMock()
    with patch(f"{MODULE}.async_call_later", side_effect=lambda *a: MagicMock()):
        yield coordinator


async def test_link_tables_are_read_over_the_radio_with_the_long_timeout(
    coord,
) -> None:
    answer = await coord.async_get_device_link_tables("DEV1")
    coord.client.async_get.assert_awaited_once_with(
        "/devices/DEV1/linkTables", timeout=RECOM_TIMEOUT
    )
    assert _outbound_ids(answer) == [protocol.MASKED_GATEWAY_ID, "OTHER"]
    assert GATEWAY_ID not in json.dumps(answer)
    coord.client.async_put.assert_not_awaited()


async def test_link_table_request_fails_fast_while_gateway_unavailable(coord) -> None:
    coord._gateway_available = False
    with pytest.raises(HomeAssistantError) as error:
        await coord.async_get_device_link_tables("DEV1")
    assert error.value.translation_key == "gateway_unavailable"
    coord.client.async_get.assert_not_awaited()


# ── Action ───────────────────────────────────────────────────────────────────


@pytest.fixture
def link_gateway(gateway: FakeGateway) -> FakeGateway:
    gateway.devices = [
        device_object(
            DEVICE_ID,
            "D2-01-01",
            friendly_id="Desk switch",
            states=[{"key": "switch", "value": "off"}],
        )
    ]
    gateway.responses[LINK_TABLES_PATH] = _document(gateway.eurid)
    return gateway


async def _get_link_tables(hass: HomeAssistant, **data):
    return await hass.services.async_call(
        DOMAIN,
        "get_device_link_tables",
        {"device_id": DEVICE_ID, **data},
        blocking=True,
        return_response=True,
    )


async def test_action_returns_the_link_tables_without_the_gateway_identifier(
    hass: HomeAssistant, link_gateway: FakeGateway
) -> None:
    entry = await _setup_entry(hass, link_gateway)
    answer = await _get_link_tables(hass, config_entry_id=entry.entry_id)
    assert _outbound_ids(answer) == [protocol.MASKED_GATEWAY_ID, "OTHER"]
    assert link_gateway.eurid not in json.dumps(answer)
    assert answer["linkTables"]["inbound"][0]["deviceId"] == "WALL1"
    assert link_gateway.requests_to("GET", LINK_TABLES_PATH) == [None]
    # Reading never writes anything to a device, and no write action exists:
    # a wrong table could cut a device off its wall switches.
    assert not [
        method
        for method, path, _ in link_gateway.requests
        if method != "GET" and "linkTables" in path
    ]
    assert not hass.services.has_service(DOMAIN, "set_device_link_tables")


async def test_action_rejects_an_unknown_device_without_a_request(
    hass: HomeAssistant, link_gateway: FakeGateway
) -> None:
    await _setup_entry(hass, link_gateway)
    with pytest.raises(ServiceValidationError) as error:
        await hass.services.async_call(
            DOMAIN,
            "get_device_link_tables",
            {"device_id": "DEADBEEF"},
            blocking=True,
            return_response=True,
        )
    assert error.value.translation_key == "device_not_found"
    assert not [p for _, p, _ in link_gateway.requests if "DEADBEEF" in p]


async def test_rejected_read_surfaces_a_translated_error(
    hass: HomeAssistant, link_gateway: FakeGateway
) -> None:
    await _setup_entry(hass, link_gateway)
    link_gateway.status[LINK_TABLES_PATH] = 500
    with pytest.raises(HomeAssistantError) as error:
        await _get_link_tables(hass)
    assert error.value.translation_key == "request_rejected"
    assert len(link_gateway.requests_to("GET", LINK_TABLES_PATH)) == 1


# ── Reading link tables from a device object ────────────────────────────────

WALL1_PARAMETERS = (
    ("rocker", "B0BI"),
    ("illuminationThreshold", "false"),
    ("centralOn", "false"),
    ("centralOff", "false"),
    ("smartMotionSensor", "false"),
)
RECORDED = LinkTables(
    inbound=(
        LinkedDevice("WALL1", "F6-02-01", WALL1_PARAMETERS),
        LinkedDevice("WALL2", "F6-02-01", (("rocker", "A0AI"), ("centralOn", "true"))),
    ),
    outbound=(
        LinkedDevice(GATEWAY_ID, "D2-01-01"),
        LinkedDevice("OTHER", "D2-01-01"),
    ),
)


def test_the_recorded_table_is_read_completely() -> None:
    parsed = protocol.link_tables({"linkTables": _document()["linkTables"]})
    assert parsed == RECORDED


def test_a_table_without_inbound_entries_has_none() -> None:
    """The gateway omits an empty ``inbound`` list (seen on every plain actuator)."""
    parsed = protocol.link_tables(
        {"linkTables": {"outbound": [{"deviceId": "OTHER", "eep": "D2-01-01"}]}}
    )
    assert parsed == LinkTables(outbound=(LinkedDevice("OTHER", "D2-01-01"),))
    assert parsed is not None
    assert parsed.inbound == ()


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"linkTables": None},
        {"linkTables": {}},  # devices without a table (sensors) list an empty object
        {"linkTables": []},
        {"linkTables": "x"},
        {"linkTables": {"inbound": "x"}},
        {"linkTables": {"inbound": None, "outbound": None}},
        {"linkTables": {"lastUpdateTime": "LAST_UPDATE"}},
    ],
)
def test_no_table_read_is_unknown_not_empty(data) -> None:
    assert protocol.link_tables(data) is None


def test_unusable_entries_and_parameters_are_skipped() -> None:
    parsed = protocol.link_tables(
        {
            "linkTables": {
                "inbound": [
                    5,
                    "WALL",
                    {"eep": "F6-02-01"},
                    {"deviceId": "", "eep": "F6-02-01"},
                    {"deviceId": 7, "eep": "F6-02-01"},
                    {"deviceId": "WALL3"},
                    {
                        "deviceId": "WALL4",
                        "eep": "F6-02-01",
                        "friendlyId": "Hall switch",
                        "parameters": [
                            {"key": "channel", "value": 1},
                            {"key": "ratio", "value": 1.5},
                            {"key": "flag", "value": True},
                            {"key": "empty", "value": None},
                            {"key": 2, "value": "a"},
                            {"key": "", "value": "a"},
                            "junk",
                            {"key": "rocker", "value": "A0AI"},
                        ],
                    },
                ]
            }
        }
    )
    assert parsed is not None
    assert parsed.inbound == (
        LinkedDevice("WALL3", ""),
        LinkedDevice(
            "WALL4",
            "F6-02-01",
            (("channel", "1"), ("ratio", "1.5"), ("rocker", "A0AI")),
        ),
    )
    # Gateway names are not kept: they are data of the gateway, not of the link.
    assert "Hall switch" not in repr(parsed)


# ── The device list owns the tables, a missing table never clears them ──────

DEV = "AABB2233"
MODULE_COORD = "custom_components.opus_greennet.coordinator"


@pytest.fixture
def listing_coord() -> Iterator[tuple[OpusGreenNetCoordinator, MagicMock]]:
    coordinator = OpusGreenNetCoordinator(MagicMock(), MagicMock(), GATEWAY_ID)
    coordinator._gateway_available = True  # noqa: SLF001
    with patch(f"{MODULE_COORD}.async_dispatcher_send") as send:
        yield coordinator, send


def _list(**fields: object) -> dict:
    return {
        "header": {"content": "devices"},
        "devices": [device_object(DEV, "D2-01-01", **fields)],
    }


def test_list_stores_replaces_and_keeps_link_tables(listing_coord) -> None:
    coord, _ = listing_coord
    coord._apply_device_list(  # noqa: SLF001
        _list(linkTables=_document()["linkTables"]), authoritative=True
    )
    device = coord.devices[DEV]
    assert device.link_tables == RECORDED

    # A list without the block, or with an empty one, says nothing: keep it.
    coord._apply_device_list(_list(), authoritative=False)  # noqa: SLF001
    assert coord.devices[DEV] is device
    assert device.link_tables == RECORDED
    coord._apply_device_list(  # noqa: SLF001
        _list(linkTables={}), authoritative=False
    )
    assert device.link_tables == RECORDED

    # A table that lists only the outbound side: the inbound links are gone.
    coord._apply_device_list(  # noqa: SLF001
        _list(linkTables={"outbound": [{"deviceId": "OTHER", "eep": "D2-01-01"}]}),
        authoritative=False,
    )
    assert device.link_tables == LinkTables(
        outbound=(LinkedDevice("OTHER", "D2-01-01"),)
    )


def test_a_changed_table_is_dispatched_to_the_entities(listing_coord) -> None:
    coord, send = listing_coord
    coord._apply_device_list(  # noqa: SLF001
        _list(linkTables=_document()["linkTables"]), authoritative=True
    )
    send.reset_mock()
    coord._apply_device_list(  # noqa: SLF001
        _list(linkTables={"outbound": []}), authoritative=False
    )
    signals = [call.args[1] for call in send.call_args_list]
    assert f"{SIGNAL_DEVICE_STATE_UPDATE}_{GATEWAY_ID}_{DEV}" in signals


# ── Sensor ───────────────────────────────────────────────────────────────────


def test_sensor_counts_the_linked_wall_switches(make_device) -> None:
    device = make_device("D2-01-01")
    sensor = OpusGreenNetWallSwitchLinksSensor(MagicMock(), GATEWAY_ID, "gw", device)
    assert sensor.native_value is None  # nothing read yet
    device.link_tables = RECORDED
    assert sensor.native_value == 2
    device.link_tables = LinkTables(outbound=(LinkedDevice("OTHER", "D2-01-01"),))
    assert sensor.native_value == 0  # read, and no wall switch is linked
    assert sensor.unique_id == f"{GATEWAY_ID}_{device.device_id}_wall_switch_links"
    assert sensor.translation_key == "wall_switch_links"
    assert sensor.entity_category is EntityCategory.DIAGNOSTIC
    assert sensor.entity_registry_enabled_default is False


async def test_sensor_exists_only_for_devices_with_a_table(make_device) -> None:
    coordinator = MagicMock()
    coordinator.devices = {}
    plain = make_device("D2-01-01", device_id="PLAIN")
    linked = make_device("D2-01-01", device_id="LINKED")
    linked.link_tables = RECORDED
    entry = SimpleNamespace(
        data={"eag_id": GATEWAY_ID},
        runtime_data=SimpleNamespace(coordinator=coordinator, gateway_device_id="gw"),
        async_on_unload=MagicMock(),
    )
    added = MagicMock()
    with patch(
        "custom_components.opus_greennet.sensor.async_dispatcher_connect"
    ) as connect:
        await async_setup_sensors(MagicMock(), entry, added)
    discovered = connect.call_args.args[2]
    discovered(plain)
    discovered(linked)
    discovered(linked)  # a repeated discovery adds nothing twice
    unique_ids = [
        entity.unique_id for call in added.call_args_list for entity in call.args[0]
    ]
    assert f"{GATEWAY_ID}_LINKED_wall_switch_links" in unique_ids
    assert unique_ids.count(f"{GATEWAY_ID}_LINKED_wall_switch_links") == 1
    assert f"{GATEWAY_ID}_PLAIN_wall_switch_links" not in unique_ids


# ── Diagnostics ──────────────────────────────────────────────────────────────


async def test_diagnostics_show_the_links_without_any_identifier() -> None:
    linked = EnOceanDevice(DEV, "Desk", [{"eep": "D2-01-01"}])
    linked.link_tables = RECORDED
    unread = EnOceanDevice("AABB2244", "Hall", [{"eep": "A5-07-03"}])
    # A plain namespace, not a MagicMock: the report must be JSON serializable.
    coordinator = SimpleNamespace(
        eag_id=GATEWAY_ID,
        gateway_info={},
        gateway_uptime="1 day",
        available=True,
        devices={DEV: linked, "AABB2244": unread},
    )
    entry = SimpleNamespace(
        entry_id="entry-id",
        title="Bridge",
        data={"eag_id": GATEWAY_ID, "password": GATEWAY_ID},
        runtime_data=SimpleNamespace(coordinator=coordinator),
    )

    result = await async_get_config_entry_diagnostics(MagicMock(), entry)

    text = json.dumps(result)
    for identifier in (GATEWAY_ID, DEV, "WALL1", "WALL2", "OTHER", "Desk"):
        assert identifier not in text
    tables = result["devices"][0]["link_tables"]
    assert [entry["eep"] for entry in tables["inbound"]] == ["F6-02-01"] * 2
    assert dict(tables["inbound"][0]["parameters"])["rocker"] == "B0BI"
    assert dict(tables["inbound"][1]["parameters"])["centralOn"] == "true"
    assert len(tables["outbound"]) == 2
    assert result["devices"][1]["link_tables"] is None
