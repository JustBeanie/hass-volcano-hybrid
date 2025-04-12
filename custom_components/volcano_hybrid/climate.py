"""Climate platform for Volcano Hybrid integration."""
import logging
from typing import Any, Dict, List, Optional
from enum import Enum

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MIN_TEMP, MAX_TEMP, TEMP_STEP
from .coordinator import VolcanoDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# Define our own FanMode enum with only ON and OFF since that's all the Volcano supports
class FanMode(str, Enum):
    """Fan mode for Volcano Hybrid."""
    OFF = "off"
    ON = "on"

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Volcano Hybrid climate entity."""
    _LOGGER.debug("Setting up Volcano Hybrid climate entity")
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([VolcanoClimate(coordinator, entry)])
    _LOGGER.debug("Volcano Hybrid climate entity setup complete")


class VolcanoClimate(CoordinatorEntity, ClimateEntity):
    """Representation of a Volcano Hybrid climate entity."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE | 
        ClimateEntityFeature.TURN_ON | 
        ClimateEntityFeature.TURN_OFF |
        ClimateEntityFeature.FAN_MODE
    )
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_min_temp = MIN_TEMP
    _attr_max_temp = MAX_TEMP
    _attr_target_temperature_step = TEMP_STEP
    _attr_fan_modes = [FanMode.OFF, FanMode.ON]

    def __init__(
        self, coordinator: VolcanoDataUpdateCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the Volcano Hybrid climate entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_climate"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.data["name"],
            "manufacturer": "Storz & Bickel",
            "model": "Volcano Hybrid",
        }

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        if self.coordinator.data:
            # Check both connection status and temperature validity
            return (self.coordinator.data.get("is_connected", False) and 
                    self.coordinator.data.get("temperature_valid", True))
        return False

    @property
    def current_temperature(self) -> Optional[float]:
        """Return the current temperature."""
        if self.coordinator.data:
            return self.coordinator.data.get("current_temperature")
        return None

    @property
    def target_temperature(self) -> Optional[float]:
        """Return the target temperature."""
        if self.coordinator.data:
            return self.coordinator.data.get("target_temperature")
        return None

    @property
    def hvac_mode(self) -> HVACMode:
        """Return hvac operation mode."""
        if self.coordinator.data and self.coordinator.data.get("heater_on"):
            return HVACMode.HEAT
        return HVACMode.OFF

    @property
    def fan_mode(self) -> Optional[str]:
        """Return the fan setting."""
        if self.coordinator.data and self.coordinator.data.get("fan_on"):
            return FanMode.ON
        return FanMode.OFF

    async def async_set_temperature(self, **kwargs) -> None:
        """Set new target temperature."""
        if ATTR_TEMPERATURE in kwargs:
            try:
                await self.coordinator.async_set_temperature(int(kwargs[ATTR_TEMPERATURE]))
            except Exception as e:
                _LOGGER.error("Failed to set temperature: %s", e)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new hvac mode."""
        try:
            if hvac_mode == HVACMode.HEAT:
                await self.coordinator.async_turn_heater_on()
            elif hvac_mode == HVACMode.OFF:
                await self.coordinator.async_turn_heater_off()
        except Exception as e:
            _LOGGER.error("Failed to set HVAC mode: %s", e)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set new fan mode."""
        try:
            if fan_mode == FanMode.ON:
                await self.coordinator.async_turn_fan_on()
            elif fan_mode == FanMode.OFF:
                await self.coordinator.async_turn_fan_off()
        except Exception as e:
            _LOGGER.error("Failed to set fan mode: %s", e)
