"""Firmware update entity for the Volcano Hybrid.

Report-only: it says whether the vaporizer runs the newest firmware this
release knows of and links to where it is installed from. It is disabled by
default because the comparison has never been checked against a device that
was actually behind.
"""

from __future__ import annotations

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import VolcanoConfigEntry, VolcanoDataUpdateCoordinator
from .entity import VolcanoEntity
from .firmware import (
    FIRMWARE_UPDATE_URL,
    format_firmware_version,
    latest_firmware_version,
    parse_firmware_version,
)

# The coordinator owns every read, so Home Assistant does not need to throttle.
PARALLEL_UPDATES = 0

FIRMWARE = UpdateEntityDescription(
    key="firmware",
    translation_key="firmware",
    device_class=UpdateDeviceClass.FIRMWARE,
    entity_category=EntityCategory.CONFIG,
    entity_registry_enabled_default=False,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VolcanoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the firmware update entity."""
    async_add_entities([VolcanoFirmwareUpdate(entry.runtime_data)])


class VolcanoFirmwareUpdate(VolcanoEntity, UpdateEntity):
    """Whether the vaporizer runs the newest known firmware."""

    entity_description = FIRMWARE
    _attr_release_url = FIRMWARE_UPDATE_URL

    def __init__(self, coordinator: VolcanoDataUpdateCoordinator) -> None:
        """Initialise the update entity."""
        super().__init__(coordinator, FIRMWARE.key)

    @property
    def available(self) -> bool:
        """Stay available while unplugged: firmware does not change on its own."""
        return self.data.firmware_version is not None

    @property
    def installed_version(self) -> str | None:
        """Return the firmware on the vaporizer."""
        installed = parse_firmware_version(self.data.firmware_version)
        return None if installed is None else format_firmware_version(installed)

    @property
    def latest_version(self) -> str | None:
        """Return the newest firmware known to this release."""
        installed = parse_firmware_version(self.data.firmware_version)
        if installed is None:
            return None
        return format_firmware_version(latest_firmware_version(installed))
