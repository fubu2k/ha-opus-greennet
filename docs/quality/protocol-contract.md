# OPUS/EnOcean MQTT Protocol Contract (current behaviour)

Baseline: `3e52fce31e7a2d503eef4820f4c706ebcfc751ab` (`main`), recorded in phase 1.
This document describes the **actual** behaviour of the code. It is the
reference for all following phases: everything listed here stays unchanged
unless a phase explicitly permits a proven bug fix.

Vendor protocol source: `opus_mqtt_services_reference.md`. Where the code goes
beyond that reference (gateway alias keys, Mosquitto bridge topic), this is noted.

Verification legend: **[Code]** read from source, **[Test]** covered by existing
tests, **[Ref]** per vendor reference, **[unverified]** supported neither by
hardware observation nor by the reference.

## 1. Topics

Base: `EnOcean/<EAG_ID>/…` (`TOPIC_BASE = "EnOcean"`). `<EAG_ID>` is 8 hex
characters, normalised to upper case by the config flow.

### 1.1 Coordinator subscriptions

| Group | Topic filter | QoS | Handler | Note |
|-------|--------------|-----|---------|------|
| Control (setup phase 1) | `opus_greennet/<EAG_ID>/bridge/status` | 1 | `_handle_bridge_status` | Mosquitto bridge notification, not an OPUS topic **[Code]** |
| Control | `EnOcean/<EAG_ID>/getAnswer/config/system/uptime` | 1 | `_handle_system_uptime` | also a request manager answer route |
| Control | `EnOcean/<EAG_ID>/getAnswer/config/system/info` | 1 | `_handle_system_info` | optional; not every firmware answers |
| Control | `EnOcean/<EAG_ID>/putAnswer/devices/+/state` | 1 | `_handle_put_answer_state` | also an answer route |
| Stream (setup phase 2, one at a time with SUBACK) | `EnOcean/<EAG_ID>/getAnswer/devices/#` | 1 | `_handle_get_answer_devices` | discovery answers |
| Stream | `EnOcean/<EAG_ID>/stream/devices/#` | 0 | `_handle_device_property_message` | startup snapshot, flattened |
| Stream | `EnOcean/<EAG_ID>/stream/device/#` | 0 | `_handle_device_stream_message` | live deltas |
| Stream | `EnOcean/<EAG_ID>/stream/telegram/#` | 0 | `_handle_telegram_property_message` | radio telegrams |

SUBACKs of the stream group are advisory only (`required=False`); a missing
SUBACK does not fail the health probe. **[Code][Test]**

### 1.1a Discovery subscription (phase 9, Home Assistant MQTT integration)

`manifest.json` declares `"mqtt": ["EnOcean/+/stream/devices/+/friendlyId"]`.
The Home Assistant MQTT integration (not the coordinator) subscribes to this
filter and starts a config flow per new topic/payload pair. `async_step_mqtt`
accepts only `EnOcean/<EAG>/stream/devices/<DEV>/friendlyId` with a non-empty
payload and an EAG segment already in canonical upper-case form
(`discovery.py`); everything else aborts with `invalid_discovery_info`.
Retained and live messages are equal candidates; neither is a health signal.
Receipt publishes nothing. Known, disabled and ignored gateways abort with
`already_configured` before a unique ID is set (no discovery-key update, no
reload of a retrying entry). Confirmation runs the existing
`async_probe_gateway` uptime request once, exactly as manual setup. The payload
(device name) is neither stored nor logged. **[Code][Test]**

Temporary subscriptions (QoS 1) are created by `MQTTRequestManager` only for
answer topics not covered by a registered route (profile, configuration,
parameters). Registered routes: the uptime answer and `putAnswer/devices/+/state`. **[Code]**

### 1.2 Published topics

| Topic | Payload | QoS / retain | Trigger |
|-------|---------|--------------|---------|
| `…/get/config/system/uptime` | `""` | 1 / false | setup, health tick, reconnect, config flow (manual submit or discovery confirmation) |
| `…/get/devices` | `""` | 1 / false | resync only |
| `…/get/config/system/info` | `""` | 1 / false | resync only, answer not awaited |
| `…/put/devices/<DEV>/state` | `{"state": {"functions": [...]}}` | 1 / false | commands and status queries |
| `…/get/devices/<DEV>/profile` | `""` | 1 / false | `async_get_device_profile` (currently no caller in the integration code) |
| `…/get/devices/<DEV>/configuration` | `""` | 1 / false | service `get_device_configuration` |
| `…/put/devices/<DEV>/configuration` | `json.dumps(config)` | 1 / false | service `set_device_configuration` |
| `…/get/devices/<DEV>/parameters` | `""` | 1 / false | service `get_device_parameters` |

