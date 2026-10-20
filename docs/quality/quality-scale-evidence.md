# Quality scale evidence matrix

Rule -> implementation -> tests -> documentation for every rule in
`custom_components/opus_greennet/quality_scale.yaml`.

## Status of this assessment

- Opus GreenNet Bridge is a **custom integration**. Home Assistant assigns
  quality scale tiers only to core integrations. This file and
  `quality_scale.yaml` are a verifiable self-assessment, **not an official
  rating**. `manifest.json` intentionally declares no `quality_scale` key.
- Rule keys, tiers and the file schema are taken from hassfest
  (`script/hassfest/quality_scale.py`, home-assistant/core commit
  `7ff0e7fbdea4e079ae3f99e048497f5e8c7e3360`, 2026-10-14): 54 rules (bronze 20,
  silver 10, gold 21, platinum 3). Phase 8 rechecked them against the fixed
  reference, Home Assistant `2026.10.2` (core
  `8d7fd8b68677c2585be36ec86043308f403875b8`): same 54 keys and tiers. Rule
  texts: <https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/>.
- Hassfest skips `quality_scale.yaml` for custom integrations. The CI test
  `tests/test_quality_scale.py` therefore applies the official schema, the
  official rule keys, the applicable official validators (`config-flow`,
  `runtime-data`, `strict-typing`, `test-before-setup`, `unique-config-entry`,
  `discovery`) and checks that this table matches the YAML.
- Summary: **45 done, 9 exempt, 0 todo** (phase 9 closed `discovery`,
  `docs-examples`, `docs-use-cases`, `icon-translations` and `repair-issues`).
- **Phase 13 final audit** (2026-10-20, base `main`
  `96510dd52931fac560cef4904ca5fcabda05348a`, report `phases/phase-13.md`):
  every row below was re-verified against the code at that base. All named
  test files and test functions exist, every cited README heading exists, the
  code facts (classes, defaults, categories, icons, manifest keys, timing
  constants) match, and every exemption is re-justified in the section
  "Exemption justifications". No status changed; exemption comments were made
  more explicit. The audit is static; the test result of record is the CI run
  on the phase 13 branch (see the report).
