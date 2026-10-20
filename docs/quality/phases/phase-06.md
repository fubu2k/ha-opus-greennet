# Phase 6 (resumed) - Handle diagnostics and entity metadata review

This report has two deliveries:

1. **Handle diagnostics** (part 1):
   completed, merged via PR #2.
2. **Metadata review** (section "Part 2 - Metadata review"): SMS supply
   voltage diagnostic and whole-percent battery display. Implemented on branch
   `fix/opus-q06-metadata`, awaiting CI and review, not merged.

# Part 1 - Mandatory handle diagnostics

- **Status:** completed. CI green; approved by the maintainer on 2026-10-19 and
  merged via PR #2. No version change, no release.
- **Branch:** `feat/opus-q06-entity-metadata` (created for this phase)
- **Base SHA:** `feabed53b5761104e9ed917c0dc44b2afb584df7` (`main`, 2026-10-19
  15:12 +0200, "Add files via upload")
- **Plan:** `implementation-plan.md` revision 3, section 10 "Mandatory
  window-handle diagnostics". The remaining metadata review of section 10 and
  the icon work of section 11 were **not** part of this delivery (icons were
  delivered in phase 9; the metadata review is continued in part 2).
## Changes since the reference baseline

Reviewed main HEAD in the plan: `63c7380`. Commits since then:

- `3e7ae0b` (2026-10-19): implementation plan revision 3 (documentation only).
- `feabed5` (2026-10-19): uploaded copies of integration files (`*.py`,
  `strings.json`, `manifest.json` with version `0.7.0b1`, `translations/`,
  `brand/`, `quality_scale.yaml`) into the **repository root**. Home Assistant
  loads only `custom_components/opus_greennet/`; the root copies are not loaded
  and differ from it in `binary_sensor.py`, `binary_sensor_descriptions.py`,
  `const.py`, `enocean_device.py`, `sensor.py`, `strings.json` and
  `manifest.json` (for example temperature/humidity sensors and an OPUS motion
  detector). **Decision (maintainer, 2026-10-19): accidental upload, removed
  on this branch.** All 28 files added by `feabed5` are deleted; none existed
  before it. Reason: hassfest found a second `manifest.json` at the repository
  root and failed with `[MANIFEST] Domain does not match dir name`
  ("Integrations: 2"). The integration itself stays at its previous code plus
  this phase. A later integration update to the 0.7.0b1 content would be a
  separate phase in `custom_components/opus_greennet/`.

## Scope and authorised behaviour change

Only the two AutoLock troubleshooting entities of D2-06-40 handles change:

| Entity | Unique ID | Category | New-entry default |
|--------|-----------|----------|-------------------|
| `unlock_request` sensor (Entsperranfrage) | `<EAG>_<DEV>_unlock_request` | `EntityCategory.DIAGNOSTIC` | `entity_registry_enabled_default = False` |
| `mechanics_fault` binary sensor (Mechanik-Fehler) | `<EAG>_<DEV>_mechanics_fault` | `EntityCategory.DIAGNOSTIC` | `entity_registry_enabled_default = False` |

Verified before editing (code at the base SHA):

- `unlock_request` is created in `sensor.py` by `OpusGreenNetHandleSensor`
  only when `device.is_window_handle and device.has_autolock` (D2-06-40).
- `mechanics_fault` is created in `binary_sensor.py` by the shared
  `OpusGreenNetStateBinarySensor` under the same condition, device class
  `PROBLEM`.
- Wire mappings in `enocean_device.py` (`unlockRequest`/`unlock`,
  `mechanicsFault`/`mechanics`) and `KNOWN_STATE_KEYS` are unchanged.

### Code

- `sensor.py`: `OpusGreenNetHandleSensor` sets `_attr_entity_category` and
  `_attr_entity_registry_enabled_default` only for the `unlock_request` suffix.
  `handle_state`, its options and all values are unchanged.
- `binary_sensor.py`: `OpusGreenNetStateBinarySensor` gains keyword-only
  `entity_category` (default `None`) and `enabled_by_default` (default `True`).
  Only the `mechanics_fault` construction passes `DIAGNOSTIC`/`False`; motion,
  smoke alarm and battery-low keep their previous metadata. This mirrors the
  existing `OpusBinarySensorDescription` fields.
- No change to parsing, coordinator, transport, timing, topics, services,
  translations, unique IDs, device assignment or the read-only AutoLock entity.

## Registry semantics (A5)

Checked against Home Assistant source, tag `2026.10.0`
(`6a811d3359c7b2076dc9e1cf900843a129c044af`):

