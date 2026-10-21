"""DataUpdateCoordinator for the Storz & Bickel BLE integration."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import time
from typing import Any

from homeassistant.components.bluetooth import (
    BluetoothChange,
    BluetoothServiceInfoBleak,
    async_address_present,
    async_ble_device_from_address,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_DEVICE_ID, CONF_TYPE
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .ble_device import (
    ConnectionFailure,
    DeviceConnectionError,
    DeviceState,
    StorzBickelDevice,
)
from .const import (
    CONF_AUTO_CONNECT,
    CONF_AUTO_CONNECT_DELAY,
    CONF_DELAYED_RECONNECT_DELAY,
    DEFAULT_AUTO_CONNECT_DELAY,
    DEFAULT_DELAYED_RECONNECT_DELAY,
    DOMAIN,
    EVENT_DEVICE,
    ISSUE_CONNECTION_REFUSED,
    ISSUE_CONNECTION_UNRESPONSIVE,
    ON_DEMAND_GRACE,
    TRIGGER_CONNECTION_LOST,
    TRIGGER_CONNECTION_RESTORED,
    TRIGGER_FAULT_RAISED,
    TRIGGER_TARGET_REACHED,
)
from .families import Family
from .volcano import VolcanoHybrid

_LOGGER = logging.getLogger(__name__)

type StorzBickelConfigEntry = ConfigEntry[StorzBickelCoordinator]

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

# How far below the target the block may read and still count as reached for
# a wait. The device holds within about a degree and reports in tenths.
REACHED_TOLERANCE = 1.0

# Link-health states beyond the ConnectionFailure kinds.
HEALTH_CONNECTED = "connected"
HEALTH_AUTO_CONNECT_OFF = "auto_connect_off"


@dataclass
class LinkHealth:
    """What has happened to the Bluetooth link since the entry was set up."""

    # The failure the link is in right now; None while it is healthy.
    current_failure: ConnectionFailure | None = None
    last_failure: ConnectionFailure | None = None
    last_failure_at: datetime | None = None
    last_restored_at: datetime | None = None
    last_outage_seconds: float | None = None
    recoveries: int = 0


class StorzBickelCoordinator(DataUpdateCoordinator[Any]):
    """Coordinate polling and commands for one Storz & Bickel device.

    The data is the device family's own state dataclass, which always
    extends ``DeviceState``.
    """

    config_entry: StorzBickelConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: StorzBickelConfigEntry,
        device: StorzBickelDevice,
        family: Family = Family.VOLCANO_HYBRID,
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=entry.title,
            update_interval=device.active_interval,
        )
        self.device = device
        self.family = family
        self.address = device.address
        self._was_available = True
        # Lost/restored triggers only make sense for a link that existed; a
        # vaporizer that was unplugged at startup has lost nothing.
        self._ever_connected = False
        self._outage_started: float | None = None
        self.consecutive_failures = 0
        # Counted separately from the total: the two repair issues describe
        # different failures, and a poll that failed for one reason must never
        # push the other one over its threshold.
        self.consecutive_refusals = 0
        self.consecutive_no_reads = 0
        self._reconnect_pending = False
        self._cancel_reconnect: CALLBACK_TYPE | None = None
        self.link_health = LinkHealth()
        # When a command or button last asked for the link, for auto-connect
        # off: see ON_DEMAND_GRACE.
        self._demanded_at: float | None = None
        # The last value of each edge-triggered signal. None until the first
        # reading, so nothing fires for a state the device was already in.
        self._last_target_reached: bool | None = None
        self._last_any_fault: bool | None = None
        # The options this entry was set up with that need a reload to change;
        # see _async_options_updated in __init__.py.
        self.reload_options: dict[str, Any] = {}
        entry.async_on_unload(device.register_callback(self._handle_push_update))
        entry.async_on_unload(self._async_cancel_reconnect)

    # -- options -----------------------------------------------------------

    @property
    def auto_connect(self) -> bool:
        """Return whether Home Assistant connects on its own."""
        return bool(self.config_entry.options.get(CONF_AUTO_CONNECT, True))

    @property
    def auto_connect_delay(self) -> float:
        """Return how long to wait after an advertisement before connecting."""
        return float(
            self.config_entry.options.get(
                CONF_AUTO_CONNECT_DELAY, DEFAULT_AUTO_CONNECT_DELAY
            )
        )

    @property
    def delayed_reconnect_delay(self) -> float:
        """Return how long the delayed reconnect stays off the link."""
        return float(
            self.config_entry.options.get(
                CONF_DELAYED_RECONNECT_DELAY, DEFAULT_DELAYED_RECONNECT_DELAY
            )
        )

    @property
    def _may_connect(self) -> bool:
        """Return whether a poll may open a link that is not already up."""
        return self.auto_connect or self._within_demand_grace

    @property
    def _within_demand_grace(self) -> bool:
        """Return whether a command asked for the link recently."""
        return (
            self._demanded_at is not None
            and time.monotonic() - self._demanded_at < ON_DEMAND_GRACE.total_seconds()
        )

    @property
    def health_state(self) -> str:
        """Return the link-health sensor's state."""
        if self.last_update_success and self.data.connected:
            return HEALTH_CONNECTED
        if not self.auto_connect and not self.data.connected:
            return HEALTH_AUTO_CONNECT_OFF
        failure = self.link_health.current_failure
        return str(failure) if failure else str(ConnectionFailure.NOT_VISIBLE)

    # -- pushes and advertisements ----------------------------------------

    @callback
    def _handle_push_update(self, _state: DeviceState) -> None:
        """Handle a state update pushed from the device.

        Never routed through async_set_updated_data. That call resets the
        refresh timer, and a push carries only the status register -- the
        temperature comes from polls alone. Each push therefore postponed the
        next temperature read by a full interval; once 3.3.0 published every
        status-bit change, "target reached" flickering around the setpoint
        stretched the 10 s cadence to 15 s and more. It would also have
        recorded a dropped link as a successful update. The coordinator's data
        is the same DeviceState object the device mutates, so notifying the
        listeners is all a push needs.
        """
        self.async_update_listeners()
        # Not the fault trigger: its payload names the newest logged fault,
        # and the log is only re-read by the poll that follows.
        self._async_fire_edges(include_fault=False)

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
        self.device.state.rssi = service_info.rssi

        # Hearing the vaporiser again is the earliest possible signal that it is
        # back, so this reconnects rather than waiting for the next scheduled
        # poll. It deliberately does not go through async_request_refresh: that
        # debouncer has a ten second cooldown which any recent command has
        # already started, so a reconnect could sit behind it for the remainder
        # -- measured at 10-15s on real hardware, against under a second when
        # the cooldown happened to have expired.
        if (
            self.auto_connect
            and not self.device.connected
            and not self._reconnect_pending
        ):
            self._reconnect_pending = True
            self._async_schedule_reconnect(self.auto_connect_delay)

    @callback
    def _async_schedule_reconnect(self, delay: float) -> None:
        """Reconnect after ``delay`` seconds, or straight away for zero.

        A short wait lets every adapter and proxy that heard the advertisement
        report it, so the connection goes through the strongest rather than
        whichever happened to be first.
        """
        if delay <= 0:
            self._async_start_reconnect()
            return

        @callback
        def _fire(_now: datetime) -> None:
            self._cancel_reconnect = None
            self._async_start_reconnect()

        self._async_cancel_reconnect()
        self._cancel_reconnect = async_call_later(self.hass, delay, _fire)

    @callback
    def _async_start_reconnect(self) -> None:
        """Run the reconnect as a task owned by the entry."""
        self.config_entry.async_create_task(
            self.hass, self._async_reconnect(), eager_start=True
        )

    @callback
    def _async_cancel_reconnect(self) -> None:
        """Cancel a scheduled reconnect, if there is one."""
        if self._cancel_reconnect is not None:
            self._cancel_reconnect()
            self._cancel_reconnect = None

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

    # -- polling -----------------------------------------------------------

    async def _async_update_data(self) -> Any:
        """Fetch the current device state."""
        # Assigned unconditionally: None means nothing can hear the device, and
        # recording that lets the connect attempt fail immediately instead of
        # working through a retry ladder against a BLEDevice that has gone.
        self.device.set_ble_device(
            async_ble_device_from_address(self.hass, self.address, connectable=True)
        )

        if not self.auto_connect and self.device.connected:
            if not self._within_demand_grace:
                # The command that opened this link is done with it; hand the
                # vaporizer back so the phone app can connect.
                await self.device.async_disconnect()
                return self.device.state
        elif not self._may_connect:
            # Auto-connect is off and nothing asked for the link: report what
            # is known without opening one. Not a failure, so no repairs.
            return self.device.state

        try:
            state = await self.device.async_update()
        except DeviceConnectionError as err:
            self._handle_update_failure(err)
            raise UpdateFailed(str(err)) from err

        self._handle_update_success()
        self._async_set_interval(
            self.device.active_interval
            if state.heater_on or state.fan_on
            else self.device.idle_interval
        )
        self._async_fire_edges()
        return state

    @callback
    def _async_set_interval(self, interval: timedelta) -> None:
        """Switch polling cadence, leaving the schedule alone if unchanged."""
        if self.update_interval != interval:
            self.update_interval = interval

    @callback
    def _handle_update_failure(self, err: DeviceConnectionError) -> None:
        """Log the outage once and raise the repair that fits the failure."""
        self.consecutive_failures += 1
        health = self.link_health
        health.current_failure = err.kind
        health.last_failure = err.kind
        health.last_failure_at = dt_util.utcnow()

        if self._was_available:
            self._was_available = False
            self._outage_started = time.monotonic()
            _LOGGER.log(
                logging.INFO if self.device.sleeps else logging.WARNING,
                "Lost connection to %s at %s: %s",
                self.config_entry.title,
                self.address,
                err,
            )
            if self._ever_connected:
                self._async_fire(TRIGGER_CONNECTION_LOST, kind=str(err.kind))

        # Asked for by a command or button, not by Home Assistant itself: the
        # user has said they are managing the link, so no repairs. And a
        # portable that has gone to sleep is doing what it is meant to.
        if not self.auto_connect or self.device.sleeps:
            return

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
        elif err.kind in (ConnectionFailure.NO_READS, ConnectionFailure.STALE_GATT):
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
    def _async_clear_issues(self) -> None:
        """Clear both connection issues."""
        for issue in (ISSUE_CONNECTION_REFUSED, ISSUE_CONNECTION_UNRESPONSIVE):
            ir.async_delete_issue(
                self.hass, DOMAIN, f"{issue}_{self.config_entry.entry_id}"
            )

    @callback
    def _handle_update_success(self) -> None:
        """Log recovery once and clear both connection issues."""
        self.consecutive_failures = 0
        self.consecutive_refusals = 0
        self.consecutive_no_reads = 0
        self.link_health.current_failure = None
        if not self._was_available:
            self._was_available = True
            _LOGGER.info(
                "Reconnected to %s at %s", self.config_entry.title, self.address
            )
            if self._ever_connected and self._outage_started is not None:
                outage = round(time.monotonic() - self._outage_started, 1)
                self.link_health.recoveries += 1
                self.link_health.last_restored_at = dt_util.utcnow()
                self.link_health.last_outage_seconds = outage
                self._async_fire(TRIGGER_CONNECTION_RESTORED, outage_seconds=outage)
        self._outage_started = None
        self._ever_connected = True
        self._async_clear_issues()

    # -- device triggers ---------------------------------------------------

    @callback
    def _async_fire_edges(self, *, include_fault: bool = True) -> None:
        """Fire the triggers whose signal has just gone from off to on."""
        state = self.device.state
        reached = state.target_reached
        if reached is not None:
            # Only while heating: the bit can stay set after the heater stops.
            reached = reached and state.heater_on
            if self._last_target_reached is False and reached:
                self._async_fire(
                    TRIGGER_TARGET_REACHED, temperature=state.target_temperature
                )
            self._last_target_reached = reached

        if include_fault and state.fault_known:
            fault = state.any_fault
            if self._last_any_fault is False and fault:
                self._async_fire(TRIGGER_FAULT_RAISED, last_fault=state.last_fault)
            self._last_any_fault = fault

    @callback
    def _async_fire(self, trigger_type: str, **data: Any) -> None:
        """Fire a device trigger event, if the device is registered yet."""
        device = dr.async_get(self.hass).async_get_device(
            identifiers={(DOMAIN, dr.format_mac(self.address))}
        )
        if device is None:
            return
        self.hass.bus.async_fire(
            EVENT_DEVICE,
            {CONF_DEVICE_ID: device.id, CONF_TYPE: trigger_type, **data},
        )

    # -- link control ------------------------------------------------------

    async def async_shutdown_device(self) -> None:
        """Drop the BLE link."""
        self._async_cancel_reconnect()
        await self.device.async_disconnect()

    async def async_reconnect(self) -> None:
        """Drop the link and connect again straight away."""
        self._demanded_at = time.monotonic()
        await self.device.async_disconnect()
        await self.async_refresh()

    async def async_delayed_reconnect(self) -> None:
        """Drop the link, then reconnect once a fresh advertisement is due.

        Staying off long enough for the vaporizer to advertise again lets the
        reconnect go through whichever adapter or proxy now hears it best.
        """
        await self.device.async_disconnect()
        self.async_update_listeners()

        @callback
        def _fire(_now: datetime) -> None:
            self._cancel_reconnect = None
            self._demanded_at = time.monotonic()
            self._async_start_reconnect()

        self._async_cancel_reconnect()
        self._cancel_reconnect = async_call_later(
            self.hass, self.delayed_reconnect_delay, _fire
        )

    async def async_set_auto_connect(self, enabled: bool) -> None:
        """Turn automatic connecting on or off, and remember the choice."""
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            options={**self.config_entry.options, CONF_AUTO_CONNECT: enabled},
        )
        if enabled:
            self.config_entry.async_create_task(
                self.hass, self.async_refresh(), eager_start=True
            )
            return

        # Off means off now: free the link for the phone app, forget any
        # pending reconnect and withdraw repairs nobody should act on.
        self._async_cancel_reconnect()
        self._demanded_at = None
        self._async_clear_issues()
        await self.device.async_disconnect()
        self.async_update_listeners()

    # -- waits for actions -------------------------------------------------

    async def _async_wait_for(
        self,
        settled: Callable[[DeviceState], bool],
        seconds: float,
        timeout_key: str,
        abort: Callable[[DeviceState], str | None] = lambda _state: None,
    ) -> None:
        """Wait until ``settled`` holds on a reading taken after this call.

        Checked on every poll and push from here on, starting with a refresh
        so a value from before the wait can never satisfy it. ``abort`` names
        the error to raise when a reading shows the wait can no longer end
        well; ``timeout_key`` the one for running out of time.
        """
        done: asyncio.Future[None] = self.hass.loop.create_future()

        @callback
        def _check() -> None:
            if done.done():
                return
            state = self.device.state
            if settled(state):
                done.set_result(None)
            elif reason := abort(state):
                done.set_exception(
                    HomeAssistantError(
                        translation_domain=DOMAIN, translation_key=reason
                    )
                )

        remove = self.async_add_listener(_check)
        try:
            async with asyncio.timeout(seconds):
                await self.async_refresh()
                _check()
                await done
        except TimeoutError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key=timeout_key,
                translation_placeholders={"seconds": str(round(seconds))},
            ) from err
        finally:
            remove()

    async def async_wait_until_reached(self, seconds: float) -> None:
        """Wait for the device to report its current target reached.

        Uses the device's own signal, plus the temperature: the bit is not
        cleared by a small rise in the target, so on its own it can still be
        set from the last target. Aborts if the heater goes off, the target is
        changed by someone else, or the link drops -- none of those ends with
        this target reached.
        """
        target = self.device.state.target_temperature

        def _settled(state: DeviceState) -> bool:
            return bool(
                state.heater_on
                and state.target_reached
                and state.current_temperature is not None
                and target is not None
                and state.current_temperature >= target - REACHED_TOLERANCE
            )

        def _abort(state: DeviceState) -> str | None:
            if not state.connected:
                return "wait_disconnected"
            if not state.heater_on:
                return "wait_heater_off"
            if state.target_temperature != target:
                return "wait_target_changed"
            return None

        await self._async_wait_for(_settled, seconds, "wait_timeout", _abort)

    async def async_wait_connected(self, seconds: float) -> None:
        """Wait for a live link."""
        await self._async_wait_for(
            lambda state: state.connected, seconds, "connect_timeout"
        )

    async def async_release(self) -> None:
        """Switch heat and air off, confirm it, then let go of the link.

        Turning auto-connect off is what makes the release stick; otherwise
        the next advertisement would reconnect. Read from the raw register,
        not the state, which is optimistic for a few seconds after a command.
        """
        state = self.device.state
        if state.heater_on:
            await self.async_turn_heater_off()
        if state.fan_on:
            await self.async_turn_fan_off()
        await self.async_refresh()
        if state.heat_or_air_reported_on:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="release_unconfirmed"
            )
        await self.async_set_auto_connect(False)

    # -- commands ----------------------------------------------------------

    @property
    def family_device(self) -> Any:
        """Return the device, for commands only some families have."""
        return self.device

    @property
    def _volcano(self) -> VolcanoHybrid:
        """Return the device, for the commands only a Volcano has."""
        assert isinstance(self.device, VolcanoHybrid)
        return self.device

    async def async_command(self, coro: Coroutine[Any, Any, None]) -> None:
        """Run a device command, then refresh."""
        self._demanded_at = time.monotonic()
        try:
            await coro
        except DeviceConnectionError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        # Any command means someone is using the device, so drop back to the
        # fast cadence before publishing -- otherwise turning the heater on from
        # an idle state would leave the climate card a minute behind the ramp.
        self._async_set_interval(self.device.active_interval)
        # Publish the optimistic result straight away. The refresh below is
        # debounced, so without this the UI snaps back to the previous state
        # for up to a full poll interval after every button press.
        self.async_set_updated_data(self.device.state)
        await self.async_request_refresh()

    async def async_turn_heater_on(self) -> None:
        """Turn the heater on."""
        await self.async_command(self.device.async_turn_heater_on())

    async def async_turn_heater_off(self) -> None:
        """Turn the heater off."""
        await self.async_command(self.device.async_turn_heater_off())

    async def async_turn_fan_on(self) -> None:
        """Turn the fan on."""
        await self.async_command(self.device.async_turn_fan_on())

    async def async_turn_fan_off(self) -> None:
        """Turn the fan off."""
        await self.async_command(self.device.async_turn_fan_off())

    async def async_set_temperature(self, celsius: float) -> None:
        """Set the target temperature."""
        await self.async_command(self.device.async_set_target_temperature(celsius))

    async def async_set_brightness(self, percent: int) -> None:
        """Set the screen brightness."""
        await self.async_command(self.device.async_set_brightness(percent))

    async def async_set_auto_off_minutes(self, minutes: int) -> None:
        """Set the auto-off delay."""
        await self.async_command(self._volcano.async_set_auto_off_minutes(minutes))

    async def async_set_vibration(self, enabled: bool) -> None:
        """Enable or disable vibration."""
        await self.async_command(self.device.async_set_vibration(enabled))

    async def async_set_display_while_cooling(self, enabled: bool) -> None:
        """Keep the display on while cooling, or not."""
        await self.async_command(self._volcano.async_set_display_while_cooling(enabled))

    async def async_set_display_fahrenheit(self, enabled: bool) -> None:
        """Show Fahrenheit or Celsius on the device."""
        await self.async_command(self.device.async_set_display_fahrenheit(enabled))
