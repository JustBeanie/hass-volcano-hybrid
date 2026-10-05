"""Binary sensor platform for the Volcano Hybrid integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import VolcanoConfigEntry, VolcanoDataUpdateCoordinator
from .entity import VolcanoEntity
from .volcano import VolcanoState

# The coordinator owns every read, and writes are already serialised by the
# single GATT lock in volcano.py, so Home Assistant does not need to throttle.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class VolcanoBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describes a Volcano Hybrid status-register binary sensor."""

    is_on_fn: Callable[[VolcanoState], bool | None]
    attributes_fn: Callable[[VolcanoState], dict[str, Any]] | None = None


BINARY_SENSORS: tuple[VolcanoBinarySensorEntityDescription, ...] = (
    VolcanoBinarySensorEntityDescription(
        key="heater_pump_fault",
        translation_key="heater_pump_fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda state: state.heater_pump_fault,
        attributes_fn=lambda state: {
            "heater_fault": state.heater_fault,
            "pump_interlock_fault": state.pump_interlock_fault,
            "regulation_faults": state.regulation_faults,
        },
    ),
    VolcanoBinarySensorEntityDescription(
        key="service_mode",
        translation_key="service_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda state: state.service_mode,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VolcanoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Volcano Hybrid binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            VolcanoConnectivitySensor(coordinator),
            *(
                VolcanoBinarySensor(coordinator, description)
                for description in BINARY_SENSORS
            ),
        ]
    )


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


class VolcanoBinarySensor(VolcanoEntity, BinarySensorEntity):
    """A binary sensor decoded from the device's status registers."""

    entity_description: VolcanoBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: VolcanoDataUpdateCoordinator,
        description: VolcanoBinarySensorEntityDescription,
    ) -> None:
        """Initialise the binary sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return whether the bit is set."""
        return self.entity_description.is_on_fn(self.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the related bits, where there are any."""
        if (attributes_fn := self.entity_description.attributes_fn) is None:
            return None
        return attributes_fn(self.data)