- `entity_platform.py`: `disabled_by = INTEGRATION` is derived from
  `entity_registry_enabled_default` and passed to `async_get_or_create`.
- `entity_registry.py` `async_get_or_create`: for an **existing** entry it calls
  `_async_update_entity` without `disabled_by`, so the user's enablement is
  kept; `entity_category` is integration-owned and is updated to `DIAGNOSTIC`.
  User-owned `name`, `icon`, `entity_id`, area and labels are not passed and
  stay unchanged. A previously deleted entry restores its own `disabled_by`.
- Consequence: existing installations keep both entities exactly as configured
  (enabled stays enabled, user-disabled stays disabled); only new registry
  entries start disabled. Nothing is deleted; both entries stay in the registry
  and can be activated for troubleshooting.

## Guardrails A1-A5

| ID | Effect |
|----|--------|
| A1 | No new publish, query, poller, interval or retry. Category and enablement are registry metadata; disabled entities simply do not consume pushed updates. Tests assert no topic addressed to the handle on creation, on rediscovery with existing entries, on activation/reload and on later pushed updates. |
| A2 | Availability logic untouched. Hidden diagnostics do not mark a handle unavailable. |
| A3 | No timeout, queue or cooldown change. |
| A4 | Retain handling untouched. |
| A5 | Identities, entity IDs, device assignment and user enablement preserved (see above and tests). |

Battery: hiding these push-based entities is **not** claimed as a battery
saving. No hardware observation was made.

## Tests

New `tests/test_handle_diagnostics.py`:

- `test_entity_metadata_is_limited_to_the_two_diagnostics`: both diagnostics
  have `DIAGNOSTIC`/`False`; unique IDs, enum options and `PROBLEM` class are
  unchanged; `handle_state`, a handle position sensor and motion keep `None`/`True`.
- `test_new_handle_registers_disabled_diagnostics` (D2-06-40, F6-10-00,
  D2-03-10): new D2-06-40 entries are `disabled_by=INTEGRATION`, diagnostic and
  not loaded; position sensors and AutoLock stay enabled, uncategorised and show
  the reported values; other handle profiles get no AutoLock diagnostics; no
  request to the handle is published.
- `test_existing_diagnostic_entries_keep_user_settings` (enabled,
  user-disabled): pre-existing entries keep entity ID, unique ID, enablement,
  custom name and icon; no duplicate entry; category becomes diagnostic; enabled
  entries show pushed states; an invalid mechanics value is `unknown`, not `off`;
  no request to the handle.
- `test_activating_diagnostics_sends_no_device_request`: user activation plus
  reload loads both entities with the reported values; later pushed updates
  arrive; no request to the handle.

Adjusted `tests/test_gateway_payload_compatibility.py::
test_hoppe_original_keys_reach_position_entities`: for D2-06-40 it now
pre-registers both diagnostics as existing enabled entries (an upgraded
installation) before discovery, so it still verifies the HOPPE `unlock` and
`mechanics` key mapping on live entities. Its assertions are unchanged.

## Gate results

| Gate | Result | Origin |
|------|--------|--------|
| `ruff check .` | passed | local, ruff 0.16.3 |
| `ruff format --check .` | passed (91 files) | local, ruff 0.16.3 |
| Python 3.14 byte-compilation of changed files | passed | local |
| `python -m mypy` | passed | CI run `39260406905`, job "Home Assistant (latest stable)" |
| `pytest -v --cov ...`, `script/check_coverage.py` | passed | CI run `39260406905` |
| Three lifecycle/race repetitions | passed | CI run `39260406905` |
| hassfest | failed on run `39260088527` (root `manifest.json` from `feabed5`); passed on run `39260406905` after removing the root copies | CI, job "validate" |

Tests were not executed locally for this phase. The authoritative run is CI
run `39260406905` on the pull request at head
`7797c0bc33eab3ece3a8e2431eaed90155ccf538`
(`.github/workflows/tests.yml`): both jobs, "Home Assistant (latest stable)"
and "validate" (hassfest), concluded `success`, confirmed by the maintainer.
That job covers ruff, mypy, the full pytest suite with the total and
per-module coverage gates, and the three lifecycle/race repetitions. The exact
Home Assistant patch version and coverage figures are in the CI log, which was
not retrievable from the authoring environment. A fixed 2026.10.x reference job
does not exist yet (phase 8).

## Documentation updated

- `README.md` (HOPPE window handles): defaults, activation path, preservation
  of existing settings, no device request, no battery claim, unknown is not
  "no fault".
