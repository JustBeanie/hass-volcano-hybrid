# Volcano Hybrid

Control a Storz & Bickel Volcano Hybrid vaporizer from Home Assistant over
Bluetooth LE. Local only — no cloud, no account, no vendor app.

## Features

- Climate entity for target temperature, heater and fan
- Separate heater and fan switches
- Screen backlight as a dimmable light, plus screen animations
- Configurable auto-off delay
- Diagnostics: serial number, firmware versions, hours of operation, connection state
- Works through ESPHome Bluetooth proxies

## Setup

Switch the vaporizer on and make sure the Storz & Bickel app is not connected to
it. Home Assistant discovers it automatically and offers it under
**Settings → Devices & services**.

Full documentation, actions and troubleshooting are in the
[README](https://github.com/JustBeanie/hass-volcano-hybrid#readme).
