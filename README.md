# Volcano Hybrid Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)

This integration allows you to control your Storz & Bickel Volcano Hybrid vaporizer from Home Assistant.

## Features

- Control heater and fan
- Set target temperature
- Monitor current temperature
- Adjust screen brightness
- Configure auto-off time
- Control vibration and display settings
- Fan timer service
- Screen animation service

## Installation

### HACS Installation (Recommended)

1. Make sure you have [HACS](https://hacs.xyz/) installed in your Home Assistant instance.
2. Add this repository as a custom repository in HACS:
   - Go to HACS > Integrations
   - Click the three dots in the top right corner
   - Select "Custom repositories"
   - Add the URL of this repository
   - Category: Integration
   - Click "Add"
3. Search for "Volcano Hybrid" in HACS and install it
4. Restart Home Assistant

### Manual Installation

1. Download the latest release from GitHub
2. Create a `custom_components` directory in your Home Assistant configuration directory if it doesn't already exist
3. Extract the `volcano_hybrid` directory from the release into the `custom_components` directory
4. Restart Home Assistant

## Configuration

The integration can be configured through the Home Assistant UI:

1. Go to Settings > Devices & Services
2. Click "Add Integration"
3. Search for "Volcano Hybrid"
4. Follow the configuration steps

### Finding Your Device's MAC Address

If you don't know your Volcano Hybrid's MAC address, you can use the included utility script:

1. SSH into your Home Assistant server
2. Navigate to the custom_components directory
3. Run the following command:

\`\`\`bash
python3 volcano_hybrid/utils/get_mac_address.py
\`\`\`

This will scan for nearby Volcano Hybrid devices and display their MAC addresses.

## Entities

The integration creates the following entities:

- **Climate**: Main control for temperature and heater/fan
- **Light**: Control for the screen brightness
- **Number**: 
  - Target temperature
  - Auto-off time
- **Switch**:
  - Heater
  - Fan
  - Register 3 (vibration)
  - Register 2 (display during cooling)
- **Sensor**:
  - Current temperature
  - Connection status
  - Heater status
  - Fan status
  - Brightness value
  - Serial number
  - BLE firmware version
  - Hours of operation
  - Firmware version
  - Auto-off time

## Services

The integration provides the following services:

### Fan Timer

Turn on the fan and set a timer to turn it off after a specified duration.

\`\`\`yaml
service: volcano_hybrid.fan_timer
data:
  duration: 60  # Duration in seconds
  turn_off_heat: false  # Optional, whether to also turn off the heater
  turn_off_screen: false  # Optional, whether to also turn off the screen
\`\`\`

### Screen Animation

Start a screen animation on the Volcano Hybrid.

\`\`\`yaml
service: volcano_hybrid.screen_animation
data:
  animation_type: "breathing"  # Options: none, blinking, breathing, ascending, descending
\`\`\`

## Troubleshooting

- Make sure your Volcano Hybrid is powered on and in range
- Check that Bluetooth is enabled on your Home Assistant device
- If you're having connection issues, try restarting your Volcano Hybrid
- Check the Home Assistant logs for detailed error messages

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the LICENSE file for details.
