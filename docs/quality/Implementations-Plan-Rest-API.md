# OPUS GreenNet – Implementations-Plan REST-API

Revision 2, 2026-10-20 (Revision 1 ergänzt um die Erkenntnis zum Aktor-Nachlauf,
Abschnitt 3.3). **Nur Bestandsaufnahme und Plan.** Durch dieses
Dokument wurde nichts implementiert. Jede Phase braucht eine eigene Freigabe
des Maintainers (Plan `implementation-plan.md`, Abschnitte 4 und 5).

| Angabe | Wert |
|--------|------|
| Geprüfter Branch | `feature/rest-api-integration` (fubu2k/ha-opus-greennet) |
| Geprüfter Commit | `2010d2f` („docs: record phase 14 CI evidence“), identisch mit `origin/feature/rest-api-integration` |
| Hauptzweig | `main` = `39b4c66`; **enthält den REST-Stand nicht** (nicht gemergt) |
| Herkunft des REST-Codes | upstream `kegelmeier/ha-opus-greennet`, Branch `feat/rest-transport` (`20b34c9`), integriert in Phase 14 (`phases/phase-14.md`) |
| CI-Nachweis des geprüften Stands | Workflow „Tests“, Lauf `39384621424` auf `e04245f` (Code identisch mit `2010d2f`), grün |
| Verbindliche Regeln | Guardrails A1–A5 aus `implementation-plan.md` Abschnitt 2 gelten ab Entscheidung D3 (Abschnitt 0.1) **nur für den MQTT-Transport**; REST wird nach Best Practices und Home-Assistant-Vorgaben entwickelt |
| Ablageort | `docs/quality/` (das Repository hat kein Verzeichnis `/Quality`; die Qualitätsdokumente liegen dort) |

Kennzeichnung der Aussagen:
**[Code]** aus dem Quellcode am geprüften Commit gelesen,
**[Test]** durch einen vorhandenen Test abgedeckt,
**[Doku]** aus Repository-Dokumentation,
**[Praxis]** vom Maintainer berichtete Beobachtung (nicht unabhängig verifiziert),
**[HW-upstream]** Hardwarebeobachtung des Upstream-Projekts,
**[Upstream-Mitteilung]** schriftliche Auskunft aus dem Upstream-Projekt mit
Messwerten einer dortigen Installation (an den Maintainer weitergegeben, hier
nicht nachgemessen),
**[Annahme]** offene, noch zu prüfende Annahme,
**[Entwurf]** Entwurfsentscheidung dieses Plans.

## 0. Entscheidungen des Maintainers

| ID | Datum | Entscheidung | Folge |
|----|-------|--------------|-------|
| D1 | 2026-10-20 | REST wird als **zusätzlicher** Transport neben MQTT integriert; MQTT bleibt unverändert, bestehende Einträge bleiben auf MQTT. | Umgesetzt in Phase 14. |
| D2 | 2026-10-20 | **Die Geräteliste wird bei REST alle 30 Minuten vom Gateway gelesen.** Dieser neue Hintergrundverkehr wird für die REST-API benötigt (Batteriestand, Signalstärke und Fahrzeiten kommen nicht über den Stream). | Die in `implementation-plan.md` Abschnitt 3 als „requires maintainer review“ eingetragene Ergänzung ist damit **genehmigt**. Dokumentationsnachzug in Phase R0. |
| D3 | 2026-10-20 | **Die Guardrails A1–A5 gelten nur für den MQTT-Transport.** Der REST-Transport wird in allen weiteren Phasen (ab R1) nach Best Practices und den Vorgaben von Home Assistant neu entwickelt. | Gilt für alle folgenden Phasen; Regeln in Abschnitt 0.1. Die bisherigen, aus A1–A5 abgeleiteten Einschränkungen für REST in diesem Plan sind ersetzt. |

Zu D2: `GET /devices` liest das Gerätemodell des Gateways und löst nach der
Upstream-Beobachtung kein Funktelegramm aus **[HW-upstream]**. Das Intervall
von 30 min ist der aktuelle Stand. Die ursprüngliche Festlegung „fest, nach A1
nicht verkürzbar“ ist durch D3 aufgehoben; eine Änderung folgt Abschnitt 0.1.

### 0.1 Geltungsbereich der Guardrails ab D3 (verbindlich für alle weiteren Phasen)

**MQTT-Transport – Guardrails A1–A5 unverändert verbindlich.** Gilt für jeden
Eintrag ohne `transport: rest` und für allen Code, den der MQTT-Pfad ausführt:

- Keine Verhaltens-, Timing-, Wire- oder Verkehrsänderung. Keine neuen
  Abfragen, keine Retries, keine Cooldowns.
- Unique IDs, Entity-IDs, Gerätezuordnungen, Registry-Einstellungen und der
  Transport bestehender Einträge bleiben unverändert. Kein automatischer
  Transportwechsel.
- Retained-Schutz (Phase 10) unverändert.
- Ein Re-Export ist keine Verhaltensänderung. Jede darüber hinausgehende
  Änderung am MQTT-Pfad braucht die Freigabe des Maintainers.

**REST-Transport – Neuentwicklung nach Best Practices und Home-Assistant-Vorgaben.**
A1–A5 sind für REST kein Bestandsschutz mehr. Maßstab sind:

- die Home Assistant Integration Quality Scale (Ziel: alle anwendbaren Regeln
  erfüllt) und die Home-Assistant-Entwicklerdokumentation (u. a. Config Flow,
  Entity-Modell, Verfügbarkeit, `inject-websession`, Fehlerbehandlung,
  Diagnose, Reauth/Reconfigure, strikte Typisierung),
- anerkannte Praxis für HTTP-Clients und Streams (getrennte Fehlerklassen,
  Timeouts, begrenzter Backoff mit Jitter, sauberer Lebenszyklus),
- Tests zuerst; Coverage-Gates, ruff, strict mypy und Hassfest werden nicht
  abgesenkt.

Verhalten, Timing, Abfragen, Entity-Modell und Code-Struktur des
REST-Transports dürfen geändert werden, wenn die Phase das begründet. Jede
Änderung wird im Phasenbericht offengelegt (Abschnitt 20 von
`implementation-plan.md`): Funk- und Gateway-Verkehr vorher/nachher sowie
Auswirkungen auf bestehende REST-Einträge und Entity-IDs.

**Gemeinsamer Code.** Module, die auch der MQTT-Pfad nutzt
(`enocean_device.py`, `entity.py`, die Plattformen, `const.py`, `wire.py`,
`domain.py` sobald vorhanden), stehen für den MQTT-Pfad weiter unter A1–A5.
Eine REST-Änderung an diesen Modulen muss transportgebunden sein (eigener
Wert, eigener Codepfad oder `isinstance`-Prüfung) und ist durch die
unveränderten MQTT-Tests zu belegen. Beispiel: `COVER_MOVEMENT_GRACE_SECONDS`
liegt in `const.py` und gilt für beide Koordinatoren; eine andere Karenz für
REST braucht eine eigene REST-Konstante.

## 1. Zielbild und Prioritäten (aus dem Auftrag)

1. REST für Befehle und gezielte Abfragen.
2. HTTP-Stream als primärer Eingang für Zustände und Ereignisse.
3. MQTT als optionaler, sauber gekapselter Fallback.
4. Voll funktionsfähiger REST-only-Betrieb ohne eingerichtete
   Home-Assistant-MQTT-Integration.
