# Troubleshooting

- [Find the failing link](#find-the-failing-link)
- [Gateway not discovered](#gateway-not-discovered)
- [Devices not appearing](#devices-not-appearing)
- [Recovery](#recovery)
- [Diagnostics](#diagnostics)
- [Debug logging](#debug-logging)
- [Measuring update lag](#measuring-update-lag)

## Find the failing link

Three independent links are involved. Find out which one fails before changing
the configuration:

| Link | Symptom | Check |
|------|---------|-------|
| Network to the gateway | All entities unavailable; actions fail with "The OPUS gateway is unavailable" | Gateway power and address; `http://<gateway>:8080/system/uptime` must answer (with the API credentials) |
| Gateway credentials | The entry asks for re-authentication | User and password of the gateway API |
| EnOcean radio | Entities available, but the device does not follow | Range, power and pairing of the actuator; the debug log of the follow-up status queries |

A gateway outage makes the entities unavailable and cancels queued commands;
they are not replayed afterwards. A quiet radio device never makes an entity
unavailable.

## Gateway not discovered

Discovery relies on mDNS (`_enocean._tcp`). It does not work when Home
Assistant cannot receive multicast from the gateway, for example in Docker
without host networking, across VLANs without an mDNS repeater, or when the
gateway's announcement is blocked. Add the gateway by its address instead; the
integration works the same.

A gateway that was ignored under **Discovered** stays ignored; remove the
ignored entry to see it again.

## Devices not appearing

1. Check that the integration entry is loaded and the gateway is available.
2. Check that the device is paired with the gateway and visible in the OPUS
   app.
3. Look for messages from `custom_components.opus_greennet` in the log.
4. Newly paired devices appear on the next stream start or device list refresh,
   at the latest after 30 minutes. Reloading the entry adds them at once.

## Recovery

- If the gateway does not answer at startup, Home Assistant retries the setup
  automatically.
- After a connection loss or a gateway restart, the event stream is reopened
  and every device is resynchronised.
- The `opus_greennet.reload_entry` action restarts the integration without
  restarting Home Assistant. Pending requests are cancelled.
- An action that timed out may still have been executed: check the physical
  state before repeating it.

## Diagnostics

**Settings → Devices & services → Opus GreenNet Bridge → ⋮ → Download
diagnostics** creates a redacted report: gateway status, devices, channel
state, last update source and the latest command error. Addresses,
credentials, identifiers and the gateway's HomeKit setup code (also as QR
payload) are redacted. Attach it when you report a problem.

Useful fields:

- `first_device_list_seconds` (gateway): time from opening the event stream to
  its first device list at startup.
- `stream` (gateway): facts about the event stream, read from the integration's
  memory (no request to the gateway):
  - `connected`: whether the stream is open now.
  - `framing_requested`: `line` (one document per line) or `plain` (the
    gateway refused the line request). It is the request the gateway accepted;
    firmware that ignores the parameter answers in the plain format anyway.
  - `attempts`: opened stream requests since the integration was loaded.
  - `connects` and `reconnects`: streams that delivered a device list, and
    those after the first one. Many attempts with few connects point to a
    gateway or network that is often unreachable.
  - `radio_queue_depth`: requests waiting for the gateway's radio queue at the
    moment of the download. Values above 0 for long are worth reporting.
  The counters start at 0 whenever the integration is reloaded.
- `last_vertical_movement_time` (covers): the last run duration the gateway
  reports.
- `position_origin` (covers, per channel): `device list`, `reported`,
  `estimated` or `assumed` (the travel estimate ended without a report).
- The profile source of every D2-01 device (gateway profile or EEP table).

## Debug logging

For a one-off check use **Settings → Devices & services → Opus GreenNet Bridge
→ ⋮ → Enable debug logging**, reproduce the problem and disable it again to
download the log.

For persistent logging add to `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.opus_greennet: debug
```

## Measuring update lag

With debug logging every applied telegram is logged as
`OPUS telegram/from <device> ... gateway_to_ha_ms=<n>`: the time from the
gateway's telegram timestamp until Home Assistant received it (clock offsets
between the two affect this value). Entity writes are logged as
`OPUS update latency entity_write` with the time since the telegram arrived.

Press a physical switch and compare both values. If `gateway_to_ha_ms` is
already high, the delay is in the gateway or the network.
