# Phase 14 - Optional REST transport

- **Status:** implemented on branch `feature/rest-api-integration` (branch
  name chosen by the maintainer), CI green (run `39384621424`, section 6).
  Not merged; no pull request opened, no
  version change, no CHANGELOG entry, no release. Merge and release approval
  remain separate (plan sections 4 and 5).
- **Base SHA:** `39b4c66` (`main`, 2026-10-20, "docs: record phase 13 merge,
  CI evidence and changelog").
- **Source of the REST code:** upstream `kegelmeier/ha-opus-greennet`, branch
  `feat/rest-transport`, head `20b34c9` (identical to the archive provided by
  the maintainer, `ha-opus-greennet-feat-rest-transport.zip`). Its merge base
  with this repository's `main` is `5fcc57c` (v0.7.0b1). Commits ported:
  `777775c` (REST transport), `72ed8cb` (D2-01 ramping mode), `41f0f95` (own
  command echo). `6caf509` (port of this repository's phases 6 and 9) is
  already contained in `main` and was not ported again; `20b34c9` is README
  only.
- **Plan:** `implementation-plan.md` revision 3, sections 2-5, 16, 17 and 20;
  new section 15a and a section 3 amendment.
- **Reference:** Home Assistant `2026.10.2`, home-assistant/core
  `8d7fd8b68677c2585be36ec86043308f403875b8` (unchanged since phase 8).

## 1. Scope and result

Maintainer request (2026-10-20): integrate the REST API changes of the
upstream fork into the current `main` without overwriting existing functions.
Maintainer decision on the approach: **REST as an additional transport**, not
as a replacement of MQTT.

| Item | Result |
|------|--------|
| MQTT transport | unchanged: `coordinator.py`, `mqtt_transport.py`, `discovery.py`, retained-replay freshness (phase 10), ACK 201 handling (phase 11) and late-answer diagnostics (phase 12) are not modified |
| Existing entries | stay on MQTT; an entry uses REST only with `transport: rest` in its data |
| New REST transport | `api.py`, `protocol.py` (upstream), `rest_coordinator.py` (upstream coordinator adapted to this repository's interfaces), `transport.py` (selection, common type) |
| Config flow | manual setup is a menu (`mqtt_bridge` = the former user step, `rest_api`); REST reauthentication; reconfigure flow switches the transport in both directions |
| Phase 11 on REST | ACK 201: no estimate, no travel estimate, no status query, `deferred_commands` in diagnostics; the later outbound telegram is a delivery |
| Phase 12 on REST | not applicable (HTTP correlates answers); diagnostics keep the keys with zero counters |
| Upstream dimmer fix | `rampingMode` for D2-01 dimmers and the transition feature on REST only; MQTT wire values unchanged |
| Quality scale | `reauthentication-flow`, `reconfiguration-flow`, `inject-websession`: exempt -> done; 48 done, 6 exempt, 0 todo |

## 2. Authorised changes and guardrails A1-A5

Authorised by the maintainer: the integration of the REST transport and the
documentation "nach bisherigem Standard". Not authorised and not done: MQTT
behaviour changes, MQTT wire value changes, automatic migration of entries,
version, CHANGELOG, release, pull request or merge.

| Guardrail | Effect |
|-----------|--------|
| A1 | No new radio request. REST uses the 60 s uptime interval of MQTT, the same two status checks per unconfirmed channel (none after ACK 201) and ReCom only on explicit service calls. Own-command echoes schedule no extra check (upstream `41f0f95`), which reduces checks compared with treating them as foreign commands. New on REST entries only: `GET /devices` every 30 min, a read of the gateway model without radio telegram (section 3 amendment). The MQTT traffic baseline is unchanged. |
| A2 | The health probe describes the gateway only and never confirms or changes device state. A command timeout or rejection fails only that action and never changes gateway availability. |
| A3 | One radio request in flight (`api.PriorityLock`: commands, queries, background checks), no retries, no cooldowns; MQTT timeouts and queue untouched. |
| A4 | MQTT retained-message protection unchanged. REST has no retained messages; the periodic device list applies only configuration, battery and metadata, never live state. |
| A5 | Unique IDs `<EAG>_<DEV>…`, entity IDs, device identifiers, names, areas and enablement unchanged in both transports. Switching the transport is an explicit user action (reconfigure); entries are never switched automatically; adding an already configured gateway over REST aborts without touching the entry. Tested by `test_switching_an_mqtt_entry_to_rest_and_back_keeps_entities` and `test_adding_mqtt_gateway_over_rest_keeps_its_transport`. |

## 3. Design

- **Two coordinators, one interface.** The MQTT coordinator is
  2,957 lines of MQTT-specific state (flattened topics, retained replays,
  debounced fragments, answer routing). Rewriting it around a transport seam
  would have touched every MQTT protection. Instead the upstream REST
  coordinator is added next to it with the same public interface (device
  model, signals, `available`, commands returning `CommandDelivery`, ReCom
  requests, `late_answer_diagnostics`). `transport.OpusCoordinator` is the
  union type used by entities, diagnostics and services.
- **Selection.** `transport.entry_transport(entry.data)` returns `rest` only
  for `transport: rest`. `__init__.async_setup_entry` starts the MQTT path
  unchanged (`_async_setup_mqtt_coordinator`, identical code) or the REST path
  (`_async_setup_rest_coordinator`, Home Assistant websession,
  `ConfigEntryAuthFailed` for rejected credentials or another gateway). The
  gateway registry call is unchanged; REST entries additionally record the
  gateway firmware as `sw_version`.
- **Shared code.** Signals, `CommandDelivery`, `CoverMovement`,
  `ACK_STATUS_DEFERRED`, `GATEWAY_HEALTH_INTERVAL` and
  `OUTBOUND_STATE_RECONCILIATION_DELAYS` are imported from `coordinator.py`,
  `request_error` from `mqtt_transport.py`, so both transports use the same
  constants and translated errors. `protocol.py` duplicates the pure helpers
  of the MQTT coordinator without the MQTT flattening; a consolidation is
  optional refactoring (plan section 19) and was not done.
- **Adaptations of the upstream coordinator.** Commands return
  `CommandDelivery`; ACK 201 runs the phase 11 handling (`_handle_deferred_command`,
  `_record_command_delivery`, `_clear_delivered_deferred`, helpers
  `protocol.command_values_by_channel` and `protocol.wire_value_matches`); a
  deferred own command is not registered as an echo; a device list keeps
  `deferred_commands`, `profile` and the finalized timestamp of an existing
  device; `late_answer_diagnostics` and `async_query_climate_status` complete
  the interface; `async_forget_device` also drops pending echo signatures.
- **Light.** `light.py` checks `isinstance(coordinator,
  OpusGreenNetRestCoordinator)` before passing `transition` and before
  advertising `LightEntityFeature.TRANSITION`; the MQTT calls keep their exact
  previous arguments.

## 4. Files

| File | Change |
|------|--------|
| `custom_components/opus_greennet/api.py` | new (upstream, docstring note) |
| `custom_components/opus_greennet/protocol.py` | new (upstream) plus ACK 201 helpers |
| `custom_components/opus_greennet/rest_coordinator.py` | new (upstream coordinator, adapted, section 3) |
| `custom_components/opus_greennet/transport.py` | new: `OpusCoordinator`, `entry_transport` |
| `custom_components/opus_greennet/__init__.py` | transport selection; MQTT setup moved unchanged into `_async_setup_mqtt_coordinator`; REST setup |
| `custom_components/opus_greennet/config_flow.py` | menu, `mqtt_bridge` (former user step, unchanged logic), `rest_api`, reauth, reconfigure (`reconfigure_rest`, `reconfigure_mqtt`) |
| `custom_components/opus_greennet/const.py` | `CONF_TRANSPORT`, `TRANSPORT_*`, `DEFAULT_PORT`, `DEFAULT_USERNAME`, `RWM_EEP`, ramping-mode keys |
| `custom_components/opus_greennet/enocean_device.py` | `supports_ramping_mode` |
| `custom_components/opus_greennet/light.py` | REST-only ramping/transition |
| `custom_components/opus_greennet/diagnostics.py` | `transport`; redaction of `baseId` and `homekitPin` |
| `entity.py`, `binary_sensor.py`, `climate.py`, `cover.py`, `event.py`, `lock.py`, `sensor.py`, `switch.py` | type annotation `OpusCoordinator` only |
| `strings.json`, `translations/en.json`, `translations/de.json` | menu, REST steps with `data_description`, reauth/reconfigure, errors, aborts, `invalid_auth`/`wrong_gateway` exceptions |
| `quality_scale.yaml` | three rules exempt -> done, comments for both transports |
| `.github/workflows/tests.yml` | REST suites added to the repeated race runs (both jobs) |
| `tests/fake_gateway.py` | new (upstream): in-process gateway serving the REST API over real HTTP |
| `tests/test_rest_api.py`, `test_rest_commands.py`, `test_rest_devices.py`, `test_rest_requests.py`, `test_rest_stream.py`, `test_rest_telegrams.py`, `test_rest_edge_cases.py`, `test_rest_lifecycle.py`, `test_rest_protocol.py`, `test_rest_reconciliation_feedback.py`, `test_rest_cover_movement.py`, `test_rest_cover_stop.py` | upstream REST suites, renamed modules and helpers; one stop case adapted to ACK 201 semantics |
| `tests/test_rest_config_flow.py` | REST user, reauth and reconfigure flows; every error followed by a recovering submit |
| `tests/test_transport_selection.py` | selection, switching both ways with registry settings, setup errors, ACK 201 on REST, dimmer ramping, diagnostics |
| `tests/conftest.py`, `tests/ha_helpers.py` | resolver without zeroconf, `gateway` and `rest_coordinator` fixtures, menu-aware flow helpers |
| `tests/test_config_flow_recovery.py`, `tests/test_ha_lifecycle.py` | MQTT flows start through the menu (assertions unchanged) |
| `tests/test_translations.py`, `tests/test_quality_scale.py` | new steps and rules; aiohttp only in `api.py`, websession injection |
| `README.md`, `CLAUDE.md`, `docs/quality/protocol-contract.md` (section 12), `capability-matrix.md`, `quality-scale-evidence.md`, `implementation-plan.md` | documentation |

## 5. Gateway/cache versus radio counts

| Scenario | MQTT (unchanged) | REST |
|----------|------------------|------|
| Long idle period, per hour | 60 uptime probes | 60 uptime probes, 2 device list reads (gateway model), 0-4 stream reconnects after 15 min silence |
| Accepted command (200), no feedback | 1 command, 2 status checks | 1 command, 2 status checks; the own echo adds none |
| Accepted command (201) | 1 command, 0 checks | 1 command, 0 checks |
| Foreign command (HomeKit) | 2 status checks | 2 status checks |
| Diagnostics, icons, reconfigure | no device request | no device request |

Counts are taken from code and tests (`test_traffic_baseline.py` for MQTT,
`test_rest_*` and `test_transport_selection.py` for REST). They are HTTP or
MQTT requests, not radio transmissions.

## 6. Commands and results

The authoring environment has no package index, so tests ran only in GitHub
Actions (workflow `Tests`, manual dispatch on the branch). Ruff ran locally as
well. The job logs are not reachable from the authoring environment; failures
were read through a temporary diagnostic workflow that reported pytest
failures and coverage as annotations (`rest-debug.yml`, added and removed on
this branch).

| Run | Head | Result |
|-----|------|--------|
| `39382451298` | `ca99927` | hassfest (reference and master), Ruff and strict mypy green; tests failed (one test, below) |
| `39383150809` (diagnostic) | `c83e5dd` | 1,882 tests, 1 failure: the switch test waited for the fake gateway to close the server side of the old stream; coverage gate failed for `protocol.py` (93.0 %) and `rest_coordinator.py` (94.1 %) |
| `39383911384` (diagnostic) | `189ac5a` | after porting the upstream protocol, feedback and cover suites: 2,081 tests, 1 failure: an upstream stop test expected status checks after ACK 201; adapted to the phase 11 semantics. Coverage: total 98.2 %, `rest_coordinator.py` 98.6 %, `config_flow.py`, `transport.py`, `light.py` 100 % |
| `39384621424` | `e04245f` | **green**: fixed reference (Home Assistant 2026.10.2) and latest stable job (Ruff, mypy, all tests, per-module coverage gate, three repetitions of the lifecycle and race suites including the REST suites), hassfest at the reference commit and hassfest master |

Commands executed by the jobs: those of plan section 17 (`pip check`, `ruff
check .`, `ruff format --check .`, `python -m mypy`, `pytest -v --cov …`,
`python script/check_coverage.py coverage.json`, the repetition loop).

## 7. Registry and identity compatibility

- Entries created before phase 14 contain only `eag_id` and stay on MQTT.
- REST entries use the EURID as unique ID, the same value as the EAG
  identifier, so one gateway can only have one entry.
- Switching MQTT -> REST -> MQTT keeps entity ID, unique ID, user-set name,
  enablement and the device registry entries (tested).
- No entity is created, removed, renamed, enabled or disabled by this phase.

## 8. Hardware observations, unknowns and residual risks

- This repository has not run the REST transport on hardware. Upstream
  observed it on OPUS-GW v1.21.31 (API 2.6.3.0, 65 devices), including the
  echo timing and the absence of streamed battery levels
  (`protocol-contract.md` section 12, **[HW-upstream]**).
- The REST API is plain HTTP with basic authentication (manufacturer design).
- Not known: which profiles receive ACK 201 on REST; whether `GET /devices`
  can cause radio traffic on any firmware; whether the gateway sends
  `content = "device"` documents.
- The manual setup now starts with a menu; MQTT users choose "Through the MQTT
  broker" first. Existing entries are not affected.

## 9. Review status and next step

- Review the branch `feature/rest-api-integration`, in particular the section
  3 amendment of the plan (30 min device list read on REST entries).
- Optional hardware check with separate approval: add a REST entry for one
  gateway or switch a test entry, compare entity IDs, switch commands, a
  dimmer with and without transition and a cover with stop.
- Open decisions: port the D2-01 ramping mode to MQTT (changes MQTT wire
  values); consolidate `protocol.py` with the MQTT helpers (plan section 19).
