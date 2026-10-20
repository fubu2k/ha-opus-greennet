# OPUS GreenNet – Profile/Operation Capability Matrix

Created in phase 8 (`implementation-plan.md` section 3 and section 8).
Repository baseline: `bbd7e168e58d3e7e61756ead7cb27b3dea2c42b7` (`main`,
2026-10-19, "chore: release v0.7.0b2").

This matrix separates gateway/cache requests from operations that can cause
EnOcean radio communication. It records what is **known**, not what is
assumed. An unknown radio effect justifies withholding new automatic requests;
it never justifies assuming that a request is harmless (guardrail A1).

## Legend

**Scope** – `gateway` (gateway endpoint), `cache` (gateway inventory/cache per
reference), `radio` (reference states that a telegram is sent or the device is
accessed), `radio?` (may cause radio traffic; not established), `none`
(local Home Assistant representation only).

**Trigger** – `setup`, `resync` (setup phase 2 or debounced reconnect),
`health` (every 60 s), `user` (explicit entity action or admin service),
`recon` (existing reconciliation after a command), `never` (no publisher in
the integration).

**Verification** – `reference` (`opus_mqtt_services_reference.md`), `code`
(read from source at the baseline), `mock test` (asserted by a test with a
mocked MQTT transport; names in brackets), `hardware` (observed on real
hardware with separate approval). No row is currently `hardware`.

**Power source** – stated only where the user's installation context or the
reference gives evidence. "Not verified" means the actual model was not
checked; the EEP alone is never used to infer a power source.

## 1. Gateway operations (all profiles)

| Operation | Topic | Scope | Trigger | Budget (baseline) | Verification |
|-----------|-------|-------|---------|-------------------|--------------|
| Uptime probe | `get/config/system/uptime` | gateway | setup, resync, health | 1 per 60 s tick; 2 at setup | reference, code, mock test [`test_traffic_baseline.py`] |
| System info | `get/config/system/info` | gateway | resync | 1 per resync, answer not awaited | reference, code, mock test [`test_traffic_baseline.py`] |
| Device inventory | `get/devices` | cache | resync | at most 1 per 60 s reconnect window (always while setup phase 2 is incomplete) | reference, code, mock test [`test_traffic_baseline.py`] |
| Stream subscriptions | `stream/device(s)/#`, `stream/telegram/#` | none (subscribe, no publish) | setup, resync | no publish | code, mock test |
| Gateway discovery (phase 9) | `EnOcean/+/stream/devices/+/friendlyId`, subscribed by the Home Assistant MQTT integration from `manifest.json` | none (subscribe, no publish) | HA start, broker reconnect | no publish on receipt; one uptime probe only after user confirmation (same as manual setup) | code, mock test [`test_discovery.py`] |

Firmware effects of `get/devices` (pure cache read or internal refresh) are
**unknown**; the reference lists it under "from gateway cache".

## 2. Device operations by profile

