"""Phase 3: line-delimited (NDJSON) event stream with fallback.

Hardware evidence (OPUS-IQ-DOT 1.21.30, phase 2): ``delimited=newLine`` writes
one document per line ending in CRLF. The fake gateway reproduces that and the
legacy format; none of this replaces a hardware check of other firmware.
"""

from __future__ import annotations

import asyncio
import json
import logging

import aiohttp
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.opus_greennet.api import (
    JsonDocumentSplitter,
    OpusAuthError,
    OpusGatewayClient,
    OpusResponseError,
)
from tests.fake_gateway import FakeGateway, device_object, telegram
from tests.ha_helpers import setup_bridge, wait_for_entity, wait_until

QUERY = "delimited=newLine"
SWITCH = "050BF714"


@pytest.fixture
async def session():
    async with aiohttp.ClientSession() as client_session:
        yield client_session


def _client(session: aiohttp.ClientSession, gateway: FakeGateway, **kw):
    config = {**gateway.config, **kw}
    return OpusGatewayClient(
        session,
        config["host"],
        config["port"],
        config["username"],
        config["password"],
    )


async def _drain(client: OpusGatewayClient) -> list[dict]:
    """Read a stream that the gateway ends right after the device list."""
    return [document async for document in client.async_stream()]


async def _collect(
    client: OpusGatewayClient, gateway: FakeGateway, pushes: list[dict | str]
) -> list[dict]:
    received: list[dict] = []

    async def consume() -> None:
        async for document in client.async_stream():
            received.append(document)

    task = asyncio.create_task(consume())
    await gateway.wait_for_stream()
    for push in pushes:
        await gateway.push(push)
    await gateway.close_streams()
    await asyncio.wait_for(task, 5)
    return received


# ── splitter: line framing ─────────────────────────────────────────────────

DOCUMENTS = [
    {"header": {"content": "devices"}, "devices": [{"deviceId": "A"}]},
    {"text": "line\nbreak é €", "list": [1, {"a": "]"}]},
    {"header": {"content": "telegram"}, "telegram": {"deviceId": "A"}},
]


def _feed_all(data: bytes, size: int) -> list[str]:
    splitter = JsonDocumentSplitter()
    found: list[str] = []
    for start in range(0, len(data), size):
        found.extend(splitter.feed(data[start : start + size]))
    splitter.finish()
    return found


@pytest.mark.parametrize("terminator", ["\n", "\r\n", "\n\n", "\r\n\r\n", " \r\n"])
@pytest.mark.parametrize("size", [1, 2, 5, 64, 100_000])
def test_splitter_cuts_lines_with_any_terminator_and_chunking(
    terminator: str, size: int
) -> None:
    """Fragmented and bundled lines; LF, CRLF and blank lines; UTF-8 splits."""
    raw = "".join(json.dumps(doc, ensure_ascii=False) + terminator for doc in DOCUMENTS)
    found = _feed_all(raw.encode(), size)
    assert [json.loads(text) for text in found] == DOCUMENTS


def test_splitter_keeps_escaped_newline_inside_one_line() -> None:
    line = json.dumps({"text": "a\nb\r\nc"})
    assert "\n" not in line  # the newline is escaped as \n
    assert JsonDocumentSplitter().feed(line.encode() + b"\r\n") == [line]


def test_splitter_returns_a_valid_last_line_without_terminator() -> None:
    splitter = JsonDocumentSplitter()
    assert splitter.feed(b'{"a": 1}\r\n{"b": 2}') == ['{"a": 1}', '{"b": 2}']
    splitter.finish()  # complete at EOF: not an error


def test_splitter_finish_accepts_trailing_whitespace_only() -> None:
    splitter = JsonDocumentSplitter()
    splitter.feed(b'{"a": 1}\r\n\r\n  ')
    splitter.finish()


@pytest.mark.parametrize(
    "tail",
    [b'{"a": 1', b'{"a": "unfinished', b'[{"a": 1},', b'{"a": "\\'],
)
def test_splitter_finish_rejects_an_unfinished_document(tail: bytes) -> None:
    splitter = JsonDocumentSplitter()
    splitter.feed(b'{"ok": true}\r\n' + tail)
    with pytest.raises(OpusResponseError, match="ended inside a document"):
        splitter.finish()


def test_splitter_finish_rejects_a_cut_utf8_character_after_a_document() -> None:
    splitter = JsonDocumentSplitter()
    assert splitter.feed(b'{"a": 1}\r\n' + "é".encode()[:1]) == ['{"a": 1}']
    with pytest.raises(OpusResponseError, match="ended inside a document"):
        splitter.finish()


