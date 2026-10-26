# State semantics and notification boundaries

REST series, phase 4. This page records which input may change which state,
how unusable input is treated, and which notifications a later optimisation
(phase 5) may or may not combine. It describes the code as it is; the tests in
`tests/test_state_semantics.py` and the tests named below keep it true.

Labels: **B** = verified in code or by a test, **E** = design decision,
**A** = open assumption or missing proof.

## 1. Authority matrix

There is one state store: `EnOceanChannel` objects inside
`OpusGreenNetCoordinator.devices` (B). Everything below changes that store in
place; entities only read it.

| Source | May set | Never sets | Evidence |
|---|---|---|---|
| Stream device list (`content: devices`) | State functions without button keys, metadata, position origin `device list` | Button occurrences, command feedback | `_apply_device_list(authoritative=True)`, `protocol.device_state_functions`, `test_device_list_snapshot_does_not_replay_button_presses` |
| Stream `device` document | Same as the list, but counted as live feedback (origin `reported`) | Button occurrences | `_handle_document`, `test_single_device_document_is_live_feedback` |
| Periodic `GET /devices` | Metadata and configuration (`CONFIGURATION_KEYS`), except a setting that repeats the value from before a confirmed write (D6); signal data only if no newer telegram | Live state | `_apply_device_object(authoritative=False)`, `test_telegram_during_the_refresh_keeps_its_metadata`, `test_setting_cache_guard.py` |
| Telegram `from` | Reported state, `last_seen`, `dbm`, the single button occurrence; confirms feedback fields | Estimates | `_handle_telegram` |
| Telegram `to` (foreign command) | Target as an estimate (origin `estimated`), cover travel estimate, schedules status checks | Confirmation; "unknown" targets; timed parameters; own echoes | `protocol.outbound_state_functions`, `_is_own_echo`, `test_rocker_then_unknown_then_end_position` |
| Command acknowledgement | An estimate, only if the channel object and `state_revision` are unchanged since the snapshot | Anything for a deferred (201) command; any retry after an unclear outcome | `snapshot_channel_state`, `test_state_ownership.py`, `test_command_timeout_reports_unknown_outcome` |
| Cover travel estimate | `movement`; at the end `position`, only if the command was accepted (origin `assumed`) | Anything that feedback already replaced | `_finish_cover_movement`, `test_short_travel_waits_for_ack_before_assuming_target` |
| Status query answer | Nothing directly: the answer arrives as a stream report | | `async_send_command` returns only the delivery kind |
| Reconciliation check | Nothing directly: sends a background query; dropped when superseded | | `test_background_efficiency.py` |

Ordering rules (B): feedback always wins over an estimate (revision guard);
a replaced stream reader applies nothing (`_is_current_stream`,
`test_replaced_reader_drops_documents_it_already_parsed`); a request queued
before an outage, an unload or a device removal is cancelled
(`_check_request_valid`, `test_device_forgotten_while_request_in_flight`).

## 2. Input classes

Applied per function of a report. Position stands for any numeric field; the
last column is what the tests pin.

| Class | Example | Reading | Counts as an update | Test |
|---|---|---|---|---|
| Missing | `{"key": "position"}` | kept | no (dropped before applying, no dispatch, no `last_seen`) | `test_missing_or_null_value_is_not_a_report` |
| Null | `"value": null` | kept | no (same) | same |
| Unknown | `"unknown"` | kept | yes | `test_unusable_position_keeps_the_reading_but_counts_as_an_update`, `test_unknown_cover_values_keep_the_last_reading` |
| Invalid | `"abc"`, `101`, `-1`, `true`, `40.5` | kept | yes | same |
| `notAvailable` (selected fields) | temperature, humidity, battery | cleared to unknown | yes | `test_enocean_device.py` |
| Additive unknown key | `futureFeature` | ignored, known keys in the same telegram still apply | yes | `test_additive_unknown_function_neither_blocks_nor_changes_known_state` |

"Counts as an update" means: `state_revision` advances, `last_seen` and `dbm`
are taken, the entities are dispatched, and the estimate guard yields.
Invalid or unknown input never confirms a command (`confirmed_fields_by_channel`,
`test_invalid_actuator_fields_cannot_cancel_checks`).

## 3. Notification boundaries for phase 5

