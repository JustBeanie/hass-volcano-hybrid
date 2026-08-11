"""Base entity for the Volcano Hybrid integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import (
    CONNECTION_BLUETOOTH,
    DeviceInfo,
    format_mac,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import VolcanoDataUpdateCoordinator
from .volcano import VolcanoState


class VolcanoEntity(CoordinatorEntity[VolcanoDataUpdateCoordinator]):
    """Common base for every Volcano Hybrid entity."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: VolcanoDataUpdateCoordinator, key: str) -> None:
        """Initialise the entity and attach it to the device."""
        super().__init__(coordinator)
        formatted_mac = format_mac(coordinator.address)
        self._attr_unique_id = f"{formatted_mac}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, formatted_mac)},
            connections={(CONNECTION_BLUETOOTH, coordinator.address)},
            manufacturer=MANUFACTURER,
            model=MODEL,
            name=coordinator.config_entry.title,
            serial_number=coordinator.data.serial_number,
            sw_version=coordinator.data.firmware_version,
        )

    @property
    def data(self) -> VolcanoState:
        """Return the current device state."""
        return self.coordinator.data

    @property
    def available(self) -> bool:
        """Return whether the device is reachable."""
        return super().available and self.coordinator.data.connected
