"""Constants for the Storz & Bickel BLE integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "storzandbickel_ble"

# The domain this integration used up to 3.x. Entries left under it are adopted
# on startup so every existing entity_id survives the rename.
LEGACY_DOMAIN = "volcano_hybrid"

DEFAULT_NAME = "Volcano Hybrid"
MANUFACTURER = "Storz & Bickel"

# Storz & Bickel's Bluetooth SIG company identifier, as advertised by the device
# and matched in manifest.json.
MANUFACTURER_ID = 1736

# Configuration keys.
CONF_MAC_ADDRESS = "mac_address"
CONF_INITIAL_TEMP = "initial_temperature"
CONF_FAN_ON_CONNECT = "fan_on_connect"
# Which protocol family the device speaks; absent on entries from before 4.3,
# which are all Volcanos.
CONF_MODEL = "model"
# Whether Home Assistant connects and stays connected on its own. Kept in the
# entry options so it survives a restart, but set from its switch, not the form.
CONF_AUTO_CONNECT = "auto_connect"
CONF_AUTO_CONNECT_DELAY = "auto_connect_delay"
CONF_DELAYED_RECONNECT_DELAY = "delayed_reconnect_delay"

# Seconds to wait after an advertisement before connecting. Long enough for
# every adapter and proxy that heard it to report, so Home Assistant connects
# through the strongest one rather than the first.
DEFAULT_AUTO_CONNECT_DELAY = 1.0
# The delayed reconnect button stays off the link this long. The vaporizer
# advertises about every ten seconds while nothing is connected, so this
# guarantees a fresh advertisement to pick a path from.
DEFAULT_DELAYED_RECONNECT_DELAY = 11.0
# With auto-connect off, a link opened on demand -- a command or a reconnect
# button -- is released once it has been idle this long, so a run of commands
# from a script is not cut off between steps.
ON_DEMAND_GRACE = timedelta(seconds=60)

# Fired on the bus for device triggers; see device_trigger.py.
EVENT_DEVICE = f"{DOMAIN}_event"
TRIGGER_TARGET_REACHED = "target_reached"
TRIGGER_FAULT_RAISED = "fault_raised"
TRIGGER_CONNECTION_LOST = "connection_lost"
TRIGGER_CONNECTION_RESTORED = "connection_restored"
TRIGGER_TYPES = (
    TRIGGER_TARGET_REACHED,
    TRIGGER_FAULT_RAISED,
    TRIGGER_CONNECTION_LOST,
    TRIGGER_CONNECTION_RESTORED,
)

# Repair issues. These describe two different failures and must not be swapped:
# refused is turned away at the connect stage, which is the only shape another
# client holding the link can produce; unresponsive means the vaporizer let us
# in and then answered nothing, which no phone app can cause.
ISSUE_CONNECTION_REFUSED = "connection_refused"
ISSUE_CONNECTION_UNRESPONSIVE = "connection_unresponsive"
# The pre-rename integration is still installed alongside this one.
ISSUE_LEGACY_INSTALLED = "legacy_integration_installed"
# A device another Storz & Bickel integration has could be moved here.
ISSUE_MIGRATE_FROM_OTHER = "migrate_from_other_integration"

# Device limits.
MIN_TEMP = 40
MAX_TEMP = 230
TEMP_STEP = 5
# The lowest auto-off delay offered. The device itself accepts less, so a value
# set lower elsewhere is still read back and shown -- it just cannot be chosen.
MIN_AUTO_OFF_MINUTES = 20
DEVICE_MIN_AUTO_OFF_MINUTES = 1
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
