"""Identity, versions and address of the gateway as diagnostic sensors.

The gateway's own device shows its serial number in the device information
and, as diagnostic sensors that are enabled by default, EURID, firmware, app
and API version, host name, IPv4 and IPv6 address.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from custom_components.opus_greennet.const import DOMAIN
from custom_components.opus_greennet.coordinator import (
    SIGNAL_GATEWAY_INFO_UPDATE,
    OpusGreenNetCoordinator,
)
from custom_components.opus_greennet.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.opus_greennet.sensor import (
    GATEWAY_INFO_READINGS,
    OpusGreenNetGatewayInfoSensor,
)
from tests.fake_gateway import FakeGateway, device_object
from tests.ha_helpers import setup_bridge, wait_for_entity, wait_until

EAG_ID = "AABB0011"
COORDINATOR = "custom_components.opus_greennet.coordinator"

EXPECTED = {
    "gateway_eurid": "AABB0011",
    "gateway_firmware_version": "OPUS-GW 2.4.1",
    "gateway_app_version": "1.21.30",
    "gateway_api_version": "1.2",
    "gateway_hostname": "gw.home",
    "gateway_ipv4_address": "192.0.2.10",
    "gateway_ipv6_address": "fe80::1",
}


def _coordinator(info: dict[str, object] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        eag_id=EAG_ID,
        available=True,
        gateway_info=info
        if info is not None
        else {
            "version": "OPUS-GW 2.4.1",
            "appVersion": "1.21.30",
            "apiVersion": "1.2",
        },
        gateway_address=("gw.home", "192.0.2.10", "fe80::1"),
    )


def _sensor(coordinator: SimpleNamespace, key: str) -> OpusGreenNetGatewayInfoSensor:
    return OpusGreenNetGatewayInfoSensor(coordinator, EAG_ID, key)  # type: ignore[arg-type]


@pytest.mark.parametrize(("key", "value"), EXPECTED.items())
def test_each_sensor_shows_its_reading(key: str, value: str) -> None:
    sensor = _sensor(_coordinator(), key)
    assert sensor.native_value == value
    assert sensor.unique_id == f"{EAG_ID}_{key}"
    assert sensor.translation_key == key
    assert sensor.device_info == {"identifiers": {(DOMAIN, EAG_ID)}}
    assert sensor.entity_category is EntityCategory.DIAGNOSTIC
    assert sensor.entity_registry_enabled_default is True
    assert sensor.should_poll is False


def test_all_readings_have_a_sensor() -> None:
    assert set(EXPECTED) == set(GATEWAY_INFO_READINGS)


@pytest.mark.parametrize(
    "info",
    [{}, {"version": None, "appVersion": "", "apiVersion": "  "}, {"version": True}],
)
def test_versions_the_gateway_did_not_report_are_unknown(info: dict) -> None:
    coordinator = _coordinator(info)
    for key in (
        "gateway_firmware_version",
        "gateway_app_version",
        "gateway_api_version",
    ):
        assert _sensor(coordinator, key).native_value is None


def test_a_numeric_version_is_shown_as_text() -> None:
    coordinator = _coordinator({"apiVersion": 2, "appVersion": 1.5})
    assert _sensor(coordinator, "gateway_api_version").native_value == "2"
    assert _sensor(coordinator, "gateway_app_version").native_value == "1.5"


def test_an_unresolved_address_is_unknown() -> None:
    coordinator = _coordinator()
    coordinator.gateway_address = (None, None, None)
    for key in ("gateway_hostname", "gateway_ipv4_address", "gateway_ipv6_address"):
        assert _sensor(coordinator, key).native_value is None


@pytest.mark.parametrize("available", [True, False])
def test_the_sensors_follow_the_gateway_availability(available: bool) -> None:
    coordinator = _coordinator()
    coordinator.available = available
    assert _sensor(coordinator, "gateway_eurid").available is available


async def test_the_address_is_resolved_and_changes_are_signalled() -> None:
    coordinator = OpusGreenNetCoordinator(MagicMock(), MagicMock(), EAG_ID)
    coordinator.client.host = "gw.home"
    lookup = AsyncMock(return_value=("gw.home", "192.0.2.10", None))
    with (
        patch(f"{COORDINATOR}.async_gateway_address", lookup),
        patch(f"{COORDINATOR}.async_dispatcher_send") as send,
    ):
        await coordinator._async_refresh_gateway_address()  # noqa: SLF001
        assert coordinator.gateway_address == ("gw.home", "192.0.2.10", None)
        send.assert_called_once_with(
            coordinator.hass, f"{SIGNAL_GATEWAY_INFO_UPDATE}_{EAG_ID}"
        )
        # An unchanged result tells nobody.
        await coordinator._async_refresh_gateway_address()  # noqa: SLF001
        send.assert_called_once()
    lookup.assert_awaited_with("gw.home")


async def test_a_lookup_that_ends_after_unload_changes_nothing() -> None:
    coordinator = OpusGreenNetCoordinator(MagicMock(), MagicMock(), EAG_ID)
    coordinator.client.host = "gw.home"
    coordinator._unloaded = True  # noqa: SLF001
    with (
        patch(
            f"{COORDINATOR}.async_gateway_address",
            AsyncMock(return_value=("gw.home", None, None)),
        ),
        patch(f"{COORDINATOR}.async_dispatcher_send") as send,
    ):
        await coordinator._async_refresh_gateway_address()  # noqa: SLF001
    assert coordinator.gateway_address == (None, None, None)
    send.assert_not_called()


async def test_a_refreshed_gateway_information_is_signalled() -> None:
    coordinator = OpusGreenNetCoordinator(MagicMock(), MagicMock(), EAG_ID)
    coordinator._gateway_available = True  # noqa: SLF001
    coordinator.client.async_get = AsyncMock(
        return_value={"systemInfo": {"eurid": EAG_ID, "version": "FW 3"}}
    )
    with patch(f"{COORDINATOR}.async_dispatcher_send") as send:
        await coordinator._async_refresh_system_info()  # noqa: SLF001
    assert coordinator.gateway_info["version"] == "FW 3"
    send.assert_called_once_with(
        coordinator.hass, f"{SIGNAL_GATEWAY_INFO_UPDATE}_{EAG_ID}"
    )


async def test_the_periodic_refresh_reads_information_address_and_devices() -> None:
    coordinator = OpusGreenNetCoordinator(MagicMock(), MagicMock(), EAG_ID)
    calls: list[str] = []

    def step(name: str) -> AsyncMock:
        async def run() -> None:
            calls.append(name)

        return AsyncMock(side_effect=run)

    with (
        patch.object(coordinator, "_async_refresh_system_info", step("info")),
        patch.object(coordinator, "_async_refresh_gateway_address", step("address")),
        patch.object(coordinator, "_async_refresh_device_list", step("devices")),
    ):
        await coordinator._async_periodic_refresh()  # noqa: SLF001
    assert calls == ["info", "address", "devices"]


async def test_the_gateway_device_shows_the_sensors_with_their_values(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.system_info = {
        "version": "OPUS-GW 2.4.1",
        "appVersion": "1.21.30",
        "apiVersion": "1.2",
        "serialNumber": "02022774",
    }
    with patch(
        f"{COORDINATOR}.async_gateway_address",
        AsyncMock(return_value=("gw.home", "192.0.2.10", "fe80::1")),
    ):
        entry = await setup_bridge(hass, gateway)
        registry = er.async_get(hass)
        for key, value in {**EXPECTED, "gateway_eurid": gateway.eurid}.items():
            entity_id = await wait_for_entity(hass, "sensor", f"{gateway.eurid}_{key}")
            registered = registry.async_get(entity_id)
            assert registered is not None
            assert registered.device_id == entry.runtime_data.gateway_device_id
            assert registered.entity_category is EntityCategory.DIAGNOSTIC
            assert registered.disabled_by is None
            # The address is resolved in the background after setup.
            await wait_until(
                lambda entity_id=entity_id, value=value: (
                    (state := hass.states.get(entity_id)) is not None
                    and state.state == value
                )
            )

    # The HomeKit setup code is never offered as a state.
    texts = " ".join(
        str(state.state) + str(state.attributes) for state in hass.states.async_all()
    )
    assert "111-22-333" not in texts
    assert "X-HM://" not in texts


async def test_a_restart_looks_up_host_name_and_addresses_at_once(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    lookup = AsyncMock(return_value=("old.home", "192.0.2.10", None))
    with patch(f"{COORDINATOR}.async_gateway_address", lookup):
        entry = await setup_bridge(hass, gateway)
        keys = ("gateway_hostname", "gateway_ipv4_address", "gateway_ipv6_address")
        ids = {
            key: await wait_for_entity(hass, "sensor", f"{gateway.eurid}_{key}")
            for key in keys
        }
        await wait_until(lambda: hass.states.get(ids[keys[0]]).state == "old.home")
        assert lookup.await_count == 1

        # The address changed while Home Assistant was stopped or reloading:
        # the new setup does not wait for the 30 minute refresh.
        lookup.return_value = ("new.home", "192.0.2.20", "fe80::2")
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        expected = ("new.home", "192.0.2.20", "fe80::2")
        for key, value in zip(keys, expected, strict=True):
            await wait_until(
                lambda key=key, value=value: hass.states.get(ids[key]).state == value
            )
        assert lookup.await_count == 2


async def test_identifiers_in_gateway_texts_reach_neither_export_nor_debug_log(
    hass: HomeAssistant, gateway: FakeGateway, caplog: pytest.LogCaptureFixture
) -> None:
    serial = "02022774"
    switch = "0A1B2C3D"
    gateway.system_info = {"serialNumber": serial}
    gateway.devices = [
        device_object(switch, "D2-01-01", states=[{"key": "switch", "value": "off"}])
    ]
    caplog.set_level(logging.DEBUG, logger="custom_components.opus_greennet")
    entry = await setup_bridge(hass, gateway)
    coordinator = entry.runtime_data.coordinator
    gateway.responses[f"/devices/{switch}/state"] = {
        "header": {
            "httpStatus": 500,
            "message": f"unit {gateway.eurid.lower()} serial {serial} failed",
        }
    }
    with pytest.raises(HomeAssistantError):
        await coordinator.async_turn_on(switch)

    # The wording is kept, the two identifiers are not.
    messages = [str(entry.get("message")) for entry in coordinator.radio_journal()]
    assert "unit <gateway-id> serial <gateway-id> failed" in " ".join(messages)
    radio_lines = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("OPUS radio request")
    ]
    assert radio_lines
    for line in radio_lines:
        assert serial not in line
        assert gateway.eurid.casefold() not in line.casefold()

    export = json.dumps(await async_get_config_entry_diagnostics(hass, entry))
    assert serial not in export
    assert gateway.eurid.casefold() not in export.casefold()
