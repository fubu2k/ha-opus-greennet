# Phase 15 – REST plan R0: documentation follow-up

- **Status:** implemented on branch `docs/opus-r0-rest-documentation`.
  Documentation only. Not merged; no pull request opened, no version change,
  no CHANGELOG entry, no release. Merge and release approval remain separate
  (`implementation-plan.md` sections 4 and 5).
- **Plan:** `Implementations-Plan-Rest-API.md` revision 2, phase R0 (section 8),
  decisions D1/D2 (section 0), deviations A-1 and A-6 (section 2.5), findings of
  sections 3.1–3.3. Phase report structure: `implementation-plan.md` section 20.
- **Numbering:** the REST plan names its phases R0–R8; reports keep the numeric
  `phase-NN.md` scheme, so R0 is reported as phase 15. `phase-01.md` stays the
  historical MQTT baseline and is not changed.

## 1. Scope, base and reference versions

| Field | Value |
|-------|-------|
| Repository | `fubu2k/ha-opus-greennet` |
| Branch | `docs/opus-r0-rest-documentation` (new) |
| Predecessor branch | `feature/rest-api-integration` (maintainer instruction) |
| Base SHA | `1847f88706b585569f7b9eefc28c664bdd22ab4c` ("docs: add implementation plan for the REST API transport") |
| Code state of the base | identical to `2010d2f` (`git diff --stat 2010d2f 1847f88`: only `Implementations-Plan-Rest-API.md` added) and in code to `e04245f`, the commit of the green CI run `39384621424` |
| `main` at phase start | `39b4c660168f4fcfdacc49b7f731c292f10a87a1` (does not contain the REST transport) |
| Home Assistant reference | `2026.10.2`, home-assistant/core `8d7fd8b68677c2585be36ec86043308f403875b8` (unchanged since phase 8) |

In scope (R0): record decision D2 as approved, record the maintainer's
practical REST baseline and the delayed end-position report with the
upstream explanation (actuator overrun), add the README limitation, link the
REST plan. Out of scope: any file under `custom_components/`, `tests/`,
`script/`, CI, `manifest.json`, `CHANGELOG.md`, `quality_scale.yaml`.

## 2. Authorised changes and guardrails A1–A5

Authorised: documentation of decision D2 (maintainer, 2026-10-20) and of the
observations of REST plan section 3. No behaviour, default or timing change.

| ID | Result |
|----|--------|
| A1 | No code change. D2 is recorded with its limit: fixed 30 min interval, no shorter interval, no further read path, no device query derived from the list. The documentation explicitly states that no extra status query is sent or may be added for the delayed end position. |
| A2 | Unchanged. The documentation keeps "uptime never confirms device state". |
| A3 | Unchanged. No cooldown or retry is described or proposed. |
| A4 | Unchanged; REST has no retained messages (contract 12.3). |
| A5 | No entity, ID, registry or transport change. |

## 3. Files and requirements

| File | Change | Requirement |
|------|--------|-------------|
| `docs/quality/implementation-plan.md` | Section 3 amendment marked approved (D2) with its A1 limit; section 15a heading "implemented, not merged", D2 note and link to the REST plan; section 7 status entry for phase 15; section 21 reference | R0 bullet 1, A-1, "link this document" |
| `docs/quality/phases/phase-14.md` | Section 8 wording made time-bound; new section 9.1 addendum (D2, practical baseline, end-position delay, follow-up plan); the original sections stay as recorded | R0 bullet 1, A-1, A-6 |
| `docs/quality/protocol-contract.md` | Section 12 intro: markers [Praxis] and [Upstream-Mitteilung]; 12.8: D2 approval and limit; 12.9 renamed "Practical baseline and limits of verification": practical baseline, end-position table, latency measurement limit, actuator overrun, effect on the travel estimate, unread `lastVerticalMovementTime`; existing "not verified" items kept | R0 bullet 1, A-6 |
| `README.md` | "Known limitations": new item on the delayed end position after full runs; REST verification item mentions the maintainer's practical check; no heading changed | R0 bullet 2, A-6 |
| `docs/quality/Implementations-Plan-Rest-API.md` | R0 status line | traceability |
| `docs/quality/phases/phase-15.md` | this report | `implementation-plan.md` section 20 |

