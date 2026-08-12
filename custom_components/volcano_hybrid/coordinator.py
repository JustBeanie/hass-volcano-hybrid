"""DataUpdateCoordinator for the Volcano Hybrid integration."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
import contextlib
import logging
from typing import Any

from homeassistant.components.bluetooth import (
    BluetoothChange,
    BluetoothServiceInfoBleak,
    async_ble_device_from_address,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    ANIMATION_ASCENDING,
    ANIMATION_BLINKING,
    ANIMATION_BREATHING,
    ANIMATION_DESCENDING,
    ANIMATION_NONE,
    DEFAULT_BRIGHTNESS,
    DOMAIN,
    UPDATE_INTERVAL,
)
from .volcano import VolcanoConnectionError, VolcanoHybrid, VolcanoState

_LOGGER = logging.getLogger(__name__)

type VolcanoConfigEntry = ConfigEntry[VolcanoDataUpdateCoordinator]

ANIMATION_STEP = 8
ANIMATION_FRAME_DELAY = 0.1
BLINK_FRAME_DELAY = 0.5


class VolcanoDataUpdateCoordinator(DataUpdateCoordinator[VolcanoState]):
    """Coordinate polling and commands for one Volcano Hybrid."""

    config_entry: VolcanoConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: VolcanoConfigEntry,
        device: VolcanoHybrid,
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=entry.title,
            update_interval=UPDATE_INTERVAL,
        )
        self.device = device
        self.address = device.address
        self._fan_timer_task: asyncio.Task[None] | None = None
        self._animation_task: asyncio.Task[None] | None = None
        entry.async_on_unload(device.register_callback(self._handle_push_update))

    @callback
    def _handle_push_update(self, state: VolcanoState) -> None:
        """Handle a state update pushed from the device."""
        self.async_set_updated_data(state)

    @callback
    def async_set_ble_device(
        self, service_info: BluetoothServiceInfoBleak, change: BluetoothChange
    ) -> None:
        """Adopt a fresh BLEDevice so reconnects use the nearest adapter/proxy.

        The Bluetooth manager always calls its callbacks with two positional
        arguments, including when it replays advertisement history at
        registration time. Omitting ``change`` silently breaks the callback.
        """
        self.device.set_ble_device(service_info.device)

    async def _async_update_data(self) -> VolcanoState:
        """Fetch the current device state."""
        if (
            ble_device := async_ble_device_from_address(
                self.hass, self.address, connectable=True
            )
        ) is not None:
            self.device.set_ble_device(ble_device)

        try:
            return await self.device.async_update()
        except VolcanoConnectionError as err:
            raise UpdateFailed(str(err)) from err

    async def async_shutdown_device(self) -> None:
        """Stop background work and drop the BLE link."""
        self.async_cancel_fan_timer()
        await self.async_stop_animation()
        await self.device.async_disconnect()

    # -- commands ----------------------------------------------------------

    async def _async_command(self, coro: Coroutine[Any, Any, None]) -> None:
        """Run a device command, then refresh."""
        try:
            await coro
        except VolcanoConnectionError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        # Publish the optimistic result straight away. The refresh below is
        # debounced, so without this the UI snaps back to the previous state
        # for up to a full poll interval after every button press.
        self.async_set_updated_data(self.device.state)
        await self.async_request_refresh()

    async def async_turn_heater_on(self) -> None:
        """Turn the heater on."""
        await self._async_command(self.device.async_turn_heater_on())

    async def async_turn_heater_off(self) -> None:
        """Turn the heater off."""
        await self._async_command(self.device.async_turn_heater_off())

    async def async_turn_fan_on(self) -> None:
        """Turn the fan on."""
        await self._async_command(self.device.async_turn_fan_on())

    async def async_turn_fan_off(self) -> None:
        """Turn the fan off."""
        await self._async_command(self.device.async_turn_fan_off())

    async def async_set_temperature(self, celsius: float) -> None:
        """Set the target temperature."""
        await self._async_command(self.device.async_set_target_temperature(celsius))

    async def async_set_brightness(self, percent: int) -> None:
        """Set the screen brightness."""
        await self._async_command(self.device.async_set_brightness(percent))

    async def async_set_auto_off_minutes(self, minutes: int) -> None:
        """Set the auto-off delay."""
        await self._async_command(self.device.async_set_auto_off_minutes(minutes))

    async def async_set_register3(self, enabled: bool) -> None:
        """Set register 3."""
        await self._async_command(self.device.async_set_register3(enabled))

    async def async_set_register2(self, enabled: bool) -> None:
        """Set register 2."""
        await self._async_command(self.device.async_set_register2(enabled))

    # -- fan timer ---------------------------------------------------------

    async def async_fan_timer(
        self,
        duration: float,
        turn_off_heat: bool = False,
        turn_off_screen: bool = False,
    ) -> None:
        """Start the fan and stop it again after ``duration`` seconds."""
        self.async_cancel_fan_timer()
        await self.async_turn_fan_on()
        self._fan_timer_task = self.config_entry.async_create_background_task(
            self.hass,
            self._fan_timer(duration, turn_off_heat, turn_off_screen),
            f"{DOMAIN} fan timer {self.address}",
        )

    @callback
    def async_cancel_fan_timer(self) -> None:
        """Cancel a running fan timer."""
        if self._fan_timer_task is not None and not self._fan_timer_task.done():
            self._fan_timer_task.cancel()
        self._fan_timer_task = None

    async def _fan_timer(
        self, duration: float, turn_off_heat: bool, turn_off_screen: bool
    ) -> None:
        """Wait out the fan timer, then shut things down."""
        await asyncio.sleep(duration)
        try:
            await self.device.async_turn_fan_off()
            if turn_off_heat:
                await self.device.async_turn_heater_off()
            if turn_off_screen:
                await self.device.async_set_brightness(0)
        except VolcanoConnectionError as err:
            _LOGGER.warning("Fan timer could not reach the device: %s", err)
        await self.async_request_refresh()

    # -- screen animation --------------------------------------------------

    async def async_start_animation(self, animation_type: str) -> None:
        """Start a screen animation, replacing any running one."""
        await self.async_stop_animation()
        if animation_type == ANIMATION_NONE:
            return
        self._animation_task = self.config_entry.async_create_background_task(
            self.hass,
            self._animate(animation_type),
            f"{DOMAIN} animation {self.address}",
        )

    async def async_stop_animation(self) -> None:
        """Stop the running animation and restore the default brightness."""
        task = self._animation_task
        self._animation_task = None
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        # Restore brightness from here rather than the animation task: awaiting
        # anything inside a cancelled task raises CancelledError immediately.
        with contextlib.suppress(VolcanoConnectionError):
            await self.device.async_set_brightness(DEFAULT_BRIGHTNESS)
        await self.async_request_refresh()

    async def _animate(self, animation_type: str) -> None:
        """Drive the screen brightness in a loop until cancelled."""
        brightness = 0
        ascending = True
        delay = (
            BLINK_FRAME_DELAY
            if animation_type == ANIMATION_BLINKING
            else ANIMATION_FRAME_DELAY
        )
        try:
            while True:
                if animation_type == ANIMATION_BLINKING:
                    brightness = 0 if brightness else 100
                elif animation_type == ANIMATION_BREATHING:
                    brightness += ANIMATION_STEP if ascending else -ANIMATION_STEP
                    if brightness >= 100 or brightness <= 0:
                        ascending = not ascending
                    brightness = min(100, max(0, brightness))
                elif animation_type == ANIMATION_ASCENDING:
                    brightness = 0 if brightness >= 100 else brightness + ANIMATION_STEP
                elif animation_type == ANIMATION_DESCENDING:
                    brightness = 100 if brightness <= 0 else brightness - ANIMATION_STEP
                else:
                    return

                await self.device.async_set_brightness(brightness)
                await asyncio.sleep(delay)
        except VolcanoConnectionError as err:
            _LOGGER.warning("Screen animation stopped: %s", err)
