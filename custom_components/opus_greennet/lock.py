"""Read-only HOPPE SecureConnect lock state."""

from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import OpusGreenNetConfigEntry
from .const import CONF_EAG_ID, DEFAULT_CHANNEL, DOMAIN
from .coordinator import SIGNAL_DEVICE_DISCOVERED, OpusGreenNetCoordinator
from .enocean_device import EnOceanDevice
from .entity import OpusGreenNetEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpusGreenNetConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add status entities for AutoLock handles only."""
    coordinator = entry.runtime_data.coordinator
    eag_id = entry.data[CONF_EAG_ID]

    @callback
    def async_add_lock(device: EnOceanDevice) -> None:
        if device.has_autolock:
            async_add_entities(
                [
                    OpusGreenNetAutoLock(
                        coordinator,
                        eag_id,
                        entry.runtime_data.gateway_device_id,
                        device,
                    )
                ]
            )

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, f"{SIGNAL_DEVICE_DISCOVERED}_{eag_id}", async_add_lock
        )
    )
    for device in coordinator.devices.values():
        async_add_lock(device)


class OpusGreenNetAutoLock(OpusGreenNetEntity, LockEntity):
    """Report lock feedback; never write the unresolved AutoLock protocol."""

    _attr_translation_key = "autolock"
    _attr_supported_features = 0

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
    ) -> None:
        super().__init__(coordinator, eag_id, gateway_device_id, device)
        self._attr_unique_id = f"{eag_id}_{device.device_id}_autolock"

    @property
    def is_locked(self) -> bool | None:
        """Return the lock state without guessing from the handle position."""
        channel = self._device.channels.get(DEFAULT_CHANNEL)
        return channel.is_locked if channel else None

    async def async_lock(self, **kwargs: Any) -> None:
        """Reject writes until a safe protocol is established."""
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="autolock_read_only"
        )

    async def async_unlock(self, **kwargs: Any) -> None:
        """Reject writes until a safe protocol is established."""
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="autolock_read_only"
        )
