# REST phase 02 – domain model (in-place update, run duration, position origin, regressions)

- **Status:** implemented on branch `feat/rest-only-r6`, not yet merged. CI
  pending (section 5). No version change, no release.
- **Source:** port of the earlier fork phase R6 (R6a + R6b) onto the REST-only
  base; maintainer decision for the visible part: **1a + 2a** (section 6).
- **Base:** `feat/rest-only-r3-r5` (REST phase 01), PR base
  `feature/rest-only`.

## 1. Scope

In scope: E6/B-4 in-place update of known devices with a field
classification test; `lastVerticalMovementTime` as a diagnostic value; the
origin of each cover position as a diagnostic value; regression tests R7
(rocker → `unknown` → end position) and R8 (6x press/release, long press).

## 2. Changes

| Change | Before | After |
|--------|--------|-------|
| Device list for a known device | a **new** `EnOceanDevice` was created and runtime fields were copied one by one (B-4: a new field could be forgotten; a command holding the old object wrote `last_command_error` into the void) | the existing object is updated in place: `EnOceanDevice.apply_listed_metadata` replaces only `LISTED_METADATA_FIELDS` (friendly ID, EEPs, manufacturer, physical device, first/last seen, dBm); newer telegram metadata is kept as before (B-5) |
| Field list | implicit in the copy code | `LISTED_METADATA_FIELDS` plus the runtime set in the test; a new dataclass field fails `test_every_device_field_is_classified` until it is classified |
| Last vertical run duration | not read | `configuration.parameters` key `lastVerticalMovementTime` kept per device and channel, shown in diagnostics as `last_vertical_movement_time`; never used for estimates, timers or queries |
| Origin of the position (1a) | not visible | diagnostics show per cover channel `position_origin`: `device list`, `reported` (device report or `device` document), `estimated` (foreign `to` command, or an entity estimate that changed the position after the last record), `assumed` (travel estimate ended after movement time + 5 s); `unknown` keeps position and origin. No entity attribute, no state change |
| Grace period (2a) | 5 s | unchanged |

## 3. Guardrails

| ID | Effect |
|----|--------|
| A1 (no additional radio polling) | no request, interval or retry added; the run duration comes from the list that is already read |
| A2 (uptime never confirms device state) | unchanged |
| A3 (no global cooldowns or write retries) | unchanged |
| A5 (identities preserved) | no unique ID, entity ID, device identifier or registry change |

## 4. Files and tests

| File | Change |
|------|--------|
| `enocean_device.py` | `LISTED_METADATA_FIELDS`, `apply_listed_metadata` |
| `coordinator.py` | in-place update in `_apply_device_object` (new argument `position_origin`); `last_vertical_movement_times`, `_record_last_movement_times`; `_position_origins`, `_note_position_origin`, `position_origin()`; recording in `_handle_telegram`, `_handle_document` and `_finish_cover_movement`; cleanup in `async_forget_device` |
| `protocol.py` | `KEY_LAST_VERTICAL_MOVEMENT_TIME`, `last_vertical_movement_times()` |
| `diagnostics.py` | device keys `last_vertical_movement_time` and `position_origin` |
| `tests/test_coordinator_domain_model.py` | **new**: field classification, same object with runtime state kept, error on a held device, missing metadata kept, no button replay, late profiles discovered once, run duration parsing and storage, diagnostics without extra request, position origin (list, report, `unknown`, foreign command, entity estimate, elapsed movement, `device` document, forget, diagnostics), R7, R8 |
| `tests/test_coordinator_devices.py` | two cases pinned the old behaviour (`updated is not original`); they now assert the in-place update |
| `README.md`, `protocol-contract.md` sections 7 and 12 | documentation |

The key name `lastVerticalMovementTime` comes from an upstream note and is
**[unverified]** on the maintainer's gateway; without it diagnostics show
`{}` and nothing else changes.

## 5. Commands and results

| Command | Result |
|---------|--------|
| `ruff check .`, `ruff format --check .` | passed |
| strict mypy over the REST modules (typed stand-ins) | 0 findings in the changed modules |
| unit cases of `test_coordinator_domain_model.py` without Home Assistant (scratch harness) | 17 passed |

| Run | Head | Result |
|-----|------|--------|
| pending | – | – |

## 6. Decision O-4 (maintainer, 2026-10-20): 1a + 2a

On full runs the D2-05 actuator overruns 3.5–6 s and reports its end position
only when it switches off. The integration ends its travel estimate after
movement time + 5 s (`COVER_MOVEMENT_GRACE_SECONDS`) and then shows the target
as an *assumed* position until the real report arrives (≈0–1 s later).

- 1a: the origin of the position is shown in diagnostics only.
- 2a: the 5 s grace period stays. Revisit a longer or derived grace period
  after the overrun measurement (R1) and after reading
  `last_vertical_movement_time` from real diagnostics.

## 7. Residual risks

- Code that relied on a new device object after a list (none found) would now
  see the same object; tests pin the behaviour.
- `estimated` is derived: a position that differs from the last recorded
  value counts as estimated. The diagnostic value never feeds back into state.
- The run duration key is unverified on hardware (section 4).

## 8. Next step

PR base `feature/rest-only`, after REST phase 01. Then the REST-only rework of
the quality documents and the switch of `main`.
