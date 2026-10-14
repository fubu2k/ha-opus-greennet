# OPUS-/EnOcean-MQTT-Protokollvertrag (Ist-Stand)

Stand: Basis `3e52fce31e7a2d503eef4820f4c706ebcfc751ab` (`main`), erfasst in Phase 1.
Dieses Dokument beschreibt das **tatsächliche** Verhalten des Codes. Es ist der
Maßstab für alle Folgephasen: Was hier steht, bleibt unverändert, außer eine
Phase erlaubt ausdrücklich eine belegte Fehlerkorrektur.

Quelle des Herstellerprotokolls: `opus_mqtt_services_reference.md`. Wo der Code
über die Referenz hinausgeht (Gateway-Aliasschlüssel, Mosquitto-Bridge-Topic),
ist das vermerkt.

Legende Verifikation: **[Code]** am Quelltext gelesen, **[Test]** durch
vorhandene Tests abgesichert, **[Ref]** laut Herstellerreferenz, **[unbelegt]**
weder durch Hardware noch durch Referenz belegt.

## 1. Topics

Basis: `EnOcean/<EAG_ID>/…` (`TOPIC_BASE = "EnOcean"`). `<EAG_ID>` sind 8
Hex-Zeichen, im Config Flow in Großbuchstaben normalisiert.

### 1.1 Abonnements des Coordinators

| Gruppe | Topic-Filter | QoS | Handler | Hinweis |
|--------|--------------|-----|---------|---------|
| Control (Setup-Phase 1) | `opus_greennet/<EAG_ID>/bridge/status` | 1 | `_handle_bridge_status` | Mosquitto-Bridge-Benachrichtigung, kein OPUS-Topic **[Code]** |
| Control | `EnOcean/<EAG_ID>/getAnswer/config/system/uptime` | 1 | `_handle_system_uptime` | zugleich Antwort-Route des Request-Managers |
| Control | `EnOcean/<EAG_ID>/getAnswer/config/system/info` | 1 | `_handle_system_info` | optional, nicht jede Firmware antwortet |
| Control | `EnOcean/<EAG_ID>/putAnswer/devices/+/state` | 1 | `_handle_put_answer_state` | zugleich Antwort-Route |
| Stream (Setup-Phase 2, einzeln nacheinander mit SUBACK) | `EnOcean/<EAG_ID>/getAnswer/devices/#` | 1 | `_handle_get_answer_devices` | Discovery-Antworten |
| Stream | `EnOcean/<EAG_ID>/stream/devices/#` | 0 | `_handle_device_property_message` | Startup-Snapshot, flach |
| Stream | `EnOcean/<EAG_ID>/stream/device/#` | 0 | `_handle_device_stream_message` | Live-Deltas |
| Stream | `EnOcean/<EAG_ID>/stream/telegram/#` | 0 | `_handle_telegram_property_message` | Funktelegramme |

Die SUBACKs der Stream-Gruppe sind nur beratend (`required=False`). Ein
fehlender SUBACK lässt den Health-Probe nicht scheitern. **[Code][Test]**

Temporäre Abonnements (QoS 1) legt der `MQTTRequestManager` nur für
Antwort-Topics an, die nicht über eine registrierte Route abgedeckt sind
(Profil, Konfiguration, Parameter). Registrierte Routen: Uptime-Antwort und
`putAnswer/devices/+/state`. **[Code]**

### 1.2 Publizierte Topics

| Topic | Payload | QoS / Retain | Auslöser |
|-------|---------|--------------|----------|
| `…/get/config/system/uptime` | `""` | 1 / false | Setup, Health-Tick, Reconnect, Config Flow |
| `…/get/devices` | `""` | 1 / false | nur bei Resync |
| `…/get/config/system/info` | `""` | 1 / false | nur bei Resync, Antwort wird nicht abgewartet |
| `…/put/devices/<DEV>/state` | `{"state": {"functions": [...]}}` | 1 / false | Befehle und Statusabfragen |
| `…/get/devices/<DEV>/profile` | `""` | 1 / false | `async_get_device_profile` (derzeit kein Aufrufer im Integrationscode) |
| `…/get/devices/<DEV>/configuration` | `""` | 1 / false | Service `get_device_configuration` |
| `…/put/devices/<DEV>/configuration` | `json.dumps(config)` | 1 / false | Service `set_device_configuration` |
| `…/get/devices/<DEV>/parameters` | `""` | 1 / false | Service `get_device_parameters` |

