# Phase 1 – Reproduzierbare Ausgangsbasis

## Status und Scope

- **Status:** abgeschlossen. Nur Dokumentation, keine Runtime-Codeänderung.
- **Scope:** Analyse von Datenfluss, Zustandsmutation, Ownership, den Abgleichs-Checks
  nach 5 und 20 Sekunden, Event-Auslösung, Typisierung, Test- und Qualitätsbaseline;
  Dokumentation der Ausschlüsse A1–A5; Prüfung der Folgephasen am realen Code.
- **Nicht im Scope:** jede Änderung unter `custom_components/`, `tests/`,
  `pyproject.toml`, `requirements_test.txt`, CI, `manifest.json`, `CHANGELOG.md`.

## Branch und Basis

| Feld | Wert |
|------|------|
| Repository | `fubu2k/ha-opus-greennet` (`origin`, geprüft per `git remote -v` und `gh repo view`) |
| Branch | `feat/opus-q01-baseline` |
| Vorgängerbranch | `main` |
| Basis-SHA | `3e52fce31e7a2d503eef4820f4c706ebcfc751ab` („fix: ignore retained device discovery answers (#73)“) |
| Prüfung vor Checkout | frischer Clone, `git status --porcelain` leer, `origin/main == HEAD == 3e52fce…`, remote nur `refs/heads/main`, Branch existierte weder lokal noch remote |
| Commit-Identität | repository-lokal `fubu2k <48277447+fubu2k@users.noreply.github.com>` (über `gh api user`, keine Identität vorhanden) |

Die finale HEAD-SHA dieser Phase steht nicht in diesem Commit. Sie wird im Chat
ausgegeben und in `phase-02.md` als Basis-SHA eingetragen.

## Gelesene Grundlagen

`CLAUDE.md`, `README.md`, `opus_mqtt_services_reference.md`, `pyproject.toml`,
`requirements_test.txt`, `.github/workflows/tests.yml`, `.github/workflows/release-asset.yml`,
`hacs.json`, `manifest.json`, `services.yaml`, alle 17 Python-Module unter
`custom_components/opus_greennet/` vollständig, außerdem `tests/conftest.py`,
`tests/ha_helpers.py` und die für die Aufgaben relevanten Testdateien
(`test_event_entity.py`, `test_scalar_telemetry.py`, `test_coordinator_parsing.py`,
`test_coordinator_mqtt.py` (Abgleich), `test_reconciliation_feedback.py`,
`test_cover_movement.py`, `test_cover_stop.py` per Suche).

Projektvorgaben aus `CLAUDE.md`: Feature-Branch `feat/…`, `pytest -v` muss grün
sein, PRs ohne Versions- und Changelog-Änderung, die Entities greifen nie direkt
auf MQTT zu, Gerätezustand liegt in `EnOceanChannel`. Die Upstream-URLs
(`kegelmeier`) in `CLAUDE.md`, `README.md` und `manifest.json` sind historisch
und ändern das Ziel `fubu2k/ha-opus-greennet` nicht.

## 1. Tatsächlicher Datenfluss

```
MQTT (HA mqtt-Komponente)
 │
 ├─ stream/devices/#  ─► _handle_device_property_message
 │     _set_nested_property(_device_data[dev], path, payload)
 │     ├─ bekanntes Gerät + states|transmitModes|configuration/parameters/<n>/…
 │     │     └─► _buffer_device_stream_property ──(0,02 s)──► _finalize_device_stream
 │     ├─ bekanntes Gerät + states/<key> (KNOWN_STATE_KEYS, keine Button-Keys)
 │     │     └─► _apply_known_device_state_property ─► update_from_telegram ─► Dispatch
 │     └─ sonst: _pending_devices + Discovery-Timer (2 s, bei jeder Nachricht neu)
 │                                  └─► _finalize_discovery ─► _create_device_from_data
 │
 ├─ stream/device/#   ─► _handle_device_stream_message
 │     ├─ batteryLevel | dbm (bekanntes Gerät) ─► _apply_scalar_telemetry ─► Dispatch (nur bei Änderung)
 │     └─ sonst ─► _buffer_device_stream_property ──(0,02 s)──► _finalize_device_stream
 │           _merge_device_data(_device_data) → indizierte Deltas mit Cache-Kontext
 │           → _device_state_functions / _configuration_state_functions
 │           → _record_confirmed_feedback → update_from_telegram → Dispatch
 │
 ├─ stream/telegram/# ─► _handle_telegram_property_message (retained: verworfen)
 │     JSON komplett: sofort _finalize_telegram
 │     flach: _telegram_data + Timer (0,15 s); Flush bei Richtungswechsel/Pfadwiederholung
 │     _finalize_telegram: from/to-Auswahl → vollständige Funktionen
 │       ├─ from: _record_confirmed_feedback → update_from_telegram → Dispatch
 │       └─ to (kein lokaler ACK-Waiter): _start_cover_commands → update_from_telegram
 │              → Dispatch → _schedule_reconciliation_queries
 │
 ├─ getAnswer/devices/# ─► _handle_get_answer_devices (retained: verworfen)
 │     _device_data[dev] = Objekt, _pending_devices, Discovery-Timer (2 s)
 │
 ├─ putAnswer/devices/+/state ─► MQTTRequestManager.async_route (ACK-Future)
 │     + _handle_put_answer_state: last_command_error setzen/löschen → Dispatch
 │
 └─ getAnswer/config/system/uptime|info, bridge/status ─► Gateway-Zustand / Verfügbarkeit

Domain-State: coordinator.devices[dev] : EnOceanDevice
              └─ channels[n] : EnOceanChannel (state_revision, Felder, transiente Button-Felder)

Dispatcher:
  SIGNAL_DEVICE_DISCOVERED_<EAG>          → Plattform-Callbacks erzeugen Entities
  SIGNAL_DEVICE_STATE_UPDATE_<EAG>_<DEV>  → OpusGreenNetEntity._handle_state_update
                                             (self._device = device; async_write_ha_state)
                                           → OpusGreenNetEvent._handle_state_update (Event)
  SIGNAL_AVAILABILITY_UPDATE_<EAG>        → nur async_write_ha_state
  SIGNAL_DEVICE_REMOVED_<EAG>             → sensor/binary_sensor/lock vergessen Reservierungen

Entity: Properties lesen live aus self._device.channels[...]; Befehle über
coordinator.async_* → async_send_command → MQTTRequestManager (globale Queue).
```

Rückweg Befehl: `Entity.async_*` → Snapshot `(channel, state_revision)` →
`coordinator.async_turn_on/…` → `_with_channel_if_needed` → `async_send_command`
(Verfügbarkeitsprüfung, `_command_waiters++`) → `MQTTRequestManager.async_request`
(Lock je Antwort-Topic → Route bzw. temporäres Abo → globale `PriorityLock` →
`before_publish` = `snapshot_feedback` → Publish → ACK-Timeout 10 s) → unbestätigte
Felder → `_schedule_reconciliation_queries` → Entity setzt optimistisch, falls
`state_revision` unverändert.

## 2. Domain-State-Mutationsstellen

Domain-State bedeutet hier `coordinator.devices` sowie die Felder von
`EnOceanDevice` und `EnOceanChannel`.

| # | Ort | Mutation |
|---|-----|----------|
| M1 | `EnOceanDevice.update_from_telegram` | setzt `last_button*` aller Kanäle auf `None`; je Funktion `state_revision += 1` und das Feld; `last_seen`, `dbm` |
| M2 | `EnOceanDevice._update_numeric_field` | `setattr(channel, attr, …)` (dynamisch) |
| M3 | `EnOceanDevice.get_or_create_channel` | legt `channels[n]` an |
| M4 | `coordinator._apply_scalar_telemetry` | `device.dbm` direkt (**ohne** M1); Batterie über M1; `_device_data`-Cache |
| M5 | `coordinator._finalize_device_stream` | `last_command_error = None` bei bestätigtem Feedback; M1; `_mark_and_log_dispatch` |
| M6 | `coordinator._apply_known_device_state_property` | `last_command_error`; M1; Metadaten |
| M7 | `coordinator._handle_put_answer_state` | `last_command_error` setzen oder löschen |
| M8 | `coordinator._start_cover_movement` | `channel.movement`, `state_revision += 1` |
| M9 | `coordinator._finish_cover_movement` | `channel.position = target` (angenommen), `state_revision += 1` |
| M10 | `coordinator._clear_cover_movement` | `channel.movement = None` |
| M11 | `coordinator._create_device_from_data` | **ersetzt** `devices[dev]` durch ein neues Objekt; übernimmt `channels` (Identität bleibt), M1 mit leerer Liste (Button-Reset), `profile`, `last_update_*`, `last_command_error`; bedingt `_apply_initial_state`; `rotationTime`/`channel` |
| M12 | `coordinator._apply_initial_state` | M1, `last_update_source = "discovery"` |
| M13 | `coordinator._finalize_telegram` | Platzhaltergerät in `devices`; M1; `last_command_error = None` bei operativem Zustand |
| M14 | `coordinator._mark_and_log_dispatch` | `last_update_source`, `last_update_*_monotonic` |
| M15 | `coordinator.async_get_device_profile` | `device.profile` |
| M16 | `coordinator.async_forget_device` | `devices.pop` plus alle Caches und Timer des Geräts |
| M17 | `light.py` `async_turn_on/off` | optimistisch nach ACK: `is_on`, `brightness` (ohne `state_revision++`) |
| M18 | `switch.py` `async_turn_on/off` | optimistisch nach ACK: `is_on` |
| M19 | `cover.py` open/close/set_position/set_tilt | optimistisch nach ACK: `position`, `angle` |
| M20 | `OpusGreenNetEntity._handle_state_update` | Referenzwechsel `self._device = device` (Entity-State, nicht Domain) |

Climate und Lock schreiben keinen optimistischen Zustand. Die Entity-Mutationen
M17–M19 laufen nur, wenn `_channel_state_matches(snapshot)` gilt.

## 3. Ownership von Tasks, Timern, Subscriptions, Futures und Locks

| Ressource | Owner | Anlage | Freigabe |
|-----------|-------|--------|----------|
| Hintergrund-Tasks (`_refresh_task`, Abgleichs-Tasks) | `coordinator._tasks` via `_create_task` | Refresh, Abgleich | `_cancel_background_work` (cancel), `async_unload` (cancel + gather) |
| Binary-Sensor-Removal-Tasks | Config Entry (`entry.async_create_background_task`) | `async_removing` | HA beim Entry-Unload |
| Discovery-Timer (2 s) | `coordinator._discovery_timer` | Discovery-Nachrichten | nach Ablauf `None`; `async_unload`; **nicht** in `_cancel_background_work` |
| Telegramm-Timer (0,15 s) | `_pending_telegrams[dev]` | flache Telegramme | Finalisierung, Forget, Disconnect, Unload |
| Delta-Timer (0,02 s) | `_pending_device_streams[dev]` | Deltas | Finalisierung, Forget, Disconnect, Unload |
| Abgleich-Timer | `_pending_reconciliation_queries[(dev, ch)]` (Liste) | `_schedule_reconciliation_queries` | `_cancel_reconciliation_queries` (siehe Abschnitt 4) |
| Cover-Bewegungs-Timer | `CoverMovement.cancel` in `_cover_movements` | `_start_cover_movement` | `_clear_cover_movement` |
| Health-Intervall (60 s) | `coordinator._subscriptions` | `async_setup` | `async_unload` |
| MQTT-Abos (Control, Stream, Connection-Status) | `coordinator._subscriptions` + Topic-Listen | Setup, Refresh | `async_unload`; Disconnect leert nur die Routen, nicht die Abos (HA abonniert neu) |
| Temporäre Antwort-Abos und `on_subscribe_done` | `MQTTRequestManager._cleanups` via `_own_cleanup` | `_async_request_locked` | `finally` des Requests, `async_cancel_pending` |
| Wartende SUBACK-Callbacks | lokal in `async_wait_for_subscriptions` | | `finally` |
| Request-Futures (`subscribed`, `response`) | `MQTTRequestManager._waiters` | je Request | Fehler über `async_cancel_pending`/`async_cancel_device`; `finally` entfernt und ruft `exception()` ab |
| Antwort-Routen | `MQTTRequestManager._routes` / `_route_patterns` | Request bzw. Setup | `finally`; Disconnect und Unload leeren die Patterns |
| Lock je Antwort-Topic | `MQTTRequestManager._locks` | `setdefault` | **nie entfernt** (wächst mit Anzahl Geräte × Request-Art) |
| Globaler Send-Lock | `MQTTRequestManager._send_lock` | | `release` im `finally` |
| Generationen | `_generation`, `_device_generations` | | invalidieren veraltete Requests |
| Dispatcher-Verbindungen | Entity (`async_on_remove`), Plattform (`entry.async_on_unload`) | | HA |

## 4. Abgleichs-Checks nach 5 und 20 Sekunden

- **Implementierung:** `OUTBOUND_STATE_RECONCILIATION_DELAYS = (5, 20)` in
  `coordinator.py`. `_schedule_reconciliation_queries(dev, ch, fields=…)` vereinigt
  noch offene Felder mit den neuen, bricht vorhandene Timer ab und legt je Delay
  einen `async_call_later` an. Bei einem erwarteten Feld `position` mit aktiver
  Cover-Schätzung gelten stattdessen `Restlaufzeit + (10.0, 20.0)`, wobei die
  Restlaufzeit die Gnadenfrist von 5 s enthält.
- **Trigger:** (a) `async_send_command` nach erfolgreicher ACK, nur für Felder ohne
  neues Feedback seit `snapshot_feedback` (Revision der Felder). (b) `_finalize_telegram`
  für ausgehende `to`-Telegramme ohne lokalen ACK-Waiter. Felder ergeben sich aus
  `_command_fields_by_channel`: `dimValue`, `position`, `angle`, `temperatureSetpoint`,
  `switch`, `heaterMode`; STOP erwartet `position` und, bei Tilt-Unterstützung, `angle`.
  Statusabfragen (`query`) erzeugen keine Felder und lösen daher **keine**
  Folgeabgleiche aus. Es gibt keine Kaskade.
- **Ausführung:** Der Callback überspringt, wenn `not available` gilt oder für
  `(dev, ch)` schon ein Abgleich läuft (`_reconciling`). Sonst startet er eine
  Task `async_query_device_status(background=True)` mit Priorität
  `PRIORITY_BACKGROUND`. `HomeAssistantError` wird nur geloggt. Kein Retry.
- **Cancellation:** vollständiges Feedback in `_record_confirmed_feedback`,
  abgelehnte PUT-Antwort (alle Kanäle des Geräts), STOP (Coordinator und `to`-Telegramm),
  Ersatz durch neuen Plan, Ersatz einer Cover-Bewegung (offene Felder bleiben erhalten),
  fehlgeschlagener Befehl mit gestarteter Bewegung, `async_forget_device`, Disconnect
  oder Bridge-down (`_cancel_background_work`), Unload.
- **Tests:** Die Literalwerte `[5, 20]` sind in `test_cover_stop.py:95`,
  `test_cover_movement.py:188/233` und `test_reconciliation_feedback.py:107/166/223/318/396`
  festgeschrieben. Die Anzahl prüft `test_coordinator_mqtt.py:105/131`
  (gegen die Konstante). Die Cover-Verschiebung prüft `test_cover_movement.py:429`.
- **Beobachtungen (keine Änderung in Phase 1):**
  - B1: Nach Ablauf beider Timer ohne Feedback bleiben `_pending_reconciliation_fields`
    und die Liste abgelaufener Handles stehen. Ein späterer Befehl auf demselben Kanal
    übernimmt die alten Felder in die erwartete Menge. Folge: Der spätere Plan
    wird erst durch Feedback zu allen Feldern vorzeitig beendet. Die Zahl der
    Termine je Befehl bleibt höchstens 2, also keine Erhöhung der Frequenz.
    Bewertung: Hypothese eines Wartbarkeitsproblems, kein belegter Fehler.
  - Eine Änderung der Zeitwerte oder der Frequenz ist ausgeschlossen (A1).

## 5. Event-Auslösung und alte `last_button`-Felder

- `last_button`/`last_button_action` werden nur in M1 gesetzt und zu Beginn jedes
  M1-Aufrufs für alle Kanäle zurückgesetzt. `OpusGreenNetEvent._handle_state_update`
  löst bei **jedem** `SIGNAL_DEVICE_STATE_UPDATE` ein Event aus, wenn Kanal 0
  beide Felder gesetzt hat.
- Vorhandener Schutz: retained Telegramme werden verworfen; Button-Keys aus
  `stream/devices` werden ignoriert; Rediscovery setzt die Felder zurück (M11);
  Availability-Updates rufen nur `async_write_ha_state` auf
  (`test_availability_changes_do_not_replay_last_rocker_event`).
- Dispatch-Pfade **ohne** vorherigen M1-Reset:
  1. `_apply_scalar_telemetry` mit `dbm` (M4). **Befund F1, Codepfad belegt.**
  2. `_dispatch_cover_estimate`: nur Cover-Geräte, für Wippen nicht erreichbar.
  3. `_handle_put_answer_state`: nur nach Befehlen, für Wippen kein Befehlspfad vorhanden.
- **Reproduktion F1** (Skript außerhalb des Repositorys, nicht committet, gegen
  Basis `3e52fce`): Wippe `F6-02-01`, flaches `from`-Telegramm `buttonA0`/`pressed`,
  danach zwei Live-Nachrichten `stream/device/R1/dbm` (`-61`, `-62`). Alle drei
  Dispatches tragen `('buttonA0', 'pressed')`. Das Event-Entity würde
  `buttonA0_pressed` also dreimal auslösen.
- **Status:** Codepfad und Unit-Reproduktion belegt. Ob das Gateway für Wippen
  `stream/device/<DEV>/dbm` live publiziert, ist **unbelegt** (keine
  Hardwareverifikation). Kein Test deckt den Fall ab. Die Korrektur ist für
  Phase 2 vorgeschlagen und nur mit ausdrücklicher Freigabe als
  Fehlerkorrektur vorgesehen.

## 6. Typisierung und dynamische Pfade

- Alle Module nutzen `from __future__ import annotations` (Ausnahme: `lock.py`,
  `binary_sensor_descriptions.py`). Ein `py.typed` gibt es nicht, ebenso keine
  mypy-Konfiguration und kein Typprüfungs-Gate in CI.
- `mypy 2.4.0` informativ (lokal, nicht als Projektabhängigkeit): Standardmodus
  **27 Fehler**, `--strict` **72 Fehler in 15 Dateien** (davon 36 in `coordinator.py`).
  Wichtigste Klassen: Payload-Typ `str | bytes | bytearray` gegen `decode_response(str | bytes)`;
  `int | None` gegen `int` (`default_channel_id`, `heat_area_type`); `float` in
  `int`-Variablen (Cover-Fortschritt, `delays`); `dict` ohne `append` in
  `_set_nested_property`; `"None" not callable` (`_pending_telegrams`);
  `entity_id: str | None` an `log_entity_state_write`; `_attr_supported_features = 0`
  in `lock.py`; `voluptuous.Schema` gegen `probatio.Schema` (HA 2026.10-Stubs).
- `Any`: 70 Vorkommen (`coordinator.py` 23, `enocean_device.py` 12, `diagnostics.py` 9 …).
  Unparametrisierte Typen: `Callable` (6×), `dict`/`list` als Parameter (9×).
- `getattr`: `msg.retain`/`msg.topic` (8×, Duck-Typing für Test-Namespaces);
  Entity-Attributzugriffe über Strings in `sensor.py` (2×), `binary_sensor.py` (1×),
  `binary_sensor_descriptions.py` (2×); `entity.hass` (2×).
  `setattr`: `_update_numeric_field` (2×).
- `# noqa: SLF001` einmal (`queue_depth`).

## 7. Tests, Coverage und Qualitätsbefehle (Baseline)

Umgebung: Python 3.14.3, venv außerhalb des Repositorys (`/home/user/venv-opus`),
`homeassistant==2026.10.1` (zum Ausführungszeitpunkt die neueste stabile Version
auf PyPI, wie in CI ermittelt), `paho-mqtt==2.1.0`, `pytest==9.1.1`,
`pytest-asyncio==1.4.0`, `pytest-cov==7.1.0`, `ruff==0.16.3`, informativ `mypy==2.4.0`.

| Befehl (im Repository-Root) | Ergebnis |
|-----------------------------|----------|
| `pip install -r requirements_test.txt` | erfolgreich |
| `python -m pip check` | „No broken requirements found.“ (rc 0) |
| `ruff check .` | „All checks passed!“ (rc 0) |
| `ruff format --check .` | „48 files already formatted“ (rc 0) |
| `pytest -v --cov --cov-report=term-missing -p no:cacheprovider` | **865 passed**, 0 failed, 1 Warnung (aiohttp-Deprecation in HA), 115,5 s, rc 0 |
| Coverage gesamt | **91,76 %** (Schwelle 75 %) |
| `mypy custom_components/opus_greennet --python-version 3.14` | rc 1, 27 Fehler (informativ, kein Gate) |
| `mypy custom_components/opus_greennet --strict` | rc 1, 72 Fehler (informativ, kein Gate) |
| `hassfest` | **nicht ausgeführt**, läuft nur als GitHub Action |

`-p no:cacheprovider` verhindert lediglich ein `.pytest_cache` im Arbeitsbaum.

Coverage je Modul: `__init__.py` 72 %, `light.py` 85 %, `diagnostics.py` 89 %,
`coordinator.py` 90 %, `mqtt_transport.py` 91 %, `cover.py` 94 %, `config_flow.py` 95 %,
`binary_sensor.py` 95 %, `lock.py` 95 %, `enocean_device.py` 97 %, `entity.py` 98 %,
`sensor.py` 98 %, `climate.py` 99 %, `binary_sensor_descriptions.py`/`const.py`/
`event.py`/`switch.py` 100 %.

Tests je Datei (bestanden): enocean_device 245, coordinator_parsing 65, entities 65,
coordinator_transport 53, gateway_payload_compatibility 53, coordinator_mqtt 51,
reconciliation_feedback 51, cover_movement 45, coordinator_helpers 42,
binary_sensor_descriptions 35, detector_support 27, ha_lifecycle 17,
binary_sensor_discovery 13, retained_device_answers 13, beta_entity_migration 12,
init 12, device_removal 11, event_entity 11, scalar_telemetry 11,
release_transport_safety 9, window_handles 9, cover_stop 6, config_flow 5, diagnostics 4.

Alle Tests sind Unit- bzw. HA-Integrationstests mit simulierter MQTT-Grenze
(`tests/ha_helpers.MQTTTransport`). Sie sind **keine** Hardwareverifikation.

## 8. Ausschlussprüfung A1–A5

Diff dieser Phase: ausschließlich neue Dateien unter `docs/quality/`.

| ID | Ergebnis |
|----|----------|
| A1 | eingehalten: keine Poller, Intervalle oder Termine geändert; keine Roadmap-Aufgabe dazu |
| A2 | eingehalten: Uptime-, Health- und Availability-Code unverändert; keine Nutzung der Uptime als Zustandsbeleg geplant |
| A3 | eingehalten: keine Quarantäne, Drain-Phase, Sperre oder Abkühlzeit; Timeouts unverändert |
| A4 | eingehalten: bestehende Retain-/Freshness-Logik nur dokumentiert (`protocol-contract.md` §6), nicht verändert; F1 betrifft transiente Event-Felder und ist keine Freshness-Härtung |
| A5 | eingehalten: Entity-Inventar nur dokumentiert (`protocol-contract.md` §8); keine Exposure-Policy geplant |

## 9. Kompatibilität und Restrisiken

- Kompatibilität: keine Laufzeitänderung, daher keine Auswirkung auf Unique IDs,
  Entity IDs, Topics, Services oder Geräte.
- R1: Die CI testet gegen die jeweils neueste stabile HA-Version. Die Baseline gilt
  für HA 2026.10.1 und kann mit späteren HA-Releases abweichen.
- R2: `hassfest` wurde lokal nicht ausgeführt.
- R3: Alle Protokollaussagen sind aus Code, Tests und Herstellerreferenz abgeleitet,
  nicht aus Hardwarebeobachtung (siehe `protocol-contract.md` §11).
- R4: F1 ist im Code belegt, auf Hardware unbelegt; B1 ist eine Hypothese.
- R5: `_locks` im Request-Manager wird nie bereinigt. Das Wachstum ist durch die
  Anzahl der Geräte begrenzt, das Risiko gering.

## 10. Prüfung der Folgephasen am realen Code

Siehe `implementation-plan.md`, Abschnitte „Phasenübersicht“ und „Bereits gelöste
Punkte“. Jede vorgeschlagene Phase nennt konkrete Zielmodule. Keine Phase
enthält Aufgaben zu A1–A5.

## 11. Umsetzungshinweise für Phase 2

- Basis: HEAD von `feat/opus-q01-baseline` (im Chat ausgegeben), nicht `main`.
- Vor Beginn: Branchname aus dem Phasen-Prompt prüfen, `git status` muss sauber sein.
- Befolgt Phase 2 den Vorschlag „Event-Vertrag“: zuerst einen fehlschlagenden
  Regressionstest für F1 schreiben (Wippe, Telegramm, dann `stream/device/<DEV>/dbm`,
  Event höchstens einmal). Dann die minimale Korrektur, z. B. transiente Felder
  vor dem Dispatch im Skalarpfad zurücksetzen. `state_revision` und die
  Dispatch-Bedingung bei unverändertem Wert bleiben unberührt. Retain-Logik nicht
  anfassen (A4).
- Reproduktionsskript aus Phase 1 (nicht im Repository): Wippe `F6-02-01`,
  `_handle_telegram_property_message` mit `from/functions/0/key=buttonA0` und
  `from/functions/0/value=pressed`, `_finalize_telegram`, danach
  `_handle_device_stream_message` mit `stream/device/R1/dbm`; `async_call_later` und
  `async_dispatcher_send` gepatcht.
- Qualitätsbefehle wie in Abschnitt 7; `pytest` dauert etwa 2 Minuten.
