"""Number platform: configured travel times of D2-05 blinds."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import OpusGreenNetConfigEntry
from .const import (
    CONF_EAG_ID,
    DEFAULT_CHANNEL,
    KEY_ROTATION_TIME,
    KEY_VERTICAL_MOVEMENT_TIME,
)
from .coordinator import SIGNAL_DEVICE_DISCOVERED, OpusGreenNetCoordinator
from .enocean_device import EnOceanDevice
from .entity import OpusGreenNetEntity

# Writes are serialized by the coordinator's radio queue.
PARALLEL_UPDATES = 0

# Range and step the gateway reports for both parameters (GET .../parameters).
MAX_SECONDS = 655.35
STEP_SECONDS = 0.01


@dataclass(frozen=True, slots=True)
class BlindTime:
    """One travel time parameter of a blind."""

    translation_key: str
    parameter: str
    attribute: str


BLIND_TIMES: tuple[BlindTime, ...] = (
    BlindTime(
        "vertical_movement_time", KEY_VERTICAL_MOVEMENT_TIME, "vertical_movement_time"
    ),
    BlindTime("rotation_time", KEY_ROTATION_TIME, "rotation_time"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpusGreenNetConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the travel time settings of blinds."""
    coordinator = entry.runtime_data.coordinator
    gateway_device_id = entry.runtime_data.gateway_device_id
    eag_id = entry.data[CONF_EAG_ID]

    @callback
    def async_add_numbers(device: EnOceanDevice) -> None:
        if device.entity_type != "cover":
            return
        async_add_entities(
            [
                OpusGreenNetBlindTimeNumber(
                    coordinator, eag_id, gateway_device_id, device, blind_time
                )
                for blind_time in BLIND_TIMES
            ]
        )

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, f"{SIGNAL_DEVICE_DISCOVERED}_{eag_id}", async_add_numbers
        )
    )
    for device in coordinator.devices.values():
        async_add_numbers(device)


class OpusGreenNetBlindTimeNumber(OpusGreenNetEntity, NumberEntity):
    """A travel time stored on the blind (seconds).

    The value comes from the device configuration, which the integration reads
    at setup and with every device list refresh; a change made by another
    controller shows up with the next refresh. A new value is adopted only
    after the blind confirmed it when it was read back.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    # Disabled for new registry entries only; existing entries keep their setting.
    _attr_entity_registry_enabled_default = False
    _attr_device_class = NumberDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_native_min_value = 0
    _attr_native_max_value = MAX_SECONDS
    _attr_native_step = STEP_SECONDS
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
        blind_time: BlindTime,
    ) -> None:
        """Initialize the number."""
        super().__init__(
            coordinator, eag_id, gateway_device_id, device, DEFAULT_CHANNEL
        )
        self._blind_time = blind_time
        self._attr_translation_key = blind_time.translation_key
        self._attr_unique_id = (
            f"{eag_id}_{device.device_id}_{blind_time.translation_key}"
        )

    @property
    def native_value(self) -> float | None:
        """Return the configured time, or None while it is unknown."""
        channel = self._device.channels.get(self._channel_id)
        if channel is None:
            return None
        value: float | None = getattr(channel, self._blind_time.attribute)
        return value

    async def async_set_native_value(self, value: float) -> None:
        """Write the time to the blind and read it back."""
        await self._coordinator.async_set_device_parameter(
            self._device.device_id, self._blind_time.parameter, value
        )
