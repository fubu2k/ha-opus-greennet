# OPUS GreenNet – REST API implementation plan

Revision 3, 2026-10-20: English translation of revision 2 (the German
original is in the Git history of this file), updated with the status of
phases R0–R5, R4.2/R4.3 and the R5 hardware check. Revision 2 added the
actuator overrun finding (section 3.3) to revision 1.

This document started as an **inventory and plan only**; the phases were
implemented later, each on its own branch with its own report and the
maintainer's approval (plan `implementation-plan.md`, sections 4 and 5).

| Item | Value |
|------|-------|
| Reviewed branch | `feature/rest-api-integration` (fubu2k/ha-opus-greennet) |
| Reviewed commit (revision 1/2) | `2010d2f` ("docs: record phase 14 CI evidence"), identical with `origin/feature/rest-api-integration` at that time |
| Main branch | `main` = `39b4c66`; **does not contain the REST work** (not merged) |
| Origin of the REST code | upstream `kegelmeier/ha-opus-greennet`, branch `feat/rest-transport` (`20b34c9`), integrated in phase 14 (`phases/phase-14.md`) |
| CI evidence of the reviewed state | workflow "Tests", run `39384621424` on `e04245f` (code identical with `2010d2f`), green |
| Binding rules | Since decision D3 (section 0.1), guardrails A1–A5 of `implementation-plan.md` section 2 apply **to the MQTT transport only**; REST is developed according to best practices and Home Assistant requirements |
| Location | `docs/quality/` (the repository has no `/Quality` directory; the quality documents live here) |

Labels used for statements:
**[Code]** read from the source code at the reviewed commit,
**[Test]** covered by an existing test,
**[Doc]** from repository documentation,
**[Field]** observation reported by the maintainer (not independently verified),
**[HW]** measured on the maintainer's hardware (diagnostics),
**[HW-upstream]** hardware observation of the upstream project,
**[Upstream note]** written information from the upstream project with
measurements of an installation there (passed on to the maintainer, not
re-measured here),
**[Assumption]** open assumption still to be checked,
**[Design]** design decision of this plan.

## 0. Maintainer decisions

| ID | Date | Decision | Consequence |
|----|------|----------|-------------|
| D1 | 2026-10-20 | REST is integrated as an **additional** transport next to MQTT; MQTT stays unchanged, existing entries stay on MQTT. | Implemented in phase 14. |
| D2 | 2026-10-20 | **REST reads the device list from the gateway every 30 minutes.** This new background traffic is needed for the REST API (battery level, signal strength and movement times are not streamed). | The addition listed as "requires maintainer review" in `implementation-plan.md` section 3 is **approved**. Documentation follow-up in phase R0. |
| D3 | 2026-10-20 | **Guardrails A1–A5 apply to the MQTT transport only.** In all further phases (from R1), the REST transport is developed anew according to best practices and Home Assistant requirements. | Applies to all following phases; rules in section 0.1. The REST restrictions previously derived from A1–A5 in this plan are replaced. |
| D4 | 2026-10-20 | **New gateways are set up over REST only** (R4.3, after R4.2 put REST first in the menu). | MQTT stays for existing entries and as a choice in the reconfigure flow; existing entries are never switched automatically. |

On D2: `GET /devices` reads the gateway's device model and, according to the
upstream observation, sends no radio telegram **[HW-upstream]**. The interval
of 30 min is the current state. The original rule "fixed, may not be
shortened under A1" is lifted by D3; a change follows section 0.1.

### 0.1 Scope of the guardrails since D3 (binding for all further phases)

**MQTT transport – guardrails A1–A5 remain binding unchanged.** This applies
to every entry without `transport: rest` and to all code the MQTT path runs:

- No change of behaviour, timing, wire format or traffic. No new queries, no
  retries, no cooldowns.
- Unique IDs, entity IDs, device assignments, registry settings and the
  transport of existing entries stay unchanged. No automatic transport switch.
- Retained-message protection (phase 10) unchanged.
- A re-export is not a behaviour change. Any further change to the MQTT path
  needs the maintainer's approval.

**REST transport – new development according to best practices and Home
Assistant requirements.** A1–A5 no longer protect the REST status quo. The
yardsticks are:

- the Home Assistant Integration Quality Scale (goal: all applicable rules
  met) and the Home Assistant developer documentation (among others config
  flow, entity model, availability, `inject-websession`, error handling,
  diagnostics, reauth/reconfigure, strict typing),
- accepted practice for HTTP clients and streams (separate error classes,
  timeouts, bounded backoff with jitter, a clean lifecycle),
- tests first; coverage gates, ruff, strict mypy and Hassfest are not
  lowered.

Behaviour, timing, queries, entity model and code structure of the REST
transport may change when the phase justifies it. Every change is disclosed
in the phase report (`implementation-plan.md` section 20): radio and gateway
traffic before/after, and the effect on existing REST entries and entity IDs.

**Shared code.** Modules that the MQTT path also uses (`enocean_device.py`,
`entity.py`, the platforms, `const.py`, `wire.py`, `domain.py`) stay under
A1–A5 for the MQTT path. A REST change to these modules must be bound to the
transport (its own value, its own code path or an `isinstance` check) and be
backed by the unchanged MQTT tests. Example: `COVER_MOVEMENT_GRACE_SECONDS`
lives in `const.py` and applies to both coordinators; a different grace period
for REST needs its own REST constant.

## 1. Target picture and priorities (from the assignment)

1. REST for commands and targeted queries.
2. The HTTP stream as the primary input for states and events.
3. MQTT as an optional, cleanly encapsulated fallback.
4. Fully working REST-only operation without a configured Home Assistant MQTT
   integration.
5. Later removal of MQTT without functional changes to the device model and
   the entity platforms.

Priorities when goals conflict: correct states and undistorted events →
preserving tested behaviour and existing installations → robust error
handling and lifecycle → a lean architecture → measurable performance. No new
abstraction layer without a concrete existing problem.

The Home Assistant Integration Quality Scale is the yardstick. The repository
keeps a **self-assessment** of a custom integration (`quality_scale.yaml`,
`quality-scale-evidence.md`), not an officially granted core rating.

