# Phase 17 – REST plan R3: REST client hardening and library boundary

- **Status:** prepared on branch `feat/opus-r3-rest-client-hardening`. Not
  merged; no pull request opened, no version change, no CHANGELOG entry, no
  release. Merge approval remains separate (`implementation-plan.md` sections 4
  and 5); the merge target is `feature/rest-api-integration`, not `main`
  (branch policy of 2026-10-20).
- **Plan:** `Implementations-Plan-Rest-API.md` phase R3 (section 8), findings
  B-1 and B-6 (section 2.6), design decision E5 (section 5.1). Guardrails per
  decision D3 (section 0.1): A1–A5 for the MQTT path and shared code, best
  practices and Home Assistant requirements for REST. Report structure:
  `implementation-plan.md` section 20.
- **Numbering:** REST plan phase R3 is reported as phase 17. R1 (measurement of
  the actuator overrun) is still skipped at the maintainer's request; R3 does
  not depend on it.

## 1. Scope, base and reference versions

| Field | Value |
|-------|-------|
| Repository | `fubu2k/ha-opus-greennet` |
| Branch | `feat/opus-r3-rest-client-hardening` (new) |
| Base | `feature/rest-api-integration` `b9c3fb0fcc1b2cd12a780a3e92accec59264cc54` ("Merge pull request #17", R2) |
| Base check | `docs/quality/phases/phase-16.md` present on the base |
| `main` | not touched (REST transport stays off `main`, PR #15) |
| Home Assistant reference | `2026.10.2`, home-assistant/core `8d7fd8b68677c2585be36ec86043308f403875b8` (unchanged since phase 8) |

In scope (only this):

1. B-1: evaluate the HTTP status line in addition to the document status
   `header.httpStatus`; the error class carries both values.
2. B-6: move `PriorityLock` and the request priorities from `api.py` into
   `rest_coordinator.py`; `api.py` keeps no integration policy.
3. Tests: no Home Assistant or integration import in `api.py`/`wire.py`
   (AST), no own `ClientSession(`, error texts without credentials, no
   repetition of a timed-out PUT.
4. Decide concurrency, timeouts and retries of the REST client by best
   practice and justify the decision (section 2).

Out of scope: stream lifecycle and backoff (R4: B-2, B-3), start sequence and
metadata freshness (R5: B-5, B-8), domain model (R6: B-4, A-5), any change of
the MQTT transport.

## 2. Authorised changes and guardrails

Authorised by the plan: the four items above. Nothing else was changed.

| ID | MQTT path (A1–A5 binding) | REST path (D3) |
|----|---------------------------|----------------|
| A1 | No file of the MQTT path changed (`coordinator.py`, `mqtt_transport.py`, `discovery.py` untouched); `test_traffic_baseline.py` unchanged | No request added; no retry added (section 4) |
| A2 | untouched | A health probe whose answer has a non-2xx HTTP status now counts as failed (section 2.1); device state is still never derived from gateway health |
| A3 | untouched | No retry, no cooldown; timeouts unchanged |
| A4 | untouched | n/a (no retained messages on REST) |
| A5 | untouched | No unique ID, entity ID, device identifier, registry setting or transport changed |

### 2.1 REST behaviour change (disclosed per D3)

Before: `api._async_request` evaluated the HTTP status only for 401/403 and
for an empty body with HTTP ≥ 300. With a JSON body, only `header.httpStatus`
decided, so **HTTP 500 with a body reporting 200 counted as success** (B-1).

After:

| HTTP status line | Document `header.httpStatus` | Result |
|------------------|------------------------------|--------|
| 401 / 403 | any | `OpusAuthError` (unchanged) |
| 2xx | 200 | success, `CommandDelivery.SENT` (unchanged) |
| 2xx | 201 | success, `CommandDelivery.DEFERRED` (unchanged) |
| 2xx | other or missing/invalid | `OpusResponseError`, reason from the document (unchanged), now with `http_status` |
| non-2xx | equal to HTTP status | `OpusResponseError` `status <h>: <message>` (unchanged text) |
| non-2xx | 200/201 or another value | **new:** `OpusResponseError` `HTTP status <h>, gateway status <g>[: <message>]` (before: success when `<g>` was 200/201) |
| non-2xx | no readable document (empty, HTML error page, `[]`, invalid status) | **new text:** `status <h>` (before: `status <h>` only for an empty body, otherwise "invalid JSON") |

`OpusResponseError` gains the keyword attributes `http_status` and
`gateway_status` (`None` when unknown). `status` stays the status that failed:
the HTTP status for a failed status line, otherwise the document status, so
existing callers and the existing message format are unchanged.

Effects of the change:

- Commands and ReCom actions with a non-2xx HTTP status fail with
  `request_rejected` and the reason above instead of being treated as
  acknowledged. No estimate, no travel estimate, no status check follows
  (`test_http_error_with_json_success_fails_the_command`).
- The health probe treats such an uptime answer as failed: the gateway is
  marked unavailable and the stream is replaced, the same path as any probe
  failure (`test_health_probe_rejects_http_error_with_json_success`). This can
  make a REST entry unavailable on a gateway or proxy that answers with a
  non-2xx status line and a valid body. No such answer is known from the
  vendor reference or from upstream hardware observations; the previous
  acceptance was an unverified assumption.
- The config flow and setup use the same client; such an answer now fails
  them with the existing handling of `OpusResponseError`: the config flow
  shows `unsupported_gateway`, entry setup raises `ConfigEntryNotReady`
  (retry by Home Assistant).
- The event stream already required HTTP 200 (unchanged); its error now also
  carries `http_status`.

### 2.2 Concurrency, timeouts and retries (decision and justification)

| Topic | Decision | Justification |
|-------|----------|---------------|
| Radio requests | **unchanged:** exactly one `PUT …/state` or ReCom request in flight per gateway, priorities command 0 < query 10 < background 20 (now `rest_coordinator.PriorityLock`) | The gateway forwards each to the EnOcean radio, which transmits one telegram at a time; parallel requests would only queue inside the gateway, lose the priority order and make the timeout of a queued command start before it is transmitted (`test_request_timeout_starts_when_sent_not_when_queued`). |
| Gateway reads | **unchanged:** `GET /system/uptime`, `GET /devices`, `GET /devices/{id}` bypass the radio queue | They read the gateway's model, reach no radio device and must not wait behind slow ReCom requests (health detection). |
| Timeouts | **unchanged:** 10 s per request (connect 10 s), ReCom 45 s, stream 15 min read idle | 10 s matches the MQTT `REQUEST_TIMEOUT` and is far above the observed acknowledgement time (~40 ms echo after the HTTP answer **[HW-upstream]**); ReCom answers take several seconds over the radio. No measurement justifies another value. |
| Retry of `PUT …/state` | **none** | The gateway may have transmitted the telegram before the answer was lost; a repeat would execute twice at the actuator. Not idempotent in effect: a repeated cover position restarts the movement and its travel estimate; a repeated dimmer command restarts a fade; ACK 201 commands would be queued twice for delivery. A repeat also doubles radio traffic. The user sees the failure (`request_timeout`) and decides. |
| Retry of ReCom writes | **none** | Same as above; configuration writes additionally use the radio for several seconds. |
| Retry of gateway reads | **none in the client** | Each read already has a natural next attempt: the uptime probe every 60 s, the stream reconnect with backoff, the device list every 30 min, the unknown-device lookup after 1 h. An immediate client retry would only add load during an outage. |
| Redirects | unchanged (aiohttp default) | The gateway sends none; the Authorization header is a fixed header, so aiohttp drops it on cross-origin redirects. Not changed without evidence (residual note, section 8). |

The "no retry" decision is pinned by
`test_timed_out_request_is_sent_exactly_once` (PUT and GET),
`test_rejected_put_is_sent_exactly_once` and
`test_command_timeout_is_never_repeated` (coordinator).

## 3. Files, requirements and reproducers

| File | Change | Requirement |
|------|--------|-------------|
| `custom_components/opus_greennet/api.py` | HTTP status checked before the document (`_http_error`); `OpusResponseError` with `http_status`/`gateway_status`; `decode_response` sets `gateway_status`; stream error carries `http_status`; `PriorityLock`, `PRIORITY_*` and the `asyncio`/`heapq`/`itertools` imports removed; module docstring states the boundary | B-1, B-6, E5 |
| `custom_components/opus_greennet/rest_coordinator.py` | `PriorityLock` and `PRIORITY_COMMAND`/`PRIORITY_QUERY`/`PRIORITY_BACKGROUND` moved here verbatim (values 0/10/20); imports adjusted | B-6 |
| `tests/fake_gateway.py` | new attribute `http_status` (per path, HTTP status line of an otherwise unchanged answer) | test support |
| `tests/test_rest_client_hardening.py` | **new**, 25 test cases, no Home Assistant needed: B-1 matrix, no retries, credential-free errors, AST boundary of `api.py`/`wire.py`, no own `ClientSession(`/`TCPConnector(`, injected session never closed | R3 tests |
| `tests/test_rest_client_coordinator.py` | **new**, 6 test cases with Home Assistant: command and health probe with HTTP 500 + JSON 200, ACK 201 semantics with any 2xx, timed-out command never repeated, queue location and priorities | R3 tests |
| `tests/test_rest_api.py` | the three `PriorityLock` tests moved out (imports adjusted); no assertion changed | B-6 |
| `tests/test_rest_commands.py` | imports `PriorityLock`/`PRIORITY_*` from `rest_coordinator`; receives the three moved tests unchanged | B-6 |
| `README.md`, `CLAUDE.md`, `protocol-contract.md` 12.2, `quality-scale-evidence.md` (`inject-websession`), `Implementations-Plan-Rest-API.md` (R3 status), `implementation-plan.md` section 7 | documentation | delivery list |

Reproducers (failing before, passing after), executed locally against the
base code (section 5):

- `test_http_error_with_json_success_is_rejected` (B-1): on the base the PUT
  with HTTP 500 and body status 200 returned the acknowledgement.
- `test_http_error_without_usable_document_reports_http_status` (4 cases):
  on the base an HTML or `[]` body with HTTP 502 was reported as invalid JSON
  or accepted.
- `test_error_texts_never_contain_credentials`: failed on the base only
  because the HTTP 500/JSON 200 case raised no error; no credential appeared
  in any error text on the base either.
- `test_request_priorities_belong_to_the_rest_coordinator` (B-6).
- `test_http_error_with_json_success_fails_the_command` and
  `test_health_probe_rejects_http_error_with_json_success` (coordinator; CI).

13 of the 25 new client tests failed on the base code, all for the reasons
above; the 12 others (session, boundary, retries) already held and now pin the
behaviour.

## 4. Gateway/cache versus potential radio counts

| Traffic | Before | After |
|---------|--------|-------|
| MQTT publishes per endpoint and trigger | baseline of `test_traffic_baseline.py` | identical (no MQTT file changed) |
| REST periodic reads | `GET /system/uptime` 60 s, `GET /devices` 30 min | identical |
| Radio requests | user commands; at most two status checks per unconfirmed channel; explicit ReCom actions | identical in count. One difference in rare error cases: a command answered with non-2xx HTTP and body 200 no longer schedules its status checks (it is treated as rejected), so radio traffic can only fall |
| Client retries | none | none (now pinned by tests) |
| Stream reconnects | per R2 | unchanged; a probe answered with non-2xx HTTP now replaces the stream like any probe failure (one extra stream open only in that error case) |

## 5. Commands and results

### Local checks (executed in the authoring environment)

The package index (pypi.org) is blocked from the authoring environment, as
in phase 16, so Home Assistant could not be installed. Locally executed:

| Command | Result |
|---------|--------|
| `ruff check .`, `ruff format --check .` (ruff 0.16.9) | passed (126 files) |
| Python 3.14.6 `compile()` of every module and test | passed |
| `pytest tests/test_rest_api.py tests/test_rest_client_hardening.py` with Python 3.14.6, pytest 9.1.1, pytest-asyncio and aiohttp 3.14.5 built from their GitHub sources (pure-Python mode), via a scratch harness that loads `custom_components.opus_greennet` as a namespace so `api.py` imports without Home Assistant | **67 passed**, three consecutive runs |
| same harness on the base code, `tests/test_rest_api.py` only | 45 passed (the 42 remaining plus the 3 moved lock tests) |
| same harness, new client tests against the base `api.py` | 13 failed, 12 passed (reproducers, section 3) |
| strict mypy 2.3.1 against home-assistant/core sources at `8d7fd8b6` (scratch copies with PEP 758 `except A, B:` parenthesised because local mypy parses with Python 3.13; third-party stubs missing) | **0 findings in the integration** on base and branch; remaining findings only in the attrs stubs of the source checkout |
| negative control: `NEGATIVE_CONTROL: int = "x"` appended to `api.py` | reported by mypy, so the check is effective |
| import check: every name imported from `api` by the integration and the tests exists in `api.py`; no string reference to `api.PriorityLock`/`api.PRIORITY_*` remains | passed |

Not run locally: every test that needs Home Assistant (including
`tests/test_rest_client_coordinator.py`, `tests/test_rest_commands.py` and the
whole MQTT suite), coverage gates, Hassfest. **CI is the test evidence.**

### CI (authoritative, to be recorded)

| Run | Head | Result |
|-----|------|--------|
| workflow_dispatch "Tests" on `feat/opus-r3-rest-client-hardening` | branch head | **pending** – to be started by the maintainer and recorded here |

A green test job covers ruff, `ruff format --check`, strict mypy, `pytest -v`
with coverage (`fail_under = 97`), the per-module gate
`script/check_coverage.py` (> 95 % per module, `config_flow.py` 100 %) and the
three repetitions of the lifecycle and race suites.

## 6. Registry and identity compatibility

No unique ID, entity ID, device identifier, translation key, entity category,
default enablement, icon or registry migration changed. No entity was added
or removed. The transport of existing entries is unchanged. No translation
string changed: the new reason text is passed through the existing
`request_rejected` placeholder.

## 7. Documentation updates

- `protocol-contract.md` 12.2: separate HTTP/document status, error texts and
  attributes, queue location `rest_coordinator.PriorityLock`.
- `README.md`: REST API section (both statuses, no retries), project
  structure, the two new test modules.
- `CLAUDE.md`: `api.py` boundary, queue in `rest_coordinator.py`.
- `quality-scale-evidence.md`: `inject-websession` evidence row names the new
  tests and this report.
- `Implementations-Plan-Rest-API.md`: R3 status.
- `implementation-plan.md` section 7: status entry for phase 17.
- `phases/phase-14.md` still names `api.PriorityLock`; it is a historical
  report and was left unchanged.

## 8. Hardware observations, unknowns and residual risks

- No hardware test. Whether any OPUS firmware answers with a non-2xx HTTP
  status line and a success document is **[unverified]**; if one does, REST
  entries on it would now fail requests and probes instead of accepting them.
  The failure text names both statuses, so such a case is visible in the log
  and in diagnostics (`last_command_error`).
- Redirect handling is unchanged (aiohttp follows redirects). The gateway sends
  none; disabling redirects would be a separate decision.
- Local tests used aiohttp 3.14.5 from source in pure-Python mode, not the
  exact wheel that Home Assistant 2026.10.2 installs; CI uses the real
  reference environment.
- B-2/B-3 (stream backoff, document size), B-5/B-8 (start, metadata
  freshness) and B-4 (in-place update) remain for R4–R6.

## 9. Review status and next step

- Start the workflow "Tests" (workflow_dispatch) on the branch and record the
  run in section 5.
- Review: open a pull request with base `feature/rest-api-integration` and
  compare `feat/opus-r3-rest-client-hardening` after review.
- Next REST phase: R4 (stream lifecycle) or R1 (measurement), each with its
  own approval.
- No merge, no PR and no release permission is implied.
