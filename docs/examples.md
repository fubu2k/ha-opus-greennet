# Examples and use cases

Entity IDs, device names and EURIDs below are fictional. Replace them with the
ones of your installation (**Settings → Devices & services → Opus GreenNet
Bridge**). How each device type behaves is described in
[Devices](devices.md); how updates and acknowledgements work in
[How it works](how-it-works.md).

- [Examples](#examples)
- [Use cases](#use-cases)

## Examples

### Run an automation from a rocker switch

Rocker switches and the buttons of OPUS actuators are event entities. Their
event types are `<button>_pressed` and `<button>_released`, for example
`buttonA0_pressed`; the attributes `button` and `action` carry the same
information. Every press is its own event, also when the same button is
pressed twice in a row. Event entities are enabled but hidden by default, so
they do not show up in generated dashboards.

```yaml
automation:
  - alias: Hallway rocker, top button, toggle the hall light
    triggers:
      - trigger: state
        entity_id: event.hallway_rocker
    conditions:
      - condition: template
        value_template: >-
          {{ trigger.to_state.attributes.event_type == 'buttonA0_pressed' }}
    actions:
      - action: light.toggle
        target:
          entity_id: light.hall
```

### Move a blind to a position, with and without a reference run

`position` uses the Home Assistant scale (100 = open, 0 = closed). A direct
move needs no reference run. `first_up` or `first_down` drives to the end
position first and recalibrates the blind's position tracking; it takes longer than a direct move.

```yaml
# Direct move
action: opus_greennet.set_cover_position
data:
  device_id: AABB1122   # EURID of the blind
  position: 30

# Recalibrate first: drive to the top end position, then to 30 %
action: opus_greennet.set_cover_position
data:
  device_id: AABB1122
  position: 30
  repositioning: first_up
```

Add `channel` for a multi-channel actuator and `config_entry_id` when more than
one gateway is set up. The action is acknowledged by the gateway, not by the
blind: see [Commands and acknowledgements](how-it-works.md#commands-and-acknowledgements).

### Notify when a window handle is left tilted

The *Handle position* sensor of a HOPPE window handle has the states `open`,
`tilted` and `closed`. A missing or invalid position stays `unknown`, so the
automation does not fire on gaps.

```yaml
automation:
  - alias: Living room window tilted for 30 minutes
    triggers:
      - trigger: state
        entity_id: sensor.living_room_window_handle_position
        to: tilted
        for: "00:30:00"
    actions:
      - action: notify.notify
        data:
          message: The living room window has been tilted for 30 minutes.
```

### Alert on a water leak

The leak sensor reports only when its state changes. Until it has reported
water the binary sensor shows *off* (dry), and it shows *unavailable* during a
gateway outage, so a loss of the gateway does not look like a dry floor.

```yaml
automation:
  - alias: Water leak in the basement
    triggers:
      - trigger: state
        entity_id: binary_sensor.basement_water_sensor_water_leak
        to: "on"
    actions:
      - action: notify.notify
        data:
          title: Water leak
          message: The basement water sensor reports a leak.
```

### Show the last run time of a blind

*Last run time* is a sensor with the attributes `direction`, `full_travel` and
the start and end position. Compare `full_travel` with the configured
`verticalMovementTime` to see whether the estimated travel matches the blind:

```yaml
type: entities
entities:
  - entity: sensor.bedroom_blind_last_run_time
  - type: attribute
    entity: sensor.bedroom_blind_last_run_time
    attribute: full_travel
```

### Read the configuration stored on a device

The read actions return their data as the action response (Admin only; see
[Actions](devices.md#actions)). In a script:

```yaml
action: opus_greennet.get_device_configuration
data:
  device_id: AABB1122
response_variable: stored
```

ReCom requests travel over the radio and can take several seconds (timeout
45 s). `set_device_configuration` writes to the device and is never retried
automatically.

### See which wall switches control a device

```yaml
action: opus_greennet.get_device_link_tables
data:
  device_id: AABB1122
response_variable: links
```

`links.linkTables.inbound` lists the wall switches the device listens to,
`outbound` the devices it reports to; the gateway itself appears as `gateway`.
This action only reads. Link tables are changed with the OPUS app.

## Use cases

- **Local, cloud-free control of an OPUS installation.** Home Assistant talks to
  the gateway in your network over its REST API; no broker and no cloud
  account. State changes arrive as pushed telegrams, typically within a few
  hundredths of a second, including local switch operation and commands from
  other controllers such as HomeKit.
- **Shutter and blind automation.** Position, tilt and the estimated movement
  appear as a cover. Sun protection or wind protection automations use
  `cover.set_cover_position`, `opus_greennet.set_cover_position` for reference
  runs, and the *Blockage* switch (disabled by default because it stops the
  blind).
- **Window supervision and heating.** HOPPE handles report open, tilted and
  closed. The diagnostic *Open* and *Tilted* binary sensors can be assigned to
  the window-contact inputs of a heating or Cover Control Automation setup.
- **Physical buttons as automation triggers.** Wall rockers and the rocker
  buttons of actuators trigger scenes and other automations in Home Assistant.
- **Safety and presence.** Water leak sensors, smoke alarms and presence
  detectors become binary sensors for notifications, with unknown values never
  taken for "no alarm".
- **Diagnosing a radio installation.** Signal strength (disabled by default),
  battery level and the redacted diagnostics download show devices with weak signal
  or low battery. See
  [Troubleshooting](troubleshooting.md).
- **Several gateways.** Each gateway is its own entry. Actions take a
  `config_entry_id` when more than one gateway is loaded.
