"""HOPPE SecureConnect AutoLock (D2-06-40) as a lock entity.

The handle sleeps between uses. When its button is pressed it asks the
gateway whether it may be unlocked (``unlock = requested``). The gateway
answers with the target state stored for the handle: ``allowed`` (the button
lights green and the handle can be turned) or ``notAllowed`` (red, the handle
stays locked). Locking and unlocking in Home Assistant set that stored answer
(the gateway acknowledges with status 202). The lock-bolt the handle reports
is a separate binary sensor.
"""

from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.components.lock.const import LockEntityFeature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import OpusGreenNetConfigEntry
from .const import CONF_EAG_ID, DEFAULT_CHANNEL
from .coordinator import (
    SIGNAL_DEVICE_DISCOVERED,
    SIGNAL_DEVICE_REMOVED,
    OpusGreenNetCoordinator,
)
from .enocean_device import EnOceanDevice
from .entity import OpusGreenNetEntity, migrate_legacy_entity_suffix

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpusGreenNetConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add lock entities for AutoLock handles only."""
    coordinator = entry.runtime_data.coordinator
    eag_id = entry.data[CONF_EAG_ID]
    added_devices: set[str] = set()

    @callback
    def async_add_lock(device: EnOceanDevice) -> None:
        if device.has_autolock and device.device_id not in added_devices:
            migrate_legacy_entity_suffix(
                hass,
                entry.entry_id,
                "lock",
                f"{eag_id}_{device.device_id}",
                "window_handle_lock",
                "autolock",
            )
            added_devices.add(device.device_id)
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
            hass, f"{SIGNAL_DEVICE_REMOVED}_{eag_id}", added_devices.discard
        )
    )
    entry.async_on_unload(
        async_dispatcher_connect(
            hass, f"{SIGNAL_DEVICE_DISCOVERED}_{eag_id}", async_add_lock
        )
    )
    for device in coordinator.devices.values():
        async_add_lock(device)


class OpusGreenNetAutoLock(OpusGreenNetEntity, LockEntity):
    """Locked = the gateway denies the handle's next unlock request."""

    _attr_translation_key = "autolock"
    _attr_supported_features = LockEntityFeature(0)

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
        """Return whether the stored answer denies unlocking."""
        channel = self._device.channels.get(DEFAULT_CHANNEL)
        if channel is None or channel.unlock_allowed is None:
            return None
        return not channel.unlock_allowed

    @property
    def extra_state_attributes(self) -> dict[str, bool] | None:
        """Show the reported lock-bolt and a pending unlock request."""
        channel = self._device.channels.get(DEFAULT_CHANNEL)
        if channel is None:
            return None
        attributes = {
            name: value
            for name, value in (
                ("bolt_locked", channel.is_locked),
                ("unlock_requested", channel.unlock_request),
            )
            if value is not None
        }
        return attributes or None

    async def _async_answer(self, allowed: bool) -> None:
        snapshot = self._device.snapshot_channel_state(DEFAULT_CHANNEL)
        await self._coordinator.async_set_handle_unlock(
            self._device.device_id, allowed=allowed
        )
        # The gateway stores the answer at once (200/201/202), even while the
        # handle sleeps; it takes effect at the handle's next request.
        if self._device.apply_unlock_permission_estimate(snapshot, allowed):
            self.async_write_ha_state()

    async def async_lock(self, **kwargs: Any) -> None:
        """Deny unlocking (answer ``notAllowed``)."""
        await self._async_answer(False)

    async def async_unlock(self, **kwargs: Any) -> None:
        """Allow unlocking (answer ``allowed``)."""
        await self._async_answer(True)