The coordinator applies every input immediately and dispatches once per
applied document (B, `test_identical_reports_stay_separate_updates`). Nothing
may debounce the raw stream. Only the entity notification after that is a
candidate for combining, and only per class:

| Class | Members | Rule |
|---|---|---|
| Never delayed or merged | Rocker press and release; any dispatch whose telegram carried a button key (also on actuators with rocker buttons); availability changes; alarm and error changes; stop and end-of-movement feedback; acknowledgement, revision and reconciliation bookkeeping; discovery and removal signals | Event entities consume `last_button` synchronously during the dispatch and the coordinator clears it right after (`clear_button_occurrence`). A delayed dispatch loses presses. |
| Already suppressed without loss | A dispatch whose whole visible projection (state, availability, features, attributes) equals the last write | `OpusGreenNetEntity._handle_state_update`. Phase 5 must not widen this. |
| Candidate only after measurement | Continuous telemetry (power, energy, temperature, humidity, illuminance, dimming ramps) | A last-value-wins window with a hard maximum delay. Not approved: automations that trigger on every change or on a threshold crossing can miss an intermediate value. Each key needs its own opt-in and a measured benefit. |
| Not combinable | Anything not listed | Do not optimise. |

If phase 5 finds no measured load problem, no combining is introduced (E).

## 4. Open points

| # | Point | Status |
|---|---|---|
| D1 | A repeated identical value is not written again, so Home Assistant's `last_reported` of that entity does not advance (`test_repeated_value_is_not_rewritten_but_a_change_is`). Suppressing RSSI-only writes was the aim; a repeated measurement is suppressed as well. Decided: accepted as is, `last_reported` does not follow repeated identical values. | E (accepted, unchanged) |
| D2 | The estimate guard is per channel, not per field: any report on the channel, even a power value, withholds a pending switch estimate (`test_any_report_on_a_channel_supersedes_a_pending_estimate`). The state then comes from the switch report or the status check. Conservative, no wrong state. | E (kept) |
| D3 | A `from` telegram without a usable function is ignored, including its `dbm` and timestamp (`_handle_telegram`: `if not functions: return`). Pinned by `test_telegram_metadata_only.py` and `test_ignores_incomplete_functions`. Decision: kept. Every telegram observed on hardware (phase 2, 25 Oct 2026) carried a function, and the 30-minute device list restores `lastSeen` and `dbm`. Revisit if such a telegram is ever seen. | E (kept), A (never observed) |
| D4 | The event entity read channel 0 only, so a button occurrence the gateway reports on another channel was dropped (the model stores it on the channel the telegram names). Fixed: every channel with an occurrence fires its event, lowest channel first; events of a channel other than 0 carry `channel` in their data, channel 0 events are unchanged (`test_event_entity.py`). Whether any device reports buttons on another channel is still unproven on hardware. | E (fixed), A (hardware) |
| D5 | `to` telegrams: observed on hardware on 25 Oct 2026 (firmware 1.21.30, 53 devices, `GET /devices/stream?delimited=newLine`, 30 s, while one actuator was switched in Apple Home). 13 telegrams: 7 `from`, 6 `to`. Five `to` telegrams carried the function `switch`, each followed about 0.12 s later by a `from` telegram with `switch` (the actuator's report); one `to` telegram carried the function `query` (a status request). The earlier phase 2 runs had none. Structure only; values were not recorded. Open: handling of a `to` telegram with `query` against real data (A); handling of `to` `switch` is covered by fake-gateway tests and now matches the observed shape. | B (hardware, structure); A (`query`) |
| D6 | The device list carries the gateway's cached copy of the configuration, and a confirmed write (PUT, then read-back from the device) is not known to refresh that cache. A list after the write could bring back the old value of a number or select. Decided: the coordinator keeps, per device and setting, the value shown before a confirmed write (`SettingGuard`, in memory only). A list entry that repeats such a value, and not the written one, is ignored. A list entry with the written value or any other value is applied and ends the protection of that setting, which also ends after 26 hours and when the device is removed. Nothing is written or queried because of it. `test_setting_cache_guard.py`. Open: whether the gateway refreshes its cache after a write is not observed on hardware; the guard is harmless if it does. A change made elsewhere back to exactly the old value stays unseen until another value or the maximum age. | E (decided), A (gateway cache behaviour) |
