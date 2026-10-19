# Phase 8 - Reproducible baseline and traffic evidence

- **Status:** implemented on a branch, **awaiting first CI run and review**.
  No merge, no version change, no release, no CHANGELOG entry.
- **Branch:** `feat/opus-q08-reproducible-baseline` (new)
- **Base SHA:** `bbd7e168e58d3e7e61756ead7cb27b3dea2c42b7` (`main`,
  2026-10-19 16:51 +0200, "chore: release v0.7.0b2")
- **Plan:** `implementation-plan.md` revision 3, section 8.
- **Runtime change:** none. No file under `custom_components/` was modified.

## Changes since the reference baseline of the plan

Reviewed main HEAD in the plan: `63c7380`. Relevant later commits on `main`:

- `3e7ae0b`, `feabed5`, root-copy deletions `e3a50c9` … `a1c247e`: plan
  revision 3; uploaded root copies of integration files were removed again.
- `2a28216` (PR #2): phase 6 (handle diagnostics, see `phase-06.md`).
- `bac5620` (PR #4): upstream v0.7.0b1 device support merged into the fork.
- `71b26f9`, `bbd7e16`: documentation, release v0.7.0b2.

CI on the base commit: workflow "Tests" run `39272797392` succeeded (jobs
"Home Assistant (latest stable)" and "validate"). Job logs could not be read
from this environment, so the exact HA patch used by that run is not recorded
here.

## Reference versions (fixed)

| Item | Value | How it was determined |
|------|-------|-----------------------|
| Home Assistant | `2026.10.2` | Latest 2026.10 patch tag in home-assistant/core (`git ls-remote`, 2026-10-19); tags 2026.10.0, .1, .2 exist |
| home-assistant/core commit | `8d7fd8b68677c2585be36ec86043308f403875b8` | Tag `2026.10.2`, commit date 2026-10-16 21:59 +0200 |
| Required Python | `>=3.14.2` | `pyproject.toml` / `REQUIRED_PYTHON_VER` at that commit |
| `paho-mqtt` | `2.1.0` | Equals `homeassistant/package_constraints.txt` at that commit |
| Frontend (icon catalogue) | `home-assistant-frontend==20260930.4` | `components/frontend/manifest.json` at that commit |
| Hassfest (fixed) | Built in CI from `script/hassfest/docker/Dockerfile` of that commit | New job `validate-reference` |
| Hassfest (moving) | `home-assistant/actions/hassfest@master` (`cd6f363` on 2026-10-19) | Runs the image `ghcr.io/home-assistant/hassfest` without a tag, so pinning the action SHA would **not** freeze the rules; kept as an additional signal only |
| Runner image (reference jobs) | `ubuntu-24.04` | GitHub announced on 2026-10-19 (annotation of run `39272797392`) that `ubuntu-latest` migrates to Ubuntu 26; the reference jobs no longer follow that label |
| Python patch | `3.14` series, exact version recorded per run | `python -VV` in the job summary and artifact; not pinned to a patch to avoid an unavailable setup-python build |
| Test tools | `mypy==2.4.0`, `pytest==9.1.1`, `pytest-asyncio==1.4.0`, `pytest-cov==7.1.0`, `ruff==0.16.3` | Unchanged pins of `requirements_test.txt` |

The complete resolved dependency set is recorded per run (`pip freeze --all`)
in the artifact `reference-baseline-<sha>`; it is evidence, not a lock file.

## Rule and validator revision recheck

`tests/test_quality_scale.py` copies the hassfest rule list and schema from
core commit `7ff0e7f` (2026-10-13, development branch). Compared against the
reference tag `2026.10.2`:

- `ALL_RULES`: identical, 54 rules, identical tiers (bronze 20, silver 10,
  gold 21, platinum 3).
- `SCHEMA` of `quality_scale.py`: identical.
- `quality_scale_validation/*.py`: same file set; only `discovery.py` differs
  (`7ff0e7f` additionally knows the development-only manifest key `lorawan`).
  Irrelevant for this integration, which plans MQTT discovery.
- `icons.py`: only the `SYSTEM` integration type handling differs; this
  integration is a `hub`.
- `manifest.py`: `7ff0e7f` additionally accepts `lorawan`; not used here.

Result: the 54-rule self-assessment and its schema test remain valid for the
fixed reference. Current statuses: 40 done, 9 exempt, 5 todo (`discovery`,
`docs-examples`, `docs-use-cases`, `icon-translations`, `repair-issues`;
phase 9). No status was changed in phase 8.

Exemption recheck (no change, notes for later phases):

| Rule | Exemption | Phase 8 assessment |
|------|-----------|--------------------|
| appropriate-polling | push integration, 60 s gateway probe documented | still valid; probe frozen by the traffic baseline |
| docs-conditions, docs-triggers | no conditions/triggers provided | still valid |
| docs-configuration-parameters | no options flow | still valid |
| reauthentication-flow | no stored credentials | still valid |
| discovery-update-info | no network address stored | **re-evaluate in phase 9** once MQTT discovery exists |
| reconfiguration-flow | only value is the unique ID | still valid |
| async-dependency, inject-websession | no requirements, no HTTP | still valid |

## Icon catalogue check (section 11 candidates)

All candidate MDI identifiers of plan section 11 were checked against
`pylint/plugins/pylint_home_assistant/generated/mdi_icons.py` at the reference
commit (generated from frontend `20260930.4`, 7,447 names).

- Available: `router-wireless`, `toggle-switch`, `power-socket-de`,
  `lightbulb`, `window-shutter`, `blinds-horizontal`, `radiator`, `pipe-valve`,
  `gesture-tap-button`, `motion-sensor`, `window-closed`, `window-open`,
  `window-open-variant`, `smoke-detector`, `water-alert`, `lock`, `lock-open`,
  `lock-outline`, `lock-clock`, `cog-outline`, `thermometer`, `water-percent`,
  `brightness-5`, `flash`, `battery`, `signal`, `lan-disconnect`.
- **Not available: `mdi:cog-alert`** (proposed for `mechanics_fault` = `on`).
  Available alternatives include `mdi:alert-circle`, `mdi:wrench-cog`,
  `mdi:cog-off`. Plan rule: prefer the standard `problem` device-class icon
  if the entity uses that class. Decision belongs to the icon phase; nothing
  was changed here.

This catalogue is not part of the `homeassistant` wheel, so no automated test
was added; the icon phase must repeat this check against its reference.

## Delivered files

| File | Change |
|------|--------|
| `requirements_test.txt` | Comment only; minimum `homeassistant>=2026.10.1` (equals `hacs.json`) and tool pins unchanged |
| `requirements_test_reference.txt` | **New:** `-r requirements_test.txt` plus `homeassistant==2026.10.2` |
| `.github/workflows/tests.yml` | **New jobs** `reference` and `validate-reference`; existing jobs `test` and `validate` unchanged in behaviour and name |
| `tests/test_reference_baseline.py` | **New:** consistency of reference version, core SHA, pinned runner, evidence steps |
| `tests/test_traffic_baseline.py` | **New:** publish counts per endpoint scope and trigger, JSON export |
| `docs/quality/capability-matrix.md` | **New:** profile/operation capability matrix and audit findings |
| `README.md` | CI description, test tree and reference install command |
| `docs/quality/phases/phase-08.md` | This report |

The new jobs only add evidence. The `reference` job runs the full gate set
(pip check, Ruff, strict mypy, pytest with coverage gates, three race
repetitions) against the fixed release and uploads `python.txt`,
`pip-freeze.txt`, `junit.xml`, `coverage.json` and `traffic-baseline.json`.

## Traffic baseline (MQTT publications, not radio transmissions)

Frozen in `tests/test_traffic_baseline.py` with the production coordinator,
request manager and timer callbacks and a mocked MQTT boundary:

| Scenario | Trigger | Expected publications |
|----------|---------|-----------------------|
| `setup` | config entry setup incl. phase 2 | `gateway_uptime` 2, `gateway_inventory` 1, `gateway_info` 1 |
| `idle_10_health_ticks` | 10 × 60 s health tick, no device activity | `gateway_uptime` 10 |
| `reconnect_burst_5` | 5 disconnect/reconnect cycles inside the 60 s debounce | `gateway_uptime` 5, `gateway_inventory` 1, `gateway_info` 1 |
| `command_ack` | one switch command | `device_command` 1 |
| `reconciliation_without_feedback` | 5 s and 20 s timers fire | `device_status_query` 2 |
| `command_with_device_feedback` | command, then matching device feedback | `device_command` 1 (both checks cancelled) |
| `incoming_live_and_retained_telegrams` | live and retained `stream/telegram` | none |

Invariants asserted: setup, health and reconnect never publish
`device_command`, `device_status_query` or `device_recom`; timing constants
`60 s`, `60 s`, `(5, 20)` unchanged.

These values come from reading the code and the existing tests that assert
the same sequences (`test_startup_probes_before_retained_streams_and_discovers_after_suback`,
`test_health_uses_uptime_without_waiting_for_optional_system_info`,
`test_identical_flow_keeps_both_checks`, `test_feedback_drops_check_still_waiting_for_the_send_slot`).
They become recorded evidence only after the first CI run of the `reference`
job. If a count differs, the code is investigated; the baseline is never
raised to match.

## Capability matrix and audit

See `docs/quality/capability-matrix.md`. Findings without runtime change:

1. Reconciliation has two callers: unconfirmed own commands, and observed
   outbound `to` telegrams from third parties (MyOPUS, HomeKit, bridge rules).
   The second case can cause up to two status queries per channel from Home
   Assistant for commands Home Assistant did not send; radio effect on
   HeatArea actuators unknown. Candidate for phase 11.
2. `async_query_climate_status` has no caller.
3. ACK 200 and 201 are handled identically (open item O1, phase 11).
4. Unknown: cache versus radio for `query: status` per profile, HeatArea
   query effect on linked actuators, firmware effect of `get/devices`, actual
   power source per installed model. These block new automatic requests.

## Guardrails A1–A5

| Guardrail | Effect of phase 8 |
|-----------|-------------------|
| A1 | No runtime change; publish counts frozen as a regression baseline |
| A2 | Gateway health asserted to derive no device request |
| A3 | Timeouts, serialization and delays unchanged and asserted |
| A4 | Retained telegram discard asserted; no freshness change |
| A5 | No entity, ID, default or registry change |

## Quality gates and evidence

| Check | Result | Origin |
|-------|--------|--------|
| `ruff check .` | passed | local, ruff 0.16.8 (CI pins 0.16.3) |
| `ruff format --check .` | passed (82 files) | local, ruff 0.16.8 |
| `tests/test_reference_baseline.py` | 6 functions passed when executed directly (no Home Assistant import needed) | local, Python 3.13 |
| `classify()` of `test_traffic_baseline.py` | 13 endpoint cases passed when executed directly | local |
| Workflow YAML | parses; jobs `reference`, `test`, `validate-reference`, `validate` | local (Ruby YAML) |
| mypy, full pytest, coverage gates, race repetitions, both hassfest jobs | **not executed** | PyPI was not reachable from this environment; first evidence is the CI run of the branch |

Nothing in this report claims a passed CI run or hardware observation for the
new files.

## Hardware observations

None. No device command, query or configuration access was performed.

## Residual risks

- `validate-reference` builds the hassfest image from core sources; if the
  build fails for infrastructure reasons, the moving `validate` job still
  runs, and the failure is recorded rather than hidden.
- The reference Python patch version follows setup-python's 3.14 build and is
  recorded, not pinned.
- MQTT mocks cannot rule out hidden firmware radio effects.

## Review status and next step

Stop for review. After a green CI run: record run ID, Python patch, test
count, coverage and the uploaded traffic JSON in this report, then mark
phase 8 completed in `implementation-plan.md`. Next planned phase: 9
(applicable quality-scale gaps). Merge and release authorisation are separate.
