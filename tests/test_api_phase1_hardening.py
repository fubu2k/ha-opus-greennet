"""Phase 1 hardening: host and port validation of the gateway client."""

from __future__ import annotations

import pytest

from custom_components.opus_greennet.api import (
    OpusInvalidHostError,
    validate_host,
    validate_port,
)


@pytest.mark.parametrize(
    "host",
    ["192.168.1.10", "10.0.0.1", "::1", "[::1]", "opus-gateway", "gw.example.org"],
)
def test_validate_host_accepts_bare_hosts(host: str) -> None:
    assert validate_host(host) == host


@pytest.mark.parametrize(
    "host",
    [
        "",
        " 192.168.1.10",
        "http://192.168.1.10",
        "192.168.1.10/path",
        "user:pw@192.168.1.10",
        "192.168.1.10:8080",
        "host?query",
        "host#frag",
        "bad host",
        "-leading.example",
        "a" * 64 + ".example",
        "[not-an-ip]",
    ],
)
def test_validate_host_rejects_unsafe_values(host: str) -> None:
    with pytest.raises(OpusInvalidHostError):
        validate_host(host)


@pytest.mark.parametrize("port", [1, 80, 8080, 65535])
def test_validate_port_accepts_range(port: int) -> None:
    assert validate_port(port) == port


@pytest.mark.parametrize("port", [0, -1, 65536, True])
def test_validate_port_rejects_invalid(port: int) -> None:
    with pytest.raises(OpusInvalidHostError):
        validate_port(port)
