"""Buttons: reconnect, and finding a portable device.

The link heals itself, so these are manual overrides: one rebuilds the link
now, the other stays off it long enough for a fresh advertisement so the
reconnect can go through whichever adapter or proxy now hears it best.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import StorzBickelConfigEntry, StorzBickelCoordinator
from .entity import StorzBickelEntity, always
from .families import ALL_FAMILIES, PORTABLE, VOLCANO_ONLY, Family

# The coordinator serialises everything these do, so no throttling is needed.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class StorzBickelButtonEntityDescription(ButtonEntityDescription):
    """Describes a button."""

    press_fn: Callable[[StorzBickelCoordinator], Awaitable[None]]
    # Which device families have this entity, and whether a given device of
    # them can use it right now.
    families: frozenset[Family] = VOLCANO_ONLY
    available_fn: Callable[[Any], bool] = always


BUTTONS: tuple[StorzBickelButtonEntityDescription, ...] = (
    StorzBickelButtonEntityDescription(
        key="reconnect",
        translation_key="reconnect",
        families=ALL_FAMILIES,
        device_class=ButtonDeviceClass.RESTART,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        press_fn=lambda coordinator: coordinator.async_reconnect(),
    ),
    StorzBickelButtonEntityDescription(
        key="delayed_reconnect",
        translation_key="delayed_reconnect",
        families=ALL_FAMILIES,
        device_class=ButtonDeviceClass.RESTART,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        press_fn=lambda coordinator: coordinator.async_delayed_reconnect(),
    ),
    StorzBickelButtonEntityDescription(
        # Makes the device buzz or flash so it can be found. Only the Crafty+
        # has it among the Craftys.
        key="find",
        translation_key="find",
        device_class=ButtonDeviceClass.IDENTIFY,
        families=PORTABLE,
        available_fn=lambda state: getattr(state, "is_plus", True),
        press_fn=lambda coordinator: coordinator.async_command(
            coordinator.family_device.async_find()
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StorzBickelConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the buttons."""
    coordinator = entry.runtime_data
    async_add_entities(
        StorzBickelButton(coordinator, description)
        for description in BUTTONS
        if coordinator.family in description.families
    )


class StorzBickelButton(StorzBickelEntity, ButtonEntity):
    """A button."""

    entity_description: StorzBickelButtonEntityDescription

    def __init__(
        self,
        coordinator: StorzBickelCoordinator,
        description: StorzBickelButtonEntityDescription,
    ) -> None:
        """Initialise the button."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Stay pressable while disconnected: reconnecting is the point.

        Unless the device cannot do it at all, like "find" on a Crafty that
        is not a Crafty+.
        """
        return self.entity_description.available_fn(self.data)

    async def async_press(self) -> None:
        """Press the button."""
        await self.entity_description.press_fn(self.coordinator)
