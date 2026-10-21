"""The Volcano Hybrid's protocol: what to read, and how to decode it.

The Bluetooth link itself -- connecting, rebuilding a dead link, the GATT lock
-- is shared with the other families in ``ble_device.py``. What is here is the
Volcano's own: one characteristic per value, temperatures in tenths, and the
five status registers decoded from the firmware.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import struct
import time
from typing import Any, ClassVar

from .ble_device import (
    ADAPTER_CACHE_RESET_AFTER,
    DEVICE_INFO_INTERVAL,
    ConnectionFailure,
    DeviceConnectionError,
    DeviceState,
    StorzBickelDevice,
    _bit,
    _decode_firmware,
    _decode_int,
    _decode_string,
)
from .const import (
    DEVICE_MIN_AUTO_OFF_MINUTES,
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
# ASCII, read only: the mains the unit was built for ("230VAC") and its own name
# for its model class ("HYBRID"). Both advertise write; nothing ever writes them.
CHAR_MAINS_VOLTAGE = "10100006-5354-4f52-5a26-4249434b454c"
CHAR_MODEL = "10100007-5354-4f52-5a26-4249434b454c"
CHAR_HOURS_OF_OPERATION = "10110015-5354-4f52-5a26-4249434b454c"
CHAR_MINUTES_OF_OPERATION = "10110016-5354-4f52-5a26-4249434b454c"
CHAR_REGISTER2 = "1010000d-5354-4f52-5a26-4249434b454c"
CHAR_REGISTER3 = "1010000e-5354-4f52-5a26-4249434b454c"
CHAR_REGISTER4 = "1010000f-5354-4f52-5a26-4249434b454c"
CHAR_REGISTER5 = "10100010-5354-4f52-5a26-4249434b454c"
CHAR_HISTORY1 = "10100015-5354-4f52-5a26-4249434b454c"
CHAR_HISTORY2 = "10100016-5354-4f52-5a26-4249434b454c"

# Every Volcano Hybrid has these. A link whose service table lacks any of them
# resolved GATT only partly: on 2026-10-06 one came up with the status service
# but not the control service, so status pushes kept arriving while every
# temperature read and every command failed with "characteristic not found".
REQUIRED_CHARACTERISTICS = frozenset(
    {
        CHAR_CURRENT_TEMP,
        CHAR_TARGET_TEMP,
        CHAR_STATUS_REGISTER,
        CHAR_HEAT_ON,
        CHAR_HEAT_OFF,
        CHAR_FAN_ON,
        CHAR_FAN_OFF,
        CHAR_BRIGHTNESS,
    }
)

# Bit masks, from the decoded firmware in magikh0e's VOLCANO_BLE_SPEC.md. Each
# register is a 16-bit word; the device sends four bytes, the top two zero.
#
# Status register 1 (PRJSTAT1): live heater and pump state.
MASK_HEATER = 0x0020
MASK_FAN = 0x2000
MASK_HEATER_FAULT = 0x0008  # fault 60: stops the heater only
MASK_HEATER_PUMP_FAULT = 0x0010  # fault 61: stops the heater and the pump
MASK_PUMP_INTERLOCK = 0x4000
MASK_R1_ERR = 0x4018
# Set the moment the block reaches the target; cleared only by raising the
# target 3 C or more, never by lowering it. Confirmed live. It is the same
# signal that drives the device's own vibration alert.
MASK_TARGET_REACHED = 0x0400
# Status register 2 (PRJSTAT2): regulation faults and display settings.
MASK_SERVICE_MODE = 0x0040  # burn-in: heats itself to 230 C for ten minutes
MASK_FAHRENHEIT = 0x0200
MASK_DISPLAY_COOLING_OFF = 0x1000  # inverted: clear keeps the display on
MASK_R2_ERR = 0x003B
R2_ERROR_FLAGS: dict[int, str] = {
    0x0001: "regulation_sample_out_of_range",
    0x0002: "regulation_value_outside_window",
    0x0008: "heartbeat_timeout",
    0x0010: "heater_feedback_low",
    0x0020: "heater_feedback_high",
}
# Status register 3 (PRJSTAT3): hardware settings.
MASK_VIBRATION_OFF = 0x0400  # inverted: clear means vibration is on

# Registers 2 and 3 take a four-byte little-endian word: the mask sets those
# bits, the mask plus this flag clears them. Register 1 does NOT accept it --
# a write there fails to latch, can stop the pump and leaves a shadow value in
# the register until a mains power cycle -- so it is never written at all.
REGISTER_CLEAR_FLAG = 0x00010000
WRITABLE_REGISTERS = frozenset({CHAR_REGISTER2, CHAR_REGISTER3})

# Fault codes logged in the error history, as decimal numbers.
FAULT_CODES: dict[int, tuple[str, str]] = {
    45: ("regulation_reading_low", "Regulation reading pinned low"),
    53: ("sensor_short", "Temperature sensor short circuit"),
    54: ("sensor_open", "Temperature sensor open circuit"),
    60: ("heater_timing_fault", "Heater timing fault (heater stopped)"),
    61: ("heater_pump_timing_fault", "Heater timing fault (heater and pump stopped)"),
    63: ("regulation_average_high", "Regulation average too high"),
    64: ("regulation_value_outside_window", "Regulation value outside valid window"),
    65: ("heartbeat_timeout", "Heartbeat / communication timeout"),
    67: ("heater_feedback_low", "Heater feedback too low"),
    68: ("heater_feedback_high", "Heater feedback too high"),
    72: ("heater_feedback_deviation", "Heater feedback outside its band"),
}

# The device reports -18.0 C on the temperature characteristic when the probe
# has no valid reading (idle / powered down). Anything below freezing is not a
# real reading from a vaporiser, so treat it as "unknown" rather than surfacing
# a nonsense number.
TEMP_INVALID_BELOW = 0.0

# How long an explicit heater/fan command is trusted over the status register.
# BLE notifications lag the write by a beat; without this the UI snaps back.
OPTIMISTIC_WINDOW = 3.0

# Heater-off and fan-off are re-sent once if the status register still shows
# them on this long after the first write. An "off" that is lost leaves the
# device running, so it is worth one retry; an "on" is never retried, because
# repeating it could start the device after the user meant it to stay off.
OFF_CONFIRM_DELAY = 1.0


@dataclass
class VolcanoState(DeviceState):
    """Snapshot of a Volcano Hybrid's state."""

    brightness: int | None = None
    # Low 16 bits of each status register; None if the device does not have it.
    status1: int | None = None
    status2: int | None = None
    status3: int | None = None
    status4: int | None = None
    status5: int | None = None
    # Error history logs, as the text the device sent.
    history1: str | None = None
    history2: str | None = None
    ble_firmware_version: str | None = None
    mains_voltage: str | None = None
    hours_of_operation: int | None = None
    minutes_of_operation: int | None = None
    auto_off_minutes: int | None = None

    @property
    def target_reached(self) -> bool | None:
        """Return whether the device reports it has reached the target."""
        return _bit(self.status1, MASK_TARGET_REACHED)

    @property
    def heater_pump_fault(self) -> bool | None:
        """Return whether the heater timing fault that also stops the pump is set."""
        return _bit(self.status1, MASK_HEATER_PUMP_FAULT)

    @property
    def heater_fault(self) -> bool | None:
        """Return whether the heater-only timing fault is set."""
        return _bit(self.status1, MASK_HEATER_FAULT)

    @property
    def pump_interlock_fault(self) -> bool | None:
        """Return whether the pump interlock fault is set."""
        return _bit(self.status1, MASK_PUMP_INTERLOCK)

    @property
    def regulation_faults(self) -> list[str]:
        """Return the regulation fault flags set in status register 2."""
        if self.status2 is None:
            return []
        return [name for mask, name in R2_ERROR_FLAGS.items() if self.status2 & mask]

    @property
    def fault_known(self) -> bool:
        """Return whether status register 1 has been read."""
        return self.status1 is not None

    @property
    def heat_or_air_reported_on(self) -> bool:
        """Return whether register 1 itself still shows heater or fan on."""
        return bool((self.status1 or 0) & (MASK_HEATER | MASK_FAN))

    @property
    def any_fault(self) -> bool:
        """Return whether either register reports any error bit."""
        return bool((self.status1 or 0) & MASK_R1_ERR) or bool(
            (self.status2 or 0) & MASK_R2_ERR
        )

    @property
    def service_mode(self) -> bool | None:
        """Return whether the device is in its service / burn-in mode."""
        return _bit(self.status2, MASK_SERVICE_MODE)

    @property
    def display_fahrenheit(self) -> bool | None:
        """Return whether the device displays Fahrenheit."""
        return _bit(self.status2, MASK_FAHRENHEIT)

    @property
    def display_while_cooling(self) -> bool | None:
        """Return whether the display stays on while cooling (inverted bit)."""
        off = _bit(self.status2, MASK_DISPLAY_COOLING_OFF)
        return None if off is None else not off

    @property
    def vibration(self) -> bool | None:
        """Return whether vibration is enabled (inverted bit)."""
        off = _bit(self.status3, MASK_VIBRATION_OFF)
        return None if off is None else not off

    @property
    def fault_log(self) -> list[int] | None:
        """Return the logged fault codes, newest first.

        History 1 then history 2, each newest first. That order comes from the
        firmware's design and has not been confirmed on a device. None when
        neither log could be read or decoded.
        """
        logs = [_decode_history(text) for text in (self.history1, self.history2)]
        if all(log is None for log in logs):
            return None
        return [code for log in logs if log for code in log]

    @property
    def last_fault(self) -> int | None:
        """Return the newest logged fault code, 0 for an empty log."""
        log = self.fault_log
        if log is None:
            return None
        return log[0] if log else 0


