# Phase 21 – REST plan R5: stream-first start and fresh list metadata

- **Status:** completed. Branch `feat/opus-r5-stream-first-start` (`a9e8517`)
  merged by the maintainer into `feature/rest-api-integration` via PR #25
  (merge commit `2d3cf9c`, 2026-10-20). CI green (section 5). No version change, no CHANGELOG entry, no
  release; `main` untouched (branch policy of 2026-10-20).
- **Plan:** `Implementations-Plan-Rest-API.md` phase R5 (section 8), section
  7 (start and resynchronisation, design decision E7), findings B-8 and B-5
  (section 2.6). Guardrails per decision D3: A1–A5 for the MQTT path and
  shared code, best practices and Home Assistant requirements for REST.
  Report structure: `implementation-plan.md` section 20.
- **Numbering:** R5 is reported as phase 21 (R4 = 18, R4.2 = 19, R4.3 = 20).
- **Hardware:** the maintainer confirmed on 2026-10-20 that
  `feature/rest-api-integration` (state of phase 20) works with the
  hardware. This phase itself is not yet tested on hardware (section 7).
- This branch also carries the merge record of phase 20 (PR #24, `bcb6276`;
  commit cherry-picked from the unpushed branch `docs/opus-r4-3-record-merge`).

## 1. Scope, base and reference versions

| Field | Value |
|-------|-------|
| Repository | `fubu2k/ha-opus-greennet` |
| Branch | `feat/opus-r5-stream-first-start` (new) |
| Base | `feature/rest-api-integration` `bcb6276` ("Merge pull request #24", R4.3) |
| `main` | not touched |
| Home Assistant reference | `2026.10.2`, home-assistant/core `8d7fd8b68677c2585be36ec86043308f403875b8` (unchanged) |

In scope (only this):

1. Stream first after section 7.2 (E7, B-8): no `GET /devices` at setup; the
   stream's first device list is the setup snapshot; a deadline for it; the
   measured time.
2. B-5: metadata of the 30 min list only when no newer telegram arrived since
   the request started.
3. Tests: race request/telegram, reconnect with snapshot, no button event
   from snapshots, setup failure without a first document.
4. Measurable: one device list fewer per start and reload.

Out of scope: domain model and in-place update (R6: B-4, A-5), the health
probe, the stream backoff (R4), any change of the MQTT transport.

## 2. Behaviour and guardrails

| ID | MQTT path (A1–A5 binding) | REST path (D3) |
|----|---------------------------|----------------|
| A1 | No MQTT file changed; `test_traffic_baseline.py` unchanged | One `GET /devices` fewer per setup and reload; no request added; no radio traffic in either case |
| A2 | untouched | Gateway health never sets device state; setup is available only after a current device list was applied |
| A3 | untouched | No retry added: a failed setup is retried by Home Assistant (`ConfigEntryNotReady`), not by the integration |
| A4 | untouched | n/a |
| A5 | untouched | No unique ID, entity ID, device identifier or registry setting changed |

### 2.1 REST behaviour changes (disclosed per D3)

| Situation | Before | After |
|-----------|--------|-------|
| Setup / reload | `GET /system/info`, `GET /system/uptime`, `GET /devices`, then the stream (its first document is the same list again) | `GET /system/info`, `GET /system/uptime`, then the stream; setup waits for its first device list and applies only that one |
| Stream does not deliver a list within 30 s | setup succeeded (list from `GET /devices`); stream retried in the background | setup fails with `ConfigEntryNotReady` ("not ready, Home Assistant will retry"); no background attempt remains |
| First stream attempt fails or ends before its list | setup succeeded; entities became unavailable until a stream connected | setup fails at once with that error (`ConfigEntryNotReady`; 401/403 → reauthentication via `ConfigEntryAuthFailed`, the runtime reauth callback is not started a second time) |
| Telegram arrives while the 30 min list is being read | the list replaced the telegram's newer `dbm` and `lastSeen` | the telegram's values stay (B-5) |
| Stream errors after setup | reconnect with backoff (R4) | unchanged |

