# Qualitätsreihe OPUS GreenNet – Implementierungsplan

Dieses Dokument verankert die sequenzielle Qualitätsreihe für
`fubu2k/ha-opus-greennet` dauerhaft im Repository. Es wird in jeder Phase
fortgeschrieben. Details je Phase stehen in `docs/quality/phases/phase-NN.md`,
der Protokollvertrag in `docs/quality/protocol-contract.md`.

## Ziel

Codequalität, Wartbarkeit, Testbarkeit, Typisierung und Dokumentation der
bestehenden OPUS-/EnOcean-MQTT-Integration verbessern. Das funktionale
Verhalten bleibt erhalten, soweit eine Phase nicht ausdrücklich eine
nachgewiesene Fehlerkorrektur erlaubt.

## Repository-Ziel

- Ziel-Repository: `fubu2k/ha-opus-greennet` (Remote `origin`, Standard-Branch `main`).
- Verweise auf `kegelmeier/ha-opus-greennet` in `CLAUDE.md`, `README.md` und
  `manifest.json` sind historische Upstream-Verweise. Sie ändern das Ziel nicht
  und werden in dieser Reihe nicht umgeschrieben (Dokumentations-URLs und
  `codeowners` gehören zu den Metadaten, nicht zum Scope).

## Verbindliche Ausschlüsse (A1–A5)

Diese Themen werden weder als Nebenarbeit noch als Voraussetzung oder als
spätere Phase umgesetzt. Erfordert ein Ziel zwingend eine ausgeschlossene
Änderung, wird das dokumentiert und die Arbeit angehalten.

| ID | Ausschluss | Eingefrorene Ist-Werte (Basis `3e52fce`) |
|----|------------|-------------------------------------------|
| A1 | Kein häufigeres Statuspolling: keine neuen Poller, keine kürzeren Intervalle, keine zusätzlichen Abgleichstermine | `GATEWAY_HEALTH_INTERVAL = 60 s`, `RECONNECT_RESYNC_DEBOUNCE_SECONDS = 60`, `OUTBOUND_STATE_RECONCILIATION_DELAYS = (5, 20)`, `COVER_RECONCILIATION_DELAYS = (10.0, 20.0)` nach Bewegungsende, `COVER_MOVEMENT_GRACE_SECONDS = 5.0` |
| A2 | Keine Änderung der Uptime-, Health- oder Availability-Semantik; Bridge-Uptime wird nicht zur Bestätigung von Gerätezuständen genutzt | `available = not _unloaded and _gateway_available and _bridge_connected is not False and mqtt.is_connected()`; ein Befehls-Timeout ändert die Verfügbarkeit nicht |
| A3 | Keine Topic-Quarantäne, Drain-Phase, Topic-Sperre oder zusätzliche Abkühlzeit nach Timeout | `REQUEST_TIMEOUT = 10 s`, `SUBSCRIPTION_TIMEOUT = 30 s`, `SETUP_REQUEST_TIMEOUT = 30 s`, `SETUP_SUBSCRIPTION_TIMEOUT = 180 s`, `MIN_SEND_INTERVAL = 0.0` |
| A4 | Keine neue Replay-/Freshness-Härtung (Timestamp-/Sequenzbewertung, Replay-Generationen, Priorisierung von Live- gegenüber retained Daten); bestehende Logik wird weder entfernt noch umgebaut | siehe Liste „Bestehende Retain-/Freshness-Logik“ in `phase-01.md` |
| A5 | Keine Opt-in-/Exposure-Policies gegen Entity-Bloat; keine Änderung an Entity-Anzahl, Auswahloptionen, Aktivierungsdefaults oder Registry-Konfiguration | Signalstärke-Sensor standardmäßig deaktiviert, alle übrigen aktiviert; Entity-Inventar siehe `protocol-contract.md` |

## Repository-Regeln (Kurzfassung)

