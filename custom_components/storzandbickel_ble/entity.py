"""Base entity for the Storz & Bickel BLE integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import (
    CONNECTION_BLUETOOTH,
    DeviceInfo,
    format_mac,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .ble_device import DeviceState
from .const import DOMAIN, MANUFACTURER
from .coordinator import StorzBickelCoordinator
from .crafty import CraftyState
from .families import FAMILY_NAMES


class StorzBickelEntity(CoordinatorEntity[StorzBickelCoordinator]):
    """Common base for every Storz & Bickel entity."""

    _attr_has_entity_name = True
    # True for entities that send commands. With auto-connect off they stay
    # usable while disconnected, because using them is what opens the link;
    # read-only entities would only show stale values, so they do not.
    _controls_device = False

    def __init__(self, coordinator: StorzBickelCoordinator, key: str) -> None:
        """Initialise the entity and attach it to the device."""
        super().__init__(coordinator)
        formatted_mac = format_mac(coordinator.address)
        self._attr_unique_id = f"{formatted_mac}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, formatted_mac)},
            connections={(CONNECTION_BLUETOOTH, coordinator.address)},
            manufacturer=MANUFACTURER,
            model=FAMILY_NAMES[coordinator.family],
            # The device's own name for its model class, e.g. "HYBRID".
            model_id=coordinator.data.model,
            name=coordinator.config_entry.title,
            serial_number=coordinator.data.serial_number,
            sw_version=coordinator.data.firmware_version,
        )

    @property
    def data(self) -> DeviceState:
        """Return the current state, as the device family's own dataclass."""
        return self.coordinator.data

    @property
    def available(self) -> bool:
        """Return whether the device is reachable, or can be reached on demand."""
        if not super().available:
            return False
        if not self._device_supports():
            return False
        if self.coordinator.data.connected:
            return True
        return self._controls_device and not self.coordinator.auto_connect

    def _device_supports(self) -> bool:
        """Return whether this particular device can use the entity at all.

        Description-driven entities answer from ``available_fn``: a Crafty on
        old firmware takes no commands, and only a Crafty+ can be told to buzz.
        """
        return True


def always(_state: DeviceState) -> bool:
    """Return True: the default for an entity every device of its family has."""
    return True


def controllable(state: DeviceState) -> bool:
    """Return whether the device accepts commands; old Crafty firmware does not."""
    return not isinstance(state, CraftyState) or state.controllable


def findable(state: DeviceState) -> bool:
    """Return whether the device can be told to signal; of Craftys, only a +."""
    return not isinstance(state, CraftyState) or state.is_plus
