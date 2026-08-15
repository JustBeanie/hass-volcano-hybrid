"""DataUpdateCoordinator for the Volcano Hybrid integration."""

from __future__ import annotations

from collections.abc import Coroutine
from datetime import timedelta
import logging
from typing import Any

from homeassistant.components.bluetooth import (
    BluetoothChange,
    BluetoothServiceInfoBleak,
    async_address_present,
    async_ble_device_from_address,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    ACTIVE_INTERVAL,
    DOMAIN,
    IDLE_INTERVAL,
    ISSUE_CONNECTION_REFUSED,
    ISSUE_CONNECTION_UNRESPONSIVE,
)
from .volcano import (
    ConnectionFailure,
    VolcanoConnectionError,
    VolcanoHybrid,
    VolcanoState,
)

_LOGGER = logging.getLogger(__name__)

type VolcanoConfigEntry = ConfigEntry[VolcanoDataUpdateCoordinator]

# The vaporizer accepts one BLE connection at a time. This many consecutive
# refusals while it is still advertising means something else holds the link.
CONTENTION_THRESHOLD = 3

# One past ADAPTER_CACHE_RESET_AFTER, deliberately. Every one of these has
# already had the link torn down and rebuilt underneath it, and the last one
# also cleared the adapter's cached service table -- so this is the first
# failure that has had every automatic remedy tried on it and still come back
# dead. Raising it any earlier would ask the user to power-cycle a vaporizer
# that the next poll was about to fix on its own.
UNRESPONSIVE_THRESHOLD = 4


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
            update_interval=ACTIVE_INTERVAL,
        )
        self.device = device
        self.address = device.address
        self._was_available = True
        self.consecutive_failures = 0
        # Counted separately from the total: the two repair issues describe
        # different failures, and a poll that failed for one reason must never
        # push the other one over its threshold.
        self.consecutive_refusals = 0
        self.consecutive_no_reads = 0
        self._reconnect_pending = False
        entry.async_on_unload(device.register_callback(self._handle_push_update))

    @callback
    def _handle_push_update(self, state: VolcanoState) -> None:
        """Handle a state update pushed from the device."""
        if not state.connected:
            # A dropped link is not a successful poll. async_set_updated_data
            # would record it as one -- it sets last_update_success and resets
            # the refresh timer -- which left the failure counter at zero and
            # the diagnostic sensors reporting available through an outage.
            # The coordinator's data is the same VolcanoState object the device
            # mutates, so listeners still see the new availability.
            self.async_update_listeners()
            return
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

        # Hearing the vaporiser again is the earliest possible signal that it is
        # back, so this reconnects rather than waiting for the next scheduled
        # poll. It deliberately does not go through async_request_refresh: that
        # debouncer has a ten second cooldown which any recent command has
        # already started, so a reconnect could sit behind it for the remainder
        # -- measured at 10-15s on real hardware, against under a second when
        # the cooldown happened to have expired.
        if not self.device.connected and not self._reconnect_pending:
            self._reconnect_pending = True
            self.config_entry.async_create_task(
                self.hass, self._async_reconnect(), eager_start=True
            )

    async def _async_reconnect(self) -> None:
        """Reconnect on an advertisement, then let the next one try again.

        Advertisements arrive continuously, so the guard flag keeps this to one
        attempt at a time. Clearing it on failure as well as success is
        deliberate: an advertisement means the device really is there, so the
        next one is worth acting on. A device that is switched off sends none,
        which is what stops this from becoming a retry loop.
        """
        try:
            await self.async_refresh()
        finally:
            self._reconnect_pending = False

    async def _async_update_data(self) -> VolcanoState:
        """Fetch the current device state."""
        # Assigned unconditionally: None means nothing can hear the device, and
        # recording that lets the connect attempt fail immediately instead of
        # working through a retry ladder against a BLEDevice that has gone.
        self.device.set_ble_device(
            async_ble_device_from_address(self.hass, self.address, connectable=True)
        )

        try:
            state = await self.device.async_update()
        except VolcanoConnectionError as err:
            self._handle_update_failure(err)
            raise UpdateFailed(str(err)) from err

        self._handle_update_success()
        self._async_set_interval(
            ACTIVE_INTERVAL if state.heater_on or state.fan_on else IDLE_INTERVAL
        )
        return state

    @callback
    def _async_set_interval(self, interval: timedelta) -> None:
        """Switch polling cadence, leaving the schedule alone if unchanged."""
        if self.update_interval != interval:
            self.update_interval = interval

    @callback
    def _handle_update_failure(self, err: VolcanoConnectionError) -> None:
        """Log the outage once and raise the repair that fits the failure."""
        self.consecutive_failures += 1

        if self._was_available:
            self._was_available = False
            _LOGGER.warning(
                "Lost connection to the Volcano Hybrid at %s: %s", self.address, err
            )

        if err.kind is ConnectionFailure.CONNECT_FAILED:
            self.consecutive_refusals += 1
            # Only a device that is advertising but refusing to connect points
            # at something else holding the link. One that is simply switched
            # off or out of range is not something the user can fix by closing
            # an app -- and neither is one that let us in and then went quiet,
            # which used to land here and send people looking for a phone app
            # that was never open.
            if self.consecutive_refusals == CONTENTION_THRESHOLD and (
                async_address_present(self.hass, self.address, connectable=True)
            ):
                self._async_raise_issue(ISSUE_CONNECTION_REFUSED)
        elif err.kind is ConnectionFailure.NO_READS:
            self.consecutive_no_reads += 1
            if self.consecutive_no_reads == UNRESPONSIVE_THRESHOLD:
                self._async_raise_issue(ISSUE_CONNECTION_UNRESPONSIVE)

    @callback
    def _async_raise_issue(self, issue: str) -> None:
        """Raise one of this integration's repair issues."""
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"{issue}_{self.config_entry.entry_id}",
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=issue,
            translation_placeholders={"name": self.config_entry.title},
            data={"entry_id": self.config_entry.entry_id},
        )

    @callback
    def _handle_update_success(self) -> None:
        """Log recovery once and clear both connection issues."""
        self.consecutive_failures = 0
        self.consecutive_refusals = 0
        self.consecutive_no_reads = 0
        if not self._was_available:
            self._was_available = True
            _LOGGER.info("Reconnected to the Volcano Hybrid at %s", self.address)
        for issue in (ISSUE_CONNECTION_REFUSED, ISSUE_CONNECTION_UNRESPONSIVE):
            ir.async_delete_issue(
                self.hass, DOMAIN, f"{issue}_{self.config_entry.entry_id}"
            )

    async def async_shutdown_device(self) -> None:
        """Drop the BLE link."""
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
        # Any command means someone is using the device, so drop back to the
        # fast cadence before publishing -- otherwise turning the heater on from
        # an idle state would leave the climate card a minute behind the ramp.
        self._async_set_interval(ACTIVE_INTERVAL)
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