| EEP / profile | HA platform | Operation | Topic / function | Scope | Trigger | Known power source | Support | Verification |
|---------------|-------------|-----------|------------------|-------|---------|--------------------|---------|--------------|
| D2-01-00 … D2-01-12 (switch, dimmer, multi-channel) | switch, light | Command | `put/…/state` `switch`, `dimValue`, `channel` | radio | user | OPUS bridges mains-powered per installation context; model not verified | supported | reference (5.1–5.3), code, mock test |
| D2-01-xx | switch, light | Status query | `put/…/state` `query: status` (+ `channel`) | radio? | recon (5 s, 20 s after unconfirmed command) | as above | supported by code; not listed for D2-01 in the reference | code, mock test [`test_traffic_baseline.py`, `test_background_efficiency.py`] |
| D2-05-00 / 01 / 02 (blinds, shutters) | cover | Command | `position`, `angle`, `stop: "true"` | radio | user | mains-powered bridge per installation context; model not verified | supported | reference (5.4), code, mock test [`test_cover_stop.py`] |
| D2-05-xx | cover | Status query | `query: status` | radio? | recon (end of estimated travel + 5 s grace, then +10 s, +20 s; after STOP) | as above | supported by code; reference asks for a status request after STOP | reference (5.4), code, mock test [`test_cover_movement.py`] |
| D1-4B-05 Valve Area | climate | Command | `temperatureSetpoint`, `heaterMode` `heating`/`off` | radio? (HeatArea is a gateway-managed area; linked actuators receive radio) | user | actuators can report `actuatorLowBattery` (battery-powered actuators exist); not verified per installation | supported | reference (5.5.1), code, mock test |
| D1-4B-06 CosiTherm Area | climate | Command | `temperatureSetpoint`, `heaterMode` `on`/`off` | radio? | user | not verified | supported | reference (5.5.2), code, mock test |
| D1-4B-07 Electro Heating Area | climate | Command | `temperatureSetpoint`, `heaterMode` `heating`/`off` | radio? | user | not verified | supported | reference (5.5.3), code, mock test |
| D1-4B-05 / 06 / 07 | climate | Status query | `query: status` | radio? (reference: "request device update from heatarea") | recon | see above | documented by reference | reference, code, mock test |
| A5-38-08 / 09 (gateway dimming/switching) | light | Command, status query | as D2-01 lights | radio? | user, recon | not verified | supported by code | code |
| D2-06-40 HOPPE SecureConnect handle | lock (read-only), sensor, binary_sensor | Incoming only | `stream/…` | none (no publish) | never | not verified | supported | code, mock test [`test_window_handles.py`, `test_handle_diagnostics.py`] |
| F6-10-00, D2-03-10 window handles | sensor, binary_sensor | Incoming only | `stream/…` | none | never | not verified (may be battery-powered) | supported | code, mock test |
| F6-02-xx, F6-03-xx rockers | event | Incoming only | `stream/telegram/…` | none | never | not verified | supported | code, mock test [`test_event_lifecycle.py`] |
| F6-02-01 + A5-04-01 OPUS motion detector | binary_sensor, sensor | Incoming only | `stream/…` | none | never | not verified | supported | code, mock test [`test_opus_motion_detector.py`] |
| A5-07-01 / 03 SMS presence | binary_sensor, sensor | Incoming only | `stream/…` | none | never | not verified | supported | code, mock test |
| A5-04-01 / 02 / 03 temperature/humidity | sensor | Incoming only | `stream/…` | none | never | not verified | supported | code, mock test |
| D5-00-01 contact | binary_sensor | Incoming only | `stream/…` | none | never | not verified | supported | code, mock test [`test_contact_sensor.py`] |
| F6-05-01 leakage, F6-05-02 RWM smoke | binary_sensor | Incoming only | `stream/…` | none | never | not verified | supported | code, mock test |
| Any device | – | Device configuration read | `get/devices/<DEV>/configuration` | radio (ReCom) | user (admin service `get_device_configuration`) | – | supported | reference (3.2), code |
| Any device | – | Device configuration write | `put/devices/<DEV>/configuration` | radio (ReCom) | user (admin service `set_device_configuration`) | – | supported | reference (3.3), code |
| Any device | – | DDF parameters | `get/devices/<DEV>/parameters` | radio (ReCom) | user (admin service `get_device_parameters`) | – | supported | reference (3.2), code |
| Any device | – | Device profile | `get/devices/<DEV>/profile` | unknown | never (`async_get_device_profile` has no caller) | – | unused | code |
| Any device | – | Diagnostics, icons, entity enablement | – | none | user | – | supported | code, mock test [`test_handle_diagnostics.py`] |

## 3. Audit findings (phase 8, no runtime change)

1. **Reconciliation callers.** `_schedule_reconciliation_queries` has exactly
   two callers: (a) after an accepted command whose fields were not yet
   confirmed by device feedback, (b) after an observed outbound `to` telegram
   that was **not** sent by this integration (bridge-native rules, MyOPUS app,
   HomeKit). Both use the frozen delays `(5, 20)` or the cover delays
   `(10, 20)` after the estimated movement end. Case (b) means third-party
   commands can cause up to two status queries per channel from Home
   Assistant. Its radio effect on HeatArea actuators is unknown. Candidate for
   phase 11 review; no change in phase 8.
   *Phase 11 review:* case (a) now applies to ACK `200` only. A third caller,
   `_handle_deferred_command`, re-arms the two checks of fields left unconfirmed
   by an earlier accepted command when a deferred cover command had stopped
   their timers; it adds no field and no slot. Case (b) is kept unchanged: it is
   the only confirmation path for bridge-native commands and, since phase 11,
   for the eventual delivery of a deferred command. Removing it would leave
   such state unconfirmed; its budget stays two checks per channel.
2. **`async_query_climate_status`** has no caller. Status queries for climate
   come only from reconciliation.
3. **ACK 201 (SoftSmartAck).** `decode_response` treats 200 and 201
   identically; reconciliation is scheduled the same way. Open item O1
   (phase 11). No extra polling may be added to force confirmation.
   *Resolved in phase 11:* `async_send_command` returns
   `CommandDelivery.DEFERRED` for 201; no estimate, no cover travel estimate
   and no status query follow. Requests per deferred command drop from up to
   three (command plus two checks) to one until the gateway transmits; then
   case (b) applies once (protocol contract section 7.1).
4. **Telegram paths.** Live and retained parsing publish nothing. Retained
   `stream/telegram` messages are discarded (asserted in
   `test_traffic_baseline.py`).
5. **Gateway health** never derives a device, status-query or ReCom request
   (asserted for setup, ten idle health ticks and a five-cycle reconnect
   burst).

## 4. Unknowns that block new automatic requests

- Whether `query: status` to D2-01/D2-05 actuators is answered from the gateway
  cache or by a radio exchange (firmware-dependent).
- Which radio traffic a HeatArea `query: status` causes for linked
  (possibly battery-powered) actuators.
- Whether `get/devices` triggers any internal radio refresh on the gateway.
- Actual power source of each installed handle, motion detector and window
  sensor model.

Resolving any of these requires separately approved hardware observation.
