"""The Crafty and Crafty+ protocol. Experimental: never run on hardware here.

Re-implemented from the protocol description in SavageNL's
``CRAFTY_BLE_SPEC.md``, itself read from Storz & Bickel's web app. The Crafty is
the Volcano's closest relative -- one characteristic per value, uint16
little-endian, temperatures in tenths -- on a different UUID base, and it
sleeps: Bluetooth goes off when it is idle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
import struct
from typing import ClassVar

from .ble_device import (
    ConnectionFailure,
    DeviceConnectionError,
    DeviceState,
    StorzBickelDevice,
    _bit,
    _decode_firmware,
    _decode_int,
    _decode_string,
)
from .firmware import parse_firmware_version

_LOGGER = logging.getLogger(__name__)


def _uuid(short: int) -> str:
    """Expand a Crafty characteristic id onto its base ("STORZ&BICKEL" reversed)."""
    return f"{short:08x}-4c45-4b43-4942-265a524f5453"


CRAFTY_SERVICE = _uuid(0x01)

CHAR_CURRENT_TEMP = _uuid(0x11)
CHAR_TARGET_TEMP = _uuid(0x21)
CHAR_BOOST = _uuid(0x31)
CHAR_BATTERY = _uuid(0x41)
CHAR_LED_BRIGHTNESS = _uuid(0x51)
CHAR_AUTO_OFF_COUNTDOWN = _uuid(0x71)
CHAR_HEAT_ON = _uuid(0x81)
CHAR_HEAT_OFF = _uuid(0x91)
CHAR_FIRMWARE = _uuid(0x32)
CHAR_SERIAL_NUMBER = _uuid(0x52)
CHAR_HOURS = _uuid(0x23)
CHAR_MINUTES = _uuid(0x1E3)
CHAR_STATUS1 = _uuid(0x93)
CHAR_STATUS2 = _uuid(0x1C3)

# Status register 1.
MASK_HEATER = 0x0010
MASK_BOOST = 0x0020
MASK_SUPERBOOST = 0x0040
MASK_ERROR = 0x2008
MASK_NEEDS_FACTORY_RESET = 0x8000
# Status register 2, written back as a whole word.
MASK_VIBRATION_OFF = 0x0001  # inverted
MASK_CHARGE_LED_OFF = 0x0002  # inverted
MASK_TARGET_REACHED = 0x0004
MASK_FIND = 0x0008  # buzzes for 30 s, then clears itself
MASK_AUTO_BLE_SHUTDOWN = 0x1000

MAX_TEMP = 210
# Below this firmware the heater cannot be switched over Bluetooth at all, and
# the settings are not exposed: the device can only be watched.
CONTROL_FIRMWARE = (2, 51)
# Major 3 and up is the Crafty+, which adds "find my Crafty".
PLUS_FIRMWARE_MAJOR = 3

# A Crafty set to show Fahrenheit reports its target in tenths of a degree
# Fahrenheit, on the same characteristic. Anything above the Celsius range is
# read as that.
FAHRENHEIT_ABOVE = MAX_TEMP * 10


@dataclass
class CraftyState(DeviceState):
    """Snapshot of a Crafty's state."""

    boost_offset: float | None = None
    battery: int | None = None
    led_brightness: int | None = None
    auto_off_countdown: int | None = None
    hours_of_operation: int | None = None
    minutes_of_operation: int | None = None
    status1: int | None = None
    status2: int | None = None

    @property
    def target_reached(self) -> bool | None:
        """Return the device's "setpoint reached" bit (register 2, bit 2)."""
        return _bit(self.status2, MASK_TARGET_REACHED)

    @property
    def fault_known(self) -> bool:
        """Return whether status register 1 has been read."""
        return self.status1 is not None

    @property
    def any_fault(self) -> bool:
        """Return whether the device asks for support."""
        return bool((self.status1 or 0) & MASK_ERROR)

    @property
    def boost(self) -> bool | None:
        """Return whether boost is active."""
        return _bit(self.status1, MASK_BOOST)

    @property
    def superboost(self) -> bool | None:
        """Return whether superboost is active."""
        return _bit(self.status1, MASK_SUPERBOOST)

    @property
    def needs_factory_reset(self) -> bool | None:
        """Return whether the device asks for a factory reset."""
        return _bit(self.status1, MASK_NEEDS_FACTORY_RESET)

    @property
    def vibration(self) -> bool | None:
        """Return whether vibration is on (inverted bit)."""
        off = _bit(self.status2, MASK_VIBRATION_OFF)
        return None if off is None else not off

    @property
    def charge_led(self) -> bool | None:
        """Return whether the charge LED is on (inverted bit)."""
        off = _bit(self.status2, MASK_CHARGE_LED_OFF)
        return None if off is None else not off

    @property
    def auto_ble_shutdown(self) -> bool | None:
        """Return whether the device switches Bluetooth off when idle."""
        return _bit(self.status2, MASK_AUTO_BLE_SHUTDOWN)

    @property
    def controllable(self) -> bool:
        """Return whether this firmware accepts heater and settings writes."""
        version = parse_firmware_version(self.firmware_version)
        return version is not None and version >= CONTROL_FIRMWARE

    @property
    def is_plus(self) -> bool:
        """Return whether this is a Crafty+, judged by its firmware."""
        version = parse_firmware_version(self.firmware_version)
        return version is not None and version[0] >= PLUS_FIRMWARE_MAJOR


