"""Button platform: reference runs of D2-05 blinds (diagnostics)."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import OpusGreenNetConfigEntry
from .const import (
    CONF_EAG_ID,
    DEFAULT_CHANNEL,
    REPOSITIONING_FIRST_DOWN,
    REPOSITIONING_FIRST_UP,
)
from .coordinator import SIGNAL_DEVICE_DISCOVERED, OpusGreenNetCoordinator
from .enocean_device import EnOceanDevice
from .entity import OpusGreenNetEntity

# Commands are serialized by the coordinator.
PARALLEL_UPDATES = 0

# Translation key, repositioning value and OPUS end position of each run.
# OPUS positions: 0 = up (open), 100 = down (closed).
REFERENCE_RUNS: tuple[tuple[str, str, int], ...] = (
    ("reference_run_up", REPOSITIONING_FIRST_UP, 0),
    ("reference_run_down", REPOSITIONING_FIRST_DOWN, 100),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpusGreenNetConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up reference run buttons for blinds that support repositioning."""
    coordinator = entry.runtime_data.coordinator
    gateway_device_id = entry.runtime_data.gateway_device_id
    eag_id = entry.data[CONF_EAG_ID]

    @callback
    def async_add_buttons(device: EnOceanDevice) -> None:
        if not device.supports_repositioning:
            return
        async_add_entities(
            [
                OpusGreenNetReferenceRunButton(
                    coordinator,
                    eag_id,
                    gateway_device_id,
                    device,
                    run,
                    channel_id,
                )
                for run in REFERENCE_RUNS
                for channel_id in range(device.channel_count)
            ]
        )

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, f"{SIGNAL_DEVICE_DISCOVERED}_{eag_id}", async_add_buttons
        )
    )
    for device in coordinator.devices.values():
        async_add_buttons(device)


class OpusGreenNetReferenceRunButton(OpusGreenNetEntity, ButtonEntity):
    """Drive a blind to an end position, then back to its current position.

    The blind recalibrates its position tracking on the way (``repositioning``
    ``firstUp``/``firstDown`` of the "Go to Position and Angle" command). An
    unknown position returns to the end position itself.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
        run: tuple[str, str, int],
        channel_id: int = DEFAULT_CHANNEL,
    ) -> None:
        """Initialize the reference run button."""
        super().__init__(coordinator, eag_id, gateway_device_id, device, channel_id)
        key, self._repositioning, self._end_position = run
        channel_suffix = f"_ch{channel_id}" if device.channel_count > 1 else ""
        self._attr_translation_key = key
        self._attr_unique_id = f"{eag_id}_{device.device_id}_{key}{channel_suffix}"

    async def async_press(self) -> None:
        """Start the reference run and return to the last known position."""
        channel = self._device.channels.get(self._channel_id)
        position = channel.position if channel is not None else None
        await self._coordinator.async_set_cover_position(
            self._device.device_id,
            self._end_position if position is None else position,
            self._channel_id,
            repositioning=self._repositioning,
        )
