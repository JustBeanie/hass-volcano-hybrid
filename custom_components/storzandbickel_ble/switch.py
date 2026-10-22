"""Switch platform for the Storz & Bickel BLE integration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .ble_device import DeviceState
from .coordinator import StorzBickelConfigEntry, StorzBickelCoordinator
from .crafty import CraftyDevice
from .entity import StorzBickelEntity, always, controllable
from .families import ALL_FAMILIES, PORTABLE, QVAP, VOLCANO_ONLY, Family
from .qvap import QvapDevice

# The coordinator owns every read, and writes are already serialised by the
# single GATT lock in volcano.py, so Home Assistant does not need to throttle.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class StorzBickelSwitchEntityDescription(SwitchEntityDescription):
    """Describes a switch."""

    is_on_fn: Callable[[Any], bool | None]
    set_fn: Callable[[StorzBickelCoordinator, bool], Awaitable[None]]
    # Which device families have this entity, and whether a given device of
    # them can use it right now.
    families: frozenset[Family] = VOLCANO_ONLY
    available_fn: Callable[[DeviceState], bool] = always


SWITCHES: tuple[StorzBickelSwitchEntityDescription, ...] = (
    StorzBickelSwitchEntityDescription(
        key="heater",
        translation_key="heater",
        families=ALL_FAMILIES,
        available_fn=controllable,
        is_on_fn=lambda state: state.heater_on,
        set_fn=lambda coordinator, on: (
            coordinator.async_turn_heater_on()
            if on
            else coordinator.async_turn_heater_off()
        ),
    ),
    StorzBickelSwitchEntityDescription(
        key="fan",
        translation_key="fan",
        is_on_fn=lambda state: state.fan_on,
        set_fn=lambda coordinator, on: (
            coordinator.async_turn_fan_on() if on else coordinator.async_turn_fan_off()
        ),
    ),
    # The keys stay "register3" / "register2" from before these were decoded, so
    # existing unique_ids and entity_ids survive the rename.
    StorzBickelSwitchEntityDescription(
        key="register3",
        translation_key="vibration",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        is_on_fn=lambda state: state.vibration,
        set_fn=lambda coordinator, on: coordinator.async_set_vibration(on),
    ),
    StorzBickelSwitchEntityDescription(
        key="register2",
        translation_key="display_while_cooling",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        is_on_fn=lambda state: state.display_while_cooling,
        set_fn=lambda coordinator, on: coordinator.async_set_display_while_cooling(on),
    ),
    StorzBickelSwitchEntityDescription(
        key="display_fahrenheit",
        translation_key="display_fahrenheit",
        families=frozenset({Family.VOLCANO_HYBRID}) | QVAP,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        is_on_fn=lambda state: state.display_fahrenheit,
        set_fn=lambda coordinator, on: coordinator.async_set_display_fahrenheit(on),
    ),
    # -- portables (experimental) --
    StorzBickelSwitchEntityDescription(
        key="vibration",
        translation_key="vibration",
        entity_category=EntityCategory.CONFIG,
        families=PORTABLE,
        available_fn=controllable,
        is_on_fn=lambda state: state.vibration,
        set_fn=lambda coordinator, on: coordinator.async_set_vibration(on),
    ),
    StorzBickelSwitchEntityDescription(
        key="charge_led",
        translation_key="charge_led",
        entity_category=EntityCategory.CONFIG,
        families=frozenset({Family.CRAFTY}),
        available_fn=controllable,
        is_on_fn=lambda state: state.charge_led,
        set_fn=lambda coordinator, on: coordinator.async_command(
            coordinator.device_as(CraftyDevice).async_set_charge_led(on)
        ),
    ),
    StorzBickelSwitchEntityDescription(
        key="auto_ble_shutdown",
        translation_key="auto_ble_shutdown",
        entity_category=EntityCategory.CONFIG,
        families=frozenset({Family.CRAFTY}),
        available_fn=controllable,
        is_on_fn=lambda state: state.auto_ble_shutdown,
        set_fn=lambda coordinator, on: coordinator.async_command(
            coordinator.device_as(CraftyDevice).async_set_auto_ble_shutdown(on)
        ),
    ),
    StorzBickelSwitchEntityDescription(
        key="charge_optimisation",
        translation_key="charge_optimisation",
        entity_category=EntityCategory.CONFIG,
        families=QVAP,
        is_on_fn=lambda state: state.charge_optimisation,
        set_fn=lambda coordinator, on: coordinator.async_command(
            coordinator.device_as(QvapDevice).async_set_charge_optimisation(on)
        ),
    ),
    StorzBickelSwitchEntityDescription(
        key="charge_limit",
        translation_key="charge_limit",
        entity_category=EntityCategory.CONFIG,
        families=QVAP,
        is_on_fn=lambda state: state.charge_limit,
        set_fn=lambda coordinator, on: coordinator.async_command(
            coordinator.device_as(QvapDevice).async_set_charge_limit(on)
        ),
    ),
    StorzBickelSwitchEntityDescription(
        key="boost_visualisation",
        translation_key="boost_visualisation",
        entity_category=EntityCategory.CONFIG,
        families=QVAP,
        is_on_fn=lambda state: state.boost_visualisation,
        set_fn=lambda coordinator, on: coordinator.async_command(
            coordinator.device_as(QvapDevice).async_set_boost_visualisation(on)
        ),
    ),
    StorzBickelSwitchEntityDescription(
        key="boost_timeout_disabled",
        translation_key="boost_timeout_disabled",
        entity_category=EntityCategory.CONFIG,
        families=QVAP,
        is_on_fn=lambda state: state.boost_timeout_disabled,
        set_fn=lambda coordinator, on: coordinator.async_command(
            coordinator.device_as(QvapDevice).async_set_boost_timeout_disabled(on)
        ),
    ),
    StorzBickelSwitchEntityDescription(
        key="permanent_bluetooth",
        translation_key="permanent_bluetooth",
        entity_category=EntityCategory.CONFIG,
        families=frozenset({Family.VEAZY}),
        is_on_fn=lambda state: state.permanent_bluetooth,
        set_fn=lambda coordinator, on: coordinator.async_command(
            coordinator.device_as(QvapDevice).async_set_permanent_bluetooth(on)
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StorzBickelConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the switches."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            StorzBickelAutoConnectSwitch(coordinator),
            *(
                StorzBickelSwitch(coordinator, description)
                for description in SWITCHES
                if coordinator.family in description.families
            ),
        ]
    )


class StorzBickelAutoConnectSwitch(StorzBickelEntity, SwitchEntity):
    """Whether Home Assistant connects to the vaporizer on its own.

    Off hands the single Bluetooth connection back, so the Storz & Bickel app
    can use the vaporizer; commands still connect on demand. The choice lives
    in the entry options, so it survives a restart.
    """

    _attr_translation_key = "auto_connect"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: StorzBickelCoordinator) -> None:
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


class StorzBickelSwitch(StorzBickelEntity, SwitchEntity):
    """A switch."""

    _controls_device = True

    entity_description: StorzBickelSwitchEntityDescription

    def __init__(
        self,
        coordinator: StorzBickelCoordinator,
        description: StorzBickelSwitchEntityDescription,
    ) -> None:
        """Initialise the switch."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    def _device_supports(self) -> bool:
        """Return whether this device can use the entity."""
        return self.entity_description.available_fn(self.data)

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
