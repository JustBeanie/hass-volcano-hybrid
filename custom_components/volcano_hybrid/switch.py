"""Switch platform for the Volcano Hybrid integration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import VolcanoConfigEntry, VolcanoDataUpdateCoordinator
from .entity import VolcanoEntity
from .volcano import VolcanoState


@dataclass(frozen=True, kw_only=True)
class VolcanoSwitchEntityDescription(SwitchEntityDescription):
    """Describes a Volcano Hybrid switch."""

    is_on_fn: Callable[[VolcanoState], bool | None]
    set_fn: Callable[[VolcanoDataUpdateCoordinator, bool], Awaitable[None]]


SWITCHES: tuple[VolcanoSwitchEntityDescription, ...] = (
    VolcanoSwitchEntityDescription(
        key="heater",
        translation_key="heater",
        is_on_fn=lambda state: state.heater_on,
        set_fn=lambda coordinator, on: (
            coordinator.async_turn_heater_on()
            if on
            else coordinator.async_turn_heater_off()
        ),
    ),
    VolcanoSwitchEntityDescription(
        key="fan",
        translation_key="fan",
        is_on_fn=lambda state: state.fan_on,
        set_fn=lambda coordinator, on: (
            coordinator.async_turn_fan_on() if on else coordinator.async_turn_fan_off()
        ),
    ),
    VolcanoSwitchEntityDescription(
        key="register3",
        translation_key="register3",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        is_on_fn=lambda state: state.register3,
        set_fn=lambda coordinator, on: coordinator.async_set_register3(on),
    ),
    VolcanoSwitchEntityDescription(
        key="register2",
        translation_key="register2",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        is_on_fn=lambda state: state.register2,
        set_fn=lambda coordinator, on: coordinator.async_set_register2(on),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VolcanoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Volcano Hybrid switches."""
    coordinator = entry.runtime_data
    async_add_entities(
        VolcanoSwitch(coordinator, description) for description in SWITCHES
    )


class VolcanoSwitch(VolcanoEntity, SwitchEntity):
    """A Volcano Hybrid switch."""

    entity_description: VolcanoSwitchEntityDescription

    def __init__(
        self,
        coordinator: VolcanoDataUpdateCoordinator,
        description: VolcanoSwitchEntityDescription,
    ) -> None:
        """Initialise the switch."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return whether the switch is on."""
        return self.entity_description.is_on_fn(self.data)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        await self.entity_description.set_fn(self.coordinator, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        await self.entity_description.set_fn(self.coordinator, False)
