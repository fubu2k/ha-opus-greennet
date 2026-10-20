"""Transport-neutral command, signal and timing definitions (phase R2).

Both coordinators, the platforms and the entity base share these names. They
were defined in ``coordinator.py`` (MQTT) and ``mqtt_transport.py`` before;
this module holds them without any MQTT or HTTP dependency, so the REST
transport and the platforms do not import the MQTT transport (plan
``Implementations-Plan-Rest-API.md``, decision E2, couplings K2 and K3).

``coordinator.py`` and ``mqtt_transport.py`` re-export every name unchanged.
The values are identical to the former definitions; moving them changes no
behaviour, timing or traffic.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from homeassistant.exceptions import HomeAssistantError

from .const import COVER_MOVEMENT_GRACE_SECONDS, DOMAIN

# Dispatcher signals. Each is suffixed with the gateway EAG identifier (and,
# for state updates, the device identifier) when sent.
SIGNAL_DEVICE_REMOVED = f"{DOMAIN}_device_removed"
SIGNAL_DEVICE_DISCOVERED = f"{DOMAIN}_device_discovered"
SIGNAL_DEVICE_STATE_UPDATE = f"{DOMAIN}_device_state_update"
SIGNAL_AVAILABILITY_UPDATE = f"{DOMAIN}_availability_update"

# Delayed status checks after a command whose result is not yet reported
# (seconds after the acknowledgement). At most these two per channel.
OUTBOUND_STATE_RECONCILIATION_DELAYS = (5, 20)
# Gateway acknowledgement "SoftSmartAck" (vendor reference 3.3): the gateway
# accepted the telegram for deferred delivery and transmits it later. It is
# neither a radio transmission nor device feedback.
ACK_STATUS_DEFERRED = 201
# Internal reason for a background status check dropped before it is sent.
SUPERSEDED = "superseded"
# Interval of the gateway health probe (uptime). Gateway health never confirms
# device state.
GATEWAY_HEALTH_INTERVAL = timedelta(seconds=60)


class CommandDelivery(StrEnum):
    """What a positive gateway acknowledgement says about one command.

    Neither value confirms physical execution: only a device report (a radio
    telegram or a device-model update) for the commanded field does that.
    """

    # httpStatus 200: the gateway transmitted the telegram.
    SENT = "sent"
    # httpStatus 201 (SoftSmartAck): queued by the gateway for later delivery.
    DEFERRED = "deferred"


@dataclass
class CoverMovement:
    """One channel's replaceable travel estimate and cancellation handle."""

    start_position: float | None
    target: int
    started_at: float
    travel: float
    cancel: Callable[[], None] | None = None
    accepted: bool = True
    elapsed: bool = False

    @property
    def ends_at(self) -> float:
        return self.started_at + self.travel + COVER_MOVEMENT_GRACE_SECONDS


def request_error(key: str, device_id: str, reason: str = "") -> HomeAssistantError:
    """Create a translated transport error without logging sensitive payloads."""
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders={"device_id": device_id, "reason": reason},
    )
