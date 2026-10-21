"""Number platform for the Storz & Bickel BLE integration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    MAX_AUTO_OFF_MINUTES,
    MAX_TEMP,
    MIN_AUTO_OFF_MINUTES,
    MIN_TEMP,
    TEMP_STEP,
)
from .coordinator import StorzBickelConfigEntry, StorzBickelCoordinator
from .entity import StorzBickelEntity, always
from .families import ALL_FAMILIES, PORTABLE, QVAP, VOLCANO_ONLY, Family


def _controllable(state: Any) -> bool:
    """Return whether the device accepts commands (old Crafty firmware does not)."""
    return bool(getattr(state, "controllable", True))


# The coordinator owns every read, and writes are already serialised by the
# single GATT lock in volcano.py, so Home Assistant does not need to throttle.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class StorzBickelNumberEntityDescription(NumberEntityDescription):
    """Describes a number."""

    value_fn: Callable[[Any], float | None]
    set_fn: Callable[[StorzBickelCoordinator, float], Awaitable[None]]
    # Which device families have this entity, and whether a given device of
    # them can use it right now.
    families: frozenset[Family] = VOLCANO_ONLY
    available_fn: Callable[[Any], bool] = always


NUMBERS: tuple[StorzBickelNumberEntityDescription, ...] = (
    StorzBickelNumberEntityDescription(
        key="target_temperature",
        translation_key="target_temperature",
        families=ALL_FAMILIES,
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=MIN_TEMP,
        native_max_value=MAX_TEMP,
        native_step=TEMP_STEP,
        value_fn=lambda state: state.target_temperature,
        set_fn=lambda coordinator, value: coordinator.async_set_temperature(value),
    ),
    StorzBickelNumberEntityDescription(
        key="auto_off_time",
        translation_key="auto_off_time",
        entity_category=EntityCategory.CONFIG,
        device_class=NumberDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        native_min_value=MIN_AUTO_OFF_MINUTES,
        native_max_value=MAX_AUTO_OFF_MINUTES,
        native_step=1,
        value_fn=lambda state: state.auto_off_minutes,
        set_fn=lambda coordinator, value: coordinator.async_set_auto_off_minutes(
            int(value)
        ),
    ),
    # -- portables (experimental) --
    StorzBickelNumberEntityDescription(
        key="boost_temperature",
        translation_key="boost_temperature",
        device_class=NumberDeviceClass.TEMPERATURE_DELTA,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=1,
        native_max_value=99,
        native_step=1,
        entity_category=EntityCategory.CONFIG,
        families=PORTABLE,
        available_fn=_controllable,
        value_fn=lambda state: state.boost_offset,
        set_fn=lambda coordinator, value: coordinator.async_command(
            coordinator.family_device.async_set_boost(value)
        ),
    ),
    StorzBickelNumberEntityDescription(
        key="superboost_temperature",
        translation_key="superboost_temperature",
        device_class=NumberDeviceClass.TEMPERATURE_DELTA,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=1,
        native_max_value=99,
        native_step=1,
        entity_category=EntityCategory.CONFIG,
        families=QVAP,
        value_fn=lambda state: state.superboost_offset,
        set_fn=lambda coordinator, value: coordinator.async_command(
            coordinator.family_device.async_set_superboost(value)
        ),
    ),
    StorzBickelNumberEntityDescription(
        key="led_brightness",
        translation_key="led_brightness",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=0,
        native_max_value=100,
        native_step=10,
        entity_category=EntityCategory.CONFIG,
        families=frozenset({Family.CRAFTY}),
        value_fn=lambda state: state.led_brightness,
        set_fn=lambda coordinator, value: coordinator.async_command(
            coordinator.family_device.async_set_led_brightness(int(value))
        ),
    ),
    StorzBickelNumberEntityDescription(
        key="display_brightness",
        translation_key="display_brightness",
        native_min_value=1,
        native_max_value=9,
        native_step=1,
        entity_category=EntityCategory.CONFIG,
        families=QVAP,
        value_fn=lambda state: state.brightness,
        set_fn=lambda coordinator, value: coordinator.async_command(
            coordinator.family_device.async_set_brightness(int(value))
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StorzBickelConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the numbers."""
    coordinator = entry.runtime_data
    async_add_entities(
        StorzBickelNumber(coordinator, description)
        for description in NUMBERS
        if coordinator.family in description.families
    )


class StorzBickelNumber(StorzBickelEntity, NumberEntity):
    """A number."""

    _controls_device = True

    entity_description: StorzBickelNumberEntityDescription

    def __init__(
        self,
        coordinator: StorzBickelCoordinator,
        description: StorzBickelNumberEntityDescription,
    ) -> None:
        """Initialise the number."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_max_value(self) -> float:
        """Return the maximum, which for the target depends on the device."""
        if self.entity_description.key == "target_temperature":
            return float(self.coordinator.device.max_temp)
        return super().native_max_value

    @property
    def native_value(self) -> float | None:
        """Return the current value."""
        return self.entity_description.value_fn(self.data)

    async def async_set_native_value(self, value: float) -> None:
        """Write a new value to the device."""
        await self.entity_description.set_fn(self.coordinator, value)
