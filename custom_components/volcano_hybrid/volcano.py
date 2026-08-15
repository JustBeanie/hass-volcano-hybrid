"""Low-level BLE control for the Storz & Bickel Volcano Hybrid.

All GATT traffic is serialised through a single lock held at exactly one level:
the public coroutines acquire it, the private ``_read``/``_write`` helpers assume
it is already held. Nothing below the lock may re-enter a public coroutine --
``asyncio.Lock`` is not reentrant, and doing so is what deadlocked the previous
implementation.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
import logging
import struct
import time
from typing import Any

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    BleakNotFoundError,
    establish_connection,
)

from .const import (
    MAX_AUTO_OFF_MINUTES,
    MAX_TEMP,
    MIN_AUTO_OFF_MINUTES,
    MIN_TEMP,
)

_LOGGER = logging.getLogger(__name__)

# Characteristic UUIDs ------------------------------------------------------
CHAR_CURRENT_TEMP = "10110001-5354-4f52-5a26-4249434b454c"
CHAR_TARGET_TEMP = "10110003-5354-4f52-5a26-4249434b454c"
CHAR_BRIGHTNESS = "10110005-5354-4f52-5a26-4249434b454c"
CHAR_STATUS_REGISTER = "1010000c-5354-4f52-5a26-4249434b454c"
CHAR_HEAT_ON = "1011000f-5354-4f52-5a26-4249434b454c"
CHAR_HEAT_OFF = "10110010-5354-4f52-5a26-4249434b454c"
CHAR_FAN_ON = "10110013-5354-4f52-5a26-4249434b454c"
CHAR_FAN_OFF = "10110014-5354-4f52-5a26-4249434b454c"
CHAR_AUTO_OFF_REMAINING = "1011000c-5354-4f52-5a26-4249434b454c"
CHAR_AUTO_OFF_SETTING = "1011000d-5354-4f52-5a26-4249434b454c"
CHAR_SERIAL_NUMBER = "10100008-5354-4f52-5a26-4249434b454c"
CHAR_BLE_FIRMWARE = "10100004-5354-4f52-5a26-4249434b454c"
CHAR_FIRMWARE = "10100003-5354-4f52-5a26-4249434b454c"
CHAR_HOURS_OF_OPERATION = "10110015-5354-4f52-5a26-4249434b454c"
CHAR_MINUTES_OF_OPERATION = "10110016-5354-4f52-5a26-4249434b454c"
CHAR_REGISTER2 = "1010000d-5354-4f52-5a26-4249434b454c"
CHAR_REGISTER3 = "1010000e-5354-4f52-5a26-4249434b454c"

# Bit masks in the status register.
MASK_HEATER = 0x0020
MASK_FAN = 0x2000

# The device reports -18.0 C on the temperature characteristic when the probe
# has no valid reading (idle / powered down). Anything below freezing is not a
# real reading from a vaporiser, so treat it as "unknown" rather than surfacing
# a nonsense number.
TEMP_INVALID_BELOW = 0.0

# How long an explicit heater/fan command is trusted over the status register.
# BLE notifications lag the write by a beat; without this the UI snaps back.
OPTIMISTIC_WINDOW = 3.0

# Device information changes rarely; re-read it on this cadence only.
DEVICE_INFO_INTERVAL = 600.0

# The coordinator retries on its own schedule, so a poll that cannot reach the
# device should give up quickly rather than work through a long retry ladder
# while holding the GATT lock -- every command queues behind that lock.
CONNECT_ATTEMPTS = 2


class VolcanoConnectionError(Exception):
    """Raised when the device cannot be reached."""


@dataclass
class VolcanoState:
    """Snapshot of the device state."""

    current_temperature: float | None = None
    target_temperature: float | None = None
    heater_on: bool = False
    fan_on: bool = False
    brightness: int | None = None
    connected: bool = False
    register2: bool | None = None
    register3: bool | None = None
    serial_number: str | None = None
    ble_firmware_version: str | None = None
    firmware_version: str | None = None
    hours_of_operation: int | None = None
    minutes_of_operation: int | None = None
    auto_off_minutes: int | None = None
    raw: dict[str, str | None] = field(default_factory=dict)


def _decode_int(raw: bytes | None, *, signed: bool = False) -> int | None:
    """Decode a little-endian integer of whatever width the device sent."""
    if not raw:
        return None
    width = 4 if len(raw) >= 4 else 2 if len(raw) >= 2 else 1
    return int.from_bytes(raw[:width], "little", signed=signed)


def _decode_temperature(raw: bytes | None) -> float | None:
    """Decode a temperature characteristic.

    The value is a little-endian *signed* integer in tenths of a degree
    Celsius. Reading it as unsigned turns the idle value (-18.0 C, wire bytes
    ``4c ff ff ff``) into 6535.6 C, which is what the previous implementation
    did before discarding every reading as out of range.
    """
    value = _decode_int(raw, signed=True)
    if value is None:
        return None
    return value / 10


def _decode_string(raw: bytes | None) -> str | None:
    """Decode a text characteristic, tolerating padding and bad bytes."""
    if not raw:
        return None
    text = raw.decode("utf-8", errors="replace").replace("\x00", "").strip()
    return text or None


def _decode_firmware(raw: bytes | None) -> str | None:
    """Decode a firmware version, which may be text or packed bytes."""
    if not raw:
        return None
    text = _decode_string(raw)
    if text and (text.startswith("V") or "." in text):
        return text
    if len(raw) >= 3:
        return f"V{raw[0]:02d}.{raw[1]:02d}.{raw[2]}"
    return f"V{raw.hex()}"


class VolcanoHybrid:
    """Bluetooth LE client for a single Volcano Hybrid."""

    def __init__(self, address: str) -> None:
        """Initialise the client for ``address``."""
        self.address = address
        self._ble_device: BLEDevice | None = None
        self._client: BleakClientWithServiceCache | None = None
        self._lock = asyncio.Lock()
        self._state = VolcanoState()
        self._callbacks: list[Callable[[VolcanoState], None]] = []
        self._optimistic: dict[str, bool] = {}
        self._optimistic_until = 0.0
        self._device_info_read_at: float | None = None
        self._static_info_read = False
        self._notifications_started = False

    # -- plumbing ----------------------------------------------------------

    @property
    def state(self) -> VolcanoState:
        """Return the last known state."""
        return self._state

    @property
    def connected(self) -> bool:
        """Return whether a live GATT connection exists."""
        return self._client is not None and self._client.is_connected

    def set_ble_device(self, ble_device: BLEDevice | None) -> None:
        """Update the BLEDevice, so reconnects use the closest adapter/proxy.

        ``None`` means no adapter or proxy can currently hear the device, and
        must be recorded rather than ignored: holding on to the last known
        BLEDevice sends every later poll into a full connection attempt for a
        vaporiser that is unplugged, which is both slow and pointless.
        """
        self._ble_device = ble_device

    def register_callback(
        self, callback: Callable[[VolcanoState], None]
    ) -> Callable[[], None]:
        """Register a listener for pushed state updates."""
        self._callbacks.append(callback)

        def _unregister() -> None:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

        return _unregister

    def _notify_listeners(self) -> None:
        for callback in self._callbacks:
            callback(self._state)

    def _handle_disconnect(self, _client: BleakClientWithServiceCache) -> None:
        """Handle the device dropping the link."""
        _LOGGER.debug("%s: disconnected", self.address)
        self._client = None
        self._notifications_started = False
        # Serial number and firmware are read once per connection, so a new link
        # has to read them again -- the device may have been swapped.
        self._static_info_read = False
        self._state.connected = False
        self._notify_listeners()

    # -- connection --------------------------------------------------------

    async def _ensure_client(self) -> BleakClientWithServiceCache:
        """Return a live client, connecting if needed. Assumes the lock is held."""
        if self._client is not None and self._client.is_connected:
            return self._client

        if (ble_device := self._ble_device) is None:
            raise VolcanoConnectionError(
                f"{self.address} is not currently visible to any Bluetooth adapter"
            )

        _LOGGER.debug("%s: connecting via %s", self.address, ble_device)
        try:
            client = await establish_connection(
                BleakClientWithServiceCache,
                ble_device,
                self.address,
                self._handle_disconnect,
                use_services_cache=True,
                max_attempts=CONNECT_ATTEMPTS,
                # Prefer whatever advertisement arrived most recently, falling
                # back to the one we started with so retries always have a
                # device to work from.
                ble_device_callback=lambda: self._ble_device or ble_device,
            )
        except (BleakNotFoundError, BleakError, TimeoutError) as err:
            raise VolcanoConnectionError(f"Could not connect: {err}") from err

        self._client = client
        self._state.connected = True

        try:
            await client.start_notify(CHAR_STATUS_REGISTER, self._notification_handler)
            self._notifications_started = True
        except (BleakError, TimeoutError) as err:
            # Notifications are an optimisation; polling still works without them.
            _LOGGER.debug(
                "%s: could not subscribe to status register: %s", self.address, err
            )

        return client

    async def async_connect(self) -> None:
        """Connect to the device, raising VolcanoConnectionError on failure."""
        async with self._lock:
            await self._ensure_client()

    async def async_disconnect(self) -> None:
        """Disconnect from the device."""
        async with self._lock:
            client = self._client
            self._client = None
            if client is None:
                return
            if self._notifications_started:
                try:
                    await client.stop_notify(CHAR_STATUS_REGISTER)
                except (BleakError, TimeoutError, EOFError) as err:
                    _LOGGER.debug("%s: stop_notify failed: %s", self.address, err)
                self._notifications_started = False
            try:
                await client.disconnect()
            except (BleakError, TimeoutError, EOFError) as err:
                _LOGGER.debug("%s: disconnect failed: %s", self.address, err)
            self._state.connected = False

    # -- GATT primitives (lock must already be held) -----------------------

    async def _read(self, uuid: str) -> bytes | None:
        """Read a characteristic, returning None if it is unavailable."""
        client = await self._ensure_client()
        try:
            return bytes(await client.read_gatt_char(uuid))
        except (BleakError, TimeoutError) as err:
            _LOGGER.debug("%s: read %s failed: %s", self.address, uuid, err)
            return None

    async def _write(self, uuid: str, data: bytes) -> None:
        """Write a characteristic."""
        client = await self._ensure_client()
        try:
            await client.write_gatt_char(uuid, data, response=True)
        except (BleakError, TimeoutError) as err:
            raise VolcanoConnectionError(f"Write to {uuid} failed: {err}") from err

    # -- notifications -----------------------------------------------------

    def _notification_handler(self, _sender: Any, data: bytearray) -> None:
        """Handle a status register notification."""
        if self._apply_status_register(bytes(data)):
            self._notify_listeners()

    def _apply_status_register(self, raw: bytes | None) -> bool:
        """Apply the heater/fan bits from the status register.

        Returns True if anything changed. Deliberately does *not* try to derive
        a temperature from these bits: the fan flag is 0x2000, so with the fan
        running the high byte lands inside the plausible temperature range and
        the old code reported the status word as a temperature.
        """
        if not raw or len(raw) < 2:
            return False

        value = int.from_bytes(raw[:2], "little")
        heater = bool(value & MASK_HEATER)
        fan = bool(value & MASK_FAN)

        if time.monotonic() < self._optimistic_until:
            heater = self._optimistic.get("heater_on", heater)
            fan = self._optimistic.get("fan_on", fan)

        changed = (heater, fan) != (self._state.heater_on, self._state.fan_on)
        self._state.heater_on = heater
        self._state.fan_on = fan
        self._state.raw["status_register"] = raw.hex()
        return changed

    def _set_optimistic(self, **values: bool) -> None:
        """Trust an explicit command over the status register for a few seconds."""
        self._optimistic = values
        self._optimistic_until = time.monotonic() + OPTIMISTIC_WINDOW
        for key, value in values.items():
            setattr(self._state, key, value)

    # -- polling -----------------------------------------------------------

    async def async_update(self) -> VolcanoState:
        """Refresh and return the device state."""
        async with self._lock:
            await self._ensure_client()

            raw_current = await self._read(CHAR_CURRENT_TEMP)
            self._state.raw["current_temperature"] = (
                raw_current.hex() if raw_current else None
            )
            current = _decode_temperature(raw_current)
            self._state.current_temperature = (
                None if current is None or current < TEMP_INVALID_BELOW else current
            )

            raw_target = await self._read(CHAR_TARGET_TEMP)
            self._state.raw["target_temperature"] = (
                raw_target.hex() if raw_target else None
            )
            target = _decode_temperature(raw_target)
            if target is not None and MIN_TEMP <= target <= MAX_TEMP:
                self._state.target_temperature = target

            # Kept on every poll even though notifications cover it: a dropped
            # notification would otherwise leave the heater reading "on" until
            # the next change, and this is the one value worth a re-read.
            raw_status = await self._read(CHAR_STATUS_REGISTER)
            self._apply_status_register(raw_status)

            # A link that answers nothing is dead even though bleak has not
            # noticed. Reporting it as connected strands every entity on stale
            # values; failing here hands it to the coordinator, which already
            # knows how to log the outage once and recover from it.
            if raw_current is None and raw_target is None and raw_status is None:
                self._state.connected = False
                raise VolcanoConnectionError(
                    f"{self.address} accepted the connection but answered no reads"
                )

            brightness = _decode_int(await self._read(CHAR_BRIGHTNESS))
            if brightness is not None and 0 <= brightness <= 100:
                self._state.brightness = brightness

            # Read on the very first poll, then only occasionally.
            if (
                self._device_info_read_at is None
                or time.monotonic() - self._device_info_read_at > DEVICE_INFO_INTERVAL
            ):
                await self._read_device_information()
                self._device_info_read_at = time.monotonic()

            self._state.connected = True
            return self._state

    async def _read_device_information(self) -> None:
        """Read the slow-changing device information. Assumes the lock is held."""
        # Serial number and firmware cannot change while a connection is open,
        # so they are read once per link rather than on every slow cycle.
        if not self._static_info_read:
            self._state.serial_number = (
                _decode_string(await self._read(CHAR_SERIAL_NUMBER))
                or self._state.serial_number
            )
            self._state.ble_firmware_version = (
                _decode_firmware(await self._read(CHAR_BLE_FIRMWARE))
                or self._state.ble_firmware_version
            )
            self._state.firmware_version = (
                _decode_firmware(await self._read(CHAR_FIRMWARE))
                or self._state.firmware_version
            )
            self._static_info_read = True

        hours = _decode_int(await self._read(CHAR_HOURS_OF_OPERATION))
        if hours is not None:
            self._state.hours_of_operation = hours
        minutes = _decode_int(await self._read(CHAR_MINUTES_OF_OPERATION))
        if minutes is not None:
            self._state.minutes_of_operation = minutes

        auto_off = await self._read(CHAR_AUTO_OFF_SETTING)
        if auto_off is None:
            auto_off = await self._read(CHAR_AUTO_OFF_REMAINING)
        self._state.raw["auto_off"] = auto_off.hex() if auto_off else None
        seconds = _decode_int(auto_off)
        if seconds is not None:
            # The device stores the auto-off delay in seconds.
            minutes_value = seconds // 60
            if MIN_AUTO_OFF_MINUTES <= minutes_value <= MAX_AUTO_OFF_MINUTES:
                self._state.auto_off_minutes = minutes_value

        register3 = _decode_int(await self._read(CHAR_REGISTER3))
        if register3 is not None:
            self._state.register3 = register3 > 0
        register2 = _decode_int(await self._read(CHAR_REGISTER2))
        if register2 is not None:
            self._state.register2 = register2 > 0

    # -- commands ----------------------------------------------------------

    async def async_turn_heater_on(self) -> None:
        """Turn the heater on."""
        async with self._lock:
            await self._write(CHAR_HEAT_ON, bytes([0]))
        self._set_optimistic(heater_on=True)

    async def async_turn_heater_off(self) -> None:
        """Turn the heater off."""
        async with self._lock:
            await self._write(CHAR_HEAT_OFF, bytes([0]))
        self._set_optimistic(heater_on=False)

    async def async_turn_fan_on(self) -> None:
        """Turn the fan on."""
        async with self._lock:
            await self._write(CHAR_FAN_ON, bytes([0]))
        self._set_optimistic(fan_on=True)

    async def async_turn_fan_off(self) -> None:
        """Turn the fan off."""
        async with self._lock:
            await self._write(CHAR_FAN_OFF, bytes([0]))
        self._set_optimistic(fan_on=False)

    async def async_set_target_temperature(self, celsius: float) -> None:
        """Set the target temperature in degrees Celsius."""
        value = int(max(MIN_TEMP, min(MAX_TEMP, celsius)))
        async with self._lock:
            await self._write(CHAR_TARGET_TEMP, struct.pack("<I", value * 10))
        self._state.target_temperature = float(value)

    async def async_set_brightness(self, percent: int) -> None:
        """Set the screen brightness as a percentage."""
        value = max(0, min(100, int(percent)))
        async with self._lock:
            await self._write(CHAR_BRIGHTNESS, struct.pack("<H", value))
        self._state.brightness = value

    async def async_set_auto_off_minutes(self, minutes: int) -> None:
        """Set the auto-off delay in minutes."""
        value = max(MIN_AUTO_OFF_MINUTES, min(MAX_AUTO_OFF_MINUTES, int(minutes)))
        payload = struct.pack("<H", value * 60)
        async with self._lock:
            try:
                await self._write(CHAR_AUTO_OFF_SETTING, payload)
            except VolcanoConnectionError:
                await self._write(CHAR_AUTO_OFF_REMAINING, payload)
        self._state.auto_off_minutes = value

    async def async_set_register3(self, enabled: bool) -> None:
        """Set register 3 (vibration on the stock firmware)."""
        async with self._lock:
            await self._write(CHAR_REGISTER3, bytes([1 if enabled else 0]))
        self._state.register3 = enabled

    async def async_set_register2(self, enabled: bool) -> None:
        """Set register 2 (display during cooling on the stock firmware)."""
        async with self._lock:
            await self._write(CHAR_REGISTER2, bytes([1 if enabled else 0]))
        self._state.register2 = enabled
