# OPUS GreenNet for Home Assistant

> **This repository is an independent fork of [kegelmeier/ha-opus-greennet](https://github.com/kegelmeier/ha-opus-greennet).**
>
> The upstream project was created and is maintained by [@kegelmeier](https://github.com/kegelmeier). This fork adds independently maintained device support and fixes. Please see the [LICENSE](LICENSE) file for the applicable license terms; the upstream copyright and license notices are retained.

A Home Assistant custom integration for OPUS GreenNet / OPUS IQ devices.

## Requirements

- Home Assistant **2026.8 or newer**
- An OPUS-IQ-DOT gateway
- For the gateway health check introduced in v0.3.4: OPUS-IQ-DOT firmware **v1.21 or newer**

## Installation

1. In Home Assistant, open HACS.
2. Select **Integrations** and open the three-dot menu.
3. Choose **Custom repositories**.
4. Add `https://github.com/fubu2k/ha-opus-greennet` as an **Integration** repository.
5. Search for **OPUS GreenNet** in HACS and install it.
6. Restart Home Assistant.
7. Add the integration via **Settings → Devices & services → Add integration**.

## Fork and upstream

This project is a fork of [kegelmeier/ha-opus-greennet](https://github.com/kegelmeier/ha-opus-greennet).

- Upstream: [kegelmeier/ha-opus-greennet](https://github.com/kegelmeier/ha-opus-greennet)
- This fork: [fubu2k/ha-opus-greennet](https://github.com/fubu2k/ha-opus-greennet)

Please report issues specific to the changes in this fork here. For functionality unchanged from upstream, checking the upstream project's issues first may be helpful.

## Release v0.3.4.2

Added three `binary_sensor` entities per device:
  - `binary_sensor.*_handle_closed` — on when handle is in closed position
  - `binary_sensor.*_handle_open` — on when handle is in open position
  - `binary_sensor.*_handle_tilt` — on when handle is in tilted position
 
Windows Handle are also now compatible with Cover Control Automation (CCA) with Binary Sensor.

## Release v0.3.4.1

This release builds on v0.3.4, the first independently versioned release of this fork, based on [kegelmeier/ha-opus-greennet](https://github.com/kegelmeier/ha-opus-greennet).

### Bug fixes

- Gateway health-probe timeout eliminated: the integration now uses `get/config/system/uptime` instead of the unsupported `get/config/system/info`. The uptime endpoint is supported by OPUS-IQ-DOT firmware v1.21 and newer, so Home Assistant no longer logs a 10-second timeout warning at every start.
- HOPPE AutoLock writeback blocked: `async_lock()` and `async_unlock()` now raise `HomeAssistantError` immediately. This prevents a simulated local state change when no MQTT command can actually be sent.

### New device support

- **D2-06-40 — HOPPE window handle with AutoLock:** read-only lock entity; `handle_state` and `unlock_request` sensors; `mechanics_fault` binary sensor.
- **F6-10-00 and D2-03-10 — Passive HOPPE window handles:** `handle_state` sensor.
- **F6-05-02 — Jaeger Direkt / OPUS smoke detector RWM:** `smoke_alarm` and `battery_low` binary sensors.
- **A5-07-03 — Jaeger Direkt / OPUS SMS presence sensor:** motion binary sensor plus `illuminance`, `supply_voltage`, and `battery_level` sensors.
- **A5-07-01 — OPUS SMS Presence Detector:** same motion binary sensor and `illuminance`, `supply_voltage`, and `battery_level` sensors as A5-07-03.

### Changes in v0.3.4.1

- `feat`: A5-07-01 support added via the existing A5-07-03 presence-detector logic; no MQTT parsing changes.

### Changes in v0.3.4

- `fix`: HOPPE read-only writeback and replacement of the `/info` probe with `/uptime` by [@fubu2k](https://github.com/fubu2k).
- `feat/new_devices` + `fix`: new device support, HOPPE writeback protection, and uptime probe, merged to `main` by [@fubu2k](https://github.com/fubu2k).

## License

This fork is distributed under the license included in the repository's [LICENSE](LICENSE) file. Forking does not replace the original project's copyright, attribution, or license obligations. The upstream project and its contributors remain credited for their respective work.

## Credits

- Original project: [@kegelmeier](https://github.com/kegelmeier) and contributors
- Fork maintenance and v0.3.4.1 changes: [@fubu2k](https://github.com/fubu2k)