5. Spätere Entfernung von MQTT ohne fachliche Änderungen an Gerätemodell und
   Entity-Plattformen.

Prioritäten bei Zielkonflikten: korrekte Zustände und unverfälschte
Ereignisse → Erhalt des getesteten Verhaltens und bestehender Installationen →
robuste Fehlerbehandlung und Lebenszyklus → schlanke Architektur → messbare
Performance. Kein neuer Abstraktionslayer ohne konkretes bestehendes Problem.

Die Home Assistant Integration Quality Scale ist Maßstab. Das Repository führt
eine **Selbsteinschätzung** einer Custom Integration (`quality_scale.yaml`,
`quality-scale-evidence.md`), keine offiziell vergebene Core-Einstufung.

## 2. Ist-Analyse

### 2.1 Datenflüsse am geprüften Commit

**REST-Eintrag (`transport: rest`)** – `rest_coordinator.OpusGreenNetRestCoordinator`:

| Fluss | Ablauf | Beleg |
|-------|--------|-------|
| Setup | `async_setup`: `GET /system/info` (EURID-Prüfung, `GatewayMismatchError`) → `GET /system/uptime` → `GET /devices` (autoritativ) → verfügbar → Timer 60 s (Uptime) und 30 min (Geräteliste) → Stream-Task | [Code] [Test] `test_rest_stream.py::test_setup_reads_identity_uptime_and_devices` |
| Stream | `api.OpusGatewayClient.async_stream` (`GET /devices/stream`, ohne Query-Parameter) → `JsonDocumentSplitter` → `_handle_document`: `devices` (autoritative Liste), `device` (Einzelgerät = Live-Rückmeldung), `telegram`; andere Typen werden ignoriert | [Code] [Test] `test_rest_stream.py::test_stream_document_types` |
| Befehl | Entity → `async_turn_on`/`async_set_cover_position`/… → `async_send_command` → `_async_radio_request` (eine Funkanfrage gleichzeitig, `PriorityLock` 0/10/20) → `PUT /devices/{id}/state`; Antwort = Quittung (`200` → `CommandDelivery.SENT`, `201` → `DEFERRED`) | [Code] [Test] `test_rest_commands.py`, `test_rest_requests.py` |
| Rückmeldung | `from`-Telegramm → `_record_confirmed_feedback` (Feldrevisionen, Abbruch ausstehender Statusprüfungen, Ende der Fahrschätzung bei gültiger Position) → `update_from_telegram` → Dispatcher | [Code] [Test] `test_rest_telegrams.py` |
| Fremdbefehl | `to`-Telegramm (HomeKit, Gateway-Logik) → nur Zielzustand optimistisch übernehmen → Statusprüfungen nach 5/20 s; eigenes Echo innerhalb 2 s nach `200` wird ignoriert | [Code] [Test] `test_rest_telegrams.py::test_homekit_command_is_applied_then_verified`, `test_rest_requests.py::test_own_echo_after_the_acknowledgement_is_ignored` |
| Tasterereignis | `from`-Telegramm mit `buttonA0`… → `EnOceanDevice.update_from_telegram` setzt das Ereignis → synchroner Dispatch → Event-Entity verbraucht → `clear_button_occurrence()`; Gerätelisten setzen nie Tasterfelder | [Code] [Test] `test_rest_telegrams.py::test_fast_press_release_keeps_both_events`, `test_rest_devices.py::test_new_rocker_from_list_has_no_button_occurrence`, `test_rest_stream.py::test_device_list_snapshot_does_not_replay_button_presses` |
| 30-min-Liste | `_async_refresh_device_list` → `_apply_device_list(authoritative=False)` → nur Konfiguration, Batterie, Metadaten | [Code] [Test] `test_rest_stream.py::test_periodic_device_list_refresh_updates_metadata_only` |

**MQTT-Eintrag (ohne `transport`)** – `coordinator.OpusGreenNetCoordinator`
mit `mqtt_transport.MQTTRequestManager`: unverändert seit Phase 13
(Retained-Schutz Phase 10, ACK 201 Phase 11, Late-Answer-Diagnose Phase 12)
**[Code] [Doku]** `protocol-contract.md` Abschnitte 1–11.

**Plattformen** greifen nur über `transport.OpusCoordinator`
(`OpusGreenNetCoordinator | OpusGreenNetRestCoordinator`), Dispatcher-Signale
und `EnOceanDevice`/`EnOceanChannel` zu **[Code]**.

### 2.2 Vorhandene Zustands-, Revisions-, Aktualitäts- und Deduplizierungsmechanismen

Diese Mechanismen werden **weiterverwendet**; der Plan führt keine parallelen
Ersatzmechanismen ein.

| Mechanismus | Ort | Zweck |
|-------------|-----|-------|
| `EnOceanChannel.state_revision` + `ChannelStateSnapshot` | `enocean_device.py` | Schätzung nach Quittung nur, wenn seit dem Senden keine neuere Information kam (Phase 2) |
| `_feedback_revisions` | beide Koordinatoren | Rückmeldung während der Quittung bestätigt den Befehl, ohne Zusatzabfrage |
| `_pending_reconciliation_fields`, `_reconcile_needed`, `SUPERSEDED` | beide Koordinatoren | höchstens 2 Statusprüfungen je Kanal, überholte Prüfungen werden vor dem Senden verworfen (Phase 4) |
| `CommandDelivery`, `deferred_commands` | `coordinator.py`, `rest_coordinator.py`, `enocean_device.py` | ACK 201: keine Annahme, keine Abfrage (Phase 11) |
| `_sent_commands` / `OWN_COMMAND_ECHO_SECONDS` | `rest_coordinator.py` | Echo eigener gesendeter Befehle ignorieren |
| `_command_waiters` | beide Koordinatoren | `to`-Telegramme während eigener Quittung ignorieren |
| `clear_button_occurrence()` | `enocean_device.py`, Aufrufer in beiden Koordinatoren | kein Wiederholen von Tasterereignissen |
| `_device_generations`, `_outage_generation` | `rest_coordinator.py` | Anfragen eines entfernten Geräts oder aus der Zeit vor einem Ausfall werden nicht gesendet |
| `_live_fields`, Replay-Guard | nur `coordinator.py` | Retained-Replays überschreiben keine Live-Felder (Phase 10) |
| Projektion `_dispatched_projection` | `entity.py` | keine redundanten State-Writes (Phase 4) |
| Lesewerte: ungültig → alter Wert, `notAvailable` → `None` | `EnOceanDevice._integer_reading`/`_float_reading` | partielle Updates löschen keine Felder; „unknown“-Positionen überschreiben die letzte Position nicht |

### 2.3 Kopplungen, die REST-only verhindern

