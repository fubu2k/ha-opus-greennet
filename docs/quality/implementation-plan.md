# OPUS GreenNet Quality Series – Implementation Plan (REST only)

This document anchors the quality series for `fubu2k/ha-opus-greennet`. It is
updated in every phase. Per-phase details live in
`docs/quality/phases/rest-NN.md`; the gateway interface contract lives in
`docs/quality/protocol-contract.md`; the quality scale self-assessment lives in
`docs/quality/quality-scale-evidence.md`.

## Goal

A verifiable Platinum level of the Home Assistant 2026.10 Integration Quality
Scale for an integration that talks to the OPUS gateway **only through its
EnOcean over IP REST API** (HTTP requests plus one event stream). Functional
behaviour is preserved unless a phase explicitly permits a proven bug fix.
Changes follow Home Assistant best practice and are disclosed in the phase
report, including their effect on traffic.

## Repository target

- Target repository: `fubu2k/ha-opus-greennet` (remote `origin`).
- Integration branch of this series: `feature/rest-only`, based on the
  REST-only beta v1.0.0b1 (`c07630b`). `main` follows it after review.
- References to `kegelmeier/ha-opus-greennet` in `CLAUDE.md`, `README.md` and
  `manifest.json` are upstream references (codeowners, documentation, issue
  tracker). They are not rewritten. Nothing is changed in the upstream
  repository; contributions go there only as pull requests the maintainer
  opens.

## Binding guardrails

| ID | Rule | Frozen values (base `feature/rest-only`) |
|----|------|------------------------------------------|
| A1 | No additional radio traffic: no new poller, no shorter interval, no additional status query, no automatic command repetition. A change may only keep or reduce traffic. | `GATEWAY_HEALTH_INTERVAL = 60 s` (`GET /system/uptime`, no radio), `DEVICE_LIST_REFRESH_INTERVAL = 30 min` (`GET /devices`, no radio), `OUTBOUND_STATE_RECONCILIATION_DELAYS = (5, 20) s`, `COVER_RECONCILIATION_DELAYS = (10, 20) s` after the travel estimate, `COVER_MOVEMENT_GRACE_SECONDS = 5`, `UNKNOWN_DEVICE_RETRY_SECONDS = 3600` |
| A2 | Gateway uptime or stream health never confirms the state of a device. A single request timeout never marks the whole gateway unavailable. | availability = stream connected and health probe answering; a command timeout changes only that command's result |
| A3 | No arbitrary global cooldowns and no write retries after a timeout. | requests are not retried; the stream backoff applies only to reopening the read stream (`1 s → 60 s`, jitter 25 %, streams shorter than 10 s count as failures) |
| A5 | Existing user activations, identities and assignments are preserved: no unique ID, entity ID or device identifier change, no forced disabling of existing entities, no automatic change of a working entry. | unique IDs `<gateway EAG ID>_<device ID>[_<suffix>]`; entries created before 1.0 keep their unique ID and only ask once for the gateway address |

A phase that would need to break a guardrail stops and documents why.

## Working rules

- One branch per phase (`feat/rest-only-…`, `fix/rest-only-…`, `docs/…`),
  pull request against `feature/rest-only`; the maintainer merges.
- No version bump and no CHANGELOG entry in a phase pull request.
- Every phase: `ruff check .`, `ruff format --check .`, strict mypy, the full
  test suite with the coverage gates, and both CI references (fixed
  Home Assistant 2026.10.2 with pinned hassfest, and latest stable).
- Every phase writes `docs/quality/phases/rest-NN.md`: scope and base,
  changes, guardrails, files and tests, commands and CI runs, residual risks.
- Tests against the real gateway use the administrative MCP access only after
  consultation; the exact actuator ID is named beforehand, at most one
  actuator per asset type.

## Completed phases

| Phase | Content | Report |
|-------|---------|--------|
| REST 01 | REST client hardening (HTTP vs. gateway status, no retries, 8 MB document limit), event stream life cycle (bounded backoff with jitter, short-lived streams, generation guard), stream-first start-up with one `GET /devices` for the cover configuration, initial entity write as projection baseline, `homekitQR` redacted | `phases/rest-01.md` |
| REST 02 | Known devices updated in place (E6, finding B-4) with a field classification test; last vertical run duration and position origin in diagnostics only (decision O-4: 1a + 2a); regression tests R7 (rocker, `unknown`, end position) and R8 (6x press/release, long press) | `phases/rest-02.md` |
| REST 03 | Fixed CI reference: Home Assistant 2026.10.2, hassfest built from core `8d7fd8b6`, MDI icon check | `phases/rest-03.md` |
| REST 04 | Quality documents rewritten for the REST-only series | `phases/rest-04.md` |

CI on `feature/rest-only` after REST 01–03: run
[39494498982](https://github.com/fubu2k/ha-opus-greennet/actions/runs/39494498982),
all four jobs green.

## Planned phases

Unless marked completed above, all work below is planned, not implemented.

| Phase | Goal | Output | Guardrail note |
|-------|------|--------|----------------|
| REST 05 – measurement (former R1) | Measure on the maintainer's installation: end-position overrun of D2-05 actuators (O-3), presence of `lastVerticalMovementTime`, stream reconnect behaviour, the commit used in the field (O-6) | measurement report with diagnostics excerpts; no code change | read only; no added requests |
| REST 06 – grace period (O-4 revisit) | Decide with the measurement whether the 5 s cover grace period stays, becomes a longer constant or is derived from `lastVerticalMovementTime` | decision, then a small code phase if chosen | status checks may shift later, never more of them (A1) |
| REST 07 – stream parameters (O-2) | Assess the effect and support of the stream query parameters, including whether `levelOfDetail=high` carries the configuration block | hardware comparison, separate approval | must remove, never add, a request |
| REST 08 – discovery (quality scale `discovery`) | Assess a discovery path for REST gateways that Home Assistant supports for custom integrations | assessment; implementation only if it adds no radio traffic | A1, A5 |
| REST 09 – documentation rules (`docs-examples`, `docs-use-cases`) | Close the open documentation rules of the quality scale | README sections and evidence rows | – |
| REST 10 – final audit (former R8) | Re-check every quality scale rule against code, tests and documentation; record the result | updated evidence matrix and audit report | – |

## Open questions

| No. | Question | Resolution |
|-----|----------|------------|
| O-2 | Effect and support of the stream parameters | REST 07 |
| O-3 | Cause and size of the end-position delay on covers (actuator overrun) on the maintainer's installation | REST 05 |
| O-4 | Grace period for an overrun longer than 5 s | decided for now (1a + 2a, REST 02); revisit in REST 06 |
| O-6 | Which commit ran in the field test | REST 05 |

## Not verified on hardware

See `protocol-contract.md` section 12.
