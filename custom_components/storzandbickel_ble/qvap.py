"""The Venty and Veazy ("Qvap") protocol. Experimental: never run on hardware here.

Re-implemented from the protocol description in SavageNL's
``VENTY_BLE_SPEC.md``, itself read from Storz & Bickel's web app. Unlike the
Volcano and Crafty there is one characteristic: the client writes a framed
command and the device notifies a frame back with the same command id, and the
device does not push on its own, so it is polled.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
import logging
import struct
import time
from typing import ClassVar

from .ble_device import (
    ADAPTER_CACHE_RESET_AFTER,
    DEVICE_INFO_INTERVAL,
    ConnectionFailure,
    DeviceConnectionError,
    DeviceState,
    StorzBickelDevice,
    _bit,
)

_LOGGER = logging.getLogger(__name__)

QVAP_SERVICE = "00000000-5354-4f52-5a26-4249434b454c"
CHAR_CONTROL = "00000001-5354-4f52-5a26-4249434b454c"

CMD_STATUS = 0x01
CMD_FIRMWARE = 0x02
CMD_USAGE = 0x04
CMD_SERIAL = 0x05
CMD_DISPLAY = 0x06
CMD_FIND = 0x0D

FRAME_LENGTH = 20
DISPLAY_FRAME_LENGTH = 7

# Status write mask: which fields of the frame the device should take.
WRITE_TARGET = 0x02
WRITE_BOOST = 0x04
WRITE_SUPERBOOST = 0x08
WRITE_HEATER = 0x20
WRITE_SETTINGS = 0x80

# Settings (status byte 14) and settings2 (byte 16).
SETTING_FAHRENHEIT = 0x01
SETTING_TARGET_REACHED = 0x02
SETTING_CHARGE_OPTIMISATION = 0x08
SETTING_TARGET_CHANGED = 0x10
SETTING_CHARGE_LIMIT = 0x20
SETTING_BOOST_VISUALISATION = 0x40  # inverted on the Veazy
SETTING2_PERMANENT_BLUETOOTH = 0x01

# Display frame (command 0x06) write mask.
DISPLAY_BRIGHTNESS = 0x01
DISPLAY_VIBRATION = 0x08
DISPLAY_BOOST_TIMEOUT = 0x10

HEATER_MODES = ("off", "heating", "boost", "superboost")
VEAZY_COLOURS = {2: "blue", 3: "pink", 4: "orange"}

MAX_TEMP = 210
# A reply not arriving in this long is a dead link, the same as a Volcano read
# that answers nothing.
REPLY_TIMEOUT = 3.0


@dataclass
class QvapState(DeviceState):
    """Snapshot of a Venty or Veazy's state."""

    boost_offset: int | None = None
    superboost_offset: int | None = None
    battery: int | None = None
    auto_off_countdown: int | None = None
    heater_mode: str | None = None
    charging: bool | None = None
    settings: int | None = None
    settings2: int | None = None
    brightness: int | None = None
    vibration: bool | None = None
    boost_timeout_disabled: bool | None = None
    bootloader: bool | None = None
    heater_minutes: int | None = None
    charge_minutes: int | None = None
    colour: str | None = None
    # The Veazy inverts the boost-visualisation bit.
    veazy: bool = False

    @property
    def target_reached(self) -> bool | None:
        """Return the device's "setpoint reached" bit."""
        return _bit(self.settings, SETTING_TARGET_REACHED)

    @property
    def display_fahrenheit(self) -> bool | None:
        """Return whether the display shows Fahrenheit."""
        return _bit(self.settings, SETTING_FAHRENHEIT)

    @property
    def charge_optimisation(self) -> bool | None:
        """Return whether charging is slowed to spare the battery."""
        return _bit(self.settings, SETTING_CHARGE_OPTIMISATION)

    @property
    def charge_limit(self) -> bool | None:
        """Return whether charging stops short of full."""
        return _bit(self.settings, SETTING_CHARGE_LIMIT)

    @property
    def target_changed_on_device(self) -> bool | None:
        """Return whether the buttons moved the target since the last write."""
        return _bit(self.settings, SETTING_TARGET_CHANGED)

    @property
    def boost_visualisation(self) -> bool | None:
        """Return whether boost is shown on the display."""
        bit = _bit(self.settings, SETTING_BOOST_VISUALISATION)
        return None if bit is None else bit != self.veazy

    @property
    def permanent_bluetooth(self) -> bool | None:
        """Return whether Bluetooth stays on while the device sleeps."""
        return _bit(self.settings2, SETTING2_PERMANENT_BLUETOOTH)


def _frame(command: int, length: int = FRAME_LENGTH) -> bytearray:
    """Return an empty request frame for ``command``."""
    frame = bytearray(length)
    frame[0] = command
    return frame


def _text(data: bytes) -> str | None:
    """Decode a fixed-width text field."""
    text = data.decode("utf-8", errors="replace").replace("\x00", "").strip()
    return text or None