## 2. Analysis of the reviewed state

### 2.1 Data flows at the reviewed commit

**REST entry (`transport: rest`)** – `rest_coordinator.OpusGreenNetRestCoordinator`:

| Flow | Sequence | Evidence |
|------|----------|----------|
| Setup | `async_setup`: `GET /system/info` (EURID check, `GatewayMismatchError`) → `GET /system/uptime` → `GET /devices` (authoritative) → available → timers 60 s (uptime) and 30 min (device list) → stream task. **Changed in R5** (section 7.2). | [Code] [Test] `test_rest_stream.py::test_setup_reads_identity_uptime_and_devices` |
| Stream | `api.OpusGatewayClient.async_stream` (`GET /devices/stream`, no query parameters) → `JsonDocumentSplitter` → `_handle_document`: `devices` (authoritative list), `device` (single device = live feedback), `telegram`; other types are ignored | [Code] [Test] `test_rest_stream.py::test_stream_document_types` |
| Command | Entity → `async_turn_on`/`async_set_cover_position`/… → `async_send_command` → `_async_radio_request` (one radio request at a time, `PriorityLock` 0/10/20) → `PUT /devices/{id}/state`; the answer is the acknowledgement (`200` → `CommandDelivery.SENT`, `201` → `DEFERRED`) | [Code] [Test] `test_rest_commands.py`, `test_rest_requests.py` |
| Feedback | `from` telegram → `_record_confirmed_feedback` (field revisions, cancel pending status checks, end of the travel estimate on a valid position) → `update_from_telegram` → dispatcher | [Code] [Test] `test_rest_telegrams.py` |
| Foreign command | `to` telegram (HomeKit, gateway logic) → apply only the target state optimistically → status checks after 5/20 s; the integration's own echo within 2 s after `200` is ignored | [Code] [Test] `test_rest_telegrams.py::test_homekit_command_is_applied_then_verified`, `test_rest_requests.py::test_own_echo_after_the_acknowledgement_is_ignored` |
| Button event | `from` telegram with `buttonA0`… → `EnOceanDevice.update_from_telegram` sets the event → synchronous dispatch → the event entity consumes it → `clear_button_occurrence()`; device lists never set button fields | [Code] [Test] `test_rest_telegrams.py::test_fast_press_release_keeps_both_events`, `test_rest_devices.py::test_new_rocker_from_list_has_no_button_occurrence`, `test_rest_stream.py::test_device_list_snapshot_does_not_replay_button_presses` |
| 30 min list | `_async_refresh_device_list` → `_apply_device_list(authoritative=False)` → configuration, battery and metadata only | [Code] [Test] `test_rest_stream.py::test_periodic_device_list_refresh_updates_metadata_only` |

**MQTT entry (without `transport`)** – `coordinator.OpusGreenNetCoordinator`
with `mqtt_transport.MQTTRequestManager`: unchanged since phase 13
(retained protection phase 10, ACK 201 phase 11, late-answer diagnostics
phase 12) **[Code] [Doc]** `protocol-contract.md` sections 1–11.

**Platforms** access only `transport.OpusCoordinator`
(`OpusGreenNetCoordinator | OpusGreenNetRestCoordinator`; a `Protocol` since
R2), dispatcher signals and `EnOceanDevice`/`EnOceanChannel` **[Code]**.

### 2.2 Existing state, revision, freshness and deduplication mechanisms

These mechanisms are **reused**; the plan introduces no parallel replacement.

| Mechanism | Location | Purpose |
|-----------|----------|---------|
| `EnOceanChannel.state_revision` + `ChannelStateSnapshot` | `enocean_device.py` | estimate after an acknowledgement only if no newer information arrived since sending (phase 2) |
| `_feedback_revisions` | both coordinators | feedback during the acknowledgement confirms the command without an extra query |
| `_pending_reconciliation_fields`, `_reconcile_needed`, `SUPERSEDED` | both coordinators | at most 2 status checks per channel; superseded checks are dropped before sending (phase 4) |
| `CommandDelivery`, `deferred_commands` | `coordinator.py`, `rest_coordinator.py`, `enocean_device.py` | ACK 201: no assumption, no query (phase 11) |
| `_sent_commands` / `OWN_COMMAND_ECHO_SECONDS` | `rest_coordinator.py` | ignore the echo of the integration's own transmitted commands |
| `_command_waiters` | both coordinators | ignore `to` telegrams during the integration's own acknowledgement |
| `clear_button_occurrence()` | `enocean_device.py`, callers in both coordinators | no replay of button events |
| `_device_generations`, `_outage_generation` | `rest_coordinator.py` | requests of a removed device or from before an outage are not sent |
| `_live_fields`, replay guard | `coordinator.py` only | retained replays do not overwrite live fields (phase 10) |
| projection `_dispatched_projection` | `entity.py` | no redundant state writes (phase 4) |
| readings: invalid → old value, `notAvailable` → `None` | `EnOceanDevice._integer_reading`/`_float_reading` | partial updates delete no fields; "unknown" positions do not overwrite the last position |

### 2.3 Couplings that prevented REST-only operation

All resolved in R2 except K7 (open for R8).

| No. | Coupling | Evidence | Effect |
|-----|----------|----------|--------|
| K1 | `manifest.json`: `"dependencies": ["mqtt"]` and `"mqtt": [...]` (discovery) | [Code] | Home Assistant loads the MQTT component for every entry. Whether startup works without a configured MQTT integration was **not tested** [Assumption]. |
| K2 | `rest_coordinator.py` imports `coordinator.py` (signals, `CommandDelivery`, `CoverMovement`, constants) and `mqtt_transport.request_error` | [Code] | Every REST import loads `homeassistant.components.mqtt`. |
| K3 | `__init__.py`, `transport.py`, `entity.py` and all platforms import from `coordinator.py` | [Code] | Platforms formally depend on the MQTT module; removing MQTT would change every platform (violates goal 5). |
| K4 | `config_flow.py` imports `homeassistant.components.mqtt`, `mqtt_transport` and `discovery` at module level | [Code] | The REST path of the flow loads MQTT too. |
| K5 | `tests/conftest.py`: fixture `hass` always adds `"mqtt"` to `hass.config.components` | [Code] | No test checks a REST entry without the MQTT component. |
| K6 | `transport.OpusCoordinator` is a union of both classes | [Code] | Removing MQTT changes the type of every platform signature. |
| K7 | Quality rule `discovery` is met only through MQTT | [Code] [Doc] | After removing MQTT the rule would fall to `todo` as long as REST has no discovery. |

