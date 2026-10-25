"""Full-text leak check of a realistic diagnostics download.

``_redact_diagnostics`` is unit-tested with small structures. This test runs the
whole download for a gateway with several device types, a rejected command, a
gateway reply that carries setup secrets and composed identifiers, and searches
the serialised text for every value that must not appear. Hits would show a path
that the key-based redaction and the identifier collection both miss.
"""

from __future__ import annotations

import json

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.redact import REDACTED

from custom_components.opus_greennet.diagnostics import (
    async_get_config_entry_diagnostics,
)
from tests.fake_gateway import FakeGateway, device_object
from tests.ha_helpers import setup_bridge

SWITCH = "0A1B2C3D"
BLIND = "0A1B2C3E"
ROCKER = "0A1B2C3F"
NAMES = ("Kitchen Ceiling", "Bedroom Blind", "Hall Rocker")
SERIAL = "SN-9876-LEAK"
ADDRESS = "192.168.2.77"
MAC = "AA:BB:CC:00:11:22"
PIN = "111-22-333"
QR_PAYLOAD = "0TESTSETUPPAYLOAD"


@pytest.fixture
def leaking_gateway(gateway: FakeGateway) -> FakeGateway:
    gateway.system_info = {
        "serialNumber": SERIAL,
        "network": {"ipAddress": ADDRESS, "macAddress": MAC},
        # Free text that repeats a secret under a key nobody listed.
        "statusText": f"paired by {SERIAL} at {ADDRESS}",
    }
    gateway.devices = [
        device_object(
            SWITCH,
            "D2-01-01",
            friendly_id=NAMES[0],
            states=[{"key": "switch", "value": "off"}],
        ),
        device_object(BLIND, "D2-05-02", friendly_id=NAMES[1]),
        device_object(ROCKER, "F6-02-01", friendly_id=NAMES[2]),
    ]
    return gateway


async def test_download_contains_no_identifier_in_any_text(
    hass: HomeAssistant, leaking_gateway: FakeGateway
) -> None:
    entry = await setup_bridge(hass, leaking_gateway)
    coordinator = entry.runtime_data.coordinator

    # A rejected command leaves the gateway's wording in ``last_command_error``.
    leaking_gateway.responses[f"/devices/{SWITCH}/state"] = {
        "header": {"httpStatus": 500}
    }
    with pytest.raises(HomeAssistantError):
        await coordinator.async_turn_on(SWITCH)
    assert coordinator.devices[SWITCH].last_command_error

    # Identifiers composed the way unique IDs are, inside a free text.
    coordinator.devices[BLIND].last_command_error = (
        f"{leaking_gateway.eurid}_{BLIND}_last_seen rejected for {NAMES[1]} "
        f"({NAMES[1].upper()}) via {leaking_gateway.eurid.lower()}-{BLIND.lower()}"
    )

    result = await async_get_config_entry_diagnostics(hass, entry)
    text = json.dumps(result).casefold()

    forbidden = [
        leaking_gateway.eurid,
        leaking_gateway.password,
        leaking_gateway.host,
        f"{leaking_gateway.eurid}_{BLIND}",
        SWITCH,
        BLIND,
        ROCKER,
        *NAMES,
        SERIAL,
        ADDRESS,
        MAC,
        PIN,
        QR_PAYLOAD,
    ]
    leaks = [value for value in forbidden if value.casefold() in text]
    assert leaks == []

    # The download stays useful: structure and non-identifying facts remain.
    assert REDACTED.casefold() in text
    assert "d2-01-01" in text
    assert "rejected" in text
