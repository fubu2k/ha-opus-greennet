# Phase 6 (resumed) - Handle diagnostics: category and safe default exposure

- **Status:** implemented on branch, **pending review**. Not merged, not released.
- **Branch:** `feat/opus-q06-entity-metadata` (created for this phase)
- **Base SHA:** `feabed53b5761104e9ed917c0dc44b2afb584df7` (`main`, 2026-10-19
  15:12 +0200, "Add files via upload")
- **Plan:** `implementation-plan.md` revision 3, section 10 "Mandatory
  window-handle diagnostics". The remaining metadata review of section 10 and
  the icon work of section 11 are **not** part of this delivery.

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
| `python -m mypy` | **not run** | PyPI unreachable in the authoring environment |
| `pytest -v --cov ...`, `script/check_coverage.py` | **not run** | as above |
| Three lifecycle/race repetitions | **not run** | as above |
| hassfest | failed on the first PR run (root `manifest.json` from `feabed5`); root copies removed, rerun pending | CI |

The test suite has not been executed for this phase. CI on the pull request
(`.github/workflows/tests.yml`, latest stable HA) is the first actual run and
its result must be recorded here before review is completed. A fixed
2026.10.x reference job does not exist yet (phase 8).

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

## Not done / residual risks

- Remaining section 10 metadata review (RSSI, voltage, overlapping handle
  projections, full per-entity inventory) is open.
- Icon policy (section 11) is not implemented; no `icons.json`.
- No hardware or frontend visual check; backend tests do not prove rendering.

## Review status

Waiting for CI and maintainer review. Merge and release require separate
approval; no version or CHANGELOG change was made.