### 2.4 Already cleanly separated – keep

- `api.py` imports only the standard library, `aiohttp` and `wire.py`; **no
  Home Assistant imports** [Code]. The session is injected and never closed;
  the Basic auth header is built once in the constructor and never logged;
  typed exceptions `OpusAuthError`, `OpusConnectionError`, `OpusTimeoutError`,
  `OpusResponseError` [Code] [Test] `test_rest_api.py`.
- `wire.py` (JSON boundary, no HA) and `protocol.py` (pure functions) [Code].
- `EnOceanDevice`/`EnOceanChannel` contain no MQTT topics, HTTP objects or
  entity objects [Code].
- No automatic repetition of write commands; a timeout does not change gateway
  availability (A2, A3) [Code] [Test]
  `test_rest_commands.py::test_command_timeout_keeps_gateway_available`.
- Transport chosen per entry, switched only through "Reconfigure" (A5) [Code]
  [Test] `test_transport_selection.py`.

### 2.5 Differences between code, documentation, tests and evidence

| No. | Difference | Correction |
|-----|------------|------------|
| A-1 | `implementation-plan.md` sections 3 and 15a and `phases/phase-14.md` listed the 30 min query as "review pending". | Approved since D2 → phase R0 (done). |
| A-2 | The assignment named `GET /devices/stream?direction=from&delimited=newLine&output=singleLine&levelOfDetail=high`. The code calls `GET /devices/stream` **without** parameters; no repository source documents these parameters. | Section 4: adopt only after a hardware check; do not use `direction=from`. |
| A-3 | The code also uses `GET /system/uptime`, `GET /devices/{id}` and `GET /devices/{id}/parameters`, which the assignment did not list. | Backed by code, tests and `protocol-contract.md` 12.2; they stay. |
| A-4 | `manifest.json` `version` is `0.7.0b2` on all commits; a field test therefore cannot be mapped to a commit. | Record the installed commit for field tests (phase R1). |
| A-5 | Goal "command accepted ≠ observed state": for covers **without** a known movement time the code takes the target as the position right after `200` (`EnOceanDevice.apply_cover_position_estimate`), **with** a movement time after movement time + 5 s (`_finish_cover_movement`, comment "Assumed, not physical feedback"). | Existing, tested behaviour (MQTT and REST). No silent change; make the origin visible and ask for a decision → phase R6. |
| A-6 | README and contract described the REST tests as "simulated", while the maintainer had meanwhile run field tests [Field]. | Add the field baseline (section 3) to `phase-14.md` and contract 12.9 → R0 (done). |

### 2.6 Further findings

