"""Switch platform for Volcano Hybrid integration."""
import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import EntityCategory

from .const import DOMAIN
from .coordinator import VolcanoDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Volcano Hybrid switch entities."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    
    async_add_entities([
        VolcanoHeaterSwitch(coordinator, entry),
        VolcanoFanSwitch(coordinator, entry),
        VolcanoRegister3Switch(coordinator, entry),
        VolcanoRegister2Switch(coordinator, entry),
    ])


class VolcanoBaseSwitch(CoordinatorEntity, SwitchEntity):
    """Base class for Volcano Hybrid switches."""
    
    @property
    def available(self) -> bool:
        """Return if entity is available."""
        if self.coordinator.data:
            return self.coordinator.data.get("is_connected", False)
        return False


class VolcanoHeaterSwitch(VolcanoBaseSwitch):
    """Representation of a Volcano Hybrid heater switch."""

    _attr_has_entity_name = True
    _attr_name = "Heater"
    _attr_icon = "mdi:radiator"

    def __init__(
        self, coordinator: VolcanoDataUpdateCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the Volcano Hybrid heater switch."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_heater"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.data["name"],
            "manufacturer": "Storz & Bickel",
            "model": "Volcano Hybrid",
        }

    @property
    def is_on(self) -> bool:
        """Return true if the heater is on."""
        if self.coordinator.data:
            return self.coordinator.data.get("heater_on", False)
        return False

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the heater on."""
        try:
            await self.coordinator.async_turn_heater_on()
        except Exception as e:
            _LOGGER.error("Failed to turn heater on: %s", e)

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the heater off."""
        try:
            await self.coordinator.async_turn_heater_off()
        except Exception as e:
            _LOGGER.error("Failed to turn heater off: %s", e)


class VolcanoFanSwitch(VolcanoBaseSwitch):
    """Representation of a Volcano Hybrid fan switch."""

    _attr_has_entity_name = True
    _attr_name = "Fan"
    _attr_icon = "mdi:fan"

    def __init__(
        self, coordinator: VolcanoDataUpdateCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the Volcano Hybrid fan switch."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_fan"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.data["name"],
            "manufacturer": "Storz & Bickel",
            "model": "Volcano Hybrid",
        }

    @property
    def is_on(self) -> bool:
        """Return true if the fan is on."""
        if self.coordinator.data:
            return self.coordinator.data.get("fan_on", False)
        return False

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the fan on."""
        try:
            await self.coordinator.async_turn_fan_on()
        except Exception as e:
            _LOGGER.error("Failed to turn fan on: %s", e)

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the fan off."""
        try:
            await self.coordinator.async_turn_fan_off()
        except Exception as e:
            _LOGGER.error("Failed to turn fan off: %s", e)


class VolcanoRegister3Switch(VolcanoBaseSwitch):
    """Representation of a Volcano Hybrid Register 3 switch (labeled as vibration in original code)."""

    _attr_has_entity_name = True
    _attr_name = "Register 3"
    _attr_icon = "mdi:vibrate"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False  # Disabled by default since it might not be supported

    def __init__(
        self, coordinator: VolcanoDataUpdateCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the Volcano Hybrid Register 3 switch."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_register3"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.data["name"],
            "manufacturer": "Storz & Bickel",
            "model": "Volcano Hybrid",
        }

    @property
    def is_on(self) -> bool:
        """Return true if Register 3 is enabled."""
        if self.coordinator.data:
            return self.coordinator.data.get("is_vibration_enabled", True)
        return True

    async def async_turn_on(self, **kwargs) -> None:
        """Enable Register 3."""
        try:
            await self.coordinator.async_set_vibration_enabled(True)
        except Exception as e:
            _LOGGER.error("Failed to enable Register 3: %s", e)

    async def async_turn_off(self, **kwargs) -> None:
        """Disable Register 3."""
        try:
            await self.coordinator.async_set_vibration_enabled(False)
        except Exception as e:
            _LOGGER.error("Failed to disable Register 3: %s", e)


class VolcanoRegister2Switch(VolcanoBaseSwitch):
    """Representation of a Volcano Hybrid Register 2 switch (toggles F/C or display during cooling)."""

    _attr_has_entity_name = True
    _attr_name = "Register 2"
    _attr_icon = "mdi:thermometer-minus"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False  # Disabled by default since it might not be supported

    def __init__(
        self, coordinator: VolcanoDataUpdateCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the Volcano Hybrid Register 2 switch."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_register2"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.data["name"],
            "manufacturer": "Storz & Bickel",
            "model": "Volcano Hybrid",
        }

    @property
    def is_on(self) -> bool:
        """Return true if Register 2 is enabled."""
        if self.coordinator.data:
            return self.coordinator.data.get("is_display_on_cooling", True)
        return True

    async def async_turn_on(self, **kwargs) -> None:
        """Enable Register 2."""
        try:
            await self.coordinator.async_set_display_on_cooling(True)
        except Exception as e:
            _LOGGER.error("Failed to enable Register 2: %s", e)

    async def async_turn_off(self, **kwargs) -> None:
        """Disable Register 2."""
        try:
            await self.coordinator.async_set_display_on_cooling(False)
        except Exception as e:
            _LOGGER.error("Failed to disable Register 2: %s", e)
