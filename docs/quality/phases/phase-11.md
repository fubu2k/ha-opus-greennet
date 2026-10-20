# Phase 11 - ACK 201 and explicit device health

- **Status:** completed. CI green on the branch and on `main` (section 5).
  Merged via PR #9 (merge `e7c5248`); CHANGELOG entry in 0.7.0b2 (`07f75e9`).
  No version change, no release.
- **Branch:** `fix/opus-q11-ack-device-health`, head `4cd7359`
- **Base SHA:** `1b36a945df79273e9d40a61730fed9e4a0d618b5` (`main`,
  2026-10-19 20:32 +0200, "docs: add phase 10 to the 0.7.0b2 changelog";
  includes the phase 10 merge `49004b7`, PR #8)
- **Plan:** `implementation-plan.md` revision 3, section 13 (scope), guardrails
  A1 (traffic) and A2 (health). Finding O1 from phase 2.
- **Reference:** Home Assistant `2026.10.2`, home-assistant/core
  `8d7fd8b68677c2585be36ec86043308f403875b8` (unchanged from phase 8). Vendor
  reference `opus_mqtt_services_reference.md` section 3.3 ("PUT answer
  behavior": `200` telegram sent, `201` SoftSmartAck, will send later).

## 1. Scope and result

| ID | Before (`1b36a94`) | After |
|----|--------------------|-------|
| O1 | An ACK `201` was handled exactly like `200`: light, switch and cover entities applied the commanded value as an estimate, a cover started its travel estimate (and later assumed the target as its position), and two status queries were scheduled 5 and 20 s later (covers: 10 and 20 s after the estimated movement end). The device had not received the telegram yet. | `async_send_command` returns `CommandDelivery.SENT` (200) or `CommandDelivery.DEFERRED` (201). For `DEFERRED` no estimate is applied, a started travel estimate is cleared, and no status query is scheduled for the command. The deferred values are kept per device, channel and key and listed in diagnostics until a matching live device report or the gateway's outbound telegram shows delivery. |
| H1 | Device-level warnings and errors never affected availability (verified by code reading), but no test pinned this for HeatArea subsystem errors or for one rejected device next to a healthy one. | Unchanged behaviour, now covered by tests (section 3). |

The command states are now distinguished as in the protocol contract,
section 7.1: desired/estimated (`200`), accepted/deferred (`201`), delivered
(outbound telegram) and device-confirmed (live report).

Not changed: wire payloads, channel selectors, STOP payload, timeouts,
serialisation and priorities, the `200` path (estimates, travel estimate,
5/20 s and cover delays), negative-ACK and timeout handling, the outbound
bridge-native path, retained handling and all entity identities.

## 2. Authorised changes and guardrails A1-A5

Authorised by plan section 13: distinguish desired/estimated, accepted/deferred
and device-confirmed states; decide 201 reconciliation and "suppress/reduce
unsuitable checks, never add polling"; keep explicit profile failures separate
from gateway health; test ACK variants, rejection/timeout, late/contradictory
feedback and a healthy gateway with an affected child.

### 2.1 Public compatibility (plan: "assess public compatibility before changing estimates")

- **Visible change, only after ACK `201`:** a switch, light or cover no longer
  shows the commanded value immediately; it keeps the last reported state
  until the device reports or the gateway transmits. The service call still
  succeeds (no new error). After ACK `200` nothing changes.
- **Diagnostics:** each device gains `deferred_commands` (empty unless a `201`
  value is pending).
- **Python API (internal):** the coordinator's command methods return
  `CommandDelivery` instead of `None`; all in-repository callers are updated.
  Entities compare with `is CommandDelivery.DEFERRED`, so any other return
  value (including test doubles) keeps the previous estimate behaviour.
- Climate entities never applied estimates; `climate.py` needs no change. Its
  commands now also schedule no checks after `201`.
- There is no `gateway.py` in this integration; gateway health lives in
  `coordinator.py` and was not modified.

### 2.2 Guardrails

| Guardrail | Effect |
|-----------|--------|
| A1 | Requests can only decrease. A deferred command publishes one PUT and no status query (before: up to three requests). When the gateway transmits, the existing outbound-telegram path schedules its usual two checks once, so a deferred command never exceeds the budget of a transmitted one. The re-arming in `_handle_deferred_command` restores only timers that the command's own movement start had cancelled, for fields of earlier accepted commands, with the frozen delays; it adds no field and no slot. No new poller, interval, subscription or retry. |
| A2 | `coordinator.available`, the health tick, reconnect and probe logic are untouched. ACK `201`, a negative ACK, a timeout and HeatArea subsystem errors change no availability (tests). Gateway uptime is not used anywhere in this phase. |
| A3 | Timeouts, serialisation, priorities and the queue are untouched; no delay, cooldown or write retry. |
| A4 | Retained protection unchanged. Retained values keep their reconciliation accounting exactly as before (phase 10 parity), but can never mark a deferred command as confirmed (`live=False`). |
| A5 | No unique ID, entity ID, name, translation key, device class, category, default or registry entry changed. No entity created or removed. |

### 2.3 Decision on the open phase 10 question

Phase 10 left open whether a discarded stale replay should confirm a command.
Decision: it keeps confirming *reconciliation accounting* (removing that would
add status queries, A1), but it does not count as delivery of a deferred
command. Only live reports and the outbound telegram clear a deferred marker.

## 3. Files and requirements

| File | Change |
|------|--------|
| `custom_components/opus_greennet/coordinator.py` | `ACK_STATUS_DEFERRED`, `NUMERIC_COMMAND_KEYS`, `CommandDelivery`; `async_send_command` evaluates the ACK status and returns the delivery; new `_command_values_by_channel`, `_wire_value_matches`, `_record_command_delivery`, `_clear_delivered_deferred`, `_handle_deferred_command`; `_record_confirmed_feedback(..., live=)` with `live=False` for retained values; outbound telegrams clear matching markers; command wrappers return `CommandDelivery` |
| `custom_components/opus_greennet/enocean_device.py` | `deferred_commands` field (diagnostic, excluded from `repr`/equality); estimate comment |
| `custom_components/opus_greennet/light.py`, `switch.py`, `cover.py` | no estimate after `CommandDelivery.DEFERRED` |
| `custom_components/opus_greennet/diagnostics.py` | `deferred_commands` per device |
| `tests/test_ack_device_health.py` | **New:** 21 test functions, 52 cases |
| `tests/test_cover_stop.py` | the `D2-05-01` case uses ACK `200`; the deferred Stop is covered by the new suite |
| `.github/workflows/tests.yml` | new suite added to both three-fold race repetitions (plan section 17) |
| `docs/quality/protocol-contract.md` | section 2, section 5, section 7 and new section 7.1, section 11 |
| `docs/quality/capability-matrix.md` | audit findings 1 and 3: phase 11 review |
| `docs/quality/implementation-plan.md` | section 7 status |
| `README.md` | acknowledgements 200/201, device health, limitation, test file |
| `docs/quality/phases/phase-11.md` | **New:** this report |

### Regression reproducers (fail on `1b36a94`, pass on the branch, by construction)

| Test | Before | After |
|------|--------|-------|
| `test_deferred_command_keeps_reported_state_and_sends_no_query` (18 actions: switch, multi-channel switch incl. channel 0, dimmer, switching light, cover open/close/position/tilt, channel 1) | estimate applied, checks `[5, 20]`, no `deferred_commands` attribute | state unchanged, no checks, markers recorded |
| `test_deferred_stop_schedules_no_status_check` (channels 0 and 1) | checks `[5, 20]` | none |
| `test_deferred_cover_command_starts_no_travel_estimate` | `movement == "opening"`, checks at 25/35/45 s | no movement, travel timer cancelled |
| `test_send_command_reports_gateway_delivery` | returns `None` | `SENT` / `DEFERRED` |

The before column is derived from the unchanged `200` path on `1b36a94`
(which handled `201` identically) and from `test_state_ownership.py`; the
tests were not executed against the base revision in this environment.

Other new tests: unchanged `200` path, restoration of earlier checks,
rejection and timeout leave no marker, matching versus different live
reports, numeric setpoint confirmation, retained value cannot confirm,
outbound telegram (matching and different value) with the usual check budget,
superseding `200` command per channel, device removal, diagnostics,
HeatArea subsystem errors (`actuatorNotResponding`, `missingTemperature`,
`circuitInUse`, `heaterMode: error`) keep everything available and publish
nothing, a rejected device next to a healthy one, helper contracts.

## 4. Gateway/cache versus radio counts

| Scenario | Before | After |
|----------|--------|-------|
| Command acknowledged with `200` | 1 PUT + up to 2 status queries | unchanged |
| Command acknowledged with `201`, until the gateway transmits | 1 PUT + up to 2 status queries | 1 PUT |
| Gateway later transmits that telegram (outbound telegram observed, no local command waiting) | up to 2 further status queries (bridge-native path) | up to 2 status queries (same path, now the only ones for this command) |
| Deferred Stop | 1 PUT + 2 queries | 1 PUT; the outbound telegram path follows once the Stop is sent |
| Deferred cover command while earlier tilt checks were pending | tilt and position checks rescheduled at the cover delays (2 timers) | tilt checks only at 5/20 s (2 timers), no position check |
| Live/retained stream messages, telegrams, diagnostics | 0 publications | 0 publications |
| HeatArea subsystem error, negative ACK, timeout | no publication, no availability change | unchanged |

`tests/test_traffic_baseline.py` is not modified; its scenarios use ACK `200`.
MQTT counts are not radio transmissions; no hardware claim is made.

## 5. Commands and results

| Check | Result | Origin |
|-------|--------|--------|
| `ruff check .` | passed | local, ruff 0.16.3 (pinned version, binary from the GitHub release) |
| `ruff format --check .` | passed (92 files) | local, ruff 0.16.3 |
| `python -m py_compile` on all integration modules and the new test module | passed | local, CPython 3.14.6 |
| pip check, strict mypy, full pytest with real Home Assistant, coverage gates, hassfest, race repetitions | not executed locally (PyPI is not reachable from this environment) | – |
| pip check, Ruff, strict mypy, full pytest incl. `test_ack_device_health.py`, total and per-module coverage gates, three race repetitions incl. `test_ack_device_health.py` and `test_traffic_baseline.py`, traffic baseline summary | passed | CI, job "Home Assistant 2026.10.2 (fixed reference)" `118245613416`, run `39306729854` (pull_request, PR #9, head `4cd7359`), 2026-10-19 20:56–21:05 +0200 |
| Same gates against the latest stable release | passed | CI, job "Home Assistant (latest stable)" `118245613634`, same run |
| Hassfest built from core `8d7fd8b` and icon validation against the reference MDI catalogue | passed | CI, job "Hassfest at Home Assistant 2026.10.2 (fixed reference)" `118245613500`, same run |
| Hassfest `@master` (moving) | passed | CI, job "validate" `118245613771`, same run |
| All four jobs on `main` after merge and CHANGELOG (`07f75e9`) | passed | CI, run `39306899304` (push), 2026-10-19 20:57–21:06 +0200; jobs `118246335941` (reference), `118246335986` (latest stable), `118246336342` (reference Hassfest), `118246335511` (validate) |

Run `39306768512` for the merge commit `e7c5248` was cancelled by the
workflow's per-branch concurrency when `07f75e9` was pushed; `07f75e9` contains
the same code plus the CHANGELOG entry. Hardware was not tested.

## 6. Registry and identity compatibility

- No unique ID, entity ID, name, translation key, device class, category or
  default enablement changed; no entity is created or removed; existing
  enabled and disabled entries are untouched.
- Diagnostics gain `deferred_commands` per device.

## 7. Documentation updates

Protocol contract (sections 2, 5, 7, new 7.1, 11), capability matrix (audit
findings 1 and 3), README (acknowledgements 200/201, device health, known
limitation, project structure), implementation plan (section 7 status), this
report. The quality-scale evidence matrix is unchanged because no rule status
changes (45 done / 9 exempt / 0 todo).

## 8. Hardware observations, unknowns and residual risks

- **No hardware observation.** Which devices or profiles receive ACK `201` on
  real gateways (the reference describes it as inverse communication, i.e. a
  device that is reached later) is unverified.
- **Superseded queued telegrams.** OPUS answers carry no request identifier.
  Whether the gateway discards a queued telegram when a newer command for the
  same key is transmitted, and how long it keeps one, is not documented. A
  later `200` command removes the integration's marker; the gateway may still
  deliver the older value afterwards. The device report then shows it.
- **Outbound telegram during another command.** An outbound telegram that
  arrives while another local command on the same device awaits its ACK is
  discarded as before (section 5 of the protocol contract); the marker then
  stays until a matching device report.
- **Different reported value.** A report with another value keeps the marker
  (the telegram may still be queued). It is diagnostic only and never becomes
  an assumed state; it is cleared by removal of the device.
- **Bridge-native confirmation (capability matrix finding 1b).** Kept
  unchanged as the only confirmation path after delivery; its radio effect on
  HeatArea actuators remains unknown.

## 9. Review status and next step

Completed: CI green, merged via PR #9, CHANGELOG updated on `main`. Release
authorisation remains separate. Next planned phases: resumed phase 6
(remaining metadata review, section 10) and phase 12 (late-response
ambiguity investigation).