| Nr. | Kopplung | Beleg | Wirkung |
|-----|----------|-------|---------|
| K1 | `manifest.json`: `"dependencies": ["mqtt"]` und `"mqtt": [...]` (Discovery) | [Code] | Home Assistant lädt für jeden Eintrag die MQTT-Komponente. Ob der Start ohne eingerichtete MQTT-Integration sauber funktioniert, ist **nicht getestet** [Annahme]. |
| K2 | `rest_coordinator.py` importiert `coordinator.py` (Signale, `CommandDelivery`, `CoverMovement`, Konstanten) und `mqtt_transport.request_error` | [Code] | Jeder REST-Import lädt `homeassistant.components.mqtt`. |
| K3 | `__init__.py`, `transport.py`, `entity.py`, alle Plattformen importieren aus `coordinator.py` | [Code] | Plattformen hängen formal am MQTT-Modul; beim Entfernen von MQTT müssten alle Plattformen geändert werden (verletzt Ziel 5). |
| K4 | `config_flow.py` importiert `homeassistant.components.mqtt`, `mqtt_transport` und `discovery` auf Modulebene | [Code] | Auch der REST-Pfad des Flows lädt MQTT. |
| K5 | `tests/conftest.py`: Fixture `hass` fügt immer `"mqtt"` zu `hass.config.components` hinzu | [Code] | Kein Test prüft einen REST-Eintrag ohne MQTT-Komponente. |
| K6 | `transport.OpusCoordinator` ist eine Union beider Klassen | [Code] | Entfernen von MQTT ändert den Typ aller Plattform-Signaturen. |
| K7 | Qualitätsregel `discovery` ist nur über MQTT erfüllt | [Code] [Doku] | Nach einer MQTT-Entfernung fiele die Regel auf `todo`, solange REST keine Discovery hat. |

### 2.4 Bereits sauber getrennt – erhalten

- `api.py` importiert nur Standardbibliothek, `aiohttp` und `wire.py`; **keine
  Home-Assistant-Importe** [Code]. Session wird injiziert und nie geschlossen;
  Basic-Auth-Header entsteht einmalig im Konstruktor und wird nicht geloggt;
  typisierte Ausnahmen `OpusAuthError`, `OpusConnectionError`,
  `OpusTimeoutError`, `OpusResponseError` [Code] [Test] `test_rest_api.py`.
- `wire.py` (JSON-Grenze, ohne HA) und `protocol.py` (reine Funktionen) [Code].
- `EnOceanDevice`/`EnOceanChannel` enthalten keine MQTT-Topics, HTTP-Objekte
  oder Entity-Objekte [Code].
- Keine automatische Wiederholung von Schreibbefehlen; Timeout ändert die
  Gateway-Verfügbarkeit nicht (A2, A3) [Code] [Test]
  `test_rest_commands.py::test_command_timeout_keeps_gateway_available`.
- Transportwahl pro Eintrag, Wechsel nur über „Neu konfigurieren“ (A5) [Code]
  [Test] `test_transport_selection.py`.

### 2.5 Abweichungen zwischen Code, Dokumentation, Tests und Nachweisen

| Nr. | Abweichung | Korrektur |
|-----|------------|-----------|
| A-1 | `implementation-plan.md` Abschnitt 3 und 15a sowie `phases/phase-14.md` führen die 30-min-Abfrage als „review pending“. | Seit D2 genehmigt → Phase R0. |
| A-2 | Die Auftragsliste nennt `GET /devices/stream?direction=from&delimited=newLine&output=singleLine&levelOfDetail=high`. Der Code ruft `GET /devices/stream` **ohne** Parameter auf; keine Repository-Quelle belegt diese Parameter. | Abschnitt 4: nur nach Hardwareprüfung übernehmen; `direction=from` nicht verwenden. |
| A-3 | Der Code nutzt zusätzlich `GET /system/uptime`, `GET /devices/{id}` und `GET /devices/{id}/parameters`, die in der Auftragsliste fehlen. | Belegt durch Code, Tests und `protocol-contract.md` 12.2; bleiben. |
| A-4 | `manifest.json` `version` ist auf allen Commits `0.7.0b2`; ein Praxistest lässt sich dadurch keinem Commit zuordnen. | Für Praxistests den installierten Commit festhalten (Phase R1). |
| A-5 | Ziel „Befehlsannahme ≠ beobachteter Zustand“: Der Code übernimmt bei Rollläden **ohne** bekannte Fahrzeit nach `200` das Ziel sofort als Position (`EnOceanDevice.apply_cover_position_estimate`) und **mit** Fahrzeit nach Fahrzeit + 5 s (`_finish_cover_movement`, Kommentar „Assumed, not physical feedback“). | Bestehendes, getestetes Verhalten (MQTT und REST). Keine stille Änderung; Herkunft sichtbar machen und Entscheidung einholen → Phase R6. |
| A-6 | README/Vertrag beschreiben REST-Tests als „simuliert“, der Maintainer hat inzwischen Praxistests durchgeführt [Praxis]. | Praxis-Baseline (Abschnitt 3) in `phase-14.md` und Vertrag 12.9 nachtragen → R0. |

### 2.6 Weitere Befunde

| Nr. | Befund | Beleg | Risiko |
|-----|--------|-------|--------|
| B-1 | `api._async_request` prüft den HTTP-Status nur auf 401/403 und auf leeren Body ≥ 300; bei vorhandenem JSON entscheidet allein `header.httpStatus`. Ein HTTP-500 mit JSON-Status 200 würde als Erfolg gelten. | [Code] | gering, aber widerspricht „HTTP-Status und JSON-Status getrennt behandeln“ |
| B-2 | `_async_run_stream`: Endet ein Stream, nachdem er das erste Dokument geliefert hat, wird **sofort** ohne Pause neu verbunden (`if connected: continue`); `failures` wird zurückgesetzt. Schließt das Gateway Verbindungen direkt nach dem Snapshot, entsteht eine enge Schleife mit vollständigem Gerätelisten-Download. Feste Pausen 1–60 s, **ohne Jitter**. | [Code] | mittel (Last auf Gateway und HA) |
| B-3 | `JsonDocumentSplitter` begrenzt nur über Chunks hinweg wachsende Reste (`_size`); ein vollständiges Dokument innerhalb eines Chunks wird nicht gegen `MAX_DOCUMENT_SIZE` geprüft. | [Code] | gering (aiohttp-Chunks sind klein) |
| B-4 | `_apply_device_object` erzeugt bei jeder Liste ein **neues** `EnOceanDevice` und kopiert Felder einzeln. Neue Felder können beim Kopieren vergessen werden (in Phase 14 für `deferred_commands`, `profile`, `last_update_finalized_monotonic` nachgezogen); ein während eines Befehls gehaltenes altes Objekt erhält `last_command_error` ins Leere. | [Code] | mittel (Wartbarkeit, seltene falsche Fehleranzeige) |
| B-5 | Die 30-min-Liste übernimmt `dbm` und `lastSeen` der Liste auch dann, wenn zwischen Anfrage und Antwort ein neueres Telegramm kam. | [Code] | gering (nur Metadaten) |
| B-6 | `PriorityLock` und die Prioritäten liegen in `api.py`, gehören aber zur Funkwarteschlange des Koordinators. | [Code] | Bibliotheksgrenze unscharf |
| B-7 | `gateway_to_ha_ms` = `time()` (HA-Uhr) minus Telegramm-Zeitstempel des Gateways (Gateway-Uhr, ms) beim Dispatch im Koordinator. Enthält den Uhrenversatz, misst **nicht** Aktor→Gateway und nicht den Entity-Write. | [Code] `rest_coordinator._mark_and_log_dispatch`, `protocol.telegram_timestamp` | Messgrenze (Abschnitt 3.2) |
| B-8 | Setup lädt die Geräteliste doppelt: `GET /devices` und sofort danach das erste Stream-Dokument. | [Code] | unnötiger Verkehr und CPU bei großen Installationen |

### 2.7 Testschutz der Praxis-Baseline

