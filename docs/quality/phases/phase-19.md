# Phase 19 – REST plan R4.2: REST first in the setup menu

- **Status:** implemented on branch `feat/opus-r4-2-rest-first-menu`, not yet
  merged. CI pending (section 5). No version change, no CHANGELOG entry, no
  release; `main` untouched (branch policy of 2026-10-20).
- **Origin:** maintainer request of 2026-10-20 ("REST an erster Stelle, MQTT
  an zweiter, als R4.2"). It brings forward the first item of REST plan R7
  ("REST als erste Menüoption"); the second R7 item (a "fallback" hint in the
  MQTT menu text) stays open for R7. Guardrails per decision D3 (section 0.1
  of `Implementations-Plan-Rest-API.md`). Report structure:
  `implementation-plan.md` section 20.
- **Numbering:** R4.2 is reported as phase 19. It does not change the R4
  stream lifecycle (phase 18).

## 1. Scope, base and reference versions

| Field | Value |
|-------|-------|
| Repository | `fubu2k/ha-opus-greennet` |
| Branch | `feat/opus-r4-2-rest-first-menu` (new) |
| Base | `feature/rest-api-integration` `c4aa809` ("Merge pull request #22", R4 CI record) |
| `main` | not touched |
| Home Assistant reference | `2026.10.2`, home-assistant/core `8d7fd8b68677c2585be36ec86043308f403875b8` (unchanged) |

In scope (only this): the order of the two options of the manual setup menu
(`config_flow.async_step_user`): REST API first, MQTT bridge second.

Out of scope: menu texts, the reconfigure menu (already REST first since
phase 14), MQTT discovery (`async_step_mqtt`), any automatic transport switch,
the stream, requests and timing.

## 2. Authorised changes and guardrails

| ID | Effect |
|----|--------|
| A1 | No request, poll, interval or retry changed; the menu itself sends nothing |
| A2 | untouched |
| A3 | untouched |
| A4 | untouched |
| A5 | **Existing entries are not touched**: an entry without `transport: rest` stays on MQTT; no unique ID, entity ID, device identifier or registry setting changes. The user still chooses the transport explicitly; nothing is preselected or chosen automatically |

User-visible change: in **Settings → Devices & services → Add integration →
Opus GreenNet Bridge** the first option is now "Directly through the gateway's
REST API" (de: "Direkt über die REST-API des Gateways"), the second "Through
the MQTT broker (EAG identifier)". Both lead to the unchanged steps. This is
deliberately different from upstream 1.0.0b0, which removes MQTT and switches
existing entries to REST automatically.

## 3. Files and tests

| File | Change |
|------|--------|
| `custom_components/opus_greennet/config_flow.py` | `menu_options=[STEP_REST_API, STEP_MQTT_BRIDGE]` (before: MQTT first); docstring |
| `strings.json`, `translations/en.json`, `translations/de.json` | keys of `config.step.user.menu_options` reordered to match the menu; texts unchanged |
| `tests/ha_helpers.py` | `start_user_flow` expects `["rest_api", "mqtt_bridge"]` |
| `tests/test_rest_config_flow.py` | **new** `test_user_menu_offers_rest_first_and_mqtt_second` (menu order, no entry created without a choice) and `test_translated_user_menu_lists_rest_first` (order in all three JSON files) |
| `README.md` | configuration section: REST described first, sentence on the menu order |
| `Implementations-Plan-Rest-API.md` (R7), `implementation-plan.md` section 7 | status |

Reproducer: on the base, `start_user_flow` with the new assertion and
`test_user_menu_offers_rest_first_and_mqtt_second` fail (menu order
`["mqtt_bridge", "rest_api"]`). Every existing MQTT and REST flow test uses
`start_user_flow` with an explicit choice and is otherwise unchanged.

## 4. Gateway/cache versus potential radio counts

Unchanged for MQTT and REST: the menu step sends no request. The steps behind
both options are identical to the base.

## 5. Commands and results

| Command | Result |
|---------|--------|
| `ruff check .`, `ruff format --check .` (ruff 0.16.9) | passed |
| JSON check of the three translation files (order `rest_api`, `mqtt_bridge`) | passed |

Home Assistant could not be installed locally (pypi.org blocked); the flow
tests, coverage (`config_flow.py` 100 % gate) and Hassfest run in CI.

| Run | Head | Result |
|-----|------|--------|
| pending | – | – |

## 6. Registry and identity compatibility

No unique ID, entity ID, device identifier, translation key, entity category,
default enablement, icon or registry migration changed. Translation keys are
unchanged; only their order in the JSON files changed.

## 7. Documentation updates

README configuration section, `Implementations-Plan-Rest-API.md` R7 status,
`implementation-plan.md` section 7.

## 8. Residual risks

- Users who follow older instructions ("choose the first option" for MQTT)
  now land in the REST form; closing the dialog and adding the integration
  again offers the menu once more. Both options keep their names.

## 9. Review status and next step

- Ready for review; PR base `feature/rest-api-integration`, never `main`.
- After the merge: record the CI run here and in both plans.
- No release permission is implied.
