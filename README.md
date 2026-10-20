<div align="center">

<img src="https://raw.githubusercontent.com/kegelmeier/ha-opus-greennet/main/custom_components/opus_greennet/brand/logo.png" alt="Opus GreenNet Bridge" width="180">

# Opus GreenNet Bridge

Home Assistant integration for the **Opus GreenNet Bridge**, controlling
**EnOcean** devices via MQTT or directly via the gateway's REST API, following
the EnOcean over IP specification.

[![HACS Custom][hacs-shield]][hacs]
[![GitHub Release][release-shield]][releases]
[![License][license-shield]][license]
![HA Min Version][ha-shield]

[![Open in HACS][hacs-repo-badge]][hacs-repo]

</div>

A custom Home Assistant integration for the Opus GreenNet Bridge, enabling control of EnOcean devices via MQTT or the gateway's REST API, following the EnOcean over IP specification.

## Features

- **Two transports**: reach the gateway through your MQTT broker (Mosquitto bridge, unchanged default for existing entries) or directly through its REST API without any broker. Each gateway entry uses one transport; switching keeps all devices, entities and IDs (see [Switching the transport](#switching-the-transport))
- **Auto-discovery**: Automatically discovers EnOcean devices connected to your Opus GreenNet Bridge
- **Gateway discovery**: Offers bridged OPUS gateways found on your MQTT broker for one-click setup, after the same fresh check as manual setup
- **Immediate telemetry**: Flat battery-level and signal-strength updates bypass the multipart state debounce
- **Real-time updates**: Receives state changes via MQTT push notifications, including device deltas, local-control telegrams, and bridge-originated command telegrams
- **Connection recovery**: Checks that the gateway responds, marks entities unavailable during outages, and refreshes discovery and state after reconnection
- **Command feedback**: Waits for gateway acknowledgements, reports rejected or timed-out commands, and follows accepted commands with channel-specific status checks
- **Bidirectional control**: Send commands to actuators (lights, switches, covers, thermostats)
- **Climate control**: HeatArea thermostat support for Valve, CosiTherm, and Electro Heating areas
- **Sensors**: Humidity, temperature, power consumption, and signal strength monitoring
- **Binary sensors**: Water leak detection, window open detection, actuator error states, battery monitoring
- **Events**: Per-button rocker switch press/release events (`buttonA0`, `buttonAI`, `buttonB0`, `buttonBI`, `multipleButtons`)
- **Device services**: ReCom API access for advanced device configuration and diagnostics
- **UI Configuration**: Set up via Home Assistant's Integrations page

## Supported Device Types

| Entity Type | EEP Profiles | Description |
|-------------|--------------|-------------|
| **Light** | D2-01-02, D2-01-03, D2-01-06, D2-01-07, D2-01-0A, D2-01-0B, D2-01-0F, D2-01-10, D2-01-12, A5-38-08, A5-38-09 | Dimmable lights and gateway switching |
| **Switch** | D2-01-00, D2-01-01, D2-01-04, D2-01-05, D2-01-08, D2-01-09, D2-01-0C, D2-01-0D, D2-01-0E, D2-01-11 | On/Off switches and actuators |
| **Cover** | D2-05-00, D2-05-01, D2-05-02 | Blinds, shades, and shutters |
| **Climate** | D1-4B-05, D1-4B-06, D1-4B-07 | OPUS HeatArea thermostats (Valve, CosiTherm, Electro Heating) |
| **Sensor** | A5-04-01, A5-04-02, A5-04-03, A5-07-01, A5-07-03, _(from climate devices)_ | Temperature and humidity (A5-04-01/02/03, including the OPUS motion detector), illuminance, supply voltage (SMS, diagnostic), battery level (shown in whole percent), humidity, feed temperature, power consumption, signal strength |
| **Binary Sensor** | D5-00-01, F6-05-01, F6-05-02, A5-07-01, A5-07-03, F6-02-01 + A5-04-01, _(from climate devices)_ | Contact open/closed state, SMS motion detection, OPUS motion detector (`buttonAI` = motion, `buttonA0` = no motion), water leak detection, RWM smoke alarm and low battery, window open, actuator errors, battery low |
| **Handle sensors** | D2-06-40, F6-10-00, D2-03-10 | Handle position plus separate open, tilted and closed binary sensors |
| **Lock** | D2-06-40 | Read-only AutoLock status, unlock-request sensor and mechanics-fault binary sensor |
| **Event** | F6-02-01, F6-02-02, F6-02-03, F6-03-01, F6-03-02 | Rocker switch press/release events, per button (`buttonA0_pressed`, `buttonA0_released`, …, `multipleButtons_released`) with `button` and `action` event attributes; event device class `button` |

Cover tilt controls are hidden when the bridge reports `rotationTime` as `0` or
`noRotation`, as used for plain roller shutters. The integration uses the existing
device discovery and state updates, without extra configuration queries. When no
valid rotation time is available, tilt support follows the device's EEP profile.

Covers with a positive configured `verticalMovementTime` show estimated
`opening` / `closing` while travelling, instead of immediately showing the target
position. The estimate uses the distance from the last known position, or a full
travel when the starting position is unknown, plus five seconds of grace. This
is an estimate, not a physical movement sensor; an incorrect configured time or
a stalled motor can make it inaccurate. A new target replaces the estimate.
Numeric position feedback, Stop, or a rejected command ends it immediately.

Status checks for these movements wait until 10 and 20 seconds after the estimate
ends, to avoid early cached target responses. For a 25-second full travel, the UI
shows movement for up to 30 seconds and checks status at 40 and 50 seconds.
Without feedback, the target becomes the assumed position when the movement
timer expires. Covers without a configured travel time retain their existing
behaviour. Existing entity IDs, tilt support, and Stop commands are unchanged.

### HOPPE window handles

All three handle profiles provide a position sensor and separate Open, Tilted,
and Closed binary sensors. `tilt` is normalized to `tilted`; missing or invalid
positions stay unknown. These report handle position, not an independent
measurement of the window sash. For Cover Control Automation, assign the Open
and Tilted binary sensors to its respective window-contact inputs.

D2-06-40 also exposes the reported lock state independently of handle position,
an unlock-request sensor, and a mechanics-fault sensor. AutoLock is read-only:
lock/unlock actions raise an error without publishing MQTT commands. The
reporter's tested MQTT write path disrupted the gateway bridge, and control
remains disabled pending manufacturer clarification. AutoLock configuration and
reported lock state are separate; a closed handle does not imply a locked handle.

The unlock-request sensor and the mechanics-fault binary sensor serve
troubleshooting rather than everyday operation. Both are diagnostic entities
and are disabled by default when Home Assistant registers them for the first
time. Existing installations keep their current setting: an entity you already
use stays enabled with its entity ID, name and icon. To activate them, open
**Settings → Devices & services → Opus GreenNet Bridge → Devices**, select the
handle, open the entity under **Diagnostic** and enable it. Home Assistant then
reloads the integration. Enabling or hiding these entities sends no request to
the handle; their values arrive only through the handle's own reports. Hiding
them is not a proven battery saving. An unrecognised mechanics value stays
unknown and is never shown as "no fault".

Handle entities use state icons: closed, open and tilted windows for the
position sensor and the Closed binary sensor, a lock with a clock for a pending
unlock request (not an unlocked lock), and an alert symbol only for a reported
mechanics fault. Unknown or unavailable values show neutral icons (a question
mark or a plain cog) and never a closed window, a lock or a healthy state.
The Open and Tilted binary sensors keep Home Assistant's window icons. Icons
you selected yourself for an entity take precedence and are not changed.

### Supply voltage and battery level

The supply voltage of SMS presence detectors (A5-07-01/03) is device
telemetry and appears under **Diagnostic** on the device page. It stays
enabled; an existing entity keeps its entity ID, name, icon and enablement, so
automations that use it continue to work.

Battery levels of all OPUS devices that report one (SMS presence detectors,
A5-04 temperature/humidity sensors and the OPUS motion detector) are shown in
whole percent, for example `88 %` instead of `87.5 %`. Only the display is
rounded: the entity state, history and long-term statistics keep the reported
value. A display precision you set yourself in the entity settings takes
precedence. Neither change sends a request to the device.

## Removing or replacing a device

Use **Settings → Devices & services → Opus GreenNet Bridge → Devices**, open the
child device, and choose **Delete**. The gateway itself cannot be removed this
way; remove its integration entry to remove the gateway.

This only removes the Home Assistant device and its entities. It sends no unpair
or deletion command to OPUS. If the gateway reports the device again, it may be
rediscovered, including after a reload or reconnection. There is no persistent
ignore list, and quiet devices are never automatically deleted.

When replacing a smoke detector or other device, first remove the old device
from the gateway's configuration where supported, then delete its stale Home
Assistant device. The replacement and other devices stay operational. Review
and update automations referencing the old entities; references are not migrated
automatically.

Binary-sensor discovery submits each entity ID once per loaded gateway. Repeated
discovery can still add newly discovered sensor types. Removing a device releases
its IDs after its live entities finish removal, so rediscovery and reload can
create them again without duplicate submissions.

### Upgrading from community test versions

German translations include “Beleuchtungsstärke” for illuminance and readable
HOPPE unlock-request states. Custom entity names remain unchanged.
The unlock-request sensor uses `requested` / `not_requested`; update automations
that previously compared its state with boolean text.

The integration migrates the community fork's `_illumination` and
`_window_handle_lock` unique IDs to the upstream equivalents while preserving
the existing Home Assistant entity IDs, custom names, and settings. If both the
fork and beta entries exist for the same device and gateway, the older fork
entity is preserved and the duplicate beta entry is removed. References to the
removed beta entity must be updated manually; the integration logs both entity
IDs and shows a repair under **Settings → System → Repairs** naming the removed
and the preserved entity. After updating your automations, scripts and
dashboards, open the repair and select **Submit** to dismiss it. The repair
survives restarts until you confirm it or delete the integration entry.
Entries belonging to another gateway or device are left untouched.

### Gateway payload compatibility

Detector feedback accepts both the beta's descriptive keys and the gateway keys
reported on OPUS IQ-DOT firmware 1.21.30: `alarm` (on/off), boolean `batteryLow`,
`motionDetected` (boolean), `illumination`, and HOPPE `handle`, `unlock`
(requested/notRequested), and `mechanics` (error/ok). The existing key/value
formats remain supported. Battery percentages accept an optional `%` suffix in
snapshots, indexed updates, and the immediate telemetry path, with finite 0–100
validation. Unknown values are never interpreted as no alarm or a closed handle.

### Large installations and gateway recovery

Awaited gateway requests share one queue across devices. Control commands take
priority over status queries, and each acknowledgement timeout starts after the
request is published. Large batches can take longer to finish because requests
are sent one at a time. A missing acknowledgement fails that action; gateway
availability is determined by connection events and the independent health probe.

Setup checks the gateway before subscribing to the large retained device streams.
Device discovery then continues in the background. Stream subscriptions use QoS 0
to reduce acknowledgement traffic during retained replay; answer subscriptions
remain at QoS 1. Discovery resumes if a reconnect interrupts startup, while rapid
reconnects avoid requesting repeated complete snapshots. An outage cancels the
integration's queued commands rather than replaying them after recovery.

Retained `getAnswer/devices` responses are ignored to prevent stale RPC replies
from restoring old sensor states. Retained `stream/devices` snapshots remain
supported.

F6-05-01 leak sensors remain `unknown` until a valid `liquidDetected` reading is
received. Missing data does not imply that a sensor is dry. Invalid updates and
rediscovery preserve a previously received alarm state.

## Prerequisites

1. **Home Assistant** 2026.10.1 or newer
2. **Opus GreenNet Bridge** with **EnOcean devices** paired to it
3. One of the two transports:
   - **MQTT**: the Home Assistant MQTT integration (e.g., Mosquitto add-on) and
     an **MQTT bridge** between your HA broker and the Opus GreenNet broker
     (see [MQTT Bridge Setup](#mqtt-bridge-setup-required))
   - **REST API**: the gateway reachable from Home Assistant on its REST port
     (`8080` by default) and its API credentials (see
     [REST API Setup](#rest-api-setup)). No broker, MQTT integration or bridge
     is needed; Home Assistant does not load the MQTT integration for a REST
     entry.

## MQTT Bridge Setup (Required)

Required for the MQTT transport only. With the REST transport, skip this
section and continue with [REST API Setup](#rest-api-setup).

The Opus GreenNet Bridge runs its own MQTT broker. To connect it to Home Assistant, you need to configure an MQTT bridge on your Home Assistant MQTT broker (e.g., Mosquitto).

### For Mosquitto Add-on

1. In Home Assistant, go to **Settings** → **Add-ons** → **Mosquitto broker** → **Configuration**

2. Add the following to the **Customize** section (or edit the Mosquitto config directly):

   Create a file `/share/mosquitto/opus_bridge.conf` with:

   ```
   connection opus_greennet_<EAG-ID>
   address <OPUS_BRIDGE_IP>:1883
   bridge_protocol_version mqttv311
   remote_username <username>
   remote_password <password>
   cleansession true
   keepalive_interval 15
   restart_timeout 5 30
   bridge_outgoing_retain false
   notifications true
   notifications_local_only true
   notification_topic opus_greennet/<EAG-ID>/bridge/status

   topic EnOcean/<EAG-ID>/stream/# in 1
   topic EnOcean/<EAG-ID>/getAnswer/# in 1
   topic EnOcean/<EAG-ID>/putAnswer/# in 1
   topic EnOcean/<EAG-ID>/get/# out 0
   topic EnOcean/<EAG-ID>/put/# out 0
   ```

   Replace:
   - `<OPUS_BRIDGE_IP>` with your Opus GreenNet Bridge's IP address
   - `<EAG-ID>` with its eight-character uppercase identifier, in every occurrence
   - `<username>`: `admin`
   - `<password>`: Your gateway's EURID in uppercase (fictional example: `A1B2C3D4`)

3. Reference this config file in the Mosquitto add-on configuration:

   ```yaml
   customize:
     active: true
     folder: mosquitto
   ```

4. Restart the Mosquitto add-on

### For Standalone Mosquitto

Use the same connection block above in your `mosquitto.conf`, including the
credentials, topic directions, and notification topic.

### Updating an existing MQTT bridge

Replace the old `topic EnOcean/# both 1` rule with the scoped rules above; do not
leave both configurations enabled. Each gateway needs its own connection name
and topic identifier.

`in` carries OPUS updates to your local broker; `out` carries requests to OPUS.
Outbound QoS 0 avoids building a persistent command backlog with Mosquitto's
default `queue_qos0_messages false`. A lost request produces an action timeout;
commands are not automatically retried. Keep that broker default when using this
configuration. `retain=False` alone does not prevent queued QoS 1 commands.
Previously retained commands or an existing queue are not purged by changing
these rules; inspect and clear any obsolete command messages separately before
reconnecting the bridge.

The optional notification topic reports `1` for a connected bridge and `0` for a
disconnected bridge. The integration listens to it for prompt availability
updates. Existing configurations without this topic continue to use periodic
gateway health checks. See the [Mosquitto bridge documentation](https://mosquitto.org/man/mosquitto-conf-5.html)
for connection, notification, and queue settings.

### Verify the Bridge

Use an MQTT client (like MQTT Explorer) connected to your HA broker to verify you can see topics like:
```
EnOcean/<EAG-ID>/stream/telegram/#
```

If you see messages when triggering EnOcean devices, the bridge is working.

### Single input contacts

D5-00-01 contacts expose a Contact binary sensor with the opening device class.
The gateway values `open` and `closed` appear as `on` and `off`. Missing or
unrecognized contact values remain `unknown`; a gateway outage makes the entity
`unavailable`. No battery percentage is inferred when the gateway publishes none.

## REST API Setup

The REST transport talks to the gateway's EnOcean over IP REST API directly
(plain HTTP with basic authentication, as provided by the manufacturer).

1. Give the gateway a fixed IP address (DHCP reservation), so the configured
   address stays valid.
2. Note the API credentials: user `admin`, password = the gateway's EURID in
   capital letters (printed on the gateway; fictional example: `A1B2C3D4`),
   unless you changed them.
3. Make sure Home Assistant can reach port `8080` of the gateway. Keep the
   gateway in a trusted network.

Setup reads only the gateway's system information (`GET /system/info`) and
uses its EURID as the stable identifier, the same EAG identifier that the MQTT
transport uses. No EnOcean device is queried during setup.

## Installation

### HACS (Recommended)

1. Make sure [HACS](https://hacs.xyz) is installed.
2. Add this repository as a **custom repository** (category **Integration**):

   [![Open in HACS][hacs-repo-badge]][hacs-repo]

   …or in HACS go to **⋮ → Custom repositories**, paste
   `https://github.com/kegelmeier/ha-opus-greennet`, choose **Integration**, and add it.
3. Search for **Opus GreenNet Bridge**, install, and **restart Home Assistant**.

Home Assistant 2026.10.1 or newer is required. To test a beta, enable pre-release
versions for this repository in HACS before selecting the beta version.

### Manual Installation

1. Copy the `custom_components/opus_greennet` folder to your Home Assistant's `custom_components` directory
2. Restart Home Assistant

## Configuration

After installing, add the integration:

[![Add Integration][config-flow-badge]][config-flow]

…or go to **Settings → Devices & Services → Add Integration → “Opus GreenNet Bridge”**
and choose how Home Assistant reaches the gateway.

**Through the MQTT broker (EAG identifier)**

1. Enter your **EAG Identifier** (Bridge ID; fictional example: `A1B2C3D4`)
2. Click **Submit**

Setup waits for a response from that specific gateway. A connected Home Assistant
MQTT broker alone is insufficient. If setup fails, check the identifier, gateway
power, bridge credentials, and the directional topic rules above. Existing
entries automatically retry when the gateway is unavailable during startup.

**Directly through the gateway's REST API**

| Field | Description |
|-------|-------------|
| **Host** | IP address or host name of the gateway |
| **Port** | Port of the REST API, `8080` by default |
| **Username** | API user, `admin` by default |
| **Password** | API password, by default the gateway's EURID in capital letters |

Setup reads the gateway's EURID and uses it as the unique identifier of the
entry, exactly like the EAG identifier of the MQTT transport. A gateway can be
configured only once, whichever transport is used. If setup fails, check the
address, the credentials and that Home Assistant can reach the REST port.
Existing entries automatically retry when the gateway is unavailable during
startup. Rejected credentials, or a different gateway answering at the stored
address, start a **re-authentication** that asks for address and credentials
and accepts them only if the gateway answers with the same EURID.

### Switching the transport

Open **Settings → Devices & services → Opus GreenNet Bridge → ⋮ → Reconfigure**
on the gateway entry:

- **Directly through the gateway's REST API**: enter or change host, port and
  credentials. For an entry that used MQTT so far, the password field suggests
  the vendor default (the EURID). The change is accepted only if the address
  answers as the same gateway.
- **Through the MQTT broker**: the integration first checks that the gateway
  answers through the broker (the same single uptime check as setup), then
  removes the stored REST address and credentials.

The entry reloads with the new transport. Devices, entities, entity IDs, unique
IDs, names, areas, enabled/disabled settings, dashboards and automations stay
unchanged. Existing entries are never switched automatically; entries created
before REST support keep using MQTT until you choose otherwise. When an entry no
longer uses MQTT and no other entry needs it, you can remove its connection
block from the Mosquitto configuration (see
[Removing the integration](#removing-the-integration)).

### Automatic gateway discovery

Once the MQTT bridge forwards `EnOcean/<EAG-ID>/stream/#` (see
[MQTT Bridge Setup](#mqtt-bridge-setup-required)), Home Assistant offers each
gateway under **Settings → Devices & services → Discovered** as
**Opus GreenNet Bridge** with its EAG identifier. Select **Add** and confirm.

- Discovery listens only to `EnOcean/+/stream/devices/+/friendlyId`, the
  retained device-model name that the gateway publishes for each paired device.
  It does not subscribe to the large retained snapshot.
- Receiving the message publishes nothing and queries no device. A retained
  message only names a candidate; it is not taken as proof that the gateway is
  online. Confirming runs the same single uptime check as manual setup, and the
  entry is created only if that specific gateway answers.
- Every device announces the same gateway, so each gateway is offered once.
  Configured, disabled and ignored gateways are not offered again. To have an
  ignored gateway offered again, stop ignoring it on the integration page.
- While an offer is open, adding the same identifier manually reports that a
  configuration flow is already in progress. Use the offer, or ignore it first.
- The identifier must appear in upper case in the topics, as the integration
  subscribes in upper case. Other topics are ignored.
- Requirements: MQTT discovery enabled in the MQTT integration (default), the
  bridge configuration above, and at least one paired device. Unbridged
  gateways cannot be discovered; manual setup is always available.

## Availability and command behavior

The integration uses push updates, with a fresh MQTT uptime health check approximately
every 60 seconds. MQTT disconnects and the optional bridge-status topic update
availability immediately; a failed health check also marks entities unavailable.
Batteryless EnOcean devices are not marked offline just because they are quiet.
On reconnection, the integration requests a fresh device list and gateway data.
Gateway system information is optional: some firmware responds to MQTT uptime
and device requests but does not answer the system-information endpoint. Device
snapshots and indexed stream updates support both map and list state formats.

Commands wait for the gateway's acknowledgement. Status `200` means the gateway
sent the telegram; `201` means it accepted deferred delivery. Neither confirms
that the physical actuator has completed the operation. After `200`, device
reports and subsequent channel-specific status queries reconcile the state.
After `201` the entity keeps its last reported state and no status query is
sent until the gateway actually transmits the telegram. Requests fail
when the gateway is known to be unavailable and are cancelled during reload.
Interim `unknown`/`invalid` readings and unrelated metadata do not cancel the
5- and 20-second checks. Confirmation must report a valid value for the requested
field on its channel. Cover Stop sends the profile's dedicated stop command and
then checks the reported position and, when supported, tilt; it does not assume
a new position.

OPUS acknowledgement topics do not include request identifiers. Requests to the
same endpoint are serialized, but a late reply after a timeout or a simultaneous
command from another MQTT client can remain ambiguous. Check the physical state
before retrying a timed-out command.

Measurements explicitly reported as `notAvailable` become unknown. Lights and
switches remain unknown until their state is reported or a command is accepted.
Climate activity uses actuator feedback where available; enabling a heating
zone does not by itself prove that it is currently heating.

### REST transport

Entries on the REST transport keep one HTTP event stream to the gateway open
(`GET /devices/stream`). Every stream starts with the complete device list,
which resynchronises all state, and then delivers each telegram as the gateway
handles it. If the stream ends, it is reopened after a short pause; if the
gateway cannot be reached, entities become unavailable. The pause starts at 1
second, doubles after every attempt up to 60 seconds and is lengthened by a
random share of up to 25 %. A stream that closes within 10 seconds of
connecting counts as a failed attempt, so a gateway that closes every
connection at once cannot cause a reconnect loop; a stream that stayed open
longer starts the pauses again at 1 second. Rejected credentials are not
retried. A stream without any data for 15 minutes is reopened.

The health check reads the gateway uptime about every 60 seconds, as with MQTT.
It marks entities unavailable when the gateway stops answering, and reopens the
stream after a gateway restart. It never confirms or changes the state of a
device. Every 30 minutes the gateway's device list is read again to refresh
values that the stream does not carry (battery level, signal strength, cover
movement times) and to add newly paired devices. This list comes from the
gateway's device model; it sends no radio telegram and queries no device.

Commands are HTTP requests; the gateway's response is the acknowledgement and
belongs to exactly that request, so the late-answer ambiguity of the MQTT
transport does not exist (the `late_answers` diagnostics stay zero). Status
`200` and `201` have the same meaning as with MQTT: after `201` the entity keeps
its last reported state, no travel estimate starts and no status query is sent
until the gateway actually transmits the telegram. The same 5- and 20-second
status checks follow accepted commands and commands from other controllers
(for example Apple HomeKit); the gateway's echo of this integration's own
transmitted command is recognised and schedules no extra check.

D2-01 dimmers receive `rampingMode: switch` with every dim command and change
brightness at once, like Apple HomeKit commands. An action with a `transition`
uses the dimmer's own configured fade (`rampingTime1`), not the requested
duration. A5-38-08 gateway dimming is unchanged. On the MQTT transport dim
commands are unchanged (no ramping mode, no transition feature).

## Known limitations

- OPUS acknowledgement topics carry no request identifier. Requests to the same
  answer topic are serialized; a reply that arrives after the 10-second timeout
  cannot be attributed with certainty.
- An acknowledgement (`200` or `201`) confirms the gateway, not the radio
  actuator. Physical completion is only known from device feedback or a later
  status query.
- After `201` (deferred delivery) it is not documented whether the gateway
  still delivers a queued telegram when a newer command for the same function
  is sent in the meantime. Check the device before repeating such a command.
- Cover positions during movement are estimates based on the reported travel
  time. The final position comes from the device report or status query.
- After a full run into the fully open or closed position, the cover actuator
  reports its final position only when it switches off its motor. D2-05
  actuators keep the motor on for a few seconds beyond the configured travel
  time to make sure the end position is reached (the upstream project measured
  3.5 to 5.7 seconds), so the final position can appear several seconds after
  the shutter has physically stopped. Apple Home and the OPUS app show the same
  delay; it is actuator behaviour on both transports. Stop and intermediate
  positions report without this delay. The integration sends no extra query to
  shorten it.
- The HOPPE AutoLock entity is read-only; lock and unlock actions are rejected.
- Gateway discovery depends on the bridged, retained `friendlyId` device-model
  topics. A gateway without paired devices, a bridge that does not forward
  `stream/#`, or a firmware that publishes no `friendlyId` is not discovered;
  enter the EAG identifier manually. Discovery has not yet been observed on
  real hardware. There is no options flow; a different gateway is a new
  integration entry. The reconfigure flow switches the transport or changes the
  REST address and credentials of the same gateway.
- REST gateways are not discovered automatically; the address is entered once.
  Give the gateway a fixed address, or update it with **Reconfigure**.
- The gateway's REST API is plain HTTP with basic authentication, as provided
  by the manufacturer. Keep the gateway in a trusted network.
- With the REST transport, battery level and signal strength are refreshed from
  the device list every 30 minutes; the event stream does not carry them.
- The REST transport was ported from the upstream project, where it was
  observed on a real gateway (OPUS-GW v1.21.31). This repository's automated
  tests run it against a simulated gateway; the maintainer has additionally
  tried it on one installation (switching, covers with Stop and retargeting,
  rockers). That is a practical check, not a complete hardware verification.
- Quiet or batteryless devices are never removed automatically, because the
  gateway cannot confirm that a device is gone (see
  [Removing or replacing a device](#removing-or-replacing-a-device)).
- Commands and configuration writes are never retried automatically after a
  timeout or an outage.
- Automated tests simulate MQTT and the REST API. They do not verify physical
  hardware.

## Services

The integration exposes the following administrator-only actions for advanced
device management, accessible via **Developer Tools** → **Actions**:

| Service | Parameters | Description |
|---------|-----------|-------------|
| `opus_greennet.get_device_configuration` | `device_id` | Retrieve the stored configuration for an EnOcean device via ReCom API |
| `opus_greennet.set_device_configuration` | `device_id`, `configuration` | Write configuration to an EnOcean device via ReCom API |
| `opus_greennet.get_device_parameters` | `device_id` | Retrieve DDF parameters for an EnOcean device via ReCom API |
| `opus_greennet.reload_entry` | _(optional)_ `config_entry_id` | Re-run integration setup/teardown without restarting HA |

The `device_id` is the EURID of the target device (fictional example: `AABB1122`). Select a
gateway when more than one Opus GreenNet config entry is loaded. The two read
actions return their response data directly in Home Assistant.

## Use cases

The integration reacts to reports the EnOcean devices send on their own. None
of the following use cases needs polling, and battery-powered devices are never
woken up by Home Assistant to serve them.

- **Wall rockers as Home Assistant controls.** Use an EnOcean rocker to switch
  Home Assistant lights, scenes or media. Each press and release is an event
  entity; retained telegrams after a restart or reconnect never fire events.
- **Window handles for heating and shading.** React to HOPPE handle positions,
  for example to switch a HeatArea off while a window is open or tilted, or
  feed the Open and Tilted binary sensors into a cover automation. The handle
  reports its position, not a measurement of the window sash.
- **Safety alarms.** Forward OPUS RWM smoke alarms, leak sensors and low
  batteries to your phone or switch on lights for an escape route.
- **Heating supervision.** Watch HeatArea warnings such as an actuator that does
  not respond or a missing temperature, which are diagnostic binary sensors.
- **Troubleshooting a handle.** Temporarily enable the unlock-request and
  mechanics-fault diagnostics of a D2-06-40 handle to see what it reports.
- **Controlling actuators.** Switch, dim, move covers and set HeatArea
  temperatures from dashboards, voice assistants and automations.

Battery-friendly behaviour in your own automations:

- Trigger on state changes of the entities. Do not schedule
  `opus_greennet.get_device_configuration`, `opus_greennet.get_device_parameters`
  or `opus_greennet.set_device_configuration`: they access the device through
  the ReCom API and are meant for manual administration.
- Each accepted command causes up to two status checks (after 5 and 20
  seconds) unless the device reports first. Use `for:` delays instead of
  sending commands on every short change.
- `opus_greennet.reload_entry` re-reads the device list from the gateway; it
  is a recovery tool, not a refresh to run periodically.

## Examples

All entity IDs below are placeholders. Replace them with the IDs shown under
**Settings → Devices & services → Opus GreenNet Bridge → Entities** for your
installation. The examples use only existing entities and actions.

### Rocker control

Rocker events are named `<button>_<action>`, for example `buttonAI_pressed` or
`buttonA0_released`; the attributes `button` and `action` carry the same
information. The event entity's state is the time of the last event. When the
integration reloads or reconnects, Home Assistant restores the last event, so
the condition below ignores restored states instead of repeating an old press.

```yaml
alias: "Hallway rocker: lights"
description: "Rocker A: I switches on, 0 switches off"
mode: queued
triggers:
  - trigger: state
    entity_id: event.hallway_rocker  # placeholder
conditions:
  # A restored or re-added state is not a new press.
  - condition: template
    value_template: >-
      {{ trigger.from_state is not none
         and trigger.from_state.state != 'unavailable'
         and trigger.to_state is not none
         and trigger.to_state.state not in ['unavailable', 'unknown'] }}
actions:
  - choose:
      - conditions:
          - condition: template
            value_template: >-
              {{ trigger.to_state.attributes.event_type == 'buttonAI_pressed' }}
        sequence:
          - action: light.turn_on
            target:
              entity_id: light.hallway  # placeholder
      - conditions:
          - condition: template
            value_template: >-
              {{ trigger.to_state.attributes.event_type == 'buttonA0_pressed' }}
        sequence:
          - action: light.turn_off
            target:
              entity_id: light.hallway  # placeholder
```

After `light.turn_on` the light shows the expected state as soon as the gateway
accepts the command; the device report or the status checks confirm or correct
it. The OPUS motion detector uses `buttonAI`/`buttonA0` for motion, which the
integration already exposes as a Motion binary sensor; use that sensor instead
of its rocker events.

### Handle state

Switch a heating zone off while a window is open or tilted, and back on when it
has been closed for a minute. Unknown or unavailable handle values match none
of the triggers, so missing data never switches the heating.

```yaml
alias: "Bedroom window: heating"
mode: restart
triggers:
  - trigger: state
    entity_id: sensor.bedroom_window_handle_position  # placeholder
    to:
      - open
      - tilted
    for: "00:02:00"
    id: opened
  - trigger: state
    entity_id: sensor.bedroom_window_handle_position  # placeholder
    to: closed
    for: "00:01:00"
    id: closed
actions:
  - choose:
      - conditions:
          - condition: trigger
            id: opened
        sequence:
          - action: climate.turn_off
            target:
              entity_id: climate.bedroom  # placeholder
      - conditions:
          - condition: trigger
            id: closed
          - condition: state
            entity_id: climate.bedroom  # placeholder
            state: "off"
        sequence:
          - action: climate.turn_on
            target:
              entity_id: climate.bedroom  # placeholder
```

The sensor states are `open`, `closed` and `tilted`; the UI shows translated
labels. `climate.turn_on` restores the profile's own "on" mode (heating or
on). Each switch is one radio command plus at most two status checks, which is
why the delays above avoid reacting to short handle movements. A closed handle
does not imply that a D2-06-40 handle is locked; use the read-only AutoLock
entity for the lock state.

The unlock-request and mechanics-fault diagnostics of a D2-06-40 handle are
disabled by default. To use the following example, enable
`binary_sensor.bedroom_window_mechanics_fault` first (see
[HOPPE window handles](#hoppe-window-handles)). Enabling it sends nothing to
the handle.

```yaml
alias: "Bedroom window: mechanics fault"
triggers:
  - trigger: state
    entity_id: binary_sensor.bedroom_window_mechanics_fault  # placeholder
    to: "on"
    for: "00:05:00"
actions:
  - action: persistent_notification.create
    data:
      title: "Window handle"
      message: "The bedroom window handle reports a mechanics fault."
```

### Alarm

Forward a smoke alarm to a phone and switch on the hallway light. Only the
state `on` is an alarm. `off` means the detector reported no alarm; `unknown`
means no valid report was received yet and is never treated as "no alarm".
A gateway or broker outage shows as `unavailable`; the second trigger warns
about a longer outage instead of silently missing alarms.

```yaml
alias: "Smoke alarm"
mode: single
triggers:
  - trigger: state
    entity_id: binary_sensor.kitchen_smoke_detector_smoke_alarm  # placeholder
    to: "on"
    id: alarm
  - trigger: state
    entity_id: binary_sensor.kitchen_smoke_detector_smoke_alarm  # placeholder
    to: unavailable
    for: "00:15:00"
    id: unavailable
actions:
  - choose:
      - conditions:
          - condition: trigger
            id: alarm
        sequence:
          - action: notify.mobile_app_my_phone  # placeholder
            data:
              title: "Smoke alarm"
              message: "The kitchen smoke detector reports smoke."
          - action: light.turn_on
            target:
              entity_id: light.hallway  # placeholder
      - conditions:
          - condition: trigger
            id: unavailable
        sequence:
          - action: notify.mobile_app_my_phone  # placeholder
            data:
              title: "Smoke detector unreachable"
              message: >-
                The OPUS gateway or the MQTT connection has been unavailable
                for 15 minutes. Check the gateway and the MQTT bridge.
```

The same pattern works for leak sensors (`binary_sensor.<name>_water_leak`,
`on` = water detected) and for the detector's `battery_low` binary sensor. A
quiet detector stays available: battery-powered detectors report changes
rather than a heartbeat, so the integration cannot detect a removed or dead
detector.

### Dashboard

An entities card for one window shows the state icons described above:

```yaml
type: entities
title: Bedroom window
entities:
  - entity: sensor.bedroom_window_handle_position  # placeholder
  - entity: binary_sensor.bedroom_window_handle_open  # placeholder
  - entity: binary_sensor.bedroom_window_handle_tilted  # placeholder
  - entity: lock.bedroom_window_autolock  # placeholder, D2-06-40 only
```

## MQTT Topic Structure

The integration follows the EnOcean over IP MQTT specification:

```
EnOcean/{EAG-Identifier}/stream/telegram/{Device-Identifier}/from  # Device → Gateway
EnOcean/{EAG-Identifier}/stream/telegram/{Device-Identifier}/to    # Gateway → Device
EnOcean/{EAG-Identifier}/put/devices/{Device-Identifier}/state     # Commands
EnOcean/{EAG-Identifier}/get/devices                               # Device discovery
EnOcean/{EAG-Identifier}/getAnswer/devices/{Device-Identifier}     # Discovery response
EnOcean/{EAG-Identifier}/stream/devices/{Device-Identifier}/friendlyId  # Gateway discovery
```

## REST API

Entries on the REST transport use these endpoints of the gateway's EnOcean over
IP REST API (basic authentication, JSON documents with a `header.httpStatus`):

```
GET  /system/info                      # Gateway identity (EURID) and version
GET  /system/uptime                    # Health check (every 60 s)
GET  /devices                          # Device list (setup, every 30 min)
GET  /devices/stream                   # Event stream: device list, then telegrams
GET  /devices/{Device-Identifier}      # One device (late discovery, at most hourly)
PUT  /devices/{Device-Identifier}/state          # Commands and status queries
GET  /devices/{Device-Identifier}/configuration  # ReCom: stored configuration
PUT  /devices/{Device-Identifier}/configuration  # ReCom: write configuration
GET  /devices/{Device-Identifier}/parameters     # ReCom: supported parameters
```

Only `PUT …/state` and the ReCom requests can reach a radio device; they run
one at a time, user commands first, and are never repeated automatically.
An answer counts as success only if both the HTTP status line (2xx) and the
document's `header.httpStatus` (`200` or `201`) report success; otherwise the
action fails with both values in its error text. No request is retried after
a timeout or an error.

## Telegram Payload Format

The identifiers and values below are fictional examples.

```json
{
  "telegram": {
    "deviceId": "AABB1122",
    "friendlyId": "Example_Light",
    "timestamp": "2024-01-15T10:30:00.000+0100",
    "direction": "from",
    "functions": [
      {"key": "switch", "value": "on"},
      {"key": "dimValue", "value": "75"}
    ],
    "telegramInfo": {
      "data": "0000000A",
      "dbm": -65,
      "rorg": "A5"
    }
  }
}
```

## Troubleshooting

### Devices not appearing

1. MQTT transport: check that the MQTT integration is connected
2. Verify your EAG Identifier is correct
3. Check Home Assistant logs for MQTT subscription errors
4. Ensure your Opus GreenNet Bridge is publishing to the expected topics
5. REST transport: download diagnostics and check that `gateway.available` is
   `true`; the devices come from the gateway's device list

### REST connection

| Symptom | Check |
|---------|-------|
| Setup form reports that the connection failed | Host, port and that Home Assistant can reach the gateway (for example from the Terminal add-on: `curl http://<host>:8080/system/uptime` with the credentials) |
| Setup form reports rejected username or password | API user (`admin`) and password (by default the EURID in capital letters) |
| “This address answers as a different gateway” | The address belongs to another OPUS gateway; enter the address of the configured one |
| The entry asks for re-authentication | The gateway rejected the stored credentials or another gateway answers at the stored address; enter the current values |
| All entities unavailable | Gateway power and network; the integration retries the event stream with pauses of up to 60 seconds |

### Broker, gateway or radio actuator

Three independent links are involved. Identify which one fails before changing
the configuration:

| Link | Symptom | Check |
|------|---------|-------|
| Home Assistant MQTT broker | All entities unavailable; actions fail with "The Home Assistant MQTT connection is unavailable" | State of the MQTT integration and the broker log |
| Bridge to the OPUS gateway | All entities unavailable; actions fail with "The OPUS gateway is unavailable" | `opus_greennet/<EAG-ID>/bridge/status` (`1` connected, `0` disconnected); the uptime health check runs about every 60 seconds |
| EnOcean radio actuator | Entities available, but the physical device does not follow | Range, power and pairing of the actuator; the debug log of the follow-up status queries |

A broker or bridge outage makes the gateway unavailable and cancels queued
commands. They are not replayed after recovery. A quiet radio device does not
make any entity unavailable.

### Acknowledgements 200 and 201

After a command, the gateway answers on `putAnswer`. Any status other than
`200` or `201` fails the action. Neither proves that the actuator executed
the command.

- `200`: the gateway sent the telegram. The integration shows the expected
  state and checks it with status queries 5 and 20 seconds later; for covers,
  10 and 20 seconds after the estimated end of the movement.
- `201` (SoftSmartAck): the gateway queued the telegram for later delivery,
  for example until the device next communicates. The action succeeds, but the
  entity keeps its last reported state, a cover starts no movement estimate,
  and no status query is sent, because a query would wait for the same device
  and adds radio traffic. When the gateway later transmits the telegram, the
  value is shown and the usual two checks follow. Until a matching device
  report or that transmission arrives, the value is listed under
  `deferred_commands` in the integration diagnostics.

A warning or error reported by one device, for example a HeatArea's
`actuatorNotResponding`, appears on that device's own entities only. It never
makes the gateway or other entities unavailable.

### Retained state

The broker keeps retained copies of OPUS topics. Retained device snapshots on
`stream/devices` restore the last known state after a restart or reconnect.
Retained `getAnswer/devices` and `putAnswer` messages are ignored because they
answer earlier requests. Retained radio telegrams never fire rocker events.
After a reconnect a fresh device list is requested at most once every 60
seconds.

While the connection is uninterrupted, a retained device-model value no longer
overwrites a field (per device and channel) that has already been updated by a
live message, a radio telegram or the answer to the integration's own device
list request. Other fields, other channels and the first snapshot of a device
are applied as before. After a broker or bridge outage, retained values apply
again, because they can be newer than the last value received live. This
protection sends no MQTT request.

### Estimated values

Cover position and tilt during a movement are estimated from the reported
travel time plus a 5-second grace period. After an accepted command the
expected state is shown until a device report or status query confirms or
corrects it.

### Timeouts without a request ID

If an action times out after 10 seconds, the gateway may still execute it, and
a late reply cannot be matched to the request. Check the physical state before
repeating the command. The integration does not retry automatically.

If the late reply arrives while the next request to the same device is
waiting, it is taken as that request's reply. The next action can then report
success although the gateway rejected it, or report an error although the
gateway accepted it. Replies never cross devices, and retained replies are
ignored. A device report always overrides what the reply suggested. The same
applies to a reply to another MQTT client (for example the MyOPUS app) that
arrives while a request to the same device is waiting.

### Repairs

The integration raises one kind of repair: after it removed a duplicate beta
entity during a unique-ID migration (see
[Upgrading from community test versions](#upgrading-from-community-test-versions)).
Broker, bridge or radio outages and quiet sensors never create repairs; they
show as unavailable entities and in the log.

### Gateway not discovered

- Check that the MQTT bridge forwards `EnOcean/<EAG-ID>/stream/#` and that
  topics such as `EnOcean/<EAG-ID>/stream/devices/<DEVICE>/friendlyId` exist on
  your broker (for example with MQTT Explorer).
- Check that discovery is enabled in the MQTT integration options.
- Check whether the gateway is already set up, disabled or ignored.
- Add the gateway manually with its EAG identifier.

### Recovery

- If the gateway does not answer at startup, Home Assistant retries the setup
  automatically.
- After an MQTT or bridge reconnect, subscriptions are restored and the device
  list is refreshed.
- Run the `opus_greennet.reload_entry` action to restart the integration
  without restarting Home Assistant. Pending requests are cancelled.
- Download diagnostics (see below) before reporting a problem.

### Enable debug logging

For temporary troubleshooting, open **Settings → Devices & services →
Opus GreenNet Bridge → three-dot menu → Enable debug logging**. Disable it
after reproducing the problem and save the downloaded log.

For persistent logging, add to `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.opus_greennet: debug
```

### Debug update lag

When debug logging is enabled, state updates include latency markers for the
main handoff points:

- `OPUS update latency received`: MQTT message reached the integration
- `OPUS update latency finalized`: debounced MQTT fragments were converted into state functions
- `OPUS update latency dispatch`: the coordinator notified Home Assistant entities
- `OPUS update latency entity_write`: the entity wrote its Home Assistant state

On the REST transport each telegram is logged once with `gateway_to_ha_ms`, the
time from the gateway's telegram timestamp to its arrival in Home Assistant.

Retained device-model properties are not logged individually during reconnects;
large cached snapshots would otherwise flood the log and delay startup. Live
updates keep their latency markers.

Download a redacted diagnostic report from **Settings → Devices & services →
Opus GreenNet Bridge → three-dot menu → Download diagnostics**. It includes the
gateway status, discovered devices, channel state, last update source, and the
latest bridge command error without adding volatile diagnostic fields to every
entity state.

Each device and the gateway also list `late_answers`:
`unanswered_requests` counts requests from the last two minutes that ended
without an answer (timeout or cancellation), and `possibly_late_answers` counts
answers that arrived while such a request was still open and may therefore
belong to it (see [Timeouts without a request ID](#timeouts-without-a-request-id)).
These counters only make the situation visible; they never delay, repeat or
change a request.

For local switch tests, press the physical control and compare the time between
`received`, `dispatch`, and `entity_write`. If `received` is already delayed, the
lag is before this integration. If `received` is fast but `entity_write` is slow,
the lag is inside Home Assistant or this integration.

## Removing the integration

1. Go to **Settings → Devices & services → Opus GreenNet Bridge**, open the
   three-dot menu of the entry and select **Delete**. Home Assistant removes the
   entry, its devices and entities. The gateway and the pairing of the EnOcean
   devices are not changed.
2. MQTT transport: when no other entry uses the gateway, remove its connection
   block from the Mosquitto configuration (for example
   `/share/mosquitto/opus_bridge.conf`) and restart the broker. Otherwise the
   broker keeps bridging the OPUS topics. The REST transport needs no cleanup
   outside Home Assistant.
3. Remove the integration in HACS, or delete
   `custom_components/opus_greennet` for a manual installation, and restart
   Home Assistant.

## Development

### Project Structure

```
custom_components/opus_greennet/
├── __init__.py           # Integration setup and service registration
├── manifest.json         # Integration metadata and version
├── config_flow.py        # UI configuration (MQTT or REST), reauth, reconfigure, MQTT discovery
├── discovery.py          # Validation of discovered gateway candidates
├── const.py              # Constants, EEP mappings, and MQTT topics
├── transport.py          # Transport selection and the common coordinator protocol
├── domain.py             # Transport-neutral command results, signals and timing
├── coordinator.py        # MQTT transport: communication, discovery, and commands
├── mqtt_transport.py     # Confirmed subscriptions, requests, and gateway probing
├── rest_coordinator.py   # REST transport: event stream, health probe, and commands
├── api.py                # REST client: requests, errors, stream splitter (no HA imports)
├── protocol.py           # Pure helpers for REST gateway documents
├── enocean_device.py     # Device and channel data model
├── entity.py             # Shared entity and device-registry behavior
├── diagnostics.py        # Redacted integration diagnostics
├── lock.py               # Read-only HOPPE AutoLock status
├── light.py              # Light entity platform
├── switch.py             # Switch entity platform
├── cover.py              # Cover entity platform
├── climate.py            # Climate entity platform (HeatArea)
├── sensor.py             # Sensor entity platform
├── binary_sensor.py      # Binary sensor entity platform
├── binary_sensor_descriptions.py # Moisture and HeatArea sensor definitions
├── event.py              # Event entity platform (rocker switches)
├── services.yaml         # HA service definitions
├── icons.json            # State-dependent entity icons and service icons
├── strings.json          # UI, service, and exception translation source
└── translations/
    ├── en.json           # English translations
    └── de.json           # German translations
tests/
├── conftest.py                  # Shared fixtures
├── ha_helpers.py                # Simulated MQTT boundary and flow helpers for real HA tests
├── fake_gateway.py              # In-process OPUS gateway serving the REST API over HTTP
├── test_rest_api.py             # REST client, error mapping and stream splitting
├── test_rest_commands.py        # REST acknowledgements, radio queue and outages
├── test_rest_devices.py         # REST device lists, resynchronisation and refresh
├── test_rest_requests.py        # REST health probe, reauth and request priorities
├── test_rest_stream.py          # REST event stream lifecycle and reconnects
├── test_rest_stream_backoff.py  # REST stream backoff with jitter, short-lived streams, connection generation
├── test_rest_telegrams.py       # REST telegrams, foreign commands and own echoes
├── test_rest_edge_cases.py      # REST coordinator edge cases
├── test_rest_lifecycle.py       # End-to-end REST lifecycle through Home Assistant
├── test_rest_protocol.py        # Pure REST document helpers
├── test_rest_reconciliation_feedback.py # REST feedback matching and status checks
├── test_rest_cover_movement.py  # REST cover travel estimates and echoes
├── test_rest_cover_stop.py      # REST cover stop and follow-up queries
├── test_rest_config_flow.py     # REST user, reauth and reconfigure flows with recovery
├── test_transport_selection.py  # Transport selection, switching, ACK 201 and dimmer ramping
├── test_rest_only_decoupling.py # REST-only entries without the MQTT integration, import boundaries
├── test_rest_client_hardening.py # REST client: HTTP vs. gateway status, no retries, no credentials in errors, library boundary
├── test_rest_client_coordinator.py # REST client hardening as seen by the coordinator (commands, health check, queue)
├── test_enocean_device.py       # Device model tests
├── test_coordinator_helpers.py  # Pure helper and command tests
├── test_coordinator_mqtt.py     # MQTT finalization tests
├── test_coordinator_parsing.py  # JSON telegrams, fragments, and late discovery
├── test_coordinator_edge_cases.py # Malformed payloads, timers, and availability logging
├── test_retained_device_answers.py # Retained response and entity state regressions
├── test_field_freshness.py      # Retained replays versus live fields per channel
├── test_ack_device_health.py    # ACK 201 deferred delivery and per-device health
├── test_late_responses.py       # Late answers without a request ID (limitation pinned)
├── test_late_answer_diagnostics.py # Late-answer counters in diagnostics (no traffic change)
├── test_coordinator_transport.py # Subscription, request, and recovery tests
├── test_transport_edge_cases.py # Request queue, subscription and probe edge cases
├── test_release_transport_safety.py # Interrupted startup and queue fault recovery
├── test_background_efficiency.py # Superseded background work and redundant writes
├── test_gateway_payload_compatibility.py # Reported IQ-DOT payload regression tests
├── test_state_ownership.py      # State ownership between reports and commands
├── test_strict_typing_boundaries.py # Typed wire-value boundaries
├── test_beta_entity_migration.py # Fork IDs, duplicate cleanup, and translations
├── test_binary_sensor_discovery.py # Repeated discovery and removal ordering
├── test_binary_sensor_descriptions.py # Stable sensor inventory and metadata
├── test_detector_support.py     # Sensor discovery and live MQTT updates
├── test_temperature_humidity_sensor.py # A5-04-03 readings, ranges, and HA entities
├── test_contact_sensor.py       # D5-00-01 states, MQTT updates, and reload
├── test_scalar_telemetry.py      # Immediate battery and signal-strength updates
├── test_window_handles.py        # Handle positions and protected AutoLock actions
├── test_handle_diagnostics.py    # Unlock request / mechanics fault diagnostic defaults
├── test_sensor_metadata.py       # SMS voltage diagnostic, whole-percent battery display
├── test_opus_motion_detector.py  # OPUS motion detector (F6-02-01 + A5-04-01)
├── test_upstream_backport.py     # New profiles under the fork guardrails (A1/A4/A5)
├── test_device_removal.py        # Child removal, rediscovery and request isolation
├── test_cover_stop.py           # Stop command encoding and follow-up queries
├── test_cover_movement.py       # Estimated travel, feedback, timing and cleanup
├── test_reconciliation_feedback.py # Valid feedback, channels, and command timing
├── test_event_entity.py         # Rocker switch event entity tests
├── test_event_device_class.py   # Rocker event device class (button), registry settings kept
├── test_event_lifecycle.py      # Transient button fields are never replayed
├── test_entities.py             # Entity state and Home Assistant service tests
├── test_platform_edge_cases.py  # Platform setup and entity edge cases
├── test_init.py                 # Entry setup, unloading, and service routing
├── test_service_actions.py      # Actions through the real Home Assistant service registry
├── test_diagnostics.py          # Diagnostic redaction tests
├── test_diagnostics_edge_cases.py # Diagnostics for partial and missing data
├── test_ha_lifecycle.py         # Real Home Assistant lifecycle and config flows
├── test_config_flow.py          # Config flow validation tests
├── test_config_flow_recovery.py # Recovery after every config flow error
├── test_discovery.py            # MQTT gateway discovery, duplicates and races
├── test_icon_translations.py    # icons.json states, fallbacks and semantics
├── test_repair_issues.py        # Duplicate-entity repair lifecycle
├── test_translations.py         # Translation, exception key and service consistency
├── test_quality_scale.py        # quality_scale.yaml schema and rule evidence
├── test_reference_baseline.py   # Fixed reference release, core commit and CI evidence
├── test_traffic_baseline.py     # MQTT publish baseline per endpoint and trigger
└── test_coverage_gate.py        # Per-module coverage gate
script/
├── check_coverage.py            # Per-module coverage gate used by CI
└── check_icons.py               # icons.json against the pinned MDI catalogue
```

### Testing

```bash
python -m pip install -r requirements_test_reference.txt  # fixed reference
ruff check .
ruff format --check .
python -m mypy
pytest -v --cov --cov-report=json:coverage.json
python script/check_coverage.py coverage.json
# Optional, needs a home-assistant/core checkout at the reference commit:
python script/check_icons.py <core>/pylint/plugins/pylint_home_assistant/generated/mdi_icons.py \
  custom_components/opus_greennet/icons.json
```

Use Python 3.14 for development. Tests cover device properties, telegram parsing,
ordered multi-channel commands, gateway errors, entity behavior, rocker events,
diagnostic redaction, and real Home Assistant configuration and lifecycle paths.
The v0.5.0 release validation covers 852 tests on Home Assistant 2026.8.2,
2026.9.4, and 2026.10.1, including interrupted startup and queue fault recovery.
The v0.6.0b0 candidate adds 24 A5-04-03 tests and 33 D5-00-01 tests, for 909 total
tests validated on Home Assistant 2026.8.2 and 2026.10.1.
Thirteen additional regression cases cover retained answers at startup and
reconnect, message ordering, missing state, and retained device-stream discovery.
The stable v0.6.0 release has 922 tests validated on Home Assistant 2026.9.4.
The v0.7.0b0 motion-detector beta adds 20 cases, for 942 tests on HA 2026.9.4.
Motion starts unknown until a valid telegram arrives after restart.
The v0.7.0b1 quality beta requires HA 2026.10.1 or newer, including for manual installation.
Its combined suite contains 1,315 tests, with strict typing and per-module coverage gates.
Users on older HA versions should keep stable v0.6.0 or upgrade HA first.
CI runs two test jobs. The fixed reference job installs
`requirements_test_reference.txt` (Home Assistant 2026.10.2) on a pinned runner
image, records Python, `pip freeze`, JUnit results, coverage and the MQTT
publish baseline as a workflow artifact, and validates metadata with hassfest
built from the matching home-assistant/core commit. The second job resolves the
latest stable Home Assistant release from PyPI on each run and tests that exact
version, recorded in the workflow summary; `hassfest@master` remains an
additional moving signal. MQTT publish counts are not radio transmission counts.
CI runs on pull requests, pushes to main, every Monday at 06:17 UTC, and manual
dispatch. Beta releases and older HA versions are excluded from the test matrix.
If a future stable release requires a newer Python version, CI fails explicitly
until the runner is updated rather than silently testing an older HA release. CI enforces
Ruff, dependency compatibility, strict mypy typing, Hassfest, a total coverage of
at least 97 %, more than 95 % for every integration module and 100 % for the
config flow (`script/check_coverage.py`). Lifecycle and race regression suites
run three more times. `quality_scale.yaml` is validated against the official
rule keys and schema by `tests/test_quality_scale.py`; it is a self-assessment,
not an official quality scale rating (see
[the evidence matrix](docs/quality/quality-scale-evidence.md)). The MQTT
transport is simulated and the REST transport runs against an in-process gateway
over real HTTP (`tests/fake_gateway.py`); physical-device verification remains
part of beta testing.

### Beta testing

Enable pre-release versions for this repository in HACS, select the beta, and
restart Home Assistant. Existing entity IDs and rocker event names are preserved.
Check physical controls, low brightness, both actuator channels, cover position
and tilt, climate activity, and rapid rocker presses. Also test a gateway outage
and reconnection, then reload the integration during a pending request. Report
your gateway firmware, device EEP, and the observed result with redacted
diagnostics.

## References

- [EnOcean over IP MQTT and REST Specification](https://www.enocean-alliance.org/ip/)
- [EnOcean Equipment Profiles (EEP)](https://www.enocean-alliance.org/eep/)
- [Home Assistant Developer Documentation](https://developers.home-assistant.io/)

## License

Released under the [MIT License](LICENSE).

<!-- badges -->
[hacs]: https://hacs.xyz
[hacs-shield]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge
[releases]: https://github.com/kegelmeier/ha-opus-greennet/releases
[release-shield]: https://img.shields.io/github/v/release/kegelmeier/ha-opus-greennet?style=for-the-badge
[license]: https://github.com/kegelmeier/ha-opus-greennet/blob/main/LICENSE
[license-shield]: https://img.shields.io/github/license/kegelmeier/ha-opus-greennet?style=for-the-badge
[ha-shield]: https://img.shields.io/badge/Home%20Assistant-2026.10.1%2B-41BDF5.svg?style=for-the-badge&logo=home-assistant&logoColor=white
[hacs-repo]: https://my.home-assistant.io/redirect/hacs_repository/?owner=kegelmeier&repository=ha-opus-greennet&category=integration
[hacs-repo-badge]: https://my.home-assistant.io/badges/hacs_repository.svg
[config-flow]: https://my.home-assistant.io/redirect/config_flow_start/?domain=opus_greennet
[config-flow-badge]: https://my.home-assistant.io/badges/config_flow_start.svg
