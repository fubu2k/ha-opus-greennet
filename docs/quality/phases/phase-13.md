# Phase 13 - Final rule audit and report

- **Status:** completed. Branch `fix/opus-q13-event-device-class`
  (maintainer decision 2026-10-20: one branch for the audit documentation and
  the F13-1 fix) merged via PR #13 (merge `2e46bfd`); CI green on the pull
  request and on `main` (section 9). CHANGELOG entry under "Unreleased"
  (approved by the maintainer). No version change, no release. The audit was first prepared as commit
  `011b6e4` on a local branch `docs/opus-q13-quality-acceptance` (plan
  section 15 suggestion); that branch was never pushed and is superseded.
- **Base SHA:** `96510dd52931fac560cef4904ca5fcabda05348a` (`main`,
  2026-10-19 22:08 +0200, "docs: add phase 12 to the unreleased changelog").
  Code identical to `e57cbd2` (PR #12 merge); `e57cbd2..96510dd` changes only
  `CHANGELOG.md`, `implementation-plan.md` and `phases/phase-12.md`.
- **Plan:** `implementation-plan.md` revision 3, section 15 (scope), section 16
  (acceptance matrix), section 17 (gates), section 20 (report structure),
  guardrails A1-A5.
- **Reference:** Home Assistant `2026.10.2`, home-assistant/core
  `8d7fd8b68677c2585be36ec86043308f403875b8` (fixed since phase 8). Rule keys
  and schema: core `7ff0e7fbdea4e079ae3f99e048497f5e8c7e3360`, rechecked
  against `8d7fd8b` in phase 8 (54 rules: bronze 20, silver 10, gold 21,
  platinum 3).

## 1. Scope and result

Plan section 15: re-audit all 54 rules with meaningful implementation, test
and documentation evidence or exact exemption reasons; verify the handle
diagnostic defaults and the actual icon semantics; synchronise YAML,
evidence, README, protocol contract and reports; record hardware gaps; claim a
Platinum-equivalent self-assessment only with complete applicable evidence.

| Item | Result |
|------|--------|
| Rule statuses | 45 done, 9 exempt, 0 todo; **no status changed** |
| Evidence references | every test file and test function named in the evidence matrix exists at the base; every cited README heading exists |
| Code facts | re-read in the code (section 3); all match the matrix |
| Exemptions | each re-justified with the condition that would invalidate it (evidence matrix, new section "Exemption justifications"); YAML comments made explicit |
| Handle diagnostics | `unlock_request` and `mechanics_fault` are `EntityCategory.DIAGNOSTIC` and `entity_registry_enabled_default = False`; normal handle entities unchanged |
| Icon semantics | requested unlock shows `mdi:lock-clock`, never an open lock; unknown mechanics value uses the neutral default `mdi:cog-outline`, never the fault icon |
| Timing baseline | all constants of plan section 2 unchanged |
| Per-entity inventory (plan section 10) | recorded in section 4; RSSI and overlapping handle projections reviewed, no change proposed |
| Finding F13-1 | **closed** in this branch: rocker events use `EventDeviceClass.BUTTON` (section 5) |
| Decision F13-2 | ownership and URLs stay with `kegelmeier` (section 5) |
| Self-assessment | **Platinum-equivalent self-assessment reached** (CI green, section 9); not an official rating |

## 2. Authorised changes and guardrails A1-A5

Authorised: final text and documentation updates, plus the F13-1 fix
approved by the maintainer on 2026-10-20 (`EventDeviceClass.BUTTON` for
rocker event entities, with tests). No change to workflows, `manifest.json`,
translations, `icons.json`, version or CHANGELOG.

| Guardrail | Effect |
|-----------|--------|
| A1 | The device class is a class attribute; it adds no publish, query, subscription, timer, interval or retry. New tests assert that no topic addressed to the rocker is published. The traffic baseline (`tests/test_traffic_baseline.py`) is unchanged. |
| A2 | Availability and health logic untouched. |
| A3 | Serialisation, timeouts and pacing untouched (constants verified, section 3). |
| A4 | Retain handling untouched; retained telegrams still never fire events. |
| A5 | Unique ID `<EAG>_<DEV>`, event types, name (device name, explicit `_attr_name = None`), entity ID, category (none) and default (enabled) unchanged. Existing entries keep enablement, name and icon (tested, enabled and user-disabled). Only `original_device_class` and the frontend default icon change; a user-chosen icon wins. |

## 3. Audit method and code facts

Static audit at the base, in this order:

1. Parsed `quality_scale.yaml`: 54 keys, official order (checked by
   `test_file_uses_exactly_the_official_rule_keys`), 45 done, 9 exempt.
2. Extracted every `tests/<file>::<function>` and alias (`QS`, `TR`, `DI`,
   `IC`, `RI`) reference from the evidence matrix and confirmed that each file
   and each `def <function>` exists. Result: no missing reference.
3. Confirmed every README heading cited by the matrix and by
   `test_documentation_rules_point_to_existing_sections`.
4. Re-read the code facts behind each rule:

| Fact | Location | Found |
|------|----------|-------|
| `PARALLEL_UPDATES = 0` | `binary_sensor.py`, `climate.py`, `cover.py`, `event.py`, `light.py`, `lock.py`, `sensor.py`, `switch.py` | all eight platforms |
| `_attr_has_entity_name = True`, `_attr_should_poll = False` | `entity.py` `OpusGreenNetEntity`, inherited by every entity class | yes |
| No Python icon property, no `_attr_icon` | all modules | none |
| Manifest | `iot_class: local_push`, `requirements: []`, `mqtt` discovery filter, `config_flow: true`, no `quality_scale` key, `codeowners` present | yes |
| No options, reauth or reconfigure flow; no trigger or condition module; no HTTP client | `config_flow.py`, package listing, imports | none present |
| Handle diagnostics | `sensor.py` `OpusGreenNetHandleSensor` (`unlock_request`), `binary_sensor.py` (`mechanics_fault`) | diagnostic, disabled for new entries |
| Icons | `icons.json`: four entity translation keys, four services | matches the phase 9 audit |
| Timing (plan section 2) | `GATEWAY_HEALTH_INTERVAL` 60 s, `RECONNECT_RESYNC_DEBOUNCE_SECONDS` 60, `OUTBOUND_STATE_RECONCILIATION_DELAYS` (5, 20), `COVER_RECONCILIATION_DELAYS` (10.0, 20.0), `COVER_MOVEMENT_GRACE_SECONDS` 5.0, `REQUEST_TIMEOUT` 10, `SUBSCRIPTION_TIMEOUT` 30, `SETUP_REQUEST_TIMEOUT` 30, `SETUP_SUBSCRIPTION_TIMEOUT` 180, `MIN_SEND_INTERVAL` 0.0 | unchanged |

Phase 12 added `LATE_ANSWER_DIAGNOSTIC_WINDOW = 120.0`; it bounds a diagnostic
flag only and never delays or re-routes an answer (phase 12, section 13).

## 4. Per-entity inventory (plan section 10, documentation only)

All entities are push-updated from the gateway streams; none polls. "Icon"
means the source of the frontend icon. Unique IDs follow
`protocol-contract.md` section 8.

| Platform / key | Condition | Device class | State class | Category | Translation key | Icon | New-entry default |
|----------------|-----------|--------------|-------------|----------|-----------------|------|-------------------|
| light | light EEPs, per channel | – (domain) | – | – | `channel` (multi-channel) or device name | domain | enabled |
| switch | switch EEPs, per channel | – (domain) | – | – | `channel` or device name | domain | enabled |
| cover | D2-05-xx, per channel | `blind` | – | – | `channel` or device name | class | enabled |
| climate | HeatArea D1-4B-05/06/07 | – | – | – | device name | domain | enabled |
| event | rockers F6-02-xx, F6-03-xx | `button` (F13-1, this phase) | – | – | device name | class | enabled |
| lock `autolock` | D2-06-40, read-only | – | – | – | `autolock` | domain | enabled |
| sensor `humidity` | HeatArea, A5-04-xx, OPUS motion | humidity | measurement | – | `humidity` | class | enabled |
| sensor `feed_temperature` | D1-4B-05 | temperature | measurement | – | `feed_temperature` | class | enabled |
| sensor `energy_consumption` | D1-4B-07 | power (kW) | measurement | – | `power_consumption` | class | enabled |
| sensor `temperature` | A5-04-xx, OPUS motion | temperature | measurement | – | `temperature` | class | enabled |
| sensor `illuminance` | SMS A5-07-01/03 | illuminance | measurement | – | `illuminance` | class | enabled |
| sensor `supply_voltage` | SMS | voltage | measurement | diagnostic | `supply_voltage` | class | enabled |
| sensor `battery_level` | SMS, A5-04-xx, OPUS motion | battery, display precision 0 | measurement | – | `battery_level` | class | enabled |
| sensor `handle_state` | window handles | enum (`open`, `closed`, `tilted`) | – | – | `handle_state` | `icons.json` | enabled |
| sensor `unlock_request` | D2-06-40 | enum (`requested`, `not_requested`) | – | diagnostic | `unlock_request` | `icons.json` | **disabled** |
| sensor `signal_strength` | every device | signal strength (dBm) | measurement | diagnostic | `signal_strength` | class | **disabled** |
| binary_sensor `motion` | SMS, OPUS motion | motion | – | – | `motion` | class | enabled |
| binary_sensor `handle_open`, `handle_tilt` | window handles | window | – | – | same | class | enabled |
| binary_sensor `handle_closed` | window handles | – (ON means closed) | – | – | `handle_closed` | `icons.json` | enabled |
| binary_sensor `mechanics_fault` | D2-06-40 | problem | – | diagnostic | `mechanics_fault` | `icons.json` | **disabled** |
| binary_sensor `smoke_alarm` | F6-05-02 | smoke | – | – | `smoke_alarm` | class | enabled |
| binary_sensor `battery_low` | F6-05-02 | battery | – | – | `battery_low` | class | enabled |
| binary_sensor `liquid_detected` | F6-05-01 | moisture | – | – | `water_leak` | class | enabled |
| binary_sensor `window_open` | HeatArea | window | – | – | `window` | class | enabled |
| binary_sensor `actuator_not_responding`, `missing_temperature` | HeatArea | problem | – | diagnostic | same | class | enabled |
| binary_sensor `actuator_low_battery` | D1-4B-05 | battery | – | diagnostic | `actuator_battery` | class | enabled |
| binary_sensor `actuator_deactivated` | D1-4B-05 | problem | – | diagnostic | same | class | enabled |
| binary_sensor `circuit_in_use` | D1-4B-06 | problem | – | diagnostic | same | class | enabled |
| binary_sensor `contact` | D5-00-01 | opening | – | – | `contact` | class | enabled |

Review of the candidates named in plan section 10 (no automatic disablement):

- **RSSI (`signal_strength`):** already diagnostic and disabled for new
  entries; it is updated from the `dbm` value that arrives with ordinary
  telegrams, never by a query. No change proposed.
- **Supply voltage:** decided in phase 6 part 2 (diagnostic, enabled). No
  change proposed.
- **Overlapping handle projections** (`handle_state` plus `handle_open`,
  `handle_tilt`, `handle_closed`): all four derive from one normalised value,
  cost no traffic and serve different automation styles (enum versus
  binary triggers). Disabling any of them would change existing user setups
  for new installations without a functional gain. No change proposed; the
  maintainer may decide otherwise in a separate phase.
- **HeatArea problem sensors** stay enabled: they report real heating faults
  and the plan forbids generalising the mechanics-fault decision.

## 5. Findings and open maintainer decisions

| ID | Finding | Rule impact | Proposal |
|----|---------|-------------|----------|
| F13-1 | Rocker event entities had no device class; `EventDeviceClass.BUTTON` fits (deferred by phase 9 to the metadata review, not decided there). | `entity-device-class` asks for classes "where possible". | **Closed** (maintainer approval 2026-10-20): `event.py` sets `_attr_device_class = EventDeviceClass.BUTTON`; tests in `tests/test_event_device_class.py`. Frontend default icon becomes the button icon. |
| F13-2 | `manifest.json` `codeowners`, `documentation`, `issue_tracker` and several README links point to `kegelmeier/ha-opus-greennet`, while the plan's target is `fubu2k/ha-opus-greennet`. | `integration-owner` is satisfied (a code owner is declared). | **Decided** (maintainer, 2026-10-20): stays with `kegelmeier`, because the code is to be handed over to the upstream maintainer later. No change. |
| F13-3 | All D2-05 covers use `CoverDeviceClass.BLIND`. Roller shutters without slats would fit `shutter`, but the profile cannot tell them apart and supports an angle. | None: a valid class is set. | Keep; revisit only with a reliable hardware distinction. |
| F13-4 | `mechanics_fault` `off` and unknown share the neutral `mdi:cog-outline` (hassfest forbids a state icon equal to the default). The state text still differs ("OK" versus unknown). | None. | Keep (documented in phase 9). |

No finding is a correctness or guardrail violation.

## 6. Acceptance matrix (plan section 16)

Status from existing tests and code; no new evidence produced in this phase.

| Scenario | Evidence |
|----------|----------|
| Long idle, gateway health tick | `test_traffic_baseline.py::test_gateway_lifecycle_traffic`, `::test_frozen_timing_baseline` |
| Discovery/setup race | `test_discovery.py` (phase 9) |
| Diagnostic activation | `test_handle_diagnostics.py::test_activating_diagnostics_sends_no_device_request` |
| Icon update/render | `test_icon_translations.py` (no MQTT; backend only, no rendered frontend) |
| Feedback before queued reconciliation, superseding command | `test_background_efficiency.py`, `test_reconciliation_feedback.py` |
| Timeout/negative ACK | `test_release_transport_safety.py`, `test_coordinator_transport.py` |
| ACK 201 | `test_ack_device_health.py` (phase 11) |
| Reconnect burst | `test_traffic_baseline.py`, `test_ha_lifecycle.py` |
| Old snapshot after live field | `test_field_freshness.py` (phase 10) |
| Silent sensor/handle | no heartbeat rule exists; `test_ack_device_health.py` |
| New/existing handle entries | `test_handle_diagnostics.py::test_new_handle_registers_disabled_diagnostics`, `::test_existing_diagnostic_entries_keep_user_settings` |
| Request versus lock, unknown mechanics | `test_icon_translations.py::test_unlock_request_never_shows_an_unlocked_lock`, `::test_missing_data_uses_a_neutral_fallback` |
| Unsupported icon candidate | `script/check_icons.py` in CI (phase 9 rejected `mdi:cog-alert`) |
| Unload/removal/bridge down | `test_device_removal.py`, `test_ha_lifecycle.py`, `test_late_responses.py` |

## 7. Files

| File | Change |
|------|--------|
| `custom_components/opus_greennet/event.py` | import `EventDeviceClass`; `_attr_device_class = EventDeviceClass.BUTTON` on `OpusGreenNetEvent` (F13-1) |
| `tests/test_event_device_class.py` | **new**: class, unique ID, event types, category and default of the entity (F6-02-01, F6-03-01); new rocker registers the class with no request to the device; existing entry (enabled and user-disabled) keeps entity ID, enablement, name and icon, is not duplicated, and no request is sent |
| `custom_components/opus_greennet/quality_scale.yaml` | header records the phase 13 audit; explicit comments for `appropriate-polling`, `docs-conditions`, `docs-triggers`, `docs-configuration-parameters`, `inject-websession`; `entity-device-class` comment. Statuses unchanged. |
| `docs/quality/quality-scale-evidence.md` | status section (audit base, method, F13-1, result); `appropriate-polling`, `entity-device-class` and `entity-disabled-by-default` rows cite the additional tests; new sections "Exemption justifications" and "Limits of this evidence" |
| `docs/quality/protocol-contract.md` | section 8: event row names the `button` device class |
| `docs/quality/implementation-plan.md` | section 6: phase 6 metadata row completed (PR #10); section 7: phase 13 status; section 10: inventory pointer; section 15 heading |
| `README.md` | Supported Device Types: event device class `button`; test list entry |
| `docs/quality/phases/phase-13.md` | **new**, this report |

Historical phase reports are left as recorded (plan section 6).

## 8. Gateway/cache versus radio counts

Unchanged. The only code change is a class attribute; it publishes nothing.
The phase 8 traffic baseline remains the reference.

## 9. Commands and results

| Check | Result | Origin |
|-------|--------|--------|
| YAML parse, 54 keys, statuses 45 done / 9 exempt, every exempt comment at least 20 characters | passed | local, Python 3.13 |
| Evidence rows equal YAML statuses (same regular expression as `test_evidence_matrix_lists_every_rule_with_its_status`) | passed (54 rows) | local |
| Every referenced test file and function exists | passed | local script |
| `ruff check .`, `ruff format --check .` | passed (99 files) | local, ruff 0.16.9 (CI pins 0.16.3) |
| Strict mypy, pytest (including `tests/test_event_device_class.py`) with coverage gates, three race repetitions, hassfest | **not executed locally**: the authoring environment cannot reach the Python package index | – |
| Last full CI on identical code | passed: run `39315310796` on `main` head `e57cbd2` (fixed reference 2026.10.2, latest stable, reference hassfest, hassfest master), recorded in phase 12 section 13.4 | CI |
| pip check, Ruff, strict mypy, full pytest incl. `tests/test_event_device_class.py`, total and per-module coverage gates, three race repetitions, traffic baseline | passed | CI, job "Home Assistant 2026.10.2 (fixed reference)" `118475537221`, run `39376442882` (pull request #13, head `81feaf1`), 2026-10-20 07:43–07:52 +0200 |
| Same gates against the latest stable release | passed | CI, job "Home Assistant (latest stable)" `118475536781`, same run |
| Hassfest built from core `8d7fd8b` and icon validation | passed | CI, job "Hassfest at Home Assistant 2026.10.2 (fixed reference)" `118475536938`, same run |
| Hassfest `@master` (moving) | passed | CI, job "validate" `118475536930`, same run |
| All four jobs on `main` after the merge (head `2e46bfd`, identical code) | passed | CI, run `39376460361` (push), 2026-10-20 07:43–07:53 +0200; jobs `118475592867` (reference), `118475592839` (latest stable), `118475592609` (reference Hassfest), `118475592816` (validate) |

Tests that can react: `tests/test_quality_scale.py` (schema, statuses,
evidence rows, README headings), the new `tests/test_event_device_class.py`,
and the event and translation suites (`test_event_entity.py`,
`test_event_lifecycle.py`, `test_translations.py`), which the device class
does not affect by design (name stays the device name). No test result is
claimed from the local environment; the CI runs above are the results of
record.

## 10. Registry and identity compatibility

Rocker event entities gain `original_device_class = button`. Unique ID,
entity ID, name, category, default, event types, translations and device
assignment are unchanged; an existing entry keeps `disabled_by`, its custom
name and its custom icon (tested). No other entity changed.

## 11. Hardware observations, unknowns and residual risks

- No hardware observation in this phase or any earlier phase.
- Unverified on hardware: MQTT discovery (`friendlyId` publication per
  firmware), ACK 201 delivery, frequency of late answers (phase 12), radio
  and battery effects of gateway requests. MQTT publish counts are not radio
  counts.
- Frontend rendering of icons and diagnostic placement was not inspected.
- The exemptions are this repository's reasoning; an official review may
  differ. The integration is not officially rated.

## 12. Review status and next step

Completed: merged via PR #13 (`2e46bfd`), CI green on the pull request and
on `main`, CHANGELOG "Unreleased" updated with maintainer approval. This
closes the quality series (phases 1-13). The Platinum-equivalent
self-assessment is reached; it is not an official rating. Versioning and
release remain separate decisions. Remaining gaps are hardware observations
only (section 11).
