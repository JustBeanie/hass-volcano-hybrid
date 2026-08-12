"""Binary sensor platform for the Volcano Hybrid integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import VolcanoConfigEntry, VolcanoDataUpdateCoordinator
from .entity import VolcanoEntity

# The coordinator owns every read, and writes are already serialised by the
# single GATT lock in volcano.py, so Home Assistant does not need to throttle.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VolcanoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Volcano Hybrid binary sensors."""
    async_add_entities([VolcanoConnectivitySensor(entry.runtime_data)])


class VolcanoConnectivitySensor(VolcanoEntity, BinarySensorEntity):
    """Reports whether Home Assistant currently holds a BLE link."""

    _attr_translation_key = "connection"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: VolcanoDataUpdateCoordinator) -> None:
        """Initialise the connectivity sensor."""
        super().__init__(coordinator, "connection")

    @property
    def available(self) -> bool:
        """Stay available so the disconnected state is actually reportable."""
        return True

    @property
    def is_on(self) -> bool:
        """Return whether the device is connected."""
        return self.coordinator.data.connected
