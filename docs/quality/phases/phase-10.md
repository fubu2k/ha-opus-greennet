# Phase 10 - Field-level freshness and replay

- **Status:** implemented on the branch; **not executed in CI yet**; awaiting
  review. Not merged, no PR. No version change, no release, no CHANGELOG entry.
- **Branch:** `fix/opus-q10-field-freshness` (new)
- **Base SHA:** `e4bb5e9acb3a6aa293c40cccc85dbc04a5876365` (`main`,
  2026-10-19 19:45 +0200, merge of PR #7 `feat/opus-q09-quality-gaps`)
- **Plan:** `implementation-plan.md` revision 3, section 12 (scope), guardrail
  A4 (retained protection), A1 (traffic). Phase order: started before the
  resumed phase 6 metadata review at the maintainer's request; that review
  stays open.
- **Reference:** Home Assistant `2026.10.2`, home-assistant/core
  `8d7fd8b68677c2585be36ec86043308f403875b8` (unchanged from phase 8).
  `homeassistant/components/mqtt/client.py` at that commit was read to verify
  subscription and retained-delivery behaviour (section 2.2).

## 1. Scope and result

The plan describes a risk, not a reproduced hardware defect: live data followed
by an older retained or snapshot value. Two ordering failures were reproduced
at code level and are corrected; no rule status changes (45 done / 9 exempt /
0 todo remain).

| ID | Failure on `e4bb5e9` (reproducer) | Correction |
|----|-----------------------------------|------------|
| R1 | Within one connection, a retained `stream/devices` or `stream/device` value overwrote a field that had already been updated by a live delta, a radio telegram, a fast-path scalar or the answer to the integration's own `get/devices` request. The stale value also replaced the cached device model, so a later rediscovery could reapply it. | Session-scoped field provenance: such a retained value is neither applied nor cached (protocol contract 6.1). |
| R2 | An applied retained replay set `last_update_received_monotonic`. If it arrived after the resync request, the rediscovery rule treated it as a live update and skipped the requested (fresher) snapshot answer entirely. | Retained applications keep the last live receipt time and are reported as `<source> (retained)`. |

Not changed: first snapshots, unrelated fields, other channels, unknown
fields, retained discovery of new devices, replays before the session is armed
and replays after any interruption (they may be newer), retained telegram and
RPC handling (still discarded), feedback accounting, timers and publications.

## 2. Authorised changes and guardrails A1-A5

Authorised by plan section 12: "minimal correction" for ordering failures
demonstrated by regression tests, field/channel provenance, cleanup on
removal/unload/interruption, protocol section 6 update.

### 2.1 Guardrails

| Guardrail | Effect |
|-----------|--------|
| A1 | No new publish, subscription, poller, interval, reconciliation slot or retry. A discarded replay is still passed to `_record_confirmed_feedback` exactly as before, so a pending reconciliation is cancelled as before and no additional status query can result. |
| A2 | Availability and health logic unchanged. Arming happens only after a successful uptime probe; the uptime value itself is not used as freshness evidence and never confirms device state. A failed probe neither arms nor disarms. |
| A3 | Timeouts, serialisation and queue untouched; no cooldown or delay added. |
| A4 | All existing retained protection kept (telegrams, `getAnswer`, `putAnswer`, request answers, button keys). The lift is narrow: only a retained state value for a field with in-session live provenance is discarded. No invented timestamps or sequences; wire `timestamp` fields were not used because their reliability is not established. |
| A5 | No unique ID, entity ID, device assignment, inventory, default or translation changed. `last_update_source` (diagnostics only) gains a ` (retained)` suffix for retained applications. |

### 2.2 Why the rule cannot discard newer data

- MQTT 3.1.1 [MQTT-3.3.1-8/9]: retain=1 is only set for messages sent because
  a subscription was made; established subscriptions receive retain=0 (quoted
  in `client.py` `_async_perform_subscriptions`). HA uses a clean session.
- HA 2026.10.2 subscribes wildcard filters one at a time, awaiting each SUBACK,
  and exact topics afterwards; `_retained_topics` is cleared on reconnect.
- The session is armed only after the health probe's uptime answer (exact
  topic, registered route) was received and all four stream wildcards are in
  `_stream_topics`. Every publication after that point on the subscribed
  filters reaches the coordinator live. A retained copy delivered later in the
  same session therefore carries a value that was either published before the
  session or already delivered live.
- Any interruption (MQTT disconnect, bridge `0`, unload) disarms and clears all
  provenance: publications during an outage may be newer than the last live
  value, so replays after reconnect apply exactly as before phase 10.

## 3. Files and requirements

| File | Change |
|------|--------|
| `custom_components/opus_greennet/coordinator.py` | `_live_fields`, `_replay_guard_armed`, `_device_stream_replay_windows`; `_arm_replay_guard`, `_function_fields`, `_record_live_fields`, `_record_live_property`, `_property_field`, `_drop_stale_replay`; `retained` keyword threaded through `_apply_known_device_state_property`, `_apply_scalar_telemetry`, `_buffer_device_stream_property`, `_mark_and_log_dispatch`; provenance recorded for live stream values, telegrams and `getAnswer/devices`; cleanup in `_cancel_background_work` and `async_forget_device`; arming in `_async_refresh_gateway` |
| `tests/test_field_freshness.py` | **New:** 25 test functions, 42 cases |
| `.github/workflows/tests.yml` | `tests/test_field_freshness.py` added to both three-fold race repetitions (plan section 17) |
| `docs/quality/protocol-contract.md` | Section 6 table and freshness rule; new section 6.1 |
| `docs/quality/implementation-plan.md` | Section 7 status |
| `README.md` | Retained state paragraph; test file in the project structure |
| `docs/quality/phases/phase-10.md` | **New:** this report |