Ungenutzte Konstanten in `const.py`: `TOPIC_STREAM_TELEGRAM`,
`TOPIC_STREAM_DEVICE`, `TOPIC_GET_/PUT_(ANSWER_)LINK_TABLES`,
`TOPIC_SUB_TELEGRAM_FROM`, `TOPIC_SUB_TELEGRAM_TO`, `TOPIC_SUB_DEVICE`,
`TOPIC_SUB_DEVICES`, `TOPIC_SUB_GET_ANSWER`. **[Code]**

## 2. Antworten und Status

- `decode_response`: Payload muss ein nicht leeres JSON-Objekt sein. Ist
  `header` vorhanden oder `require_status=True`, muss `header.httpStatus`
  ganzzahlig sein (kein `bool`, kein nicht ganzzahliger `float`) und den Wert
  **200 oder 201** haben. 201 steht laut Referenz für SoftSmartAck. **[Code][Test][Ref]**
- Uptime: `systemUptimeResponse.uptime` muss `str`, `int` oder `float` sein
  (kein `bool`), endlich und ≥ 0. Gespeichert wird `str(value)`. **[Code][Test]**
- Bridge-Status: nur die Payloads `"0"` und `"1"` werden ausgewertet, alle
  anderen ignoriert. **[Code][Test]**
- `getAnswer/devices`: akzeptiert eine Liste, `{"devices": [...]}`, ein
  einzelnes Objekt und Einträge der Form `{"device": {...}}`. Unterpfade mit
  mehr als einer Ebene unter `getAnswer/devices/` (Profil, Konfiguration)
  werden ignoriert. **[Code][Test]**

## 3. Befehls-Payloads (Wire-Werte, unveränderlich)

Format: `json.dumps({"state": {"functions": functions}})`, Werte immer als String.

| Aktion | functions | Quelle |
|--------|-----------|--------|
| Schalter an/aus | `[{"key": "switch", "value": "on"/"off"}]` | `async_turn_on/off` |
| Dimmer an ohne Helligkeit | `[{"key": "dimValue", "value": "100"}]` | Referenz 5.3 |
| Dimmer aus | `[{"key": "dimValue", "value": "0"}]` | Referenz 5.3 |
| Dimmer Helligkeit | `[{"key": "dimValue", "value": str(max(1, round(brightness_to_value((1, 100), b))))}]`; HA-Helligkeit 0 → aus | `light.py` |
| Cover öffnen / schließen | `position` `"0"` / `"100"` | OPUS 0 = offen, 100 = geschlossen |
| Cover Position | `position` `str(100 - ha_position)` | **Cover-Inversion** |
| Cover Tilt | `angle` `str(ha_tilt)` (nicht invertiert) | `cover.py` |
| Cover STOP | `[{"key": "stop", "value": "true"}]` | **STOP-Payload**, nie `position: "stop"` **[Ref][Test]** |
| Statusabfrage | `[{"key": "query", "value": "status"}]` | Abgleich, Klima-Abfrage |
| HeatArea Sollwert | `[{"key": "temperatureSetpoint", "value": str(temperature)}]` | ohne Kanal-Selektor |
| HeatArea aus | `[{"key": "heaterMode", "value": "off"}]` | |
| HeatArea an | D1-4B-06: `"on"`; D1-4B-05 und D1-4B-07: `"heating"` | **proprietäre HeatArea-Profile** |

### 3.1 Kanal-Selektor (Kanal null)

`_with_channel_if_needed` stellt `{"key": "channel", "value": str(channel)}`
voran, wenn `channel > 0` **oder** `channel_count > 1` gilt. Bei
Mehrkanalgeräten wird damit auch **Kanal 0 explizit gesendet**. Einkanalgeräte
erhalten keinen Selektor. Klima-Befehle senden nie einen Selektor. **[Code][Test]**