- Keine direkten Commits auf `main`, keine Versionsänderung, kein `CHANGELOG.md`-Update.
- Kein Release, kein Merge, kein automatischer PR, kein force-push.
- Keine echten Geräteaktionen oder Konfigurationswrites an Hardware.
- Unique IDs, Entity IDs, Topic-Pfade, öffentliche Services und Gerätezuordnungen bleiben unverändert.
- OPUS-Wire-Werte, Kanal null, `notAvailable`, Cover-Inversion, STOP-Payload und
  proprietäre HeatArea-Profile bleiben erhalten (siehe `protocol-contract.md`).
- Keine automatischen Write-Retries nach Timeout.
- Keine pauschale Umstellung auf `DataUpdateCoordinator`.
- Keine zusätzlichen Abhängigkeiten ohne konkreten Bedarf.

## Branch-Kette

Phase 1 basiert auf dem zu Beginn geprüften `main`. Jede Folgephase basiert auf
dem abgeschlossenen HEAD des Vorgängerbranches. Kein Rebase, kein Squash, kein
Löschen vorheriger Branches. Existiert ein Branch bereits, wird er mit dieser
Dokumentation abgeglichen und nicht überschrieben.

| Phase | Branch | Vorgänger | Basis-SHA | Status |
|-------|--------|-----------|-----------|--------|
| 1 | `feat/opus-q01-baseline` | `main` | `3e52fce31e7a2d503eef4820f4c706ebcfc751ab` | abgeschlossen (lokal committet) |
| 2 | `feat/opus-q02-…` (Name folgt mit dem Phasen-Prompt) | `feat/opus-q01-baseline` | HEAD von Phase 1 (im Chat ausgegeben) | offen |
| 3 | `feat/opus-q03-…` | Phase 2 | HEAD von Phase 2 | offen |
| 4 | `feat/opus-q04-…` | Phase 3 | HEAD von Phase 3 | offen |
| 5 | `feat/opus-q05-…` | Phase 4 | HEAD von Phase 4 | offen |
| 6 | `feat/opus-q06-…` | Phase 5 | HEAD von Phase 5 | offen |
| 7 | `feat/opus-q07-…` | Phase 6 | HEAD von Phase 6 | offen |

Die HEAD-SHA einer Phase steht nicht im eigenen Commit. Sie wird im Chat
ausgegeben und in der Dokumentation der Folgephase als Basis-SHA eingetragen.

## Qualitätsbefehle (CI-konform)

Aus `.github/workflows/tests.yml` übernommen:

```bash
python -m pip install --upgrade -r requirements_test.txt "homeassistant==<latest stable>"
python -m pip check
ruff check .
ruff format --check .
pytest -v --cov --cov-report=term-missing
```

Die Coverage-Schwelle ist `fail_under = 75` (`pyproject.toml`). `hassfest` läuft
nur in CI (`home-assistant/actions/hassfest@master`) und ist lokal nicht
ausführbar. `mypy` ist **kein** Projekt-Gate. In Phase 1 lief es nur
informativ in einer lokalen Umgebung außerhalb des Repositorys.

## Phasenübersicht (Vorschlag aus Phase 1, an realem Code geprüft)

Die Inhalte der Phasen 2–7 sind Vorschläge auf Basis der Analyse in Phase 1.
Maßgeblich ist jeweils der ausdrückliche Prompt zur Phase. Weicht er ab, gilt
der Prompt, und dieser Plan wird in jener Phase angepasst.

### Phase 2 – Event-Vertrag und belegte Event-Korrektur

- Befund F1 (Codepfad belegt, Hardware-Auftreten unbelegt): `_apply_scalar_telemetry`
  setzt bei `dbm` `device.dbm` direkt, ohne `update_from_telegram` aufzurufen,
  und dispatcht danach `SIGNAL_DEVICE_STATE_UPDATE`. Die transienten Felder
  `last_button`/`last_button_action` des vorherigen Wippentelegramms bleiben
  dabei gesetzt, und `OpusGreenNetEvent._handle_state_update` würde dasselbe
  Event erneut auslösen. Lokal mit einem Skript außerhalb des Repositorys
  reproduziert (siehe `phase-01.md`).