### Regression reproducers (failing before, passing after)

| Test | Before (`e4bb5e9`) | After |
|------|--------------------|-------|
| `test_retained_replay_cannot_overwrite_live_delta` (named `stream/devices`, indexed `stream/devices`, indexed `stream/device`) | `is_on = False` | `True` |
| `test_retained_replay_cannot_overwrite_live_telegram` | `False` | `True` |
| `test_answer_to_own_request_is_not_overwritten_before_discovery` | `False` | `True` |
| `test_retained_replay_does_not_suppress_resync_answer` (R2) | `False` | `True` |

The four scenarios were also run as a standalone script against both
revisions (section 5). The remaining tests cover per-channel scope, unrelated
and unknown fields, scalars, first snapshots, new-device discovery, arming,
interruption (broker and bridge), removal and re-add, unload, A1 feedback
parity, replay windows and field resolution (RWM slots, buffered keys,
malformed channels, buttons, metadata).

## 4. Gateway/cache versus radio counts

| Scenario | Before | After |
|----------|--------|-------|
| Setup, health, reconnect, resync, commands, reconciliation | `test_traffic_baseline.py` | unchanged; the baseline test is not modified |
| Live or retained stream message, telegram, answer | 0 publications | 0 publications |
| Discarded stale replay with a pending reconciliation | reconciliation cancelled (stale value applied) | reconciliation cancelled (stale value not applied) |

MQTT counts are not radio transmissions; no hardware claim is made.

## 5. Commands and results

| Check | Result | Origin |
|-------|--------|--------|
| `ruff check .` | passed | local, ruff 0.16.3 (pinned version, binary from the GitHub release) |
| `ruff format --check .` | passed (90 files) | local, ruff 0.16.3 |
| `python -m py_compile coordinator.py` | passed | local, CPython 3.14.6 |
| Reproducer script (R1 x5, R2 x1) against `e4bb5e9` and the branch | 6/6 wrong on `e4bb5e9`, 6/6 correct on the branch | local, see note |
| `pytest tests/test_field_freshness.py`, three repetitions | 42 passed each time | local, see note |
| Full suite, differential: `e4bb5e9` versus branch | 1,108 passing cases unchanged; branch adds 42 passing cases; no case changed status | local, see note |
| pip check, strict mypy, full pytest with real Home Assistant, coverage gates, hassfest, CI race repetitions | **not executed** | pending CI |

**Note on local execution.** PyPI is blocked by the egress proxy in this
environment, so Home Assistant, pytest-asyncio and mypy for Python 3.14 could
not be installed. The local runs used pytest 9.1.1 from a local cache, a
minimal offline asyncio shim and import stubs for `homeassistant` (scratch
files, not committed). They execute the real coordinator code but not real
Home Assistant: 292 cases that need the real `hass` fixture, entity platforms
or config flows error or fail identically on both revisions and were not
evaluated (among them `test_ha_lifecycle.py`, `test_retained_device_answers.py`,
`test_upstream_backport.py`, `test_diagnostics.py`). `test_traffic_baseline.py`
(17 cases) and `test_reconciliation_feedback.py` (51 cases) ran and passed
unchanged. These results are evidence for the logic only; acceptance requires
the CI jobs on the branch (workflow_dispatch), including per-module coverage.

## 6. Registry and identity compatibility

- No unique ID, entity ID, name, translation key, device class, category or
  default enablement changed; no entity is created or removed.
- Diagnostics show `last_update_source` with a ` (retained)` suffix for
  retained applications; the receipt age then refers to the last live update.

## 7. Documentation updates

Protocol contract (section 6 and new 6.1), README (Retained state, project
structure), implementation plan (section 7 status), this report. The quality
evidence matrix is unchanged because no rule status changed.

## 8. Hardware observations, unknowns and residual risks

- **No hardware observation.** Whether real gateways and the Mosquitto bridge
  produce the reproduced orderings, and with which retain flags, is unverified.
- A stream-wildcard SUBACK that times out during setup phase 2 (advisory wait,
  180 s) still arms the session; in that pathological case a replay on that
  filter could be newer than an armed live value.
- A Mosquitto bridge reconnect forwards the gateway broker's retained topics to
  Home Assistant as live messages (retain=0). They are applied and recorded as
  live, as before phase 10; the rule cannot distinguish them.
- After an interruption, retained replays apply again by design. A stale
  `stream/devices` boot snapshot replayed on reconnect is corrected only by the
  resync answer (at most once per 60 s) or later live data; within the
  debounce window it can remain until the next live update (unchanged
  behaviour, documented limitation).
- A discarded replay still confirms pending feedback (A1 parity). Whether a
  stale replay should confirm a command belongs to phase 11 (ACK and
  confirmation semantics).
- A live indexed value whose `key` arrives later in the same window is not
  recorded; a later replay of that field is applied as before.

## 9. Review status and next step

Awaiting review. Next step: push the branch, run the Tests workflow by manual
dispatch on `fix/opus-q10-field-freshness`, and record the job IDs and results
in section 5 (CI evidence is to be added in a follow-up commit to this report).
Merge and release authorisation remain separate. Next planned phase: resumed
phase 6 (remaining metadata review, section 10), then phases 11 and 12.