def _decode_target(raw: bytes | None) -> float | None:
    """Decode the target, undoing the Fahrenheit quirk."""
    value = _decode_int(raw)
    if value is None:
        return None
    if value > FAHRENHEIT_ABOVE:
        return round((value / 10 - 32) / 1.8, 1)
    return value / 10


def _decode_tenths(raw: bytes | None) -> float | None:
    """Decode a uint16 in tenths of a degree."""
    value = _decode_int(raw)
    return None if value is None else value / 10


class CraftyDevice(StorzBickelDevice):
    """Bluetooth LE client for a Crafty or Crafty+."""

    required_characteristics: ClassVar[frozenset[str]] = frozenset(
        {CHAR_CURRENT_TEMP, CHAR_TARGET_TEMP, CHAR_STATUS1}
    )
    # Optional ones last: a subscribe failing stops the rest, and the
    # countdown only exists on newer firmware.
    notify_characteristics: ClassVar[tuple[str, ...]] = (
        CHAR_CURRENT_TEMP,
        CHAR_BATTERY,
        CHAR_STATUS1,
        CHAR_STATUS2,
        CHAR_AUTO_OFF_COUNTDOWN,
    )
    active_interval: ClassVar[timedelta] = timedelta(seconds=10)
    idle_interval: ClassVar[timedelta] = timedelta(seconds=30)
    sleeps: ClassVar[bool] = True
    max_temp: ClassVar[int] = MAX_TEMP

    def __init__(self, address: str) -> None:
        """Initialise the client for ``address``."""
        super().__init__(address, CraftyState())
        self._state: CraftyState

    @property
    def state(self) -> CraftyState:
        """Return the last known state."""
        return self._state

    # -- reading -----------------------------------------------------------

    def _apply(self, uuid: str, raw: bytes | None) -> None:
        """Store one characteristic's value."""
        state = self._state
        if uuid == CHAR_CURRENT_TEMP:
            state.current_temperature = _decode_tenths(raw)
        elif uuid == CHAR_TARGET_TEMP:
            if (target := _decode_target(raw)) is not None:
                state.target_temperature = target
        elif uuid == CHAR_BOOST:
            state.boost_offset = _decode_tenths(raw)
        elif uuid == CHAR_BATTERY:
            state.battery = _decode_int(raw)
        elif uuid == CHAR_LED_BRIGHTNESS:
            state.led_brightness = _decode_int(raw)
        elif uuid == CHAR_AUTO_OFF_COUNTDOWN:
            state.auto_off_countdown = _decode_int(raw)
        elif uuid == CHAR_STATUS1:
            if (value := _decode_int(raw)) is not None:
                state.status1 = value & 0xFFFF
                state.heater_on = bool(value & MASK_HEATER)
        elif uuid == CHAR_STATUS2 and (value := _decode_int(raw)) is not None:
            state.status2 = value & 0xFFFF
        state.raw[uuid[:8]] = raw.hex() if raw else None

    def _handle_notification(self, uuid: str, data: bytes) -> None:
        """Apply a pushed value and tell the coordinator."""
        self._apply(uuid, data)
        self._notify_listeners()

    async def async_update(self) -> CraftyState:
        """Refresh and return the device state."""
        async with self._lock:
            await self._ensure_client()
            for uuid in (
                CHAR_CURRENT_TEMP,
                CHAR_TARGET_TEMP,
                CHAR_STATUS1,
                CHAR_STATUS2,
                CHAR_BOOST,
                CHAR_BATTERY,
                CHAR_LED_BRIGHTNESS,
                CHAR_AUTO_OFF_COUNTDOWN,
            ):
                self._apply(uuid, await self._read(uuid))

            # The same guard as the Volcano: a link that answers nothing is
            # dead even though bleak has not noticed.
            if self._state.raw.get(CHAR_CURRENT_TEMP[:8]) is None and (
                self._state.raw.get(CHAR_TARGET_TEMP[:8]) is None
            ):
                await self._async_reset_link(reset_adapter_cache=False)
                raise DeviceConnectionError(
                    f"{self.address} accepted the connection but answered no"
                    f" reads ({self._last_read_error})",
                    ConnectionFailure.NO_READS,
                )

            if not self._static_info_read:
                state = self._state
                serial = _decode_string(await self._read(CHAR_SERIAL_NUMBER))
                # The app keeps the first eight characters.
                state.serial_number = serial[:8] if serial else state.serial_number
                state.firmware_version = (
                    _decode_firmware(await self._read(CHAR_FIRMWARE))
                    or state.firmware_version
                )
                state.model = "Crafty+" if state.is_plus else "Crafty"
                state.hours_of_operation = _decode_int(await self._read(CHAR_HOURS))
                state.minutes_of_operation = _decode_int(await self._read(CHAR_MINUTES))
                self._static_info_read = True

            self._state.connected = True
            return self._state

    # -- commands ----------------------------------------------------------

    def _require_control(self) -> None:
        """Refuse a write the firmware does not accept."""
        if not self._state.controllable:
            raise DeviceConnectionError(
                "This Crafty's firmware does not accept commands over Bluetooth",
                ConnectionFailure.WRITE_FAILED,
            )

    async def async_turn_heater_on(self) -> None:
        """Turn the heater on."""
        self._require_control()
        async with self._lock:
            await self._write(CHAR_HEAT_ON, b"\x00\x00")
        self._state.heater_on = True

    async def async_turn_heater_off(self) -> None:
        """Turn the heater off."""
        self._require_control()
        async with self._lock:
            await self._write(CHAR_HEAT_OFF, b"\x00\x00")
        self._state.heater_on = False

    async def async_set_target_temperature(self, celsius: float) -> None:
        """Set the target, then rewrite the boost the way the app does.

        The app always follows a target write with the current boost; the
        firmware most likely re-derives the boost from the target, so leaving
        it out could lose the user's boost setting.
        """
        value = int(max(40, min(MAX_TEMP, celsius)))
        async with self._lock:
            await self._write(CHAR_TARGET_TEMP, struct.pack("<H", value * 10))
            if (boost := self._state.boost_offset) is not None:
                await self._write(CHAR_BOOST, struct.pack("<H", round(boost * 10)))
        self._state.target_temperature = float(value)

    async def async_set_boost(self, offset: float) -> None:
        """Set the boost offset added to the target while boosting."""
        headroom = MAX_TEMP - (self._state.target_temperature or 0)
        value = int(max(1, min(99, offset, headroom)))
        async with self._lock:
            await self._write(CHAR_BOOST, struct.pack("<H", value * 10))
        self._state.boost_offset = float(value)

    async def async_set_led_brightness(self, percent: int) -> None:
        """Set the LED brightness, 0-100."""
        value = max(0, min(100, int(percent)))
        async with self._lock:
            await self._write(CHAR_LED_BRIGHTNESS, struct.pack("<H", value))
        self._state.led_brightness = value

    async def _async_write_status2(self, mask: int, set_: bool) -> None:
        """Read-modify-write status register 2 as a whole word."""
        self._require_control()
        async with self._lock:
            current = _decode_int(await self._read(CHAR_STATUS2))
            if current is None:
                raise DeviceConnectionError(
                    "Could not read the settings register",
                    ConnectionFailure.WRITE_FAILED,
                )
            word = (current | mask) if set_ else (current & ~mask)
            await self._write(CHAR_STATUS2, struct.pack("<H", word & 0xFFFF))
            self._apply(CHAR_STATUS2, await self._read(CHAR_STATUS2))

    async def async_set_vibration(self, enabled: bool) -> None:
        """Turn vibration on or off (inverted bit)."""
        await self._async_write_status2(MASK_VIBRATION_OFF, not enabled)

    async def async_set_charge_led(self, enabled: bool) -> None:
        """Turn the charge LED on or off (inverted bit)."""
        await self._async_write_status2(MASK_CHARGE_LED_OFF, not enabled)

    async def async_set_auto_ble_shutdown(self, enabled: bool) -> None:
        """Let the device switch Bluetooth off when idle, or keep it on."""
        await self._async_write_status2(MASK_AUTO_BLE_SHUTDOWN, enabled)

    async def async_find(self) -> None:
        """Make a Crafty+ buzz for thirty seconds."""
        await self._async_write_status2(MASK_FIND, True)