Unused constants in `const.py`: `TOPIC_STREAM_TELEGRAM`, `TOPIC_STREAM_DEVICE`,
`TOPIC_GET_/PUT_(ANSWER_)LINK_TABLES`, `TOPIC_SUB_TELEGRAM_FROM`,
`TOPIC_SUB_TELEGRAM_TO`, `TOPIC_SUB_DEVICE`, `TOPIC_SUB_DEVICES`,
`TOPIC_SUB_GET_ANSWER`. **[Code]**

## 2. Answers and status

- `decode_response`: the payload must be a non-empty JSON object. If `header`
  is present or `require_status=True`, `header.httpStatus` must be an integer
  (no `bool`, no non-integral `float`) equal to **200 or 201** (201 = SoftSmartAck
  per reference). **[Code][Test][Ref]**
- State commands (`putAnswer/devices/<DEV>/state`) distinguish the two accepted
  statuses since phase 11: `200` = `CommandDelivery.SENT`, `201` =
  `CommandDelivery.DEFERRED` (section 7.1). Both clear `last_command_error`;
  neither is device feedback. **[Code][Test][Ref]**
- Uptime: `systemUptimeResponse.uptime` must be `str`, `int` or `float` (no `bool`),
  finite and ≥ 0; stored as `str(value)`. **[Code][Test]**
- Bridge status: only payloads `"0"` and `"1"` are evaluated; anything else is ignored. **[Code][Test]**
- `getAnswer/devices`: accepts a list, `{"devices": [...]}`, a single object and
  entries shaped `{"device": {...}}`. Sub-paths more than one level below
  `getAnswer/devices/` (profile, configuration) are ignored. **[Code][Test]**

## 3. Command payloads (wire values, immutable)

Format: `json.dumps({"state": {"functions": functions}})`; values are always strings.

| Action | functions | Source |
|--------|-----------|--------|
| Switch on/off | `[{"key": "switch", "value": "on"/"off"}]` | `async_turn_on/off` |
| Dimmer on without brightness | `[{"key": "dimValue", "value": "100"}]` | reference 5.3 |
| Dimmer off | `[{"key": "dimValue", "value": "0"}]` | reference 5.3 |
| Dimmer brightness | `[{"key": "dimValue", "value": str(max(1, round(brightness_to_value((1, 100), b))))}]`; HA brightness 0 → off | `light.py` |
| Cover open / close | `position` `"0"` / `"100"` | OPUS 0 = open, 100 = closed |
| Cover position | `position` `str(100 - ha_position)` | **cover inversion** |
| Cover tilt | `angle` `str(ha_tilt)` (not inverted) | `cover.py` |
| Cover STOP | `[{"key": "stop", "value": "true"}]` | **STOP payload**, never `position: "stop"` **[Ref][Test]** |
| Status query | `[{"key": "query", "value": "status"}]` | reconciliation, climate query |
| HeatArea setpoint | `[{"key": "temperatureSetpoint", "value": str(temperature)}]` | no channel selector |
| HeatArea off | `[{"key": "heaterMode", "value": "off"}]` | |
| HeatArea on | D1-4B-06: `"on"`; D1-4B-05 and D1-4B-07: `"heating"` | **proprietary HeatArea profiles** |

### 3.1 Channel selector (channel zero)

`_with_channel_if_needed` prepends `{"key": "channel", "value": str(channel)}`
when `channel > 0` **or** `channel_count > 1`. Multi-channel devices therefore
**send channel 0 explicitly**; single-channel devices get no selector. Climate
commands never send a selector. **[Code][Test]**

Inbound: without a selector the default channel is 0. A malformed selector
(negative, non-integral, `bool`) does **not** fall back to channel 0; functions
without their own valid channel are then dropped (`_parse_channel_id`). **[Code][Test]**

## 4. Inbound values (validation in `EnOceanDevice.update_from_telegram`)

