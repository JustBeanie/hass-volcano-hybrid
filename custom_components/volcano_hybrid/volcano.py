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
from enum import StrEnum
import logging
import struct
import time
from typing import Any

from bleak.backends.device import BLEDevice
from bleak.exc import BleakCharacteristicNotFoundError, BleakError
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    BleakNotFoundError,
    clear_cache as clear_adapter_cache,
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

# Device information changes rarely; re-read it on this cadence only.
DEVICE_INFO_INTERVAL = 600.0

# The coordinator retries on its own schedule, so a poll that cannot reach the
# device should give up quickly rather than work through a long retry ladder
# while holding the GATT lock -- every command queues behind that lock.
CONNECT_ATTEMPTS = 2

# Dropping the client and reconnecting is enough to clear most unresponsive
# links. This many in a row means the cached GATT table on the adapter itself is
# suspect, so the next reset clears that too.
ADAPTER_CACHE_RESET_AFTER = 3


class ConnectionFailure(StrEnum):
    """Why the device could not be reached.

    The shapes are not interchangeable and the coordinator acts on the
    difference. A vaporiser that accepts a connection and then answers nothing
    was reported as a *refused* connection for a while, which sent users looking
    for a phone app holding a link that Home Assistant itself held.
    """

    # Nothing can currently hear the device: no adapter, no proxy.
    NOT_VISIBLE = "not_visible"
    # Turned away at the connect stage. The only shape that points at another
    # client holding the single connection the device allows.
    CONNECT_FAILED = "connect_failed"
    # Connected, but GATT is unusable -- a stale service table or a wedged
    # device. Nothing the user can fix by closing an app.
    NO_READS = "no_reads"
    # Connected, but the service table is missing characteristics every
    # Volcano has, even after rediscovering it. Recovered the same way as
    # NO_READS; kept apart so the log says which one it was.
    STALE_GATT = "stale_gatt"
    WRITE_FAILED = "write_failed"


class VolcanoConnectionError(Exception):
    """Raised when the device cannot be reached."""

    def __init__(self, message: str, kind: ConnectionFailure) -> None:
        """Record what kind of failure this was.

        ``kind`` is deliberately required rather than defaulted: guessing it is
        the exact bug this class exists to prevent.
        """
        super().__init__(message)
        self.kind = kind


@dataclass
class VolcanoState:
    """Snapshot of the device state."""

    current_temperature: float | None = None
    target_temperature: float | None = None
    heater_on: bool = False
    fan_on: bool = False
    brightness: int | None = None
    connected: bool = False
    # Low 16 bits of each status register; None if the device does not have it.
    status1: int | None = None
    status2: int | None = None
    status3: int | None = None
    status4: int | None = None
    status5: int | None = None
    # Error history logs, as the text the device sent.
    history1: str | None = None
    history2: str | None = None
    serial_number: str | None = None
    ble_firmware_version: str | None = None
    firmware_version: str | None = None
    hours_of_operation: int | None = None
    minutes_of_operation: int | None = None
    auto_off_minutes: int | None = None
    raw: dict[str, str | None] = field(default_factory=dict)

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


def _bit(value: int | None, mask: int) -> bool | None:
    """Return whether ``mask`` is set in ``value``, None if it is unknown."""
    return None if value is None else bool(value & mask)


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


def _missing_characteristics(client: BleakClientWithServiceCache) -> list[str]:
    """Return the required characteristics the client's service table lacks."""
    try:
        services = client.services
    except BleakError:
        # Service discovery never completed: nothing is resolvable.
        return sorted(REQUIRED_CHARACTERISTICS)
    return sorted(
        uuid
        for uuid in REQUIRED_CHARACTERISTICS
        if services.get_characteristic(uuid) is None
    )


