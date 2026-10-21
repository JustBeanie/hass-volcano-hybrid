"""Sensor platform for the Storz & Bickel BLE integration."""

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
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .ble_device import (
    ConnectionFailure,
)
from .coordinator import (
    HEALTH_AUTO_CONNECT_OFF,
    HEALTH_CONNECTED,
    StorzBickelConfigEntry,
    StorzBickelCoordinator,
)
from .entity import StorzBickelEntity, always
from .families import ALL_FAMILIES, PORTABLE, QVAP, VOLCANO_ONLY, Family
from .qvap import HEATER_MODES
from .volcano import (
    FAULT_CODES,
    VolcanoState,
)

# The coordinator owns every read, and writes are already serialised by the
# single GATT lock in volcano.py, so Home Assistant does not need to throttle.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class StorzBickelSensorEntityDescription(SensorEntityDescription):
    """Describes a sensor."""

    value_fn: Callable[[Any], StateType]
    # Diagnostic sensors must stay readable while the device is unreachable.
    available_when_disconnected: bool = False
    attributes_fn: Callable[[Any], dict[str, Any]] | None = None
    # Which device families have this entity, and whether a given device of
    # them can use it right now.
    families: frozenset[Family] = VOLCANO_ONLY
    available_fn: Callable[[Any], bool] = always


LINK_HEALTH_OPTIONS = [
    HEALTH_CONNECTED,
    HEALTH_AUTO_CONNECT_OFF,
    *(str(kind) for kind in ConnectionFailure),
]

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


def _raw_sensor(key: str) -> StorzBickelSensorEntityDescription:
    """Describe a sensor showing one raw wire value as hex."""
    return StorzBickelSensorEntityDescription(
        key=key,
        translation_key=key,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        available_when_disconnected=True,
        value_fn=lambda state: state.raw.get(key),
    )


