"""Reconnect buttons for the Volcano Hybrid.

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

from .coordinator import VolcanoConfigEntry, VolcanoDataUpdateCoordinator
from .entity import VolcanoEntity, always
from .families import ALL_FAMILIES, PORTABLE, VOLCANO_ONLY, Family

# The coordinator serialises everything these do, so no throttling is needed.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class VolcanoButtonEntityDescription(ButtonEntityDescription):
    """Describes a Volcano Hybrid button."""

    press_fn: Callable[[VolcanoDataUpdateCoordinator], Awaitable[None]]
    # Which device families have this entity, and whether a given device of
    # them can use it right now.
    families: frozenset[Family] = VOLCANO_ONLY
    available_fn: Callable[[Any], bool] = always


BUTTONS: tuple[VolcanoButtonEntityDescription, ...] = (
    VolcanoButtonEntityDescription(
        key="reconnect",
        translation_key="reconnect",
        families=ALL_FAMILIES,
        device_class=ButtonDeviceClass.RESTART,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        press_fn=lambda coordinator: coordinator.async_reconnect(),
    ),
    VolcanoButtonEntityDescription(
        key="delayed_reconnect",
        translation_key="delayed_reconnect",
        families=ALL_FAMILIES,
        device_class=ButtonDeviceClass.RESTART,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        press_fn=lambda coordinator: coordinator.async_delayed_reconnect(),
    ),
    VolcanoButtonEntityDescription(
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
    entry: VolcanoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Volcano Hybrid buttons."""
    coordinator = entry.runtime_data
    async_add_entities(
        VolcanoButton(coordinator, description)
        for description in BUTTONS
        if coordinator.family in description.families
    )


class VolcanoButton(VolcanoEntity, ButtonEntity):
    """A Volcano Hybrid button."""

    entity_description: VolcanoButtonEntityDescription

    def __init__(
        self,
        coordinator: VolcanoDataUpdateCoordinator,
        description: VolcanoButtonEntityDescription,
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
