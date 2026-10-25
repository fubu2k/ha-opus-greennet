"""Number platform: configured times and thresholds of D2-05 and D2-01 devices."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.const import LIGHT_LUX, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import OpusGreenNetConfigEntry
from .const import (
    CONF_EAG_ID,
    DEFAULT_CHANNEL,
    KEY_ILLUMINATION_THRESHOLD,
    KEY_LONG_PRESS,
    KEY_MAX_HOLDING_TIME,
    KEY_RAMPING_TIME,
    KEY_ROTATION_TIME,
    KEY_SHORT_PRESS,
    KEY_SWITCH_OFF_WARNING,
    KEY_SWITCH_OFF_WARNING_SIGNAL,
    KEY_TIMER,
    KEY_TIMER_LONG,
    KEY_VERTICAL_MOVEMENT_TIME,
)
from .coordinator import SIGNAL_DEVICE_DISCOVERED, OpusGreenNetCoordinator
from .enocean_device import EnOceanDevice
from .entity import OpusGreenNetEntity

# Writes are serialized by the coordinator's radio queue.
PARALLEL_UPDATES = 0

# Range and step the gateway reports for each parameter (GET .../parameters).
MAX_TRAVEL_SECONDS = 655.35
TRAVEL_STEP_SECONDS = 0.01
MAX_HOLDING_SECONDS = 65535
HOLDING_STEP_SECONDS = 1
MAX_RAMPING_SECONDS = 25.5
RAMPING_STEP_SECONDS = 0.1
MAX_PRESS_SECONDS = 2.55
PRESS_STEP_SECONDS = 0.01
MIN_TIMER_SECONDS = 60
MAX_TIMER_SECONDS = 7200
MAX_TIMER_LONG_SECONDS = 18000
MAX_WARNING_SECONDS = 45
MIN_WARNING_SIGNAL_SECONDS = 0.2
MAX_WARNING_SIGNAL_SECONDS = 2
WARNING_SIGNAL_STEP_SECONDS = 0.001
MAX_ILLUMINATION_LUX = 1500

# The parameter catalogs were read from a D2-05-02 blind, a D2-01-03 dimmer and
# a D2-01-01 and a D2-01-11 switch. Other types get no settings until their
# catalog is known.
BLIND_TIME_EEPS = frozenset({"D2-05-02"})
DIMMER_TIME_EEPS = frozenset({"D2-01-03"})
SWITCH_SETTING_EEPS = frozenset({"D2-01-01", "D2-01-11"})


@dataclass(frozen=True, slots=True)
class DeviceTime:
    """One number parameter of a device with the range the gateway reports."""

    translation_key: str
    parameter: str
    attribute: str
    maximum: float = MAX_TRAVEL_SECONDS
    step: float = TRAVEL_STEP_SECONDS
    minimum: float = 0
    # Seconds unless the parameter is a threshold in lux.
    unit: str = UnitOfTime.SECONDS
    device_class: NumberDeviceClass = NumberDeviceClass.DURATION
    # Platform type of the devices that have the parameter.
    entity_type: str = "cover"
    # EEPs whose catalog lists the parameter.
    eeps: frozenset[str] = frozenset()

    def applies_to(self, device: EnOceanDevice) -> bool:
        """Return whether the device is known to have this parameter."""
        return device.entity_type == self.entity_type and not self.eeps.isdisjoint(
            device.eep_names
        )


BLIND_TIMES: tuple[DeviceTime, ...] = (
    DeviceTime(
        "vertical_movement_time",
        KEY_VERTICAL_MOVEMENT_TIME,
        "vertical_movement_time",
        eeps=BLIND_TIME_EEPS,
    ),
    DeviceTime(
        "rotation_time", KEY_ROTATION_TIME, "rotation_time", eeps=BLIND_TIME_EEPS
    ),
    DeviceTime(
        "max_holding_time",
        KEY_MAX_HOLDING_TIME,
        "max_holding_time",
        maximum=MAX_HOLDING_SECONDS,
        step=HOLDING_STEP_SECONDS,
        eeps=BLIND_TIME_EEPS,
    ),
)

DIMMER_TIMES: tuple[DeviceTime, ...] = (
    DeviceTime(
        "ramping_time",
        KEY_RAMPING_TIME,
        "ramping_time",
        maximum=MAX_RAMPING_SECONDS,
        step=RAMPING_STEP_SECONDS,
        entity_type="light",
        eeps=DIMMER_TIME_EEPS,
    ),
    DeviceTime(
        "short_press_time",
        KEY_SHORT_PRESS,
        "short_press_time",
        maximum=MAX_PRESS_SECONDS,
        step=PRESS_STEP_SECONDS,
        entity_type="light",
        eeps=DIMMER_TIME_EEPS,
    ),
    DeviceTime(
        "long_press_time",
        KEY_LONG_PRESS,
        "long_press_time",
        maximum=MAX_PRESS_SECONDS,
        step=PRESS_STEP_SECONDS,
        entity_type="light",
        eeps=DIMMER_TIME_EEPS,
    ),
)

# The settings of switches belong to the device, not to a channel: the
# gateway reports no channel for them, also not for a 2-channel switch.
SWITCH_SETTINGS: tuple[DeviceTime, ...] = (
    DeviceTime(
        "timer",
        KEY_TIMER,
        "timer",
        minimum=MIN_TIMER_SECONDS,
        maximum=MAX_TIMER_SECONDS,
        step=1,
        entity_type="switch",
        eeps=SWITCH_SETTING_EEPS,
    ),
    DeviceTime(
        "timer_long",
        KEY_TIMER_LONG,
        "timer_long",
        minimum=MIN_TIMER_SECONDS,
        maximum=MAX_TIMER_LONG_SECONDS,
        step=1,
        entity_type="switch",
        eeps=SWITCH_SETTING_EEPS,
    ),
    DeviceTime(
        "switch_off_warning",
        KEY_SWITCH_OFF_WARNING,
        "switch_off_warning",
        maximum=MAX_WARNING_SECONDS,
        step=1,
        entity_type="switch",
        eeps=SWITCH_SETTING_EEPS,
    ),
    DeviceTime(
        "switch_off_warning_signal",
        KEY_SWITCH_OFF_WARNING_SIGNAL,
        "switch_off_warning_signal",
        minimum=MIN_WARNING_SIGNAL_SECONDS,
        maximum=MAX_WARNING_SIGNAL_SECONDS,
        step=WARNING_SIGNAL_STEP_SECONDS,
        entity_type="switch",
        eeps=SWITCH_SETTING_EEPS,
    ),
    DeviceTime(
        "illumination_threshold",
        KEY_ILLUMINATION_THRESHOLD,
        "illumination_threshold",
        maximum=MAX_ILLUMINATION_LUX,
        step=1,
        unit=LIGHT_LUX,
        device_class=NumberDeviceClass.ILLUMINANCE,
        entity_type="switch",
        eeps=SWITCH_SETTING_EEPS,
    ),
)

DEVICE_TIMES = BLIND_TIMES + DIMMER_TIMES + SWITCH_SETTINGS


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpusGreenNetConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the time settings of blinds and dimmers."""
    coordinator = entry.runtime_data.coordinator
    gateway_device_id = entry.runtime_data.gateway_device_id
    eag_id = entry.data[CONF_EAG_ID]

    @callback
    def async_add_numbers(device: EnOceanDevice) -> None:
        async_add_entities(
            [
                OpusGreenNetTimeNumber(
                    coordinator, eag_id, gateway_device_id, device, device_time
                )
                for device_time in DEVICE_TIMES
                if device_time.applies_to(device)
            ]
        )

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, f"{SIGNAL_DEVICE_DISCOVERED}_{eag_id}", async_add_numbers
        )
    )
    for device in coordinator.devices.values():
        async_add_numbers(device)