class QvapDevice(StorzBickelDevice):
    """Bluetooth LE client for the Venty and Veazy."""

    required_characteristics: ClassVar[frozenset[str]] = frozenset({CHAR_CONTROL})
    notify_characteristics: ClassVar[tuple[str, ...]] = (CHAR_CONTROL,)
    active_interval: ClassVar[timedelta] = timedelta(seconds=5)
    idle_interval: ClassVar[timedelta] = timedelta(seconds=30)
    sleeps: ClassVar[bool] = True
    max_temp: ClassVar[int] = MAX_TEMP
    veazy: ClassVar[bool] = False

    def __init__(self, address: str) -> None:
        """Initialise the client for ``address``."""
        super().__init__(address, QvapState(veazy=self.veazy))
        self._state: QvapState
        self._replies: dict[int, asyncio.Future[bytes]] = {}
        self._usage_read_at: float | None = None
        self._missed_replies = 0

    @property
    def state(self) -> QvapState:
        """Return the last known state."""
        return self._state

    # -- frames ------------------------------------------------------------

    def _handle_notification(self, uuid: str, data: bytes) -> None:
        """Hand a reply to whoever is waiting for it, and apply a status."""
        if not data:
            return
        if (waiter := self._replies.pop(data[0], None)) and not waiter.done():
            waiter.set_result(data)
        if data[0] == CMD_STATUS and self._apply_status(data):
            self._notify_listeners()

    async def _command(self, frame: bytes | bytearray) -> bytes:
        """Send a frame and return the device's reply. Assumes the lock is held."""
        command = frame[0]
        waiter: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()
        self._replies[command] = waiter
        try:
            await self._write(CHAR_CONTROL, bytes(frame))
            async with asyncio.timeout(REPLY_TIMEOUT):
                reply = await waiter
        except TimeoutError as err:
            self._missed_replies += 1
            await self._async_reset_link(
                reset_adapter_cache=self._missed_replies >= ADAPTER_CACHE_RESET_AFTER
            )
            raise DeviceConnectionError(
                f"{self.address} did not answer command 0x{command:02x}",
                ConnectionFailure.NO_READS,
            ) from err
        finally:
            self._replies.pop(command, None)
        self._missed_replies = 0
        return reply

    def _apply_status(self, data: bytes) -> bool:
        """Apply a status frame; return whether anything changed."""
        if len(data) < 15:
            return False
        state = self._state
        before = (state.heater_on, state.settings, state.heater_mode)
        state.current_temperature = int.from_bytes(data[2:4], "little") / 10
        state.target_temperature = int.from_bytes(data[4:6], "little") / 10
        state.boost_offset = data[6]
        state.superboost_offset = data[7]
        state.battery = data[8]
        state.auto_off_countdown = int.from_bytes(data[9:11], "little")
        mode = data[11]
        state.heater_mode = HEATER_MODES[mode] if mode < len(HEATER_MODES) else None
        state.heater_on = mode != 0
        state.charging = data[13] > 0
        state.settings = data[14]
        if len(data) >= 17:
            state.settings2 = data[16]
        state.raw["status"] = data.hex()
        return before != (state.heater_on, state.settings, state.heater_mode)

    def _apply_firmware(self, data: bytes) -> None:
        """Apply a firmware-info reply."""
        state = self._state
        if len(data) >= 8:
            state.bootloader = not data[1] & 0x01
            state.firmware_version = _text(data[2:8]) or state.firmware_version
        state.raw["firmware"] = data.hex()

    def _apply_serial(self, data: bytes) -> None:
        """Apply a serial-and-colour reply."""
        state = self._state
        if len(data) >= 17:
            state.serial_number = _text(data[15:17] + data[9:15])
        if self.veazy and len(data) >= 19:
            state.colour = VEAZY_COLOURS.get(data[18], "black")

    def _apply_display(self, data: bytes) -> None:
        """Apply a brightness / vibration / boost-timeout reply."""
        if len(data) >= DISPLAY_FRAME_LENGTH:
            self._state.brightness = data[2]
            self._state.vibration = bool(data[5])
            self._state.boost_timeout_disabled = bool(data[6])

    def _apply_usage(self, data: bytes) -> None:
        """Apply a usage-counter reply."""
        if len(data) >= 7:
            self._state.heater_minutes = int.from_bytes(data[1:4], "little")
            self._state.charge_minutes = int.from_bytes(data[4:7], "little")

    # -- polling -----------------------------------------------------------

    async def async_update(self) -> QvapState:
        """Refresh and return the device state."""
        async with self._lock:
            await self._ensure_client()
            if not self._static_info_read:
                self._apply_firmware(await self._command(_frame(CMD_FIRMWARE)))
                self._apply_serial(await self._command(_frame(CMD_SERIAL)))
                self._apply_display(
                    await self._command(_frame(CMD_DISPLAY, DISPLAY_FRAME_LENGTH))
                )
                self._state.model = "Veazy" if self.veazy else "Venty"
                self._static_info_read = True
            self._apply_status(await self._command(_frame(CMD_STATUS)))
            if (
                self._usage_read_at is None
                or time.monotonic() - self._usage_read_at > DEVICE_INFO_INTERVAL
            ):
                self._apply_usage(await self._command(_frame(CMD_USAGE)))
                self._usage_read_at = time.monotonic()
            self._state.connected = True
            return self._state

    # -- commands ----------------------------------------------------------

    async def _async_status_write(self, frame: bytearray) -> None:
        """Send a status write and apply the full status the device answers."""
        if self._state.bootloader:
            raise DeviceConnectionError(
                "The device is in its bootloader and cannot be controlled",
                ConnectionFailure.WRITE_FAILED,
            )
        async with self._lock:
            self._apply_status(await self._command(frame))

    async def async_turn_heater_on(self) -> None:
        """Turn the heater on."""
        frame = _frame(CMD_STATUS)
        frame[1], frame[11] = WRITE_HEATER, 1
        await self._async_status_write(frame)

    async def async_turn_heater_off(self) -> None:
        """Turn the heater off."""
        frame = _frame(CMD_STATUS)
        frame[1], frame[11] = WRITE_HEATER, 0
        await self._async_status_write(frame)

    async def async_set_target_temperature(self, celsius: float) -> None:
        """Set the target temperature."""
        frame = _frame(CMD_STATUS)
        frame[1] = WRITE_TARGET
        frame[4:6] = struct.pack("<H", int(max(40, min(MAX_TEMP, celsius))) * 10)
        await self._async_status_write(frame)

    async def async_set_boost(self, offset: float) -> None:
        """Set the boost offset."""
        frame = _frame(CMD_STATUS)
        frame[1], frame[6] = WRITE_BOOST, int(max(1, min(99, offset)))
        await self._async_status_write(frame)

    async def async_set_superboost(self, offset: float) -> None:
        """Set the superboost offset."""
        frame = _frame(CMD_STATUS)
        frame[1], frame[7] = WRITE_SUPERBOOST, int(max(1, min(99, offset)))
        await self._async_status_write(frame)

    async def _async_setting(
        self, mask: int, on: bool, *, second: bool = False
    ) -> None:
        """Set or clear one settings bit; the device takes only that bit."""
        frame = _frame(CMD_STATUS)
        frame[1] = WRITE_SETTINGS
        value, which = (16, 17) if second else (14, 15)
        frame[value], frame[which] = (mask if on else 0), mask
        await self._async_status_write(frame)

    async def async_set_display_fahrenheit(self, enabled: bool) -> None:
        """Show Fahrenheit or Celsius on the display."""
        await self._async_setting(SETTING_FAHRENHEIT, enabled)

    async def async_set_charge_optimisation(self, enabled: bool) -> None:
        """Slow charging to spare the battery, or not."""
        await self._async_setting(SETTING_CHARGE_OPTIMISATION, enabled)

    async def async_set_charge_limit(self, enabled: bool) -> None:
        """Stop charging short of full, or not."""
        await self._async_setting(SETTING_CHARGE_LIMIT, enabled)

    async def async_set_boost_visualisation(self, enabled: bool) -> None:
        """Show boost on the display, or not. The Veazy's bit is inverted."""
        await self._async_setting(SETTING_BOOST_VISUALISATION, enabled != self.veazy)

    async def async_set_permanent_bluetooth(self, enabled: bool) -> None:
        """Keep Bluetooth on while asleep (Veazy only)."""
        await self._async_setting(SETTING2_PERMANENT_BLUETOOTH, enabled, second=True)

    async def _async_display(self, mask: int, index: int, value: int) -> None:
        """Write one field of the brightness / vibration / timeout frame."""
        frame = _frame(CMD_DISPLAY, DISPLAY_FRAME_LENGTH)
        frame[1], frame[index] = mask, value
        async with self._lock:
            self._apply_display(await self._command(frame))

    async def async_set_brightness(self, level: int) -> None:
        """Set the display brightness, 1-9."""
        await self._async_display(DISPLAY_BRIGHTNESS, 2, max(1, min(9, int(level))))

    async def async_set_vibration(self, enabled: bool) -> None:
        """Turn vibration on or off."""
        await self._async_display(DISPLAY_VIBRATION, 5, int(enabled))

    async def async_set_boost_timeout_disabled(self, disabled: bool) -> None:
        """Let boost run until switched off, or time out as usual."""
        await self._async_display(DISPLAY_BOOST_TIMEOUT, 6, int(disabled))

    async def async_find(self) -> None:
        """Make the device signal so it can be found."""
        frame = _frame(CMD_FIND)
        frame[1] = 0x01
        async with self._lock:
            await self._command(frame)


class VentyDevice(QvapDevice):
    """A Venty."""


class VeazyDevice(QvapDevice):
    """A Veazy: the Venty protocol with an inverted boost-display bit."""

    veazy: ClassVar[bool] = True