Eingehend gilt: Fehlt der Selektor, ist der Default-Kanal 0. Ist ein Selektor
fehlerhaft (negativ, keine ganze Zahl, `bool`), wird **nicht** auf Kanal 0
ausgewichen. Funktionen ohne eigenen gültigen Kanal werden dann verworfen
(`_parse_channel_id`). **[Code][Test]**

## 4. Eingehende Werte (Validierung in `EnOceanDevice.update_from_telegram`)

| Schlüssel | Gültige Werte | Bei `notAvailable` | Bei ungültigem Wert |
|-----------|---------------|--------------------|---------------------|
| `switch`, `localControl` | `on` / `off` | ignoriert | ignoriert |
| `dimValue` | ganzzahlig 0–100; setzt `is_on = value > 0` | ignoriert | ignoriert |
| `position`, `angle` | ganzzahlig 0–100 | ignoriert | ignoriert |
| `verticalMovementTime` | ≥ 0 | ignoriert | ignoriert |
| `rotationTime` | `noRotation` → 0; Zahl ≥ 0 | ignoriert | ignoriert |
| `energy`, `power` | endliche Zahl | ignoriert | ignoriert |
| `liquidDetected`, `windowOpen`, `summerMode` | bool oder `true`/`false` | ignoriert | ignoriert |
| `smokeAlarm`, RWM-`alarm` | `on`/`off`, `alarm`/`idle`, bool | – | **setzt `None`** |
| `batteryLow` | `low`/`ok`, bool | – | **setzt `None`** |
| `batteryLevel` | 0–100, optionales `%`-Suffix | **setzt `None`** | ignoriert |
| `motionDetector`, SMS-`motionDetected` | `detected`/`noMotion`, bool | – | **setzt `None`** |
| `illuminance`, SMS-`illumination`, `supplyVoltage` | ≥ 0 | **setzt `None`** | ignoriert |
| `handleState`, Griff-`handle` | `open`/`closed`/`tilted`, `tilt` → `tilted` | – | **setzt `None`** |
| `lock` | `locked`/`unlocked` | – | **setzt `None`** |
| `unlockRequest`, AutoLock-`unlock` | `requested`/`notRequested`, bool | – | **setzt `None`** |
| `mechanicsFault`, AutoLock-`mechanics` | `error`/`ok`, bool | – | **setzt `None`** |
| `temperature`, `temperatureSetpoint` | 0–40 | **setzt `None`** | ignoriert |
| `humidity` | 0–100 | **setzt `None`** | ignoriert |
| `feedTemperature` | 0–80 | **setzt `None`** | ignoriert |
| `energyConsumption` | 0–10 | **setzt `None`** | ignoriert |
| `heaterMode` | `heating`, `on`, `off`, `autoOff`, `configIncomplete`, `error` | ignoriert | ignoriert |
| `thermalMode` | `heating` / `cooling` | ignoriert | ignoriert |
| `powerState` | `active` / `inactive` | ignoriert | ignoriert |
| `temperatureOrigin` | `external` / `internal` | ignoriert | ignoriert |
| `actuatorDeactivated` | `info` / `reset` | ignoriert | ignoriert |
| `actuatorLowBattery` | `warning` / `reset` | ignoriert | ignoriert |
| `actuatorNotResponding` | `warning` / `error` / `reset` | ignoriert | ignoriert |
| `missingTemperature` | `info` / `warning` / `error` / `reset` | ignoriert | ignoriert |
| `circuitInUse` | `error` / `reset` | ignoriert | ignoriert |
| `buttonA0`, `buttonAI`, `buttonB0`, `buttonBI`, `multipleButtons` | `pressed` / `released` | – | ignoriert |

Profilgebundene Aliasse (IQ-DOT 1.21.30): `alarm` nur für F6-05-02,
`motionDetected`/`illumination` nur für A5-07-01/03, `handle` nur für
Griffprofile, `unlock`/`mechanics` nur für D2-06-40. **[Code][Test]**

