"""Light platform for the Volcano Hybrid screen."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, LightEntity

# ColorMode comes from .const because the package root re-exports it
# implicitly, which mypy rejects under no_implicit_reexport.
from homeassistant.components.light.const import ColorMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.color import brightness_to_value, value_to_brightness

from .const import DEFAULT_BRIGHTNESS
from .coordinator import StorzBickelConfigEntry, StorzBickelCoordinator
from .entity import StorzBickelEntity
from .families import Family

# The device takes 0-100; Home Assistant lights are 0-255.
BRIGHTNESS_SCALE = (1, 100)


# The coordinator owns every read, and writes are already serialised by the
# single GATT lock in volcano.py, so Home Assistant does not need to throttle.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StorzBickelConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Volcano Hybrid screen light."""
    # Only the Volcano has this.
    if entry.runtime_data.family is Family.VOLCANO_HYBRID:
        async_add_entities([VolcanoScreenLight(entry.runtime_data)])


class VolcanoScreenLight(StorzBickelEntity, LightEntity):
    """The Volcano Hybrid screen backlight."""

    _controls_device = True

    _attr_translation_key = "screen"
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, coordinator: StorzBickelCoordinator) -> None:
        """Initialise the light."""
        super().__init__(coordinator, "screen")

    @property
    def brightness(self) -> int | None:
        """Return the brightness on Home Assistant's 0-255 scale."""
        if (percent := self.data.brightness) is None:
            return None
        return value_to_brightness(BRIGHTNESS_SCALE, percent)

    @property
    def is_on(self) -> bool | None:
        """Return whether the screen is lit."""
        percent: int | None = self.data.brightness
        if percent is None:
            return None
        return percent > 0

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the screen on, optionally at a given brightness."""
        if (brightness := kwargs.get(ATTR_BRIGHTNESS)) is not None:
            percent = round(brightness_to_value(BRIGHTNESS_SCALE, brightness))
        else:
            percent = self.data.brightness or DEFAULT_BRIGHTNESS
        await self.coordinator.async_set_brightness(percent)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the screen off."""
        await self.coordinator.async_set_brightness(0)
