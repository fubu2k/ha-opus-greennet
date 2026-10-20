"""Transport selection for a config entry (phase 14).

An entry runs exactly one coordinator. Both coordinators expose the same
public interface to platforms, diagnostics and services: device model,
dispatcher signals, availability, commands returning ``CommandDelivery`` and
the explicit ReCom service requests.

* MQTT (``OpusGreenNetCoordinator``): every entry without ``transport`` in
  its data, i.e. all entries created before phase 14. Unchanged.
* REST (``OpusGreenNetRestCoordinator``): entries whose data contains
  ``transport: rest``, created by the REST config flow step or switched
  explicitly by the user in the reconfigure flow.
"""

from __future__ import annotations

from collections.abc import Mapping

from .const import CONF_TRANSPORT, TRANSPORT_MQTT, TRANSPORT_REST
from .coordinator import OpusGreenNetCoordinator
from .rest_coordinator import OpusGreenNetRestCoordinator

type OpusCoordinator = OpusGreenNetCoordinator | OpusGreenNetRestCoordinator


def entry_transport(data: Mapping[str, object]) -> str:
    """Return the transport of config entry data; MQTT unless REST is chosen."""
    if data.get(CONF_TRANSPORT) == TRANSPORT_REST:
        return TRANSPORT_REST
    return TRANSPORT_MQTT
