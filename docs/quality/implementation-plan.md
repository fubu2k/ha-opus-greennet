# OPUS GreenNet Quality Series – Implementation Plan

This document anchors the sequential quality series for
`fubu2k/ha-opus-greennet` in the repository. It is updated in every phase.
Per-phase details live in `docs/quality/phases/phase-NN.md`; the protocol
contract lives in `docs/quality/protocol-contract.md`.

## Goal

Improve code quality, maintainability, testability, typing and documentation
of the existing OPUS/EnOcean MQTT integration. Functional behaviour is
preserved unless a phase explicitly permits a proven bug fix.

## Repository target

- Target repository: `fubu2k/ha-opus-greennet` (remote `origin`, default branch `main`).
- References to `kegelmeier/ha-opus-greennet` in `CLAUDE.md`, `README.md` and
  `manifest.json` are historical upstream references. They do not change the
  target and are not rewritten in this series (documentation URLs and
  `codeowners` are metadata, not in scope).

## Binding exclusions (A1–A5)

These topics are not implemented as side work, as a prerequisite or as a later
phase. If a goal strictly requires an excluded change, this is documented and
work stops.

| ID | Exclusion | Frozen current values (base `3e52fce`) |
|----|-----------|----------------------------------------|
| A1 | No more frequent status polling: no new pollers, shorter intervals or additional reconciliation slots | `GATEWAY_HEALTH_INTERVAL = 60 s`, `RECONNECT_RESYNC_DEBOUNCE_SECONDS = 60`, `OUTBOUND_STATE_RECONCILIATION_DELAYS = (5, 20)`, `COVER_RECONCILIATION_DELAYS = (10.0, 20.0)` after movement end, `COVER_MOVEMENT_GRACE_SECONDS = 5.0` |
| A2 | No change to uptime, health or availability semantics; bridge uptime is never used to confirm device state | `available = not _unloaded and _gateway_available and _bridge_connected is not False and mqtt.is_connected()`; a command timeout does not change availability |
| A3 | No topic quarantine, drain phase, topic lock or additional cool-down after a timeout | `REQUEST_TIMEOUT = 10 s`, `SUBSCRIPTION_TIMEOUT = 30 s`, `SETUP_REQUEST_TIMEOUT = 30 s`, `SETUP_SUBSCRIPTION_TIMEOUT = 180 s`, `MIN_SEND_INTERVAL = 0.0` |
| A4 | No new replay/freshness hardening (timestamp/sequence evaluation, replay generations, prioritising live over retained data); existing logic is neither removed nor restructured | see "Existing retain/freshness logic" in `protocol-contract.md` §6 |
| A5 | No opt-in/exposure policies against entity bloat; no change to entity count, selection options, enablement defaults or registry configuration | signal strength sensor disabled by default, all others enabled; entity inventory in `protocol-contract.md` §8 |

## Repository rules (summary)

- No direct commits to `main`, no version change, no `CHANGELOG.md` update.
- No release, no merge, no automatic PR, no force-push.
- No real device actions or configuration writes to hardware.
- Unique IDs, entity IDs, topic paths, public services and device assignments stay unchanged.
- OPUS wire values, channel zero, `notAvailable`, cover inversion, the STOP payload
  and the proprietary HeatArea profiles are preserved (see `protocol-contract.md`).
- No automatic write retries after a timeout.
- No blanket migration to `DataUpdateCoordinator`.
- No additional dependencies without a concrete need.

## Branch chain

Phase 1 is based on `main` as verified at the start. Each following phase is
based on the completed HEAD of the previous branch. No rebase, no squash, no
deletion of earlier branches. If a branch already exists, it is compared with
this documentation and never overwritten.