- `docs/quality/protocol-contract.md` section 8: separate inventory rows for
  the two diagnostics. The former "all non-RSSI entities enabled" baseline is
  historical.
- `docs/quality/quality-scale-evidence.md`: `entity-category` and
  `entity-disabled-by-default` rows extended with this evidence.
- `custom_components/opus_greennet/quality_scale.yaml`:
  `entity-disabled-by-default` comment extended. No rule status changed.
- `docs/quality/implementation-plan.md`: phase 6 status row.

## Not done / residual risks (part 1, as recorded at merge)

- Remaining section 10 metadata review (RSSI, voltage, overlapping handle
  projections, full per-entity inventory) was open; see part 2.
- Icon policy (section 11) was not implemented in part 1; delivered later in
  phase 9 (`phases/phase-09.md`).
- No hardware or frontend visual check; backend tests do not prove rendering.

## Review status (part 1)

CI green and approved by the maintainer; merged into `main` via PR #2. Release
and versioning remain separate; no version or CHANGELOG change was made.

# Part 2 - Metadata review: SMS supply voltage and battery display

- **Status:** implemented, **awaiting CI and review, not merged**.
- **Branch:** `fix/opus-q06-metadata` (created for this delivery)
- **Base SHA:** `8001d8b2cfe657dddfee8eaaa5eae68bd4b15149` (`main`,
  "docs: record phase 11 CI evidence"). Phases 8, 9, 10 and 11 are on `main`
  at this base; nothing in them is changed here.
- **Plan:** `implementation-plan.md` section 10 "Remaining metadata review",
  new subsection "Metadata decisions (added 2026-10-19)".

## Verification of part 1 at the new base

Re-checked on `main` `8001d8b` before editing:

| Requirement (plan section 10) | Evidence at `8001d8b` | Result |
|-------------------------------|-----------------------|--------|
| `unlock_request` diagnostic, disabled for new entries | `sensor.py` `OpusGreenNetHandleSensor` | present |
| `mechanics_fault` diagnostic, disabled for new entries | `binary_sensor.py` (`entity_category=DIAGNOSTIC`, `enabled_by_default=False`) | present |
| Tests for categories, new-entry defaults, preserved enabled/disabled entries, no request on activation | `tests/test_handle_diagnostics.py` (4 tests) | present |
| README, protocol inventory, evidence, `quality_scale.yaml` | README "HOPPE window handles"; contract section 8; evidence rows `entity-category`, `entity-disabled-by-default` | present |
| Icons (section 11) | `icons.json` (phase 9) | present |

Part 1 is fully on `main`. Open from section 10 was only the metadata review.

## Scope and authorised behaviour change (maintainer request, 2026-10-19)

| Entity | Profiles | Change | New-entry default |
|--------|----------|--------|-------------------|
| `supply_voltage` sensor ("Versorgungsspannung") | SMS presence detectors A5-07-01, A5-07-03 | `EntityCategory.DIAGNOSTIC` | enabled (unchanged) |
| `battery_level` sensor ("Batteriestand") | every device that has one: SMS A5-07-01/03, A5-04-01/02/03 temperature/humidity sensors, OPUS motion detector (F6-02-01 + A5-04-01) | `suggested_display_precision = 0` (whole percent) | enabled, no category (unchanged) |

Verified before editing: `supply_voltage` is created only in the
`device.is_presence_detector` branch (A5-07-01/03); `battery_level` is created
only by `OpusGreenNetMeasurementSensor` (SMS branch and the A5-04 branch, which
also covers the OPUS motion detector). No other platform exposes a numeric
battery level (RWM `battery_low` and HeatArea `actuator_low_battery` are binary
sensors and unaffected).

### Code

- `sensor.py`:
  - `OpusGreenNetMeasurementSensor` gains keyword-only `entity_category`
    (default `None`). Only the SMS `supply_voltage` construction passes
    `EntityCategory.DIAGNOSTIC`; illuminance and battery level pass `None`.
  - New constant `MEASUREMENT_DISPLAY_PRECISION = {"battery_level": 0}`; the
    sensor sets `_attr_suggested_display_precision` from it. Keyed by suffix,
    so every battery level sensor is covered, whichever device creates it.
- Unchanged: unique IDs, translation keys, device classes, units, state
  classes, parsing in `enocean_device.py`, coordinator fast path for
  `batteryLevel`, transport, timing, topics, services, translations, icons.

## Registry and display semantics (A5)

Checked against Home Assistant source, tag `2026.10.2`
(`8d7fd8b68677c2585be36ec86043308f403875b8`), `components/sensor/__init__.py`:

- `entity_category` is integration-owned: an existing `supply_voltage` entry
  takes `DIAGNOSTIC` on the next setup (same mechanism as part 1), while
  `disabled_by`, entity ID, name, icon, area and labels stay as the user set
  them. Nothing is disabled or deleted.
- `_update_suggested_precision` stores `suggested_display_precision` as a
  `sensor` registry option. `_display_precision` / `async_rounded_state` use a
  user-set `display_precision` first, so a user choice wins.
- The precision is presentation only: the state machine value (for example
  `87.5`), recorder history and long-term statistics are not rounded.
  Automations with numeric triggers are unaffected.

## Guardrails A1-A5

| ID | Effect |
|----|--------|
| A1 | No new publish, query, poller, interval or retry. Category and display precision are registry metadata. Tests assert no topic addressed to the device on creation and on rediscovery with existing entries. |
| A2 | Availability logic untouched. |
| A3 | No timeout, queue or cooldown change. |
| A4 | Retain handling untouched. |
| A5 | Unique IDs, entity IDs, device assignment, enablement and user customisations (name, icon, display precision) preserved; see tests. |

Battery: the category and display changes are **not** a battery saving and do
not change device traffic. No hardware observation was made.

## Tests

New `tests/test_sensor_metadata.py`:

- `test_measurement_metadata_is_scoped`: `supply_voltage` is diagnostic and
  enabled by default with unchanged unique ID/device class and no precision;
  battery levels (SMS and A5-04) suggest precision 0, no category, enabled;
  illuminance unchanged.
- `test_new_sms_registers_voltage_diagnostic_and_whole_battery`
  (A5-07-01, A5-07-03): new `supply_voltage` entry is diagnostic, enabled and
  shows the reported value; `battery_level` registry option
  `suggested_display_precision == 0`, state stays `87.5`, rounded presentation
  `88`; illuminance and motion keep their metadata; no request to the device.
- `test_every_battery_level_suggests_whole_percent`
  (temperature/humidity A5-04-03, OPUS motion detector): precision 0, no
  category, enabled, no request to the device.
- `test_existing_sms_entries_keep_user_settings` (enabled, user-disabled):
  a pre-existing `supply_voltage` entry keeps entity ID, enablement, custom
  name and icon, becomes diagnostic and is not duplicated; a pre-existing
  `battery_level` entry with a user `display_precision` of 1 keeps it and is
  presented as `87.5`; no request to the device.

Existing tests are unchanged. `test_upstream_backport.py` still asserts that
A5-04 and OPUS motion `battery_level` entries are enabled and uncategorised,
and `test_device_removal.py` still sees the unrounded state `85.0`.

## Gate results

| Gate | Result | Origin |
|------|--------|--------|
| `ruff check .` | passed | local, ruff 0.16.9 (CI pins 0.16.3) |
| `ruff format --check .` | passed (94 files) | local, ruff 0.16.9 |
| `python -m mypy` | **not run locally** | pending CI |
| `pytest -v --cov ...`, `script/check_coverage.py` | **not run locally** | pending CI |
| Three lifecycle/race repetitions | **not run locally** | pending CI |
| hassfest | **not run locally** | pending CI |

The authoring environment had no access to the Python package index, so
Home Assistant, pytest and mypy could not be installed. No test result is
claimed for part 2 until the CI run on the pull request (fixed reference
2026.10.2 and latest stable jobs) is green. Record the run ID here after CI.

## Documentation updated

- `README.md`: supported device table and new subsection "Supply voltage and
  battery level" (diagnostic placement, whole-percent display, display-only
  rounding, user precision wins, no device request).
- `docs/quality/protocol-contract.md` section 8: separate inventory row for
  the SMS `supply_voltage` diagnostic; display precision paragraph.
- `docs/quality/quality-scale-evidence.md`: `entity-category` row extended.
- `docs/quality/implementation-plan.md`: section 6 row, section 7 status,
  section 10 subsection "Metadata decisions".
- `quality_scale.yaml`: no change (no rule status changes).

## Not done / residual risks

- Full per-entity inventory of section 10 (purpose, class, state class,
  category, translation key, icon, default, update frequency) is still open,
  as are the RSSI and overlapping handle projection reviews.
- No frontend visual check: tests verify the registry option and HA's
  `async_rounded_state`, not the rendered dashboard.
- Dashboards: auto-generated dashboards list diagnostic entities separately;
  manually built dashboards that reference the voltage entity are unaffected.
- No hardware observation.

## Review status

Implemented on `fix/opus-q06-metadata`; awaiting CI and maintainer review.
Merge, versioning and CHANGELOG remain separate and are not part of this
change.
