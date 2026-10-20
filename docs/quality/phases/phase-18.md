# Phase 18 – REST plan R4: event stream lifecycle

- **Status:** completed. Branch `feat/opus-r4-stream-lifecycle` (`5e21cf8`)
  merged by the maintainer into `feature/rest-api-integration` via PR #21
  (merge commit `ae3e084`, 2026-10-20). CI on the merged head green
  (section 5). No version change, no CHANGELOG entry, no release; `main`
  untouched (branch policy of 2026-10-20).
- **Plan:** `Implementations-Plan-Rest-API.md` phase R4 (section 8), findings
  B-2 and B-3 (section 2.6), stream lifecycle table (section 6), acceptance
  row "Gateway schließt Verbindungen sofort" (section 9). Guardrails per
  decision D3 (section 0.1): A1–A5 for the MQTT path and shared code, best
  practices and Home Assistant requirements for REST. Report structure:
  `implementation-plan.md` section 20.
- **Numbering:** REST plan phase R4 is reported as phase 18. R1 (measurement
  of the actuator overrun) is still skipped; R4 does not depend on it.

## 1. Scope, base and reference versions

| Field | Value |
|-------|-------|
| Repository | `fubu2k/ha-opus-greennet` |
| Branch | `feat/opus-r4-stream-lifecycle` (new) |
| Base | `feature/rest-api-integration` `e5b609e4efa0c41cb550b2e0bcb4dd1f9563794e` ("Merge pull request #20", R0/R2 merge status) |
| Base check | `docs/quality/phases/phase-17.md` present on the base, R3 merged (`1c30105`) |
| `main` | not touched (REST transport stays off `main`, PR #15) |
| Home Assistant reference | `2026.10.2`, home-assistant/core `8d7fd8b68677c2585be36ec86043308f403875b8` (unchanged since phase 8) |

In scope (only this):

1. B-2: bounded exponential backoff with jitter; a pause after every stream
   end; short-lived streams (< 10 s) count as failures.
2. Connection generation: a replaced stream reader applies no further
   document and changes no availability.
3. B-3: size check for complete documents, not only for remainders growing
   across chunks.
4. Tests: closing right after the snapshot causes no tight loop; reload and
   stream replacement leave exactly one reader; authentication errors end the
   reader without another attempt.
5. Gateway traffic before/after (section 4).

Out of scope: start sequence and metadata freshness (R5: B-5, B-8), domain
model (R6: B-4, A-5), the idle timeout (900 s, unchanged), any change of the
MQTT transport.

## 2. Authorised changes and guardrails

Authorised by the plan: the five items above. Nothing else was changed.

| ID | MQTT path (A1–A5 binding) | REST path (D3) |
|----|---------------------------|----------------|
| A1 | No file of the MQTT path changed (`coordinator.py`, `mqtt_transport.py`, `discovery.py` untouched); `test_traffic_baseline.py` unchanged | No radio request added. Stream reconnects (gateway-only reads) become less frequent (section 4) |
| A2 | untouched | Availability rules unchanged: a stream that delivered its device list never makes the gateway unavailable; a failed attempt without a document does. Device state is never derived from gateway health |
| A3 | untouched | No command retry, no cooldown on commands; timeouts unchanged. The backoff applies only to reopening the event stream |
| A4 | untouched | n/a (no retained messages on REST) |
| A5 | untouched | No unique ID, entity ID, device identifier, registry setting or transport changed |

### 2.1 REST behaviour changes (disclosed per D3)

| Situation | Before (R2/R3) | After (R4) |
|-----------|----------------|------------|
| Stream ends after it delivered a document | reopened **immediately**, failure counter reset | pause `stream_retry_delay(failures)`; counter reset only when the stream stayed open ≥ 10 s (`STREAM_MIN_HEALTHY_SECONDS`) |
| Gateway closes every stream right after the device list | **tight loop**: full device list download per round trip, unbounded (B-2) | pauses 1, 2, 4, 8, 16, 32, 60, 60 … s (+0–25 %) |
| Attempt fails before any document | pauses 1, 2, 5, 10, 30, 60 s (fixed, no jitter) | nominal 1, 2, 4, 8, 16, 32, 60 s, each lengthened by a random 0–25 %; bound 75 s |
| Stream open ≥ 10 s ends (e.g. idle timeout 900 s) | immediate reopen | reopen after 1–1.25 s |
| Rejected credentials (401/403) on the stream | reader ends, reauth once | unchanged (now pinned by a unit test without HTTP) |
| Restart of the stream (failed probe, falling uptime) while the old reader still holds parsed documents | old reader could apply documents of the same chunk and set `_stream_connected`/availability after its cancellation was requested | old reader drops them; only the current generation applies state |
| Replaced reader leaves its loop early | HTTP response released when the generator is garbage collected | released at once (`contextlib.aclosing`) |
| Complete document larger than 8 MB inside one chunk, or completed in a later chunk | accepted (B-3) | `OpusResponseError("An event stream document is too large")`, stream reopened with backoff |

Visible effects:

- After the gateway goes down, entities become unavailable about 1 s later
  than before: the closed stream is now followed by a pause before the
  failing attempt that marks the gateway unavailable. The 60 s health probe
  is unchanged and still detects outages independently.
- After an ordinary stream end (idle timeout, gateway closing a long-lived
  connection), live updates resume about 1 s later than before. The new
  stream starts with the full device list, so no state is lost; button
  presses within that second are not replayed (as before, events are never
  created from snapshots).

### 2.2 Backoff decision and justification

| Choice | Value | Justification |
|--------|-------|---------------|
| Form | bounded exponential, factor 2 | Standard for reconnect loops; reaches the bound in 6 steps |
| Initial / bound | 1 s / 60 s | Same first pause and same steady-state interval as R2, so a long outage is never retried more often than before |
| Jitter | random 0–25 % **added** to the nominal pause | Decorrelates several Home Assistant instances that lose the same gateway at once (gateway reboot), while the pause is never shorter than the nominal value. "Full jitter" (0–100 % of the pause) was rejected because it can retry almost immediately and would increase gateway load in the worst case |
| Short-lived threshold | 10 s (`STREAM_MIN_HEALTHY_SECONDS`) | Value suggested by the plan. A real stream that delivered its list and then stays open for 10 s shows a working gateway; closing sooner is treated as a failure so the backoff can grow |
| Availability on short-lived streams | unchanged (available) | The gateway answered with a current device list; marking it unavailable would cancel estimates and reconciliation and make entities flap |
| Authentication errors | no further attempt | Unchanged; repeating rejected credentials would only lock accounts or flood the log. Reauth flow is started once |

## 3. Files, requirements and reproducers

| File | Change | Requirement |
|------|--------|-------------|
| `custom_components/opus_greennet/rest_coordinator.py` | `STREAM_RETRY_DELAYS` replaced by `STREAM_BACKOFF_INITIAL/MAX/JITTER`, `STREAM_MIN_HEALTHY_SECONDS` and `stream_retry_delay()`; `_stream_generation`, `_is_current_stream()`; `_async_run_stream(generation)` pauses after every stream, resets only after a healthy stream, ignores documents and errors of a replaced reader, uses `aclosing` | B-2, generation |
| `custom_components/opus_greennet/api.py` | `JsonDocumentSplitter.feed` checks every completed document against `max_size`; `async_stream` annotated `AsyncGenerator` (needed for `aclosing`, no runtime change) | B-3 |
| `tests/test_rest_stream_backoff.py` | **new**, 18 test cases with a mocked Home Assistant and a scripted client (no network, `asyncio.sleep` replaced): schedule, jitter bounds, never more frequent than before, short-lived streams, healthy reset at exactly 10 s, failures, unexpected errors, auth without retry, generation (stale documents, availability, auth), `aclosing`, cancellation during the pause | R4 tests |
| `tests/test_rest_stream.py` | fixture patches the new constants instead of `STREAM_RETRY_DELAYS`; **new** integration tests with the fake HTTP gateway: closing after every snapshot (bounded attempts, entity stays available), reload leaves one reader, stream replacement leaves one reader and one stream task | R4 tests |
| `tests/test_rest_api.py` | **new** splitter tests: complete document in one chunk, document completed in a later chunk, documents up to the limit, default limit unchanged | B-3 |
| `tests/test_rest_edge_cases.py` | `test_stream_loop_ends_after_unload` passes the generation argument; assertion unchanged | signature |
| `tests/fake_gateway.py` | new attribute `close_after_snapshot` (end every stream right after its device list) | test support |
| `README.md`, `protocol-contract.md` 12.3/12.4, `Implementations-Plan-Rest-API.md` (R4 status, section 6), `implementation-plan.md` section 7 | documentation | delivery list |

Reproducers (failing before, passing after), executed locally against the
base code (section 5):

- B-2: a scratch driver runs the base `_async_run_stream` against a client
  whose every stream delivers the device list and ends. Base: **50 attempts,
  0 pauses** (stopped only by the driver). Branch: 8 attempts with pauses
  `[1, 2, 4, 8, 16, 32, 60, 60]`. The same scenario is pinned by
  `test_stream_closed_after_snapshot_backs_off_instead_of_looping`.
- B-3: `test_splitter_limits_a_complete_document_within_one_chunk` and
  `test_splitter_limits_a_document_completed_in_a_later_chunk` fail on the
  base `api.py` (no error raised) and pass on the branch.
- `tests/test_rest_stream_backoff.py` cannot be imported on the base (the new
  constants and `stream_retry_delay` do not exist).

## 4. Gateway/cache versus potential radio counts

Every stream attempt is one `GET /devices/stream`; a successful one transfers
the complete device list from the gateway's model. **No attempt sends a radio
telegram or queries a device.**

| Traffic | Before | After |
|---------|--------|-------|
| MQTT publishes per endpoint and trigger | baseline of `test_traffic_baseline.py` | identical (no MQTT file changed) |
| REST periodic reads | `GET /system/uptime` 60 s, `GET /devices` 30 min | identical |
| Radio requests | user commands; at most two status checks per unconfirmed channel; explicit ReCom actions | identical |
| Stream opens, gateway closes every stream after the list | unbounded (one per round trip, each with a full device list) | ≤ 6 in the first minute, then ≤ 1 per 60 s |
| Stream opens, gateway unreachable | first minute: 6 attempts (0, 1, 3, 8, 18, 48 s); then 1 per 60 s | first minute: ≤ 6 attempts (earliest 0, 1, 3, 7, 15, 31 s); then 1 per 60–75 s |
| Stream opens after a healthy stream ended | 1, immediately | 1, after 1–1.25 s |

Net effect: the number of stream opens can only fall; in the error case of
B-2 it falls from unbounded to bounded.

## 5. Commands and results

### Local checks (executed in the authoring environment)

The package index (pypi.org) is blocked from the authoring environment, as in
phases 16 and 17, so Home Assistant could not be installed. Locally executed:

| Command | Result |
|---------|--------|
| `ruff check .`, `ruff format --check .` (ruff 0.16.9) | passed |
| `pytest tests/test_rest_stream_backoff.py tests/test_rest_api.py tests/test_rest_client_hardening.py` with Python 3.14.6, pytest 9.1.1, pytest-asyncio 1.4.0 and aiohttp 3.14.5 (pure-Python mode, from source), via a scratch harness that loads the integration as a namespace package with minimal stand-ins for `homeassistant.core`, `.exceptions`, `.helpers.dispatcher` and `.helpers.event` | **89 passed**, three consecutive runs |
| same harness, `tests/test_rest_api.py` and `tests/test_rest_client_hardening.py` against the base `api.py`/`rest_coordinator.py` | 2 failed (the B-3 reproducers), 69 passed |
| B-2 scratch driver on base and branch | base 50 attempts / 0 pauses; branch 8 attempts / bounded pauses (section 3) |
| strict mypy 2.3.1 (`--strict --warn-unreachable`) over `api`, `rest_coordinator`, `domain`, `const`, `enocean_device`, `protocol`, `wire` with typed stand-ins for the four Home Assistant modules (PEP 758 `except A, B:` parenthesised in scratch copies because local mypy parses with Python 3.13) | **0 findings in the integration** |
| negative control: `NEGATIVE_CONTROL: int = "x"` appended to `rest_coordinator.py` | reported by mypy, so the check is effective |

Not run locally: every test that needs Home Assistant (including the new
integration tests in `tests/test_rest_stream.py` and the whole MQTT suite),
coverage gates, Hassfest. **CI is the test evidence.**

Note for CI review: tests outside `test_rest_stream.py` that close the stream
(`test_rest_cover_movement.py`, `test_rest_devices.py`,
`test_rest_requests.py::test_unreachable_gateway_fails_fast`) now wait about
1 s for the reconnect instead of none; their `wait_until` timeout is 5 s.

### CI (authoritative, to be recorded)

| Run | Head | Result |
|-----|------|--------|
| `39431240243` (workflow_dispatch) | `feature/rest-api-integration` `ae3e084` (merge of PR #21; code identical to `5e21cf8`) | **success**: `validate`, `Hassfest at Home Assistant 2026.10.2 (fixed reference)`, `Home Assistant 2026.10.2 (fixed reference)`, `Home Assistant (latest stable)` |

No CI run ran on the branch before the merge (the workflow starts on pull
requests only for base `main`); the run above on the merged head is the test
evidence of this phase, including the Home Assistant tests that could not run
locally (`tests/test_rest_stream.py` integration tests, the whole MQTT suite,
coverage gates and the three repetitions of the lifecycle and race suites).
Exact test counts and coverage percentages are in the job logs and the
`reference-baseline-*` artifact; they are not quoted here.

## 6. Registry and identity compatibility

No unique ID, entity ID, device identifier, translation key, entity category,
default enablement, icon or registry migration changed. No entity was added
or removed. The transport of existing entries is unchanged. No translation
string changed.

## 7. Documentation updates

- `protocol-contract.md` 12.3: document size limit for every document,
  connection generation and `aclosing`; 12.4: new backoff, short-lived
  streams, no retry on rejected credentials.
- `README.md`: REST transport section (reconnect pauses), project structure
  (new test module).
- `Implementations-Plan-Rest-API.md`: R4 status, section 6 marked implemented.
- `implementation-plan.md` section 7: status entry for phase 18.

## 8. Hardware observations, unknowns and residual risks

- No hardware test. Whether any OPUS firmware closes streams right after the
  device list is **[unverified]**; the change bounds the load if one does.
- How long a real stream stays open without data before the gateway or a
  network device closes it is **[unverified]**. If it is shorter than 10 s,
  such streams now back off up to 60 s between opens and live updates can
  pause for that time; the entities stay available and every reopen
  resynchronises state. The debug log line "closed after less than 10 s"
  makes this visible.
- The jitter uses `random.random()` (not cryptographic); it only spreads
  reconnect times.
- Local tests used aiohttp 3.14.5 from source in pure-Python mode and
  stand-ins for four Home Assistant modules; CI uses the real reference
  environment.
- B-5/B-8 (start, metadata freshness) and B-4 (in-place update) remain for
  R5–R6.

## 9. Review status and next step

- Merged via PR #21 (`ae3e084`) into `feature/rest-api-integration`, not into
  `main`; CI run `39431240243` green on the merged head (section 5). This
  record was added on branch `docs/opus-r4-record-ci`; it changes no code,
  test or workflow.
- Next REST phase: R5 (start and resynchronisation) or R1 (measurement), each
  with its own approval.
- No release permission is implied.