| Phase | Branch | Predecessor | Base SHA | Status |
|-------|--------|-------------|----------|--------|
| 1 | `feat/opus-q01-baseline` | `main` | `3e52fce31e7a2d503eef4820f4c706ebcfc751ab` | completed |
| 2 | `feat/opus-q02-…` (name given by the phase prompt) | `feat/opus-q01-baseline` | final HEAD of phase 1 | open |
| 3 | `feat/opus-q03-…` | phase 2 | HEAD of phase 2 | open |
| 4 | `feat/opus-q04-…` | phase 3 | HEAD of phase 3 | open |
| 5 | `feat/opus-q05-…` | phase 4 | HEAD of phase 4 | open |
| 6 | `feat/opus-q06-…` | phase 5 | HEAD of phase 5 | open |
| 7 | `feat/opus-q07-…` | phase 6 | HEAD of phase 6 | open |

A phase's HEAD SHA is never written into its own commit. It is reported in the
chat and recorded as the base SHA in the next phase's documentation.

## Quality commands (CI equivalent)

Taken from `.github/workflows/tests.yml`:

```bash
python -m pip install --upgrade -r requirements_test.txt "homeassistant==<latest stable>"
python -m pip check
ruff check .
ruff format --check .
pytest -v --cov --cov-report=term-missing
```

The coverage threshold is `fail_under = 75` (`pyproject.toml`). `hassfest` only
runs in CI (`home-assistant/actions/hassfest@master`) and cannot be executed
locally. `mypy` is **not** a project gate; in phase 1 it was run for information
only, in a local environment outside the repository.

## Phase overview (proposal from phase 1, checked against the actual code)

The contents of phases 2–7 are proposals derived from the phase 1 analysis.
The explicit prompt for each phase is authoritative; if it differs, the prompt
wins and this plan is updated in that phase.

### Phase 2 – Event contract and proven event fix

- Finding F1 (code path proven, hardware occurrence unproven): `_apply_scalar_telemetry`
  sets `device.dbm` directly for `dbm`, without calling `update_from_telegram`,
  and then dispatches `SIGNAL_DEVICE_STATE_UPDATE`. The transient fields
  `last_button`/`last_button_action` of the previous rocker telegram stay set,
  so `OpusGreenNetEvent._handle_state_update` would fire the same event again.
  Reproduced locally with a script outside the repository (see `phase-01.md`).
- Other dispatch paths without a reset: `_dispatch_cover_estimate` (covers only)
  and `_handle_put_answer_state` (commands only). Not reachable for rockers in
  the current code, but to be recorded in the contract.
- Target modules: `event.py`, `coordinator.py` (`_apply_scalar_telemetry`, possibly
  `_handle_put_answer_state`, `_dispatch_cover_estimate`), `enocean_device.py`
  (reset helper), tests `tests/test_event_entity.py`, `tests/test_scalar_telemetry.py`.
- Requires explicit approval as a bug fix in the phase prompt. Not related to
  A4: this concerns transient event fields, not freshness or retain evaluation.

### Phase 3 – Typing without behaviour change

- Starting point: `mypy` (default) reports 27 errors, `mypy --strict` 72 errors in 15 files.
- Target modules: `mqtt_transport.py` (payload types `str | bytes | bytearray`,
  lambda inference), `enocean_device.py` (`heat_area_type`, `default_channel_id: int | None`),
  `coordinator.py` (unparameterised `Callable`, `dict`, `list`; `CALLBACK_TYPE`
  for timer handles; `started` annotation; delay tuple type), `entity.py`/`event.py`
  (`entity_name: str | None`), `lock.py` (`LockEntityFeature(0)` instead of `0`;
  same value), `binary_sensor.py`.