| Key | Valid values | On `notAvailable` | On invalid value |
|-----|--------------|-------------------|------------------|
| `switch`, `localControl` | `on` / `off` | ignored | ignored |
| `dimValue` | integer 0–100; sets `is_on = value > 0` | ignored | ignored |
| `position`, `angle` | integer 0–100 | ignored | ignored |
| `verticalMovementTime` | ≥ 0 | ignored | ignored |
| `rotationTime` | `noRotation` → 0; number ≥ 0 | ignored | ignored |
| `energy`, `power` | finite number | ignored | ignored |
| `liquidDetected`, `windowOpen`, `summerMode` | bool or `true`/`false` | ignored | ignored |
| `smokeAlarm`, RWM `alarm` | `on`/`off`, `alarm`/`idle`, bool | – | **sets `None`** |
| `batteryLow` | `low`/`ok`, bool | – | **sets `None`** |
| `batteryLevel` | 0–100, optional `%` suffix | **sets `None`** | ignored |
| `motionDetector`, SMS `motionDetected` | `detected`/`noMotion`, bool | – | **sets `None`** |
| `illuminance`, SMS `illumination`, `supplyVoltage` | ≥ 0 | **sets `None`** | ignored |
| `handleState`, handle `handle` | `open`/`closed`/`tilted`, `tilt` → `tilted` | – | **sets `None`** |
| `lock` | `locked`/`unlocked` | – | **sets `None`** |
| `unlockRequest`, AutoLock `unlock` | `requested`/`notRequested`, bool | – | **sets `None`** |
| `mechanicsFault`, AutoLock `mechanics` | `error`/`ok`, bool | – | **sets `None`** |
| `temperature`, `temperatureSetpoint` | 0–40 (HeatArea, A5-04-01) | **sets `None`** | ignored |
| `temperature` on A5-04-02/03 | −20–60 | **sets `None`** | ignored |
| `contact` (D5-00-01 only) | `open` → on / `closed` → off | – | **sets `None`** |
| `humidity` | 0–100 | **sets `None`** | ignored |
| `feedTemperature` | 0–80 | **sets `None`** | ignored |
| `energyConsumption` | 0–10 | **sets `None`** | ignored |
| `heaterMode` | `heating`, `on`, `off`, `autoOff`, `configIncomplete`, `error` | ignored | ignored |
| `thermalMode` | `heating` / `cooling` | ignored | ignored |
| `powerState` | `active` / `inactive` | ignored | ignored |
| `temperatureOrigin` | `external` / `internal` | ignored | ignored |
| `actuatorDeactivated` | `info` / `reset` | ignored | ignored |
| `actuatorLowBattery` | `warning` / `reset` | ignored | ignored |
| `actuatorNotResponding` | `warning` / `error` / `reset` | ignored | ignored |
| `missingTemperature` | `info` / `warning` / `error` / `reset` | ignored | ignored |
| `circuitInUse` | `error` / `reset` | ignored | ignored |
| `buttonA0`, `buttonAI`, `buttonB0`, `buttonBI`, `multipleButtons` | `pressed` / `released` | – | ignored |
| OPUS motion detector (F6-02-01 + A5-04-01): `buttonAI` / `buttonA0` | `pressed` → motion on / off; `released` keeps the state | – | ignored |

Profile-bound aliases (IQ-DOT 1.21.30): `alarm` only for F6-05-02,
`motionDetected`/`illumination` only for A5-07-01/03, `handle` only for handle
profiles, `unlock`/`mechanics` only for D2-06-40. **[Code][Test]**

Fast path (`stream/device/<DEV>/batteryLevel|dbm`): no debounce; `dbm` as an
integer, `notAvailable` → `None`; invalid values are dropped; an older buffered
scalar is discarded so it cannot overwrite the newer one. **[Code][Test]**

RWM (F6-05-02): value-only slots in `transmitModes` are resolved as `smokeAlarm`
(index 0) and `batteryLow` (index 1). **[Code][Test]**

## 5. Telegrams (`stream/telegram`)

- Flattened: `…/telegram/<DEV>/from/functions/<i>/key|value`, likewise `to/…`,
  or flat with a `direction` field.
- Complete JSON: `…/telegram/<DEV>`, `…/from`, `…/to`; optionally wrapped in
  `telegram`; `state` is flattened.
- A frame ends after 0.15 s of silence, on a direction change, or when a path
  repeats while a complete pair already exists (fast press/release is preserved).
- `from` with `direction = "to"` is discarded.
- `to` (outbound) is discarded while a local command awaits its ACK
  (`_command_waiters`); otherwise it is applied optimistically (bridge-native or
  HomeKit commands), restricted to `KNOWN_STATE_KEYS`, `channel` and `stop`, and
  reconciliation is scheduled afterwards. Since phase 11 a matching value also
  clears the deferred marker of a SoftSmartAck command (section 7.1).