| Erwartung (Abschnitt 3.1) | Vorhandene Tests | Lücke |
|---------------------------|------------------|-------|
| R1 Verbindung nach Migration | `test_transport_selection.py::test_switching_an_mqtt_entry_to_rest_and_back_keeps_entities` | – |
| R2 Reload lädt Liste, Stream verbindet neu | `test_rest_stream.py::test_new_stream_resynchronises_state`, `::test_unload_stops_stream_and_timers` | Reload über `opus_greennet.reload_entry` mit REST nicht explizit |
| R3 Öffnungs-/Schließindikatoren | `test_rest_cover_movement.py::test_real_ha_cover_shows_motion_and_accepts_configuration_updates` | – |
| R4 Position entspricht Realität | `test_rest_devices.py::test_stream_resync_replaces_cover_state`, `test_rest_telegrams.py::test_from_telegram_updates_state_and_metadata` | Umrechnung roh→HA nur in MQTT-Entity-Tests explizit |
| R5 Zieländerung während der Fahrt | `test_rest_cover_movement.py::test_retarget_uses_elapsed_estimate_and_cancels_old_timer`, `::test_retarget_cancels_old_queries_before_ack` | – |
| R6 Stop → Positionsmeldung aktualisiert sofort | `test_rest_cover_stop.py`, `test_rest_cover_movement.py::test_numeric_feedback_ends_estimate_and_reconciliation` | – |
| R7 Lokale Wippen der Rollladenaktoren, danach Position | `test_rest_telegrams.py::test_integrated_rocker_telegrams_leave_actuator_state` | Sequenz „Wippe → `unknown` → Endlage“ fehlt |
| R8 F6-02-01: je Press/Release genau ein Event-Update | `test_rest_telegrams.py::test_rocker_press_and_release_fire_events`, `::test_fast_press_release_keeps_both_events` | Langdruck und 6×-Folge mit Zählung der Entity-Writes fehlt |

## 3. Praxistest-Baseline

### 3.1 Regressionserwartungen [Praxis]

Vom Maintainer berichtet, nicht unabhängig verifiziert; zu erhalten, aber kein
Beweis vollständiger Korrektheit:
R1 Verbindung nach der Migration · R2 Reload lädt die Geräteliste und
verbindet den Stream neu · R3 Öffnungs-/Schließindikatoren vorhanden ·
R4 Zustand und Position entsprechen der Realität · R5 Positionsbefehle und
Zieländerung während der Fahrt · R6 Stop → Positionsmeldung nach ≈110 ms,
sofortige Aktualisierung · R7 lokale Wippentelegramme der Rollladenaktoren,
danach Positionsupdates · R8 F6-02-01: 6 Press- und 6 Release-Telegramme, je
genau ein Event-Entity-Update, Gateway→HA 7–19 ms, Empfang→Write 0,9–1,5 ms.
Aus der Stichprobe R8 folgt **keine** allgemeine Exactly-once-Garantie.

### 3.2 Offene Beobachtung: verzögerte Endlagenmeldung

| Ablauf | Zeit | Beobachtung | Einordnung |
|--------|-----:|-------------|-----------|
| A | T+0,000 s | Rohwert 0 gesendet | OPUS 0 = offen → HA-Position 100 (`cover.py`: `100 - channel.position`) [Code] |
| A | T+0,482 s | Position `unknown` | `from`-Telegramm; ungültiger Wert lässt die letzte Position stehen und bestätigt nichts (`_integer_reading`, `confirmed_fields_by_channel`) [Code] |
| A | T+32,976 s | Position 0.0, `gateway_to_ha_ms=8` | Gateway-Zeitstempel liegt bereits bei ≈T+33 s → die Verzögerung entsteht **vor** dem Zeitstempel des Gateways, sofern die Uhren synchron sind (B-7) |
| B | T+0,000 / 0,460 / 33,100 s | Rohwert 100 → `unknown` → 100.0, `gateway_to_ha_ms=19` | wie A; OPUS 100 = geschlossen → HA 0 |

Bewertung [Entwurf]: Die Integration verarbeitet die Endlagenmeldung innerhalb
von Millisekunden nach dem Gateway-Zeitstempel. Eine Regression gegenüber
v0.7.0 ist **nicht nachgewiesen** (kein kontrollierter Vergleich).

Stand nach Abschnitt 3.3: H1 ist durch die Upstream-Mitteilung gestützt
(Aktor-Nachlauf bei Fahrten in die Endlage), H2 und H4 sind dadurch
unwahrscheinlich, H5 ist weiter durch das Log widerlegt. Die Bestätigung an
der eigenen Installation ist Gegenstand von R1.

Ursprüngliche Hypothesen (Revision 1):
- **H1** Der Aktor meldet erst nach Ablauf seiner eingestellten Laufzeit
  (`verticalMovementTime`/Laufzeit inkl. Nachlauf), nicht beim Erreichen des
  Endschalters. Bei ≈33 s gesamt und ≈5–6 s nach Stillstand wäre eine Laufzeit
  von ≈33 s plausibel. [Annahme]
- **H2** Das Gateway stellt die Meldung verzögert bereit (z. B. nach eigener
  Statusabfrage). [Annahme]
- **H3** Uhrenversatz Gateway↔HA verfälscht `gateway_to_ha_ms`. [Annahme]
- **H4** Die Integration verzögert (Debounce, Warteschlange). Gegen H4 spricht:
  REST hat keinen Debounce, Telegramme werden synchron im Stream-Task
  verarbeitet [Code].
- **H5** Die angezeigte Endlage stammt aus der eigenen Fallback-Annahme
  (`_finish_cover_movement` nach Fahrzeit + 5 s) statt aus einem Telegramm.
  Gegen H5 spricht das protokollierte `gateway_to_ha_ms` (nur bei Telegrammen
  geloggt) [Code].

Untersuchung (Phase R1, ohne Polling, ohne Zeitgeber-Annahmen): siehe Phase R1.

### 3.3 Neue Erkenntnis: Nachlauf des D2-05-Aktors (Upstream-Mitteilung, 2026-10-20)

Kernaussage **[Upstream-Mitteilung]**: Die späte Endlagenmeldung ist
erwartetes Verhalten des Aktors und entsteht **nicht** im REST-Transport.

- Fährt ein D2-05-Aktor in die vollständig offene oder geschlossene Lage, hält
  er den Motor einige Sekunden länger als die konfigurierte Fahrzeit
  eingeschaltet, damit die Endlage sicher erreicht wird. Der Endschalter des
  Motors stoppt den Behang physisch; der Aktor sendet seine endgültige
  Positionsmeldung aber erst beim Abschalten.
- Bei Fahrten auf eine Zwischenposition und bei Stop gibt es keinen Nachlauf.
  Das erklärt die Stop-Meldung nach ≈110 ms (Praxis-Baseline R6).
- Die Geräteliste des Gateways enthält die konfigurierte Fahrzeit und die Dauer
  der letzten Fahrt. Messwerte der Upstream-Installation – jede volle Fahrt
  dauerte 3,5–5,7 s länger als konfiguriert:

| Aktor | Konfigurierte Fahrzeit | Letzte volle Fahrt |
|-------|-----------------------:|-------------------:|
| Rolladen 1 | 20,68 s | 26,37 s (+5,7 s) |
| Jalousie 1 | 26,14 s | 31,14 s (+5,0 s) |
| Jalousie 2 | 52,78 s | 57,79 s (+5,0 s) |
| Jalousie 3 | 55,09 s | 60,21 s (+5,1 s) |

