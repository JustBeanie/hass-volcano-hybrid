"""Reconnect buttons for the Volcano Hybrid.

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

from .coordinator import VolcanoConfigEntry, VolcanoDataUpdateCoordinator
from .entity import VolcanoEntity

# The coordinator serialises everything these do, so no throttling is needed.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class VolcanoButtonEntityDescription(ButtonEntityDescription):
    """Describes a Volcano Hybrid button."""

    press_fn: Callable[[VolcanoDataUpdateCoordinator], Awaitable[None]]


BUTTONS: tuple[VolcanoButtonEntityDescription, ...] = (
    VolcanoButtonEntityDescription(
        key="reconnect",
        translation_key="reconnect",
        device_class=ButtonDeviceClass.RESTART,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        press_fn=lambda coordinator: coordinator.async_reconnect(),
    ),
    VolcanoButtonEntityDescription(
        key="delayed_reconnect",
        translation_key="delayed_reconnect",
        device_class=ButtonDeviceClass.RESTART,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        press_fn=lambda coordinator: coordinator.async_delayed_reconnect(),
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
        VolcanoButton(coordinator, description) for description in BUTTONS
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
        """Stay pressable while disconnected: reconnecting is the point."""
        return True

    async def async_press(self) -> None:
        """Press the button."""
        await self.entity_description.press_fn(self.coordinator)
