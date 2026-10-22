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

# The coordinator owns every read, and writes are already serialised by the
# single GATT lock in volcano.py, so Home Assistant does not need to throttle.
PARALLEL_UPDATES = 0


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
    # The keys stay "register3" / "register2" from before these were decoded, so
    # existing unique_ids and entity_ids survive the rename.
    VolcanoSwitchEntityDescription(
        key="register3",
        translation_key="vibration",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        is_on_fn=lambda state: state.vibration,
        set_fn=lambda coordinator, on: coordinator.async_set_vibration(on),
    ),
    VolcanoSwitchEntityDescription(
        key="register2",
        translation_key="display_while_cooling",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        is_on_fn=lambda state: state.display_while_cooling,
        set_fn=lambda coordinator, on: coordinator.async_set_display_while_cooling(on),
    ),
    VolcanoSwitchEntityDescription(
        key="display_fahrenheit",
        translation_key="display_fahrenheit",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        is_on_fn=lambda state: state.display_fahrenheit,
        set_fn=lambda coordinator, on: coordinator.async_set_display_fahrenheit(on),
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
        [
            VolcanoAutoConnectSwitch(coordinator),
            *(VolcanoSwitch(coordinator, description) for description in SWITCHES),
        ]
    )


class VolcanoAutoConnectSwitch(VolcanoEntity, SwitchEntity):
    """Whether Home Assistant connects to the vaporizer on its own.

    Off hands the single Bluetooth connection back, so the Storz & Bickel app
    can use the vaporizer; commands still connect on demand. The choice lives
    in the entry options, so it survives a restart.
    """

    _attr_translation_key = "auto_connect"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: VolcanoDataUpdateCoordinator) -> None:
        """Initialise the switch."""
        super().__init__(coordinator, "auto_connect")

    @property
    def available(self) -> bool:
        """Stay usable while disconnected: turning it on is how to reconnect."""
        return True

    @property
    def is_on(self) -> bool:
        """Return whether auto-connect is on."""
        return self.coordinator.auto_connect

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Connect on its own again."""
        await self.coordinator.async_set_auto_connect(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Release the link and stop connecting on its own."""
        await self.coordinator.async_set_auto_connect(False)
        self.async_write_ha_state()


class VolcanoSwitch(VolcanoEntity, SwitchEntity):
    """A Volcano Hybrid switch."""

    _controls_device = True

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