- Weitere Dispatch-Pfade ohne Rücksetzung: `_dispatch_cover_estimate` (nur Cover)
  und `_handle_put_answer_state` (nur bei Befehlen). Für Wippen sind sie nach
  heutigem Code nicht erreichbar, sollen aber im Vertrag festgehalten werden.
- Zielmodule: `event.py`, `coordinator.py` (`_apply_scalar_telemetry`, ggf.
  `_handle_put_answer_state`, `_dispatch_cover_estimate`), `enocean_device.py`
  (Reset-Helfer), Tests `tests/test_event_entity.py`, `tests/test_scalar_telemetry.py`.
- Erfordert eine ausdrückliche Freigabe als Fehlerkorrektur im Phasen-Prompt.
  Abgrenzung zu A4: Es geht um transiente Event-Felder, nicht um Freshness
  oder Retain-Bewertung.

### Phase 3 – Typisierung ohne Verhaltensänderung

- Ausgangslage: `mypy` (Standard) meldet 27 Fehler, `mypy --strict` 72 Fehler in 15 Dateien.
- Zielmodule: `mqtt_transport.py` (Payload-Typen `str | bytes | bytearray`,
  Lambda-Inferenz), `enocean_device.py` (`heat_area_type`, `default_channel_id: int | None`),
  `coordinator.py` (unparametrisierte `Callable`, `dict`, `list`; `CALLBACK_TYPE`
  für Timer-Handles; `started`-Annotation; Delay-Tupeltyp), `entity.py`/`event.py`
  (`entity_name: str | None`), `lock.py` (`LockEntityFeature(0)` statt `0`;
  Wert bleibt gleich), `binary_sensor.py`.
- Optionale mypy-Konfiguration in `pyproject.toml`; ob `mypy` eine
  Test-Abhängigkeit oder ein CI-Schritt wird, ist im Phasen-Prompt zu
  entscheiden (Regel „keine zusätzlichen Abhängigkeiten ohne konkreten Bedarf“).
- Bekannte Grenze: Die Abweichung `voluptuous.Schema` gegenüber `probatio.Schema`
  in HA 2026.10 (`__init__.py`, `config_flow.py`) ist ein Typkonflikt der
  HA-Stubs. Sie wird nicht durch Laufzeitänderungen gelöst.
- Die dynamischen `getattr`/`setattr`-Pfade werden nur typisiert oder durch
  explizite Zugriffe ersetzt, wenn Verhalten und Unique IDs gleich bleiben.

### Phase 4 – Protokoll-/Parsing-Modul extrahieren

- Reine Funktionen aus `coordinator.py` in ein neues Modul (z. B. `protocol.py`)
  verschieben: Topic-Regexe, `_set_nested_property`, `_parse_value`,
  `_merge_device_data`, `_device_state_functions`, `_indexed_changed_functions`,
  `_configuration_state_functions`, `_rwm_transmit_modes`,
  `_channel_from_functions`, `_confirmed_fields_by_channel`,
  `_command_fields_by_channel`, Payload-Builder für `state.functions`.
- Kompatibilitäts-Wrapper als Staticmethods, damit bestehende Tests unverändert bleiben.
- Golden-/Vertragstests gegen `protocol-contract.md` (Kanal null, STOP, Inversion,
  HeatArea-Modi, `notAvailable`).
- Zielmodule: `coordinator.py`, neues Protokollmodul, `const.py` (nur lesend),
  neue Tests `tests/test_protocol_contract.py`.

### Phase 5 – Ownership und Lebenszyklus im Coordinator strukturieren

