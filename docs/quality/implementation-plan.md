# OPUS GreenNet Quality Series – Implementation Plan

Revision: 2026-10-19, revision 3. Proposed replacement; planned implementation
work has not been performed by creating this document.

Revision 2 made unlock-request and mechanics-fault window-handle diagnostics
mandatory. Revision 3 adds concrete icon proposals, semantic constraints,
version validation and acceptance tests. Those diagnostic defaults remain binding.

Target repository: `fubu2k/ha-opus-greennet`, default branch `main`.
Phase reports: `docs/quality/phases/phase-NN.md`.
Protocol contract: `docs/quality/protocol-contract.md`.

## 1. Goal and assessment boundary

Improve correctness, maintainability, testability, typing, documentation and
user experience against the Home Assistant 2026.10 Integration Quality Scale,
without increasing battery-draining radio-device polling.

Target a verifiable Platinum-equivalent self-assessment: all applicable rules
satisfied and all exemptions justified. This custom integration is not an
officially Platinum-rated core integration. Do not add a manifest tier as a
substitute for official review. Official recognition requires the applicable
Home Assistant core contribution and review process.

Preserve functional behaviour unless a phase explicitly permits a narrow,
tested correction. Planned work is not evidence of completion. Distinguish
static analysis, actually executed tests, CI outcomes and hardware observations.

### Reference baseline

- Reviewed main HEAD: `63c7380db5ef5ce477dae5c83a1487e9e23a2d6e`
  (2026-10-14; merge of quality-scale evidence work).
- Original plan blob: `941041dfdd8906b74eeae2b4d8405ede16f0dc5d`.
- Starting evidence: 54 rules, 40 done, 9 exempt, 5 todo. This is the recorded
  repository self-assessment, not a fresh certification.
- Existing evidence references Home Assistant core commit
  `7ff0e7fbdea4e079ae3f99e048497f5e8c7e3360` for rules/schema.
- This document claims no freshly executed tests or current hardware validation.
- At every phase start, record actual HEAD and review changes since this base.
- Historical references to `kegelmeier/ha-opus-greennet` do not change the target.
  Review metadata/ownership/documentation URLs separately, not incidentally.

## 2. Binding guardrails A1–A5

These supersede the original blanket exclusions. Permission for a scoped phase
is not permission for unrelated side work.

| ID | Binding rule | Permitted work | Prohibited shortcuts |
|----|--------------|----------------|----------------------|
| A1 | No increase in automatic radio-device polling, reconciliation slots or retry traffic. Keep intervals unless a reviewed change reduces unnecessary work. | Suppress redundant queries, classify endpoint/radio effects, prove unchanged or reduced traffic. | New periodic device pollers, shorter intervals, extra reconciliation slots, automatic command retries. |
| A2 | Preserve gateway-health semantics by default. Gateway uptime never confirms device state; one command timeout never makes the whole gateway unavailable. | Profile-specific handling of explicit existing failure/recovery reports after separate review. | Silence-based offline detection without a documented heartbeat contract; gateway health as proof of actuator execution. |
| A3 | Keep existing serialization and timeout defaults. No blanket post-timeout delay. | Reproduce late-response ambiguity and evaluate narrow, evidence-based mitigation separately. | Claiming local generations solve missing wire request IDs; arbitrary global cooldowns; retrying writes. |
| A4 | Preserve existing retained-message protection. Add freshness protection only for demonstrated ordering failures. | Field/channel-level live-versus-snapshot tests and minimal corrections using reliable provenance. | Rejecting all snapshots, invented timestamps/sequences, extra refreshes to compensate for stale data. |
| A5 | Preserve identities, existing user enablement and assignments. Permit scoped metadata/default-exposure review. | Classes, categories, icon translations, new-entry defaults and mandatory handle diagnostics below. | Forced disabling of existing entries, entity deletion, opt-in polling or incidental ID/inventory changes. |

### Timing and health baseline

- `GATEWAY_HEALTH_INTERVAL = 60 s`.
- `RECONNECT_RESYNC_DEBOUNCE_SECONDS = 60`.
- `OUTBOUND_STATE_RECONCILIATION_DELAYS = (5, 20)`.
- `COVER_RECONCILIATION_DELAYS = (10.0, 20.0)` after movement end.
- `COVER_MOVEMENT_GRACE_SECONDS = 5.0`.
- `REQUEST_TIMEOUT = 10 s`; `SUBSCRIPTION_TIMEOUT = 30 s`.
- `SETUP_REQUEST_TIMEOUT = 30 s`; `SETUP_SUBSCRIPTION_TIMEOUT = 180 s`.
- `MIN_SEND_INTERVAL = 0.0`.

