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

### "The gateway address is not in the local network"

Entries that were set up with a public IP address before the gateway address
had to be local keep working, but **Settings → System → Repairs** shows this
notice for them, because the gateway speaks unencrypted HTTP and its login can
be read on the way. Re-authentication and reconfiguration only accept a local
address (error `host_not_local`). Choose **Reconfigure** in the integration's
menu and enter the gateway's address in your local network, or reach the
gateway through a VPN. The notice disappears when the integration is loaded
again with a local address. Only IP addresses are checked when the integration
loads; a host name is checked when you enter it.

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
- `versions` (gateway): `firmware`, `app` and `api` version from the gateway's
  `/system/info`. It is read at startup and then every 30 minutes together with
  the device list refresh (a plain gateway read without radio traffic, never
  with the health probe). `receive_counter` (received telegrams) and
  `average_dbm` (mean signal strength) come from the same read, so they are as
  old as `info_read_at`. A field the gateway does not report stays empty.
- `connection` (gateway): `hostname`, `ipv4` and `ipv6` of the configured
  gateway address (IPv6 only if the host name has one), resolved when the download is made. Both are redacted when they have
  a value. An empty value means the name or address could not be resolved; that
  is no error.
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
- `radio_journal` (in the block of every device): what the integration sent to
  that device over the radio, from the last 300 requests of all devices
  together, oldest first. It is kept in memory only (empty after a reload or
  restart). Each entry has:
  - `seq`: a counter that orders the entries across devices.
  - `at`: when the request ended (UTC).
  - `request`: `command` (a state command), `status_query`,
    `configuration_write`, `configuration_read` (also the read-back after a
    write), `parameters_read`, `link_tables_read`, or `feedback`.
  - `priority`, `duration_ms` and `waited_ms` (time in the queue before the
    request was sent).
  - `values`: the keys and values that were sent. Text is shortened, nested
    values show their type.
  - `outcome`: `ok` (the gateway accepted the request), `deferred` (the gateway
    stored it for a sleeping device), `rejected` (with `gateway_status`, the
    optional `http_status` and the gateway's text in `message`), `timeout`,
    `connection_error`, `auth_rejected`, `cancelled` (the caller gave up while
    the request was in flight, so it may still have been sent) or `error`.
  - A `feedback` entry is written when a device reported a field that one of
    our commands was still waiting for (`fields`, `channel`).

  `ok` only means that the gateway accepted the request; it is neither the
  stored value nor the device's state. A configuration write has two entries:
  the write and the read-back. If the write is `ok` and the read-back is
  `rejected` (for example `504`, no response from device), the value may have
  been stored. Nothing is repeated automatically. Requests that were not sent
  (gateway offline, device unknown, queued check dropped) have no entry.
- `last_vertical_movement_time` (covers): the last run duration the gateway
  reports.
- `position_origin` (covers, per channel): `device list`, `reported`,
  `estimated` or `assumed` (the travel estimate ended without a report).
- The profile source of every D2-01 device (gateway profile or EEP table).

## Debug logging

For a one-off check use **Settings → Devices & services → Opus GreenNet Bridge
→ ⋮ → Enable debug logging**, reproduce the problem and disable it again to
download the log.

With debug logging every radio request writes one line, `OPUS radio request:
device=… request=… outcome=… gateway_status=… duration_ms=… values=…
message=…`, with the same content as a `radio_journal` entry, and a device
report that confirms a command writes `OPUS device … reported […]`. The
journal in the diagnostics download is filled without debug logging; the log
adds the exact time order with other messages.

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