- Unknown device with a telegram: a placeholder device is created; a discovery
  signal is only sent if an `entity_type` can be determined. **[Code][Test]**

## 6. Retain behaviour (existing logic, frozen by A4)

| Input | Retained message |
|-------|------------------|
| `stream/devices/#` | processed (snapshot, discovery); raw debug log suppressed; since phase 10 a state value for a field already known in the armed session is discarded (6.1) |
| `stream/device/#` | processed; raw debug log suppressed; latency log only for live messages; phase 10 rule 6.1 as for `stream/devices` |
| `stream/telegram/#` | **discarded** (no events, no state) |
| `getAnswer/devices/#` | **discarded** |
| `putAnswer/devices/+/state` | routed to the request manager (which ignores retained), no state |
| Request answers in general | `handle_response` ignores retained |
| Uptime answer | routed; `gateway_uptime` is updated from the payload (display only) |
| System info, bridge status | evaluated without a retain check |

Additional existing freshness rule: on rediscovery of a known device, the
snapshot state is only applied if the last live update is not newer than
`_resync_requested_at`; otherwise only `rotationTime`/`channel` are applied.
Since phase 10, "live" means a non-retained message: an applied retained replay
keeps `last_update_received_monotonic` unchanged and is reported as
`<source> (retained)` in `last_update_source`, so a replay arriving after the
resync request no longer suppresses the requested snapshot answer. A delta
window (`_device_stream_data`) opened by a retained message counts as retained
until a live message joins it (`_device_stream_replay_windows`).
Button keys from `stream/devices` are never treated as occurrences. **[Code][Test]**

### 6.1 Field-level freshness of retained replays (phase 10)

Basis: an established subscription receives every publication with retain=0;
retain=1 is only set for messages sent because a subscription was made
(MQTT 3.1.1 [MQTT-3.3.1-8/9]). Home Assistant re-subscribes each wildcard one
at a time and exact topics afterwards (`_async_perform_subscriptions`, HA
2026.10.2), and keeps every subscription until disconnect. **[Ref][Code]**

- **Session.** Armed (`_replay_guard_armed`) at the end of a successful
  `_async_refresh_gateway` health probe, after its uptime answer and before the
  optional `get/devices` request, and only if all four stream wildcards are in
  `_stream_topics`. Disarmed and cleared by `_cancel_background_work` (MQTT
  disconnect, bridge `0`, unload). A failed probe does not disarm or arm.
- **Provenance.** `_live_fields[device] = {(channel, key)}`, recorded only while
  armed, from: non-retained `stream/devices` and `stream/device` value
  messages (field resolved at receipt), fast-path `batteryLevel`/`dbm`,
  applied telegram functions (`from` and optimistic `to`, without estimated
  cover positions), and non-retained `getAnswer/devices` answers. No wire
  timestamps or sequence numbers are used. **[Code][Test]**
- **Field resolution.** Fast-path scalars → `(0, key)`; named
  `states/<key>` → `(0, key)` for `KNOWN_STATE_KEYS` only; indexed
  `states|transmitModes/<n>/value` → key from the cached model or the open
  delta window, channel as in `_indexed_changed_functions` (RWM value-only
  slots resolved for F6-05-02). Metadata, `key`/`channel` paths, buttons and
  configuration parameters are never resolved and therefore never discarded.
- **Rule.** A retained value whose resolved field is in `_live_fields` is
  neither applied nor written to `_device_data`. Everything else is processed
  exactly as in the table above: first snapshots, other fields, other channels,
  devices without provenance, replays before arming and after an interruption.
- **Feedback parity (A1).** A discarded replay is still passed to
  `_record_confirmed_feedback` (known devices only), so pending reconciliation is
  cancelled exactly as before phase 10 and no additional query is published.
- **Cleanup.** `async_forget_device` removes the device's provenance; size is
  bounded by devices × reported fields and reset on every interruption.
- **Residual risk [unverified].** A stream-wildcard SUBACK that times out
  during setup phase 2 still arms the session; a Mosquitto bridge reconnect
  forwards the gateway's retained topics to Home Assistant as live messages,
  which this rule cannot distinguish. Hardware ordering was not observed.

## 7. Availability, health and timing (frozen by A1–A3)

- `coordinator.available` see `implementation-plan.md` (A2); every entity mirrors it.
- Health tick every 60 s, only while MQTT is connected and the bridge is not
  reported as disconnected; `resync = not _gateway_available`.
