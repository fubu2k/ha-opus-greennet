"""Transport selection and the common coordinator interface.

An entry runs exactly one coordinator. Both coordinators expose the same
public interface to platforms, diagnostics and services: device model,
dispatcher signals, availability, commands returning ``CommandDelivery`` and
the explicit ReCom service requests.

* MQTT (``coordinator.OpusGreenNetCoordinator``): every entry without
  ``transport`` in its data, i.e. all entries created before phase 14.
  Unchanged.
* REST (``rest_coordinator.OpusGreenNetRestCoordinator``): entries whose data
  contains ``transport: rest``, created by the REST config flow step or
  switched explicitly by the user in the reconfigure flow.

``OpusCoordinator`` is a structural protocol (phase R2, plan E3). Platforms
and services depend on this interface only, not on a coordinator class, so
this module imports neither transport and removing one transport later does
not change any platform signature. Strict mypy checks that both coordinators
satisfy the protocol where ``__init__`` assigns them to an ``OpusCoordinator``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from .const import CONF_TRANSPORT, TRANSPORT_MQTT, TRANSPORT_REST
from .domain import CommandDelivery
from .enocean_device import EnOceanDevice
from .wire import JsonObject, WireMapping


class OpusCoordinator(Protocol):
    """What platforms, diagnostics and services use of a coordinator."""

    eag_id: str
    devices: dict[str, EnOceanDevice]
    gateway_info: JsonObject
    gateway_uptime: str | None

    @property
    def available(self) -> bool:
        """Return whether the gateway can currently be reached."""
        ...

    def get_device(self, device_id: str) -> EnOceanDevice | None:
        """Return a known device of this gateway."""
        ...

    def late_answer_diagnostics(self, owner: str) -> dict[str, int]:
        """Return the late-answer counters of a device or the gateway."""
        ...

    def async_forget_device(self, device_id: str) -> None:
        """Forget a child device locally (registry removal)."""
        ...

    async def async_unload(self) -> None:
        """Stop all work of the coordinator."""
        ...

    async def async_turn_on(
        self,
        device_id: str,
        channel: int = 0,
        brightness: int | None = None,
        is_dimmable: bool = False,
    ) -> CommandDelivery:
        """Switch a channel on, optionally to a brightness."""
        ...

    async def async_turn_off(
        self,
        device_id: str,
        channel: int = 0,
        is_dimmable: bool = False,
    ) -> CommandDelivery:
        """Switch a channel off."""
        ...

    async def async_set_cover_position(
        self,
        device_id: str,
        position: int,
        channel: int = 0,
    ) -> CommandDelivery:
        """Move a cover to a Home Assistant position."""
        ...

    async def async_set_cover_tilt(
        self,
        device_id: str,
        tilt: int,
        channel: int = 0,
    ) -> CommandDelivery:
        """Set the slat angle of a cover."""
        ...

    async def async_stop_cover(
        self, device_id: str, channel: int = 0
    ) -> CommandDelivery:
        """Stop a moving cover."""
        ...

    async def async_set_climate_setpoint(
        self,
        device_id: str,
        temperature: float,
    ) -> CommandDelivery:
        """Set the temperature setpoint of a HeatArea."""
        ...

    async def async_set_climate_mode(
        self,
        device_id: str,
        mode: str,
    ) -> CommandDelivery:
        """Set the heater mode of a HeatArea."""
        ...

    async def async_get_device_configuration(self, device_id: str) -> JsonObject:
        """Read the configuration of a device (explicit user action)."""
        ...

    async def async_set_device_configuration(
        self, device_id: str, config: WireMapping
    ) -> None:
        """Write the configuration of a device (explicit user action)."""
        ...

    async def async_get_device_parameters(self, device_id: str) -> JsonObject:
        """Read the parameters of a device (explicit user action)."""
        ...


def entry_transport(data: Mapping[str, object]) -> str:
    """Return the transport of config entry data; MQTT unless REST is chosen."""
    if data.get(CONF_TRANSPORT) == TRANSPORT_REST:
        return TRANSPORT_REST
    return TRANSPORT_MQTT
