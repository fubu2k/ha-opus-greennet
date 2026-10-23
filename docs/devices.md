# Devices

How each device type appears in Home Assistant. For the list of supported
profiles see [Supported devices](../README.md#supported-devices).

- [Switches and dimmers (D2-01)](#switches-and-dimmers-d2-01)
- [Buttons and rocker switches](#buttons-and-rocker-switches)
- [Blinds and shutters (D2-05)](#blinds-and-shutters-d2-05)
- [Presence detectors (A5-07)](#presence-detectors-a5-07)
- [HOPPE window handles](#hoppe-window-handles)
- [Water sensors (F6-05-01)](#water-sensors-f6-05-01)
- [Single input contacts (D5-00-01)](#single-input-contacts-d5-00-01)
- [Sensor values](#sensor-values)
- [Removing or replacing a device](#removing-or-replacing-a-device)
- [Actions](#actions)

## Switches and dimmers (D2-01)

**Switch or dimmer, and channel count.** At setup the integration reads one
gateway profile per EEP combination (`GET /devices/<id>/profile`, gateway data
without a radio telegram):

- A profile that accepts `dimValue` makes a light, one that accepts `switch` a
  switch.
- A `channel` key in the profile gives the channel count.
- When no profile can be read, the EEP specification decides (see the table in
  the README). Devices paired later use a profile already read for the same
  EEPs, otherwise the table until the next reload. Diagnostics show the source
  per device.

Earlier releases derived type and channel count from the EEP number. A
single-channel actuator that was created as channel 0 keeps its entity ID and
settings. Entities of channels or of a platform the device does not have are
removed (with a log warning) only when the gateway profile confirmed the
outputs; update dashboards and automations that used them.

**Dimming.** Dimmers switch to the new brightness at once, like commands from
Apple Home. An action with a `transition` (any duration) uses the dimmer's own
configured fade instead; the requested duration itself cannot be passed to the
device.

**Local control.** D2-01 actuators that report local control get a diagnostic
*Local control* binary sensor, disabled by default.

## Buttons and rocker switches

Rocker switches (F6-02, F6-03) get one event entity with an event per button
and action: `buttonA0_pressed`, `buttonA0_released`, …,
`multipleButtons_released`, with the attributes `button` and `action`. F6-03
rockers also report `buttonC0` … `buttonDI`.

Many OPUS actuators also announce a rocker profile (F6-03-01) for their own
buttons. They get an event entity named *Buttons* with the unique ID suffix
`_buttons`, so it never collides with the switch, light or cover entity of the
same device.

All button event entities are meant for automations: they are enabled but
hidden by default, so they do not appear in automatically generated
dashboards. This applies to newly created entities; existing ones keep their
visibility.

## Blinds and shutters (D2-05)

**Position while moving.** Blinds report position and angle as `unknown` while
they move; Home Assistant keeps the last known value until the end report
arrives. Command targets of `unknown` are ignored.

**Estimated movement.** Covers with a positive configured `verticalMovementTime`
show `opening` / `closing` while travelling instead of jumping to the target:

- The estimate uses the distance from the last known position (a full travel
  when the start is unknown) plus five seconds of grace. It is an estimate,
  not a movement sensor; a wrong configured time or a stalled motor makes it
  inaccurate.
- A new target replaces the estimate. A numeric position report, Stop or a
  rejected command ends it immediately.
- Status checks run 10 and 20 seconds after the estimate ends. Example: for a
  25-second full travel the UI shows movement for up to 30 seconds and checks
  the status at 40 and 50 seconds.
- Without feedback, the target becomes the assumed position when the estimate
  ends. Covers without a configured travel time just show the reported state.

**Tilt.** Tilt controls are hidden when the gateway reports `rotationTime` as `0`
or `noRotation` (plain roller shutters). Without a valid rotation time, tilt
support follows the EEP.

**Stop** sends the profile's stop command and then checks the reported position
and, where supported, tilt. It does not assume a new position.

**Diagnostic controls** (D2-05-00 and D2-05-02):

| Entity | What it does |
|--------|--------------|
| **Blockage** switch | For automations such as wind protection or an open door. On sends `lockingMode = block`, off sends `unblock`. A D2-05-00 `alarm` counts as blocked; the reported mode is the `locking_mode` attribute. **Disabled by default** because it stops the blind. |
| **Reference run via top / bottom** buttons | Drive the blind to an end position first (`repositioning = firstUp` / `firstDown`) and then back to its last known position. This recalibrates the blind's position tracking. |
| **Last run time** sensor (every blind) | Seconds from a position command (from HA, another controller or a reference run) to the blind's report at the target. Attributes: `direction` (`up`, `down`, `reference_up`, `reference_down`), start and end position (100 = open) and `full_travel`, the value to compare with the configured `verticalMovementTime`. Runs ended by Stop, without an end report or only confirmed by a status query record no time. The last value is kept across Home Assistant restarts. |

Movement and rotation times always come from the device list (seconds). The
milliseconds of a D2-05-00 "Set parameters" command sent by another controller
are not taken over.

## Presence detectors (A5-07)

- `motionDetected = false` means the detector is *uncertain of occupancy*, not
  that the room is certainly empty; Home Assistant shows it as `off`.
- An illumination above the sensor range (`overRange`, above 1000 lx) is shown
  as the range maximum with the attribute `over_range: true`.
- The diagnostic *Sensor error* binary sensor is on while the detector reports
  an error code (251–255).
- A5-07-01 has no illumination sensor, so it gets no illuminance entity.
- The OPUS motion detector (F6-02-01 + A5-04-01) reports motion as
  `buttonAI` and no motion as `buttonA0`; it also gets temperature and
  humidity sensors.

## HOPPE window handles

All three handle profiles (D2-06-40, F6-10-00, D2-03-10) provide:

- a **Handle position** sensor (the main entity),
- diagnostic **Open**, **Tilted** and **Closed** binary sensors for automations
  (enabled and visible); for Cover Control Automation, assign Open and Tilted
  to its window-contact inputs,
- a diagnostic battery level from the device list.

`tilt` (D2-03-10) and `up` (F6-10-00) are normalized to `tilted`. Missing or
invalid positions, including the gateway's `unkown`, stay unknown. The sensors
report the handle position, not an independent measurement of the sash.

**D2-06-40 AutoLock.** The handle additionally reports its lock state, an unlock
request, a mechanics fault and a diagnostic **Lock bolt** binary sensor (on =
unlocked). The **AutoLock** lock entity sets the answer the gateway gives the
handle when its button is pressed and it asks to be unlocked
(`unlock = requested`, attribute `unlock_requested`):

| Lock entity | Stored answer | Handle |
|-------------|---------------|--------|
| *Unlocked* | `unlock = allowed` | Button lights green, handle can be turned |
| *Locked* | `unlock = notAllowed` | Button lights red, handle stays locked |

The gateway stores the answer as the handle's target state (status 202) and
uses it at every request, also while the handle sleeps. The entity shows the
stored answer, read from the device list at start-up; the attribute
`bolt_locked` repeats the reported lock bolt. Quick changes settle for 10
seconds (*locking* / *unlocking*) and only the last one is sent, because the
gateway queues every answer for the sleeping handle.

## Water sensors (F6-05-01)

The leak sensor sends only when its state changes, and the gateway lists no
state for it. Until it has reported water, the binary sensor shows *dry*
instead of *unknown*; a gateway outage still shows *unavailable*.

## Single input contacts (D5-00-01)

A *Contact* binary sensor with the opening device class: `open` / `closed`
appear as `on` / `off`. Missing or unrecognized values stay `unknown`. No
battery percentage is invented when the gateway publishes none.

## Sensor values

- Measurements reported as `notAvailable` become unknown. Unknown values are
  never read as "no alarm" or "closed handle".
- Lights and switches stay unknown until their state is reported or a command
  is accepted.
- Battery percentages accept an optional `%` suffix (the gateway reports for
  example `96%`) and must be within 0–100.
- Battery level and signal strength come from the device list and are
  refreshed every 30 minutes; the signal strength sensor is disabled by
  default.
- Climate activity uses actuator feedback where available; enabling a heating
  zone does not by itself mean it is heating.

## Removing or replacing a device

Open **Settings → Devices & services → Opus GreenNet Bridge → Devices**, select
the device and choose **Delete**. The gateway itself cannot be removed this
way; remove the integration entry instead.

- This removes only the Home Assistant device and its entities. Nothing is
  unpaired or deleted on the gateway (see the [roadmap](../README.md#roadmap)).
- If the gateway still lists the device, it comes back after a reload or
  reconnection. Quiet devices are never deleted automatically, because the
  gateway cannot confirm that a device is gone.
- When replacing a device, first remove the old one in the OPUS app, then
  delete it in Home Assistant. Automations that reference the old entities are
  not migrated.

## Actions

Available in **Developer Tools → Actions**. `device_id` is the EURID of the
device (fictional example: `AABB1122`). Select a gateway with
`config_entry_id` when more than one gateway is set up.

| Action | Parameters | Description |
|--------|------------|-------------|
| `opus_greennet.set_cover_position` | `device_id`, `position` (100 = open), optional `repositioning` (`direct`, `first_up`, `first_down`), `channel`, `config_entry_id` | Move a D2-05 blind, optionally after a reference run |
| `opus_greennet.get_device_configuration` | `device_id` | Admin only. Read the configuration stored on a device (ReCom) |
| `opus_greennet.set_device_configuration` | `device_id`, `configuration` | Admin only. Write configuration to a device (ReCom) |
| `opus_greennet.get_device_parameters` | `device_id` | Admin only. Read the parameters a device supports (ReCom) |
| `opus_greennet.reload_entry` | optional `config_entry_id` | Admin only. Reload the integration without restarting Home Assistant |

The read actions return their data directly as the action response. ReCom
requests travel over the radio and may take several seconds (timeout 45 s).
