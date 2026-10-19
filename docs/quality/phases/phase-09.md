# Phase 9 - Applicable quality-scale gaps

- **Status:** implemented; CI green on the branch (workflow_dispatch run
  `39288963525`, head `0e09a3a`); awaiting review. Not merged, no PR. No
  version change, no release, no CHANGELOG entry.
- **Branch:** `feat/opus-q09-quality-gaps` (new)
- **Base SHA:** `b4f6abf5983ec20451f6611fc92279fe608b87d8` (`main`,
  2026-10-19 18:19 +0200, merge of PR #6 `docs/opus-q08-ci-evidence`)
- **Plan:** `implementation-plan.md` revision 3, section 9 (scope) and
  section 11 (icon policy).
- **Reference:** Home Assistant `2026.10.2`, home-assistant/core
  `8d7fd8b68677c2585be36ec86043308f403875b8`, frontend `20260930.4`
  (unchanged from phase 8).

## 1. Scope and result

The five rules left open in phase 8 are resolved with code, tests and
documentation. The self-assessment changes from 40 done / 9 exempt / 5 todo
to **45 done / 9 exempt / 0 todo**. No tier is claimed (custom integration;
exemptions still need official acceptance; discovery has no hardware
observation).

| Rule | Before | After | Evidence |
|------|--------|-------|----------|
| `discovery` | todo | done | `manifest.json` `mqtt`, `discovery.py`, `config_flow.py`, `tests/test_discovery.py` |
| `docs-examples` | todo | done | README Examples |
| `docs-use-cases` | todo | done | README Use cases |
| `icon-translations` | todo | done | `icons.json`, `tests/test_icon_translations.py`, `script/check_icons.py` |
| `repair-issues` | todo | done | `entity.py`, `__init__.py`, `tests/test_repair_issues.py` |
| `discovery-update-info` | exempt | exempt (re-evaluated) | Discovery announces only the EAG identifier, which is the unique ID; no network address exists to update |

## 2. Authorised changes and guardrails A1-A5

Authorised by the plan (section 9): MQTT discovery with `async_step_mqtt`,
documentation examples, icon translations, a repair issue for the duplicate
beta migration. No other behaviour was changed.

| Guardrail | Effect |
|-----------|--------|
| A1 | No new publish, poller, interval, reconciliation slot or retry. Discovery only adds a subscription made by the Home Assistant MQTT integration; receipt publishes nothing. Confirmation sends the existing single uptime probe (gateway endpoint), exactly as manual setup. Icons and repairs are local metadata. |
| A2 | A retained or live discovery message is a candidate, never a health signal. Known gateways abort before `_abort_if_unique_id_configured`, so discovery does not even reload an entry in setup retry. Gateway availability logic is untouched. |
| A3 | Timeouts, serialisation and the probe are unchanged; a failed confirmation probe is reported (`gateway_unavailable`) and not retried. |
| A4 | Retained handling is unchanged. The discovery filter does not touch the coordinator's retained-message logic. |
| A5 | No unique ID, entity ID, translation key, device class, category or default changed. Manual and discovered setup derive the same unique ID through `normalize_eag_id`. Icons do not override icons selected by the user. The repair does not change any registry entry. |

## 3. Files and requirements

| File | Change |
|------|--------|
| `custom_components/opus_greennet/manifest.json` | **New key** `"mqtt": ["EnOcean/+/stream/devices/+/friendlyId"]` |
| `custom_components/opus_greennet/discovery.py` | **New:** `normalize_eag_id` (shared with manual setup), `eag_id_from_discovery`, `DISCOVERY_TOPIC_FILTER` |
| `custom_components/opus_greennet/config_flow.py` | `async_step_mqtt`, `async_step_discovery_confirm`; `validate_input` and the user step use `normalize_eag_id` (same results as before) |
| `custom_components/opus_greennet/icons.json` | **New:** entity state icons and service icons |
| `custom_components/opus_greennet/entity.py` | `async_create_duplicate_beta_entity_issue`, `async_delete_config_entry_issues`; the duplicate removal raises the issue |
| `custom_components/opus_greennet/__init__.py` | **New** `async_remove_entry`: deletes the entry's issues |
| `custom_components/opus_greennet/strings.json`, `translations/en.json`, `translations/de.json` | `config.flow_title`, step `discovery_confirm`, abort `invalid_discovery_info`, issue `duplicate_beta_entity` with confirm fix flow |
| `custom_components/opus_greennet/quality_scale.yaml` | Five statuses to `done`, `discovery-update-info` comment |
| `script/check_icons.py` | **New:** checks every `mdi:` value against the pinned catalogue |
| `.github/workflows/tests.yml` | Job `validate-reference`: sparse checkout adds `pylint/plugins/pylint_home_assistant/generated`; new step runs `check_icons.py` |
| `tests/test_discovery.py`, `tests/test_icon_translations.py`, `tests/test_repair_issues.py` | **New** |
| `tests/test_quality_scale.py` | Discovery status test renamed and inverted by the code, README headings for examples/use cases, icon and repair evidence |
| `README.md` | Features, HOPPE icons, repair note, Automatic gateway discovery, Known limitations, Use cases, Examples, Repairs, Gateway not discovered, topic list, project tree, testing |
| `docs/quality/quality-scale-evidence.md`, `protocol-contract.md`, `capability-matrix.md`, `implementation-plan.md` | Evidence rows, discovery subscription, repair and icon contract, unverified items, phase status |

### 3.1 Discovery design

The OPUS gateway publishes no gateway-level topic without a request; uptime
and system information are answers only. It does publish retained, flattened
device models below `stream/devices/<DEV>`. The filter takes one narrow leaf
per device, `friendlyId`, instead of `stream/devices/#`, which would make the
MQTT integration start a flow for each of up to ~30,000 retained topics.

Evidence for the leaf: CHANGELOG ("numeric device name from a flattened MQTT
topic, for example `friendlyId` = `123`") and the existing tests. It is
**[unverified]** on hardware and recorded as such in the protocol contract.

Validation (`eag_id_from_discovery`): exact topic shape, non-empty payload,
eight hexadecimal characters already in upper case. Topics are case sensitive
and the coordinator subscribes with the upper-case identifier, so a
lower-case segment could never be served by the resulting entry and is
rejected rather than normalised into an entry that cannot work. The payload
(device name) is neither stored nor logged.

Flow behaviour, each covered by a test:

| Situation | Result |
|-----------|--------|
| Valid first announcement | Confirm form, title placeholder `eag_id`, nothing published |
| Confirm | Existing fresh probe (one uptime request), then entry with unique ID `<EAG>`, source `mqtt`, the discovery key of the topic |
| Further devices of the same gateway | `already_in_progress`; one flow per gateway |
| Two gateways | Two independent flows |
| Configured, disabled or ignored gateway | `already_configured` before a unique ID is set: no publish, no discovery-key rewrite, no reload |
| Invalid topic, empty payload, lower-case identifier | `invalid_discovery_info`, no publish |
| Manual setup while an offer is open | Manual flow aborts `already_in_progress` (Home Assistant's standard behaviour); no duplicate entry; another gateway can still be added |
| Entry appears while the confirm form is open | `already_configured`, no probe |
| Broker disconnected / gateway silent / unexpected error on confirm | Form error `cannot_connect` / `gateway_unavailable` / `unknown`, then a recovering confirmation; the silent gateway gets exactly one probe |
| Manual setup after a discovered entry | `already_configured` without probe |

The pre-check before `async_set_unique_id` matters: Home Assistant would
otherwise append every device topic to the entry's `discovery_keys` (rotating
the last ten on each start for installations with more than ten devices) and
would reload an entry that is in setup retry on each announcement.

### 3.2 Icon audit (plan section 11)

Validated against `mdi_icons.py` at core `8d7fd8b` (frontend `20260930.4`,
7,447 names): all 16 identifiers in `icons.json` exist. A manipulated copy with
`mdi:cog-alert` is rejected by `script/check_icons.py` (checked locally).

| Entity (translation key) | Device class | Decision | Icons |
|--------------------------|--------------|----------|-------|
| `sensor.handle_state` | enum (`open`, `closed`, `tilted`) | custom: the enum class has no state icons | default `help-circle-outline`; `closed` `window-closed`, `open` `window-open`, `tilted` `window-open-variant` (approximation; translated label kept) |
| `sensor.unlock_request` | enum (`requested`, `not_requested`) | custom | default `lock-question`; `not_requested` `lock-outline`; `requested` `lock-clock` (a request, never an open lock) |
| `binary_sensor.handle_closed` | none (ON means closed; WINDOW would invert) | custom: generic default icon otherwise | default `help-circle-outline`; `on` `window-closed`; `off` `window-open` (not closed: open or tilted) |
| `binary_sensor.mechanics_fault` | problem (unchanged) | custom: the problem-class default shows the alert icon for every state other than `off`, i.e. also for unknown | default `cog-outline` (neutral for `off`, unknown, unavailable); `on` `alert-circle` (replaces the unavailable `cog-alert`) |
| `binary_sensor.handle_open`, `handle_tilt` | window | standard window icons kept | – |
| `lock.autolock` | lock | standard lock icons kept | – |
| Rocker event | none | standard kept; `EventDeviceClass.BUTTON` (default `gesture-tap-button`) is a class decision for resumed phase 6 | – |
| Motion, smoke, moisture, opening, battery, problem (HeatArea), temperature, humidity, illuminance, voltage, power, signal strength | device classes | standard kept | – |
| Light, switch, cover (blind), climate | domain | standard kept; cover class (blind vs. shutter) is a phase 6 question | – |
| Gateway (`router-wireless`) | – | not implemented: no gateway entity exists and none is created for an icon | – |
| Service actions | – | icons added (frontend only) | `file-download-outline`, `tune-variant`, `reload`, `file-upload-outline` |

`off` for `mechanics_fault` and missing data share the neutral cog because
hassfest rejects a state icon equal to the default. `off` is still labelled
"OK" by the problem class; the icon makes no extra health claim. No Python
`icon` property or `_attr_icon` exists (tested).

### 3.3 Repair issue

Only one user-remediable case exists: the fork-suffix migration removes a
duplicate beta entry once, and references to its entity ID break. The issue
`duplicate_beta_entity_<removed entity ID>` is persistent (the removal never
repeats, so a non-persistent issue would vanish on restart), fixable through
the repairs integration's confirm flow (no `repairs.py` platform needed),
severity warning, with the removed and preserved entity IDs as placeholders and
`config_entry_id` as data. Confirming deletes it; removing the config entry
deletes the issues of that entry only. Outages and quiet sensors raise none
(tested). The aggregate multi-channel cleanup (`migrate_legacy_multichannel_entity`)
is a similar case outside the plan's scope; it still only logs and is listed as
a candidate below.

### 3.4 Documentation

README: Use cases (including battery-friendly automation advice: no scheduled
ReCom actions, `for:` delays, `reload_entry` is not a refresh) and Examples:
rocker control with a guard against restored event states, handle state to
heating with neutral handling of unknown values, optional mechanics-fault
diagnostic, smoke alarm with outage warning, and a dashboard card. All entity
IDs are placeholders. The examples use only existing entities and actions and
need no runtime change. Estimates and the 5/20-second checks are explained
where the examples send commands.

## 4. Gateway/cache versus radio counts

| Scenario | Before | After |
|----------|--------|-------|
| Discovery message received (retained or live) | not applicable | 0 publications |
| Discovery confirmed | not applicable | 1 `gateway_uptime` (same probe as manual setup), then the unchanged setup baseline |
| Setup, health, reconnect, commands, reconciliation | `test_traffic_baseline.py` | unchanged; the baseline test is not modified |
| Repair creation, confirmation, entry removal | – | 0 publications |
| Icon loading | – | 0 publications, 0 subscriptions |

MQTT counts are not radio transmissions; no hardware claim is made.

## 5. Commands and results

| Check | Result | Origin |
|-------|--------|--------|
| `ruff check .` | passed | local, ruff 0.16.3 (pinned version, binary from GitHub release) |
| `ruff format --check .` | passed (88 files) | local, ruff 0.16.3 |
| `python3 script/check_icons.py <core 8d7fd8b>/pylint/plugins/pylint_home_assistant/generated/mdi_icons.py custom_components/opus_greennet/icons.json` | passed: 16 icons, 7,447 names, frontend `20260930.4` | local, sparse checkout of core tag `2026.10.2` |
| Same script with `mdi:cog-alert` injected | rejected | local |
| `quality_scale.yaml` status count | 45 done, 9 exempt | local (PyYAML) |
| Home Assistant APIs used (`async_step_mqtt`, `_async_current_ids`, `_set_confirm_only`, `MqttServiceInfo`, `DiscoveryKey`, `ir.async_create_issue(is_persistent=...)`, `ConfirmRepairFlow` fallback without a repairs platform, `async_get_icons`, hassfest icon and translation schemas) | read from source at core `8d7fd8b` | local, static |
| pip check, mypy, pytest with coverage gates, three race repetitions | not executed locally (PyPI is blocked in this environment) | – |
| pip check, Ruff, strict mypy, full pytest incl. the three new test modules, total and per-module coverage gates (100 % `config_flow.py`), three race repetitions incl. `test_traffic_baseline.py` | passed | CI, job "Home Assistant 2026.10.2 (fixed reference)" `118184286706`, run `39288963525` (workflow_dispatch on `feat/opus-q09-quality-gaps`, head `0e09a3a`), 2026-10-19 18:50–18:59 +0200 |
| Same gates against the latest stable release | passed | CI, job "Home Assistant (latest stable)" `118184287061`, same run |
| Hassfest built from core `8d7fd8b` (manifest `mqtt`, `icons.json` schema, translations incl. `issues`, discoverable config flow) and `check_icons.py` against the reference MDI catalogue | passed | CI, job "Hassfest at Home Assistant 2026.10.2 (fixed reference)" `118184287145`, same run |
| Hassfest `@master` (moving) | passed | CI, job "validate" `118184286949`, same run |

Because the traffic baseline asserts exact publish counts and was not changed,
its success confirms that setup, health, reconnect and command traffic are
unchanged. Evidence artifact: `reference-baseline-0e09a3a…` (artifact
`12205316465`). Job logs and artifact contents (exact test count, coverage
figures, Python patch) could not be downloaded from this environment (log
storage blocked by the egress proxy); they remain in the GitHub Actions UI for
90 days. The commit that adds these results changes only this report.

## 6. Registry and identity compatibility

- No unique ID, entity ID, name, translation key, device class, entity
  category or default enablement changed (asserted for the handle entities in
  `test_icon_translations.py`).
- Existing enabled or disabled diagnostics keep their state; icons are
  frontend metadata and user-selected icons take precedence in Home Assistant.
- Config entries keep `VERSION = 1`; discovered and manual entries are
  identical apart from `source` and `discovery_keys`.
- The repair changes no registry entry; the migration itself is unchanged.

## 7. Documentation updates

README (see 3.4), quality evidence matrix (all rows, summary 45/9/0),
protocol contract (sections 1.1a, 1.2, 8, 11), capability matrix (discovery
row), implementation plan (section 7 status).

## 8. Hardware observations, unknowns and residual risks

- **No hardware observation.** Whether every firmware publishes a retained
  `stream/devices/<DEV>/friendlyId` per device is unverified. If it does not,
  the gateway is simply not offered; manual setup is unchanged.
- With many devices, Home Assistant starts one short-lived flow per device at
  startup; all but the first abort immediately without I/O. Measured cost on
  large installations is unknown.
- While a discovery offer is open, a manual attempt for the same gateway
  aborts as already in progress (documented).
- Disabling MQTT discovery in the MQTT integration disables gateway discovery.
- Candidate for a later phase: a repair for the removed aggregate multi-channel
  entity; `EventDeviceClass.BUTTON` for rockers and the cover device class
  (resumed phase 6 metadata review).
- Visual rendering of the icons in the frontend was not checked; backend tests
  do not prove rendering.

## 9. Review status and next step

CI passed on `feat/opus-q09-quality-gaps` (run `39288963525`). Awaiting review
and a PR decision. Merge and release authorisation remain separate. Next planned phase: resumed phase 6 (remaining
metadata review, section 10).
