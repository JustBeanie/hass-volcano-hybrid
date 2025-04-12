"""DataUpdateCoordinator for the Volcano Hybrid integration."""
import asyncio
import logging
from datetime import timedelta
from typing import Any, Dict, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    ANIMATION_ASCENDING, ANIMATION_BLINKING, ANIMATION_BREATHING, 
    ANIMATION_DESCENDING, ANIMATION_NONE, CONF_FAN_ON_CONNECT, 
    CONF_INITIAL_TEMP, CONF_MAC_ADDRESS, DOMAIN
)
from .volcano import VolcanoHybrid

_LOGGER = logging.getLogger(__name__)

class VolcanoDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Volcano Hybrid data."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the data update coordinator."""
        _LOGGER.info("Initializing Volcano Hybrid coordinator")
        self.volcano = VolcanoHybrid(entry.data[CONF_MAC_ADDRESS])
        self.hass = hass
        self.entry = entry
        self._animation_task = None
        self._is_animating = False
        self._current_animation = ANIMATION_NONE
        self._connection_retry_count = 0
        self._max_connection_retries = 5
        self._initialization_complete = False
        self._last_update_time = 0
        self._min_update_interval = 5  # Increased to 5 seconds to reduce polling frequency

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=10),  # Increased to 10 seconds to reduce polling frequency
        )
        _LOGGER.debug("Volcano Hybrid coordinator initialized")

    async def async_connect(self) -> None:
        """Connect to the Volcano Hybrid device."""
        _LOGGER.info("Connecting to Volcano Hybrid device")
        try:
            success = await self.volcano.connect(self._state_updated)
            
            if not success:
                self._connection_retry_count += 1
                if self._connection_retry_count > self._max_connection_retries:
                    self._connection_retry_count = 0
                    _LOGGER.error("Failed to connect after %d attempts", self._max_connection_retries)
                    raise UpdateFailed("Failed to connect to Volcano Hybrid after multiple attempts")
                else:
                    _LOGGER.warning(
                        "Connection attempt %d/%d failed", 
                        self._connection_retry_count, 
                        self._max_connection_retries
                    )
                    raise UpdateFailed("Failed to connect to Volcano Hybrid, will retry")
            else:
                self._connection_retry_count = 0
                _LOGGER.info("Successfully connected to Volcano Hybrid device")
                
            # Apply initial settings if configured
            if self.entry.data.get(CONF_FAN_ON_CONNECT, False):
                _LOGGER.debug("Turning fan on as configured")
                await self.volcano.turn_fan_on()
                
            initial_temp = self.entry.data.get(CONF_INITIAL_TEMP)
            if initial_temp:
                _LOGGER.debug("Setting initial temperature to %s°C", initial_temp)
                await self.volcano.set_target_temperature(initial_temp)
                
            self._initialization_complete = True
            return success
        except Exception as e:
            _LOGGER.exception("Error connecting to Volcano Hybrid: %s", e)
            raise

    async def async_disconnect(self) -> None:
        """Disconnect from the Volcano Hybrid device."""
        _LOGGER.info("Disconnecting from Volcano Hybrid device")
        # Stop any running animations
        await self.async_stop_animation()
        
        # Disconnect from the device
        await self.volcano.disconnect()
        _LOGGER.debug("Disconnected from Volcano Hybrid device")

    async def _async_update_data(self) -> Dict[str, Any]:
        """Update data via library."""
        # Implement rate limiting for updates
        current_time = asyncio.get_event_loop().time()
        if current_time - self._last_update_time < self._min_update_interval:
            _LOGGER.debug("Skipping update due to rate limiting")
            return self.data if self.data else self.volcano.get_state()
        
        _LOGGER.debug("Updating Volcano Hybrid data")
        try:
            # If we're not connected, try to reconnect
            if not self.volcano.get_state()["is_connected"]:
                _LOGGER.info("Device disconnected, attempting to reconnect")
                await self.async_connect()
            
            # Read the current and target temperatures - use fast methods
            try:
                _LOGGER.debug("Reading current temperature")
                current_temp = await self.volcano._fast_read_current_temperature()
                _LOGGER.debug("Current temperature: %s°C", current_temp)
            
                _LOGGER.debug("Reading target temperature")
                target_temp = await self.volcano._fast_read_target_temperature()
                _LOGGER.debug("Target temperature: %s°C", target_temp)
            except Exception as e:
                _LOGGER.warning("Failed to read temperatures: %s", e)
                # Continue anyway, we'll use the cached values
        
            # Periodically refresh device information (every 10 minutes)
            if not hasattr(self, '_last_device_info_update') or current_time - getattr(self, '_last_device_info_update', 0) > 600:
                _LOGGER.debug("Refreshing device information")
                try:
                    await self.volcano._read_device_information()
                    await self.volcano._read_device_settings()
                    self._last_device_info_update = current_time
                except Exception as e:
                    _LOGGER.warning("Failed to refresh device information: %s", e)
        
            # Validate temperature is within reasonable range
            if self.volcano._current_temperature > 1000 or self.volcano._current_temperature < 0:
                _LOGGER.warning("Current temperature value is unreasonable: %s°C, marking as invalid", 
                               self.volcano._current_temperature)
                state = self.volcano.get_state()
                state["temperature_valid"] = False
                return state
            
            self._last_update_time = current_time
            state = self.volcano.get_state()
            state["temperature_valid"] = True
            _LOGGER.debug("Device state: %s", state)
            return state
        except Exception as error:
            _LOGGER.exception("Error communicating with device: %s", error)
            # Return the last known state but mark as disconnected
            state = self.volcano.get_state()
            state["is_connected"] = False
            return state

    def _state_updated(self, state: Dict[str, Any]) -> None:
        """Handle state updates from the device."""
        _LOGGER.debug("Received state update from device")
        self.async_set_updated_data(state)

    async def async_set_temperature(self, temperature: int) -> None:
        """Set target temperature."""
        _LOGGER.info("Setting target temperature to %s°C", temperature)
        try:
            await self.volcano.set_target_temperature(temperature)
            await self.async_request_refresh()
        except Exception as e:
            _LOGGER.exception("Failed to set temperature: %s", e)
            raise
    
    async def async_set_auto_off_time(self, minutes: int) -> None:
        """Set auto-off time in minutes."""
        _LOGGER.info("Setting auto-off time to %s minutes", minutes)
        try:
            await self.volcano.set_auto_off_time(minutes)
            await self.async_request_refresh()
        except Exception as e:
            _LOGGER.exception("Failed to set auto-off time: %s", e)
            raise
    
    async def async_set_vibration_enabled(self, enabled: bool) -> None:
        """Set whether vibration is enabled."""
        _LOGGER.info("Setting register3 to %s", enabled)
        try:
            await self.volcano.set_vibration_enabled(enabled)
            await self.async_request_refresh()
        except Exception as e:
            _LOGGER.error("Failed to set register3: %s", e)
            # Update the state anyway to reflect the user's intent
            if self.data:
                self.data["is_vibration_enabled"] = enabled
                self.async_set_updated_data(self.data)
    
    async def async_set_display_on_cooling(self, enabled: bool) -> None:
        """Set whether the display stays on during cooling."""
        _LOGGER.info("Setting register2 to %s", enabled)
        try:
            await self.volcano.set_display_on_cooling(enabled)
            await self.async_request_refresh()
        except Exception as e:
            _LOGGER.error("Failed to set register2: %s", e)
            # Update the state anyway to reflect the user's intent
            if self.data:
                self.data["is_display_on_cooling"] = enabled
                self.async_set_updated_data(self.data)
    
    async def async_turn_heater_on(self) -> None:
        """Turn on the heater."""
        _LOGGER.info("Turning heater on")
        try:
            await self.volcano.turn_heater_on()
            await self.async_request_refresh()
        except Exception as e:
            _LOGGER.exception("Failed to turn heater on: %s", e)
            raise
    
    async def async_turn_heater_off(self) -> None:
        """Turn off the heater."""
        _LOGGER.info("Turning heater off")
        try:
            await self.volcano.turn_heater_off()
            await self.async_request_refresh()
        except Exception as e:
            _LOGGER.exception("Failed to turn heater off: %s", e)
            raise
    
    async def async_turn_fan_on(self) -> None:
        """Turn on the fan."""
        _LOGGER.info("Turning fan on")
        try:
            await self.volcano.turn_fan_on()
            await self.async_request_refresh()
        except Exception as e:
            _LOGGER.exception("Failed to turn fan on: %s", e)
            raise
    
    async def async_turn_fan_off(self) -> None:
        """Turn off the fan."""
        _LOGGER.info("Turning fan off")
        try:
            await self.volcano.turn_fan_off()
            await self.async_request_refresh()
        except Exception as e:
            _LOGGER.exception("Failed to turn fan off: %s", e)
            raise
        
    async def async_set_brightness(self, brightness: int) -> None:
        """Set the screen brightness."""
        _LOGGER.info("Setting brightness to %s%%", brightness)
        try:
            _LOGGER.debug("Coordinator setting brightness to %s", brightness)
            await self.volcano.set_brightness(brightness)
            await self.async_request_refresh()
        except Exception as e:
            _LOGGER.exception("Failed to set brightness: %s", e)
            raise
        
    async def async_fan_timer(self, duration: float, turn_off_heat: bool = False, turn_off_screen: bool = False) -> None:
        """Set a timer to turn off the fan after a specified duration."""
        _LOGGER.info("Setting fan timer for %s seconds", duration)
        try:
            await self.volcano.fan_timer(duration, turn_off_heat, turn_off_screen)
        except Exception as e:
            _LOGGER.exception("Failed to set fan timer: %s", e)
            raise

    async def async_start_animation(self, animation_type: str) -> None:
        """Start a screen animation."""
        _LOGGER.info("Starting screen animation: %s", animation_type)
        # Stop any existing animation
        await self.async_stop_animation()
        
        if animation_type == ANIMATION_NONE:
            return
            
        self._current_animation = animation_type
        self._is_animating = True
        self._animation_task = self.hass.async_create_task(
            self._animation_loop(animation_type)
        )
        
    async def async_stop_animation(self) -> None:
        """Stop the current animation."""
        if self._animation_task and not self._animation_task.done():
            _LOGGER.info("Stopping screen animation")
            self._is_animating = False
            # Wait for the animation to stop gracefully
            try:
                await asyncio.wait_for(self._animation_task, timeout=1.0)
            except asyncio.TimeoutError:
                _LOGGER.warning("Animation task did not stop gracefully")
            
        self._current_animation = ANIMATION_NONE
        
    async def _animation_loop(self, animation_type: str) -> None:
        """Run the animation loop."""
        MIN_BRIGHTNESS, MAX_BRIGHTNESS, interval = 0, 100, 8
        brightness = MIN_BRIGHTNESS
        increment = True
        sleep_time = 0.1

        try:
            while self._is_animating:
                if animation_type == ANIMATION_BLINKING:
                    brightness = 0 if brightness == 100 else 100
                    sleep_time = 0.5
                elif animation_type == ANIMATION_BREATHING:
                    brightness += interval if increment else -interval
                    if brightness >= MAX_BRIGHTNESS or brightness <= MIN_BRIGHTNESS:
                        increment = not increment
                    brightness = min(max(brightness, MIN_BRIGHTNESS), MAX_BRIGHTNESS)
                elif animation_type == ANIMATION_ASCENDING:
                    if brightness >= MAX_BRIGHTNESS:
                        brightness = -interval
                    brightness = min(interval + brightness, MAX_BRIGHTNESS)
                elif animation_type == ANIMATION_DESCENDING:
                    if brightness <= MIN_BRIGHTNESS:
                        brightness = MAX_BRIGHTNESS + interval
                    brightness = max(brightness - interval, MIN_BRIGHTNESS)
                else:
                    break
                
                try:
                    _LOGGER.debug("Animation setting brightness to %s", brightness)
                    await self.volcano.set_brightness(brightness)
                    await asyncio.sleep(sleep_time)
                except Exception as e:
                    _LOGGER.error("Error in animation loop: %s", e)
                    # Brief pause before continuing
                    await asyncio.sleep(1)
                    # If we've lost connection, try to reconnect
                    if not self.volcano.get_state()["is_connected"]:
                        try:
                            await self.async_connect()
                        except Exception:
                            # If reconnection fails, stop the animation
                            self._is_animating = False
                            break
        finally:
            # Reset brightness to default when animation stops
            try:
                if self.volcano.get_state()["is_connected"]:
                    _LOGGER.debug("Animation ended, resetting brightness to 70")
                    await self.volcano.set_brightness(70)
            except Exception as e:
                _LOGGER.error("Failed to reset brightness after animation: %s", e)
            self._is_animating = False
