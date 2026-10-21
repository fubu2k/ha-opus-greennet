"""Phase 1b: host address classes and credential validation of the client."""

from __future__ import annotations

import base64
from unittest.mock import MagicMock

import pytest

from custom_components.opus_greennet.api import (
    MAX_CREDENTIAL_LENGTH,
    OpusAuthError,
    OpusGatewayClient,
    OpusInvalidHostError,
    validate_host,
    validate_password,
    validate_username,
)


@pytest.mark.parametrize(
    "host",
    [
        "192.168.1.20",
        "10.0.0.5",
        "203.0.113.7",
        "8.8.8.8",
        "127.0.0.1",
        "fd00::1",
        "[fd00::1]",
        "gateway.example.org",
        "opus-gateway",
    ],
)
def test_validate_host_accepts_gateway_addresses(host: str) -> None:
    """Private, public and loopback addresses and host names stay allowed."""
    assert validate_host(host) == host


@pytest.mark.parametrize(
    "host",
    [
        "0.0.0.0",
        "::",
        "[::]",
        "224.0.0.1",
        "239.255.255.250",
        "ff02::1",
        "169.254.169.254",
        "169.254.10.10",
        "fe80::1",
        "[fe80::1]",
    ],
)
def test_validate_host_rejects_non_unicast_addresses(host: str) -> None:
    """Unspecified, multicast and link-local addresses cannot be a gateway."""
    with pytest.raises(OpusInvalidHostError):
        validate_host(host)


@pytest.mark.parametrize(
    "host",
    [
        "",
        " 192.168.1.20",
        "192.168.1.20 ",
        "http://192.168.1.20",
        "a/b",
        "a:80",
    ],
)
def test_validate_host_still_rejects_malformed_values(host: str) -> None:
    """Earlier host validation keeps working."""
    with pytest.raises(OpusInvalidHostError):
        validate_host(host)


@pytest.mark.parametrize(
    "username", ["admin", "user.name", "a" * MAX_CREDENTIAL_LENGTH]
)
def test_validate_username_accepts_plain_names(username: str) -> None:
    """Ordinary user names are returned unchanged."""
    assert validate_username(username) == username


@pytest.mark.parametrize(
    "username",
    ["", "ad:min", ":", "ad\nmin", "ad\x00min", "ad\x7fmin", "a" * 257],
)
def test_validate_username_rejects_unsafe_names(username: str) -> None:
    """A colon, control characters, emptiness and excess length are refused."""
    with pytest.raises(OpusAuthError):
        validate_username(username)


def test_validate_username_rejects_non_string() -> None:
    """Only strings can be user names."""
    with pytest.raises(OpusAuthError):
        validate_username(None)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "password", ["A1B2C3D4", "12345678", "p:ss word", "pässwörd", ""]
)
def test_validate_password_accepts_ordinary_values(password: str) -> None:
    """Colons, spaces and non-ASCII letters are valid in a password."""
    assert validate_password(password) == password


@pytest.mark.parametrize("password", ["pa\nss", "pa\x00ss", "pa\x7fss", "p" * 257])
def test_validate_password_rejects_unsafe_values(password: str) -> None:
    """Control characters and excess length are refused."""
    with pytest.raises(OpusAuthError):
        validate_password(password)


def test_validate_password_rejects_non_string() -> None:
    """Only strings can be passwords."""
    with pytest.raises(OpusAuthError):
        validate_password(None)  # type: ignore[arg-type]


def test_client_builds_basic_authentication_header() -> None:
    """A valid user name and password produce the expected header."""
    client = OpusGatewayClient(
        MagicMock(), "192.168.1.20", 8080, "admin", "A1B2C3D4"
    )
    expected = base64.b64encode(b"admin:A1B2C3D4").decode()
    assert client._headers == {"Authorization": f"Basic {expected}"}


def test_client_rejects_non_unicast_host() -> None:
    """The client refuses to be built for the cloud metadata address."""
    with pytest.raises(OpusInvalidHostError):
        OpusGatewayClient(
            MagicMock(), "169.254.169.254", 8080, "admin", "A1B2C3D4"
        )


def test_client_rejects_username_with_colon() -> None:
    """A colon in the user name cannot reach the authorization header."""
    with pytest.raises(OpusAuthError):
        OpusGatewayClient(MagicMock(), "192.168.1.20", 8080, "ad:min", "A1B2C3D4")


def test_client_rejects_password_with_control_character() -> None:
    """A control character in the password cannot reach the header."""
    with pytest.raises(OpusAuthError):
        OpusGatewayClient(MagicMock(), "192.168.1.20", 8080, "admin", "A1B2\nC3D4")
