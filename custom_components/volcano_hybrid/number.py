"""Number platform for Volcano Hybrid integration."""
import logging

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import EntityCategory

from .const import DOMAIN, MIN_TEMP, MAX_TEMP, TEMP_STEP
from .coordinator import VolcanoDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Volcano Hybrid number entities."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    
    async_add_entities([
        VolcanoTemperatureNumber(coordinator, entry),
        VolcanoAutoOffTimeNumber(coordinator, entry),
    ])


class VolcanoTemperatureNumber(CoordinatorEntity, NumberEntity):
    """Representation of a Volcano Hybrid temperature setting."""

    _attr_has_entity_name = True
    _attr_name = "Target Temperature"
    _attr_native_min_value = MIN_TEMP
    _attr_native_max_value = MAX_TEMP
    _attr_native_step = TEMP_STEP
    _attr_native_unit_of_measurement = "°C"
    _attr_icon = "mdi:thermometer"

    def __init__(
        self, coordinator: VolcanoDataUpdateCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the Volcano Hybrid temperature number."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_target_temperature"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.data["name"],
            "manufacturer": "Storz & Bickel",
            "model": "Volcano Hybrid",
        }

    @property
    def native_value(self) -> float:
        """Return the current temperature setting."""
        if self.coordinator.data:
            return self.coordinator.data.get("target_temperature", MIN_TEMP)
        return MIN_TEMP

    async def async_set_native_value(self, value: float) -> None:
        """Set the temperature value."""
        await self.coordinator.async_set_temperature(int(value))


class VolcanoAutoOffTimeNumber(CoordinatorEntity, NumberEntity):
    """Representation of a Volcano Hybrid auto-off time setting."""

    _attr_has_entity_name = True
    _attr_name = "Auto-Off Time"
    _attr_native_min_value = 1
    _attr_native_max_value = 180
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "min"
    _attr_icon = "mdi:timer-outline"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator: VolcanoDataUpdateCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the Volcano Hybrid auto-off time number."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_auto_off_time"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.data["name"],
            "manufacturer": "Storz & Bickel",
            "model": "Volcano Hybrid",
        }

    @property
    def native_value(self) -> float:
        """Return the current auto-off time setting in minutes."""
        if self.coordinator.data and self.coordinator.data.get("device_info"):
            seconds = self.coordinator.data["device_info"].get("auto_off_time_seconds", 0)
            if seconds:
                return seconds // 60  # Convert seconds to minutes
        return 30  # Default to 30 minutes

    async def async_set_native_value(self, value: float) -> None:
        """Set the auto-off time value in minutes."""
        await self.coordinator.async_set_auto_off_time(int(value))
