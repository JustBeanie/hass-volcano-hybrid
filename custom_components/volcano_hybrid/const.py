"""Constants for the Volcano Hybrid integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "volcano_hybrid"

DEFAULT_NAME = "Volcano Hybrid"
MANUFACTURER = "Storz & Bickel"
MODEL = "Volcano Hybrid"

# Configuration keys.
CONF_MAC_ADDRESS = "mac_address"
CONF_INITIAL_TEMP = "initial_temperature"
CONF_FAN_ON_CONNECT = "fan_on_connect"

# Repair issues.
ISSUE_CONNECTION_REFUSED = "connection_refused"

# Actions.
SERVICE_FAN_TIMER = "fan_timer"
SERVICE_SCREEN_ANIMATION = "screen_animation"

ATTR_DURATION = "duration"
ATTR_TURN_OFF_HEAT = "turn_off_heat"
ATTR_TURN_OFF_SCREEN = "turn_off_screen"
ATTR_ANIMATION_TYPE = "animation_type"

# Screen animations.
ANIMATION_NONE = "none"
ANIMATION_BLINKING = "blinking"
ANIMATION_BREATHING = "breathing"
ANIMATION_ASCENDING = "ascending"
ANIMATION_DESCENDING = "descending"

ANIMATION_TYPES = [
    ANIMATION_NONE,
    ANIMATION_BLINKING,
    ANIMATION_BREATHING,
    ANIMATION_ASCENDING,
    ANIMATION_DESCENDING,
]

# Device limits.
MIN_TEMP = 40
MAX_TEMP = 230
TEMP_STEP = 5
MIN_AUTO_OFF_MINUTES = 1
MAX_AUTO_OFF_MINUTES = 180
DEFAULT_BRIGHTNESS = 70

MIN_FAN_TIMER_SECONDS = 1
MAX_FAN_TIMER_SECONDS = 3600

# The heater ramps fast enough that a slower interval makes the climate card
# feel broken; 10s is a reasonable compromise for a connected BLE device.
UPDATE_INTERVAL = timedelta(seconds=10)
