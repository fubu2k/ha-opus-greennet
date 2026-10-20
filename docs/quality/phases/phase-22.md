# Phase 22 – REST plan R6a: domain model (in-place update, run duration, regressions)

- **Status:** implemented on branch `feat/opus-r6-domain-model`, not yet
  merged. CI pending (section 5). No version change, no release; `main`
  untouched.
- **Plan:** `Implementations-Plan-Rest-API.md` phase R6, design decision E6,
  finding B-4, section 3.3 (run duration), section 2.7 (test gaps R7/R8).
  Guardrails per decision D3: A1–A5 for the MQTT path and shared code, best
  practices for REST.
- **Split:** R6 is split. R6a (this report) changes no visible behaviour.
  R6b (origin of the position, grace period O-4) changes what users see and
  therefore waits for the maintainer's decision (section 8).

## 1. Scope and base

| Field | Value |
|-------|-------|
| Branch | `feat/opus-r6-domain-model` (new) |
| Base | `feature/rest-api-integration` `da80334` ("Merge pull request #32") |
| Home Assistant reference | `2026.10.2` (unchanged) |

In scope: E6/B-4 in-place update with a field classification test;
`lastVerticalMovementTime` as a REST diagnostic value; regression tests R7
(rocker → `unknown` → end position) and R8 (6× press/release, long press).

Out of scope (R6b): position origin `reported`/`estimated`/`assumed`, the
5 s grace period.

## 2. Changes and guardrails

| Change | Before | After |
|--------|--------|-------|
| REST device list for a known device | a **new** `EnOceanDevice` was created and runtime fields were copied one by one (B-4: a new field could be forgotten; a command holding the old object wrote `last_command_error` into the void) | the existing object is updated in place: `EnOceanDevice.apply_listed_metadata` replaces only `LISTED_METADATA_FIELDS` (friendly ID, EEPs, manufacturer, physical device, first/last seen, dBm); B-5 rules unchanged |
| Field list | implicit in the copy code | `LISTED_METADATA_FIELDS` plus the runtime set in the test; a new dataclass field fails `test_every_device_field_is_classified` until it is classified |
| Last vertical run duration | not read | `configuration.parameters` key `lastVerticalMovementTime` kept per device and channel (`last_vertical_movement_times`), shown in REST diagnostics as `last_vertical_movement_time`; never used for estimates, timers or queries |

| ID | Effect |
|----|--------|
| A1 | No request, interval or retry added; the duration comes from the list that is already read |
| A2–A4 | unchanged |
| A5 | No unique ID, entity ID or registry change |
| Shared code | `enocean_device.py` gains a method and a class constant that only the REST coordinator calls; `diagnostics.py` adds the device key only when the coordinator has `last_vertical_movement_times` (REST); MQTT diagnostics keep their exact keys; `protocol.py` (REST helpers) gains a pure parser |

## 3. Files and tests

| File | Change |
|------|--------|
| `enocean_device.py` | `LISTED_METADATA_FIELDS`, `apply_listed_metadata` |
| `rest_coordinator.py` | in-place update in `_apply_device_object`; `last_vertical_movement_times`, `_record_last_movement_times`, cleanup in `async_forget_device` |
| `protocol.py` | `KEY_LAST_VERTICAL_MOVEMENT_TIME`, `last_vertical_movement_times()` |
| `diagnostics.py` | REST-only device key `last_vertical_movement_time` |
| `tests/test_rest_domain_model.py` | **new**: field classification, same object with runtime state kept, error on a held device, missing metadata kept, no button replay, late profiles discovered once, run duration parsing (values, channel, invalid input), stored but never used as state, diagnostics without extra request; R7 sequence; R8 6× press/release with one write each and no replay after a resync; long press |
| `README.md`, `protocol-contract.md` 12.4, plans | documentation |

The key name `lastVerticalMovementTime` comes from the upstream note
(plan section 3.3) and is **[unverified]** on the maintainer's gateway; if
the key is absent, diagnostics show `{}` and nothing else changes.

## 4. Traffic

Unchanged for MQTT and REST: no request added or removed.

## 5. Commands and results

| Command | Result |
|---------|--------|
| `ruff check .`, `ruff format --check .` | passed |
| strict mypy over the REST modules (typed stand-ins, as phase 18) | 0 findings |
| unit cases of `test_rest_domain_model.py` without Home Assistant plus R5 cases (scratch harness) | 25 passed |

| Run | Head | Result |
|-----|------|--------|
| pending | – | – |

## 6. Registry and identity compatibility

No unique ID, entity ID, device identifier, translation key or registry
setting changed.

## 7. Residual risks

- Code that relied on getting a new device object after a list (none found
  in the integration) would now see the same object; tests pin the
  behaviour.
- The run duration key is unverified on hardware (section 3).

## 8. Decision template R6b (O-4) for the maintainer

Background (plan section 3.3): on full runs the D2-05 actuator overruns
3.5–6 s and reports its end position only when it switches off. The
integration ends its travel estimate after movement time + 5 s
(`COVER_MOVEMENT_GRACE_SECONDS`, shared with MQTT) and then shows the target
as an *assumed* position until the real report arrives (≈0–1 s later).

**Question 1 – origin of the position (A-5).** Show where the current
position comes from?

| Option | Effect |
|--------|--------|
| 1a: diagnostics only (plan default) | REST diagnostics show per channel `position_origin` = `reported` / `estimated` / `assumed` / `device list`; no entity change |
| 1b: also as an entity attribute | visible in the UI and automations; new attribute, no ID change |
| 1c: nothing | status quo |

**Question 2 – grace period on REST.**

| Option | Effect | Traffic |
|--------|--------|---------|
| 2a: unchanged 5 s | assumed end position may appear up to ≈1 s before the real report | unchanged |
| 2b: REST-only constant, e.g. 7 s | the real report usually arrives before the fallback; covers show "opening/closing" ≈2 s longer when no report arrives | unchanged; status checks shift later, never more |
| 2c: derive from `lastVerticalMovementTime − verticalMovementTime` (bounded) | adapts per actuator; needs the key on hardware (section 3) | unchanged; status checks shift, never more |

MQTT stays on 5 s in every option (A1–A5). Recommendation: **1a + 2a** now;
revisit 2b/2c after R1 (overrun measurement) and after reading
`last_vertical_movement_time` from real diagnostics.

## 9. Next step

PR base `feature/rest-api-integration`. After the merge: record CI and merge;
R6b after the decision; R1 (measurement) and R8 (final audit) remain.
