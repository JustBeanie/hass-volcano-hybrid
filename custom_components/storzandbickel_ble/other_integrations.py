"""Moving a device over from another Storz & Bickel integration.

Two other integrations control the same devices: SavageNL's, which uses the
``volcano_hybrid`` domain this integration used up to 3.x, and Chuffnugget's
``volcano_integration``. A device set up in one of them can be moved here
without losing its entity IDs. For each one found, a repair offers the move.
It is never done unasked, since it takes the device out of the other
integration.

Entity IDs survive only where an entity here means the same thing, in the
same unit, as one there; the tables below list exactly those. The rest of
the other integration's entities go with its entry, and this integration
creates its own.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import logging

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)

from .const import CONF_MODEL, DOMAIN, ISSUE_MIGRATE_FROM_OTHER, LEGACY_DOMAIN
from .families import Family
from .migration import is_own_legacy_entry, move_registry_rows

_LOGGER = logging.getLogger(__name__)

# Set on an entry created by a move; setup consumes it. Kept in the entry, not
# in memory, so a restart part-way through finishes the move.
CONF_MOVE_FROM = "move_from"

# (entity platform, their key) -> our key. A value may depend on the family:
# the Volcano's vibration switch kept the "register3" key of an older release.
type KeyMap = Mapping[tuple[str, str], str | Mapping[Family, str]]

SAVAGENL_KEYS: KeyMap = {
    ("climate", "volcano"): "climate",
    ("sensor", "rssi"): "rssi",
    ("sensor", "connected_addr"): "connected_via",
    ("sensor", "mains_voltage"): "mains_voltage",
    ("sensor", "last_fault"): "last_fault",
    ("sensor", "prj1"): "raw_register",
    ("sensor", "prj2"): "status_register_2",
    ("sensor", "prj3"): "status_register_3",
    ("sensor", "prj4"): "status_register_4",
    ("sensor", "prj5"): "status_register_5",
    ("sensor", "hist1"): "error_history_1",
    ("sensor", "hist2"): "error_history_2",
    ("sensor", "battery"): "battery",
    ("sensor", "auto_off_countdown"): "auto_off_countdown",
    ("sensor", "heater_mode"): "heater_mode",
    ("sensor", "charging_time"): "charging_time",
    ("sensor", "color"): "colour",
    ("binary_sensor", "at_temperature"): "ready",
    ("binary_sensor", "actuator_fault"): "heater_pump_fault",
    ("binary_sensor", "service_mode"): "service_mode",
    ("binary_sensor", "connected"): "connection",
    ("binary_sensor", "boost_mode"): "boost",
    ("binary_sensor", "superboost_mode"): "superboost",
    ("binary_sensor", "error"): "error",
    ("binary_sensor", "needs_factory_reset"): "needs_factory_reset",
    ("binary_sensor", "charging"): "charging",
    ("binary_sensor", "bootloader_mode"): "bootloader",
    (
        "switch",
        "vibration",
    ): {
        Family.VOLCANO_HYBRID: "register3",
        Family.CRAFTY: "vibration",
        Family.VENTY: "vibration",
        Family.VEAZY: "vibration",
    },
    ("switch", "display_on_cooling"): "register2",
    ("switch", "auto_connect"): "auto_connect",
    ("switch", "charge_led"): "charge_led",
    ("switch", "auto_ble_shutdown"): "auto_ble_shutdown",
    ("switch", "charge_optimization"): "charge_optimisation",
    ("switch", "charge_limit"): "charge_limit",
    ("switch", "boost_visualization"): "boost_visualisation",
    ("switch", "boost_timeout_disabled"): "boost_timeout_disabled",
    ("switch", "permanent_bluetooth"): "permanent_bluetooth",
    ("number", "shut_off"): "auto_off_time",
    ("number", "led_brightness"): {Family.CRAFTY: "led_brightness"},
    ("number", "brightness"): "display_brightness",
    ("number", "boost_temp"): "boost_temperature",
    ("number", "superboost_temp"): "superboost_temperature",
    ("button", "reconnect"): "reconnect",
    ("button", "delayed_reconnect"): "delayed_reconnect",
    ("button", "find_device"): "find",
    ("update", "firmware"): "firmware",
}

CHUFFNUGGET_KEYS: KeyMap = {
    ("sensor", "current_temperature"): "temperature",
    ("sensor", "serial_number"): "serial_number",
    ("sensor", "firmware_version"): "firmware_version",
    ("sensor", "ble_firmware_version"): "ble_firmware",
    ("sensor", "hours_of_operation"): "hours_operation",
    ("switch", "vibration"): "register3",
    ("number", "heater_temperature_setpoint"): "target_temperature",
    ("number", "auto_shutoff_minutes"): "auto_off_time",
}


@dataclass(frozen=True, kw_only=True)
class OtherIntegration:
    """How one other integration stores a device and names its entities."""

    domain: str
    name: str
    address_key: str
    keys: KeyMap
    # Their entity key, from a unique_id and the device's address; None for
    # an entity that is not one of theirs.
    key_from: Callable[[str, str], str | None]
    # Whether an entry on ``domain`` was made by this integration.
    owns: Callable[[ConfigEntry], bool]

    def address(self, entry: ConfigEntry) -> str | None:
        """Return the device address an entry of theirs points at."""
        address = entry.data.get(self.address_key)
        return address if isinstance(address, str) else None

    def family(self, entry: ConfigEntry) -> Family:
        """Return the device family; Chuffnugget's only does the Volcano."""
        try:
            return Family(entry.data.get(CONF_MODEL, Family.VOLCANO_HYBRID))
        except ValueError:
            return Family.VOLCANO_HYBRID

    def our_key(self, platform: str, key: str, family: Family) -> str | None:
        """Return our key for one of their entities, or None for no match."""
        mapped = self.keys.get((platform, key))
        if isinstance(mapped, Mapping):
            return mapped.get(family)
        return mapped


