# Volcano Hybrid for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/)
[![Quality scale](https://img.shields.io/badge/quality%20scale-platinum-E5E4E2.svg)](https://developers.home-assistant.io/docs/core/integration-quality-scale/)

Control a **Storz & Bickel Volcano Hybrid** vaporizer from Home Assistant over
Bluetooth LE.

The Volcano Hybrid is a desktop convection vaporizer with a Bluetooth radio
built into the base. This integration talks to that radio directly on your own
network — there is no cloud service, no account, and no vendor app involved.
It exposes the heater, the fan, the target temperature, the screen backlight and
the device's own settings and counters as regular Home Assistant entities, so
they can be used in automations, scripts and dashboards like anything else.

## Features

- **Climate entity** — target temperature, heater on/off and fan on/off in one card
- **Individual switches** for the heater and the fan
- **Screen backlight** as a dimmable light
- **Auto-off delay** as a configurable number entity
- **Diagnostics** — serial number, both firmware versions, hours of operation and
  live connection state
- **Works through ESPHome Bluetooth proxies**, so the vaporizer does not need to
  be in range of the machine running Home Assistant

## Supported devices

| Device | Supported | Notes |
| --- | --- | --- |
| Volcano Hybrid | Yes | The Bluetooth model this integration is built for |
| Volcano Classic | No | No Bluetooth radio — nothing to talk to |
| Venty, Crafty, Mighty | No | Different Bluetooth protocols; untested here |

Developed against firmware `V01.03.00.00` with BLE firmware `V01.00.00.00`. Other
firmware revisions are expected to work — the protocol has been stable — but the two
`Register` switches are disabled by default because their meaning is not confirmed
across revisions.

## Requirements

- Home Assistant 2025.2 or newer
- A Volcano Hybrid (the Bluetooth-equipped Hybrid model — the Classic has no radio)
- Bluetooth reception where the vaporizer lives, from **either**:
  - a Bluetooth adapter on your Home Assistant host, **or**
  - an [ESPHome Bluetooth proxy](https://esphome.io/projects/?type=bluetooth)
    within range
- The [Bluetooth integration](https://www.home-assistant.io/integrations/bluetooth/)
  set up in Home Assistant (it is part of the default configuration)

The vaporizer only accepts one Bluetooth connection at a time. If the Storz &
Bickel phone app is connected, Home Assistant cannot connect, and vice versa.

## Installation

### HACS (recommended)

1. Open **HACS** in Home Assistant.
2. Open the three-dot menu in the top right and choose **Custom repositories**.
3. Add `https://github.com/JustBeanie/hass-volcano-hybrid` with category
   **Integration**, then select **Add**.
4. Search HACS for **Volcano Hybrid** and select **Download**.
5. Restart Home Assistant.

### Manual

1. Download the latest release from the
   [releases page](https://github.com/JustBeanie/hass-volcano-hybrid/releases).
2. Copy the `custom_components/volcano_hybrid` folder into your Home Assistant
   configuration directory, so you end up with
   `<config>/custom_components/volcano_hybrid/manifest.json`.
3. Restart Home Assistant.

## Setup

Switch the vaporizer on and make sure the Storz & Bickel app is not connected to it.

Home Assistant discovers the Volcano Hybrid by itself. Within a minute or so it
appears under **Settings → Devices & services** as a discovered device — select
**Configure** and confirm.

If it does not appear, you can add it manually:

1. Go to **Settings → Devices & services**.
2. Select **Add integration** and search for **Volcano Hybrid**.
3. Pick your vaporizer from the list of devices Home Assistant can currently see.

Either route ends on an optional startup step. Both settings can be changed later —
see below.

## Configuration options

**Settings → Devices & services → Volcano Hybrid → Configure.** Changing either one
reloads the integration so it takes effect immediately.

| Option | Default | Meaning |
| --- | --- | --- |
| Initial temperature | unset | A target temperature in °C, between 40 and 230, written whenever Home Assistant starts or the integration is reloaded. Leave blank to keep whatever the device already has. |
| Start the fan on startup | off | Runs the fan once on startup or reload. It is **not** re-applied when Bluetooth reconnects — that behaviour caused the fan to start on its own after any signal blip. |

There is also a **Reconfigure** option on the same menu. A vaporizer's Bluetooth
address never changes, so there is nothing to re-point; what it does is attempt a
connection right now and tell you whether it succeeded, which is otherwise only
visible in the log.

## How data is updated

The integration holds a **persistent Bluetooth connection** rather than connecting
for each read. Over that connection:

- **Every 10 seconds while the heater or fan is running** it polls the current
  temperature, target temperature, status register and screen brightness. The heat
  block moves fast enough that a longer interval makes the climate card feel broken.
- **Every 60 seconds once both are off.** There is no ramp left to watch, and a
  cooling vaporizer does not need six polls a minute. Pressing a button in Home
  Assistant restores the fast interval immediately.
- **On change**, the device pushes its status register — heater and fan state — as a
  BLE notification, so those update without waiting for the next poll whichever
  interval is in force. The register is still polled as well, so a dropped
  notification cannot leave the heater reading the wrong way round.
- **Every 10 minutes** it re-reads the things that barely change: hours of operation,
  auto-off delay and the two registers. Serial number and firmware are read once per
  connection, since they cannot change while one is open.
- **After a command**, the new state is published optimistically and a refresh is
  requested, so the UI does not snap back while waiting for the next poll.

Connections are established through Home Assistant's Bluetooth manager, so they
follow whichever adapter or ESPHome proxy most recently heard the device. If the
device stops responding the entities go unavailable and the integration keeps
retrying; it does not need a restart to recover.

Because the vaporizer is normally unplugged between sessions, coming back is
treated as the expected case rather than an error. When no adapter or proxy can
hear it, polls fail immediately instead of working through a connection ladder —
so a command pressed during an outage fails straight away rather than hanging.
The moment it advertises again, Home Assistant reconnects, including when the
config entry was left retrying setup because the device was off at startup.

## Use cases

**Step through session temperatures.** Vaporizing the same load repeatedly works
better at a rising temperature. Watch the fan switch, and each time a bag finishes,
bump the target to the next value in a list:

```yaml
triggers:
  - trigger: state
    entity_id: switch.volcano_hybrid_fan
    from: "on"
    to: "off"
actions:
  - action: climate.set_temperature
    target:
      entity_id: climate.volcano_hybrid
    data:
      temperature: >-
        {{ [180, 190, 200, 210, 220]
           | select('>', state_attr('climate.volcano_hybrid', 'temperature') | float)
           | list | first
           | default(220) }}
```

**Tell me when it is ready.** The vaporizer takes a couple of minutes to reach
temperature and it is easy to wander off. Pulse the screen as a visual cue, and
notify as well so it reaches you in another room:

```yaml
triggers:
  - trigger: numeric_state
    entity_id: sensor.volcano_hybrid_temperature
    above: 179
actions:
  - repeat:
      count: 3
      sequence:
        - action: light.turn_on
          target:
            entity_id: light.volcano_hybrid_screen
          data:
            brightness_pct: 100
        - delay:
            seconds: 1
        - action: light.turn_on
          target:
            entity_id: light.volcano_hybrid_screen
          data:
            brightness_pct: 10
        - delay:
            seconds: 1
  - action: notify.persistent_notification
    data:
      message: Volcano is up to temperature.
```

**Fill a bag without standing over it.** Run the fan for a measured time, then shut
everything down:

```yaml
actions:
  - action: switch.turn_on
    target:
      entity_id: switch.volcano_hybrid_fan
  - delay:
      seconds: 36
  - action: switch.turn_off
    target:
      entity_id: switch.volcano_hybrid_fan
  - action: switch.turn_off
    target:
      entity_id: switch.volcano_hybrid_heater
```

A `delay` does not survive a Home Assistant restart. If Home Assistant restarts
mid-bag the fan keeps running until you stop it, so pair this with the auto-off
number entity if that matters to you.

**Never leave it heating.** The device has its own auto-off, but it is capped at
180 minutes and does not know whether you are home:

```yaml
triggers:
  - trigger: state
    entity_id: person.you
    to: not_home
conditions:
  - condition: state
    entity_id: switch.volcano_hybrid_heater
    state: "on"
actions:
  - action: switch.turn_off
    target:
      entity_id: switch.volcano_hybrid_heater
```

More, including the full session script this was originally built around, are in
[`examples/`](examples/).

## Known limitations

These are design constraints, not bugs:

- **One Bluetooth connection at a time.** The vaporizer accepts a single BLE link, so
  Home Assistant and the Storz & Bickel phone app are mutually exclusive. If the app
  is connected, Home Assistant cannot be, and the integration will raise a repair
  issue telling you so.
- **No temperature while idle.** With the heater off the device reports a −18 °C
  placeholder rather than an ambient reading, so the temperature sensor is `unknown`
  until it starts heating. Reporting the placeholder as a real temperature would
  poison your history.
- **`Register 2` and `Register 3` are not labelled.** On the stock firmware these
  appear to control the display during cooling and the vibration alert, but that is
  not confirmed across firmware revisions, so they are named after the registers they
  write and ship disabled by default.
- **Hours of operation only counts hours.** The device exposes minutes separately and
  the sensor reports whole hours, so it steps rather than climbs smoothly.
- **No control the device itself does not expose.** There is no bag-volume sensor, no
  remaining-auto-off countdown and no way to read the physical dial position — the
  vaporizer does not publish them.

## Entities

| Entity | Notes |
| --- | --- |
| `climate.<name>` | Target temperature, heater mode and fan mode |
| `switch.<name>_heater` | Heater on/off |
| `switch.<name>_fan` | Fan on/off |
| `light.<name>_screen` | Screen backlight, dimmable |
| `number.<name>_target_temperature` | 40–230 °C in 5° steps |
| `number.<name>_auto_off_time` | 1–180 minutes |
| `sensor.<name>_temperature` | Current heat-block temperature |
| `binary_sensor.<name>_connection` | Whether Home Assistant holds a Bluetooth link |

Plus diagnostic sensors for the serial number, firmware versions, hours of
operation, brightness and auto-off delay. A few debug entities (raw register,
connection status, heater/fan status text) ship disabled by default and can be
enabled from the device page.

The temperature sensor reports **unknown** while the heater is idle. That is not
a fault: the device reports a −18 °C placeholder when the probe has no live
reading, and surfacing that as a real temperature would poison your history.

This integration provides no custom actions, triggers or conditions — use Home
Assistant's standard ones against the entities above.

## Removal

1. Go to **Settings → Devices & services → Volcano Hybrid**.
2. Open the three-dot menu next to the integration entry and select **Delete**.
   This removes the device and all of its entities.
3. If you installed via HACS, open HACS → **Volcano Hybrid** → three-dot menu →
   **Remove**. For a manual install, delete
   `<config>/custom_components/volcano_hybrid`.
4. Restart Home Assistant.

## Troubleshooting

**The device is never discovered.** Confirm the vaporizer is powered on and that
the phone app is disconnected. Check
**Settings → Devices & services → Bluetooth → ⋯ → Advertisement monitor** and
look for a device named `S&B VOLCANO H`. If nothing shows there, the problem is
Bluetooth reception, not this integration.

**The entry keeps retrying setup.** Home Assistant will not set the integration
up until it can actually reach the device, by design. Check the log for
`is not visible to any Bluetooth adapter or proxy` (reception) versus
`Could not connect` (something else holds the connection).

**Connection drops when you walk away.** If the only adapter is on the Home
Assistant host, range is whatever that host can hear. Adding an ESPHome
Bluetooth proxy in the same room fixes this; the integration automatically
reconnects through whichever adapter or proxy last heard the device.

**Download diagnostics.** Settings → Devices & services → Volcano Hybrid → the
three-dot menu → *Download diagnostics*. This includes the raw bytes read from each
characteristic, which is what actually identifies a decoding problem — every bug
fixed in v2.0.0 was diagnosed from those rather than from entity states. The MAC,
serial number and scanner source are redacted, so it is safe to attach to an issue.

**Enable debug logging:**

```yaml
logger:
  logs:
    custom_components.volcano_hybrid: debug
```

## Dependencies

The integration pulls in one PyPI package,
[`bleak-retry-connector`](https://pypi.org/project/bleak-retry-connector/), which
is the same connection helper Home Assistant's own Bluetooth integrations use.
`bleak` itself comes from Home Assistant.

## Contributing

Issues and pull requests are welcome. Run the checks before opening one:

```bash
python -m pytest tests/ && python -m ruff check . && python -m ruff format --check .
```

## Disclaimer

Not affiliated with or endorsed by Storz & Bickel. The Bluetooth protocol was
determined by observation; use at your own risk.

## License

MIT — see [LICENSE](LICENSE).
