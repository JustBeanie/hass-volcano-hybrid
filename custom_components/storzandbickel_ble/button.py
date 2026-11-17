"""Buttons: reconnect, and finding a portable device.

The link heals itself, so these are manual overrides: one rebuilds the link
now, the other stays off it long enough for a fresh advertisement so the
reconnect can go through whichever adapter or proxy now hears it best.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import StorzBickelConfigEntry, StorzBickelCoordinator
from .entity import (
    StorzBickelDescribedEntity,
    StorzBickelEntityDescription,
    descriptions_for,
    findable,
)
from .families import ALL_FAMILIES, PORTABLE

# The coordinator serializes everything these do, so no throttling is needed.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class StorzBickelButtonEntityDescription(
    ButtonEntityDescription, StorzBickelEntityDescription
):
    """Describes a button."""

    press_fn: Callable[[StorzBickelCoordinator], Awaitable[None]]


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
        available_fn=findable,
        press_fn=lambda coordinator: coordinator.async_command(
            coordinator.portable.async_find()
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
        for description in descriptions_for(coordinator, BUTTONS)
    )


class StorzBickelButton(StorzBickelDescribedEntity, ButtonEntity):
    """A button."""

    entity_description: StorzBickelButtonEntityDescription

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
