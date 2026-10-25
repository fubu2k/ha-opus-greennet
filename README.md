<div align="center">

<img src="https://raw.githubusercontent.com/kegelmeier/ha-opus-greennet/main/custom_components/opus_greennet/brand/logo.png" alt="Opus GreenNet Bridge" width="180">

# Opus GreenNet Bridge

Local Home Assistant integration for **OPUS greenNet** EnOcean smart homes,
connected directly to the OPUS gateway's EnOcean over IP REST API.

[![HACS Custom][hacs-shield]][hacs]
[![GitHub Release][release-shield]][releases]
[![License][license-shield]][license]
![HA Min Version][ha-shield]

[![Open in HACS][hacs-repo-badge]][hacs-repo]

</div>

> [!NOTE]
> **Version 1.0 connects to the gateway directly over its REST API** instead of
> MQTT. Updating from 0.x keeps all devices and entities; see
> [Upgrading](docs/upgrading.md). v0.8.0 stays available as the last MQTT
> release.

## Features

- **Local and fast.** One event stream from the gateway delivers every telegram
  within a fraction of a second, including wall switches, rocker presses and
  commands from the OPUS app or HomeKit. No cloud, no MQTT broker.
- **Automatic discovery** of the gateway in the local network and of all
  devices paired with it; newly paired devices appear without a restart.
  Upgrades from the MQTT versions find the gateway on their own.
- **Lights, switches, blinds, heating areas, sensors and window handles**, with
  per-button rocker events for automations.
- **Reliable commands.** Every command waits for the gateway's acknowledgement
  and is confirmed with status checks.
- **Recovers on its own** after network outages or gateway restarts and
  resynchronises every device.
- **Diagnostics** with redacted downloads, plus actions for device
  configuration (ReCom).
- **UI setup** with re-authentication and reconfiguration; English and German.

## Supported devices

| Device | EEP | Home Assistant entities |
|--------|-----|-------------------------|
| Switch actuators | D2-01-00, -01, -06 … -0F (1 channel); D2-01-10, -11, -12 (2 channels) | Switch per channel, actuator buttons |
| Dimmers | D2-01-02 … -05, -09; A5-38-08 | Light |
| Gateway switching | A5-38-09 | Light |
| Blinds and shutters | D2-05-00, -01, -02 | Cover with position and tilt; blockage, reference run, last run time |
| Heating areas | D1-4B-05, -06, -07 (Valve, CosiTherm, Electro) | Climate, feed temperature, power, humidity, window open, errors, battery |
| Temperature and humidity sensors | A5-04-01, -02, -03 | Temperature, humidity |
| Presence detectors (SMS) | A5-07-01, A5-07-03 | Motion, illuminance (A5-07-03), supply voltage, sensor error |
| OPUS motion detector | F6-02-01 + A5-04-01 | Motion, temperature, humidity |
| Smoke detectors (RWM) | F6-05-02 | Smoke alarm, low battery |
| Water sensors | F6-05-01 | Moisture |
| Contacts | D5-00-01 | Contact |
| HOPPE window handles | D2-06-40, F6-10-00, D2-03-10 | Handle position, open / tilted / closed, battery; AutoLock lock (D2-06-40) |
| Rocker switches | F6-02-01, -02, -03; F6-03-01, -02 | Button events |

Signal strength and battery level are available where the gateway reports them.
Every device also has a diagnostic **Last seen** time (disabled by default).

The gateway's own device shows its **Uptime** as a diagnostic sensor in days,
hours, minutes and seconds (for example `1 d 2 h 3 min 4 s`, plus the seconds as
attribute `uptime_seconds`). It is read by the health probe once a minute and
updated after each reading; it is not extrapolated in between.
Details per device type: [docs/devices.md](docs/devices.md).

## Compatibility

| | Requirement |
|---|---|
| Home Assistant | 2026.10.1 or newer |
| Gateway | OPUS greenNet gateway with the REST API on port 8080 (tested with firmware OPUS-GW v1.21.31) |
| Network | Home Assistant and the gateway in the same local network. Discovery uses mDNS; without it (for example Docker without host networking) enter the address once |
| Devices | Paired with the gateway in the OPUS app |

No MQTT broker, MQTT integration or Mosquitto bridge is needed.

## Installation

### HACS (recommended)