- Passt zur eigenen Beobachtung (Abschnitt 3.2): ≈27 s Fahrt + ≈5–6 s
  Nachlauf ≈ 33 s. `gateway_to_ha_ms=8/19` heißt, dass das Gateway das
  Endtelegramm nur Millisekunden vor Home Assistant erhalten hat; die Wartezeit
  liegt zwischen Aktor und Gateway, bevor die REST-API beteiligt ist (vgl. B-7:
  gilt bei synchronen Uhren).
- Vergleich mit der HomeKit-Seite desselben Gateways (Upstream, parallel zu
  v1.0.0b0): Bei 8 Rollläden in einer geplanten Vollfahrt erhielt HomeKit jede
  Endlage im selben Moment; v1.0.0b0 war jedes Mal 4–28 ms früher. Apple Home
  oder die OPUS-App zeigen dieselbe Verzögerung.
- v0.7.0 (MQTT) verhält sich nicht anders: Die Cover-Schätzung ist unverändert.
  Während der Fahrt zeigt die Entity „opening/closing“, bis die konfigurierte
  Fahrzeit plus 5 s Karenz abgelaufen ist oder der Aktor meldet, je nachdem was
  zuerst eintritt. Mit MQTT konnte die Endmeldung nur gleichzeitig oder später
  ankommen; upstream kam sie 0,8–10 s nach HomeKit (Durchsatz des MQTT-Brokers
  im Gateway).

Abgleich mit dem Code [Code]:
- „Fahrzeit + 5 s Karenz“ entspricht `CoverMovement.ends_at`
  (`started_at + travel + COVER_MOVEMENT_GRACE_SECONDS`, 5,0 s) in beiden
  Koordinatoren; eine gültige Positionsmeldung beendet die Schätzung vorher
  (`_record_confirmed_feedback`).
- Folgerung für A-5/R6: Liegt der Nachlauf über der Karenz (upstream bis
  5,7 s), setzt `_finish_cover_movement` das Ziel als **angenommene** Position
  kurz **vor** der echten Endmeldung; die Meldung ersetzt sie danach. Sichtbar
  bleibt das Ergebnis gleich, aber die Herkunft ist für ≈0–1 s „angenommen“.
- `lastVerticalMovementTime` wird vom Code derzeit **nicht** gelesen; nur
  `verticalMovementTime` und `rotationTime` aus `configuration.parameters`
  (`protocol.configuration_state_functions`). Diagnose zeigt die letzte
  Fahrtdauer daher nicht.

Konsequenzen für den Plan [Entwurf]:
- Kein Code-Fix für die Endlagenverzögerung; sie ist Aktorverhalten und wird
  dokumentiert (README „Known limitations“/Cover, Vertrag 12.9) → R0/R1.
- R1 wird von einer Ursachenanalyse zu einer **Bestätigungsmessung** an der
  eigenen Installation verkleinert.
- Die Karenz von 5 s bleibt vorerst unverändert. Sie ist eine gemeinsame
  Konstante beider Transporte (`const.py`) und gilt für MQTT weiter unter
  A1–A5; eine andere Karenz für REST braucht eine eigene REST-Konstante
  (Abschnitt 0.1). Ob REST die Karenz bei Nachlauf > 5 s anpasst oder die
  Herkunft nur sichtbar macht, entscheidet R6 (O-4).

## 4. REST-Ressourcen

| Ressource | Im Code verwendet | Beleg |
|-----------|-------------------|-------|
| `GET /system/info` | ja: Config-Flow, Setup, Reauth/Reconfigure | [Code] [Test] [HW-upstream] |
| `GET /system/uptime` | ja: Setup, alle 60 s | [Code] [Test] [HW-upstream] |
| `GET /devices` | ja: Setup, alle 30 min (D2) | [Code] [Test] [HW-upstream] |
| `GET /devices/stream` | ja, **ohne** Query-Parameter, Format: aneinandergereihte JSON-Dokumente (auch Pretty-Print) | [Code] [Test] [HW-upstream] |
| `GET /devices/{id}` | ja: unbekanntes Gerät, höchstens 1×/h je Gerät | [Code] [Test] |
| `PUT /devices/{id}/state` | ja: Befehle, Statusabfragen | [Code] [Test] [HW-upstream] |
| `GET`/`PUT /devices/{id}/configuration` | ja: nur Service-Aktionen | [Code] [Test] |
| `GET /devices/{id}/parameters` | ja: nur Service-Aktion | [Code] [Test] |
| Stream-Parameter `direction=from`, `delimited=newLine`, `output=singleLine`, `levelOfDetail=high` | **nein**, in keiner Repository-Quelle belegt | – |

Bewertung der Stream-Parameter [Entwurf]:
- `direction=from` **nicht** verwenden: Der Code braucht `to`-Telegramme für
  Fremdbefehle (HomeKit), für die erkannte Zustellung nach ACK 201 und für die
  Echo-Erkennung [Code]. Ohne sie gingen Zustände verloren.
- `delimited=newLine`/`output=singleLine` wären mit dem vorhandenen Splitter
  kompatibel (Zeilenumbrüche sind Leerraum zwischen Dokumenten) und würden das
  Parsen nicht vereinfachen müssen. Übernahme erst nach Hardwarenachweis.
- `levelOfDetail=high`: Wirkung unbekannt; nur nach Hardwarevergleich.

## 5. Zielarchitektur (kleinste tragfähige Lösung)

### 5.1 Entwurfsentscheidungen

| ID | Entscheidung | Konkretes Problem, das sie löst |
|----|--------------|--------------------------------|
| E1 | **Zwei Koordinatoren bleiben**; keine gemeinsame Transport-Engine jetzt. | Eine Vereinheitlichung müsste die MQTT-Schutzmechanismen (Retained-Replays, Fragment-Debounce, Antwort-Routing, A4) neu bauen, obwohl MQTT später entfallen soll. |
| E2 | Neues transportneutrales Modul **`domain.py`** für `CommandDelivery`, `CoverMovement`, Dispatcher-Signale, `request_error`, `ACK_STATUS_DEFERRED`, Zeitkonstanten; `coordinator.py`/`mqtt_transport.py` re-exportieren für Kompatibilität. | K2, K3: REST und Plattformen importieren kein MQTT-Modul mehr. |
| E3 | `transport.OpusCoordinator` wird ein `typing.Protocol` (statt Union). | K6: Entfernen des MQTT-Koordinators ändert keine Plattform. |
| E4 | MQTT nur noch bei Bedarf importieren (`__init__`, `config_flow`), `manifest.json` `dependencies` → `after_dependencies` nach Prüfung (Hassfest, Discovery-Anforderung, MQTT-Start ohne Broker). | K1, K4, K5: REST-only ohne MQTT-Integration. |
| E5 | Bibliotheksgrenze = `api.py` + JSON-Helfer aus `wire.py`; `PriorityLock` wandert in den REST-Koordinator. Spätere Auslagerung als PyPI-Paket ohne Codeänderung am Kern. | B-6, Auftrag „isolierter Client“. |
| E6 | Geräte werden **in place** aktualisiert (`EnOceanDevice`-Methode für Metadaten), nicht neu erzeugt. | B-4: keine vergessenen Felder, keine verwaisten Referenzen. |
| E7 | Stream-first-Start (Abschnitt 7) statt doppelter Geräteliste. | B-8, Startrennen. |

### 5.2 Schichten

