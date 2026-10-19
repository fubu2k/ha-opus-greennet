"""Gateway candidates from OPUS MQTT publications (config flow discovery).

Home Assistant's MQTT integration subscribes to the topic filter declared in
``manifest.json`` (``"mqtt"``) and starts a config flow for every new topic and
payload pair. The OPUS bridge publishes no gateway-level topic on its own; it
does publish (retained) device models below ``stream/devices/<DEVICE>``. One
narrow leaf per device, ``friendlyId``, is enough to name the gateway in the
topic without subscribing to the large retained snapshot.

A matching message only identifies a candidate. It is neither proof that the
gateway is reachable now nor a reason to send a request. The config flow keeps
its existing fresh gateway check before it creates an entry. Nothing in this
module publishes to MQTT.
"""

from __future__ import annotations

import re
from typing import Final

from homeassistant.helpers.service_info.mqtt import ReceivePayloadType

from .const import TOPIC_BASE

# Eight hexadecimal characters; normalised to upper case.
EAG_ID_PATTERN: Final = re.compile(r"[0-9A-Fa-f]{8}")

# Must equal the single entry of "mqtt" in manifest.json (tested).
DISCOVERY_TOPIC_FILTER: Final = f"{TOPIC_BASE}/+/stream/devices/+/friendlyId"

_DISCOVERY_TOPIC: Final = re.compile(
    rf"{re.escape(TOPIC_BASE)}/(?P<eag_id>[^/]+)/stream/devices/[^/]+/friendlyId"
)


def normalize_eag_id(value: str) -> str | None:
    """Return the canonical EAG identifier, or None if it is invalid.

    Shared by manual setup and discovery so both derive the same unique ID.
    """
    candidate = value.strip().upper()
    return candidate if EAG_ID_PATTERN.fullmatch(candidate) else None


def eag_id_from_discovery(topic: str, payload: ReceivePayloadType) -> str | None:
    """Return the gateway identifier announced by an OPUS device-model topic.

    Returns None for topics outside the filter, for an empty payload (a cleared
    retained topic is no announcement) and for identifiers that are not already
    in canonical form. MQTT topics are case sensitive and the integration
    subscribes to ``EnOcean/<EAG_ID>/...`` with the upper-case identifier, so a
    candidate whose topic segment differs from its canonical form could never
    be served by the resulting config entry.
    """
    match = _DISCOVERY_TOPIC.fullmatch(topic)
    if match is None or not payload:
        return None
    segment = match.group("eag_id")
    eag_id = normalize_eag_id(segment)
    if eag_id is None or eag_id != segment:
        return None
    return eag_id