Schneller Pfad (`stream/device/<DEV>/batteryLevel|dbm`): ohne Debounce.
`dbm` als Ganzzahl, `notAvailable` → `None`. Ungültige Werte werden verworfen.
Ein älterer gepufferter Skalarwert wird verworfen, damit er den neueren nicht
überschreibt. **[Code][Test]**

RWM (F6-05-02): Wertslots in `transmitModes` ohne Schlüssel werden als
`smokeAlarm` (Index 0) und `batteryLow` (Index 1) aufgelöst. **[Code][Test]**

## 5. Telegramme (`stream/telegram`)

- Flach: `…/telegram/<DEV>/from/functions/<i>/key|value`, ebenso `to/…`, oder
  flach mit `direction`-Feld.
- Vollständiges JSON: `…/telegram/<DEV>`, `…/from`, `…/to`. Optional unter
  `telegram` gekapselt, `state` wird eingeflacht.
- Ein Rahmen endet nach 0,15 s Ruhe, bei Richtungswechsel oder bei einem
  wiederholten Pfad mit bereits vollständigem Paar (schnelles Drücken und
  Loslassen bleibt erhalten).
- `from` mit `direction = "to"` wird verworfen.
- `to` (ausgehend) wird verworfen, solange ein lokaler Befehl auf seine ACK
  wartet (`_command_waiters`). Sonst wird es optimistisch angewendet
  (bridge-native bzw. HomeKit-Befehle), nur mit `KNOWN_STATE_KEYS`,
  `channel` und `stop`, und danach wird der Abgleich geplant.
- Unbekanntes Gerät mit Telegramm: Ein Platzhaltergerät wird angelegt. Ein
  Discovery-Signal geht nur, wenn ein `entity_type` bestimmbar ist. **[Code][Test]**

## 6. Retain-Verhalten (bestehende Logik, eingefroren nach A4)

| Eingang | Retained-Nachricht |
|---------|--------------------|
| `stream/devices/#` | verarbeitet (Snapshot, Discovery); Debug-Rohlog unterdrückt |
| `stream/device/#` | verarbeitet; Debug-Rohlog unterdrückt; Latenzlog nur live |
| `stream/telegram/#` | **verworfen** (keine Events, kein Zustand) |
| `getAnswer/devices/#` | **verworfen** |
| `putAnswer/devices/+/state` | an Request-Manager geroutet (der retained ignoriert), kein Zustand |
| Request-Antworten allgemein | `handle_response` ignoriert retained |
| Uptime-Antwort | geroutet; `gateway_uptime` wird aus der Payload aktualisiert (nur Anzeige) |
| System-Info, Bridge-Status | ohne Retain-Prüfung ausgewertet |

Weitere bestehende Freshness-Regel: Bei Rediscovery eines bekannten Geräts wird
der Snapshot-Zustand nur angewendet, wenn das letzte Live-Update nicht neuer
ist als `_resync_requested_at`. Andernfalls werden nur `rotationTime` bzw.
`channel` übernommen. Button-Keys aus `stream/devices` gelten nie als
Ereignis. **[Code][Test]**

## 7. Verfügbarkeit, Health und Zeiten (eingefroren nach A1–A3)

- `coordinator.available` siehe `implementation-plan.md` (A2). Alle Entities
  übernehmen diesen Wert.
- Health-Tick alle 60 s, nur wenn MQTT verbunden ist und die Bridge nicht als
  getrennt gemeldet ist. `resync = not _gateway_available`.
- Reconnect (MQTT oder Bridge): Refresh. Ein voller Snapshot wird höchstens
  einmal je 60 s angefordert, außerdem immer, solange Phase 2 unvollständig ist.
- Disconnect oder Bridge-down: Verfügbarkeit `False`, offene Requests werden
  abgebrochen, Hintergrundarbeit wird verworfen (Telegramm- und Delta-Puffer,
  Abgleich-Timer, Cover-Schätzungen, Tasks).
- Health-Probe-Fehler (`HomeAssistantError`, `TimeoutError`): Verfügbarkeit
  `False`, offene Requests mit `gateway_unavailable` abgebrochen.
