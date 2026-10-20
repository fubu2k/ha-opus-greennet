# Phase 20 – REST plan R4.3: REST-only setup of new gateways

- **Status:** implemented on branch `feat/opus-r4-3-rest-only-setup`, not yet
  merged. CI section 5. No version change, no CHANGELOG entry, no release;
  `main` untouched (branch policy of 2026-10-20).
- **Origin:** maintainer request of 2026-10-20: the branch should set up a
  gateway like the working upstream build `opus-greennet-v1.0.0b0.zip`, with
  only the REST dialog (address, `admin`, password) and no MQTT dialog.
  Supersedes the menu of phase 19 (R4.2). Guardrails per decision D3.
- **Numbering:** R4.3 is reported as phase 20.

## 1. Cause of the reported MQTT dialog

Two MQTT entry points existed on `c8573ef`, none in the upstream ZIP:

1. `manifest.json` `"mqtt": ["EnOcean/+/stream/devices/+/friendlyId"]`:
   Home Assistant offers a bridged gateway under **Discovered**; its dialog
   was an MQTT confirmation (uptime check over the broker).
2. Manual setup opened a menu (REST first since R4.2, MQTT second).

## 2. Scope and decision

| Path | Before | After |
|------|--------|-------|
| Add integration (manual) | menu: REST / MQTT | **REST form directly** (host, port `8080`, user `admin`, password); step `user` |
| Discovered gateway (MQTT discovery) | MQTT confirmation | **same REST form**, password suggested with the announced EURID; accepted only if the address answers with that EURID (`wrong_gateway` otherwise) |
| Reauth (REST entries) | REST form | unchanged |
| Reconfigure (existing entry) | menu REST / MQTT | unchanged: the only way to choose MQTT |
| Existing MQTT entries | MQTT | **unchanged** (A5); no automatic switch, no reauth |

MQTT discovery is kept, not removed as in the upstream ZIP: it only names a
candidate and now leads into the REST dialog, so the quality-scale rule
`discovery` stays `done`. Removing the manifest filter would be a separate
decision.

Removed: steps `mqtt_bridge` and `rest_api`, `STEP_USER_DATA_SCHEMA`, the
`invalid_eag_id` form error and their translations. `validate_input` stays for
the reconfigure step `reconfigure_mqtt`.

## 3. Guardrails

| ID | Effect |
|----|--------|
| A1 | No runtime change. Discovery confirmation no longer publishes the MQTT uptime request; it reads `GET /system/info` (gateway only, no radio) |
| A2–A4 | untouched |
| A5 | Existing entries, unique IDs (EURID), entity IDs and registry settings unchanged; MQTT entries keep MQTT; transport switches only by explicit reconfigure |

## 4. Files

| File | Change |
|------|--------|
| `config_flow.py` | `async_step_user` shows the REST form; `async_step_discovery_confirm` asks for the REST connection; `_async_create_rest_entry`; MQTT setup steps removed |
| `strings.json`, `translations/en.json`, `translations/de.json` | `user` and `discovery_confirm` with REST fields and descriptions; `mqtt_bridge`, `rest_api`, `invalid_eag_id` removed |
| `quality_scale.yaml` | `discovery` comment |
| `tests/ha_helpers.py` | `start_user_flow(hass)` expects the REST form; `configure_bridge` adds an MQTT entry with the former step's normalisation, probe, data and unique ID (MQTT entries can no longer be created by the flow) |
| `tests/test_rest_config_flow.py`, `test_config_flow_recovery.py`, `test_discovery.py`, `test_ha_lifecycle.py`, `test_rest_only_decoupling.py`, `test_translations.py` | adapted to the REST-only setup; discovery tests cover the REST confirmation, wrong gateway, recovery and abort paths |
| `README.md` | Configuration (REST form only, existing MQTT entries), gateway discovery |
| `Implementations-Plan-Rest-API.md` (R7), `implementation-plan.md` §7, `phases/phase-19.md` (CI, superseded) | status |

Note on authorship: the code and test changes were found as uncommitted work
in the session's working copy (timestamps 16:29–16:30, partial "R4.3"
attempt). They were reviewed, completed (lint, unused imports, README,
quality scale, documentation, this report) and committed together.

## 5. Commands and results

| Command | Result |
|---------|--------|
| `ruff check .`, `ruff format --check .` (ruff 0.16.9) | passed |
| translation check: every form error and abort reason used by `config_flow.py` exists in all three JSON files, no unused error key | passed |

Home Assistant cannot be installed locally (pypi.org blocked); the flow
tests, the `config_flow.py` 100 % coverage gate and Hassfest run in CI.

| Run | Head | Result |
|-----|------|--------|
| `39449339335` (workflow_dispatch) | `feat/opus-r4-3-rest-only-setup` `ae43b7d` | **success**: `validate`, `Hassfest at Home Assistant 2026.10.2 (fixed reference)`, `Home Assistant 2026.10.2 (fixed reference)`, `Home Assistant (latest stable)` |

## 6. Residual risks

- Users who relied on adding a new gateway through MQTT must add it over REST
  (or add it over REST and switch to MQTT in Reconfigure).
- A discovered gateway now needs its IP address; the EURID is no longer
  enough for the offer.
- No hardware test.

## 7. Next step

PR base `feature/rest-api-integration`, never `main`. After the merge, record
the CI run here and in both plans. No release permission is implied.
