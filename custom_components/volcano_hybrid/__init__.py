"""The Volcano Hybrid integration."""
import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry

# Make sure we're importing the constant correctly
from .const import (
    DOMAIN, SERVICE_FAN_TIMER, SERVICE_SCREEN_ANIMATION,
    ANIMATION_NONE, ANIMATION_BLINKING, ANIMATION_BREATHING,
    ANIMATION_ASCENDING, ANIMATION_DESCENDING
)
from .coordinator import VolcanoDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["climate", "sensor", "switch", "number", "light"]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Volcano Hybrid from a config entry."""
    _LOGGER.info("Setting up Volcano Hybrid integration")
    hass.data.setdefault(DOMAIN, {})
    
    coordinator = VolcanoDataUpdateCoordinator(hass, entry)
    
    try:
        _LOGGER.debug("Attempting to connect to Volcano Hybrid device")
        # Set a timeout for the connection attempt
        connect_task = asyncio.create_task(coordinator.async_connect())
        await asyncio.wait_for(connect_task, timeout=45.0)  # Increased timeout for connection
        _LOGGER.debug("Successfully connected to Volcano Hybrid device")
    except asyncio.TimeoutError:
        _LOGGER.error("Connection to Volcano Hybrid timed out after 45 seconds")
        raise ConfigEntryNotReady("Connection to Volcano Hybrid timed out") 
    except Exception as ex:
        _LOGGER.exception("Failed to connect to Volcano Hybrid: %s", ex)
        # Instead of failing immediately, store the coordinator and let it retry in the background
        hass.data[DOMAIN][entry.entry_id] = coordinator
        # Set up platforms anyway to allow them to show as unavailable until connection succeeds
        for platform in PLATFORMS:
            hass.async_create_task(
                hass.config_entries.async_forward_entry_setup(entry, platform)
            )
        # Return True to indicate setup was successful, even though device is not yet connected
        return True
    
    try:
        _LOGGER.debug("Performing initial data refresh")
        # Set a timeout for the first refresh
        refresh_task = asyncio.create_task(coordinator.async_config_entry_first_refresh())
        await asyncio.wait_for(refresh_task, timeout=30.0)
        _LOGGER.debug("Initial data refresh completed successfully")
    except asyncio.TimeoutError:
        _LOGGER.error("Initial data refresh timed out after 30 seconds")
        raise ConfigEntryNotReady("Initial data refresh timed out")
    except Exception as ex:
        _LOGGER.exception("Error during initial data refresh: %s", ex)
        raise ConfigEntryNotReady(f"Error during initial data refresh: {ex}") from ex
    
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Forward the entry setup to each platform with a timeout
    for platform in PLATFORMS:
        try:
            _LOGGER.debug("Setting up platform: %s", platform)
            setup_task = asyncio.create_task(
                hass.config_entries.async_forward_entry_setup(entry, platform)
            )
            await asyncio.wait_for(setup_task, timeout=30.0)
            _LOGGER.debug("Successfully set up platform: %s", platform)
        except asyncio.TimeoutError:
            _LOGGER.error("Setup of platform %s timed out", platform)
        except Exception as ex:
            _LOGGER.exception("Error setting up platform %s: %s", platform, ex)

    # Register services
    async def handle_fan_timer(call: ServiceCall) -> None:
        """Handle the fan timer service call."""
        entity_id = call.data.get("entity_id")
        duration = call.data.get("duration")
        turn_off_heat = call.data.get("turn_off_heat", False)
        turn_off_screen = call.data.get("turn_off_screen", False)
        
        if entity_id:
            entity_reg = entity_registry.async_get(hass)
            for entity in entity_id:
                entry_id = entity_reg.async_get(entity)
                if entry_id and entry_id.config_entry_id in hass.data[DOMAIN]:
                    coordinator = hass.data[DOMAIN][entry_id.config_entry_id]
                    await coordinator.async_fan_timer(duration, turn_off_heat, turn_off_screen)
        else:
            # Apply to all devices if no specific entity_id
            for coordinator in hass.data[DOMAIN].values():
                await coordinator.async_fan_timer(duration, turn_off_heat, turn_off_screen)

    async def handle_screen_animation(call: ServiceCall) -> None:
        """Handle the screen animation service call."""
        entity_id = call.data.get("entity_id")
        animation_type = call.data.get("animation_type", ANIMATION_NONE)
        
        if entity_id:
            entity_reg = entity_registry.async_get(hass)
            for entity in entity_id:
                entry_id = entity_reg.async_get(entity)
                if entry_id and entry_id.config_entry_id in hass.data[DOMAIN]:
                    coordinator = hass.data[DOMAIN][entry_id.config_entry_id]
                    await coordinator.async_start_animation(animation_type)
        else:
            # Apply to all devices if no specific entity_id
            for coordinator in hass.data[DOMAIN].values():
                await coordinator.async_start_animation(animation_type)

    hass.services.async_register(DOMAIN, SERVICE_FAN_TIMER, handle_fan_timer)
    hass.services.async_register(DOMAIN, SERVICE_SCREEN_ANIMATION, handle_screen_animation)

    _LOGGER.info("Volcano Hybrid integration setup completed successfully")
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading Volcano Hybrid integration")
    
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    
    if unload_ok:
        coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        try:
            await coordinator.async_disconnect()
            _LOGGER.debug("Successfully disconnected from Volcano Hybrid device")
        except Exception as ex:
            _LOGGER.error("Error disconnecting from Volcano Hybrid: %s", ex)

    _LOGGER.info("Volcano Hybrid integration unloaded: %s", unload_ok)
    return unload_ok