def _decode_register(raw: bytes | None) -> int | None:
    """Decode a status register to its 16-bit word."""
    if not raw or len(raw) < 2:
        return None
    return int.from_bytes(raw[:2], "little")


def _decode_history(text: str | None) -> list[int] | None:
    """Decode an error history log into its fault codes.

    The device sends text of two-digit decimal fields, one per log slot, with
    ``00`` for an empty slot. Anything else is not a format we understand, so
    it decodes to None and only the raw value is kept.
    """
    if not text or len(text) % 2 or not (text.isascii() and text.isdigit()):
        return None
    codes = (int(text[i : i + 2]) for i in range(0, len(text), 2))
    return [code for code in codes if code]


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


class VolcanoHybrid(StorzBickelDevice):
    """Bluetooth LE client for a single Volcano Hybrid."""

    required_characteristics: ClassVar[frozenset[str]] = REQUIRED_CHARACTERISTICS
    notify_characteristics: ClassVar[tuple[str, ...]] = (CHAR_STATUS_REGISTER,)

    def __init__(self, address: str) -> None:
        """Initialise the client for ``address``."""
        super().__init__(address, VolcanoState())
        self._state: VolcanoState
        self._optimistic: dict[str, bool] = {}
        self._optimistic_until = 0.0
        self._device_info_read_at: float | None = None
        self._no_read_failures = 0
        self._fault_seen = False

    @property
    def state(self) -> VolcanoState:
        """Return the last known state."""
        return self._state

    # -- notifications -----------------------------------------------------

    def _handle_notification(self, uuid: str, data: bytes) -> None:
        """Route a notification; the status register is the only one."""
        self._notification_handler(None, bytearray(data))

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
        value = _decode_register(raw)
        if raw is None or value is None:
            return False

        heater = bool(value & MASK_HEATER)
        fan = bool(value & MASK_FAN)

        if time.monotonic() < self._optimistic_until:
            heater = self._optimistic.get("heater_on", heater)
            fan = self._optimistic.get("fan_on", fan)

        changed = (heater, fan, value) != (
            self._state.heater_on,
            self._state.fan_on,
            self._state.status1,
        )
        self._state.heater_on = heater
        self._state.fan_on = fan
        self._state.status1 = value
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
            #
            # Status answering on its own is no better: that is a half-resolved
            # service table, and counting it a success kept a link up for five
            # minutes with no temperature and every command failing.
            if raw_current is None and raw_target is None:
                self._no_read_failures += 1
                await self._async_reset_link(
                    reset_adapter_cache=(
                        self._no_read_failures >= ADAPTER_CACHE_RESET_AFTER
                    )
                )
                if raw_status is None:
                    raise DeviceConnectionError(
                        f"{self.address} accepted the connection but answered no"
                        f" reads ({self._last_read_error})",
                        ConnectionFailure.NO_READS,
                    )
                raise DeviceConnectionError(
                    f"{self.address} answered the status register but no"
                    f" temperature reads ({self._last_read_error})",
                    ConnectionFailure.STALE_GATT,
                )

            self._no_read_failures = 0

            # Read every poll, unlike registers 3-5: service mode and the
            # regulation faults live here, and both can change mid-session.
            self._apply_register(2, await self._read(CHAR_REGISTER2))

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
            elif self._state.any_fault and not self._fault_seen:
                # A fault just appeared, so the log has a new entry worth
                # showing now rather than up to ten minutes from now. Tracked
                # across polls because a notification can set it in between.
                await self._read_history()
            self._fault_seen = self._state.any_fault

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
            self._state.mains_voltage = (
                _decode_string(await self._read(CHAR_MAINS_VOLTAGE))
                or self._state.mains_voltage
            )
            self._state.model = (
                _decode_string(await self._read(CHAR_MODEL)) or self._state.model
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
            # The device's own floor, not the one offered for setting: a delay
            # set below that elsewhere is real and must still be shown.
            if DEVICE_MIN_AUTO_OFF_MINUTES <= minutes_value <= MAX_AUTO_OFF_MINUTES:
                self._state.auto_off_minutes = minutes_value

        self._apply_register(3, await self._read(CHAR_REGISTER3))
        # Nothing decodes registers 4 and 5 yet; they are kept for diagnosis
        # and stay None on a device that does not report them.
        self._apply_register(4, await self._read(CHAR_REGISTER4))
        self._apply_register(5, await self._read(CHAR_REGISTER5))
        await self._read_history()

    def _apply_register(self, number: int, raw: bytes | None) -> None:
        """Store status register ``number`` (2-5) and its raw bytes."""
        setattr(self._state, f"status{number}", _decode_register(raw))
        self._state.raw[f"status_register_{number}"] = raw.hex() if raw else None

    async def _read_history(self) -> None:
        """Read both error history logs. Assumes the lock is held."""
        for number, uuid in ((1, CHAR_HISTORY1), (2, CHAR_HISTORY2)):
            raw = await self._read(uuid)
            setattr(self._state, f"history{number}", _decode_string(raw))
            self._state.raw[f"error_history_{number}"] = raw.hex() if raw else None

    # -- commands ----------------------------------------------------------

    async def async_turn_heater_on(self) -> None:
        """Turn the heater on."""
        async with self._lock:
            await self._write(CHAR_HEAT_ON, bytes([0]))
        self._set_optimistic(heater_on=True)

    async def async_turn_heater_off(self) -> None:
        """Turn the heater off, re-sending once if it does not take."""
        async with self._lock:
            await self._write(CHAR_HEAT_OFF, bytes([0]))
        self._set_optimistic(heater_on=False)
        await self._async_confirm_off(CHAR_HEAT_OFF, MASK_HEATER)

    async def async_turn_fan_on(self) -> None:
        """Turn the fan on."""
        async with self._lock:
            await self._write(CHAR_FAN_ON, bytes([0]))
        self._set_optimistic(fan_on=True)

    async def async_turn_fan_off(self) -> None:
        """Turn the fan off, re-sending once if it does not take."""
        async with self._lock:
            await self._write(CHAR_FAN_OFF, bytes([0]))
        self._set_optimistic(fan_on=False)
        await self._async_confirm_off(CHAR_FAN_OFF, MASK_FAN)

    async def _async_confirm_off(self, uuid: str, mask: int) -> None:
        """Re-send an off command once if the device still reports it on.

        Read straight from the register rather than the state: the state is
        optimistic for a few seconds after a command, which is exactly the
        window this is checking. The lock is not held across the wait, so
        other commands are not queued behind it.
        """
        await asyncio.sleep(OFF_CONFIRM_DELAY)
        async with self._lock:
            value = _decode_register(await self._read(CHAR_STATUS_REGISTER))
            if value is None or not value & mask:
                return
            _LOGGER.debug("%s: still on after %s, sending again", self.address, uuid)
            await self._write(uuid, bytes([0]))

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
            except DeviceConnectionError:
                await self._write(CHAR_AUTO_OFF_REMAINING, payload)
        self._state.auto_off_minutes = value

    async def _write_register_bits(self, uuid: str, mask: int, set_: bool) -> None:
        """Set or clear ``mask`` in status register 2 or 3, then read it back.

        Assumes the lock is held. Reading back means the state shows what the
        device latched rather than what was asked for: the front panel changes
        these settings too, and a rejected write must not look accepted.
        """
        if uuid not in WRITABLE_REGISTERS:
            raise ValueError(f"{uuid} does not accept set/clear writes")
        word = mask if set_ else REGISTER_CLEAR_FLAG | mask
        await self._write(uuid, struct.pack("<I", word))
        number = 2 if uuid == CHAR_REGISTER2 else 3
        self._apply_register(number, await self._read(uuid))

    async def async_set_display_while_cooling(self, enabled: bool) -> None:
        """Keep the display on while cooling. The bit is inverted."""
        async with self._lock:
            await self._write_register_bits(
                CHAR_REGISTER2, MASK_DISPLAY_COOLING_OFF, not enabled
            )

    async def async_set_display_fahrenheit(self, enabled: bool) -> None:
        """Show Fahrenheit (True) or Celsius (False) on the device."""
        async with self._lock:
            await self._write_register_bits(CHAR_REGISTER2, MASK_FAHRENHEIT, enabled)

    async def async_set_vibration(self, enabled: bool) -> None:
        """Enable or disable vibration. The bit is inverted."""
        async with self._lock:
            await self._write_register_bits(
                CHAR_REGISTER3, MASK_VIBRATION_OFF, not enabled
            )