- Optional mypy configuration in `pyproject.toml`. Whether `mypy` becomes a test
  dependency or a CI step must be decided in the phase prompt (rule "no additional
  dependencies without a concrete need").
- Known limitation: the `voluptuous.Schema` vs. `probatio.Schema` mismatch in
  HA 2026.10 (`__init__.py`, `config_flow.py`) is a stub-level type conflict;
  it is not resolved through runtime changes.
- Dynamic `getattr`/`setattr` paths are only typed or replaced by explicit access
  if behaviour and unique IDs stay identical.

### Phase 4 – Extract a protocol/parsing module

- Move pure functions out of `coordinator.py` into a new module (e.g. `protocol.py`):
  topic regexes, `_set_nested_property`, `_parse_value`, `_merge_device_data`,
  `_device_state_functions`, `_indexed_changed_functions`,
  `_configuration_state_functions`, `_rwm_transmit_modes`,
  `_channel_from_functions`, `_confirmed_fields_by_channel`,
  `_command_fields_by_channel`, payload builder for `state.functions`.
- Keep compatibility wrappers as static methods so existing tests stay unchanged.
- Golden/contract tests against `protocol-contract.md` (channel zero, STOP,
  inversion, HeatArea modes, `notAvailable`).
- Target modules: `coordinator.py`, new protocol module, `const.py` (read only),
  new tests `tests/test_protocol_contract.py`.

### Phase 5 – Structure ownership and lifecycle in the coordinator

- Move the cover movement estimate (`CoverMovement`, `_start_/_finish_/_clear_cover_movement`)
  and the reconciliation scheduler (`_schedule_/_cancel_reconciliation_queries`,
  `_pending_reconciliation_*`, `_reconciling`) into clearly separated helpers with
  explicit ownership. Timing values and frequency stay unchanged (A1); the literal
  `[5, 20]` tests stay unchanged.
- Consolidate the duplicated cleanup in `_cancel_background_work` and `async_unload`.
- Lifecycle regression tests: no orphaned timers, tasks or futures after disconnect,
  bridge down, forget and unload.
- Target modules: `coordinator.py`, possibly new `cover_movement.py` and
  `reconciliation.py`, tests `test_cover_movement.py`, `test_reconciliation_feedback.py`,
  `test_release_transport_safety.py`.

### Phase 6 – Platform and entity consolidation

- Move the repeated discovery listener pattern (`light`, `switch`, `cover`,
  `climate`, `event`) and the optimistic post-ACK updates into shared helpers in `entity.py`.
- Unique IDs, entity count, translation keys and enablement defaults unchanged (A5);
  inventory tests protect this.
- Close test gaps: `__init__.py` (72 % – service handlers `get_device_parameters`,
  `reload_entry`, error paths of `_loaded_entry`), `light.py` (85 %).
- Target modules: `entity.py`, `light.py`, `switch.py`, `cover.py`, `climate.py`,
  `event.py`, `sensor.py`, `binary_sensor.py`, `__init__.py` (tests only, or the
  dead `result is None` checks after agreement).

### Phase 7 – Documentation and quality gates

- Extend the "Development" section of `README.md` with the quality commands;
  complete docstrings and module descriptions; final report of the series.
- Optional, only after an explicit decision: a CI step for `mypy`.
- Target modules: `README.md`, `docs/quality/*`, possibly `.github/workflows/tests.yml`, `pyproject.toml`.

## Already solved (do not re-implement)

Confirmed against the code at base `3e52fce` and existing tests:

- Retained `getAnswer/devices` answers create neither discovery nor state
  (`_handle_get_answer_devices`, `tests/test_retained_device_answers.py`).
- Retained telegrams trigger neither events nor state (`_handle_telegram_property_message`).
- Button keys from `stream/devices` are not treated as new occurrences (`_apply_known_device_state_property`).
- Availability changes do not replay an old rocker event
  (`test_availability_changes_do_not_replay_last_rocker_event`).
- Rediscovery resets the transient button fields (`update_from_telegram({"functions": []})`).
- A command timeout does not change gateway availability and is not retried automatically.
- Global send queue with priorities (command > query > background), exactly one request in flight.
- STOP as a dedicated function `{"key": "stop", "value": "true"}` with channel selector (`tests/test_cover_stop.py`).
- Feedback is matched per channel and field; feedback for an earlier command cannot
  confirm a later one (`tests/test_reconciliation_feedback.py`).
- Idempotent binary sensor discovery and declarative descriptions
  (`tests/test_binary_sensor_discovery.py`, `tests/test_binary_sensor_descriptions.py`).
- Child device removal without MQTT writes and with request isolation (`tests/test_device_removal.py`).
