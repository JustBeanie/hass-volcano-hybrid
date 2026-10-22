"""Climate platform for the Volcano Hybrid integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import ClimateEntity

# Imported from .const rather than the package root: Home Assistant re-exports
# these implicitly, which mypy rejects under no_implicit_reexport.
from homeassistant.components.climate.const import (
    FAN_OFF,
    FAN_ON,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import MAX_TEMP, MIN_TEMP, TEMP_STEP
from .coordinator import VolcanoConfigEntry
from .entity import VolcanoEntity

# The coordinator owns every read, and writes are already serialised by the
# single GATT lock in volcano.py, so Home Assistant does not need to throttle.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VolcanoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Volcano Hybrid climate entity."""
    async_add_entities([VolcanoClimate(entry.runtime_data)])


class VolcanoClimate(VolcanoEntity, ClimateEntity):
    """The Volcano Hybrid as a climate entity."""

    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.FAN_MODE
    )
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_fan_modes = [FAN_OFF, FAN_ON]
    _attr_min_temp = MIN_TEMP
    _attr_max_temp = MAX_TEMP
    _attr_target_temperature_step = TEMP_STEP

    def __init__(self, coordinator: Any) -> None:
        """Initialise the climate entity."""
        super().__init__(coordinator, "climate")

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature."""
        return self.data.current_temperature

    @property
    def target_temperature(self) -> float | None:
        """Return the target temperature."""
        return self.data.target_temperature

    @property
    def hvac_mode(self) -> HVACMode:
        """Return the current operating mode."""
        return HVACMode.HEAT if self.data.heater_on else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction:
        """Return what the device is actually doing."""
        if not self.data.heater_on:
            return HVACAction.OFF
        current = self.data.current_temperature
        target = self.data.target_temperature
        if current is not None and target is not None and current < target:
            return HVACAction.HEATING
        return HVACAction.IDLE

    @property
    def fan_mode(self) -> str:
        """Return the fan setting."""
        return FAN_ON if self.data.fan_on else FAN_OFF

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set a new target temperature."""
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is not None:
            await self.coordinator.async_set_temperature(temperature)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Turn the heater on or off."""
        if hvac_mode is HVACMode.HEAT:
            await self.coordinator.async_turn_heater_on()
        else:
            await self.coordinator.async_turn_heater_off()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Turn the fan on or off."""
        if fan_mode == FAN_ON:
            await self.coordinator.async_turn_fan_on()
        else:
            await self.coordinator.async_turn_fan_off()