| No. | Finding | Evidence | Risk | Status |
|-----|---------|----------|------|--------|
| B-1 | `api._async_request` checked the HTTP status only for 401/403 and for an empty body ≥ 300; with a JSON body only `header.httpStatus` decided. An HTTP 500 with JSON status 200 counted as success. | [Code] | low, but contradicts "treat HTTP and JSON status separately" | fixed in R3 |
| B-2 | `_async_run_stream`: when a stream ended after its first document, it reconnected **immediately** without a pause (`if connected: continue`) and reset `failures`. A gateway closing connections right after the snapshot caused a tight loop with a full device list download each time. Fixed pauses 1–60 s, **without jitter**. | [Code] | medium (load on gateway and HA) | fixed in R4 |
| B-3 | `JsonDocumentSplitter` limited only remainders growing across chunks (`_size`); a complete document inside one chunk was not checked against `MAX_DOCUMENT_SIZE`. | [Code] | low (aiohttp chunks are small) | fixed in R4 |
| B-4 | `_apply_device_object` creates a **new** `EnOceanDevice` for every list and copies fields one by one. New fields can be forgotten (phase 14 added `deferred_commands`, `profile`, `last_update_finalized_monotonic`); an old object held during a command writes `last_command_error` into the void. | [Code] | medium (maintainability, rare wrong error display) | open, R6 |
| B-5 | The 30 min list took `dbm` and `lastSeen` from the list even when a newer telegram arrived between request and answer. | [Code] | low (metadata only) | fixed in R5 |
| B-6 | `PriorityLock` and the priorities lived in `api.py` but belong to the coordinator's radio queue. | [Code] | unclear library boundary | fixed in R3 |
| B-7 | `gateway_to_ha_ms` = `time()` (HA clock) minus the gateway's telegram timestamp (gateway clock, ms) at dispatch in the coordinator. It contains the clock offset and measures **neither** actuator→gateway nor the entity write. | [Code] `rest_coordinator._mark_and_log_dispatch`, `protocol.telegram_timestamp` | measurement limit (section 3.2) | documented |
| B-8 | Setup loaded the device list twice: `GET /devices` and right after it the first stream document. | [Code] | unnecessary traffic and CPU on large installations | R5; see R5-H1 |
| R5-H1 | The device list in the stream carries **no `configuration` block** (cover movement and rotation times) on OPUS-IQ-DOT 1.21.30; only `GET /devices` does. After a stream-first start, 0 of 10 covers had a movement time (10 of 10 before). | [HW] phase 21 section 7 | high for covers (no travel estimate after a start) | fixed (PR #28) |

### 2.7 Test protection of the field baseline

| Expectation (section 3.1) | Existing tests | Gap |
|---------------------------|----------------|-----|
| R1 connection after migration | `test_transport_selection.py::test_switching_an_mqtt_entry_to_rest_and_back_keeps_entities` | – |
| R2 reload loads the list, stream reconnects | `test_rest_stream.py::test_new_stream_resynchronises_state`, `::test_unload_stops_stream_and_timers` | reload through `opus_greennet.reload_entry` with REST not explicit |
| R3 opening/closing indicators | `test_rest_cover_movement.py::test_real_ha_cover_shows_motion_and_accepts_configuration_updates` | – |
| R4 position matches reality | `test_rest_devices.py::test_stream_resync_replaces_cover_state`, `test_rest_telegrams.py::test_from_telegram_updates_state_and_metadata` | raw→HA conversion explicit only in MQTT entity tests |
| R5 target change while moving | `test_rest_cover_movement.py::test_retarget_uses_elapsed_estimate_and_cancels_old_timer`, `::test_retarget_cancels_old_queries_before_ack` | – |
| R6 stop → position report updates at once | `test_rest_cover_stop.py`, `test_rest_cover_movement.py::test_numeric_feedback_ends_estimate_and_reconciliation` | – |
| R7 local rockers of cover actuators, then position | `test_rest_telegrams.py::test_integrated_rocker_telegrams_leave_actuator_state` | sequence "rocker → `unknown` → end position" missing |
| R8 F6-02-01: exactly one event update per press/release | `test_rest_telegrams.py::test_rocker_press_and_release_fire_events`, `::test_fast_press_release_keeps_both_events` | long press and 6× sequence with a count of entity writes missing |

## 3. Field-test baseline

### 3.1 Regression expectations [Field]

Reported by the maintainer, not independently verified; to be preserved, but
no proof of complete correctness:
R1 connection after the migration · R2 reload loads the device list and
reconnects the stream · R3 opening/closing indicators present ·
R4 state and position match reality · R5 position commands and target change
while moving · R6 stop → position report after ≈110 ms, immediate update ·
R7 local rocker telegrams of the cover actuators, then position updates ·
R8 F6-02-01: 6 press and 6 release telegrams, exactly one event entity update
each, gateway→HA 7–19 ms, receipt→write 0.9–1.5 ms.
The R8 sample does **not** imply a general exactly-once guarantee.

### 3.2 Open observation: delayed end-position report

| Run | Time | Observation | Interpretation |
|-----|-----:|-------------|----------------|
| A | T+0.000 s | raw value 0 sent | OPUS 0 = open → HA position 100 (`cover.py`: `100 - channel.position`) [Code] |
| A | T+0.482 s | position `unknown` | `from` telegram; an invalid value keeps the last position and confirms nothing (`_integer_reading`, `confirmed_fields_by_channel`) [Code] |
| A | T+32.976 s | position 0.0, `gateway_to_ha_ms=8` | the gateway timestamp is already ≈T+33 s → the delay arises **before** the gateway's timestamp, provided the clocks are in sync (B-7) |
| B | T+0.000 / 0.460 / 33.100 s | raw value 100 → `unknown` → 100.0, `gateway_to_ha_ms=19` | as A; OPUS 100 = closed → HA 0 |

Assessment [Design]: the integration processes the end-position report within
milliseconds of the gateway timestamp. A regression against v0.7.0 is **not
shown** (no controlled comparison).

State after section 3.3: H1 is supported by the upstream note (actuator
overrun on runs into an end position), H2 and H4 are therefore unlikely, H5
remains refuted by the log. Confirmation on the maintainer's own installation
is the subject of R1.

Original hypotheses (revision 1):
- **H1** The actuator reports only after its configured run time
  (`verticalMovementTime`/run time incl. overrun), not when the limit switch
  is reached. With ≈33 s in total and ≈5–6 s after standstill, a run time of
  ≈33 s would be plausible. [Assumption]
- **H2** The gateway provides the report late (e.g. after its own status
  query). [Assumption]
- **H3** A clock offset gateway↔HA distorts `gateway_to_ha_ms`. [Assumption]
- **H4** The integration delays (debounce, queue). Against H4: REST has no
  debounce, telegrams are processed synchronously in the stream task [Code].
- **H5** The shown end position comes from the integration's own fallback
  assumption (`_finish_cover_movement` after movement time + 5 s) rather than
  a telegram. Against H5: the logged `gateway_to_ha_ms` (logged only for
  telegrams) [Code].

Investigation (phase R1, no polling, no timer assumptions): see phase R1.

### 3.3 New finding: overrun of the D2-05 actuator (upstream note, 2026-10-20)

Key statement **[Upstream note]**: the late end-position report is expected
actuator behaviour and does **not** arise in the REST transport.

- When a D2-05 actuator moves to the fully open or closed position, it keeps
  the motor on for a few seconds longer than the configured movement time so
  that the end position is reached safely. The motor's limit switch stops the
  shutter physically; the actuator sends its final position report only when
  it switches off.
- Runs to an intermediate position and stop have no overrun. This explains
  the stop report after ≈110 ms (field baseline R6).
- The gateway's device list contains the configured movement time and the
  duration of the last run. Measurements of the upstream installation – every
  full run took 3.5–5.7 s longer than configured:

| Actuator | Configured movement time | Last full run |
|----------|-------------------------:|--------------:|
| Shutter 1 | 20.68 s | 26.37 s (+5.7 s) |
| Blind 1 | 26.14 s | 31.14 s (+5.0 s) |
| Blind 2 | 52.78 s | 57.79 s (+5.0 s) |
| Blind 3 | 55.09 s | 60.21 s (+5.1 s) |

- Matches the maintainer's observation (section 3.2): ≈27 s run + ≈5–6 s
  overrun ≈ 33 s. `gateway_to_ha_ms=8/19` means the gateway received the end
  telegram only milliseconds before Home Assistant; the waiting time lies
  between actuator and gateway, before the REST API is involved (see B-7:
  valid with synchronised clocks).
- Comparison with the HomeKit side of the same gateway (upstream, alongside
  v1.0.0b0): with 8 covers in one scheduled full run, HomeKit received every
  end position at the same moment; v1.0.0b0 was 4–28 ms earlier each time.
  Apple Home or the OPUS app show the same delay.
- v0.7.0 (MQTT) behaves no differently: the cover estimate is unchanged.
  While moving, the entity shows "opening/closing" until the configured
  movement time plus 5 s grace has elapsed or the actuator reports, whichever
  comes first. With MQTT the end report could only arrive at the same time or
  later; upstream it arrived 0.8–10 s after HomeKit (throughput of the MQTT
  broker in the gateway).

Comparison with the code [Code]:
- "Movement time + 5 s grace" corresponds to `CoverMovement.ends_at`
  (`started_at + travel + COVER_MOVEMENT_GRACE_SECONDS`, 5.0 s) in both
  coordinators; a valid position report ends the estimate earlier
  (`_record_confirmed_feedback`).
- Consequence for A-5/R6: if the overrun exceeds the grace period (upstream up
  to 5.7 s), `_finish_cover_movement` sets the target as an **assumed**
  position shortly **before** the real end report; the report replaces it
  afterwards. The visible result is the same, but for ≈0–1 s the origin is
  "assumed".
- `lastVerticalMovementTime` is currently **not** read by the code; only
  `verticalMovementTime` and `rotationTime` from `configuration.parameters`
  (`protocol.configuration_state_functions`). Diagnostics therefore do not
  show the last run duration. Note (R5-H1): this configuration block exists
  only in `GET /devices`, not in the stream's device list.

Consequences for the plan [Design]:
- No code fix for the end-position delay; it is actuator behaviour and is
  documented (README "Known limitations"/Cover, contract 12.9) → R0/R1.
- R1 is reduced from a root-cause analysis to a **confirmation measurement**
  on the maintainer's own installation.
- The grace period of 5 s stays unchanged for now. It is a constant shared by
  both transports (`const.py`) and stays under A1–A5 for MQTT; a different
  grace period for REST needs its own REST constant (section 0.1). Whether
  REST adapts the grace period for an overrun > 5 s or only makes the origin
  visible is decided in R6 (O-4).

## 4. REST resources

| Resource | Used by the code | Evidence |
|----------|------------------|----------|
| `GET /system/info` | yes: config flow, setup, reauth/reconfigure | [Code] [Test] [HW-upstream] |
| `GET /system/uptime` | yes: setup, every 60 s | [Code] [Test] [HW-upstream] |
| `GET /devices` | yes: once right after setup (configuration and battery, R5-H1), every 30 min (D2) | [Code] [Test] [HW-upstream] [HW] |
| `GET /devices/stream` | yes, **without** query parameters; format: concatenated JSON documents (also pretty-printed); its first document is the setup snapshot since R5; its device list has no `configuration` block [HW] | [Code] [Test] [HW-upstream] [HW] |
| `GET /devices/{id}` | yes: unknown device, at most once per hour per device | [Code] [Test] |
| `PUT /devices/{id}/state` | yes: commands, status queries | [Code] [Test] [HW-upstream] |
| `GET`/`PUT /devices/{id}/configuration` | yes: service actions only | [Code] [Test] |
| `GET /devices/{id}/parameters` | yes: service action only | [Code] [Test] |
| Stream parameters `direction=from`, `delimited=newLine`, `output=singleLine`, `levelOfDetail=high` | **no**, not documented in any repository source | – |

Assessment of the stream parameters [Design]:
- Do **not** use `direction=from`: the code needs `to` telegrams for foreign
  commands (HomeKit), for detecting delivery after ACK 201 and for echo
  detection [Code]. Without them, states would be lost.
- `delimited=newLine`/`output=singleLine` would be compatible with the existing
  splitter (line breaks are whitespace between documents) and would not
  simplify parsing. Adopt only after hardware evidence.
- `levelOfDetail=high`: effect unknown (possibly the missing configuration of
  R5-H1); only after a hardware comparison.

## 5. Target architecture (smallest viable solution)

### 5.1 Design decisions

| ID | Decision | Concrete problem it solves |
|----|----------|----------------------------|
| E1 | **Two coordinators stay**; no shared transport engine for now. | A unification would have to rebuild the MQTT protections (retained replays, fragment debounce, answer routing, A4), although MQTT is to be removed later. |
| E2 | New transport-neutral module **`domain.py`** for `CommandDelivery`, `CoverMovement`, dispatcher signals, `request_error`, `ACK_STATUS_DEFERRED`, timing constants; `coordinator.py`/`mqtt_transport.py` re-export for compatibility. | K2, K3: REST and platforms import no MQTT module. |
| E3 | `transport.OpusCoordinator` becomes a `typing.Protocol` (instead of a union). | K6: removing the MQTT coordinator changes no platform. |
| E4 | Import MQTT only on demand (`__init__`, `config_flow`); `manifest.json` `dependencies` → `after_dependencies` after verification (Hassfest, discovery requirement, MQTT start without broker). | K1, K4, K5: REST-only without the MQTT integration. |
| E5 | Library boundary = `api.py` + JSON helpers from `wire.py`; `PriorityLock` moves into the REST coordinator. A later extraction as a PyPI package without code changes to the core. | B-6, assignment "isolated client". |
| E6 | Devices are updated **in place** (an `EnOceanDevice` method for metadata), not recreated. | B-4: no forgotten fields, no orphaned references. |
| E7 | Stream-first start (section 7) instead of a double device list. | B-8, start race. |

### 5.2 Layers

| Layer | Modules (target) | Must not know |
|-------|------------------|---------------|
| Transport | `api.py` (HTTP, stream, errors), `mqtt_transport.py` | Home Assistant (except `mqtt_transport`), entities, device model |
| Decoding | `protocol.py` (REST), MQTT flattening in `coordinator.py` | aiohttp objects, entities |
| Domain logic | `enocean_device.py` (state, event, metadata, delivery status), `domain.py` | MQTT topics, HTTP responses, entities |
| Command routing, availability | `rest_coordinator.py`, `coordinator.py` | entity objects |
| Home Assistant | `__init__.py`, `config_flow.py`, platforms, `entity.py`, `diagnostics.py` | transport details (only `OpusCoordinator`) |

### 5.3 Domain model

Reused: `EnOceanDevice`/`EnOceanChannel`, `state_revision`,
`ChannelStateSnapshot`, `deferred_commands`, EEP mappings in `const.py`.
Kept separate (current state, stays):

| Category | Current location |
|----------|------------------|
| Persistent state | `EnOceanChannel` (e.g. `is_on`, `position`, `angle`) |
| Short-lived events | button fields, cleared after dispatch (`clear_button_occurrence`) |
| Metadata/configuration | `EnOceanDevice` (`dbm`, `last_seen`, `eeps`), `EnOceanChannel.vertical_movement_time`, `rotation_time` |
| Command acceptance/delivery status | return value `CommandDelivery`, `deferred_commands`, `last_command_error` |
| Observed device state | fields after a `from` telegram/single device; **not yet** distinguishable from estimates (A-5) |

Rules (existing, pinned by tests): a missing field changes nothing; an invalid
value keeps the old one; only `notAvailable` sets readings to unknown. State
ownership: exactly one store per entry (`coordinator.devices`), no extra
stores.

## 6. Stream lifecycle: before and target

| Requirement | Before [Code] | Target / phase |
|-------------|---------------|----------------|
| One reader per gateway, bound to the entry | one `_stream_task` via `hass.async_create_background_task`, cancelled and awaited on unload | stays; connection generation against late documents of a replaced stream (R4, **done** phase 18) |
| Messages ≠ chunks, UTF-8 boundaries | `JsonDocumentSplitter` with incremental UTF-8 decoder, state across chunks | stays |
| Empty/malformed/unknown messages | non-objects skipped, unknown types ignored, foreign characters → error → reconnect | stays |
| Oversized messages | 8 MB limit only for remainders growing across chunks (B-3) | also check complete documents (R4, **done** phase 18) |
| EOF/abort | reconnect | minimum pause and backoff also after short-lived connections (B-2, R4, **done** phase 18: stream < 10 s = failure) |
| Auth vs. network | 401/403 → `OpusAuthError` → reauth, no retries; otherwise backoff | stays |
| Backoff with jitter | fixed 1, 2, 5, 10, 30, 60 s, no jitter | bounded exponential with jitter (R4, **done** phase 18: nominal 1–60 s, +0–25 %) |
| Cancellation, response release | `async with session.get(...)`; unload cancels tasks and waits | stays; test "no open reader after reload" (R4, **done** phase 18, plus `aclosing`) |
| Gateway restart | falling uptime → replace the stream | stays |
| Silent stream | reopen after 900 s without data (`STREAM_IDLE_TIMEOUT`); no documented heartbeat; silence ≠ error (no unavailability) | stays; availability only through connection errors and the uptime probe |

## 7. Start and resynchronisation

### 7.1 Analysis of the race between `GET /devices` and the stream [Code]

1. Setup read `GET /devices` (time t₁), then started the stream.
2. The stream delivers the complete list as its first document (t₂ > t₁) and
   then telegrams **in order on the same connection**.
3. Changes between t₁ and t₂ are contained in the stream snapshot; no state is
   lost. Button events from this window are lost – that is correct, events are
   never created from snapshots.
4. Consequence: the first list is redundant (B-8). A real overwrite risk
   exists only for an acknowledgement shortly before t₂: the snapshot may show
   the old state. Pending status checks stay, because lists are not feedback
   (`test_rest_devices.py::test_device_list_is_not_command_feedback`).
5. The 30 min list is not authoritative and overwrites no live state; only
   metadata could jump back (B-5).

Correction after the hardware check (R5-H1): point 4 holds for **state** only.
The stream's list lacks the `configuration` block, so it is not a full
replacement of `GET /devices`.

### 7.2 Proposed sequence [Design, phase R5]

1. `GET /system/info` – check identity (EURID); auth/mismatch → reauth.
2. Open the stream and wait for the first `devices` document (deadline e.g.
   30 s, value set in R5). Missing → `ConfigEntryNotReady`.
3. Apply this document authoritatively → available → load platforms.
4. The same task keeps reading the stream (no second connection).
5. Every reconnect starts again with an authoritative snapshot; the
   connection generation prevents applying documents of a replaced stream.
6. `GET /devices` only every 30 min (D2); metadata from this list does not
   overwrite newer telegram metadata (B-5).
7. `GET /system/uptime` stays the 60 s health check and never changes device
   states (gateway health is not device state; HA rule
   `entity-unavailable`).

Rationale: one source of truth at startup, no race between two snapshots, one
device list fewer per start. The alternative "keep both" remained possible if
hardware showed that the first stream snapshot arrives late; decision in R5
after measurement.

**Implemented in R5 (phase 21):** sequence as above, deadline 30 s.
`GET /system/uptime` stays at setup (baseline of the restart detection). The
hardware measurement uses `gateway.first_device_list_seconds` in the
diagnostics. **Result [HW]:** 0.076 s with 53 devices (deadline stays).
Because of R5-H1, the non-authoritative metadata refresh (`GET /devices`)
runs once right after setup for configuration and battery only (PR #28).

## 8. Phase plan

Working branch (decision 2026-10-20): until further notice all work happens on
`feature/rest-api-integration`. Every phase branch starts from there and
returns by PR with base `feature/rest-api-integration`, never into `main`. A
later merge into `main` first needs the revert of the revert commit `32685d1`
(PR #15).

Every phase: its own branch (`feat/`/`fix/`), tests first, CI green, phase
report `phases/phase-NN.md` per section 20 of `implementation-plan.md`, no
version, no CHANGELOG without approval. Hardware tests only with separate
approval. Section 0.1 (D3) applies to every phase: A1–A5 for the MQTT path and
shared code, best practices and Home Assistant requirements for the REST
transport.

Phase numbering: R0 = 15, R2 = 16, R3 = 17, R4 = 18, R4.2 = 19, R4.3 = 20,
R5 = 21 (R1 is still open).

### R0 – Documentation follow-up (docs only)

**Status 2026-10-20:** implemented on branch `docs/opus-r0-rest-documentation`
(base `1847f88`), report `phases/phase-15.md`. **Merged** into
`feature/rest-api-integration` via PR #16 (merge commit `4014b1c`,
2026-10-20); not into `main`. Resolves A-1 and A-6 of section 2.5.

- Record D2: `implementation-plan.md` section 3 (addition "approved
  2026-10-20") and 15a, `phases/phase-14.md` section 9, `protocol-contract.md`
  12.8/12.9 (field baseline, end-position observation, actuator overrun per
  section 3.3 labelled as upstream note).
- README "Known limitations": on full runs the end-position report arrives
  only after the actuator overrun (≈3.5–6 s after physical standstill); stop
  and intermediate positions without overrun.
- Link this document.
- Acceptance: no code change, consistent cross-references.

### R1 – Confirmation of the actuator overrun (measurement, no fix)

Starting point is section 3.3 (upstream note, H1 supported). **Open.**

- Record the installed commit (A-4).
- Full run up/down: watch Apple Home or the OPUS app next to Home Assistant;
  the end position should appear in both at the same moment.
- Compare the configured movement time and the last run duration
  (`verticalMovementTime`, `lastVerticalMovementTime`): a difference of
  ≈5–6 s after a full run is expected. The preferred source is the gateway's
  device list (no radio request). The upstream suggestion
  `opus_greennet.get_device_configuration` is a ReCom request
  (`protocol-contract.md` 12.2: potentially radio) and is therefore only
  allowed as a one-off, explicitly approved user action.
- Diagnostics: `vertical_movement_time` and `last_update_source` of the end
  position (telegram instead of fallback, H5); check the clocks of gateway and
  HA (NTP) (H3).
- Document the result (phase report, README note on the end position on full
  runs, contract 12.9).
- Pure measurement phase without code change. For an undistorted measurement,
  no extra queries during the measurement runs. Consequences for the code
  (e.g. grace period, origin of the position) go to R6.

### R2 – REST-only decoupling

**Status 2026-10-20:** implemented on branch `feat/opus-r2-rest-only-decoupling`
(base `feature/rest-api-integration` `4014b1c`), report `phases/phase-16.md`;
**merged** into `feature/rest-api-integration` via PR #17 (merge commit
`b9c3fb0`, 2026-10-20); not into `main`. K1–K6 resolved: `domain.py` (E2),
`OpusCoordinator` as a `Protocol` (E3), MQTT imports only on demand in
`__init__`/`config_flow` (E4), fixture `hass_without_mqtt` (K5). O-1 settled:
`after_dependencies: ["mqtt"]` with evidence (Hassfest green at the reference
commit, MQTT discovery and MQTT entries unchanged, REST entry without the MQTT
integration). No behaviour, timing, wire or traffic change on MQTT or REST.
K7 stays open (R8).

- `domain.py` (E2), re-exports in `coordinator.py`/`mqtt_transport.py`.
- `OpusCoordinator` as a `Protocol` (E3).
- Lazy MQTT imports (`__init__`, `config_flow`); `manifest.json`
  `after_dependencies: ["mqtt"]` after verification (E4) – evidence through
  Hassfest at the reference commit, MQTT discovery and MQTT entries without a
  behaviour change.
- Tests: fixture without `"mqtt"` in `hass.config.components`; a REST entry
  loads, controls and unloads; importing `rest_coordinator` loads no
  `homeassistant.components.mqtt` (checked through `sys.modules`).
- MQTT (A1–A5): no behaviour or traffic change; MQTT tests unchanged and
  green; re-exports are not a behaviour change.
- REST (D3): structure per Home Assistant requirements; behaviour changes on
  the REST path are allowed when justified and disclosed in the report.

### R3 – REST client hardening and library boundary

**Status 2026-10-20:** implemented on branch `feat/opus-r3-rest-client-hardening`
(base `feature/rest-api-integration` `b9c3fb0`), report `phases/phase-17.md`;
merged via PR #18 (`1c30105`), CI run `39423057097` green. B-1 fixed (HTTP
status and JSON status separate, error class with `http_status` and
`gateway_status`), B-6 fixed (`PriorityLock` and priorities in the REST
coordinator; `api.py` imports only the standard library, aiohttp and
`wire.py`). No retries; concurrency and timeouts unchanged (justification in
the report, section 2). MQTT path unchanged.

- Evaluate the HTTP status in addition to the JSON status (B-1); error class
  with both values.
- `PriorityLock` into the REST coordinator (B-6); `api.py` without
  integration constants.
- Tests: no HA import in `api.py`/`wire.py` (AST), no `ClientSession(`, error
  messages without credentials, no repetition after a PUT timeout.
- REST (D3): set concurrency, timeouts and retries by best practice and
  justify them in the report (idempotency of the command, risk of a double
  execution at the actuator, radio traffic before/after).

### R4 – Stream lifecycle

**Status 2026-10-20:** implemented on branch `feat/opus-r4-stream-lifecycle`
(base `feature/rest-api-integration` `e5b609e`), report `phases/phase-18.md`;
merged via PR #21 (`ae3e084`), CI run `39431240243` green. B-2 fixed (bounded
exponential backoff 1–60 s with 0–25 % upward jitter, a pause after every
stream end, a stream < 10 s counts as a failure), B-3 fixed (size check also
for complete documents), connection generation and `aclosing` against late
documents of a replaced reader. MQTT path unchanged.

