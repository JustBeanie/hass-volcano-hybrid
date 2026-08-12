# Volcano Hybrid for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/)
[![Quality scale](https://img.shields.io/badge/quality%20scale-bronze-CD7F32.svg)](https://developers.home-assistant.io/docs/core/integration-quality-scale/)

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
- **Screen backlight** as a dimmable light, plus screen animations
- **Auto-off delay** as a configurable number entity
- **Diagnostics** — serial number, both firmware versions, hours of operation and
  live connection state
- **Works through ESPHome Bluetooth proxies**, so the vaporizer does not need to
  be in range of the machine running Home Assistant

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

Either route ends on an optional startup step:

| Option | Meaning |
| --- | --- |
| Initial temperature | A target temperature written whenever Home Assistant starts or the integration is reloaded. Leave blank to keep whatever the device already has. |
| Start the fan on startup | Runs the fan once, on startup or reload. It is **not** re-applied when Bluetooth reconnects. |

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

## Actions

### `volcano_hybrid.fan_timer`

Runs the fan for a set time, then stops it — optionally switching off the heater
and the screen too.

```yaml
action: volcano_hybrid.fan_timer
target:
  entity_id: climate.volcano_hybrid
data:
  duration: 36
  turn_off_heat: false
  turn_off_screen: false
```

| Field | Required | Description |
| --- | --- | --- |
| `duration` | yes | Seconds to run the fan, 1–3600 |
| `turn_off_heat` | no | Also switch the heater off when the timer expires |
| `turn_off_screen` | no | Also switch the screen off when the timer expires |

A pending timer is cancelled if the integration is reloaded or removed, so it can
never operate the device after Home Assistant has stopped managing it.

### `volcano_hybrid.screen_animation`

Animates the screen brightness — useful as a "your session is ready" cue.

```yaml
action: volcano_hybrid.screen_animation
target:
  entity_id: climate.volcano_hybrid
data:
  animation_type: breathing
```

| Field | Required | Description |
| --- | --- | --- |
| `animation_type` | yes | `none`, `blinking`, `breathing`, `ascending` or `descending` |

Use `none` to stop an animation and restore the default brightness.

This integration provides no custom triggers or conditions; use the standard
state triggers and conditions against its entities.

## Examples

[`examples/`](examples/) contains the "session" automation and script this was
originally built around — stepping the target temperature through a list after
each fan cycle.

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
