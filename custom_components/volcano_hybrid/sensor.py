"""Sensor platform for the Volcano Hybrid integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .coordinator import VolcanoConfigEntry, VolcanoDataUpdateCoordinator
from .entity import VolcanoEntity
from .volcano import VolcanoState


@dataclass(frozen=True, kw_only=True)
class VolcanoSensorEntityDescription(SensorEntityDescription):
    """Describes a Volcano Hybrid sensor."""

    value_fn: Callable[[VolcanoState], StateType]
    # Diagnostic sensors must stay readable while the device is unreachable.
    available_when_disconnected: bool = False


SENSORS: tuple[VolcanoSensorEntityDescription, ...] = (
    VolcanoSensorEntityDescription(
        key="temperature",
        translation_key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda state: state.current_temperature,
    ),
    VolcanoSensorEntityDescription(
        key="connection_status",
        translation_key="connection_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        available_when_disconnected=True,
        value_fn=lambda state: "Connected" if state.connected else "Disconnected",
    ),
    VolcanoSensorEntityDescription(
        key="raw_register",
        translation_key="raw_register",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        available_when_disconnected=True,
        value_fn=lambda state: state.raw.get("status_register"),
    ),
    VolcanoSensorEntityDescription(
        key="heater_status",
        translation_key="heater_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda state: "On" if state.heater_on else "Off",
    ),
    VolcanoSensorEntityDescription(
        key="fan_status",
        translation_key="fan_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda state: "On" if state.fan_on else "Off",
    ),
    VolcanoSensorEntityDescription(
        key="brightness_value",
        translation_key="brightness_value",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda state: state.brightness,
    ),
    VolcanoSensorEntityDescription(
        key="serial_number",
        translation_key="serial_number",
        entity_category=EntityCategory.DIAGNOSTIC,
        available_when_disconnected=True,
        value_fn=lambda state: state.serial_number,
    ),
    VolcanoSensorEntityDescription(
        key="ble_firmware",
        translation_key="ble_firmware",
        entity_category=EntityCategory.DIAGNOSTIC,
        available_when_disconnected=True,
        value_fn=lambda state: state.ble_firmware_version,
    ),
    VolcanoSensorEntityDescription(
        key="firmware_version",
        translation_key="firmware_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        available_when_disconnected=True,
        value_fn=lambda state: state.firmware_version,
    ),
    VolcanoSensorEntityDescription(
        key="hours_operation",
        translation_key="hours_operation",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.HOURS,
        available_when_disconnected=True,
        value_fn=lambda state: state.hours_of_operation,
    ),
    VolcanoSensorEntityDescription(
        key="auto_off_time",
        translation_key="auto_off_time",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        available_when_disconnected=True,
        value_fn=lambda state: state.auto_off_minutes,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VolcanoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Volcano Hybrid sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        VolcanoSensor(coordinator, description) for description in SENSORS
    )


class VolcanoSensor(VolcanoEntity, SensorEntity):
    """A Volcano Hybrid sensor."""

    entity_description: VolcanoSensorEntityDescription

    def __init__(
        self,
        coordinator: VolcanoDataUpdateCoordinator,
        description: VolcanoSensorEntityDescription,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Return whether the sensor has a meaningful value."""
        if self.entity_description.available_when_disconnected:
            return (
                self.coordinator.last_update_success or self.coordinator.data.connected
            )
        return super().available

    @property
    def native_value(self) -> StateType:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.data)

    @property
    def extra_state_attributes(self) -> dict[str, str | None] | None:
        """Expose the raw wire values on the debug sensor."""
        if self.entity_description.key != "raw_register":
            return None
        return dict(self.data.raw)
