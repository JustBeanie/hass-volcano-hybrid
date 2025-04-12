"""Sensor platform for Volcano Hybrid integration."""
import logging
from typing import Optional

from homeassistant.components.sensor import (
    SensorEntity, 
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature, EntityCategory
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
    """Set up the Volcano Hybrid sensor entities."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    
    async_add_entities([
        VolcanoTemperatureSensor(coordinator, entry),
        VolcanoConnectionStatusSensor(coordinator, entry),
        VolcanoRawRegisterSensor(coordinator, entry),
        VolcanoHeaterStatusSensor(coordinator, entry),
        VolcanoFanStatusSensor(coordinator, entry),
        VolcanoBrightnessSensor(coordinator, entry),
        # Device information sensors
        VolcanoSerialNumberSensor(coordinator, entry),
        VolcanoBLEFirmwareSensor(coordinator, entry),
        VolcanoHoursOfOperationSensor(coordinator, entry),
        VolcanoFirmwareVersionSensor(coordinator, entry),
        VolcanoAutoOffTimeSensor(coordinator, entry),
    ])


class VolcanoTemperatureSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Volcano Hybrid temperature sensor."""

    _attr_has_entity_name = True
    _attr_name = "Temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(
        self, coordinator: VolcanoDataUpdateCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the Volcano Hybrid temperature sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_temperature"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.data["name"],
            "manufacturer": "Storz & Bickel",
            "model": "Volcano Hybrid",
        }

    @property
    def native_value(self):
        """Return the current temperature."""
        if self.coordinator.data:
            return self.coordinator.data.get("current_temperature")
        return None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        if self.coordinator.data:
            # Check both connection status and temperature validity
            return (self.coordinator.data.get("is_connected", False) and 
                    self.coordinator.data.get("temperature_valid", True))
        return False


class VolcanoConnectionStatusSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Volcano Hybrid connection status sensor."""

    _attr_has_entity_name = True
    _attr_name = "Connection Status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:bluetooth"

    def __init__(
        self, coordinator: VolcanoDataUpdateCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the Volcano Hybrid connection status sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_connection_status"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.data["name"],
            "manufacturer": "Storz & Bickel",
            "model": "Volcano Hybrid",
        }

    @property
    def native_value(self):
        """Return the connection status."""
        if self.coordinator.data:
            return "Connected" if self.coordinator.data.get("is_connected", False) else "Disconnected"
        return "Unknown"


class VolcanoRawRegisterSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Volcano Hybrid raw register sensor for debugging."""

    _attr_has_entity_name = True
    _attr_name = "Raw Register"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:code-json"

    def __init__(
        self, coordinator: VolcanoDataUpdateCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the Volcano Hybrid raw register sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_raw_register"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.data["name"],
            "manufacturer": "Storz & Bickel",
            "model": "Volcano Hybrid",
        }
        self._raw_register = None

    @property
    def native_value(self):
        """Return the raw register value."""
        return self._raw_register

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        if self.coordinator.data:
            return {
                "current_temperature_raw": self.coordinator.volcano._current_temperature_raw,
                "target_temperature_raw": self.coordinator.volcano._target_temperature_raw,
                "register_one_raw": self.coordinator.volcano._register_one_raw,
            }
        return {}

    async def async_update(self):
        """Update the sensor."""
        await super().async_update()
        if self.coordinator.data and self.coordinator.data.get("is_connected", False):
            try:
                # Get the raw register value
                self._raw_register = await self.coordinator.volcano.get_raw_register()
            except Exception as e:
                _LOGGER.error("Failed to get raw register: %s", e)


class VolcanoHeaterStatusSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Volcano Hybrid heater status sensor."""

    _attr_has_entity_name = True
    _attr_name = "Heater Status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:radiator"

    def __init__(
        self, coordinator: VolcanoDataUpdateCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the Volcano Hybrid heater status sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_heater_status"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.data["name"],
            "manufacturer": "Storz & Bickel",
            "model": "Volcano Hybrid",
        }

    @property
    def native_value(self):
        """Return the heater status."""
        if self.coordinator.data:
            return "On" if self.coordinator.data.get("heater_on", False) else "Off"
        return "Unknown"


class VolcanoFanStatusSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Volcano Hybrid fan status sensor."""

    _attr_has_entity_name = True
    _attr_name = "Fan Status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:fan"

    def __init__(
        self, coordinator: VolcanoDataUpdateCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the Volcano Hybrid fan status sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_fan_status"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.data["name"],
            "manufacturer": "Storz & Bickel",
            "model": "Volcano Hybrid",
        }

    @property
    def native_value(self):
        """Return the fan status."""
        if self.coordinator.data:
            return "On" if self.coordinator.data.get("fan_on", False) else "Off"
        return "Unknown"


class VolcanoBrightnessSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Volcano Hybrid brightness sensor."""

    _attr_has_entity_name = True
    _attr_name = "Brightness"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:brightness-percent"
    _attr_native_unit_of_measurement = "%"

    def __init__(
        self, coordinator: VolcanoDataUpdateCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the Volcano Hybrid brightness sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_brightness_value"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.data["name"],
            "manufacturer": "Storz & Bickel",
            "model": "Volcano Hybrid",
        }

    @property
    def native_value(self):
        """Return the brightness value."""
        if self.coordinator.data:
            return self.coordinator.data.get("brightness", 0)
        return 0


class VolcanoSerialNumberSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Volcano Hybrid serial number sensor."""

    _attr_has_entity_name = True
    _attr_name = "Serial Number"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:identifier"

    def __init__(
        self, coordinator: VolcanoDataUpdateCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the Volcano Hybrid serial number sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_serial_number"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.data["name"],
            "manufacturer": "Storz & Bickel",
            "model": "Volcano Hybrid",
        }

    @property
    def native_value(self):
        """Return the serial number."""
        if self.coordinator.data and self.coordinator.data.get("device_info"):
            return self.coordinator.data["device_info"].get("serial_number")
        return None


class VolcanoBLEFirmwareSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Volcano Hybrid BLE firmware version sensor."""

    _attr_has_entity_name = True
    _attr_name = "BLE Firmware Version"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:bluetooth-settings"

    def __init__(
        self, coordinator: VolcanoDataUpdateCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the Volcano Hybrid BLE firmware version sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_ble_firmware"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.data["name"],
            "manufacturer": "Storz & Bickel",
            "model": "Volcano Hybrid",
        }

    @property
    def native_value(self):
        """Return the BLE firmware version."""
        if self.coordinator.data and self.coordinator.data.get("device_info"):
            return self.coordinator.data["device_info"].get("ble_firmware_version")
        return None


class VolcanoHoursOfOperationSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Volcano Hybrid hours of operation sensor."""

    _attr_has_entity_name = True
    _attr_name = "Hours Of Operation"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:clock-time-eight"
    _attr_native_unit_of_measurement = "h"

    def __init__(
        self, coordinator: VolcanoDataUpdateCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the Volcano Hybrid hours of operation sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_hours_operation"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.data["name"],
            "manufacturer": "Storz & Bickel",
            "model": "Volcano Hybrid",
        }

    @property
    def native_value(self):
        """Return the hours of operation."""
        if self.coordinator.data and self.coordinator.data.get("device_info"):
            hours_data = self.coordinator.data["device_info"].get("hours_of_operation", {})
            hours = hours_data.get("hours", 0)
            # Return the hours directly without rounding
            return hours
        return None

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        if self.coordinator.data and self.coordinator.data.get("device_info"):
            hours_data = self.coordinator.data["device_info"].get("hours_of_operation", {})
            return {
                "hours": hours_data.get("hours", 0),
                "minutes": hours_data.get("minutes", 0),
                "display": f"{hours_data.get('hours', 0)}h {hours_data.get('minutes', 0)}m"
            }
        return {}


class VolcanoFirmwareVersionSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Volcano Hybrid firmware version sensor."""

    _attr_has_entity_name = True
    _attr_name = "Firmware Version"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:chip"

    def __init__(
        self, coordinator: VolcanoDataUpdateCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the Volcano Hybrid firmware version sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_firmware_version"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.data["name"],
            "manufacturer": "Storz & Bickel",
            "model": "Volcano Hybrid",
        }

    @property
    def native_value(self):
        """Return the firmware version."""
        if self.coordinator.data and self.coordinator.data.get("device_info"):
            return self.coordinator.data["device_info"].get("volcano_firmware_version")
        return None


class VolcanoAutoOffTimeSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Volcano Hybrid auto-off time sensor."""

    _attr_has_entity_name = True
    _attr_name = "Auto-Off Time"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:timer-off"
    _attr_native_unit_of_measurement = "min"

    def __init__(
        self, coordinator: VolcanoDataUpdateCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the Volcano Hybrid auto-off time sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_auto_off_time"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.data["name"],
            "manufacturer": "Storz & Bickel",
            "model": "Volcano Hybrid",
        }

    @property
    def native_value(self):
        """Return the auto-off time in minutes."""
        if self.coordinator.data and self.coordinator.data.get("device_info"):
            seconds = self.coordinator.data["device_info"].get("auto_off_time_seconds", 0)
            if seconds:
                return seconds // 60  # Convert seconds to minutes
        return None