def test_splitter_limits_one_line_and_does_not_buffer_more() -> None:
    splitter = JsonDocumentSplitter(max_size=16)
    big = json.dumps({"a": "x" * 64}).encode() + b"\r\n"
    with pytest.raises(OpusResponseError, match="too large"):
        for start in range(0, len(big), 4):
            splitter.feed(big[start : start + 4])


# ── client: framing choice ─────────────────────────────────────────────────


async def test_stream_requests_line_framing_by_default(
    session: aiohttp.ClientSession, gateway: FakeGateway
) -> None:
    gateway.close_after_snapshot = True
    await _drain(_client(session, gateway))
    assert gateway.stream_queries == [QUERY]


@pytest.mark.parametrize("framing", ["legacy", "ndjson"])
async def test_both_formats_yield_the_same_documents(
    session: aiohttp.ClientSession, gateway: FakeGateway, framing: str
) -> None:
    """Same business messages, same documents, whatever the framing."""
    gateway.stream_framing = framing
    gateway.devices = [device_object("DEV1", "F6-02-01")]
    press = telegram("DEV1", [{"key": "buttonAI", "value": "pressed"}])
    other = {"header": {"content": "device"}, "device": {"text": "é\n€"}}
    received = await _collect(
        _client(session, gateway),
        gateway,
        [
            press,
            press,  # identical presses stay two documents
            json.dumps(other, ensure_ascii=False),
            "\r\n\r\n",  # blank lines between documents
            press,
        ],
    )
    assert received == [
        gateway.devices_document(),
        press,
        press,
        other,
        press,
    ]


@pytest.mark.parametrize("framing", ["legacy", "ndjson"])
async def test_stream_ending_inside_a_document_raises(
    session: aiohttp.ClientSession, gateway: FakeGateway, framing: str
) -> None:
    gateway.stream_framing = framing
    with pytest.raises(OpusResponseError, match="ended inside a document"):
        await _collect(
            _client(session, gateway), gateway, ['{"header": {"content": "tele']
        )


async def test_stream_delivers_a_last_document_without_terminator(
    session: aiohttp.ClientSession, gateway: FakeGateway
) -> None:
    gateway.stream_framing = "legacy"
    received = await _collect(_client(session, gateway), gateway, ['{"last": 1}'])
    assert received[-1] == {"last": 1}


@pytest.mark.parametrize("framing", ["legacy", "ndjson"])
async def test_stream_invalid_line_raises_without_skipping(
    session: aiohttp.ClientSession, gateway: FakeGateway, framing: str
) -> None:
    """A broken document is an error, never silently skipped."""
    gateway.stream_framing = framing
    with pytest.raises(OpusResponseError, match="invalid JSON"):
        await _collect(
            _client(session, gateway),
            gateway,
            [telegram("DEV1", []), '{"a": tru}', telegram("DEV2", [])],
        )


# ── client: fallback ───────────────────────────────────────────────────────


async def test_refused_parameter_falls_back_and_is_remembered(
    session: aiohttp.ClientSession, gateway: FakeGateway
) -> None:
    gateway.stream_framing = "reject"
    gateway.close_after_snapshot = True
    gateway.devices = [device_object("DEV1", "D2-01-01")]
    client = _client(session, gateway)

    first = await _drain(client)
    assert first == [gateway.devices_document()]
    assert gateway.stream_queries == [QUERY, ""]

    # The refusal is remembered: no repeated attempt with the parameter.
    await _drain(client)
    assert gateway.stream_queries == [QUERY, "", ""]

    # After a gateway restart the parameter is offered again once.
    client.reset_stream_framing()
    await _drain(client)
    assert gateway.stream_queries == [QUERY, "", "", QUERY, ""]


async def test_framing_choice_is_logged_without_host_or_credentials(
    session: aiohttp.ClientSession,
    gateway: FakeGateway,
    caplog: pytest.LogCaptureFixture,
) -> None:
    gateway.stream_framing = "reject"
    gateway.close_after_snapshot = True
    with caplog.at_level(logging.DEBUG, logger="custom_components.opus_greennet.api"):
        await _drain(_client(session, gateway))
    messages = [record.getMessage() for record in caplog.records]
    assert messages == [
        "OPUS event stream requested with line framing",
        "OPUS gateway refused line framing (HTTP 400); using the plain event stream",
        "OPUS event stream requested with plain framing",
    ]
    text = "\n".join(messages)
    assert gateway.host not in text
    assert gateway.password not in text


