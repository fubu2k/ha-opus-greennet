# REST phase 01 – stream life cycle, start-up and REST client (port of R3–R5)

- **Status:** implemented on branch `feat/rest-only-r3-r5`, PR #35, not yet
  merged. CI green (section 5). No version change, no release.
- **Series:** first phase of the REST-only series in `fubu2k/ha-opus-greennet`.
  The base is the REST-only beta v1.0.0b1; the gateway is reached only through
  its EnOcean over IP REST API.
- **Source:** the four commits of the earlier port branch
  `feat/rest-hardening-from-fork`, applied unchanged on top of v1.0.0b1
  (no conflicts).

## 1. Scope and base

| Field | Value |
|-------|-------|
| Integration branch | `feature/rest-only` = v1.0.0b1 `c07630b` |
| Phase branch | `feat/rest-only-r3-r5` |
| Home Assistant reference | `2026.10.2` (CI fixed reference) and latest stable |

In scope: the REST client (R3), the event stream life cycle (R4) and the
start-up (R5), each as in the earlier fork phases.

## 2. Changes

| Area | Change |
|------|--------|
| REST client (`api.py`) | HTTP status line checked separately from `header.httpStatus`; `OpusResponseError` carries `http_status` and `gateway_status`; no retries; no credentials in error texts |
| Stream size | every streamed document limited to 8 MB, also within one chunk |
| Reconnect (`coordinator.py`) | bounded exponential backoff 1 s → 60 s with up to 25 % jitter after every stream end; a stream shorter than 10 s counts as a failure; rejected credentials are never retried; a replaced reader drops late documents and closes its response |
| Start-up | device states come from the stream's first document (30 s deadline, then `ConfigEntryNotReady` or reauthentication); one `GET /devices` right after it reads the cover configuration the streamed list lacks |
| 30 min device list | keeps a newer telegram's signal strength and last-seen time |
| Entities (`entity.py`) | the initial entity write is the projection baseline, so the first unchanged telegram does not rewrite every entity |
| Diagnostics | `first_device_list_seconds`; HomeKit QR payload (`homekitQR`) redacted |

## 3. Guardrails

| ID | Effect |
|----|--------|
| A1 (no additional radio polling) | no new poller, no shorter interval, no command repetition; start-up reads the device list once, as before |
| A2 (uptime never confirms device state) | unchanged; a stream failure marks the gateway unavailable, never a single device timeout |
| A3 (no global cooldowns or write retries) | unchanged; the backoff applies only to reopening the read stream |
| A5 (identities preserved) | no unique ID, entity ID, device identifier or registry change |

## 4. Files and tests

| File | Change |
|------|--------|
| `api.py`, `coordinator.py`, `entity.py`, `diagnostics.py` | as in section 2 |
| `tests/test_api_hardening.py`, `test_coordinator_stream_backoff.py`, `test_coordinator_stream_first.py`, `test_coordinator_status_checks.py` | **new** |
| `tests/fake_gateway.py` and existing tests | adapted to the stream-first start (setup failures now come from the stream) |
| `README.md`, `docs/quality/protocol-contract.md` | documentation |

## 5. Commands and results

| Command | Result |
|---------|--------|
| `ruff check .`, `ruff format --check .` | passed |

| Run | Head | Result |
|-----|------|--------|
| [39493014243](https://github.com/fubu2k/ha-opus-greennet/actions/runs/39493014243) | `c07630b` (v1.0.0b1, unchanged base) | passed: Home Assistant latest stable, HACS validate |
| [39493018041](https://github.com/fubu2k/ha-opus-greennet/actions/runs/39493018041) | `8aa9ee0` | passed: Home Assistant latest stable, HACS validate |

## 6. Next step

PR base `feature/rest-only`. Then REST phase 02 (domain model, port of R6)
and the REST-only rework of the quality documents.