- Cover-Bewegungsschätzung (`CoverMovement`, `_start_/_finish_/_clear_cover_movement`)
  und den Abgleichsplaner (`_schedule_/_cancel_reconciliation_queries`,
  `_pending_reconciliation_*`, `_reconciling`) in klar abgegrenzte Helfer mit
  eindeutiger Ownership überführen. Zeitwerte und Häufigkeit bleiben
  unverändert (A1). Die Literal-Tests `[5, 20]` bleiben unverändert.
- Doppelte Aufräumlogik zwischen `_cancel_background_work` und `async_unload` konsolidieren.
- Lifecycle-Regressionstests: keine verwaisten Timer, Tasks oder Futures nach
  Disconnect, Bridge-down, Forget und Unload.
- Zielmodule: `coordinator.py`, ggf. neue Module `cover_movement.py` und
  `reconciliation.py`, Tests `test_cover_movement.py`, `test_reconciliation_feedback.py`,
  `test_release_transport_safety.py`.

### Phase 6 – Plattform- und Entity-Konsolidierung

- Wiederholtes Discovery-Listener-Muster (`light`, `switch`, `cover`, `climate`,
  `event`) und optimistische Nach-ACK-Updates in gemeinsame Helfer in `entity.py`
  überführen.
- Unique IDs, Entity-Anzahl, Übersetzungsschlüssel und Aktivierungsdefaults
  unverändert (A5). Inventar-Tests sichern das ab.
- Testlücken schließen: `__init__.py` (72 % – Service-Handler
  `get_device_parameters`, `reload_entry`, Fehlerpfade von `_loaded_entry`),
  `light.py` (85 %).
- Zielmodule: `entity.py`, `light.py`, `switch.py`, `cover.py`, `climate.py`,
  `event.py`, `sensor.py`, `binary_sensor.py`, `__init__.py` (nur Tests oder
  totes `result is None`-Prüfen nach Abstimmung).

### Phase 7 – Dokumentation und Qualitäts-Gates

- Abschnitt „Development“ in `README.md` um Qualitätsbefehle ergänzen; Docstrings
  und Modulbeschreibungen vervollständigen; Abschlussbericht der Reihe.
- Optional, nur nach ausdrücklicher Entscheidung: CI-Schritt für `mypy`.
- Zielmodule: `README.md`, `docs/quality/*`, ggf. `.github/workflows/tests.yml`, `pyproject.toml`.

## Bereits gelöste Punkte (nicht erneut implementieren)

Am Code der Basis `3e52fce` und an vorhandenen Tests bestätigt:

- Retained `getAnswer/devices`-Antworten erzeugen weder Discovery noch Zustand
  (`_handle_get_answer_devices`, `tests/test_retained_device_answers.py`).
- Retained Telegramme lösen weder Events noch Zustände aus (`_handle_telegram_property_message`).
- Button-Keys aus `stream/devices` gelten nicht als neues Ereignis (`_apply_known_device_state_property`).
- Verfügbarkeitswechsel lösen kein altes Wippen-Event erneut aus
  (`test_availability_changes_do_not_replay_last_rocker_event`).
- Rediscovery setzt transiente Button-Felder zurück (`update_from_telegram({"functions": []})`).
- Befehls-Timeout ändert die Gateway-Verfügbarkeit nicht und wird nicht automatisch wiederholt.
- Globale Sendewarteschlange mit Priorität (Befehl > Abfrage > Hintergrund), genau ein Request gleichzeitig.
- STOP als eigene Funktion `{"key": "stop", "value": "true"}` mit Kanal-Selektor (`tests/test_cover_stop.py`).
- Feedback nur pro Kanal und Feld; Feedback eines früheren Befehls bestätigt keinen späteren
  (`tests/test_reconciliation_feedback.py`).
- Idempotente Binary-Sensor-Discovery und deklarative Beschreibungen
  (`tests/test_binary_sensor_discovery.py`, `tests/test_binary_sensor_descriptions.py`).
- Child-Removal ohne MQTT-Writes, mit Isolation der Requests (`tests/test_device_removal.py`).
