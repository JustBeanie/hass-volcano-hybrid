"""Constants for the Volcano Hybrid integration."""

DOMAIN = "volcano_hybrid"

# Default values
DEFAULT_NAME = "Volcano Hybrid"

# Configuration
CONF_MAC_ADDRESS = "mac_address"
CONF_INITIAL_TEMP = "initial_temperature"
CONF_FAN_ON_CONNECT = "fan_on_connect"

# Attributes
ATTR_TEMPERATURE = "temperature"
ATTR_TARGET_TEMPERATURE = "target_temperature"
ATTR_HEATER_STATE = "heater_state"
ATTR_FAN_STATE = "fan_state"
ATTR_BRIGHTNESS = "brightness"

# Services
SERVICE_SET_TEMPERATURE = "set_temperature"
SERVICE_START_HEATER = "start_heater"
SERVICE_STOP_HEATER = "stop_heater"
SERVICE_START_FAN = "start_fan"
SERVICE_STOP_FAN = "stop_fan"
SERVICE_SET_BRIGHTNESS = "set_brightness"
SERVICE_FAN_TIMER = "fan_timer"
SERVICE_SCREEN_ANIMATION = "screen_animation"

# Animation types
ANIMATION_NONE = "none"
ANIMATION_BLINKING = "blinking"
ANIMATION_BREATHING = "breathing"
ANIMATION_ASCENDING = "ascending"
ANIMATION_DESCENDING = "descending"

# Temperature limits
MIN_TEMP = 40
MAX_TEMP = 230
TEMP_STEP = 5
