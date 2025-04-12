"""Light platform for Volcano Hybrid integration."""
import logging

from homeassistant.components.light import LightEntity, ColorMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import VolcanoDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Volcano Hybrid light entity."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    
    async_add_entities([VolcanoScreenLight(coordinator, entry)])


class VolcanoScreenLight(CoordinatorEntity, LightEntity):
    """Representation of the Volcano Hybrid screen light."""

    _attr_has_entity_name = True
    _attr_name = "Screen"
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_icon = "mdi:tablet"

    def __init__(
        self, coordinator: VolcanoDataUpdateCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the Volcano Hybrid screen light."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_screen"
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
            return self.coordinator.data.get("is_connected", False)
        return False

    @property
    def brightness(self) -> int:
        """Return the brightness of the light."""
        if self.coordinator.data:
            # Convert 0-100 scale to 0-255 scale
            return int(self.coordinator.data.get("brightness", 0) * 255 / 100)
        return 0

    @property
    def is_on(self) -> bool:
        """Return true if the light is on."""
        if self.coordinator.data:
            return self.coordinator.data.get("brightness", 0) > 0
        return False

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the light on."""
        try:
            brightness = kwargs.get("brightness", 255)
            # Convert 0-255 scale to 0-100 scale
            brightness_percent = int(brightness * 100 / 255)
            _LOGGER.debug("Light entity turning on with brightness %s (%s%%)", brightness, brightness_percent)
            await self.coordinator.async_set_brightness(brightness_percent)
        except Exception as e:
            _LOGGER.error("Failed to turn screen on: %s", e)

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the light off."""
        try:
            _LOGGER.debug("Light entity turning off")
            await self.coordinator.async_set_brightness(0)
        except Exception as e:
            _LOGGER.error("Failed to turn screen off: %s", e)