SENSORS: tuple[StorzBickelSensorEntityDescription, ...] = (
    StorzBickelSensorEntityDescription(
        key="temperature",
        translation_key="temperature",
        families=ALL_FAMILIES,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda state: state.current_temperature,
    ),
    StorzBickelSensorEntityDescription(
        key="connection_status",
        translation_key="connection_status",
        families=ALL_FAMILIES,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        available_when_disconnected=True,
        value_fn=lambda state: "Connected" if state.connected else "Disconnected",
    ),
    StorzBickelSensorEntityDescription(
        key="raw_register",
        translation_key="raw_register",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        available_when_disconnected=True,
        value_fn=lambda state: state.raw.get("status_register"),
    ),
    StorzBickelSensorEntityDescription(
        key="heater_status",
        translation_key="heater_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda state: "On" if state.heater_on else "Off",
    ),
    StorzBickelSensorEntityDescription(
        key="fan_status",
        translation_key="fan_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda state: "On" if state.fan_on else "Off",
    ),
    StorzBickelSensorEntityDescription(
        key="brightness_value",
        translation_key="brightness_value",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda state: state.brightness,
    ),
    StorzBickelSensorEntityDescription(
        key="serial_number",
        translation_key="serial_number",
        families=ALL_FAMILIES,
        entity_category=EntityCategory.DIAGNOSTIC,
        available_when_disconnected=True,
        value_fn=lambda state: state.serial_number,
    ),
    StorzBickelSensorEntityDescription(
        key="ble_firmware",
        translation_key="ble_firmware",
        entity_category=EntityCategory.DIAGNOSTIC,
        available_when_disconnected=True,
        value_fn=lambda state: state.ble_firmware_version,
    ),
    StorzBickelSensorEntityDescription(
        key="firmware_version",
        translation_key="firmware_version",
        families=ALL_FAMILIES,
        entity_category=EntityCategory.DIAGNOSTIC,
        available_when_disconnected=True,
        value_fn=lambda state: state.firmware_version,
    ),
    StorzBickelSensorEntityDescription(
        key="hours_operation",
        translation_key="hours_operation",
        families=frozenset({Family.VOLCANO_HYBRID, Family.CRAFTY}),
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.HOURS,
        available_when_disconnected=True,
        value_fn=lambda state: state.hours_of_operation,
    ),
    StorzBickelSensorEntityDescription(
        key="auto_off_time",
        translation_key="auto_off_time",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        available_when_disconnected=True,
        value_fn=lambda state: state.auto_off_minutes,
    ),
    StorzBickelSensorEntityDescription(
        key="last_fault",
        translation_key="last_fault",
        device_class=SensorDeviceClass.ENUM,
        options=LAST_FAULT_OPTIONS,
        entity_category=EntityCategory.DIAGNOSTIC,
        available_when_disconnected=True,
        value_fn=_last_fault,
        attributes_fn=_fault_log_attributes,
    ),
    StorzBickelSensorEntityDescription(
        key="rssi",
        translation_key="rssi",
        families=ALL_FAMILIES,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        available_when_disconnected=True,
        value_fn=lambda state: state.rssi,
    ),
    StorzBickelSensorEntityDescription(
        key="connected_via",
        translation_key="connected_via",
        families=ALL_FAMILIES,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda state: state.connected_via,
    ),
    StorzBickelSensorEntityDescription(
        key="mains_voltage",
        translation_key="mains_voltage",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        available_when_disconnected=True,
        value_fn=lambda state: state.mains_voltage,
    ),
    # Status register 1 is the existing "raw_register" sensor above.
    _raw_sensor("status_register_2"),
    _raw_sensor("status_register_3"),
    _raw_sensor("status_register_4"),
    _raw_sensor("status_register_5"),
    _raw_sensor("error_history_1"),
    _raw_sensor("error_history_2"),
    # -- portables (experimental) --
    StorzBickelSensorEntityDescription(
        key="battery",
        translation_key="battery",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        families=PORTABLE,
        value_fn=lambda state: state.battery,
    ),
    StorzBickelSensorEntityDescription(
        key="auto_off_countdown",
        translation_key="auto_off_countdown",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        families=PORTABLE,
        value_fn=lambda state: state.auto_off_countdown,
    ),
    StorzBickelSensorEntityDescription(
        key="heater_mode",
        translation_key="heater_mode",
        device_class=SensorDeviceClass.ENUM,
        options=list(HEATER_MODES),
        families=QVAP,
        value_fn=lambda state: state.heater_mode,
    ),
    StorzBickelSensorEntityDescription(
        key="heater_runtime",
        translation_key="heater_runtime",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.DIAGNOSTIC,
        available_when_disconnected=True,
        families=QVAP,
        value_fn=lambda state: state.heater_minutes,
    ),
    StorzBickelSensorEntityDescription(
        key="charging_time",
        translation_key="charging_time",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.DIAGNOSTIC,
        available_when_disconnected=True,
        families=QVAP,
        value_fn=lambda state: state.charge_minutes,
    ),
    StorzBickelSensorEntityDescription(
        key="colour",
        translation_key="colour",
        entity_category=EntityCategory.DIAGNOSTIC,
        available_when_disconnected=True,
        families=frozenset({Family.VEAZY}),
        value_fn=lambda state: state.colour,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StorzBickelConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            StorzBickelLinkHealthSensor(coordinator),
            *(
                StorzBickelSensor(coordinator, description)
                for description in SENSORS
                if coordinator.family in description.families
            ),
        ]
    )


class StorzBickelLinkHealthSensor(StorzBickelEntity, SensorEntity):
    """What the Bluetooth link is doing, and what it has recovered from.

    The link heals itself without telling anyone; this is where that shows.
    The counters start from zero whenever the integration is set up.
    """

    _attr_translation_key = "link_health"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_options = LINK_HEALTH_OPTIONS

    def __init__(self, coordinator: StorzBickelCoordinator) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, "link_health")

    @property
    def available(self) -> bool:
        """Stay available: an outage is exactly what this reports."""
        return True

    @property
    def native_value(self) -> str:
        """Return the link's current state."""
        return self.coordinator.health_state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the history behind the state."""
        health = self.coordinator.link_health
        device = self.coordinator.device
        return {
            "last_failure": str(health.last_failure) if health.last_failure else None,
            "last_failure_at": health.last_failure_at,
            "last_restored_at": health.last_restored_at,
            "last_outage_seconds": health.last_outage_seconds,
            "recoveries": health.recoveries,
            "link_resets": device.link_resets,
            "adapter_cache_resets": device.adapter_cache_resets,
        }


class StorzBickelSensor(StorzBickelEntity, SensorEntity):
    """A sensor."""

    entity_description: StorzBickelSensorEntityDescription

    def __init__(
        self,
        coordinator: StorzBickelCoordinator,
        description: StorzBickelSensorEntityDescription,
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