async def test_ignored_parameter_needs_no_fallback(
    session: aiohttp.ClientSession, gateway: FakeGateway
) -> None:
    gateway.stream_framing = "legacy"
    gateway.close_after_snapshot = True
    client = _client(session, gateway)
    await _drain(client)
    await _drain(client)
    assert gateway.stream_queries == [QUERY, QUERY]


async def test_other_statuses_do_not_switch_the_framing(
    session: aiohttp.ClientSession, gateway: FakeGateway
) -> None:
    """Server errors say nothing about the firmware."""
    gateway.status["/devices/stream"] = 503
    client = _client(session, gateway)
    with pytest.raises(OpusResponseError, match="status 503"):
        await _drain(client)
    assert len(gateway.requests_to("GET", "/devices/stream")) == 1
    del gateway.status["/devices/stream"]
    gateway.close_after_snapshot = True
    await _drain(client)
    assert gateway.stream_queries == [QUERY]


async def test_auth_error_is_not_taken_for_missing_support(
    session: aiohttp.ClientSession, gateway: FakeGateway
) -> None:
    gateway.accept_auth = False
    client = _client(session, gateway)
    with pytest.raises(OpusAuthError):
        await _drain(client)
    assert len(gateway.requests_to("GET", "/devices/stream")) == 1
    gateway.accept_auth = True
    gateway.close_after_snapshot = True
    await _drain(client)
    assert gateway.stream_queries == [QUERY]


async def test_connection_error_is_not_taken_for_missing_support(
    session: aiohttp.ClientSession, gateway: FakeGateway
) -> None:
    client = _client(session, gateway)
    await gateway.stop()
    with pytest.raises(Exception, match="event stream failed"):
        await _drain(client)
    await gateway.start()
    gateway.close_after_snapshot = True
    client = _client(session, gateway)
    await _drain(client)
    assert gateway.stream_queries == [QUERY]


async def test_fallback_failure_is_reported_once_per_attempt(
    session: aiohttp.ClientSession, gateway: FakeGateway
) -> None:
    """The plain stream failing too raises; nothing loops inside the client."""
    gateway.status["/devices/stream"] = 400
    with pytest.raises(OpusResponseError, match="status 400"):
        await _drain(_client(session, gateway))
    assert len(gateway.requests_to("GET", "/devices/stream")) == 2


async def test_closing_the_stream_releases_the_connection(
    session: aiohttp.ClientSession, gateway: FakeGateway
) -> None:
    gateway.stream_framing = "reject"
    client = _client(session, gateway)
    stream = client.async_stream()
    assert (await anext(stream))["header"]["content"] == "devices"
    await gateway.wait_for_stream()
    await stream.aclose()
    # The server notices the closed connection only on a later write: the first
    # one can still land in the socket buffer.
    for _ in range(20):
        if gateway.stream_count == 0:
            break
        await gateway.push(telegram("DEV1", []))
    assert gateway.stream_count == 0


# ── coordinator ────────────────────────────────────────────────────────────


def _switch(value: str = "off") -> dict:
    return device_object(SWITCH, "D2-01-01", states=[{"key": "switch", "value": value}])


@pytest.mark.parametrize("framing", ["legacy", "ndjson", "reject"])
async def test_setup_and_telegrams_work_in_every_framing(
    hass: HomeAssistant, gateway: FakeGateway, framing: str
) -> None:
    gateway.stream_framing = framing
    gateway.devices = [_switch("off")]
    await setup_bridge(hass, gateway)
    entity_id = await wait_for_entity(hass, "switch", f"AABB0011_{SWITCH}")
    assert hass.states.get(entity_id).state == "off"
    assert gateway.stream_queries[0] == QUERY

    await gateway.push(telegram(SWITCH, [{"key": "switch", "value": "on"}]))
    await wait_until(lambda: hass.states.get(entity_id).state == "on")
    # One reader per gateway, whichever framing was negotiated.
    assert gateway.stream_count == 1


async def test_gateway_restart_offers_line_framing_again(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.stream_framing = "reject"
    gateway.devices = [_switch()]
    entry = await setup_bridge(hass, gateway)
    assert gateway.stream_queries == [QUERY, ""]

    gateway.uptime = 5
    coordinator = entry.runtime_data.coordinator
    coordinator._async_health_tick(dt_util.utcnow())  # noqa: SLF001
    await coordinator._probe_task  # noqa: SLF001
    await wait_until(lambda: len(gateway.stream_queries) >= 4)
    assert gateway.stream_queries[2:4] == [QUERY, ""]