class OpusGreenNetTimeNumber(OpusGreenNetEntity, NumberEntity):
    """A time or threshold stored on the blind, dimmer or switch.

    The value comes from the device configuration, which the integration reads
    at setup and with every device list refresh; a change made by another
    controller shows up with the next refresh. A new value is adopted only
    after the device confirmed it when it was read back.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    # Disabled for new registry entries only; existing entries keep their setting.
    _attr_entity_registry_enabled_default = False
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
        device_time: DeviceTime,
    ) -> None:
        """Initialize the number."""
        super().__init__(
            coordinator, eag_id, gateway_device_id, device, DEFAULT_CHANNEL
        )
        self._device_time = device_time
        self._attr_translation_key = device_time.translation_key
        self._attr_device_class = device_time.device_class
        self._attr_native_unit_of_measurement = device_time.unit
        self._attr_native_min_value = device_time.minimum
        self._attr_native_max_value = device_time.maximum
        self._attr_native_step = device_time.step
        # The translation key is part of the unique ID: never rename it.
        self._attr_unique_id = (
            f"{eag_id}_{device.device_id}_{device_time.translation_key}"
        )

    @property
    def available(self) -> bool:
        """Return whether the gateway is reachable and reported the setting.

        A setting the device never reported is never written.
        """
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> float | None:
        """Return the configured time, or None while it is unknown."""
        channel = self._device.channels.get(self._channel_id)
        if channel is None:
            return None
        value: float | None = getattr(channel, self._device_time.attribute)
        return value

    async def async_set_native_value(self, value: float) -> None:
        """Write the time to the device and read it back."""
        await self._coordinator.async_set_device_parameter(
            self._device.device_id,
            self._device_time.parameter,
            value,
            step=self._device_time.step,
            minimum=self._device_time.minimum,
        )
