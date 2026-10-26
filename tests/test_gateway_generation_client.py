"""REST client of the two gateway generations.

Gateways before WebUI 2026 are reached over plain HTTP with basic
authentication. Gateways with WebUI 2026 are reached over HTTPS, pinned to the
remembered certificate, with a user login and a token. The login endpoint and
the token header are assumptions (see ``api.py``); these tests pin the
contract of the client around them: the credentials go only into the login, the
token stays in memory, a rejected token is never answered by repeating the
request, and nothing secret appears in an error text.

The tests use a fake session, so they need neither Home Assistant nor a TLS
server.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import aiohttp
import pytest

from custom_components.opus_greennet import api
from custom_components.opus_greennet.api import (
    OpusAuthError,
    OpusCertificateError,
    OpusConnectionError,
    OpusGatewayClient,
    OpusResponseError,
    OpusTokenExpiredError,
    async_probe_webui_2026,
    async_read_certificate_fingerprint,
)
from custom_components.opus_greennet.const import (
    GENERATION_LEGACY,
    GENERATION_WEBUI_2026,
)

PIN = hashlib.sha256(b"certificate of the gateway").hexdigest()
PASSWORD = "S3cret-passw0rd"
TOKEN = "tok-123"
INFO = json.dumps({"header": {"httpStatus": 200}, "systemInfo": {"eurid": "AABB0011"}})
LOGIN_OK = json.dumps({"token": TOKEN})


class FakeResponse:
    """The parts of ``aiohttp.ClientResponse`` the client reads."""

    def __init__(self, status: int = 200, body: str = INFO) -> None:
        self.status = status
        self._body = body.encode()
        self.content_length = len(self._body)
        self.content = self

    async def iter_chunked(self, size: int):
        yield self._body

    async def iter_any(self):
        yield self._body


class _Context:
    def __init__(self, outcome: FakeResponse | BaseException) -> None:
        self._outcome = outcome

    async def __aenter__(self) -> FakeResponse:
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return self._outcome

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class FakeSession:
    """Answers in the order given and records every call."""

    def __init__(self, *outcomes: FakeResponse | BaseException, delay: float = 0):
        self.calls: list[SimpleNamespace] = []
        self._outcomes = list(outcomes)
        self._delay = delay

    def _next(self, method: str, url: str, kwargs: dict[str, Any]) -> _Context:
        self.calls.append(SimpleNamespace(method=method, url=url, **kwargs))
        return _Context(self._outcomes.pop(0))

    def request(self, method: str, url: str, **kwargs: Any) -> _Context:
        return self._next(method, url, kwargs)

    def get(self, url: str, **kwargs: Any) -> _Context:
        return self._next("GET", url, kwargs)

    def post(self, url: str, **kwargs: Any) -> _Context:
        return self._next("POST", url, kwargs)


def _legacy(session: FakeSession, port: int = 8080) -> OpusGatewayClient:
    return OpusGatewayClient(session, "192.168.1.5", port, "admin", "AABB0011")  # type: ignore[arg-type]


def _new(
    session: FakeSession, fingerprint: str | None = PIN, host: str = "192.168.1.5"
) -> OpusGatewayClient:
    return OpusGatewayClient(
        session,  # type: ignore[arg-type]
        host,
        443,
        "installer",
        PASSWORD,
        generation=GENERATION_WEBUI_2026,
        fingerprint=fingerprint,
    )


# ── the earlier generation is unchanged ────────────────────────────────────


async def test_the_earlier_generation_uses_plain_http_and_basic_authentication():
    session = FakeSession(FakeResponse())
    client = _legacy(session)

    await client.async_get("/system/info")

    (call,) = session.calls
    assert call.url == "http://192.168.1.5:8080/system/info"
    assert call.headers["Authorization"].startswith("Basic ")
    assert call.ssl is True
    assert client.generation == GENERATION_LEGACY


async def test_the_default_generation_is_the_earlier_one():
    assert _legacy(FakeSession()).generation == GENERATION_LEGACY


# ── WebUI 2026: login, token, certificate ──────────────────────────────────


async def test_the_first_request_logs_in_and_then_sends_the_token():
    session = FakeSession(FakeResponse(200, LOGIN_OK), FakeResponse())
    client = _new(session)

    await client.async_get("/system/info")

    login, request = session.calls
    assert (login.method, login.url) == ("POST", "https://192.168.1.5:443/auth/login")
    assert login.json == {"username": "installer", "password": PASSWORD}
    assert (request.method, request.url) == (
        "GET",
        "https://192.168.1.5:443/system/info",
    )
    assert request.headers == {"Authorization": f"Bearer {TOKEN}"}
    # Same paths as before; the certificate is pinned for both requests.
    assert isinstance(login.ssl, aiohttp.Fingerprint)
    assert isinstance(request.ssl, aiohttp.Fingerprint)
    assert login.allow_redirects is False
    assert request.allow_redirects is False


async def test_the_token_is_reused():
    session = FakeSession(FakeResponse(200, LOGIN_OK), FakeResponse(), FakeResponse())
    client = _new(session)

    await client.async_get("/system/info")
    await client.async_get("/system/info")

    assert [call.method for call in session.calls] == ["POST", "GET", "GET"]


async def test_requests_made_together_log_in_once():
    class Slow(FakeSession):
        def post(self, url: str, **kwargs: Any) -> _Context:
            self.calls.append(SimpleNamespace(method="POST", url=url, **kwargs))
            return _SlowContext(self._outcomes.pop(0))

    class _SlowContext(_Context):
        async def __aenter__(self) -> FakeResponse:
            await asyncio.sleep(0.01)
            return await super().__aenter__()

    session = Slow(FakeResponse(200, LOGIN_OK), FakeResponse(), FakeResponse())
    client = _new(session)

    await asyncio.gather(
        client.async_get("/system/info"), client.async_get("/system/info")
    )

    assert [call.method for call in session.calls].count("POST") == 1


async def test_a_rejected_token_is_not_retried_but_requested_again_next_time():
    session = FakeSession(
        FakeResponse(200, LOGIN_OK),
        FakeResponse(401, ""),
        FakeResponse(200, json.dumps({"token": "tok-456"})),
        FakeResponse(),
    )
    client = _new(session)

    with pytest.raises(OpusTokenExpiredError):
        await client.async_get("/system/info")
    # The request that met the rejection was sent once and not repeated.
    assert [call.method for call in session.calls] == ["POST", "GET"]

    await client.async_get("/system/info")

    assert [call.method for call in session.calls] == ["POST", "GET", "POST", "GET"]
    assert session.calls[-1].headers == {"Authorization": "Bearer tok-456"}


async def test_a_write_met_by_a_rejected_token_is_not_sent_again():
    session = FakeSession(FakeResponse(200, LOGIN_OK), FakeResponse(401, ""))
    client = _new(session)

    with pytest.raises(OpusTokenExpiredError):
        await client.async_put("/devices/D1/state", {"state": {}})

    assert [call.method for call in session.calls] == ["POST", "PUT"]


def test_a_rejected_token_is_a_connection_problem_not_a_credential_problem():
    """The coordinator treats it as an outage, not as a reason to ask again."""
    assert issubclass(OpusTokenExpiredError, OpusConnectionError)
    assert not issubclass(OpusTokenExpiredError, OpusAuthError)


async def test_403_is_an_authentication_error():
    """Forbidden for the role: asking for a new token does not help."""
    session = FakeSession(FakeResponse(200, LOGIN_OK), FakeResponse(403, ""))
    client = _new(session)

    with pytest.raises(OpusAuthError) as raised:
        await client.async_get("/system/info")

    assert not isinstance(raised.value, OpusTokenExpiredError)


@pytest.mark.parametrize("status", [401, 403])
async def test_a_rejected_login_is_an_authentication_error_without_secrets(status):
    session = FakeSession(FakeResponse(status, ""))
    client = _new(session)

    with pytest.raises(OpusAuthError) as raised:
        await client.async_get("/system/info")

    assert not isinstance(raised.value, OpusTokenExpiredError)
    assert PASSWORD not in str(raised.value)
    assert [call.method for call in session.calls] == ["POST"]


@pytest.mark.parametrize(
    "body", ["", "not json", "[]", "{}", json.dumps({"token": ""}), '{"token": 5}']
)
async def test_a_login_without_a_token_is_a_response_error(body):
    session = FakeSession(FakeResponse(200, body))
    client = _new(session)

    with pytest.raises(OpusResponseError) as raised:
        await client.async_get("/system/info")

    assert PASSWORD not in str(raised.value)


async def test_a_login_with_an_http_error_is_a_response_error():
    session = FakeSession(FakeResponse(500, ""))

    with pytest.raises(OpusResponseError):
        await _new(session).async_get("/system/info")


async def test_the_token_is_not_part_of_an_error_text():
    session = FakeSession(FakeResponse(200, LOGIN_OK), FakeResponse(500, ""))

    with pytest.raises(OpusResponseError) as raised:
        await _new(session).async_get("/system/info")

    assert TOKEN not in str(raised.value)
    assert PASSWORD not in str(raised.value)


async def test_a_changed_certificate_is_reported_as_such():
    mismatch = aiohttp.ServerFingerprintMismatch(
        bytes.fromhex(PIN), b"\x00" * 32, "192.168.1.5", 443
    )
    session = FakeSession(mismatch)

    with pytest.raises(OpusCertificateError) as raised:
        await _new(session).async_get("/system/info")

    # An authentication failure: the connection is trusted again only after
    # the user confirmed the certificate.
    assert isinstance(raised.value, OpusAuthError)
    assert PIN not in str(raised.value)


async def test_a_changed_certificate_is_reported_for_a_request_too():
    mismatch = aiohttp.ServerFingerprintMismatch(
        bytes.fromhex(PIN), b"\x00" * 32, "192.168.1.5", 443
    )
    session = FakeSession(FakeResponse(200, LOGIN_OK), mismatch)

    with pytest.raises(OpusCertificateError):
        await _new(session).async_get("/system/info")


@pytest.mark.parametrize("fingerprint", [None, "", "not hex", "ab" * 16, "ab" * 33])
async def test_without_a_valid_pin_nothing_is_sent(fingerprint):
    session = FakeSession()

    with pytest.raises(OpusCertificateError):
        await _new(session, fingerprint).async_get("/system/info")

    assert session.calls == []


async def test_a_fingerprint_with_colons_is_accepted():
    colon = ":".join(PIN[i : i + 2] for i in range(0, len(PIN), 2)).upper()
    session = FakeSession(FakeResponse(200, LOGIN_OK), FakeResponse())

    await _new(session, colon).async_get("/system/info")

    assert len(session.calls) == 2


async def test_a_network_error_during_the_login_is_a_connection_error():
    session = FakeSession(aiohttp.ClientConnectionError("refused"))

    with pytest.raises(OpusConnectionError):
        await _new(session).async_get("/system/info")


async def test_a_login_that_times_out_is_a_timeout_error():
    session = FakeSession(TimeoutError())

    with pytest.raises(api.OpusTimeoutError):
        await _new(session).async_get("/system/info")


async def test_a_changed_certificate_is_reported_for_the_event_stream():
    mismatch = aiohttp.ServerFingerprintMismatch(
        bytes.fromhex(PIN), b"\x00" * 32, "192.168.1.5", 443
    )
    session = FakeSession(FakeResponse(200, LOGIN_OK), mismatch)

    with pytest.raises(OpusCertificateError):
        async for _ in _new(session).async_stream():
            pass


async def test_ipv6_hosts_are_bracketed_in_the_https_address():
    session = FakeSession(FakeResponse(200, LOGIN_OK), FakeResponse())

    await _new(session, host="fe80::1").async_get("/system/info")

    assert session.calls[0].url == "https://[fe80::1]:443/auth/login"


async def test_the_event_stream_uses_the_token_and_the_pin():
    session = FakeSession(FakeResponse(200, LOGIN_OK), FakeResponse(200, "{}"))
    client = _new(session)

    documents = [document async for document in client.async_stream()]

    login, stream = session.calls
    assert login.method == "POST"
    assert stream.url.startswith("https://192.168.1.5:443/devices/stream")
    assert stream.headers == {"Authorization": f"Bearer {TOKEN}"}
    assert isinstance(stream.ssl, aiohttp.Fingerprint)
    assert documents == [{}]


async def test_a_rejected_token_ends_the_event_stream_with_a_connection_error():
    session = FakeSession(FakeResponse(200, LOGIN_OK), FakeResponse(401, ""))

    with pytest.raises(OpusTokenExpiredError):
        async for _ in _new(session).async_stream():
            pass

    # One attempt: the stream is reopened by the coordinator, not by the client.
    assert [call.method for call in session.calls] == ["POST", "GET"]


# ── recognising the generation ─────────────────────────────────────────────


async def test_the_setup_status_is_read_without_credentials_or_certificate_check():
    session = FakeSession(FakeResponse(200, json.dumps({"setupRequired": False})))

    assert await async_probe_webui_2026(session, "192.168.1.5", 443) is True  # type: ignore[arg-type]

    (call,) = session.calls
    assert call.url == "https://192.168.1.5:443/auth/setup-status"
    assert call.ssl is False
    assert not hasattr(call, "headers")


@pytest.mark.parametrize(
    "outcome",
    [
        FakeResponse(404, "{}"),
        FakeResponse(200, "<html>"),
        FakeResponse(200, "[]"),
        aiohttp.ClientConnectionError("refused"),
        TimeoutError(),
    ],
)
async def test_anything_else_is_not_a_webui_2026_gateway(outcome):
    assert (
        await async_probe_webui_2026(FakeSession(outcome), "192.168.1.5", 443) is False
    )  # type: ignore[arg-type]


class _Writer:
    def __init__(self, certificate: bytes | None) -> None:
        self.closed = False
        self._certificate = certificate

    def get_extra_info(self, name: str) -> object:
        certificate = self._certificate
        return SimpleNamespace(getpeercert=lambda binary_form: certificate)

    def close(self) -> None:
        self.closed = True


async def test_the_certificate_fingerprint_is_the_sha256_of_the_certificate():
    writer = _Writer(b"certificate of the gateway")

    async def connect(host, port, **kwargs):
        assert (host, port) == ("192.168.1.5", 443)
        assert kwargs["server_hostname"] == ""
        context = kwargs["ssl"]
        assert context.verify_mode.name == "CERT_NONE"
        assert context.check_hostname is False
        return None, writer

    with patch.object(api.asyncio, "open_connection", connect):
        assert await async_read_certificate_fingerprint("192.168.1.5", 443) == PIN

    assert writer.closed


async def test_an_ipv6_literal_is_connected_without_brackets():
    async def connect(host, port, **kwargs):
        assert host == "fe80::1"
        return None, _Writer(b"x")

    with patch.object(api.asyncio, "open_connection", connect):
        await async_read_certificate_fingerprint("[fe80::1]", 443)


async def test_a_failed_handshake_is_a_connection_error():
    async def connect(host, port, **kwargs):
        raise ConnectionRefusedError

    with (
        patch.object(api.asyncio, "open_connection", connect),
        pytest.raises(OpusConnectionError),
    ):
        await async_read_certificate_fingerprint("192.168.1.5", 443)


async def test_a_gateway_without_a_certificate_is_a_response_error():
    writer = _Writer(None)

    async def connect(host, port, **kwargs):
        return None, writer

    with (
        patch.object(api.asyncio, "open_connection", connect),
        pytest.raises(OpusResponseError),
    ):
        await async_read_certificate_fingerprint("192.168.1.5", 443)

    assert writer.closed
