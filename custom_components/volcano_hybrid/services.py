"""Actions for the Volcano Hybrid integration.

Registered from ``async_setup`` so they exist whether or not a config entry is
currently loaded, per the ``action-setup`` quality-scale rule.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_AREA_ID, ATTR_DEVICE_ID, ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
import voluptuous as vol

from .const import (
    ANIMATION_NONE,
    ANIMATION_TYPES,
    ATTR_ANIMATION_TYPE,
    ATTR_DURATION,
    ATTR_TURN_OFF_HEAT,
    ATTR_TURN_OFF_SCREEN,
    DOMAIN,
    MAX_FAN_TIMER_SECONDS,
    MIN_FAN_TIMER_SECONDS,
    SERVICE_FAN_TIMER,
    SERVICE_SCREEN_ANIMATION,
)
from .coordinator import VolcanoDataUpdateCoordinator

FAN_TIMER_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Optional(ATTR_DEVICE_ID): cv.ensure_list,
        vol.Optional(ATTR_AREA_ID): cv.ensure_list,
        vol.Required(ATTR_DURATION): vol.All(
            vol.Coerce(float),
            vol.Range(min=MIN_FAN_TIMER_SECONDS, max=MAX_FAN_TIMER_SECONDS),
        ),
        vol.Optional(ATTR_TURN_OFF_HEAT, default=False): cv.boolean,
        vol.Optional(ATTR_TURN_OFF_SCREEN, default=False): cv.boolean,
    }
)

SCREEN_ANIMATION_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Optional(ATTR_DEVICE_ID): cv.ensure_list,
        vol.Optional(ATTR_AREA_ID): cv.ensure_list,
        vol.Required(ATTR_ANIMATION_TYPE): vol.In(ANIMATION_TYPES),
    }
)


def _referenced_entry_ids(hass: HomeAssistant, call: ServiceCall) -> set[str]:
    """Resolve the entity/device/area target of a call to config entry ids."""
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    entry_ids: set[str] = set()

    for entity_id in cv.ensure_list(call.data.get(ATTR_ENTITY_ID) or []):
        if (entity := entity_registry.async_get(entity_id)) and entity.config_entry_id:
            entry_ids.add(entity.config_entry_id)

    for device_id in cv.ensure_list(call.data.get(ATTR_DEVICE_ID) or []):
        if device := device_registry.async_get(device_id):
            entry_ids.update(device.config_entries)

    for area_id in cv.ensure_list(call.data.get(ATTR_AREA_ID) or []):
        for device in dr.async_entries_for_area(device_registry, area_id):
            entry_ids.update(device.config_entries)
        for entity in er.async_entries_for_area(entity_registry, area_id):
            if entity.config_entry_id:
                entry_ids.add(entity.config_entry_id)

    return {
        entry_id
        for entry_id in entry_ids
        if (entry := hass.config_entries.async_get_entry(entry_id))
        and entry.domain == DOMAIN
    }


def _async_get_coordinators(
    hass: HomeAssistant, call: ServiceCall
) -> list[VolcanoDataUpdateCoordinator]:
    """Return the coordinators a call targets, or raise if there are none."""
    entry_ids = _referenced_entry_ids(hass, call)
    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if not entry_ids or entry.entry_id in entry_ids
    ]

    if not entries:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="no_target"
        )

    loaded = [entry for entry in entries if entry.state is ConfigEntryState.LOADED]
    if not loaded:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_loaded",
            translation_placeholders={"target": entries[0].title},
        )

    return [entry.runtime_data for entry in loaded]


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the Volcano Hybrid actions."""

    async def handle_fan_timer(call: ServiceCall) -> None:
        """Run the fan for a fixed duration."""
        for coordinator in _async_get_coordinators(hass, call):
            await coordinator.async_fan_timer(
                call.data[ATTR_DURATION],
                call.data[ATTR_TURN_OFF_HEAT],
                call.data[ATTR_TURN_OFF_SCREEN],
            )

    async def handle_screen_animation(call: ServiceCall) -> None:
        """Start or stop a screen animation."""
        animation_type = call.data.get(ATTR_ANIMATION_TYPE, ANIMATION_NONE)
        for coordinator in _async_get_coordinators(hass, call):
            await coordinator.async_start_animation(animation_type)

    hass.services.async_register(
        DOMAIN, SERVICE_FAN_TIMER, handle_fan_timer, schema=FAN_TIMER_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SCREEN_ANIMATION,
        handle_screen_animation,
        schema=SCREEN_ANIMATION_SCHEMA,
    )
