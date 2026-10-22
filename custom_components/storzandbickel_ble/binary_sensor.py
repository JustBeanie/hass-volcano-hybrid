"""Binary sensor platform for the Storz & Bickel BLE integration."""

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

from .ble_device import DeviceState
from .coordinator import StorzBickelConfigEntry, StorzBickelCoordinator
from .entity import StorzBickelEntity, always
from .families import ALL_FAMILIES, QVAP, VOLCANO_ONLY, Family

# The coordinator owns every read, and writes are already serialised by the
# single GATT lock in volcano.py, so Home Assistant does not need to throttle.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class StorzBickelBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describes a binary sensor decoded from a device's status."""

    is_on_fn: Callable[[Any], bool | None]
    attributes_fn: Callable[[Any], dict[str, Any]] | None = None
    # Which device families have this entity, and whether a given device of
    # them can use it right now.
    families: frozenset[Family] = VOLCANO_ONLY
    available_fn: Callable[[DeviceState], bool] = always


BINARY_SENSORS: tuple[StorzBickelBinarySensorEntityDescription, ...] = (
    StorzBickelBinarySensorEntityDescription(
        # The device's own "target reached" bit, which also drives its
        # vibration alert, so this turns on exactly when the Volcano says it is
        # ready. Only while heating: the bit can outlive the heater.
        key="ready",
        translation_key="ready",
        families=ALL_FAMILIES,
        is_on_fn=lambda state: (
            None
            if state.target_reached is None
            else state.target_reached and state.heater_on
        ),
    ),
    StorzBickelBinarySensorEntityDescription(
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
    StorzBickelBinarySensorEntityDescription(
        key="service_mode",
        translation_key="service_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda state: state.service_mode,
    ),
    # -- portables (experimental) --
    StorzBickelBinarySensorEntityDescription(
        key="boost",
        translation_key="boost",
        families=frozenset({Family.CRAFTY}),
        is_on_fn=lambda state: state.boost,
    ),
    StorzBickelBinarySensorEntityDescription(
        key="superboost",
        translation_key="superboost",
        families=frozenset({Family.CRAFTY}),
        is_on_fn=lambda state: state.superboost,
    ),
    StorzBickelBinarySensorEntityDescription(
        key="error",
        translation_key="error",
        device_class=BinarySensorDeviceClass.PROBLEM,
        families=frozenset({Family.CRAFTY}),
        is_on_fn=lambda state: state.any_fault if state.fault_known else None,
    ),
    StorzBickelBinarySensorEntityDescription(
        key="needs_factory_reset",
        translation_key="needs_factory_reset",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        families=frozenset({Family.CRAFTY}),
        is_on_fn=lambda state: state.needs_factory_reset,
    ),
    StorzBickelBinarySensorEntityDescription(
        key="charging",
        translation_key="charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        families=QVAP,
        is_on_fn=lambda state: state.charging,
    ),
    StorzBickelBinarySensorEntityDescription(
        # A device sitting in its bootloader takes no commands; this says why.
        key="bootloader",
        translation_key="bootloader",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        families=QVAP,
        is_on_fn=lambda state: state.bootloader,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StorzBickelConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            StorzBickelConnectivitySensor(coordinator),
            *(
                StorzBickelBinarySensor(coordinator, description)
                for description in BINARY_SENSORS
                if coordinator.family in description.families
            ),
        ]
    )


class StorzBickelConnectivitySensor(StorzBickelEntity, BinarySensorEntity):
    """Reports whether Home Assistant currently holds a BLE link."""

    _attr_translation_key = "connection"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: StorzBickelCoordinator) -> None:
        """Initialise the connectivity sensor."""
        super().__init__(coordinator, "connection")

    @property
    def available(self) -> bool:
        """Stay available so the disconnected state is actually reportable."""
        return True

    @property
    def is_on(self) -> bool:
        """Return whether the device is connected."""
        return bool(self.coordinator.data.connected)


class StorzBickelBinarySensor(StorzBickelEntity, BinarySensorEntity):
    """A binary sensor decoded from the device's status registers."""

    entity_description: StorzBickelBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: StorzBickelCoordinator,
        description: StorzBickelBinarySensorEntityDescription,
    ) -> None:
        """Initialise the binary sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    def _device_supports(self) -> bool:
        """Return whether this device can use the entity."""
        return self.entity_description.available_fn(self.data)

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
