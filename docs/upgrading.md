# Upgrading

- [From an MQTT version (0.x)](#from-an-mqtt-version-0x)
- [From the community test versions](#from-the-community-test-versions)

## From an MQTT version (0.x)

Versions up to 0.8 reached the gateway through a Mosquitto bridge and the Home
Assistant MQTT integration. From 1.0 on the integration connects to the gateway
directly. Devices, entities, entity IDs, names, areas, dashboards and
automations are kept; only the connection changes. v0.8.0 is the last MQTT
release, and you can return to it at any time (entities keep their IDs in both
directions).

1. Update the integration and restart Home Assistant.
2. The gateway entry reports **Re-authentication required**, because it does
   not know the gateway's address yet. Open the repair notification or
   **Settings → Devices & services → Opus GreenNet Bridge → Reconfigure** and
   enter the gateway's IP address. Port (`8080`), user (`admin`) and password
   (the EURID) are prefilled with the vendor defaults.
3. The entry loads again with all existing entities.
4. Optional, once everything works: remove the OPUS connection block from your
   Mosquitto configuration (for example `/share/mosquitto/opus_bridge.conf`)
   and restart the broker. Keep the MQTT broker and integration only if other
   devices use them.

The integration checks that the address answers as the configured gateway
(same EURID) and rejects a different gateway.

**Check after updating to 1.0.0b2 or later:** some D2-01 actuator types now
follow the gateway profile. An actuator that changes from switch to light, or
whose channel count changes, gets new entities; update the dashboards and
automations that used the old ones. See
[Switches and dimmers](devices.md#switches-and-dimmers-d2-01).

## From the community test versions

- The community fork's `_illumination` and `_window_handle_lock` unique IDs are
  migrated to the upstream ones. Entity IDs, custom names and settings are
  kept.
- If both the fork entity and a beta entity exist for the same device and
  gateway, the older fork entity is kept and the duplicate beta entity is
  removed. Its references must be updated manually; a repair issue and the log
  name both entity IDs. Entities of other gateways or devices are not touched.
- The unlock-request sensor uses `requested` / `not_requested`; update
  automations that compared its state with boolean text.
- German translations include “Beleuchtungsstärke” for illuminance and readable
  HOPPE unlock-request states. Custom entity names stay unchanged.
- Detector payloads of both the beta and OPUS IQ-DOT firmware 1.21.30 are
  accepted: `alarm` (on/off), boolean `batteryLow`, `motionDetected`,
  `illumination`, and HOPPE `handle`, `unlock` (requested/notRequested) and
  `mechanics` (error/ok).