1. Install [HACS](https://hacs.xyz) if you haven't yet.
2. Add this repository as a custom repository of type **Integration**:

   [![Open in HACS][hacs-repo-badge]][hacs-repo]

   …or in HACS open **⋮ → Custom repositories**, paste
   `https://github.com/kegelmeier/ha-opus-greennet` and choose **Integration**.
3. Search for **Opus GreenNet Bridge**, download it and restart Home Assistant.

To try a beta, enable pre-release versions for this repository in HACS first.

### Manual

1. Copy `custom_components/opus_greennet` into the `custom_components` folder of
   your Home Assistant configuration.
2. Restart Home Assistant.

> [!IMPORTANT]
> A manual copy does not check the Home Assistant version. On a version older
> than 2026.10.1 setup fails with `ImportError: cannot import name
> 'ATTR_BRIGHTNESS'`.

## Configuration

Home Assistant discovers the gateway in the local network: it appears under
**Settings → Devices & services → Discovered** with its address filled in, and
you only enter the password. To add it by hand, use

[![Add Integration][config-flow-badge]][config-flow]

…or **Settings → Devices & services → Add integration → Opus GreenNet Bridge**:

| Field | Description |
|-------|-------------|
| **Host** | IP address or host name of the gateway. It must be in the local network (see below) |
| **Port** | Port of the REST API, `8080` by default |
| **Username** | API user, `admin` by default |
| **Password** | API password, by default the gateway's EURID in capital letters (printed on the gateway; fictional example: `A1B2C3D4`) |

The gateway's EURID becomes the stable identifier of the entry, its devices and
entities. If the gateway gets a new IP address, discovery updates the entry
automatically. To change the credentials, use **Reconfigure** on the entry.

**Local addresses only:** the gateway speaks unencrypted HTTP, so Home Assistant
sends its credentials only to private addresses (`10/8`, `172.16/12`,
`192.168/16`), link-local (`169.254/16`, `fe80::/10`), unique local
(`fc00::/7`), loopback and carrier-grade NAT (`100.64/10`, used by VPN meshes).
A host name must resolve only to such addresses. For remote access use a VPN,
not a port forward. Entries that already use a public address keep working and
log a warning at setup.

**Upgrading from an MQTT version (0.x):** nothing to enter in most cases. At the
first start the integration finds the gateway in the local network and
connects with the default credentials; all entities are kept. It asks only if
the gateway cannot be found or its password was changed. See
[docs/upgrading.md](docs/upgrading.md).

## Troubleshooting

- **All entities unavailable:** the gateway is not reachable. Check its power
  and address; `http://<gateway>:8080/system/uptime` must answer.
- **Entities available, but a device does not react:** a radio problem (range,
  power, pairing). The debug log shows the status checks after each command.
- **A new device is missing:** it appears within 30 minutes; reloading the
  entry adds it at once.

Before reporting a problem, download the redacted diagnostics (**⋮ → Download
diagnostics** on the integration entry). More in
[docs/troubleshooting.md](docs/troubleshooting.md).

## Known limitations

- An acknowledgement confirms the gateway, not the actuator; the actual state
  follows from the device's report or a status check.
- Cover positions during movement are estimated from the configured travel
  time.
- The gateway API is plain HTTP with basic authentication (the gateway has no
  TLS). Keep the gateway in a trusted network; new or changed addresses must be
  local.
- Devices cannot be added to or removed from the gateway yet, and device
  settings are only available as actions (see [Roadmap](#roadmap)).

All limitations: [docs/how-it-works.md](docs/how-it-works.md#known-limitations).

## Roadmap

| Status | Topic |
|--------|-------|
| ✅ Released | 1.0 with the REST API instead of MQTT |
| 🚧 In progress | Device settings as Home Assistant entities (blind travel, rotation and maximum holding times, dimmer ramping and press times and switch timers, warning and illumination threshold released as numbers; switch operation mode, LED and external input released as selects; more settings planned) |
| 🔍 Investigating | Adding and removing devices from Home Assistant |
| 💡 Idea | HTTPS if the gateway offers it |

### Device settings

The OPUS app configures every actuator and the gateway. The gateway already
reports the current values, and the integration can read and write them with
the ReCom actions. The travel and rotation times and the maximum holding time
of blinds, the ramping, short-press and long-press time of D2-01-03 dimmers and
the timer durations, switch-off warning, its signal duration and the
illumination threshold of D2-01-01/-11 switches are already offered as number
entities, and the operation mode, LED colour, LED channel and external input
function of these switches as select entities (all disabled by default). The
plan is to offer the other settings as configuration entities on each device in
the same way, so they can be changed in the UI and in automations:

| Device | Settings |
|--------|----------|
| Switch actuators (D2-01-00/-01, 2-channel D2-01-11) | Function of rocker A and B (channel assignment), ViNET routing time (timer durations, switch-off warning, its signal duration and illumination threshold are available as numbers; operation mode, LED colour, LED channel and external input function as selects) |
| Blinds (D2-05-02) | Function of rocker A and B, ViNET routing time (vertical movement time, rotation time and maximum holding time are available as numbers) |
| All actuators | Repeater mode; linked wall switches and their role (link tables, e.g. central on/off or up/down) |
| Gateway | Firmware, app and API version; services such as HomeKit, Alexa, MQTT, InfluxDB, scheduler and heating zones; received telegrams and average signal strength |

Settings are written over the radio, so a change can take several seconds and
needs the actuator to answer.

### Adding and removing devices

Today new devices are taught in with the OPUS app, and deleting a device in Home
Assistant does not remove it from the gateway. The EnOcean over IP REST API
standard has no endpoints for this. The gateway advertises additional methods,
and OPUS's own web tools use vendor extensions of the local REST API. The next
step is to record what the OPUS app sends to the gateway when it teaches in and
removes a device. If that turns out to be local and repeatable, the integration
could offer:

- **Add device:** a 60-second learn mode (press the learn button on the
  device), or manual entry of EURID and EEP for sensors and wall switches.
- **Remove device:** the existing **Delete** also removes the device from the
  gateway after a confirmation, and clears the actuator's link to it.

## Documentation

| Page | Contents |
|------|----------|
| [Devices](docs/devices.md) | Behaviour per device type, removing devices, actions |
| [Examples and use cases](docs/examples.md) | Automations, actions and typical uses |
| [How it works](docs/how-it-works.md) | Updates, availability, commands, all known limitations, REST API |
| [Troubleshooting](docs/troubleshooting.md) | Diagnosis, diagnostics, debug logging, update lag |
| [Upgrading](docs/upgrading.md) | From the MQTT versions and the community test versions |
| [Development](docs/development.md) | Project structure, tests, CI, beta testing, contributing |
| [Changelog](CHANGELOG.md) | Changes per release |

## Removing the integration

1. **Settings → Devices & services → Opus GreenNet Bridge → ⋮ → Delete** removes
   the entry, its devices and entities. The gateway and the pairing of the
   EnOcean devices are not changed.
2. Remove the integration in HACS (or delete `custom_components/opus_greennet`)
   and restart Home Assistant.

## Contributing

Bug reports, test results from real devices and pull requests are welcome. See
[docs/development.md](docs/development.md#contributing) before opening a pull
request.

This is a community project. It is not affiliated with or endorsed by OPUS
greenNet, Jaeger Direkt or the EnOcean Alliance.

## License

Released under the [MIT License](LICENSE).

<!-- badges -->
[hacs]: https://hacs.xyz
[hacs-shield]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge
[releases]: https://github.com/kegelmeier/ha-opus-greennet/releases
[release-shield]: https://img.shields.io/github/v/release/kegelmeier/ha-opus-greennet?style=for-the-badge
[license]: https://github.com/kegelmeier/ha-opus-greennet/blob/main/LICENSE
[license-shield]: https://img.shields.io/github/license/kegelmeier/ha-opus-greennet?style=for-the-badge
[ha-shield]: https://img.shields.io/badge/Home%20Assistant-2026.10.1%2B-41BDF5.svg?style=for-the-badge&logo=home-assistant&logoColor=white
[hacs-repo]: https://my.home-assistant.io/redirect/hacs_repository/?owner=kegelmeier&repository=ha-opus-greennet&category=integration
[hacs-repo-badge]: https://my.home-assistant.io/badges/hacs_repository.svg
[config-flow]: https://my.home-assistant.io/redirect/config_flow_start/?domain=opus_greennet
[config-flow-badge]: https://my.home-assistant.io/badges/config_flow_start.svg
