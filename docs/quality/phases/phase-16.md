# Phase 16 – REST plan R2: REST-only decoupling

- **Status:** implemented on branch `feat/opus-r2-rest-only-decoupling`. Not
  merged; no pull request opened, no version change, no CHANGELOG entry, no
  release. Merge approval remains separate (`implementation-plan.md` sections 4
  and 5); the merge target is `feature/rest-api-integration`, not `main`
  (branch policy of 2026-10-20).
- **Plan:** `Implementations-Plan-Rest-API.md` phase R2 (section 8), design
  decisions E2–E4 (section 5.1), couplings K1–K6 (section 2.3), open question
  O-1 (section 10). Guardrails per decision D3 (section 0.1): A1–A5 for the MQTT
  path and shared code, best practices and Home Assistant requirements for REST.
  Report structure: `implementation-plan.md` section 20.
- **Numbering:** REST plan phase R2 is reported as phase 16. R1 (measurement of
  the actuator overrun on the maintainer's installation) was skipped for now at
  the maintainer's request; it does not depend on R2.

## 1. Scope, base and reference versions

| Field | Value |
|-------|-------|
| Repository | `fubu2k/ha-opus-greennet` |
| Branch | `feat/opus-r2-rest-only-decoupling` (new) |
| Base | `feature/rest-api-integration` `4014b1cc554ccf370279ea5d9df1d81c08906501` ("Merge pull request #16", R0 + D3) |
| Base check | `docs/quality/phases/phase-15.md` present on the base |
| `main` | not touched (REST transport stays off `main`, PR #15) |
| Home Assistant reference | `2026.10.2`, home-assistant/core `8d7fd8b68677c2585be36ec86043308f403875b8` (unchanged since phase 8) |

In scope (only this):

1. New transport-neutral module `domain.py` (E2); `coordinator.py` and
   `mqtt_transport.py` re-export its names unchanged.
2. `rest_coordinator.py`, `transport.py`, `entity.py` and all platforms import
   only from `domain.py` / `transport.py` (K2, K3).
3. `transport.OpusCoordinator` as a `typing.Protocol` instead of a union (E3, K6).
4. MQTT, `mqtt_transport` and `discovery` imported on demand in `__init__.py`
   and `config_flow.py` (E4, K4).
5. Check of `"dependencies": ["mqtt"]` → `"after_dependencies": ["mqtt"]` (O-1, K1).

Out of scope: any change of behaviour, timing, wire values, requests or
entities on either transport; R3–R8 items (B-1 … B-8, K7).

## 2. Authorised changes and guardrails

Authorised by the phase prompt: the five items above. A re-export is no
behaviour change. Nothing beyond that was changed.

| ID | MQTT path (A1–A5 binding) | REST path (D3) |
|----|---------------------------|----------------|
| A1 | No new or changed publish; `test_traffic_baseline.py` green unchanged | No request added or removed |
| A2 | Health and availability code untouched | untouched |
| A3 | No retry, no cooldown, timeouts unchanged | unchanged |
| A4 | Retained protection of phase 10 untouched (`coordinator.py` only lost moved definitions) | n/a |
| A5 | Unique IDs, entity IDs, device identifiers, registry settings and the transport of existing entries unchanged; no automatic transport change | unchanged |

The values of every moved name are pinned by
`test_domain_values_are_unchanged` (signals, `201`, `"superseded"`, 60 s,
`(5, 20)`, `CommandDelivery` values, `CoverMovement.ends_at` = start + travel +
5 s grace).

## 3. Files, requirements and reproducers

| File | Change | Requirement |
|------|--------|-------------|
| `custom_components/opus_greennet/domain.py` | **new**: `CommandDelivery`, `CoverMovement`, the four dispatcher signals, `request_error`, `ACK_STATUS_DEFERRED`, `SUPERSEDED`, `GATEWAY_HEALTH_INTERVAL`, `OUTBOUND_STATE_RECONCILIATION_DELAYS` (moved verbatim) | E2 |
| `coordinator.py` | definitions removed, same names imported from `domain.py` (re-export); `request_error` from `domain.py` | E2 |
| `mqtt_transport.py` | `request_error` moved, imported from `domain.py` (re-export) | E2 |
| `rest_coordinator.py` | imports from `domain.py` instead of `coordinator.py` / `mqtt_transport.py` | K2 |
| `entity.py`, `light.py`, `switch.py`, `cover.py`, `climate.py`, `sensor.py`, `binary_sensor.py`, `event.py`, `lock.py` | `from .coordinator import` → `from .domain import` (same names) | K3 |
| `transport.py` | `OpusCoordinator` is a `typing.Protocol` listing exactly what platforms, diagnostics and services use; imports neither coordinator | E3, K6 |
| `__init__.py` | no module-level import of `coordinator.py`; MQTT entries import it in the executor (`homeassistant.helpers.importlib.async_import_module`) and take the class through the package attribute `OpusGreenNetCoordinator`, which a PEP 562 `__getattr__` resolves on first use | E4, K4 |
| `config_flow.py` | no module-level import of `homeassistant.components.mqtt`, `mqtt_transport` or `discovery`; the MQTT steps import them on first use in the executor; `config_flow.async_probe_gateway` delegates to the unchanged MQTT probe; `config_flow.mqtt` stays resolvable (PEP 562) | E4, K4 |
| `manifest.json` | `"dependencies": ["mqtt"]` → `"after_dependencies": ["mqtt"]` (separate commit `e80884c`) | O-1, K1 |
| `tests/conftest.py` | new fixture `hass_without_mqtt` (the shared `hass` fixture is unchanged) | K5 |
| `tests/test_rest_only_decoupling.py` | **new**, 47 test cases (section 5) | R2 tests |
| `CLAUDE.md`, `README.md`, `docs/quality/protocol-contract.md` 12.1, `Implementations-Plan-Rest-API.md` (R2 status, O-1), `implementation-plan.md` section 7 | documentation | delivery list |

Existing tests: no test file was changed. All MQTT and REST tests ran
unchanged; their patch targets (`custom_components.opus_greennet.OpusGreenNetCoordinator`,
`config_flow.mqtt.is_connected`, `config_flow.async_probe_gateway`) keep working
through the PEP 562 attributes and the delegating probe.

Reproducers (failing before, passing after):

- `test_rest_modules_do_not_import_the_mqtt_component`: on the base, importing
  `rest_coordinator` loads `homeassistant.components.mqtt` (chain
  `rest_coordinator → coordinator → homeassistant.components.mqtt`, see
  section 5, static graph).
- `test_rest_entry_loads_controls_and_unloads_without_mqtt`: with
  `dependencies: ["mqtt"]`, setting up a REST entry makes Home Assistant set up
  the MQTT integration, so `"mqtt" in hass.config.components`; after the change
  it stays absent.
- `test_manifest_loads_mqtt_after_but_does_not_require_it` pins the manifest.

Test-first order on the branch: `19be1e4` (tests) before `260fe97` (code) and
`e80884c` (manifest).

## 4. Gateway/cache versus potential radio counts

No change on either transport. No request path, interval, timeout or queue
priority was touched; only definitions moved and imports changed.

| Traffic | Before | After |
|---------|--------|-------|
| MQTT publishes per endpoint and trigger | baseline of `test_traffic_baseline.py` | identical (test unchanged, green) |
| REST periodic reads | `GET /system/uptime` 60 s, `GET /devices` 30 min | identical |
| Radio requests (both transports) | user commands, at most two status checks per unconfirmed channel, explicit ReCom actions | identical |
| Home Assistant setup of the MQTT integration for a REST-only installation | forced by `dependencies` | not set up |

## 5. Commands and results

### CI (authoritative)

| Run | Head | Result |
|-----|------|--------|
| `39411796543` (workflow_dispatch) | `e80884c` | failure: both test jobs; 4 new tests failed in fixture setup (`_ComponentSet` of HA has no `discard`, `remove` is required). Hassfest fixed reference and moving hassfest green |
| `39412756597` (workflow_dispatch) | `f71a07d` | failure (diagnostic run with a temporary annotation hook; revealed the 4 fixture errors above, no other failure) |
| `39413541722` (workflow_dispatch) | `439cf11` | **success**: `validate`, `Hassfest at Home Assistant 2026.10.2 (fixed reference)`, `Home Assistant 2026.10.2 (fixed reference)`, `Home Assistant (latest stable)` |
| `39414974240` (workflow_dispatch) | `5db40a7` (branch head with report; code identical to `439cf11` plus the revert `bd66a9d`) | **success**: all four jobs green |

A green test job covers ruff, `ruff format --check`, strict mypy, `pytest -v`
with coverage (`fail_under = 97`), the per-module gate
`script/check_coverage.py` (> 95 % per module, `config_flow.py` 100 %) and the
three repetitions of the lifecycle and race suites. Exact test counts and
coverage percentages are in the job logs and the `reference-baseline-*`
artifact (90-day retention); they could not be downloaded from the authoring
environment and are therefore not quoted here.

The temporary annotation hook (`f71a07d`) printed failed tests as GitHub
annotations because run logs were not readable from the authoring
environment. It was reverted in `bd66a9d`; it is not part of the phase.

### O-1 evidence (`after_dependencies`)

| Evidence required | Result | Source |
|-------------------|--------|--------|
| Hassfest at reference commit `8d7fd8b6…` accepts the `mqtt` discovery key with `after_dependencies` | **yes**: job "Hassfest at Home Assistant 2026.10.2 (fixed reference)" green in runs `39411796543` and `39413541722`. Source check: the manifest schema allows both keys independently; imports of `mqtt` are allowed because the manifest has the `mqtt` key (`DISCOVERY_INTEGRATIONS`) and because of `after_dependencies` (`script/hassfest/dependencies.py`) | CI, [Code] |
| MQTT discovery and existing MQTT entries unchanged | **yes**: all MQTT tests unchanged and green (incl. `test_discovery.py`, `test_ha_lifecycle.py`, `test_traffic_baseline.py`); `loader.async_get_mqtt` returns the integration's topic filter from the manifest key without MQTT set up (`test_mqtt_discovery_registration_comes_from_the_manifest`); requirements of after-dependencies and of the `mqtt` discovery key are still processed (`homeassistant/requirements.py`); with MQTT configured, `setup.py` waits for after-dependencies that are being set up, so the start order is unchanged; an MQTT entry without the MQTT integration still ends in setup retry and the MQTT flow still aborts with `mqtt_not_configured` | CI, [Code] at `8d7fd8b6` |
| A REST entry loads without a configured MQTT integration | **yes**: `test_rest_entry_loads_controls_and_unloads_without_mqtt` (setup, stream telegram, PUT command, unload; `mqtt` never in `hass.config.components`) | CI |

Decision: evidence complete and positive, so `manifest.json` was changed.

One difference in Home Assistant's handling, not in the integration's
behaviour: with `dependencies`, Home Assistant set up the MQTT integration
(services, websocket commands) even when no MQTT broker was configured; with
`after_dependencies` it no longer does so for this integration. When the MQTT
integration is configured, Home Assistant still sets it up first and waits for
it (`setup.py`, `_async_process_dependencies`), as before.

### Local checks (executed in the authoring environment)

| Command | Result |
|---------|--------|
| `ruff check .`, `ruff format --check .` (ruff 0.16.9) | passed |
| Python 3.14.6 `compile()` of every module and test | passed |
| strict mypy 2.3.1 against home-assistant/core sources at `8d7fd8b6` (third-party packages missing, PEP 758 `except A, B:` parenthesised in a scratch copy because local mypy runs on Python 3.13) | 0 findings in the integration on base and branch |
| negative control: an extra member in `OpusCoordinator` | mypy reports both coordinators as not satisfying the protocol, so the protocol check is effective |
| static module-level import graph (integration + local HA sources) | base: `rest_coordinator → coordinator → homeassistant.components.mqtt`; branch: no path from the 17 REST-safe modules (197 modules visited); control: `coordinator` reaches MQTT |
| `pytest` | **not run locally**: the package index is not reachable from the authoring environment, so Home Assistant could not be installed. CI is the test evidence |

## 6. Registry and identity compatibility

No unique ID, entity ID, device identifier, translation key, entity category,
default enablement, icon or registry migration changed. The transport of
existing entries is unchanged; entries without `transport` keep using MQTT.
No entity was added or removed.

## 7. Documentation updates

- `CLAUDE.md`: project structure (`domain.py`, protocol in `transport.py`);
  coding pattern for the import boundary.
- `README.md`: project structure, new test module, REST prerequisite (Home
  Assistant does not load the MQTT integration for a REST entry).
- `protocol-contract.md` 12.1: REST-only operation, import boundary, MQTT
  entry without MQTT integration.
- `Implementations-Plan-Rest-API.md`: R2 status, O-1 resolved.
- `implementation-plan.md` section 7: status entry for phase 16.

## 8. Hardware observations, unknowns and residual risks

- No hardware test (not needed: no runtime behaviour change). Not verified on
  a real Home Assistant installation: startup order and log output with and
  without a configured MQTT broker after the manifest change **[unverified]**.
- `light.py` still imports `rest_coordinator` for the REST-only dimmer ramping
  check (`isinstance`). This is a REST dependency of a platform, not an MQTT
  one; it does not block a later MQTT removal (plan goal 5).
- K7 (discovery only via MQTT) stays open for R8.
- The PEP 562 attributes (`OpusGreenNetCoordinator` in the package,
  `config_flow.mqtt`) exist for compatibility with existing imports and patch
  targets; they can be dropped with the MQTT transport.

## 9. Review status and next step

- Final CI run `39414974240` on `5db40a7`: all four jobs green (section 5).
  This commit only records that result; it changes no code, test or workflow.
- Review: open a pull request with base `feature/rest-api-integration` and
  compare `feat/opus-r2-rest-only-decoupling` after review.
- Next REST phase: R1 (measurement, skipped for now) or R3 (REST client
  hardening), each with its own approval.
- No merge, no PR and no release permission is implied.