| Schicht | Module (Ziel) | Darf nicht kennen |
|---------|---------------|-------------------|
| Transport | `api.py` (HTTP, Stream, Fehler), `mqtt_transport.py` | Home Assistant (nur `mqtt_transport`), Entities, Gerätemodell |
| Dekodierung | `protocol.py` (REST), MQTT-Flattening in `coordinator.py` | aiohttp-Objekte, Entities |
| Fachlogik | `enocean_device.py` (Zustand, Ereignis, Metadaten, Zustellstatus), `domain.py` | MQTT-Topics, HTTP-Responses, Entities |
| Befehlsrouting, Verfügbarkeit | `rest_coordinator.py`, `coordinator.py` | Entity-Objekte |
| Home Assistant | `__init__.py`, `config_flow.py`, Plattformen, `entity.py`, `diagnostics.py` | Transportdetails (nur `OpusCoordinator`) |

### 5.3 Fachmodell

Weiterverwendet: `EnOceanDevice`/`EnOceanChannel`, `state_revision`,
`ChannelStateSnapshot`, `deferred_commands`, EEP-Zuordnungen in `const.py`.
Getrennt geführt (Ist, bleibt):

| Kategorie | Ist-Ort |
|-----------|---------|
| Dauerhafter Zustand | `EnOceanChannel` (z. B. `is_on`, `position`, `angle`) |
| Kurzlebige Ereignisse | Tasterfelder, nach dem Dispatch gelöscht (`clear_button_occurrence`) |
| Metadaten/Konfiguration | `EnOceanDevice` (`dbm`, `last_seen`, `eeps`), `EnOceanChannel.vertical_movement_time`, `rotation_time` |
| Befehlsannahme/Zustellstatus | Rückgabe `CommandDelivery`, `deferred_commands`, `last_command_error` |
| Beobachteter Gerätezustand | Felder nach `from`-Telegramm/Einzelgerät; **noch nicht** von Schätzungen unterscheidbar (A-5) |

Regeln (bestehen, werden mit Tests festgeschrieben): Fehlendes Feld ändert
nichts; ungültiger Wert behält den alten; nur `notAvailable` setzt Messwerte
auf unbekannt. Zustandsverantwortung: genau ein Speicher je Eintrag
(`coordinator.devices`), keine zusätzlichen Stores.

## 6. Stream-Lebenszyklus: Ist und Soll

| Anforderung | Ist [Code] | Soll / Phase |
|-------------|-----------|--------------|
| Ein Reader je Gateway, an den Eintrag gebunden | ein `_stream_task` über `hass.async_create_background_task`, beim Unload abgebrochen und abgewartet | bleibt; Verbindungsgeneration gegen Rückläufer eines ersetzten Streams (R4, **umgesetzt** Phase 18) |
| Nachrichten ≠ Chunks, UTF-8-Grenzen | `JsonDocumentSplitter` mit inkrementellem UTF-8-Decoder, Zustand über Chunks | bleibt |
| Leere/fehlerhafte/unbekannte Nachrichten | Nicht-Objekte übersprungen, unbekannte Typen ignoriert, Fremdzeichen → Fehler → Neuverbindung | bleibt |
| Übergroße Nachrichten | Grenze 8 MB nur für über Chunks wachsende Reste (B-3) | auch vollständige Dokumente prüfen (R4, **umgesetzt** Phase 18) |
| EOF/Abbruch | Neuverbindung | Mindestpause und Backoff auch nach kurzlebigen Verbindungen (B-2, R4, **umgesetzt** Phase 18: Stream < 10 s = Fehlschlag) |
| Auth vs. Netz | 401/403 → `OpusAuthError` → Reauth, keine Neuversuche; sonst Backoff | bleibt |
| Backoff mit Jitter | fest 1, 2, 5, 10, 30, 60 s, ohne Jitter | begrenzt exponentiell mit Jitter (R4, **umgesetzt** Phase 18: nominal 1–60 s, +0–25 %) |
| Cancellation, Response-Freigabe | `async with session.get(...)`; Unload bricht Tasks ab und wartet | bleibt; Test „kein offener Reader nach Reload“ (R4, **umgesetzt** Phase 18, zusätzlich `aclosing`) |
| Gateway-Neustart | fallende Uptime → Stream ersetzen | bleibt |
| Stiller Stream | nach 900 s ohne Daten neu öffnen (`STREAM_IDLE_TIMEOUT`); kein Heartbeat dokumentiert; Stille ≠ Fehler (keine Nichtverfügbarkeit) | bleibt; Verfügbarkeit nur über Verbindungsfehler und Uptime-Probe |

## 7. Start und Resynchronisierung

### 7.1 Analyse des Rennens zwischen `GET /devices` und Stream [Code]

1. Setup liest `GET /devices` (Zeitpunkt t₁), startet danach den Stream.
2. Der Stream liefert als erstes Dokument die vollständige Liste (t₂ > t₁) und
   danach Telegramme **in Reihenfolge auf derselben Verbindung**.
3. Änderungen zwischen t₁ und t₂ sind im Stream-Snapshot enthalten; Zustände
   gehen nicht verloren. Tasterereignisse aus diesem Fenster gehen verloren –
   das ist korrekt, Ereignisse werden nie aus Snapshots erzeugt.
4. Folge: Die erste Liste ist redundant (B-8). Ein echtes Überschreibrisiko
   besteht nur bei einer Quittung kurz vor t₂: Der Snapshot kann den alten
   Zustand zeigen. Ausstehende Statusprüfungen bleiben bestehen, weil Listen
   keine Rückmeldung sind (`test_rest_devices.py::test_device_list_is_not_command_feedback`).
5. Die 30-min-Liste ist nicht autoritativ und überschreibt keinen Live-Zustand;
   nur Metadaten können zurückspringen (B-5).

### 7.2 Vorgeschlagener Ablauf [Entwurf, Phase R5]

1. `GET /system/info` – Identität (EURID) prüfen; Auth/Mismatch → Reauth.
2. Stream öffnen und auf das erste `devices`-Dokument warten (Frist z. B.
   30 s, Wert in R5 festlegen). Ausbleiben → `ConfigEntryNotReady`.
3. Dieses Dokument autoritativ anwenden → verfügbar → Plattformen laden.
4. Derselbe Task liest den Stream weiter (keine zweite Verbindung).
5. Jede Neuverbindung beginnt wieder mit einem autoritativen Snapshot;
   Verbindungsgeneration verhindert die Anwendung von Dokumenten eines
   ersetzten Streams.
6. `GET /devices` nur noch alle 30 min (D2); Metadaten aus dieser Liste
   überschreiben neuere Telegramm-Metadaten nicht (B-5).
7. `GET /system/uptime` bleibt die 60-s-Gesundheitsprüfung, ändert nie
   Gerätezustände (Gateway-Gesundheit ist kein Gerätezustand; HA-Regel
   `entity-unavailable`).

Begründung: eine Quelle der Wahrheit beim Start, kein Rennen zwischen zwei
Snapshots, eine Geräteliste weniger je Start. Alternative „beides behalten“
bleibt möglich, falls Hardware zeigt, dass der erste Stream-Snapshot verzögert
kommt; Entscheidung in R5 nach Messung.

## 8. Phasenplan

