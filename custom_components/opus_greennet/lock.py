"""HOPPE SecureConnect AutoLock (D2-06-40) as a lock entity.

The handle reports its lock-bolt (``lock``) and asks for permission to unlock
when its button is pressed (``unlock = requested``). Locking and unlocking
answer that protocol: ``unlock = allowed`` unlocks, ``notAllowed`` locks.
The handle sleeps between uses, so the gateway may queue the answer (201)
until the handle wakes up, e.g. at the next button press.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.components.lock.const import LockEntityFeature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from . import OpusGreenNetConfigEntry
from .const import CONF_EAG_ID, DEFAULT_CHANNEL
from .coordinator import (
    SIGNAL_DEVICE_DISCOVERED,
    SIGNAL_DEVICE_REMOVED,
    CommandDelivery,
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


# How long the entity shows "locking"/"unlocking" without a lock report: an
# answer the gateway sent at once, or one it queued for the sleeping handle.
LOCK_PENDING_SECONDS = 60.0
LOCK_DEFERRED_PENDING_SECONDS = 600.0


class OpusGreenNetAutoLock(OpusGreenNetEntity, LockEntity):
    """Lock and unlock the handle; the state follows the reported lock-bolt."""

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
        # Requested lock state (True = locked) until the handle reports it.
        self._target: bool | None = None
        self._cancel_target: Callable[[], None] | None = None

    def _channel_locked(self) -> bool | None:
        channel = self._device.channels.get(DEFAULT_CHANNEL)
        return channel.is_locked if channel else None

    def _unlock_requested(self) -> bool | None:
        channel = self._device.channels.get(DEFAULT_CHANNEL)
        return channel.unlock_request if channel else None

    @property
    def is_locked(self) -> bool | None:
        """Return the reported lock-bolt, never a guess from the handle."""
        return self._channel_locked()

    @property
    def is_locking(self) -> bool:
        """Return whether a lock command waits for the handle's report."""
        return self._target is True

    @property
    def is_unlocking(self) -> bool:
        """Return whether an unlock command waits for the handle's report."""
        return self._target is False

    @property
    def extra_state_attributes(self) -> dict[str, bool] | None:
        """Show whether the handle currently asks to be unlocked."""
        requested = self._unlock_requested()
        return None if requested is None else {"unlock_requested": requested}

    def _clear_target(self) -> None:
        self._target = None
        if self._cancel_target is not None:
            self._cancel_target()
            self._cancel_target = None

    @callback
    def _target_expired(self, _now: datetime) -> None:
        self._cancel_target = None
        self._target = None
        self.async_write_ha_state()

    async def _async_answer(self, locked: bool) -> None:
        delivery = await self._coordinator.async_set_handle_unlock(
            self._device.device_id, allowed=not locked
        )
        self._clear_target()
        if self._channel_locked() is not locked:
            self._target = locked
            self._cancel_target = async_call_later(
                self.hass,
                LOCK_DEFERRED_PENDING_SECONDS
                if delivery is CommandDelivery.DEFERRED
                else LOCK_PENDING_SECONDS,
                self._target_expired,
            )
        self.async_write_ha_state()

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock the handle (answer ``notAllowed``)."""
        await self._async_answer(True)

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the handle (answer ``allowed``)."""
        await self._async_answer(False)

    @callback
    def _handle_state_update(self, device: EnOceanDevice) -> None:
        """End "locking"/"unlocking" once the handle reports the target."""
        self._device = device
        if self._target is not None and self._channel_locked() is self._target:
            self._clear_target()
        super()._handle_state_update(device)

    async def async_will_remove_from_hass(self) -> None:
        """Stop the pending timer."""
        self._clear_target()
        await super().async_will_remove_from_hass()
