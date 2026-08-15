"""Constants for the Volcano Hybrid integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "volcano_hybrid"

DEFAULT_NAME = "Volcano Hybrid"
MANUFACTURER = "Storz & Bickel"
MODEL = "Volcano Hybrid"

# Storz & Bickel's Bluetooth SIG company identifier, as advertised by the device
# and matched in manifest.json.
MANUFACTURER_ID = 1736

# Configuration keys.
CONF_MAC_ADDRESS = "mac_address"
CONF_INITIAL_TEMP = "initial_temperature"
CONF_FAN_ON_CONNECT = "fan_on_connect"

# Repair issues.
ISSUE_CONNECTION_REFUSED = "connection_refused"

# Device limits.
MIN_TEMP = 40
MAX_TEMP = 230
TEMP_STEP = 5
MIN_AUTO_OFF_MINUTES = 1
MAX_AUTO_OFF_MINUTES = 180
DEFAULT_BRIGHTNESS = 70

# The heater ramps fast enough that a slower interval makes the climate card
# feel broken; 10s is a reasonable compromise for a connected BLE device.
ACTIVE_INTERVAL = timedelta(seconds=10)

# Once the heater and fan are both off there is nothing left to watch ramp, and
# polling a cooling vaporiser six times a minute is pure BLE traffic and recorder
# rows. Backing off is only safe because the status register is a notification
# subscription: pressing heat or fan on the device itself still pushes a state
# change straight away, whatever the poll interval says.
IDLE_INTERVAL = timedelta(seconds=60)