Arbeitszweig (Entscheidung 2026-10-20): Bis auf Weiteres wird nur auf
`feature/rest-api-integration` gearbeitet. Jeder Phasen-Branch startet von
dort und wird per PR mit Base `feature/rest-api-integration` zurückgeführt,
nie nach `main`. Ein späterer Merge nach `main` braucht zuerst den Revert des
Revert-Commits `32685d1` (PR #15).

Jede Phase: eigener Branch (`feat/`/`fix/`), Tests zuerst, CI grün,
Phasenbericht `phases/phase-NN.md` nach Abschnitt 20 von
`implementation-plan.md`, keine Version, kein CHANGELOG ohne Freigabe.
Hardwaretests nur mit separater Freigabe. Für jede Phase gilt Abschnitt 0.1
(D3): A1–A5 für den MQTT-Pfad und gemeinsamen Code, Best Practices und
Home-Assistant-Vorgaben für den REST-Transport.

### R0 – Dokumentationsnachzug (nur Doku)

**Status 2026-10-20:** umgesetzt auf Branch `docs/opus-r0-rest-documentation`
(Basis `1847f88`), Bericht `phases/phase-15.md`. **Gemergt** in
`feature/rest-api-integration` über PR #16 (Merge-Commit `4014b1c`,
2026-10-20); nicht in `main`. Erledigt damit
A-1 und A-6 aus Abschnitt 2.5.

- D2 eintragen: `implementation-plan.md` Abschnitt 3 (Ergänzung „genehmigt
  2026-10-20“) und 15a, `phases/phase-14.md` Abschnitt 9,
  `protocol-contract.md` 12.8/12.9 (Praxis-Baseline, Endlagenbeobachtung,
  Aktor-Nachlauf nach Abschnitt 3.3 als Upstream-Mitteilung gekennzeichnet).
- README „Known limitations“: Endlagenmeldung bei Vollfahrten kommt erst nach
  dem Aktor-Nachlauf (≈3,5–6 s nach physischem Stillstand); Stop und
  Zwischenpositionen ohne Nachlauf.
- Dieses Dokument verlinken.
- Akzeptanz: keine Codeänderung, Querverweise konsistent.

### R1 – Bestätigung des Aktor-Nachlaufs (Messung, kein Fix)

Ausgangspunkt ist Abschnitt 3.3 (Upstream-Mitteilung, H1 gestützt).

- Installierten Commit notieren (A-4).
- Vollfahrt auf/zu: Apple Home oder OPUS-App neben Home Assistant beobachten;
  die Endlage soll in beiden im selben Moment erscheinen.
- Konfigurierte Fahrzeit und letzte Fahrtdauer vergleichen
  (`verticalMovementTime`, `lastVerticalMovementTime`): Differenz nach einer
  Vollfahrt ≈5–6 s erwartet. Quelle bevorzugt die Geräteliste des Gateways
  (keine Funkanfrage). Der Upstream-Vorschlag
  `opus_greennet.get_device_configuration` ist eine ReCom-Anfrage
  (`protocol-contract.md` 12.2: potenziell Funk) und daher nur als einmalige,
  ausdrücklich freigegebene Benutzeraktion zulässig.
- Diagnose: `vertical_movement_time` und `last_update_source` der Endlage
  (Telegramm statt Fallback, H5); Uhren von Gateway und HA (NTP) prüfen (H3).
- Ergebnis dokumentieren (Phasenbericht, README-Hinweis zur Endlage bei
  Vollfahrten, Vertrag 12.9).
- Reine Messphase ohne Codeänderung. Für eine unverfälschte Messung keine
  zusätzlichen Abfragen während der Messfahrten. Folgerungen für den Code
  (z. B. Karenz, Herkunft der Position) gehen nach R6.

### R2 – REST-only-Entkopplung

**Status 2026-10-20:** umgesetzt auf Branch `feat/opus-r2-rest-only-decoupling`
(Basis `feature/rest-api-integration` `4014b1c`), Bericht `phases/phase-16.md`;
**gemergt** in `feature/rest-api-integration` über PR #17
(Merge-Commit `b9c3fb0`, 2026-10-20); nicht in `main`. K1–K6 aufgelöst:
`domain.py` (E2), `OpusCoordinator` als
`Protocol` (E3), MQTT-Importe nur bei Bedarf in `__init__`/`config_flow` (E4),
Fixture `hass_without_mqtt` (K5). O-1 geklärt: `after_dependencies: ["mqtt"]`
mit Belegen (Hassfest am Referenzcommit grün, MQTT-Discovery und MQTT-Einträge
unverändert, REST-Eintrag ohne MQTT-Integration). Keine Verhaltens-, Timing-,
Wire- oder Verkehrsänderung auf MQTT oder REST. K7 bleibt offen (R8).

- `domain.py` (E2), Re-Exporte in `coordinator.py`/`mqtt_transport.py`.
- `OpusCoordinator` als `Protocol` (E3).
- Lazy-Imports MQTT (`__init__`, `config_flow`); `manifest.json` nach Prüfung
  `after_dependencies: ["mqtt"]` (E4) – Hassfest am Referenzcommit, MQTT-
  Discovery und MQTT-Einträge ohne Verhaltensänderung belegen.
- Tests: Fixture ohne `"mqtt"` in `hass.config.components`; REST-Eintrag lädt,
  steuert, entlädt; Import von `rest_coordinator` lädt kein
  `homeassistant.components.mqtt` (Prüfung über `sys.modules`).
- MQTT (A1–A5): keine Verhaltens- oder Verkehrsänderung; MQTT-Tests
  unverändert grün; Re-Exporte sind keine Verhaltensänderung.
- REST (D3): Struktur nach Home-Assistant-Vorgaben; Verhaltensänderungen am
  REST-Pfad sind zulässig, wenn begründet und im Bericht offengelegt.

### R3 – REST-Client-Härtung und Bibliotheksgrenze

**Status 2026-10-20:** umgesetzt auf Branch `feat/opus-r3-rest-client-hardening`
(Basis `feature/rest-api-integration` `b9c3fb0`), Bericht `phases/phase-17.md`;
gemergt über PR #18 (`1c30105`), CI-Lauf `39423057097` grün. B-1 behoben (HTTP-Status und JSON-Status getrennt, Fehlerklasse
mit `http_status` und `gateway_status`), B-6 behoben (`PriorityLock` und
Prioritäten im REST-Koordinator; `api.py` importiert nur Standardbibliothek,
aiohttp und `wire.py`). Keine Wiederholungen, Nebenläufigkeit und Timeouts
unverändert (Begründung im Bericht, Abschnitt 2). MQTT-Pfad unverändert.

- HTTP-Status zusätzlich zum JSON-Status auswerten (B-1); Fehlerklasse mit
  beiden Werten.
- `PriorityLock` in den REST-Koordinator (B-6); `api.py` ohne
  Integrationskonstanten.
- Tests: kein HA-Import in `api.py`/`wire.py` (AST), kein `ClientSession(`,
  Fehlermeldungen ohne Zugangsdaten, keine Wiederholung bei Timeout eines PUT.
- REST (D3): Nebenläufigkeit, Timeouts und Wiederholungen nach Best Practice
  festlegen und im Bericht begründen (Idempotenz des Befehls, Risiko einer
  Doppelausführung am Aktor, Funkverkehr vorher/nachher).

### R4 – Stream-Lebenszyklus

**Status 2026-10-20:** umgesetzt auf Branch `feat/opus-r4-stream-lifecycle`
(Basis `feature/rest-api-integration` `e5b609e`), Bericht `phases/phase-18.md`;
gemergt über PR #21 (`ae3e084`), CI-Lauf `39431240243` grün. B-2 behoben (begrenzter exponentieller
Backoff 1–60 s mit 0–25 % Jitter nach oben, Pause nach jedem Stream-Ende,
Stream < 10 s zählt als Fehlschlag), B-3 behoben (Größenprüfung auch für
vollständige Dokumente), Verbindungsgeneration und `aclosing` gegen
Rückläufer eines ersetzten Readers. MQTT-Pfad unverändert.

