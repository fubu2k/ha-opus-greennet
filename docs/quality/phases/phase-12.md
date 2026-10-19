# Phase 12 - Late-response ambiguity investigation

- **Status:** investigation implemented as tests and documentation on the
  branch; CI green (run `39313178932`, section 9); awaiting review. Not merged, no PR. No production code change, no
  version change, no release, no CHANGELOG entry.
- **Branch:** `test/opus-q12-late-responses` (new)
- **Base SHA:** `c1ac37656198892f668cb9503ee18a3c508d8cf5` (`main`,
  2026-10-19 21:28 +0200, "docs: add unreleased changelog entry for phase 6
  metadata fix"; includes the resumed phase 6 merge `6ce8f38`, PR #10)
- **Plan:** `implementation-plan.md` revision 3, section 14 (scope), guardrail
  A3 (serialisation, timeouts, no cooldown, no write retry), A1 (traffic),
  A2 (health), A4 (retained). Section 14: "Separate investigation from
  implementation approval"; "Changing production timeout behaviour requires a
  separate design decision".
- **Reference:** Home Assistant `2026.10.2`, home-assistant/core
  `8d7fd8b68677c2585be36ec86043308f403875b8` (unchanged from phase 8). Vendor
  reference `opus_mqtt_services_reference.md` sections 3.2 to 4.
- **Note on file names:** the integration has no `gateway.py`. The topic locks
  and the global send queue live in `mqtt_transport.py`
  (`PriorityLock`, `MQTTRequestManager`); command dispatch and the
  device-level `putAnswer` listener live in `coordinator.py`.

## 1. Scope and result

Plan section 14 asks to reproduce request A timing out, request B starting on
the same answer topic and A's late success or negative reply arriving during
B, including cancellation and reconnect; to establish the actual correlation
information; and to document the limitation if no reliable mitigation exists.

Result: the ambiguity is reproduced deterministically and pinned by
`tests/test_late_responses.py` (16 test functions, 18 cases). The wire format
offers no correlation information that is verified to be reliable (section 3).
No mitigation is implemented in this phase (section 5); one traffic-neutral,
diagnostic-only candidate is proposed for a separate decision.

| ID | Finding | Status |
|----|---------|--------|
| L1 | A late answer that arrives after B (same answer topic) was published is taken as B's answer. B's own answer is then dropped, or taken by C if C was already published. The shift ends at the first answer that arrives while no request owns the topic. | reproduced, pinned, documented limitation |
| L2 | A late answer that arrives before B is published (B waiting for SUBACK or the send slot), or while no request owns the topic, is dropped by the request manager. | protection, pinned |
| L3 | Late `200` for B, B's own answer negative: B is shown as accepted (estimate, 5/20 s checks scheduled). B's real negative answer, handled by the device-level listener, sets `last_command_error` and cancels B's checks. The estimate stays until the device reports. | reproduced, pinned, residual risk |
| L4 | Late negative answer for B, B accepted by the gateway: the action raises `request_rejected`, no estimate, no check. B's real `200` clears `last_command_error`. The displayed state is corrected only by a device report. | reproduced, pinned, residual risk |
| L5 | Local generations (`async_cancel_pending` on reconnect or health failure, `async_cancel_device` on removal) and caller cancellation invalidate A locally but cannot label A's wire answer; it reaches B in the same way as after a timeout. | reproduced, pinned (plan A3: no such claim) |
| L6 | Same mechanism on `getAnswer/config/system/uptime`: a late uptime answer can complete the next health probe. It proves only that the gateway answered; no device state is derived (A2). | reproduced, pinned |
| L7 | An answer to another MQTT client on the same topic (MyOPUS app, a second integration) is indistinguishable and is taken by a waiting request. | reproduced, pinned |
| P1 | Answers never cross answer topics; the shared global send queue does not share attribution. | protection, pinned |
| P2 | Retained answers are ignored by the request manager and by the device-level listener (A4). | protection, pinned |
| P3 | No delay, cooldown, drain or retry follows a timeout (A3); every scenario publishes exactly the requests the callers issued (A1); availability never changes (A2). | protection, pinned |

## 2. Existing mechanism (analysis of `mqtt_transport.py` and `coordinator.py`)

| Element | Behaviour | Effect on late answers |
|---------|-----------|------------------------|
| Per-answer-topic `PriorityLock` (`MQTTRequestManager._locks`) | one local owner per answer topic; priority, then FIFO; idle locks are dropped | B can only start after A has finished (timeout, cancel, answer). It cannot tell an answer of A from one of B. |
| Global send lock (`_send_lock`) | exactly one request in flight across all topics, `MIN_SEND_INTERVAL = 0.0` | after A's timeout the next request is published at once (no cooldown) |
| `sent` flag in `handle_response` | answers before publish are ignored | L2 |
| `retain` check in `handle_response` and `_handle_put_answer_state` | retained answers ignored | P2 |
| Route table for `putAnswer/devices/+/state` and the uptime answer | the persistent wildcard listener feeds `async_route`; a handler exists only while a request owns the topic | answers without an owner are dropped by the request manager |
| Temporary subscriptions (configuration, parameters, profile, configuration write) | subscribed per request, SUBACK awaited, removed on completion | same attribution rules; the late answer has no listener after A ended |
| Device-level listener `_handle_put_answer_state` | runs for every non-retained `putAnswer/devices/<DEV>/state`, after routing: a negative answer sets `last_command_error` and cancels the device's pending status checks; a success clears the marker | L3, L4 and the "no owner" row of the protocol contract section 7.2 |
| Generations (`_generation`, `_device_generations`) | invalidate queued and active local requests | cannot label wire answers (L5) |
| Timeout start | at publish, not at enqueue or SUBACK | unchanged |

Requests that use answer topics through the request manager:
`putAnswer/devices/<DEV>/state` (commands, interactive status queries,
background reconciliation queries), `getAnswer/config/system/uptime` (setup
probe, 60 s health tick; the config flow uses its own manager),
`getAnswer/devices/<DEV>/configuration`, `…/parameters`, `…/profile` and
`putAnswer/devices/<DEV>/configuration` (service actions).
`get/devices` and `get/config/system/info` are published without awaiting an
answer; their answers are processed as data (`getAnswer/devices` with the
retained check) and are not attributed to a request.

Conditions for L1: a timeout (10 s without answer) or a cancellation after
publish, a later answer that is not lost, and a next request on the same topic
published before that answer arrives. After a command timeout no new status
check is scheduled, but checks still pending from an earlier accepted command
on the same device, the bridge-native outbound path and the user can supply
that next request.

## 3. Correlation information

| Information | Source | Usable for attribution? |
|-------------|--------|-------------------------|
| Request ID, echo of topic or payload | not part of the reference answer format | does not exist |
| Answer topic | reference section 3 | identifies the device and endpoint, not the request |
| `header.httpStatus` | reference section 4 | outcome only |
| `header.gateway` | reference section 4 example | firmware string only |
| `header.timestamp` | reference section 4 example (`2024-11-14T13:20:50.905+0100`) | **not established.** Gateway clock with unknown offset to Home Assistant; presence on every firmware, monotonicity (clock set, NTP) and the relation to the time the request was processed are unverified. Plan section 12: wire times only after their reliability is established. Not read by the integration. |
| MQTT response topic / correlation data | not used by the reference (fixed answer topics) | not available |
| Local generations, arrival order | integration-local | cannot label an old wire answer (L5) |

Conclusion: no reliable correlation information exists. A local counter can
know *that* an answer is outstanding, never *which* answer arrived.

## 4. Reproducers and consequences

All tests drive the real `MQTTRequestManager`, coordinator, device model and
switch entity against the deterministic broker of
`tests/test_coordinator_transport.py`. Coordinator tests deliver answers
through `_handle_put_answer_state`, the production path of the persistent
`putAnswer` wildcard listener, after `_register_answer_routes()`.

| Test | Pins |
|------|------|
| `test_timeout_values_and_pacing_are_unchanged` | `REQUEST_TIMEOUT` 10 s, `SUBSCRIPTION_TIMEOUT` 30 s, setup bounds 30/180 s, `MIN_SEND_INTERVAL` 0 (A3) |
| `test_late_answer_before_next_publish_is_discarded` | L2 |
| `test_late_answer_after_next_publish_is_taken_as_its_answer` (`200`, `201`) | L1, published within 20 loop iterations (no cooldown), no retry |
| `test_late_rejection_fails_the_next_request` | L1 with a negative late answer |
| `test_shift_continues_while_requests_overlap_and_ends_on_a_gap` | L1 cascade and realignment |
| `test_late_answer_never_crosses_answer_topics` | P1 |
| `test_retained_late_answer_is_never_taken` | P2 (request manager) |
| `test_generation_bump_cannot_label_an_old_wire_answer` (`request_cancelled`, `gateway_unavailable`) | L5 (reconnect, health failure) |
| `test_device_removal_cannot_label_an_old_wire_answer` | L5 (removal) |
| `test_cancelled_caller_leaves_an_unclaimed_answer_like_a_timeout` | L5 (caller cancellation) |
| `test_late_uptime_answer_is_taken_by_the_next_health_probe` | L6 |
| `test_answer_to_another_client_is_indistinguishable` | L7 |
| `test_late_success_masks_rejection_of_next_command` | L3 through the switch entity: estimate, checks scheduled then cancelled, 2 publications, available |
| `test_late_rejection_fails_next_command_the_gateway_accepted` | L4 through the switch entity |
| `test_late_answer_without_waiting_request_touches_only_device_marker` | no owner: marker only; pending checks of an earlier command cancelled (fewer requests, never more) |
| `test_retained_answer_reaches_neither_request_nor_device_marker` | P2 (device-level listener) |

These tests pin current behaviour. They pass on the base revision by
construction, because no production code changed. They are written so that a
later mitigation must update them deliberately (the limitation tests change
their expectation, the protection tests must keep passing).

Consequences for a user:

| Situation | Shown in Home Assistant | Corrected by |
|-----------|-------------------------|--------------|
| L3 false success | commanded value as estimate, checks cancelled, `last_command_error` set | next device report (live stream or telegram) |
| L4 false error | action fails with `request_rejected`, last reported state kept | next device report |
| Late `201` taken for B (B really `200`) | B treated as deferred: no estimate, no checks, deferred marker | device report or outbound telegram |
| Late `200` taken for B (B really `201`) | B treated as sent: estimate and checks (the pre-phase-11 behaviour) | the checks or a device report |
| Reconciliation query takes a stray answer | only the acknowledgement is affected; state still comes from the device | n/a |

## 5. Mitigation evaluation

| ID | Option | Assessment | Decision |
|----|--------|------------|----------|
| M1 | Global cooldown after a timeout | prohibited by A3; delays every device | rejected |
| M2 | Longer `REQUEST_TIMEOUT` | production timeout change (separate design decision); slower error feedback; moves, does not remove, the window | not proposed |
| M3 | Per-topic quarantine/drain: hold the next publish on the topic until a stray answer arrives or a bound expires | needs a defensible lateness bound; none is measured (H2). Adds latency to user commands on that device; waits the full bound if the answer was lost. Plan section 14 allows it only with such a bound. | deferred until H1/H2 exist |
| M4 | Count-based skip: after an unresolved timeout, discard the first answer and take the second | assumes exactly-once, in-order answers (H1, unverified). If A's answer was lost, B discards its own answer and times out: false failure, and a user repeat adds radio traffic | rejected without H1 |
| M5 | Order answers by `header.timestamp` | reliability not established (section 3, H3); plan section 12 forbids wire times before that | deferred until H3 |
| M6 | Diagnostic-only marker: count published-but-unanswered requests per answer topic (timeout or cancellation after publish); flag a result obtained while the count is above zero as "possibly late" in the debug log and diagnostics; decrement on an answer without owner; clear on reconnect, removal and unload | no publish, no delay, no behaviour change for entities (A1-A5 neutral). Needs a rule for a lost answer (the flag would otherwise stay): bounded expiry of the diagnostic flag only. Changes no state decision, so it is a transparency aid, not a fix | candidate; needs maintainer approval in a separate change |
| M7 | Keep B's checks when its real negative answer arrives after a misattributed success (L3) | the checks would correct the estimate, but they are two requests that the current code does not send; under A1 not without a reviewed traffic decision | not proposed |

None of the options removes the ambiguity without hardware evidence. Phase 12
therefore documents the limitation (README, protocol contract section 7.2)
instead of adding a blanket cooldown, as plan section 14 requires.

## 6. Guardrails A1-A5

| Guardrail | Effect |
|-----------|--------|
| A1 | No production change. Every scenario asserts the exact publication count (only caller-issued requests). The only automatic effect of a late answer, cancelling pending checks after a negative answer, reduces traffic and is unchanged. |
| A2 | Every coordinator scenario asserts `coordinator.available is True`. A late uptime answer derives no device state. |
| A3 | Timeouts, priorities, both lock levels, `MIN_SEND_INTERVAL` and the no-retry rule are unchanged and pinned. No cooldown, no drain, no write retry. |
| A4 | Retained answers stay ignored on both paths; two tests pin it. |
| A5 | No entity, unique ID, entity ID, name, category, default or registry entry changed. |

## 7. Files

| File | Change |
|------|--------|
| `tests/test_late_responses.py` | **New:** 16 test functions, 18 cases (section 4) |
| `.github/workflows/tests.yml` | new suite added to both three-fold race repetitions (plan section 17: "Add freshness/ACK/late-response suites when implemented") |
| `docs/quality/protocol-contract.md` | new section 7.2; section 11 (unverified ordering, lateness, timestamp) |
| `docs/quality/implementation-plan.md` | section 7 status |
| `README.md` | "Timeouts without a request ID" (user-facing consequences), test list |
| `docs/quality/phases/phase-12.md` | **New:** this report |

No file under `custom_components/` changed.

## 8. Gateway/cache versus radio counts

Unchanged: no production code changed. In every reproduced scenario the broker
records only the requests issued by the test (one per command, query or
probe); no status query, retry or extra subscription is added.
`tests/test_traffic_baseline.py` is not modified. MQTT counts are not radio
transmissions; no hardware claim is made.

## 9. Commands and results

| Check | Result | Origin |
|-------|--------|--------|
| `ruff check .` | passed | local, ruff 0.16.3 (pinned version, binary from the GitHub release) |
| `ruff format --check .` | passed (95 files) | local, ruff 0.16.3 |
| `pytest tests/test_late_responses.py`, three repetitions | 18 passed each time | local smoke run, **not authoritative**: CPython 3.14.6, pytest 9.1.1, with a minimal stand-in for Home Assistant and a small asyncio runner instead of pytest-asyncio (both kept outside the repository). The integration modules under test (`mqtt_transport.py`, `coordinator.py`, `enocean_device.py`, `entity.py`, `switch.py`) were the real ones. |
| Sensitivity check (local, same harness, reverted) | removing the `sent` check fails `test_late_answer_before_next_publish_is_discarded`; removing the retained check in `handle_response` fails both retained tests; `MIN_SEND_INTERVAL = 0.2` fails 16 of 18 cases | local smoke run |
| pip check, strict mypy, full pytest with real Home Assistant, coverage gates, hassfest, race repetitions | not executed locally (PyPI is not reachable from this environment) | – |
| pip check, Ruff, strict mypy, full pytest incl. `test_late_responses.py`, total and per-module coverage gates, three race repetitions incl. `test_late_responses.py` and `test_traffic_baseline.py`, traffic baseline summary | passed | CI, job "Home Assistant 2026.10.2 (fixed reference)" `118267800617`, run `39313178932` (workflow_dispatch, head `7c9c67c`), 2026-10-19 21:42–21:51 +0200 |
| Same gates against the latest stable release | passed | CI, job "Home Assistant (latest stable)" `118267800547`, same run |
| Hassfest built from core `8d7fd8b` and icon validation against the reference MDI catalogue | passed | CI, job "Hassfest at Home Assistant 2026.10.2 (fixed reference)" `118267800648`, same run |
| Hassfest `@master` (moving) | passed | CI, job "validate" `118267800098`, same run |

Strict mypy covers `custom_components/opus_greennet` only; no file in its
scope changed.

## 10. Registry and identity compatibility

No unique ID, entity ID, name, translation key, device class, category or
default enablement changed; no entity is created or removed; existing enabled
and disabled entries are untouched. Diagnostics are unchanged.

## 11. Hardware observations, unknowns and residual risks

No hardware was used. Evidence needed before any behaviour change (M3-M6):

| ID | Question | Why it matters |
|----|----------|----------------|
| H1 | Does the gateway answer every `put`/`get` exactly once and in request order, also under radio load and for ACK `201`? | M4 and the realignment assumption of L1 |
| H2 | Distribution and maximum of the time from publish to answer, including answers after 10 s | the lateness bound for M3, a timeout decision (M2) |
| H3 | Is `header.timestamp` present on every firmware, monotonic, and set when the request is processed? | M5 |
| H4 | Do commands from MyOPUS or other clients produce answers on the same MQTT answer topic? | frequency of L7 |
| H5 | How often do timeouts occur in practice (per device, per profile)? | whether M6 is worth its complexity |

Residual risks (documented in README and protocol contract section 7.2):
L1, L3, L4, L6, L7. Each is bounded to one device's answer topic, never
changes gateway availability, never adds a request, and is corrected by the
next device report.

## 12. Review status and next step

Awaiting review of the branch (CI green). Merge and release authorisation remain
separate. Decision requested from the maintainer: accept the documented
limitation as the phase 12 outcome, and decide whether M6 (diagnostic-only
marker) should be implemented in a separate change. Next planned phase:
phase 13 (final rule audit); the per-entity inventory of resumed phase 6
stays open.