- Bounded exponential backoff with jitter; short-lived connections
  (e.g. < 10 s) count as failures (B-2).
- Connection generation for documents; size check also for complete
  documents (B-3).
- Tests: closing right after the snapshot causes no tight loop; reload leaves
  no second reader; auth errors without retry.
- REST (D3): backoff and reconnect by best practice; gateway traffic
  before/after in the report.

### R5 – Start and resynchronisation

**Status 2026-10-20:** implemented on branch `feat/opus-r5-stream-first-start`
(base `feature/rest-api-integration` `bcb6276`), report `phases/phase-21.md`;
merged via PR #25 (`2d3cf9c`), CI run `39458477903` green. Stream first per
7.2, deadline 30 s for the first list (`FIRST_DEVICE_LIST_TIMEOUT`), measured
time in log and diagnostics; B-5 fixed (a telegram after the request start
keeps `dbm`/`lastSeen`).

**Hardware check 2026-10-20 (phase 21, section 7):** first list after
0.076 s, deadline 30 s confirmed. Finding R5-H1: on OPUS-IQ-DOT 1.21.30 the
stream's list contains **no `configuration` block**; covers had no movement
times after a start (0 of 10). Fix on branch `fix/opus-r5-movement-times`:
`GET /devices` once right after setup (not authoritative, like the 30 min
list); merged via PR #28 (`8153328`), CI run `39464251539` green. The goal
"one device list fewer per start" is not reachable on this firmware and is
withdrawn; state and availability still come only from the stream.