- Begrenzter exponentieller Backoff mit Jitter; kurzlebige Verbindungen
  (z. B. < 10 s) zählen als Fehlschlag (B-2).
- Verbindungsgeneration für Dokumente; Größenprüfung auch für vollständige
  Dokumente (B-3).
- Tests: Schließen direkt nach dem Snapshot erzeugt keine enge Schleife;
  Reload hinterlässt keinen zweiten Reader; Auth-Fehler ohne Neuversuch.
- REST (D3): Backoff und Neuverbindung nach Best Practice; Gateway-Verkehr
  vorher/nachher im Bericht.

### R5 – Start und Resynchronisierung

- Stream-first nach Abschnitt 7.2 (E7); Frist für das erste Dokument messen.
- B-5: Metadaten der 30-min-Liste nur übernehmen, wenn kein neueres Telegramm
  seit Anfragebeginn kam.
- Tests: Rennen Anfrage/Telegramm, Neuverbindung mit Snapshot, kein
  Tasterereignis aus Snapshots, Setup-Fehler ohne erstes Dokument.
- Messbar: eine Geräteliste weniger je Start und Reload.

### R6 – Fachmodell

- In-place-Aktualisierung (E6) mit Test „Feldliste vollständig übernommen“.
- Herkunft der Position (`reported`/`estimated`/`assumed`) zunächst nur in der
  Diagnose (A-5); keine sichtbare Verhaltensänderung ohne Entscheidung.
- `lastVerticalMovementTime` aus der Geräteliste als Diagnosewert übernehmen
  (keine neue Anfrage; Abschnitt 3.3), damit der Nachlauf ohne ReCom sichtbar
  ist.
- Entscheidungsvorlage zur 5-s-Karenz bei Nachlauf > 5 s (O-4): unverändert
  lassen, nur Herkunft anzeigen, oder Karenz aus gemessener Fahrtdauer
  ableiten (würde Statusprüfungen zeitlich verschieben, nie vermehren).
- Regressionstests für R4/R7/R8 aus Abschnitt 2.7 (Sequenz Wippe → `unknown`
  → Endlage; 6× Press/Release mit genau einem Entity-Write je Telegramm;
  Langdruck).

### R7 – Standard für neue Einträge (Entscheidung)

**Teilweise vorgezogen 2026-10-20 als R4.2** (Entscheidung des Maintainers):
REST steht im Einrichtungsmenü an erster, MQTT an zweiter Stelle; Branch
`feat/opus-r4-2-rest-first-menu`, Bericht `phases/phase-19.md`. Offen für R7:
MQTT-Hinweis „Fallback“ in den Menütexten.

**Weiter vorgezogen 2026-10-20 als R4.3** (Entscheidung des Maintainers):
Neue Gateways werden nur noch über REST eingerichtet. Der Einrichtungsdialog
zeigt direkt Host, Port, Benutzer und Passwort (kein Menü), auch ein über MQTT
entdecktes Gateway führt in dieses REST-Formular. MQTT bleibt für bestehende
Einträge und als Wahl im Neu-konfigurieren-Menü. Branch
`feat/opus-r4-3-rest-only-setup`, Bericht `phases/phase-20.md`; gemergt über
PR #24 (`bcb6276`), CI-Lauf `39449339335` grün. Damit ist der
Menüteil von R7 erledigt; ein „Fallback“-Hinweis entfällt.

- Optional: REST als erste Menüoption (**erledigt**, R4.2; ersetzt durch R4.3: REST als einzige Einrichtung); MQTT-Hinweis „Fallback“ (entfällt).
- Bestehende MQTT-Einträge werden nie automatisch umgestellt (A5 gilt für
  MQTT). Auswirkungen auf bestehende REST-Einträge und Entity-IDs werden im
  Bericht offengelegt (D3).

### R8 – Abschlussaudit

- `quality_scale.yaml`, Evidenzmatrix, README, Vertrag synchronisieren;
  K7 (Discovery für REST) bewerten: Discovery mit vorbelegter Adresse und
  Herstellerstandard-Passwort prüfen oder Regel nach MQTT-Entfernung
  begründet neu einstufen.
- Repetierte Race-Suites inkl. REST, Referenz- und Latest-Job grün.

### Später (nicht Teil dieses Plans)

MQTT-Entfernung erst nach R2–R8, nach Freigabe und nach einer
Übergangsversion: `coordinator.py`, `mqtt_transport.py`, `discovery.py`,
MQTT-Flow-Schritte, MQTT-Tests entfernen; Plattformen und Gerätemodell
bleiben unverändert (Ziel 5).

## 9. Akzeptanzmatrix

| Szenario | Erwartung |
|----------|-----------|
| Langer Leerlauf | nur Uptime 60 s und Geräteliste 30 min; keine Gerätestatusabfrage |
| Stream schweigt 15 min | Neuverbindung mit Snapshot, Entities bleiben verfügbar |
| Gateway schließt Verbindungen sofort | Backoff mit Jitter, keine enge Schleife |
| Reload/Unload | kein offener Reader, keine Timer, keine Tasks |
| Snapshot nach Telegramm auf derselben Verbindung | Telegramm gilt (Reihenfolge) |
| 30-min-Liste während Telegramm | Live-Zustand bleibt; Metadaten springen nicht zurück |
| Positionsmeldung `unknown` | letzte Position bleibt, keine Bestätigung |
| Endlage verzögert (Vollfahrt, Aktor-Nachlauf) | keine Zusatzabfrage, keine Zeitgeber-Endlage als Rückmeldung; Endmeldung wird bei Empfang sofort übernommen |
| Stop oder Zwischenposition | Positionsmeldung ohne Nachlauf, sofortige Übernahme |
| Tasterfolge | je Telegramm genau ein Event-Update, keine Wiederholung aus Listen |
| REST-only ohne MQTT-Integration | Eintrag lädt, steuert, entlädt |
| MQTT-Eintrag | Verhalten und Verkehr unverändert |
| ACK 201 | keine Annahme, keine Abfrage |
| Timeout eines Befehls | nur diese Aktion fehlgeschlagen, Gateway verfügbar, kein Retry |

## 10. Offene Annahmen und Entscheidungen

| Nr. | Offen | Klärung |
|-----|-------|---------|
| O-1 | Startet Home Assistant die Integration ohne eingerichtete MQTT-Integration mit `after_dependencies`, und akzeptiert Hassfest den `mqtt`-Discovery-Schlüssel dann? | **Geklärt in R2** (`phases/phase-16.md` Abschnitt 5): ja; `manifest.json` umgestellt |
| O-2 | Wirkung und Unterstützung der Stream-Parameter | Hardwarevergleich, separate Freigabe |
| O-3 | Ursache der Endlagenverzögerung: durch Upstream-Mitteilung als Aktor-Nachlauf erklärt (Abschnitt 3.3); Bestätigung an der eigenen Installation offen | R1 |
| O-4 | Soll die Positionsschätzung (A-5) sichtbar bleiben, und wie wird die 5-s-Karenz bei Nachlauf > 5 s behandelt? | Entscheidung nach R6 |
| O-5 | REST als Standard für neue Einträge | R7 |
| O-6 | Welcher Commit lief im Praxistest? | R1 (A-4) |
| O-7 | Discovery für REST-Gateways | R8 |