- Reconnect (MQTT or bridge): refresh; a full snapshot is requested at most once
  per 60 s, and always while setup phase 2 is incomplete.
- Disconnect / bridge down: availability `False`, pending requests cancelled,
  background work discarded (telegram and delta buffers, reconciliation timers,
  cover estimates, tasks).
- Health probe failure (`HomeAssistantError`, `TimeoutError`): availability
  `False`, pending requests cancelled with `gateway_unavailable`.
- A command timeout raises `request_timeout` for that action only; no retry,
  no availability change.
- Debounce times: discovery 2 s after the last message, delta finalisation 0.02 s,
  telegram finalisation 0.15 s.
- Reconciliation after a command: 5 s and 20 s; covers with a movement estimate:
  remaining travel incl. 5 s grace, then +10 s and +20 s. Only after ACK `200`;
  after ACK `201` see section 7.1.

### 7.1 ACK 201 and explicit device health (phase 11)

States of a command, from intent to confirmation:

| State | Source | Shown in Home Assistant |
|-------|--------|-------------------------|
| desired / estimated | ACK `200` (`SENT`): the gateway transmitted the telegram | estimate applied by light, switch and cover entities (unchanged), travel estimate for covers, then the 5/20 s checks |
| accepted / deferred | ACK `201` (`DEFERRED`, SoftSmartAck): the gateway queued the telegram | last reported state kept; no estimate, no travel estimate; value listed in diagnostics under `deferred_commands` |
| delivered | outbound telegram `stream/telegram/<DEV>/to` with the same value | existing bridge-native path: value applied optimistically, then the 5/20 s checks |
| device-confirmed | live device report (`stream/telegram/.../from`, `stream/device`, live `stream/devices`) for the field and channel | reported value; deferred marker cleared if the value matches |

- **No query for a deferred command (A1).** `_handle_deferred_command`
  schedules no status check for the command's fields. A status query would be
  deferred as well and could not confirm a telegram that has not been sent.
  Per command, at most the two existing checks follow, and only once the
  gateway transmits (outbound telegram). **[Code][Test]**
- **Cover travel.** A movement estimate started before publish is cleared on
  `201`; fields of earlier accepted commands whose timers that start had
  stopped get their two checks again (no additional field, no extra slot).
  A deferred Stop schedules no final-position check. **[Code][Test]**
- **Markers.** `EnOceanDevice.deferred_commands[channel][key] = value` for
  confirmable keys (`switch`, `dimValue`, `position`, `angle`,
  `temperatureSetpoint`, `heaterMode`) not yet reported during the ACK wait,
  and for `stop`. Cleared by a matching live report (numeric keys compared
  numerically), a matching outbound telegram, a newer `200` command for the
  same key and channel, or device removal. Retained values never clear a
  marker. A report with another value keeps it. Markers are diagnostic only
  and never become an assumed state. **[Code][Test]**
- **Residual risk [unverified].** OPUS answers carry no request identifier.
  Whether the gateway discards a queued telegram when a newer command for the
  same key is transmitted, and how long it keeps one, is not documented and
  was not observed on hardware. An outbound telegram arriving while another
  local command awaits its ACK is still discarded (section 5), so its marker
  stays until a device report matches.
- **Device health.** Profile-specific warnings and errors
  (`actuatorNotResponding`, `missingTemperature`, `circuitInUse`,
  `actuatorLowBattery`, `actuatorDeactivated`, `heaterMode: error`) are shown
  on their own entities only. They never change `coordinator.available` or any
  entity's availability, and they publish nothing. A rejected or timed-out
  command sets `last_command_error` for that device only. No silence-based
  offline rule exists (no documented heartbeat contract). **[Code][Test]**

## 8. Entity inventory and identities (frozen by A5)

Device identifier: `(opus_greennet, "<EAG>_<DEV>")`; gateway: `(opus_greennet, "<EAG>")`.