Gateway availability:
`not _unloaded and _gateway_available and _bridge_connected is not False and mqtt.is_connected()`.
Authorised child-level availability must remain separate from gateway health.

## 3. Battery and communication contract

Separate gateway/cache requests from operations that cause EnOcean radio
communication. In the user's installation context, bridges are mains-powered;
window handles, motion detectors and window sensors may use batteries. Verify
the actual model rather than generalising. A mains-powered bridge can forward
a request to a battery-powered device.

| Operation | Scope to verify | Policy |
|-----------|-----------------|--------|
| `get/config/system/uptime` | Gateway endpoint | Existing interval only; no derived child refresh. |
| `get/devices` | Gateway inventory/cache according to reference | Setup/resync only; keep debounce and verify relevant firmware effects. |
| `put/devices/<DEV>/state` with `query: status` | Profile-dependent device/HeatArea operation, potentially radio | No new periodic use; document support and radio effect per profile. |
| Configuration/parameters | May involve ReCom/device access | Explicit user action only; no automatic background reads/writes. |
| Incoming streams/telegrams | Existing publications | Local parsing must not publish requests. |
| Diagnostics/icons/enablement | HA representation | No device polling or configuration reads/writes. |

Create a profile/operation capability matrix: EEP/profile, operation, reference,
cache-versus-radio behaviour, known power source, support and verification
status (`reference`, `code`, `mock test`, `hardware`). Unknown radio effects
justify withholding new automatic requests, not assuming safety. Do not infer
power source from EEP without evidence.

MQTT mocks cannot prove unchanged battery lifetime. Count emitted requests and
validate relevant radio effects using separately approved hardware observation
before making hardware claims. Hiding push-based entities is not evidence of
battery savings. Icon changes must remain frontend/metadata work only.

## 4. Repository and execution rules

- No direct main commits, version changes, releases, merges, force-pushes or
  automatic PRs. No CHANGELOG update without explicit approval.
- Historical exception: phases 1–5 were fast-forwarded to `367dd9a`; Unreleased
  entry added in `f400866`. This authorises no later writes.
- No real-device commands/configuration writes in ordinary development.
  Hardware verification requires separate explicit approval.
- Preserve unique IDs, entity IDs, topics, public actions and device assignments.
- Preserve wire values, channel zero, `notAvailable`, cover inversion, STOP and
  proprietary HeatArea profiles unless specifically authorised otherwise.
- No write retries after timeout, blanket `DataUpdateCoordinator` migration
  or new dependencies without concrete need.
- Behaviour changes need scope, requirement/reproducer, affected profiles,
  compatibility review, traffic comparison, tests and documentation.
- A file existing or a test checking implementation shape is not sufficient
  proof that a user-facing quality rule is fulfilled.

## 5. Branch and phase workflow

Keep historical branches as historical records. Start new work from verified
current main or an explicitly agreed predecessor when stacking. Record bases.

1. Inspect actual baseline and applicable rules.
2. Agree on one phase and permitted behaviour changes.
3. Create/use branches only with repository-write authorisation.
4. Add tests and the smallest coherent implementation.
5. Run gates and record results, failures and limits.
6. Update plan, phase report, protocol contract and evidence matrix.
7. Present diff and stop for review; merge/release approval is separate.

Conflicts with battery/identity protections require an explicit amendment.
Phase prompts cannot silently override guardrails. Report a phase HEAD after
commit, not within its own commit; record it in the next baseline/report.
Suggested branch names below do not imply existing branches.

## 6. Historical phases

