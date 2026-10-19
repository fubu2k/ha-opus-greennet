# Upstream sync - kegelmeier/ha-opus-greennet v0.7.0b1

- **Status:** implemented on branch, **pending CI and review**. Not merged.
- **Branch:** `feat/opus-upstream-v0.7.0b1`
- **Fork base:** `main` at `2a28216` (phases 1-7 and phase 6, PR #2)
- **Upstream:** tag `v0.7.0b1` = `5fcc57c` (2026-10-18). The archive attached
  by the maintainer was no longer readable (temporary 7-Zip folder); the 28
  files uploaded to the repository root in `feabed5` are byte-identical to this
  tag and confirm it as the intended upstream version.
- **Merge base:** `63c7380` (fork PR #1). Upstream had merged this fork's
  quality series itself, so a true three-way merge was possible.
- This is not a numbered phase of `implementation-plan.md`.

## Upstream changes since the merge base

| Upstream commit | Content | Taken over |
|-----------------|---------|------------|
| `485d190`, `3912e68` | A5-04-03 temperature/humidity/battery sensors (#64/#65), -20..60 °C | yes |
| `7577e31`, `88d3d55` | D5-00-01 contact binary sensor (#70/#71) | yes |
| `467f0db` | Keep the established order of binary sensor descriptions | yes (contact appended last) |
| `33c240a`, `b335248` | OPUS motion detector F6-02-01 + A5-04-01 (#72), A5-04-01/02 ranges | yes |
| `409a716`, `8b3c2b8` | Upstream's own combination of these features with this fork's architecture; HA 2026.10.1 minimum | yes |
| `f31cc2b`, `59a7a76`, `5fcc57c` | Version bumps and CHANGELOG for v0.6.0, v0.7.0b0, v0.7.0b1 | **no** (see below) |

The retained-answer fix #73 (`3e52fce`) was already part of the fork.

## How the upstream logic fits the fork architecture

Upstream had already rewritten its features against this fork's model in
`409a716`/`8b3c2b8`. Each change was reviewed against the fork's patterns:

- **Typed readers instead of `getattr`** (phase 5): temperature is a new entry
  in `MEASUREMENT_READINGS`; the contact uses `channel_boolean` in an
  `OpusBinarySensorDescription` (key `contact`, device class `OPENING`).
- **Wire boundary** (phase 5): secondary EEPs are read with `wire.string_key`,
  so malformed entries are skipped instead of raising.
- **Domain model owns state** (phase 2): `EnOceanChannel.contact`, profile
  predicates (`is_contact_sensor`, `is_temperature_humidity_sensor`,
  `is_opus_motion_detector`) and per-profile temperature ranges
  (`A5_04_TEMPERATURE_RANGES`) live in `enocean_device.py`/`const.py`; entities
  only project.
- **Unknown is not a value** (protocol contract): unrecognised contact values
  become `None` via `_parse_enum_boolean`; out-of-range temperatures are
  ignored; `notAvailable` clears them.
- **Events** (phase 3): motion is derived from the press inside the device
  model, while the rocker event keeps the one-shot occurrence that is cleared
  after dispatch, so it is never replayed.
- **Write skipping** (phase 4): the new entities inherit
  `OpusGreenNetEntity._handle_state_update`, so unchanged projections are not
  written.
- **Transport/coordinator:** no upstream change. No new topic, query, timer,
  retry or polling (A1-A3); retain handling unchanged (A4).

No fork structure was reverted to an upstream pattern; the diff against fork
`main` touches only the files listed below.

## Resolved points

- `manifest.json` version stays `0.5.0` and `CHANGELOG.md` is unchanged:
  `CLAUDE.md` forbids version and CHANGELOG changes in pull requests. The
  maintainer decides on version and release notes after the merge.
- Kept from upstream: minimum Home Assistant 2026.10.1 in `hacs.json`,
  `requirements_test.txt` and README. The fork already imports `probatio` and
  APIs unavailable in 2026.9.4, so the previous 2026.8.0 claim was too low.
- The automatic README merge left a broken test list (empty line, missing
  files); fixed.
- `protocol-contract.md` sections 4 and 8: new inbound keys/ranges and the new
  entities added to the frozen inventory. Existing rows are unchanged.

## Changed files compared with fork `main`

Integration: `binary_sensor.py`, `binary_sensor_descriptions.py`, `const.py`,
`enocean_device.py`, `sensor.py`, `strings.json`, `translations/de.json`,
`translations/en.json`. Metadata: `hacs.json`, `requirements_test.txt`.
Tests: `test_contact_sensor.py`, `test_opus_motion_detector.py`,
`test_temperature_humidity_sensor.py` (upstream), `test_upstream_backport.py`
(new), `test_binary_sensor_discovery.py` (+ D5-00-01 profile). Docs: README,
protocol contract, this report.

## New tests (`tests/test_upstream_backport.py`)

The upstream tests cover parsing and basic entity behaviour. The new module
checks the new profiles against the fork's guardrails:

| Test | Guardrail |
|------|-----------|
| `test_new_profiles_register_stable_enabled_entities` (A5-04-01/02/03, D5-00-01, motion) | A5: unique IDs, translation keys, child device via gateway, enabled and uncategorised; RSSI stays disabled; event only for the motion detector |
| `test_existing_profiles_keep_their_inventory` (HeatArea, rockers) | A5: no new entities on established profiles; one HeatArea humidity entry |
| `test_discovery_and_live_updates_send_no_device_request` | A1: no device-addressed publish on discovery, telegrams, RSSI or battery |
| `test_motion_press_is_one_event_and_never_replayed` | Phase 3 + contract: no replay on later updates, release keeps motion, outage → unavailable |
| `test_unchanged_readings_skip_redundant_state_writes` | Phase 4: identical readings / RSSI-only updates are not written; real changes once |
| `test_retained_answers_cannot_supply_new_profile_readings` | A4: retained answers never supply or overwrite contact/temperature readings |

## Gate results

| Gate | Result | Origin |
|------|--------|--------|
| `ruff check .`, `ruff format --check .` | passed | local, ruff 0.16.3 |
| mypy, pytest with coverage gates, lifecycle repetitions, hassfest | **pending** | CI on the pull request (PyPI unreachable locally) |

## Residual risks

- No hardware validation of the combination in this fork. Upstream reports
  community tests for A5-04-03, D5-00-01 and the motion detector on their own
  builds only.
- Motion stays unknown after a restart until the next motion telegram.
- A5-04 sensors without a battery report keep an unknown battery entity
  (upstream behaviour, unchanged).