Code statements used in the documentation were checked against the base:
`DEVICE_LIST_REFRESH_INTERVAL = timedelta(minutes=30)` (`rest_coordinator.py`),
`COVER_MOVEMENT_GRACE_SECONDS = 5.0` (`const.py`), `CoverMovement.ends_at` =
start + travel + grace (`coordinator.py`), `_finish_cover_movement` sets
`movement.target` with the comment "Assumed, not physical feedback" (both
coordinators), `_integer_reading` keeps the current value for a malformed
reading (`enocean_device.py`), `gateway_to_ha_ms` = `time()` minus the
gateway telegram timestamp (`rest_coordinator.py`), and no occurrence of
`lastVerticalMovementTime` in the integration code.

No regression reproducer: there is no behaviour change.

## 4. Gateway/cache versus potential radio counts

Unchanged before and after (no code change). For REST entries the approved
periodic traffic is: `GET /system/uptime` every 60 s and `GET /devices` every
30 min, both gateway reads without radio telegram (the latter **[HW-upstream]**).
Radio requests stay limited to user commands, at most two status checks per
unconfirmed channel and explicit ReCom service actions (contract 12.2 and 12.8).

## 5. Commands and results

| Command | Result |
|---------|--------|
| `git diff --stat` against the base | only `README.md` and files under `docs/quality/` |
| `git diff -U0` heading check on `README.md` | no README heading changed (`tests/test_quality_scale.py` asserts README headings and the Examples section) |
| Cross-reference search (`12.9`, "review pending", "Not verified in this repository") | all references consistent |
| `pytest`, `ruff`, `mypy` | **not executed locally**: the authoring environment could not reach the package index (PyPI DNS lookup failed), so Home Assistant could not be installed. No Python, test or configuration file changed. CI evidence: see section 9. |

## 6. Registry and identity compatibility

No runtime change; unique IDs, entity IDs, enablement, device assignments and
the transport of every entry are unaffected.

## 7. Protocol, README and evidence updates

See section 3. `quality-scale-evidence.md` and `capability-matrix.md` already
describe the 30 min read without a review reservation and needed no change.
`quality_scale.yaml` is unchanged (48 done, 6 exempt, 0 todo on the base).

## 8. Hardware observations, unknowns and residual risks

- The practical baseline is reported by the maintainer **[Praxis]** and was
  not independently verified; the installed commit was not recorded
  (`manifest.json` reports `0.7.0b2` on every commit, REST plan A-4).
- The actuator overrun explanation is an upstream communication with
  measurements of the upstream installation **[Upstream-Mitteilung]**; it is
  not yet confirmed on the own installation (REST plan R1, O-3).
- `gateway_to_ha_ms` contains any clock offset between gateway and Home
  Assistant; the statement "the delay arises before the gateway" holds only
  with synchronised clocks (REST plan B-7, H3).
- If the overrun exceeds the 5 s grace, the travel estimate shows the target
  as an assumed position for a short time before the real report. This is
  documented, not changed (REST plan A-5, R6, O-4).

## 9. Review status and next phase

- Review the branch `docs/opus-r0-rest-documentation`; it is stacked on
  `feature/rest-api-integration` and should be merged into that branch, not
  into `main`.
- CI: the workflow does not run automatically for branch pushes; a manual run
  (`workflow_dispatch`) on this branch is the evidence for this phase. The
  run number is recorded in the next phase report.
- Next REST phase: R1, confirmation measurement of the actuator overrun on the
  own installation (no fix, no polling, no timing change). It needs separate
  approval, including approval of any hardware observation.
- No merge or release permission is implied.