def _savagenl_key(unique_id: str, address: str) -> str | None:
    prefix = f"{address}-"
    return unique_id[len(prefix) :] if unique_id.startswith(prefix) else None


def _chuffnugget_key(unique_id: str, address: str) -> str | None:
    prefix, suffix = "volcano_", f"_{address}".upper()
    if unique_id.startswith(prefix) and unique_id.upper().endswith(suffix):
        return unique_id[len(prefix) : -len(suffix)]
    return None


OTHER_INTEGRATIONS = (
    OtherIntegration(
        domain=LEGACY_DOMAIN,
        name="Volcano Hybrid (SavageNL)",
        address_key=CONF_ADDRESS,
        keys=SAVAGENL_KEYS,
        key_from=_savagenl_key,
        # Our own pre-rename entries share the domain; they are adopted on
        # their own, automatically, by migration.py.
        owns=lambda entry: not is_own_legacy_entry(entry),
    ),
    OtherIntegration(
        domain="volcano_integration",
        name="Volcano Integration (Chuffnugget)",
        address_key="bt_address",
        keys=CHUFFNUGGET_KEYS,
        key_from=_chuffnugget_key,
        owns=lambda _entry: True,
    ),
)


def _other_of(entry: ConfigEntry) -> OtherIntegration | None:
    """Return the other integration that made ``entry``, if any."""
    for other in OTHER_INTEGRATIONS:
        if entry.domain == other.domain and other.owns(entry):
            return other
    return None


def _issue_id(entry_id: str) -> str:
    return f"{ISSUE_MIGRATE_FROM_OTHER}_{entry_id}"


def async_offer_moves(hass: HomeAssistant) -> None:
    """Raise a repair for each device another integration has that we do not."""
    ours = {
        dr.format_mac(entry.data[CONF_ADDRESS])
        for entry in hass.config_entries.async_entries(DOMAIN)
    }
    offered: set[str] = set()
    for other in OTHER_INTEGRATIONS:
        for entry in hass.config_entries.async_entries(other.domain):
            address = other.address(entry)
            if not other.owns(entry) or not address or dr.format_mac(address) in ours:
                continue
            offered.add(_issue_id(entry.entry_id))
            ir.async_create_issue(
                hass,
                DOMAIN,
                _issue_id(entry.entry_id),
                is_fixable=True,
                is_persistent=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_MIGRATE_FROM_OTHER,
                translation_placeholders={
                    "name": entry.title,
                    "integration": other.name,
                },
                data={"entry_id": entry.entry_id},
            )

    # An offer whose entry has gone, or was moved already, is withdrawn.
    for domain, issue_id in list(ir.async_get(hass).issues):
        if (
            domain == DOMAIN
            and issue_id.startswith(f"{ISSUE_MIGRATE_FROM_OTHER}_")
            and issue_id not in offered
        ):
            ir.async_delete_issue(hass, DOMAIN, issue_id)


async def async_start_move(hass: HomeAssistant, entry_id: str) -> bool:
    """Begin moving another integration's entry here; False if it is gone.

    The other entry is unloaded first: that releases the Bluetooth link,
    which only one integration can hold, and takes its entities out of the
    state machine so their registry rows can move. The rows move when the new
    entry sets up, before any of its own entities exist.
    """
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or (other := _other_of(entry)) is None:
        return False
    address = other.address(entry)
    if address is None:
        return False

    if entry.state is ConfigEntryState.LOADED:
        await hass.config_entries.async_unload(entry_id)

    await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_IMPORT},
        data={
            CONF_ADDRESS: address,
            "title": entry.title,
            "options": {},
            CONF_MODEL: other.family(entry).value,
            CONF_MOVE_FROM: entry_id,
        },
    )
    ir.async_delete_issue(hass, DOMAIN, _issue_id(entry_id))
    return True


async def async_finish_move(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Move the registry rows a pending move names onto ``entry``.

    Runs in setup before any platform is forwarded, like the legacy adoption,
    so no entity of ours exists yet to collide with the ones coming across.
    """
    if (source_id := entry.data.get(CONF_MOVE_FROM)) is None:
        return
    source = hass.config_entries.async_get_entry(source_id)
    other = _other_of(source) if source else None
    if source is not None and other is not None:
        address = other.address(source) or entry.data[CONF_ADDRESS]
        family = Family(entry.data.get(CONF_MODEL, Family.VOLCANO_HYBRID))
        mac = dr.format_mac(address)

        def _unique_id_for(row: er.RegistryEntry) -> str | None:
            key = other.key_from(row.unique_id, address)
            ours = other.our_key(row.domain, key, family) if key else None
            return f"{mac}_{ours}" if ours else None

        move_registry_rows(hass, source, entry, mac, _unique_id_for)
        await hass.config_entries.async_remove(source_id)
        _LOGGER.info("Moved %s from %s", source.title, other.name)

    hass.config_entries.async_update_entry(
        entry,
        data={key: value for key, value in entry.data.items() if key != CONF_MOVE_FROM},
    )