def _is_characteristic_not_found(err: BaseException | None) -> bool:
    """Return whether bleak failed because the service table lacks the UUID.

    Matched on the text too: an ESPHome proxy reports it as a plain BleakError.
    """
    return isinstance(err, BleakCharacteristicNotFoundError) or (
        isinstance(err, BleakError) and "not found" in str(err).lower()
    )


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
        self._last_read_error: str | None = None
        self._no_read_failures = 0
        self._fault_seen = False

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
                f"{self.address} is not currently visible to any Bluetooth adapter",
                ConnectionFailure.NOT_VISIBLE,
            )

        _LOGGER.debug("%s: connecting via %s", self.address, ble_device)
        client = await self._async_establish(ble_device, use_services_cache=True)

        # A partly resolved service table is not fixed by reconnecting with the
        # same cache -- the 2026-10-06 outage survived a drop and reconnect and
        # only cleared on a reload. Rediscover once before giving up on it.
        if missing := _missing_characteristics(client):
            _LOGGER.debug(
                "%s: service table lacks %s, rediscovering", self.address, missing
            )
            await self._async_drop_client(client)
            client = await self._async_establish(ble_device, use_services_cache=False)
            if missing := _missing_characteristics(client):
                await self._async_drop_client(client)
                await clear_adapter_cache(self.address)
                raise VolcanoConnectionError(
                    f"{self.address} connected but its service table lacks"
                    f" {', '.join(missing)}",
                    ConnectionFailure.STALE_GATT,
                )

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

    async def _async_establish(
        self, ble_device: BLEDevice, *, use_services_cache: bool
    ) -> BleakClientWithServiceCache:
        """Open a link, raising CONNECT_FAILED if the device turns us away."""
        try:
            return await establish_connection(
                BleakClientWithServiceCache,
                ble_device,
                self.address,
                self._handle_disconnect,
                use_services_cache=use_services_cache,
                max_attempts=CONNECT_ATTEMPTS,
                # Prefer whatever advertisement arrived most recently, falling
                # back to the one we started with so retries always have a
                # device to work from.
                ble_device_callback=lambda: self._ble_device or ble_device,
            )
        except (BleakNotFoundError, BleakError, TimeoutError) as err:
            raise VolcanoConnectionError(
                f"Could not connect: {err}", ConnectionFailure.CONNECT_FAILED
            ) from err

    async def _async_drop_client(self, client: BleakClientWithServiceCache) -> None:
        """Clear a client's cached service table and disconnect it."""
        try:
            # Clears the cached service table this client resolved against,
            # local adapter or proxy, so the reconnect rediscovers GATT.
            await client.clear_cache()
            await client.disconnect()
        except (BleakError, TimeoutError, EOFError) as err:
            _LOGGER.debug("%s: resetting the link failed: %s", self.address, err)

    async def async_connect(self) -> None:
        """Connect to the device, raising VolcanoConnectionError on failure."""
        async with self._lock:
            await self._ensure_client()

    async def _async_reset_link(self, *, reset_adapter_cache: bool) -> None:
        """Throw the link away so the next attempt rebuilds it from scratch.

        Assumes the lock is held, which is why it cannot simply call
        ``async_disconnect`` -- that acquires the same non-reentrant lock and
        would deadlock the poll it is being called from.

        Without this, a client that bleak still considers connected but that
        answers no reads was handed straight back by ``_ensure_client`` on every
        later poll, so the integration could not recover on its own and needed
        the config entry reloading by hand.
        """
        client = self._client
        self._client = None
        self._notifications_started = False
        # A new link has to read these again; they are only valid per-connection.
        self._static_info_read = False
        self._state.connected = False

        if client is not None:
            await self._async_drop_client(client)

        if reset_adapter_cache:
            # Swallows its own errors and returns False off a local BlueZ
            # adapter, where the client-level clear above is the one that counts.
            cleared = await clear_adapter_cache(self.address)
            _LOGGER.debug("%s: adapter cache cleared: %s", self.address, cleared)

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
            # Kept so a link that answers nothing can report why. Losing this to
            # a debug line meant an outage was only ever recorded as "answered
            # no reads", with no way to tell a stale service table (bleak says
            # the characteristic was not found) from a device that has stopped
            # responding (a timeout) after the fact.
            self._last_read_error = f"{uuid}: {err}"
            _LOGGER.debug("%s: read %s failed: %s", self.address, uuid, err)
            return None

    async def _write(self, uuid: str, data: bytes) -> None:
        """Write a characteristic, rebuilding a link that has lost it once.

        A required characteristic going missing means the service table is
        stale, not that the device lacks it, so the link is rebuilt and the
        write tried again rather than failing the button press. Optional ones
        (the auto-off setting) really are absent on some firmware and fail
        straight through to their caller's fallback.
        """
        try:
            await self._write_once(uuid, data)
        except VolcanoConnectionError as err:
            if uuid not in REQUIRED_CHARACTERISTICS or not (
                _is_characteristic_not_found(err.__cause__)
            ):
                raise
            _LOGGER.debug("%s: %s, rebuilding the link", self.address, err)
            await self._async_reset_link(reset_adapter_cache=True)
            await self._write_once(uuid, data)

    async def _write_once(self, uuid: str, data: bytes) -> None:
        """Write a characteristic on the current link."""
        client = await self._ensure_client()
        try:
            await client.write_gatt_char(uuid, data, response=True)
        except (BleakError, TimeoutError) as err:
            raise VolcanoConnectionError(
                f"Write to {uuid} failed: {err}", ConnectionFailure.WRITE_FAILED
            ) from err

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
                    raise VolcanoConnectionError(
                        f"{self.address} accepted the connection but answered no"
                        f" reads ({self._last_read_error})",
                        ConnectionFailure.NO_READS,
                    )
                raise VolcanoConnectionError(
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