| Platform | Condition | Unique ID | Default |
|----------|-----------|-----------|---------|
| light | `entity_type == "light"`, per channel | `<EAG>_<DEV>`, with `channel_count > 1`: `<EAG>_<DEV>_ch<n>` | enabled |
| switch | `entity_type == "switch"`, per channel | as light | enabled |
| cover | `entity_type == "cover"`, per channel | as light | enabled |
| climate | `entity_type == "climate"` | `<EAG>_<DEV>` | enabled |
| event | `entity_type == "event"` (F6-02-xx, F6-03-xx) | `<EAG>_<DEV>` | enabled |
| lock | D2-06-40 | `<EAG>_<DEV>_autolock` (read-only) | enabled |
| sensor | HeatArea: `humidity`; D1-4B-05: `feed_temperature`; D1-4B-07: `energy_consumption`; SMS: `illuminance`, `supply_voltage`, `battery_level`; handle: `handle_state` | `<EAG>_<DEV>_<suffix>` | enabled |
| sensor | D2-06-40: `unlock_request` (diagnostic, phase 6) | `<EAG>_<DEV>_unlock_request` | **disabled for new registry entries**; existing entries unchanged |
| sensor | A5-04-01/02/03 and OPUS motion detector (F6-02-01 + A5-04-01): `temperature`, `humidity`, `battery_level` (upstream v0.6.0/v0.7.0b0) | `<EAG>_<DEV>_<suffix>` | enabled |
| sensor | **every** discovered device: `signal_strength` | `<EAG>_<DEV>_signal_strength` | **disabled** |
| binary_sensor | SMS: `motion`; handle: `handle_open`, `handle_tilt`, `handle_closed`; RWM: `smoke_alarm`, `battery_low`; descriptions: `liquid_detected` (F6-05-01), `window_open`, `actuator_not_responding`, `missing_temperature` (HeatArea), `actuator_low_battery`, `actuator_deactivated` (D1-4B-05), `circuit_in_use` (D1-4B-06) | `<EAG>_<DEV>_<key>` | enabled |
| binary_sensor | D5-00-01: `contact` (description, opening); OPUS motion detector: `motion` (upstream v0.6.0/v0.7.0b0) | `<EAG>_<DEV>_<key>` | enabled |
| binary_sensor | D2-06-40: `mechanics_fault` (diagnostic, phase 6) | `<EAG>_<DEV>_mechanics_fault` | **disabled for new registry entries**; existing entries unchanged |

Registry migrations (existing, unchanged): legacy multi-channel
`<EAG>_<DEV>` → `<EAG>_<DEV>_ch0` (light, switch); fork suffixes
`_illumination` → `_illuminance` and `_window_handle_lock` → `_autolock`.

Repair issue (phase 9): when the fork-suffix migration removes a duplicate
beta entry, `duplicate_beta_entity_<removed entity ID>` is created once
(persistent, fixable via the repairs confirm flow, severity warning, data
`config_entry_id`). It is deleted on confirmation or when the config entry is
removed. The aggregate multi-channel cleanup raises no issue. No other code
path creates issues; outages never do. **[Code][Test]**

Icons (phase 9): `icons.json` adds state icons for `sensor.handle_state`,
`sensor.unlock_request`, `binary_sensor.handle_closed` and
`binary_sensor.mechanics_fault`, keyed by the unchanged raw states. Unique IDs,
translation keys, device classes, categories and defaults are unchanged.
Icons are frontend metadata only. **[Code][Test]**

## 9. Services (public, unchanged)

`opus_greennet.get_device_configuration` (response only),
`opus_greennet.set_device_configuration`, `opus_greennet.get_device_parameters`
(response only), `opus_greennet.reload_entry`. All are admin services with an
optional `config_entry_id`.

## 10. Dispatcher signals (internal)

`opus_greennet_device_discovered_<EAG>` (payload: device),
`opus_greennet_device_state_update_<EAG>_<DEV>` (payload: device),
`opus_greennet_availability_update_<EAG>` (no payload),
`opus_greennet_device_removed_<EAG>` (payload: `device_id`).

## 11. Not verifiable in this environment

- Real gateway behaviour (firmware variants, retain flags, QoS, ordering and
  timing of topics) is **[unverified]**; every statement above comes from code,
  tests and the vendor reference, not from hardware observation.
- Whether the gateway publishes `stream/device/<DEV>/dbm` for rockers (relevance
  of finding F1) is **[unverified]**.
- Whether the Mosquitto bridge status is published as retained is **[unverified]**.
- Which devices or profiles receive ACK `201`, and how the gateway handles a
  queued telegram that a newer command supersedes, is **[unverified]** on
  hardware (section 7.1).
- Whether every firmware publishes a retained `stream/devices/<DEV>/friendlyId`
  leaf for each device (the discovery filter) is **[unverified]** on hardware;
  the evidence is a field report of a numeric `friendlyId` topic (CHANGELOG)
  and the flattened device model of the reference.
