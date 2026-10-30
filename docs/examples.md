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

*Last run time* is a sensor with the attributes `direction`, `full_travel` (true when the run
covered the whole height) and the start and end position. Its value is the time from the
command to the blind's end report, not the mechanical travel time, and can be longer than the
configured `verticalMovementTime`. On the blinds measured so far it follows the blind's own
timer, which is set by the stored `verticalMovementTime`, so it does not reveal a stored time
that is wrong. Compare runs of the same kind (`full_travel: true`) with each other, for example
to notice a blind whose reports change:

```yaml
type: entities
entities:
  - entity: sensor.bedroom_blind_last_run_time
  - type: attribute
    entity: sensor.bedroom_blind_last_run_time
    attribute: full_travel
```

### Measure the real travel time of a blind

The stored *Vertical movement time* should equal the time the blind really needs (see the
calibration tip in [Troubleshooting](troubleshooting.md)). The sensors cannot show it, so
measure it with a stopwatch built in Home Assistant: the script below starts a full run, you
tap a STOP button as soon as the blind has physically stopped, and the elapsed time is written
to a text helper and the logbook. A new start replaces a waiting measurement; after 90 s
without a tap the script stops the blind and logs a timeout. Your reaction time is part of the
result, so repeat the measurement and use the typical value. Enter it in the blind's
*Vertical movement time* number (it accepts decimals).

This example comes from a user who measured 24 blinds this way (anonymised: entity IDs and room
names are generic). Adjust the names to your installation.

Helpers (create them in the UI or in YAML):

```yaml
input_button:
  blind_measure_stop:
    name: Blind measurement stop

input_text:
  blind_measure_result:
    name: Blind measurement result
    max: 255
```

Script:

```yaml
script:
  blind_measure_travel_time:
    alias: Measure blind travel time (tap)
    mode: restart
    fields:
      cover:
        name: Blind
        required: true
        selector:
          entity:
            domain: cover
      direction:
        name: Direction
        required: true
        selector:
          select:
            options: [open, close]
    sequence:
      - if:
          - condition: template
            value_template: "{{ direction == 'open' }}"
        then:
          - action: cover.open_cover
            target:
              entity_id: "{{ cover }}"
        else:
          - action: cover.close_cover
            target:
              entity_id: "{{ cover }}"
      - variables:
          t_start: "{{ now().timestamp() }}"
      - wait_for_trigger:
          - trigger: state
            entity_id: input_button.blind_measure_stop
        timeout:
          seconds: 90
        continue_on_timeout: true
      - if:
          - condition: template
            value_template: "{{ not wait.completed }}"
        then:
          - action: cover.stop_cover
            target:
              entity_id: "{{ cover }}"
          - action: input_text.set_value
            target:
              entity_id: input_text.blind_measure_result
            data:
              value: "{{ cover }} {{ direction }}: timeout (no stop tapped)"
          - stop: Timeout
      - variables:
          duration: "{{ (now().timestamp() - t_start) | round(1) }}"
      - action: input_text.set_value
        target:
          entity_id: input_text.blind_measure_result
        data:
          value: "{{ cover }} {{ direction }}: {{ duration }} s"
      - action: logbook.log
        data:
          name: Blind measurement
          entity_id: "{{ cover }}"
          message: "{{ cover }} {{ direction }}: {{ duration }} s"
```

Dashboard (one blind shown; repeat the blind section for every blind). Start the blind at the
opposite end, tap OPEN or CLOSE, then tap STOP when it has stopped:

```yaml
title: Measurement
views:
  - title: Measurement
    path: measurement
    type: sections
    max_columns: 3
    sections:
      - type: grid
        cards:
          - type: heading
            heading: Last result
            icon: mdi:timer-outline
          - type: tile
            entity: input_text.blind_measure_result
            name: Result
            grid_options: {columns: 12, rows: 1}
          - type: markdown
            content: >-
              Start the blind at the opposite end: tap **OPEN** or **CLOSE**,
              then tap **STOP** as soon as the blind has stopped. After 90 s
              without a tap the measurement is aborted.
            grid_options: {columns: 12}
      - type: grid
        cards:
          - type: heading
            heading: Blind A
            icon: mdi:blinds
          - type: tile
            entity: cover.blind_a
            name: OPEN (full travel)
            icon: mdi:arrow-up-bold
            color: blue
            vertical: false
            tap_action:
              action: perform-action
              perform_action: script.blind_measure_travel_time
              data: {cover: cover.blind_a, direction: open}
            grid_options: {columns: 6, rows: 1}
          - type: tile
            entity: cover.blind_a
            name: CLOSE (full travel)
            icon: mdi:arrow-down-bold
            color: blue
            vertical: false
            tap_action:
              action: perform-action
              perform_action: script.blind_measure_travel_time
              data: {cover: cover.blind_a, direction: close}
            grid_options: {columns: 6, rows: 1}
          - type: tile
            entity: input_button.blind_measure_stop
            name: STOP - tap as soon as the blind has stopped
            icon: mdi:stop-circle
            color: red
            tap_action:
              action: perform-action
              perform_action: input_button.press
              target: {entity_id: input_button.blind_measure_stop}
            grid_options: {columns: 12, rows: 2}
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
  detectors become binary sensors for notifications. During a gateway outage
  they show *unavailable*, so a lost gateway does not look like "no alarm".
- **Diagnosing a radio installation.** Signal strength (disabled by default),
  battery level and the redacted diagnostics download show devices with weak signal
  or low battery. See
  [Troubleshooting](troubleshooting.md).
- **Several gateways.** Each gateway is its own entry. Actions take a
  `config_entry_id` when more than one gateway is loaded.
