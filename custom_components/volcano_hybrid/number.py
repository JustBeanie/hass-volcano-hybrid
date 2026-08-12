"""Number platform for the Volcano Hybrid integration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
)
from homeassistant.const import EntityCategory, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    MAX_AUTO_OFF_MINUTES,
    MAX_TEMP,
    MIN_AUTO_OFF_MINUTES,
    MIN_TEMP,
    TEMP_STEP,
)
from .coordinator import VolcanoConfigEntry, VolcanoDataUpdateCoordinator
from .entity import VolcanoEntity
from .volcano import VolcanoState


@dataclass(frozen=True, kw_only=True)
class VolcanoNumberEntityDescription(NumberEntityDescription):
    """Describes a Volcano Hybrid number."""

    value_fn: Callable[[VolcanoState], float | None]
    set_fn: Callable[[VolcanoDataUpdateCoordinator, float], Awaitable[None]]


NUMBERS: tuple[VolcanoNumberEntityDescription, ...] = (
    VolcanoNumberEntityDescription(
        key="target_temperature",
        translation_key="target_temperature",
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=MIN_TEMP,
        native_max_value=MAX_TEMP,
        native_step=TEMP_STEP,
        value_fn=lambda state: state.target_temperature,
        set_fn=lambda coordinator, value: coordinator.async_set_temperature(value),
    ),
    VolcanoNumberEntityDescription(
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
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VolcanoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Volcano Hybrid numbers."""
    coordinator = entry.runtime_data
    async_add_entities(
        VolcanoNumber(coordinator, description) for description in NUMBERS
    )


class VolcanoNumber(VolcanoEntity, NumberEntity):
    """A Volcano Hybrid number."""

    entity_description: VolcanoNumberEntityDescription

    def __init__(
        self,
        coordinator: VolcanoDataUpdateCoordinator,
        description: VolcanoNumberEntityDescription,
    ) -> None:
        """Initialise the number."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        """Return the current value."""
        return self.entity_description.value_fn(self.data)

    async def async_set_native_value(self, value: float) -> None:
        """Write a new value to the device."""
        await self.entity_description.set_fn(self.coordinator, value)
