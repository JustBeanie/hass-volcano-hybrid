"""Repairs for the sensors deprecated in 4.4.0.

Seven sensors duplicate something else: four kept only so v1 entity IDs
survived 2.0, and three identity sensors whose values the device page already
shows. They are removed in the Home Assistant 2027 readiness release. Until
then each one that is enabled raises a repair naming its replacement and what
still uses it, and the repair clears the moment the sensor is disabled or
deleted -- no restart needed.
"""

from __future__ import annotations

from homeassistant.components.automation import automations_with_entity
from homeassistant.components.script import scripts_with_entity
from homeassistant.const import Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.helpers.device_registry import format_mac

from .const import DOMAIN, ISSUE_DEPRECATED_SENSOR, ISSUE_DEPRECATED_SENSOR_USED
from .coordinator import StorzBickelConfigEntry

LEARN_MORE_URL = "https://github.com/JustBeanie/hass-volcano-hybrid#deprecated-sensors"

# Each deprecated sensor's key, and what to use instead.
DEPRECATED_SENSORS: dict[str, str] = {
    "connection_status": "the Connection binary sensor, or Link health",
    "heater_status": "the Heater switch",
    "fan_status": "the Fan switch",
    "auto_off_time": "the Auto-off time number",
    "serial_number": "Serial number on the device page",
    "firmware_version": (
        "Firmware on the device page, or the Firmware update entity's installed version"
    ),
    "ble_firmware": "Hardware on the device page",
}


def _issue_id(entry: StorzBickelConfigEntry, key: str) -> str:
    """Return the repair's id for one sensor of one entry."""
    return f"{ISSUE_DEPRECATED_SENSOR}_{key}_{entry.entry_id}"


@callback
def async_setup_deprecations(
    hass: HomeAssistant, entry: StorzBickelConfigEntry
) -> None:
    """Raise a repair for each enabled deprecated sensor, and keep it current.

    Call once the platforms are set up, so the sensors are registered.
    """
    registry = er.async_get(hass)
    mac = format_mac(entry.runtime_data.address)
    # The entity IDs worth reacting to; a rename adds the new one.
    tracked: set[str] = set()

    @callback
    def _async_check_all() -> None:
        for key, replacement in DEPRECATED_SENSORS.items():
            entity_id = registry.async_get_entity_id(
                Platform.SENSOR, DOMAIN, f"{mac}_{key}"
            )
            if entity_id is not None:
                tracked.add(entity_id)
            entity = None if entity_id is None else registry.async_get(entity_id)
            if entity is None or entity.disabled:
                ir.async_delete_issue(hass, DOMAIN, _issue_id(entry, key))
                continue
            _async_raise(hass, entry, key, entity, replacement)

    @callback
    def _async_registry_updated(
        event: Event[er.EventEntityRegistryUpdatedData],
    ) -> None:
        data = event.data
        if data["entity_id"] in tracked or data.get("old_entity_id") in tracked:
            _async_check_all()

    _async_check_all()
    entry.async_on_unload(
        hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, _async_registry_updated)
    )


@callback
def _async_raise(
    hass: HomeAssistant,
    entry: StorzBickelConfigEntry,
    key: str,
    entity: er.RegistryEntry,
    replacement: str,
) -> None:
    """Raise the repair for one enabled sensor, listing what uses it."""
    entity_id = entity.entity_id
    users = sorted(
        {
            *automations_with_entity(hass, entity_id),
            *scripts_with_entity(hass, entity_id),
        }
    )
    placeholders = {
        "entity_id": entity_id,
        "name": entry.title,
        "replacement": replacement,
    }
    if users:
        placeholders["items"] = "\n".join(f"- `{user}`" for user in users)
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id(entry, key),
        # Removing the entry removes its issues with it.
        config_entry_id=entry.entry_id,
        is_fixable=False,
        learn_more_url=LEARN_MORE_URL,
        severity=ir.IssueSeverity.WARNING,
        translation_key=(
            ISSUE_DEPRECATED_SENSOR_USED if users else ISSUE_DEPRECATED_SENSOR
        ),
        translation_placeholders=placeholders,
    )