| Phase | Branch | Recorded base SHA | Status |
|-------|--------|-------------------|--------|
| 1 | `feat/opus-q01-baseline` | `3e52fce31e7a2d503eef4820f4c706ebcfc751ab` | Completed |
| 2 | `fix/opus-q02-state-ownership` | `ff164f9db7adde27dfa8c37ddf6c8cf350b633a0` | Completed |
| 3 | `fix/opus-q03-event-lifecycle` | `16d9a45777cef8891fe8772963c8827b8a479798` | Completed |
| 4 | `fix/opus-q04-background-efficiency` | `8ece6eaf7be8ce7ccdbf5c9cf54cb93449a10ea7` | Completed |
| 5 | `feat/opus-q05-strict-typing` | `bf2ad417559cdaee3b695274a37455476a356e6c` | Completed |
| 6 | `feat/opus-q06-entity-metadata` | `feabed53b5761104e9ed917c0dc44b2afb584df7` | Completed: mandatory handle diagnostics (PR #2, see `phases/phase-06.md`) |
| 6 (metadata) | `fix/opus-q06-metadata` | `8001d8b2cfe657dddfee8eaaa5eae68bd4b15149` | Implemented, awaiting CI and review: SMS supply voltage diagnostic, whole-percent battery display (see `phases/phase-06.md`) |
| 7 | `feat/opus-quality-scale-evidence` | `f4008667c0d42b202554fb0849ee3226b81b011a` | Completed |

Historical phase reports remain evidence of what actually ran. Do not imply
later behaviour changes were already delivered.

- Phase 1: baseline, protocol contract and findings.
- Phase 2: optimistic post-ACK ownership moved to domain snapshot/comparison/
  estimate helpers and `ChannelStateSnapshot`; no ACK/timing/wire changes.
  O1 (ACK 201 estimate semantics) remained open.
- Phase 3: transient button occurrences cleared after telegram dispatch;
  fixed F1 (previous events replayed on metadata/dbm/ACK dispatch).
- Phase 4: superseded queued reconciliation removed, idle locks released,
  unchanged entity projections not rewritten; timer churn measured, unchanged.
- Phase 5: strict typing in 18 modules, typed entries, `wire.py` validation and
  strict mypy CI. Historical report: 74 initial errors across 15 files, no
  casts/type ignores in delivered work, justified HA-signature `Any` uses.
- Phase 7: 54-rule self-assessment/evidence, coverage gates, repeated races,
  removal/troubleshooting/limitation docs; historical 92.76% to >97% coverage.

## 7. Phase order and status

Status update 2026-10-19: phase 8 completed (PR #5, merge `a4d593a`, report
`phases/phase-08.md`). Phase 6 delivered only the mandatory handle diagnostics
of section 10 (PR #2); the remaining metadata review and section 11 remain open.
Phase 8 found that `mdi:cog-alert` (section 11) is not in the HA 2026.10.2 icon
catalogue; the icon phase must choose a validated replacement.

Status update 2026-10-19 (phase 9): implemented on branch
`feat/opus-q09-quality-gaps`, report `phases/phase-09.md`; CI green (run
`39288963525`), awaiting review, not merged. Section 11 icons were implemented for the four handle
entities and the service actions; `mdi:alert-circle` replaces `mdi:cog-alert`.
The remaining metadata review of resumed phase 6 (section 10) stays open.

Status update 2026-10-19 (phase 10): phase 9 merged (PR #7, merge `e4bb5e9`).
Phase 10 was started before the resumed phase 6 metadata review at the
maintainer's request; that review stays open. Phase 10 is implemented on branch
`fix/opus-q10-field-freshness`, report `phases/phase-10.md`: session-scoped
field provenance for retained replays (protocol contract section 6.1) and live-only
receipt times for the rediscovery rule. Not merged; CI on the branch is pending.

Status update 2026-10-19 (phase 11): phase 10 is on `main` (head `1b36a94`,
changelog 0.7.0b2). Phase 11 is implemented on branch
`fix/opus-q11-ack-device-health`, report `phases/phase-11.md`: ACK 201 is
reported as deferred delivery (finding O1 resolved), applies no estimate,
starts no cover travel estimate and schedules no status query; deferred values
are listed in diagnostics; per-device health stays separate from gateway
health (protocol contract section 7.1). Completed: CI green (runs
`39306729854` and `39306899304`), merged via PR #9 (`e7c5248`), CHANGELOG
0.7.0b2 (`07f75e9`). The remaining metadata review of resumed phase 6 stays open.

Status update 2026-10-19 (resumed phase 6, metadata review): implemented on
branch `fix/opus-q06-metadata` from `main` `8001d8b`, report
`phases/phase-06.md` section "Metadata review". Maintainer decisions: the SMS
(A5-07-01/03) `supply_voltage` sensor becomes `EntityCategory.DIAGNOSTIC` and
stays enabled; every `battery_level` sensor suggests display precision 0
(whole percent, display only). No new entity, request or wire change. Tests not
run locally (no package index in the authoring environment); CI pending, not
merged. The full per-entity inventory of section 10 stays open.

Status update 2026-10-19 (phase 12): base `main` `c1ac376` (includes the
resumed phase 6 metadata fix, PR #10, merge `6ce8f38`). Phase 12 is an
investigation on branch `test/opus-q12-late-responses`, report
`phases/phase-12.md`: deterministic tests (`tests/test_late_responses.py`)
reproduce and pin late-answer attribution without a wire request ID
(protocol contract section 7.2). No production code, timeout, queue or traffic
change. No mitigation is implemented; candidates and the hardware evidence
they need are listed in the report for a separate maintainer decision. Tests
ran locally against a stub of Home Assistant; CI green (run `39313178932`).
Option M6 (diagnostic-only late-answer counters, no traffic or behaviour
change) was approved by the maintainer and implemented on branch
`feat/opus-q12-late-answer-diagnostics` (report section 13; CI run
`39314655845`). Completed: merged via PR #11 (`1be841e`) and PR #12
(`e57cbd2`), CI green on `main` (run `39315310796`), CHANGELOG "Unreleased".

Unless marked completed above, all work below is planned, not implemented.
Default order:
phase 8 -> phase 9 -> resumed phase 6 -> phases 10, 11, 12 -> phase 13.
A separately approved metadata phase can run earlier. Investigations may find
that a proposed correction is unnecessary; record evidence rather than force it.

## 8. Phase 8 – Reproducible baseline and traffic evidence (completed)

Suggested branch: `feat/opus-q08-reproducible-baseline`.

- Select a real supported HA `2026.10.x` patch after checking compatibility.
- Add a fixed reference job alongside a separate latest-stable job.
- Record Python, dependency and immutable rule/validator revisions. Keep moving
  hassfest/master only as an additional compatibility signal if desired.
- Rerun tests; record publish counts by operation, endpoint and trigger.
  Do not equate MQTT counts with radio transmissions.
- Create capability matrix; audit reconciliation callers, telegram paths and
  ACK 201; identify unknown firmware/radio effects.
- Recheck all 54 rules/exemptions and supported icon catalogue against target.

Acceptance: reproducible environment/results/traffic baseline, no runtime change.

## 9. Planned phase 9 – Applicable quality-scale gaps

Suggested branch: `feat/opus-q09-quality-gaps`.

### Gateway discovery (`discovery`)

Implement MQTT discovery from actual OPUS publications with appropriate manifest
filters and `async_step_mqtt`. Validate/normalise EAG IDs, share unique-ID checks
with manual setup, test duplicates, multiple gateways, ignored/invalid messages
and setup races. First/retained messages identify candidates, not live health.
Keep existing fresh setup validation without child polling. Document that MQTT
bridging must already expose gateway topics. Do not promise unbridged discovery.

### Examples and use cases (`docs-examples`, `docs-use-cases`)

Add validated complete examples for rocker control, handle state and a suitable
alarm. Use actual contracts and installation-specific entity placeholders.
Explain estimates, limits, battery-friendly behaviour and optional handle
diagnostics. These examples must not add polling or require runtime changes.

### Icon translations (`icon-translations`)

Implement the audit and concrete proposals in section 11. Prefer appropriate
domain/device-class defaults. Add `icons.json` for useful custom/state-dependent
icons only; no empty compliance file. A valid exemption is acceptable where
all entities actually use appropriate standard icons. New custom icons require
actual translation-key mapping, state tests and target-version validation.

### Repairs (`repair-issues`)

Evaluate duplicate-beta-entity migration as an actionable case. If applicable,
provide a stable translated issue and creation/resolution/persistence tests.
Do not raise repairs for ordinary transient outages or silent sensors. If no
user-remediable case exists, justify exemption rather than fabricate a repair.

Acceptance: original five open rules resolved or legitimately exempt with real
code/test/doc evidence, no duplicate entries and no added child queries.

## 10. Resumed phase 6 – Metadata and safe default exposure

Suggested branch: `feat/opus-q06-entity-metadata`.

### Mandatory window-handle diagnostics

Windows Handle, 2026-10-19: unlock requests (Entsperranfrage) and mechanics faults
(Mechanik-Fehler) normally serve troubleshooting rather than everyday operation
of a functioning handle. Both existing entities must be diagnostics and disabled
by default for newly created registry entries.

| Existing entity | Meaning | Category | New-entry default |
|-----------------|---------|----------|-------------------|
| `unlock_request` sensor | Entsperranfrage | `EntityCategory.DIAGNOSTIC` | `entity_registry_enabled_default = False` |
| `mechanics_fault` binary sensor | Mechanik-Fehler | `EntityCategory.DIAGNOSTIC` | `entity_registry_enabled_default = False` |

Verify exact existing keys/classes/profile conditions before editing code.
Preserve identifiers and wire mappings including `unlockRequest`/`unlock` and
`mechanicsFault`/`mechanics` where applicable. Do not create unsupported entities.

- Implement category AND default enablement; one does not substitute for the other.
- Keep entities in the registry, optionally activatable for troubleshooting.
- Never forcibly disable existing enabled entries or enable disabled entries.
- Preserve names, IDs, assignments and user customisations. Apply category via
  the entity definition without resetting user-owned registry settings.
- Do not change normal handle position/open/closed/tilted entities or lock control.
- Do not modify shared incoming parsing or introduce polling/configuration
  requests when enabling diagnostics. Icons also must not initiate requests.
- Document defaults, activation instructions and the absence of proven battery
  savings from hiding push-based diagnostics.
- Update README, protocol inventory and evidence. “All non-RSSI entities enabled”
  describes the historical baseline, not the new required defaults.

Required tests per applicable handle/profile: diagnostic categories, new-entry
disabled defaults, preservation of existing enabled AND disabled entries,
customisations and IDs, no extra publish on activation, unchanged normal handle
states and supported-profile conditions.

### Remaining metadata review

Inventory every entity's purpose, class/state class, category, translation key,
icon, default and update frequency. Review RSSI, voltage and overlapping handle
projections as candidates, not automatic disablement decisions. Preserve useful
exposure of unrelated safety alarms/failures; do not generalise the specific
mechanics-fault decision. No inventory deletion or selection options in this
phase. Recheck category/class/default/translation/icon rules across all entities.

Acceptance: both required diagnostics correctly classified and disabled for NEW
entries, existing user settings preserved, no extra radio communication.

### Metadata decisions (added 2026-10-19, branch `fix/opus-q06-metadata`)

| Entity | Decision | New-entry default | Rationale |
|--------|----------|-------------------|-----------|
| SMS (A5-07-01/03) `supply_voltage` sensor | `EntityCategory.DIAGNOSTIC` | enabled (unchanged) | Device telemetry, not everyday operation; voltage class, unit and unique ID unchanged. |
| Every `battery_level` sensor (SMS, A5-04-01/02/03, OPUS motion detector) | `suggested_display_precision = 0` | enabled (unchanged), no category | Whole-percent display. State, history and statistics are not rounded; a user `display_precision` wins. |

- The category is integration-owned registry metadata: existing entries take
  it on the next setup but keep `disabled_by`, entity ID, name, icon and area
  (A5). The suggested precision is stored as a sensor registry option and is
  overridden by a user-set display precision.
- Supply voltage is not disabled by default: this review is not an automatic
  disablement decision. Battery levels stay ordinary (non-diagnostic) readings.
- No parsing, coordinator, transport, timing, topic, translation or icon
  change; no new publish (A1). Tests: `tests/test_sensor_metadata.py`.
- Still open: the complete per-entity inventory (purpose, class, state class,
  category, translation key, icon, default, update frequency), RSSI and the
  overlapping handle projections.

## 11. Concrete icon policy and implementation guide (added in revision 3)

### Scope and priority

These are proposed MDI identifiers, not a claim that every identifier has been
validated in the pinned HA release. Validate against the actual HA 2026.10
frontend/icon set and the applicable validators before implementation. Replace
unsupported candidates with an approved available equivalent and document it.

Apply priority in this order:

1. Correct entity domain and device class for actual function.
2. Suitable standard domain/device-class/state icon.
3. A custom state-dependent icon in `icons.json` only when useful and semantically
   correct. Link it using the entity's actual `translation_key` and raw states.

Do not override good defaults just to use every candidate below. Do not implement
Python dynamic icon properties where icon translations suffice. Never use an
icon as a substitute for correct state/device-class semantics or translated text.

Entity icons are not integration brand images or physical-product photographs.
This phase does not promise a separately configurable device-registry icon for
each hardware product. A gateway symbol can be used on an existing appropriate
entity/dashboard representation; do not create a new entity just to show it.
Do not add dashboard customisations or overwrite user-selected icons incidentally.

### Hardware and actuator candidates

| Hardware/function | Candidate icon(s) | Decision rule |
|-------------------|-------------------|---------------|
| Bridge/gateway representation | `mdi:router-wireless` | Existing suitable representation only; not a health assertion. |
| Generic switching actuator | `mdi:toggle-switch` | Prefer switch defaults; not every relay is a socket. |
| Actual switched socket | `mdi:power-socket-de` | Only if socket function/type is known; do not assume every switch is a German socket. |
| Light/dimmer | `mdi:lightbulb` | Prefer light defaults and existing brightness/state handling. |
| Roller shutter | `mdi:window-shutter` | Prefer correct cover device class and its state-dependent defaults. |
| Venetian blind | `mdi:blinds-horizontal` | Only when actual blind function is known; do not confuse with shutters. |
| Heating/HeatArea | `mdi:radiator` | Heating function, not proof of active heat or valve opening. |
| Explicit valve function | `mdi:pipe-valve` | Only for an existing actual valve representation; no invented valve entity. |
| Wall button/rocker | `mdi:gesture-tap-button` | Event/button representation; retain actual event semantics. |
| Motion detector | `mdi:motion-sensor` | Prefer motion binary-sensor device-class defaults. |
| Window contact | `mdi:window-closed`, `mdi:window-open` | Prefer window device-class state defaults. |
| Smoke detector/alarm | `mdi:smoke-detector` | Prefer smoke device-class semantics and alarm indication. |
| Water/leak sensor | `mdi:water-alert` | Prefer the appropriate moisture binary-sensor class/default. |

These choices do not change inventory, supported profiles or device assignments.

### Window-handle states and diagnostics

Implement custom mappings where the existing enum sensor is not sufficiently
represented by device-class defaults. Keep the normal state distinct from
request/diagnostic information.

| Existing entity/function | Raw state to verify against code | Candidate icon |
|--------------------------|-----------------------------------|----------------|
| Handle-position sensor | `closed` | `mdi:window-closed` |
| Handle-position sensor | `open` | `mdi:window-open` |
| Handle-position sensor | `tilted` | `mdi:window-open-variant` |
| Existing lock entity | `locked` | `mdi:lock` (prefer standard) |
| Existing lock entity | `unlocked` | `mdi:lock-open` (prefer standard) |
| `unlock_request` diagnostic sensor | `not_requested` | `mdi:lock-outline` |
| `unlock_request` diagnostic sensor | `requested` | `mdi:lock-clock` |
| `mechanics_fault` diagnostic binary sensor | `off` | `mdi:cog-outline` |
| `mechanics_fault` diagnostic binary sensor | `on` | `mdi:cog-alert` |

Semantic safeguards:

- These raw states are targets to verify, not permission to rename runtime values.
  Do not use German UI labels as JSON state keys.
- Unlock requested is NOT unlocked. Do not display an open-lock icon as proof of
  a completed unlock merely because a request is present.
- `mdi:lock-clock` is a visual cue for a request; it must not imply a verified
  pending command/timer if the entity only reports a request. Keep the translated
  label and description explicit; choose a less ambiguous supported icon if needed.
- `mdi:window-open-variant` is an approximation for tilted, not a uniquely
  guaranteed tilted-window pictogram. Retain the explicit translated state.
- A missing/unknown mechanics value is NOT “no fault”. Define a neutral default
  such as a validated `mdi:cog-outline` without converting unknown to `off` or
  presenting an explicit healthy confirmation. Preserve unavailable semantics.
- Define sensible neutral defaults for unknown/unavailable handle/request states;
  never imply closed, locked or successful execution from missing data.
- Do not change an entity's correct device class to force an icon. If a standard
  problem-class icon is more appropriate, retain it and document the decision.
- Category and disabled-by-default requirements from section 10 remain mandatory
  regardless of icon choices. Both diagnostics remain user-activatable.

### Measurements and other diagnostics

| Existing value | Icon orientation/candidate | Implementation policy |
|----------------|----------------------------|-----------------------|
| Temperature | `mdi:thermometer` | Prefer temperature class/default. |
| Humidity | `mdi:water-percent` | Prefer humidity class/default. |
| Illuminance | `mdi:brightness-5` | Prefer illuminance class/default. |
| Voltage | `mdi:flash` | Prefer voltage class/default; label actual quantity. |
| Battery level | `mdi:battery` | Preserve battery class and standard level/state rendering. |
| RSSI/signal | `mdi:signal` | Prefer standard signal-strength representation. |
| Power | `mdi:flash` | Prefer power class/default; do not confuse with energy. |
| Existing communication diagnostic | `mdi:lan-disconnect` | Only for a clearly defined communication failure; not for normal sensor silence. |

No new entities or automatic requests are authorised by this table.

### Icon implementation steps and acceptance tests

1. Inventory actual platform, `translation_key`, raw states, device class and
   existing user-visible icon behaviour for every affected entity.
2. Validate candidate MDI identifiers and exact `icons.json` schema against the
   pinned reference. Record validation commands/results, not assumptions.
3. Add minimal entries for custom icons keyed by actual translation keys.
   Retain suitable standards for everything else; avoid duplicate mappings.
4. Test all mapped values, neutral defaults, unknown/unavailable, supported
   profiles and normal-versus-diagnostic separation. Use schema/static tests and
   appropriate frontend/UI checks; do not claim backend tests prove rendering.
5. Verify no extra publish, subscription-triggered query, config read or write
   on state changes, icon rendering or diagnostic enablement.
6. Verify registry identities and user icon/enablement overrides remain intact.
7. Update README/examples and quality evidence, recording custom versus standard
   icons and reasons. Complete the entity metadata audit with section 10.

Acceptance: clear state semantics, validated target-version icons, no misleading
request/lock/fault indication, no new entity inventory, no additional radio load.

## 12. Planned phase 10 – Field-level freshness and replay

Suggested branch: `fix/opus-q10-field-freshness`.

The earlier review identified a risk, not a reproduced hardware defect. Lift A4
only for ordering failures demonstrated by regression tests.

Test live followed by old retained/snapshot fields across `stream/device`,
flattened `stream/devices`, rediscovery and channels. Include partial snapshots,
unknown fields, profile discovery, reconnect, removal/re-add and gateway reboot.
Preserve first snapshot initialisation and unrelated unknown fields. Define
field/channel provenance; use wire times/sequences only after their reliability
is established. Local arrival order cannot establish all source ages.

Retained telegrams/RPC replies remain non-events/non-confirmations. Bound and
clean freshness metadata on removal/unload/generation changes. Update protocol
section 6. Acceptance: failing-before/passing-after reproducer, minimal correction,
no new polls/publishes and no lost legitimate initial data.

## 13. Planned phase 11 – ACK 201 and explicit device health

Suggested branch: `fix/opus-q11-ack-device-health`.

Address O1 and narrow A2 exceptions without global health rewriting.

- Verify vendor semantics: ACK 200 is not physical-execution proof; 201 may
  mean deferred delivery (SoftSmartAck).
- Distinguish desired/estimated, accepted/deferred and device-confirmed states.
  Assess public compatibility before changing estimates.
- Decide 201 reconciliation behaviour; suppress/reduce unsuitable checks,
  never add polling to force confirmation.
- Handle explicit profile-specific failures/recovery separately from gateway
  health. Subsystem warnings do not automatically offline whole HeatAreas.
- No silent-device offline rule without a verified heartbeat contract.
- Hidden handle diagnostics still have incoming data semantics. Category/icon
  policy does not justify automatically marking a handle unavailable.
- Test ACK variants, rejection/timeout, late/contradictory feedback, partial
  failure/recovery and a healthy gateway with an affected child.

Acceptance: clear estimates/confirmation, compatibility docs, no write retries
or higher radio-query budget; hardware claims require approved observations.

## 14. Planned phase 12 – Late-response ambiguity investigation

Suggested branch initially: `test/opus-q12-late-responses`.

Retain existing topic locks/global queue. Reproduce A timing out, B starting on
the same answer topic and A's late success/negative reply arriving during B,
including cancellation/reconnect. Establish actual correlation information:
local generations cannot label an old wire response without a correlation ID.

Consider narrow quarantine/drain only with a defensible lateness bound and
latency trade-off. Arbitrary delays are not proofs. If reliable mitigation is
unavailable, document the limitation rather than add blanket cooldowns. Separate
investigation from implementation approval.

Acceptance: deterministic tests, stated residual risks, no extra requests/retries.
Changing production timeout behaviour requires a separate design decision.

## 15. Planned phase 13 – Final rule audit and report

Suggested branch: `docs/opus-q13-quality-acceptance`.

Re-audit all 54 rules with meaningful implementation/test/doc evidence or exact
exemption reasons. Verify handle diagnostic defaults and actual icon semantics,
not merely this plan. Synchronise YAML, evidence, README, protocol and reports.
Run fixed 2026.10/latest-stable jobs and repeated races; record hardware gaps.

Claim Platinum-equivalent self-assessment only with complete applicable evidence,
never official custom certification. Optional robustness findings are not
necessarily tier blockers; assess actual rule/correctness impact. Acceptance:
no unreasoned applicable todo, no false hardware guarantee, reviewed compatibility
and traffic evidence; release authorisation remains separate.

## 16. Mandatory acceptance matrix

| Scenario | Required result |
|----------|-----------------|
| Long idle period | No periodic child status queries; baseline gateway health only. |
| Gateway health tick | No derived child/configuration operation. |
| Discovery/setup race | One entry, existing fresh check, no child polling. |
| Diagnostic activation/read | No new device request/configuration write. |
| Icon update/render | No new publish or device read; actual state unchanged. |
| Feedback before queued reconciliation | Superseded not-yet-published query dropped. |
| New command supersedes old work | No polling/confirmation for wrong command. |
| Timeout/negative ACK | Error surfaced, no write retry or global gateway outage. |
| ACK 201 | Explicit deferred semantics; no extra confirmation polling. |
| Reconnect burst | Debounce retained; no unbounded resync/query burst. |
| Old snapshot after live field | No stale overwrite within documented provenance limits. |
| Silent sensor/handle | No invented heartbeat failure/wake-up polling. |
| New supported handle | Unlock-request/mechanics-fault diagnostic and disabled; normal entities unchanged. |
| Existing handle entries | Enablement, IDs and customisations preserved. |
| Request versus lock | Requested-unlock icon does not assert unlocked. |
| Unknown mechanics value | Not converted to healthy/off; neutral fallback. |
| Unsupported icon candidate | Caught by reference validation; supported equivalent chosen. |
| Unload/removal/bridge down | No orphan work/cross-device cancellation. |

Compare equivalent command/channel/reconnect scenarios. Investigate increased
counts; never redefine baseline to conceal them. MQTT tests cannot rule out
hidden firmware radio effects.

## 17. Quality gates and evidence

Select an actual patch before replacing the placeholder:

```bash
python -m pip install --upgrade -r requirements_test.txt "homeassistant==<selected-2026.10-patch>"
python -m pip check
ruff check .
ruff format --check .
python -m mypy
pytest -v --cov --cov-report=term-missing --cov-report=json:coverage.json
python script/check_coverage.py coverage.json
```

Baseline gates: total `fail_under = 97`, >95% per integration module, 100%
config-flow coverage, strict mypy, hassfest CI. Never lower them to land work.
`tests/test_quality_scale.py` supplements custom metadata checks. Passing
hassfest alone is not a complete quality-scale review or frontend visual test.
Add applicable icon/schema validation for the fixed reference.

Repeat lifecycle/race suites three times:

```bash
for run in 1 2 3; do
  pytest -q -p no:cacheprovider \
    tests/test_release_transport_safety.py \
    tests/test_ha_lifecycle.py \
    tests/test_event_lifecycle.py \
    tests/test_background_efficiency.py \
    tests/test_device_removal.py \
    tests/test_binary_sensor_discovery.py \
    tests/test_transport_edge_cases.py || exit 1
done
```

Add freshness/ACK/late-response suites when implemented. Record commands,
environment, exit status, coverage, traffic and local/CI origin. Keep individual
evidence even if a different combined job fails.

## 18. Existing protections – retain tests, do not reimplement as absent

- Retained device RPC answers: no discovery/state; retained telegrams: no events/state.
- Snapshot button keys are not occurrences; metadata/dbm/availability dispatches
  do not replay old rocker events; rediscovery clears transient fields.
- Command timeout does not change gateway availability or retry writes.
- Priorities command > query > background; exactly one request in flight.
- Dedicated STOP, correct channel zero and profile-specific wire semantics.
- Per-channel/field feedback matching; superseded reconciliation removal.
- Idempotent binary-sensor discovery/declarative descriptions.
- Child removal without MQTT writes and isolated cancellation.
- Unchanged visible entity projections avoid redundant HA writes.

## 19. Optional structural refactoring

Not a Platinum prerequisite. Do not mix refactoring with behavioural fixes.

Extract pure parsing with golden tests for channel zero, STOP, inversion,
HeatArea and `notAvailable`. Separate movement/reconciliation ownership while
preserving timing, query budgets and cleanup. Consolidate discovery only where
useful, preserving IDs/inventory/reviewed defaults. Move HeatArea “on” selection
to domain rules only with unchanged wire values. Improve docstrings and remove
proven dead code with tests. Preserve icon/diagnostic policy in sections 10–11.

## 20. Required phase report

1. Scope, actual base SHA and exact reference versions.
2. Authorised behaviour/default changes and A1–A5 implications.
3. Files, requirements and regression reproducers.
4. Gateway/cache versus potential radio counts before/after.
5. Commands, results, coverage, icon/schema validation and any UI checks.
6. Registry/identity compatibility including existing enabled diagnostics/icons.
7. Protocol/README/evidence updates.
8. Hardware observations, unknowns and residual risks.
9. Review status and next phase; no implied merge/release permission.

## 21. Reference sources

- Baseline: https://github.com/fubu2k/ha-opus-greennet/tree/63c7380db5ef5ce477dae5c83a1487e9e23a2d6e
- Evidence: `docs/quality/quality-scale-evidence.md`.
- Rules: `custom_components/opus_greennet/quality_scale.yaml`.
- Protocol: `docs/quality/protocol-contract.md`.
- Vendor reference: `opus_mqtt_services_reference.md`.
- CI: `.github/workflows/tests.yml`.
- Official scale: https://developers.home-assistant.io/docs/core/integration-quality-scale/
- Rules: https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/
- Polling: https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/appropriate-polling/
- Availability: https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/entity-unavailable/
- Defaults: https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/entity-disabled-by-default/
- Icon rule: https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/icon-translations/
- Icons overview: https://www.home-assistant.io/docs/frontend/icons/
- Icon translation introduction: https://developers.home-assistant.io/blog/2024/01/19/icon-translations/
- Binary-sensor classes: https://www.home-assistant.io/integrations/binary_sensor/
- Repairs: https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/repair-issues/
- Discovery: https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/discovery/

Online documentation can change. Record specific rule text/revisions during
phase 8; do not use an unverified current webpage as a fixed-version guarantee.