Decision on the deadline: **30 s** (`FIRST_DEVICE_LIST_TIMEOUT`). The first
document is the same list as `GET /devices`, which has a 10 s request
timeout; three times that leaves room for large installations without
holding Home Assistant's startup long. Upstream reports the stream on a
65-device installation within milliseconds **[HW-upstream]**. Because the
plan asks to measure it, the time is logged (debug) and exposed in
diagnostics as `gateway.first_device_list_seconds`; with that value from the
real gateway the deadline can be confirmed or adjusted. `GET /system/uptime`
stays at setup: it is the baseline of the restart detection of the health
probe.

### 2.2 Why B-5 uses "telegram after request start"

The device list and telegrams travel on different connections, so their
order is unknown. A telegram received after the refresh request started may
be newer than the list's values; keeping it can at worst keep values that
are a few seconds older than the list, never values older than the request.
Stream snapshots are not limited this way: they share the connection with
the telegrams and are ordered with them.

## 3. Files and tests

| File | Change |
|------|--------|
| `custom_components/opus_greennet/rest_coordinator.py` | `FIRST_DEVICE_LIST_TIMEOUT`; `async_setup` stream first (future resolved by the stream reader, `asyncio.timeout`, `first_device_list_seconds`); `_resolve_first_device_list`; first-attempt errors fail setup; `_telegram_metadata_at` and `requested_at` for B-5; module docstring |
| `custom_components/opus_greennet/diagnostics.py` | `gateway.first_device_list_seconds` (REST; `None` for MQTT) |
| `tests/test_rest_stream_first.py` | **new**: real HTTP gateway (no `GET /devices` at setup and reload, late list, timeout → retry without reauth, stream 503 → retry without background attempt, stream 401 → one reauth, diagnostics, reconnect snapshot without button event) and scripted stream (documents before the list, end before the list, first errors, auth, timeout, normal reconnect after setup), B-5 (telegram during the refresh, before it, without telegrams, snapshots unaffected, forget) |
| `tests/test_rest_stream.py` | setup request order now ends with `GET /devices/stream`; `GET /devices` not requested |
| `tests/fake_gateway.py` | `snapshot_delay` (stream open, list later) |
| `README.md`, `protocol-contract.md` 12.2/12.3, `Implementations-Plan-Rest-API.md` (R5, 7.2), `implementation-plan.md` §7 | documentation |

Existing tests that cover plan items without change:
`test_rest_stream.py::test_new_stream_resynchronises_state` (reconnect with
snapshot), `::test_device_list_snapshot_does_not_replay_button_presses`.

Reproducers: against the base `rest_coordinator.py`, the scripted setup tests
fail (setup returns before any stream document; no error from the stream) and
`test_telegram_during_the_refresh_keeps_its_metadata` fails (the list
replaced `dbm`/`lastSeen`); 9 of 12 fail, the 3 B-5 control cases pass.

## 4. Gateway/cache versus potential radio counts

| Traffic | Before | After |
|---------|--------|-------|
| MQTT | baseline | identical |
| Per REST setup or reload | 3 GETs + stream; the device list transferred **twice** | 2 GETs + stream; the device list transferred **once** (R5); **after the follow-up fix (7.2): 3 GETs + stream again**, `GET /devices` once right after setup for the configuration the stream lacks |
| Periodic | uptime 60 s, `GET /devices` 30 min | identical |
| Radio | user commands, status checks, ReCom | identical |

## 5. Commands and results

| Command | Result |
|---------|--------|
| `ruff check .`, `ruff format --check .` (ruff 0.16.9) | passed |
| strict mypy 2.3.1 over the REST modules with typed Home Assistant stand-ins (as phase 18) | 0 findings in the integration |
| pytest (Python 3.14.6, pytest 9.1.1, pytest-asyncio 1.4.0, aiohttp 3.14.5 from source) with the scratch harness of phase 18: the 12 scripted/B-5 cases of `test_rest_stream_first.py` plus `test_rest_stream_backoff.py` and `test_rest_api.py` | **76 passed**, two runs |
| same 12 cases against the base `rest_coordinator.py` | 9 failed, 3 passed (section 3) |

Not run locally (pypi.org blocked): the Home Assistant tests, coverage gates,
Hassfest. CI is the evidence.