- Result: every applicable rule is done or justified as exempt. One finding
  stays open: **F13-1**, the rocker event entities carry no device class
  although `EventDeviceClass.BUTTON` fits (`entity-device-class`, "where
  possible"). It is a one-attribute metadata change and therefore outside this
  documentation-only phase. The **Platinum-equivalent self-assessment** is
  final once F13-1 is closed or the maintainer records why it stays open. It remains a
  self-assessment: the exemptions have not been accepted by an official Home
  Assistant review, and discovery, ACK 201 handling and radio/battery effects
  have not been observed on hardware (see "Limits of this evidence").

`QS` = `tests/test_quality_scale.py`, `TR` = `tests/test_translations.py`,
`DI` = `tests/test_discovery.py`, `IC` = `tests/test_icon_translations.py`,
`RI` = `tests/test_repair_issues.py`.

## Bronze

| Rule | Status | Implementation | Tests | Documentation |
|------|--------|----------------|-------|---------------|
| `action-setup` | done | `__init__.py`: `async_setup` -> `_register_services`; handlers validate entry and device | `test_service_actions.py::test_actions_are_registered_before_any_entry_loads`, `::test_unknown_and_unloaded_entries_are_validation_errors`; QS `test_actions_are_set_up_in_async_setup` | README Services |
| `appropriate-polling` | exempt | Push integration (`iot_class: local_push`); `entity.py` `_attr_should_poll = False`; 60 s gateway uptime probe only (`coordinator.py` `GATEWAY_HEALTH_INTERVAL`), a gateway endpoint without device queries | QS `test_every_entity_class_has_entity_name_and_no_polling`; TR `test_every_created_entity_has_a_translated_name` (`should_poll is False`); `test_traffic_baseline.py::test_frozen_timing_baseline`, `::test_gateway_lifecycle_traffic` | README Availability and command behavior |
| `brands` | done | `brand/icon.png`, `icon@2x.png`, `logo.png`, `logo@2x.png` (local brand images, HA 2026.3+) | QS `test_brands` | [Brand images](https://developers.home-assistant.io/docs/core/integration/brand_images/) |
| `common-modules` | done | `coordinator.py` (runtime coordinator), `entity.py` (`OpusGreenNetEntity`) | QS `test_common_modules` | README Project Structure |
| `config-flow` | done | `config_flow.py`, `manifest.json` `config_flow: true`, `data_description` in `strings.json` | QS `test_config_flow_validator`; TR `test_config_flow_errors_and_aborts_are_translated` | README Configuration |
| `config-flow-test-coverage` | done | `config_flow.py` at 100 % (CI gate) | `test_config_flow_recovery.py` (every error followed by a recovering submit, unique ID asserted), `test_config_flow.py`, `test_ha_lifecycle.py`; `script/check_coverage.py` | `phases/phase-07.md` |
| `dependency-transparency` | done | `manifest.json` `requirements: []` | QS `test_no_requirements_for_dependency_rules` | – |
| `docs-actions` | done | `services.yaml`, service strings | TR `test_services_yaml_matches_service_strings` | README Services |
| `docs-conditions` | exempt | No conditions provided | – | – |
| `docs-high-level-description` | done | – | QS `test_documentation_rules_point_to_existing_sections` | README introduction, Features |
| `docs-installation-instructions` | done | – | QS `test_documentation_rules_point_to_existing_sections` | README Prerequisites, MQTT Bridge Setup, Installation |
| `docs-removal-instructions` | done | – | QS `test_documentation_rules_point_to_existing_sections` | README Removing the integration |
| `docs-triggers` | exempt | No triggers provided (no `device_trigger.py`, no trigger platform) | – | – |
| `entity-event-setup` | done | `entity.py` `async_added_to_hass` with `async_on_remove(async_dispatcher_connect(...))`, inherited by all entity classes | `test_event_lifecycle.py`, `test_binary_sensor_discovery.py::test_rediscovery_waits_for_live_binary_removal`, `test_ha_lifecycle.py::test_config_flow_creates_and_unloads_entry` | – |
| `entity-unique-id` | done | Unique IDs from EAG ID, device ID, channel and key (`entity.py`, platforms) | `test_beta_entity_migration.py`, `test_binary_sensor_descriptions.py::test_description_inventory_matches_stable`, `test_entities.py::test_platform_setup_adds_expected_entities` | `protocol-contract.md` §8 |
| `has-entity-name` | done | `_attr_has_entity_name = True` on every entity class | QS `test_every_entity_class_has_entity_name_and_no_polling`; TR `test_every_created_entity_has_a_translated_name` | – |
| `runtime-data` | done | `entry.runtime_data = OpusGreenNetRuntimeData(...)` | QS `test_runtime_data_validator`; `test_init.py::test_setup_creates_gateway_registry_device` | – |
| `test-before-configure` | done | `config_flow.py` probes the gateway uptime endpoint (`async_probe_gateway`) | `test_config_flow_recovery.py::test_recovers_from_unresponsive_gateway`, `::test_recovers_from_disconnected_broker` | README Configuration |
| `test-before-setup` | done | `async_setup_entry` raises `ConfigEntryNotReady` | QS `test_test_before_setup_validator`; `test_ha_lifecycle.py::test_setup_retries_and_cleans_subscriptions_for_unreachable_gateway` | README Troubleshooting, Recovery |
| `unique-config-entry` | done | `async_set_unique_id(eag_id)` + `_abort_if_unique_id_configured()` | QS `test_unique_config_entry_validator`; `test_config_flow_recovery.py::test_second_entry_for_same_gateway_is_aborted` | – |

## Silver

| Rule | Status | Implementation | Tests | Documentation |
|------|--------|----------------|-------|---------------|
| `action-exceptions` | done | `ServiceValidationError` for invalid input, translated `HomeAssistantError` (`request_error`) for failed requests and entity commands | `test_service_actions.py::test_unknown_device_is_a_validation_error_without_publish`, `::test_rejected_read_surfaces_translated_error`; `test_coordinator_transport.py::test_rejected_or_malformed_ack_fails_action`, `::test_offline_commands_are_never_published` | README Troubleshooting |
| `config-entry-unloading` | done | `async_unload_entry` unloads platforms, then stops the coordinator | `test_init.py::test_unload_stops_coordinator_after_platforms`, `::test_failed_platform_unload_keeps_coordinator_running`; `test_coordinator_transport.py::test_unload_cancels_request_and_subscription` | – |
| `docs-configuration-parameters` | exempt | No options flow | – | README Known limitations |
| `docs-installation-parameters` | done | `data_description` for the EAG identifier | TR `test_config_flow_errors_and_aborts_are_translated` | README Configuration |
| `entity-unavailable` | done | `OpusGreenNetEntity.available` follows `coordinator.available` | `test_ha_lifecycle.py::test_entity_service_and_broker_reconnect`; `test_coordinator_transport.py::test_mqtt_disconnect_still_marks_gateway_unavailable` | README Availability and command behavior |
| `integration-owner` | done | `manifest.json` `codeowners` | QS `test_integration_owner` | – |
| `log-when-unavailable` | done | `_set_gateway_available` logs once at info on each change | `test_coordinator_edge_cases.py::test_unavailability_is_logged_once_and_recovery_once` | – |
| `parallel-updates` | done | `PARALLEL_UPDATES = 0` in every platform; transport serialises requests | QS `test_parallel_updates_in_every_platform`; `test_coordinator_transport.py::test_global_send_queue_keeps_one_request_in_flight` | – |
| `reauthentication-flow` | exempt | No credentials in the config entry | – | README MQTT Bridge Setup |
| `test-coverage` | done | CI: `fail_under = 97`, `script/check_coverage.py` (> 95 % per module) | `tests/test_coverage_gate.py` | `phases/phase-07.md` |

## Gold

| Rule | Status | Implementation | Tests | Documentation |
|------|--------|----------------|-------|---------------|
| `devices` | done | Gateway device in `__init__.py`, EnOcean devices via `device_info` with `via_device_id` (`entity.py`) | `test_init.py::test_setup_creates_gateway_registry_device`; `test_binary_sensor_discovery.py::test_gateway_discovery_and_tracking_are_isolated` | – |
| `diagnostics` | done | `diagnostics.py` (config entry and device diagnostics, `TO_REDACT`, identifier redaction in text) | `test_diagnostics.py`, `test_diagnostics_edge_cases.py`; QS `test_stale_devices_and_diagnostics_entry_points` | README Troubleshooting |
| `discovery` | done | `manifest.json` `mqtt: ["EnOcean/+/stream/devices/+/friendlyId"]`; `discovery.py` (`normalize_eag_id`, `eag_id_from_discovery`); `config_flow.py` `async_step_mqtt` + `async_step_discovery_confirm` (shared unique ID, abort before side effects for known/ignored gateways, existing fresh probe on confirm) | DI (validation, duplicates, multiple gateways, ignored/disabled/invalid messages, setup races, error recovery, no publish before confirm); QS `test_discovery_status_matches_the_code` | README Automatic gateway discovery, Gateway not discovered, Known limitations |
| `discovery-update-info` | exempt | No network address stored; discovery announces only the EAG identifier, which is the unique ID | DI `test_configured_gateway_aborts_without_side_effects` (no entry update) | – |
| `docs-data-update` | done | – | QS `test_documentation_rules_point_to_existing_sections` | README Availability and command behavior |
| `docs-examples` | done | – | QS `test_documentation_rules_point_to_existing_sections` | README Examples (rocker control, handle state, alarm, dashboard) |
| `docs-known-limitations` | done | – | QS `test_documentation_rules_point_to_existing_sections` | README Known limitations |
| `docs-supported-devices` | done | `const.py` `EEP_MAPPINGS` | TR `test_every_created_entity_has_a_translated_name` (all EEPs) | README Supported Device Types |
| `docs-supported-functions` | done | – | QS `test_documentation_rules_point_to_existing_sections` | README Features, Supported Device Types, Services |
| `docs-troubleshooting` | done | – | QS `test_documentation_rules_point_to_existing_sections` | README Troubleshooting |
| `docs-use-cases` | done | – | QS `test_documentation_rules_point_to_existing_sections` | README Use cases |
| `dynamic-devices` | done | `SIGNAL_DEVICE_DISCOVERED` dispatcher listeners in every platform | `test_coordinator_parsing.py::test_live_device_discovery_runs_after_initial_timer_completed`; `test_entities.py::test_sensor_setup_adds_new_types_after_profile_discovery`; `test_platform_edge_cases.py::test_lock_setup_adds_already_discovered_handles` | README Removing or replacing a device |
| `entity-category` | done | `EntityCategory.DIAGNOSTIC` for signal strength, actuator status binary sensors, the D2-06-40 `unlock_request`/`mechanics_fault` handle diagnostics and the SMS `supply_voltage` (enabled) | `test_binary_sensor_descriptions.py::test_description_inventory_matches_stable`; `test_entities.py::test_sensor_values_and_metadata`; `test_handle_diagnostics.py`; `test_sensor_metadata.py` | `phases/phase-06.md` |
| `entity-device-class` | done | Device classes on sensors, binary sensors and covers (`CoverDeviceClass.BLIND`, D2-05 position and angle); rocker event entities have no class yet: open finding F13-1 (`EventDeviceClass.BUTTON`), see `phases/phase-13.md` | `test_entities.py::test_sensor_values_and_metadata`, `::test_moisture_sensor_values_and_metadata`; `test_binary_sensor_descriptions.py` | – |
| `entity-disabled-by-default` | done | `entity_registry_enabled_default = False` for the signal strength sensor and the D2-06-40 `unlock_request`/`mechanics_fault` diagnostics (new registry entries only) | `test_entities.py::test_sensor_values_and_metadata`; `test_handle_diagnostics.py::test_new_handle_registers_disabled_diagnostics`, `::test_existing_diagnostic_entries_keep_user_settings`, `::test_activating_diagnostics_sends_no_device_request` | README HOPPE window handles; `phases/phase-06.md` |
| `entity-translations` | done | `translation_key` on every named entity; `strings.json`, `translations/en.json`, `translations/de.json` | TR (all tests) | – |
| `exception-translations` | done | `translation_domain`/`translation_key` on raised Home Assistant errors | TR `test_raised_exception_keys_are_translated`; `test_service_actions.py::test_rejected_read_surfaces_translated_error` | – |
| `icon-translations` | done | `icons.json`: `sensor.handle_state`, `sensor.unlock_request`, `binary_sensor.handle_closed`, `binary_sensor.mechanics_fault` (state icons with neutral defaults) and four service icons; every other entity keeps its domain/device-class icon; no Python icon property | IC (states, fallbacks, unchanged classes, HA icon loader, no MQTT); CI `script/check_icons.py` against the MDI catalogue of core `8d7fd8b`; hassfest at `8d7fd8b` (schema) | README HOPPE window handles; `phases/phase-09.md` |
| `reconfiguration-flow` | exempt | The only field (EAG identifier) is the config entry unique ID | QS `test_reconfiguration_exemption_matches_the_code` | README Known limitations |
| `repair-issues` | done | `entity.py` `async_create_duplicate_beta_entity_issue` (persistent, fixable, confirm flow of the repairs integration) after the one-time duplicate removal; `__init__.py` `async_remove_entry` deletes the entry's issues | RI (creation, no issue without removal or on outages, restart persistence, confirmation, entry removal) | README Repairs, Upgrading from community test versions |
| `stale-devices` | done | `async_remove_config_entry_device`; no automatic removal (gateway cannot confirm a device is gone) | `test_device_removal.py`; QS `test_stale_devices_and_diagnostics_entry_points` | README Removing or replacing a device |

## Platinum

| Rule | Status | Implementation | Tests | Documentation |
|------|--------|----------------|-------|---------------|
| `async-dependency` | exempt | No Python requirements | QS `test_no_requirements_for_dependency_rules` | – |
| `inject-websession` | exempt | No HTTP client imports | QS `test_no_requirements_for_dependency_rules` | – |
| `strict-typing` | done | `[tool.mypy] strict = true` for all modules; CI step `python -m mypy`; typed `OpusGreenNetConfigEntry` | QS `test_strict_typing_requires_typed_config_entry_everywhere`, `test_strict_typing_gate_is_configured_and_enforced_in_ci` | `phases/phase-05.md` |

## Exemption justifications (phase 13)

Each exemption was checked against the rule text and the code at the audit
base. "Check" names what would invalidate the exemption.

| Rule | Why the rule does not apply | Check |
|------|-----------------------------|-------|
| `appropriate-polling` | The rule sets the update interval of polling integrations. Entity state arrives only by MQTT push (`local_push`, `should_poll = False`). The 60 s uptime probe measures gateway reachability, updates no entity state from devices and addresses no radio device (guardrails A1, A2). | An entity with `should_poll = True`, an `update_interval`, or a periodic `put/devices/<DEV>/state` query. |
| `docs-conditions` | No condition platform and no `device_condition.py`. | A condition module is added. |
| `docs-triggers` | No trigger platform and no `device_trigger.py`. Rocker presses are event entities; the core state trigger covers them and README Examples shows it. | A trigger module is added. |
| `docs-configuration-parameters` | No options flow, so nothing can be configured after setup. The single setup value is covered by `docs-installation-parameters`. | `async_get_options_flow` or an `OptionsFlow` is added. |
| `reauthentication-flow` | The config entry stores only the EAG identifier, no credentials. Broker credentials belong to the Home Assistant MQTT integration and the Mosquitto bridge; the gateway password is set in the bridge configuration (README MQTT Bridge Setup). | A secret is stored in the config entry. |
| `discovery-update-info` | No host, port or other network data is stored. The gateway is addressed by its EAG identifier through the shared broker; discovery announces only that identifier, which is the unique ID, so there is nothing to update. | A network address is stored in the entry. |
| `reconfiguration-flow` | The only value is the EAG identifier, which is the unique ID. A different identifier is a different gateway and therefore a new entry. | A changeable, non-identity setting is added. |
| `async-dependency` | `manifest.json` `requirements: []`; the integration uses Home Assistant's async MQTT API. | A Python requirement is added. |
| `inject-websession` | No HTTP client is imported or used. | Any HTTP client use. |

## Limits of this evidence

- Tests simulate MQTT. They pin publish counts by endpoint scope
  (`test_traffic_baseline.py`), not radio transmissions, and cannot rule out
  hidden firmware radio effects.
- Not observed on hardware: MQTT discovery, ACK 201 (SoftSmartAck) delivery,
  late-answer frequency, battery impact of any change.
- Icon tests check `icons.json`, the MDI catalogue of core `8d7fd8b` and Home
  Assistant's icon loader; no rendered frontend was inspected.
- Exemptions are this repository's reasoning; an official review may judge
  them differently.