Related fix: the diagnostics redact the HomeKit QR setup payload
(`homekitQR`), found in the same hardware diagnostics; PR #27 (`56640c0`), CI
run `39462018715` green.

- Stream first per section 7.2 (E7); measure the deadline for the first
  document.
- B-5: take metadata from the 30 min list only if no newer telegram arrived
  since the request started.
- Tests: race request/telegram, reconnect with snapshot, no button event from
  snapshots, setup failure without a first document.
- Measurable: one device list fewer per start and reload (**withdrawn** after
  hardware finding R5-H1: the stream's list carries no configuration).

### R6 – Domain model

**Status 2026-10-20:** split into R6a and R6b.
**R6a** implemented on branch `feat/opus-r6-domain-model` (base
`feature/rest-api-integration` `da80334`), report `phases/phase-22.md`: in-place
update (E6, B-4 fixed) with a field classification test,
`lastVerticalMovementTime` as a REST diagnostic value, regression tests for
R7/R8 of section 2.7. No visible behaviour change. **R6b** decided by the
maintainer on 2026-10-20 as **1a + 2a** (`phases/phase-22.md` section 8):
REST diagnostics show the origin of each cover position (`device list`,
`reported`, `estimated`, `assumed`); no entity change; the 5 s grace period
stays unchanged. Implemented on the same branch.

- In-place update (E6) with a test "field list completely taken over".
- Origin of the position (`reported`/`estimated`/`assumed`) at first only in
  diagnostics (A-5); no visible behaviour change without a decision.
- Take `lastVerticalMovementTime` from the device list as a diagnostic value
  (no new request; section 3.3), so the overrun is visible without ReCom.
- Decision template for the 5 s grace period with an overrun > 5 s (O-4):
  leave it unchanged, only show the origin, or derive the grace period from
  the measured run duration (would shift status checks in time, never add
  any).
- Regression tests for R4/R7/R8 of section 2.7 (sequence rocker → `unknown`
  → end position; 6× press/release with exactly one entity write per
  telegram; long press).

### R7 – Default for new entries (decision)

**Partly brought forward 2026-10-20 as R4.2** (maintainer decision): REST
first, MQTT second in the setup menu; branch `feat/opus-r4-2-rest-first-menu`,
report `phases/phase-19.md`; merged via PR #23 (`c8573ef`).

**Brought forward further 2026-10-20 as R4.3** (maintainer decision D4): new
gateways are set up over REST only. The setup dialog shows host, port, user
and password directly (no menu); a gateway discovered over MQTT also leads to
this REST form. MQTT stays for existing entries and as a choice in the
reconfigure menu. Branch `feat/opus-r4-3-rest-only-setup`, report
`phases/phase-20.md`; merged via PR #24 (`bcb6276`), CI run `39449339335`
green. The menu part of R7 is therefore done; a "fallback" hint is dropped.

- Optional: REST as the first menu option (**done**, R4.2; replaced by R4.3:
  REST as the only setup); MQTT hint "fallback" (dropped).
- Existing MQTT entries are never switched automatically (A5 applies to
  MQTT). Effects on existing REST entries and entity IDs are disclosed in the
  report (D3).

### R8 – Final audit

- Synchronise `quality_scale.yaml`, the evidence matrix, README and contract;
  assess K7 (discovery for REST): check discovery with a prefilled address and
  the vendor default password, or reclassify the rule with justification after
  removing MQTT.
- Repeated race suites incl. REST, reference and latest jobs green.

### Later (not part of this plan)

Remove MQTT only after R2–R8, after approval and after a transition version:
remove `coordinator.py`, `mqtt_transport.py`, `discovery.py`, the MQTT flow
steps and the MQTT tests; platforms and device model stay unchanged (goal 5).

## 9. Acceptance matrix

| Scenario | Expectation |
|----------|-------------|
| Long idle | only uptime 60 s and device list 30 min; no device status query |
| Stream silent for 15 min | reconnect with snapshot, entities stay available |
| Gateway closes connections at once | backoff with jitter, no tight loop |
| Reload/unload | no open reader, no timers, no tasks |
| Snapshot after a telegram on the same connection | the telegram counts (order) |
| 30 min list during a telegram | live state stays; metadata does not jump back |
| Start | state from the stream's first list within 30 s; configuration from one `GET /devices` right after |
| Position report `unknown` | last position stays, no confirmation |
| Delayed end position (full run, actuator overrun) | no extra query, no timer end position as feedback; the end report is applied at once on receipt |
| Stop or intermediate position | position report without overrun, applied at once |
| Button sequence | exactly one event update per telegram, no repetition from lists |
| REST-only without MQTT integration | entry loads, controls, unloads |
| MQTT entry | behaviour and traffic unchanged |
| ACK 201 | no assumption, no query |
| Command timeout | only this action failed, gateway available, no retry |

## 10. Open assumptions and decisions

| No. | Open | Resolution |
|-----|------|------------|
| O-1 | Does Home Assistant start the integration without a configured MQTT integration with `after_dependencies`, and does Hassfest then accept the `mqtt` discovery key? | **Settled in R2** (`phases/phase-16.md` section 5): yes; `manifest.json` changed |
| O-2 | Effect and support of the stream parameters (incl. whether `levelOfDetail=high` adds the configuration block, R5-H1) | hardware comparison, separate approval |
| O-3 | Cause of the end-position delay: explained by the upstream note as actuator overrun (section 3.3); confirmation on the maintainer's installation open | R1 |
| O-4 | Should the position estimate (A-5) stay visible, and how is the 5 s grace period handled for an overrun > 5 s? | **Decided** 2026-10-20 (R6b, 1a + 2a): origin in REST diagnostics only, grace period unchanged; revisit 2b/2c after R1 |
| O-5 | REST as the default for new entries | **Decided** (D4): REST only for new entries (R4.3) |
| O-6 | Which commit ran in the field test? | R1 (A-4) |
| O-7 | Discovery for REST gateways | R8 (MQTT discovery leads to the REST form since R4.3) |