| Run | Head | Result |
|-----|------|--------|
| `39458477903` (workflow_dispatch) | `feat/opus-r5-stream-first-start` `a9e8517` (merged unchanged as PR #25, `2d3cf9c`) | **success**: `validate`, `Hassfest at Home Assistant 2026.10.2 (fixed reference)`, `Home Assistant 2026.10.2 (fixed reference)`, `Home Assistant (latest stable)` |

The merge was done while the two test jobs were still running; both finished
green on the merged code afterwards.

## 6. Registry and identity compatibility

No unique ID, entity ID, device identifier, translation key or registry
setting changed. Diagnostics gain one key.

## 7. Hardware observations, unknowns and residual risks

### 7.1 Hardware check after the merge (2026-10-20, maintainer diagnostics)

Gateway OPUS-IQ-DOT v1.21.30 (API 2.6.12.0), 53 devices, Home Assistant
2026.10.2. Two diagnostics files: before R5 (0.7.0b2 build without R5) and
after R5 (merge `2d3cf9c`).

| Observation | Before R5 | After R5 |
|-------------|-----------|----------|
| `gateway.first_device_list_seconds` | – | **0.076 s** **[HW]** |
| `config_entry_setup` (Home Assistant) | 0.23 s | 2.42 s (platform setup included; not attributable to the stream) |
| Covers with `vertical_movement_time` / `rotation_time` | 10 of 10 (25.42–33.0 s / 0.0) | **0 of 10** |
| Channels with a battery level | 20 | 20 |

Conclusions:

1. **Deadline confirmed:** the first device list arrives in 76 ms; 30 s
   leaves a margin of about 400×. `FIRST_DEVICE_LIST_TIMEOUT` stays 30 s.
2. **Finding R5-H1 (regression, fixed in the follow-up below):** the device
   list in the stream carries **no `configuration` block** on this firmware
   **[HW]**; only `GET /devices` does. The setup code path is identical for
   both lists, so the missing movement and rotation times come from the
   stream content. With R5 alone, covers had no travel estimate after every
   start until the 30 min refresh. The assumption "stream list = `GET
   /devices`" of plan section 7.1 does not hold for configuration.

### 7.2 Follow-up fix (branch `fix/opus-r5-movement-times`)

- Setup stays stream first (state, availability and the 30 s deadline
  unchanged). Right after setup, the non-authoritative metadata refresh runs
  once (`GET /devices`, the same code as the 30 min tick): it adds
  configuration and battery, never live state, and keeps newer telegram
  metadata (B-5).
- Traffic per start: back to **one** `GET /devices` per start and reload, as
  before R5 (no increase); the device list is no longer applied twice as
  authoritative state, so the snapshot race of section 7.1 of the plan stays
  removed. The measurable goal "one device list fewer per start" is **not
  reachable** on this firmware and is withdrawn.
- Reconnect snapshots without configuration keep the stored movement times
  (a missing field changes nothing).
- Tests: `test_cover_movement_times_without_configuration_in_the_stream`
  (fake gateway option `stream_without_configuration`), request counts
  adjusted (`test_setup_takes_state_from_the_stream`,
  `test_each_start_reads_the_device_list_once`).

### 7.3 Other notes

- `homekitQR` was still unredacted in the second file: that build predates
  the redaction fix (PR #27, merged afterwards).
- A gateway that delays the list by more than 30 s would now fail setup and
  be retried by Home Assistant, where before the separate `GET /devices`
  succeeded. The log line "not ready, Home Assistant will retry: The event
  stream sent no device list within 30 s" names this case.
- Button presses between `GET /system/uptime` and the list are not replayed
  (unchanged: events never come from snapshots).

## 8. Review status and next step

- Merged via PR #25 (`2d3cf9c`) into `feature/rest-api-integration`, not into
  `main`; CI run `39458477903` green.
- Hardware check done (section 7.1): deadline confirmed (0.076 s); finding
  R5-H1 fixed on `fix/opus-r5-movement-times` (section 7.2).
- Next REST phase: R6 (domain model) or R1, each with its own approval.
- No release permission is implied.
