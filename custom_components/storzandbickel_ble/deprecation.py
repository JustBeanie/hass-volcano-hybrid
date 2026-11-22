"""Repairs for the sensors deprecated in 4.4.0.

Seven sensors duplicate something else: four kept only so v1 entity IDs
survived 2.0, and three identity sensors whose values the device page already
shows. They are removed in the Home Assistant 2027 readiness release. Until
then a repair is raised only where there is something to do: an enabled sensor
that an automation or script still uses. It names the replacement and what
uses it, and clears once nothing does or the sensor is disabled or deleted --
no restart needed. A sensor nothing uses raises nothing; 4.4.0 raised one for
every enabled sensor, which asked for action where there was none.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.automation import automations_with_entity
from homeassistant.components.script import scripts_with_entity
from homeassistant.const import EVENT_CALL_SERVICE, SERVICE_RELOAD, Platform
from homeassistant.core import (
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
)
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.event import (
    async_track_state_added_domain,
    async_track_state_removed_domain,
)

from .const import DOMAIN, ISSUE_DEPRECATED_SENSOR, ISSUE_DEPRECATED_SENSOR_USED
from .coordinator import StorzBickelConfigEntry

_LOGGER = logging.getLogger(__name__)

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

# What can use a sensor, as far as Home Assistant can tell. Dashboards cannot
# be checked; the README says to look at those by hand.
USER_DOMAINS = ("automation", "script")
# How long automations and scripts are left to settle before checking again.
# A removed one is still listed while its removal is being announced, and a
# reload or startup adds and removes many at once; one check covers them all.
USERS_SETTLE = 1.0


def _issue_id(entry: StorzBickelConfigEntry, key: str) -> str:
    """Return the repair's id for one sensor of one entry."""
    return f"{ISSUE_DEPRECATED_SENSOR}_{key}_{entry.entry_id}"


@callback
def async_setup_deprecations(
    hass: HomeAssistant, entry: StorzBickelConfigEntry
) -> None:
    """Raise a repair for each deprecated sensor in use, and keep it current.

    Call once the platforms are set up, so the sensors are registered. What
    uses a sensor changes when an automation or script is added, edited or
    removed. The editors reload on every save and delete, and the ones that
    load after this entry at startup are added as entities, so either is the
    signal to check again. A removal is not reliably an entity going away:
    the state of a deleted automation can stay behind, hence the reload.
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
            if entity is None or entity.disabled or not (users := _users(hass, entity)):
                ir.async_delete_issue(hass, DOMAIN, _issue_id(entry, key))
                continue
            _async_raise(hass, entry, key, entity, replacement, users)

    @callback
    def _async_registry_updated(
        event: Event[er.EventEntityRegistryUpdatedData],
    ) -> None:
        data = event.data
        if data["entity_id"] in tracked or data.get("old_entity_id") in tracked:
            _async_check_all()

    debouncer = Debouncer(
        hass,
        _LOGGER,
        cooldown=USERS_SETTLE,
        immediate=False,
        function=_async_check_all,
    )
    entry.async_on_unload(debouncer.async_shutdown)

    @callback
    def _async_users_changed(_event: Event[EventStateChangedData]) -> None:
        debouncer.async_schedule_call()

    @callback
    def _async_is_reload(data: dict[str, Any]) -> bool:
        return data.get("domain") in USER_DOMAINS and (
            data.get("service") == SERVICE_RELOAD
        )

    @callback
    def _async_reloaded(_event: Event[dict[str, Any]]) -> None:
        debouncer.async_schedule_call()

    _async_check_all()
    entry.async_on_unload(
        hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, _async_registry_updated)
    )
    for track in (async_track_state_added_domain, async_track_state_removed_domain):
        entry.async_on_unload(track(hass, USER_DOMAINS, _async_users_changed))
    entry.async_on_unload(
        hass.bus.async_listen(
            EVENT_CALL_SERVICE, _async_reloaded, event_filter=_async_is_reload
        )
    )


def _users(hass: HomeAssistant, entity: er.RegistryEntry) -> list[str]:
    """Return the automations and scripts that use ``entity``, sorted."""
    entity_id = entity.entity_id
    return sorted(
        {
            *automations_with_entity(hass, entity_id),
            *scripts_with_entity(hass, entity_id),
        }
    )


@callback
def _async_raise(
    hass: HomeAssistant,
    entry: StorzBickelConfigEntry,
    key: str,
    entity: er.RegistryEntry,
    replacement: str,
    users: list[str],
) -> None:
    """Raise the repair for one sensor, listing what uses it."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id(entry, key),
        # Removing the entry removes its issues with it.
        config_entry_id=entry.entry_id,
        is_fixable=False,
        learn_more_url=LEARN_MORE_URL,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_DEPRECATED_SENSOR_USED,
        translation_placeholders={
            "entity_id": entity.entity_id,
            "name": entry.title,
            "replacement": replacement,
            "items": "\n".join(f"- `{user}`" for user in users),
        },
    )