- Ein Befehls-Timeout führt zu `request_timeout`, nur für diese Aktion. Kein
  Retry, keine Änderung der Verfügbarkeit.
- Debounce-Zeiten: Discovery 2 s nach der letzten Nachricht, Delta-Finalisierung
  0,02 s, Telegramm-Finalisierung 0,15 s.
- Abgleich nach Befehl: 5 s und 20 s. Cover mit Bewegungsschätzung: Restlaufzeit
  plus 5 s Gnadenfrist, dann +10 s und +20 s.

## 8. Entity-Inventar und Identitäten (eingefroren nach A5)

Gerätekennung: `(opus_greennet, "<EAG>_<DEV>")`, Gateway: `(opus_greennet, "<EAG>")`.

| Plattform | Bedingung | Unique ID | Default |
|-----------|-----------|-----------|---------|
| light | `entity_type == "light"`, je Kanal | `<EAG>_<DEV>`, bei `channel_count > 1`: `<EAG>_<DEV>_ch<n>` | aktiv |
| switch | `entity_type == "switch"`, je Kanal | wie light | aktiv |
| cover | `entity_type == "cover"`, je Kanal | wie light | aktiv |
| climate | `entity_type == "climate"` | `<EAG>_<DEV>` | aktiv |
| event | `entity_type == "event"` (F6-02-xx, F6-03-xx) | `<EAG>_<DEV>` | aktiv |
| lock | D2-06-40 | `<EAG>_<DEV>_autolock` (read-only) | aktiv |
| sensor | HeatArea: `humidity`; D1-4B-05: `feed_temperature`; D1-4B-07: `energy_consumption`; SMS: `illuminance`, `supply_voltage`, `battery_level`; Griff: `handle_state`; D2-06-40: `unlock_request` | `<EAG>_<DEV>_<suffix>` | aktiv |
| sensor | **jedes** entdeckte Gerät: `signal_strength` | `<EAG>_<DEV>_signal_strength` | **deaktiviert** |
| binary_sensor | SMS: `motion`; Griff: `handle_open`, `handle_tilt`, `handle_closed`; D2-06-40: `mechanics_fault`; RWM: `smoke_alarm`, `battery_low`; Beschreibungen: `liquid_detected` (F6-05-01), `window_open`, `actuator_not_responding`, `missing_temperature` (HeatArea), `actuator_low_battery`, `actuator_deactivated` (D1-4B-05), `circuit_in_use` (D1-4B-06) | `<EAG>_<DEV>_<key>` | aktiv |

Registry-Migrationen (bestehend, unverändert): Legacy-Mehrkanal
`<EAG>_<DEV>` → `<EAG>_<DEV>_ch0` (light, switch), Fork-Suffixe
`_illumination` → `_illuminance` und `_window_handle_lock` → `_autolock`.

## 9. Services (öffentlich, unverändert)

`opus_greennet.get_device_configuration` (nur mit Response),
`opus_greennet.set_device_configuration`, `opus_greennet.get_device_parameters`
(nur mit Response), `opus_greennet.reload_entry`. Alle sind Admin-Services
mit optionalem `config_entry_id`.

## 10. Dispatcher-Signale (intern)

`opus_greennet_device_discovered_<EAG>` (Payload: Gerät),
`opus_greennet_device_state_update_<EAG>_<DEV>` (Payload: Gerät),
`opus_greennet_availability_update_<EAG>` (ohne Payload),
`opus_greennet_device_removed_<EAG>` (Payload: `device_id`).

## 11. Nicht verifizierbar in dieser Umgebung

- Reales Gateway-Verhalten (Firmware-Varianten, Retain-Flags, QoS, Reihenfolge
  und Timing der Topics) ist **[unbelegt]**. Alle Aussagen oben stammen aus Code,
  Tests und Herstellerreferenz, nicht aus Hardwarebeobachtung.
- Ob das Gateway für Wippen `stream/device/<DEV>/dbm` publiziert (Relevanz von
  Befund F1), ist **[unbelegt]**.
- Ob der Mosquitto-Bridge-Status retained gesendet wird, ist **[unbelegt]**.
