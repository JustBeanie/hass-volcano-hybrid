"""Sensor platform for the Volcano Hybrid integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

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
from .volcano import FAULT_CODES, VolcanoState

# The coordinator owns every read, and writes are already serialised by the
# single GATT lock in volcano.py, so Home Assistant does not need to throttle.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class VolcanoSensorEntityDescription(SensorEntityDescription):
    """Describes a Volcano Hybrid sensor."""

    value_fn: Callable[[VolcanoState], StateType]
    # Diagnostic sensors must stay readable while the device is unreachable.
    available_when_disconnected: bool = False
    attributes_fn: Callable[[VolcanoState], dict[str, Any]] | None = None


LAST_FAULT_NONE = "none"
LAST_FAULT_UNRECOGNISED = "unrecognised"
LAST_FAULT_OPTIONS = [
    LAST_FAULT_NONE,
    *(slug for slug, _ in FAULT_CODES.values()),
    LAST_FAULT_UNRECOGNISED,
]


def _last_fault(state: VolcanoState) -> str | None:
    """Name the newest fault in the device's error history."""
    code = state.last_fault
    if code is None:
        return None
    if code == 0:
        return LAST_FAULT_NONE
    if code in FAULT_CODES:
        return FAULT_CODES[code][0]
    return LAST_FAULT_UNRECOGNISED


def _fault_log_attributes(state: VolcanoState) -> dict[str, Any]:
    """Return the whole error history, decoded and raw."""
    return {
        "code": state.last_fault or None,
        "log": [
            {
                "code": code,
                "hex": f"0x{code:02X}",
                "fault": FAULT_CODES.get(code, (None, "Unrecognised fault"))[1],
            }
            for code in state.fault_log or []
        ],
        "error_history_1": state.raw.get("error_history_1"),
        "error_history_2": state.raw.get("error_history_2"),
        # The spec takes this order from the firmware's design; it has not been
        # confirmed on a device, so say so where the order is shown.
        "order": "newest first (history 1, then history 2; unconfirmed)",
    }


def _raw_sensor(key: str) -> VolcanoSensorEntityDescription:
    """Describe a sensor showing one raw wire value as hex."""
    return VolcanoSensorEntityDescription(
        key=key,
        translation_key=key,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        available_when_disconnected=True,
        value_fn=lambda state: state.raw.get(key),
    )


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
    VolcanoSensorEntityDescription(
        key="last_fault",
        translation_key="last_fault",
        device_class=SensorDeviceClass.ENUM,
        options=LAST_FAULT_OPTIONS,
        entity_category=EntityCategory.DIAGNOSTIC,
        available_when_disconnected=True,
        value_fn=_last_fault,
        attributes_fn=_fault_log_attributes,
    ),
    # Status register 1 is the existing "raw_register" sensor above.
    _raw_sensor("status_register_2"),
    _raw_sensor("status_register_3"),
    _raw_sensor("status_register_4"),
    _raw_sensor("status_register_5"),
    _raw_sensor("error_history_1"),
    _raw_sensor("error_history_2"),
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
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose the raw wire values on the debug sensor."""
        if (attributes_fn := self.entity_description.attributes_fn) is not None:
            return attributes_fn(self.data)
        if self.entity_description.key != "raw_register":
            return None
        return dict(self.data.raw)
