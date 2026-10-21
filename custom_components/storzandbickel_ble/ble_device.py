"""The Bluetooth link every Storz & Bickel device shares.

Connecting, rebuilding a dead link, rediscovering a half-resolved service
table and serialising GATT traffic are the same whatever the device, so they
live here and each family only says what to read and how to decode it. The
Volcano is where all of this was learned; see ``volcano.py`` for why each
piece exists.

All GATT traffic is serialised through a single lock held at exactly one level:
the public coroutines acquire it, the private ``_read``/``_write`` helpers
assume it is already held. Nothing below the lock may re-enter a public
coroutine -- ``asyncio.Lock`` is not reentrant.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum
import logging
from typing import Any, ClassVar

from bleak.backends.device import BLEDevice
from bleak.exc import BleakCharacteristicNotFoundError, BleakError
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    BleakNotFoundError,
    clear_cache as clear_adapter_cache,
    establish_connection,
)

from .const import ACTIVE_INTERVAL, IDLE_INTERVAL, MAX_TEMP

_LOGGER = logging.getLogger(__name__)

# The coordinator retries on its own schedule, so a poll that cannot reach the
# device should give up quickly rather than work through a long retry ladder
# while holding the GATT lock -- every command queues behind that lock.
CONNECT_ATTEMPTS = 2

# Dropping the client and reconnecting is enough to clear most unresponsive
# links. This many in a row means the cached GATT table on the adapter itself is
# suspect, so the next reset clears that too.
ADAPTER_CACHE_RESET_AFTER = 3

# Device information changes rarely; re-read it on this cadence only.
DEVICE_INFO_INTERVAL = 600.0


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
    # device of the family has, even after rediscovering it. Recovered the
    # same way as NO_READS; kept apart so the log says which one it was.
    STALE_GATT = "stale_gatt"
    WRITE_FAILED = "write_failed"


class DeviceConnectionError(Exception):
    """Raised when the device cannot be reached."""

    def __init__(self, message: str, kind: ConnectionFailure) -> None:
        """Record what kind of failure this was.

        ``kind`` is deliberately required rather than defaulted: guessing it is
        the exact bug this class exists to prevent.
        """
        super().__init__(message)
        self.kind = kind


@dataclass
class DeviceState:
    """What every family reports. Each family's state adds its own fields."""

    current_temperature: float | None = None
    target_temperature: float | None = None
    heater_on: bool = False
    # Only the Volcano has an air pump; the portables leave this False.
    fan_on: bool = False
    connected: bool = False
    serial_number: str | None = None
    firmware_version: str | None = None
    model: str | None = None
    # Signal strength of the last advertisement, and the adapter or proxy the
    # current link goes through.
    rssi: int | None = None
    connected_via: str | None = None
    raw: dict[str, str | None] = field(default_factory=dict)

    @property
    def target_reached(self) -> bool | None:
        """Return the device's own "at temperature" signal, None if unknown."""
        return None

    @property
    def fault_known(self) -> bool:
        """Return whether the fault flags have been read at all."""
        return False

    @property
    def any_fault(self) -> bool:
        """Return whether the device reports any fault."""
        return False

    @property
    def last_fault(self) -> int | None:
        """Return the newest logged fault code, where the device logs them."""
        return None

    @property
    def heat_or_air_reported_on(self) -> bool:
        """Return whether the device's own registers say it is still running.

        Used to confirm an "off" before letting go of the link, so it has to
        come from the device rather than from the optimistic state.
        """
        return self.heater_on or self.fan_on


def _ble_device_source(ble_device: BLEDevice) -> str | None:
    """Return the adapter or proxy a BLEDevice was heard through.

    Home Assistant's Bluetooth stack records it as ``details["source"]`` for
    local adapters and ESPHome proxies alike; anything else has no source.
    """
    details = ble_device.details
    source = details.get("source") if isinstance(details, dict) else None
    return source if isinstance(source, str) else None


def _bit(value: int | None, mask: int) -> bool | None:
    """Return whether ``mask`` is set in ``value``, None if it is unknown."""
    return None if value is None else bool(value & mask)


def _decode_int(raw: bytes | None, *, signed: bool = False) -> int | None:
    """Decode a little-endian integer of whatever width the device sent."""
    if not raw:
        return None
    width = 4 if len(raw) >= 4 else 2 if len(raw) >= 2 else 1
    return int.from_bytes(raw[:width], "little", signed=signed)


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


def _missing_characteristics(
    client: BleakClientWithServiceCache, required: frozenset[str]
) -> list[str]:
    """Return the required characteristics the client's service table lacks."""
    try:
        services = client.services
    except BleakError:
        # Service discovery never completed: nothing is resolvable.
        return sorted(required)
    return sorted(
        uuid for uuid in required if services.get_characteristic(uuid) is None
    )


def _is_characteristic_not_found(err: BaseException | None) -> bool:
    """Return whether bleak failed because the service table lacks the UUID.

    Matched on the text too: an ESPHome proxy reports it as a plain BleakError.
    """
    return isinstance(err, BleakCharacteristicNotFoundError) or (
        isinstance(err, BleakError) and "not found" in str(err).lower()
    )


class StorzBickelDevice:
    """The link to one Storz & Bickel device; a family subclass decodes it."""

    # Every device of the family has these. A link whose service table lacks
    # any of them resolved GATT only partly, which no amount of reconnecting
    # with the same cache fixes.
    required_characteristics: ClassVar[frozenset[str]] = frozenset()
    # Characteristics subscribed to on every new link.
    notify_characteristics: ClassVar[tuple[str, ...]] = ()
    # Polling cadence while the device is working, and while it is idle.
    active_interval: ClassVar[timedelta] = ACTIVE_INTERVAL
    idle_interval: ClassVar[timedelta] = IDLE_INTERVAL
    # Portable devices switch Bluetooth off when idle. For them "not visible"
    # is the normal state, not something to raise a repair about.
    sleeps: ClassVar[bool] = False
    # The highest target the device accepts, in degrees Celsius.
    max_temp: ClassVar[int] = MAX_TEMP

    def __init__(self, address: str, state: DeviceState) -> None:
        """Initialise the link for ``address``."""
        self.address = address
        self._ble_device: BLEDevice | None = None
        self._client: BleakClientWithServiceCache | None = None
        self._lock = asyncio.Lock()
        self._state = state
        self._callbacks: list[Callable[[Any], None]] = []
        self._static_info_read = False
        self._notifications_started = False
        self._last_read_error: str | None = None
        # How often the link has been torn down and rebuilt, and how many of
        # those also cleared the adapter's cache. Shown on the link-health
        # sensor so the self-healing can be seen working.
        self.link_resets = 0
        self.adapter_cache_resets = 0

    # -- what a family provides --------------------------------------------

    async def async_update(self) -> Any:
        """Refresh and return the device state."""
        raise NotImplementedError

    def _handle_notification(self, uuid: str, data: bytes) -> None:
        """Apply a notification from ``uuid``."""

    # Commands more than one family has. A family without one never offers
    # the entity that would call it.

    async def async_turn_heater_on(self) -> None:
        """Turn the heater on."""
        raise NotImplementedError

    async def async_turn_heater_off(self) -> None:
        """Turn the heater off."""
        raise NotImplementedError

    async def async_set_target_temperature(self, celsius: float) -> None:
        """Set the target temperature."""
        raise NotImplementedError

    async def async_turn_fan_on(self) -> None:
        """Turn the air pump on."""
        raise NotImplementedError

    async def async_turn_fan_off(self) -> None:
        """Turn the air pump off."""
        raise NotImplementedError

    async def async_set_brightness(self, level: int) -> None:
        """Set the display or LED brightness, on the family's own scale."""
        raise NotImplementedError

    async def async_set_vibration(self, enabled: bool) -> None:
        """Turn vibration on or off."""
        raise NotImplementedError

    async def async_set_display_fahrenheit(self, enabled: bool) -> None:
        """Show Fahrenheit or Celsius on the device."""
        raise NotImplementedError

    # -- plumbing ----------------------------------------------------------

    @property
    def state(self) -> Any:
        """Return the last known state, as the family's own dataclass."""
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
        device that is unplugged, which is both slow and pointless.
        """
        self._ble_device = ble_device

    def register_callback(self, callback: Callable[[Any], None]) -> Callable[[], None]:
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
        self._state.connected_via = None
        self._notify_listeners()

    # -- connection --------------------------------------------------------

    async def _ensure_client(self) -> BleakClientWithServiceCache:
        """Return a live client, connecting if needed. Assumes the lock is held."""
        if self._client is not None and self._client.is_connected:
            return self._client

        if (ble_device := self._ble_device) is None:
            raise DeviceConnectionError(
                f"{self.address} is not currently visible to any Bluetooth adapter",
                ConnectionFailure.NOT_VISIBLE,
            )

        _LOGGER.debug("%s: connecting via %s", self.address, ble_device)
        client = await self._async_establish(ble_device, use_services_cache=True)

        # A partly resolved service table is not fixed by reconnecting with the
        # same cache -- the 2026-10-06 outage survived a drop and reconnect and
        # only cleared on a reload. Rediscover once before giving up on it.
        required = self.required_characteristics
        if missing := _missing_characteristics(client, required):
            _LOGGER.debug(
                "%s: service table lacks %s, rediscovering", self.address, missing
            )
            await self._async_drop_client(client)
            client = await self._async_establish(ble_device, use_services_cache=False)
            if missing := _missing_characteristics(client, required):
                await self._async_drop_client(client)
                await clear_adapter_cache(self.address)
                raise DeviceConnectionError(
                    f"{self.address} connected but its service table lacks"
                    f" {', '.join(missing)}",
                    ConnectionFailure.STALE_GATT,
                )

        self._client = client
        self._state.connected = True
        self._state.connected_via = _ble_device_source(ble_device)

        try:
            for uuid in self.notify_characteristics:
                await client.start_notify(uuid, self._notification_callback(uuid))
            self._notifications_started = bool(self.notify_characteristics)
        except (BleakError, TimeoutError) as err:
            # Notifications are an optimisation where a family also polls; a
            # family that cannot work without them fails its first read.
            _LOGGER.debug("%s: could not subscribe: %s", self.address, err)

        return client

    def _notification_callback(self, uuid: str) -> Callable[[Any, bytearray], None]:
        """Return the bleak callback that routes ``uuid``'s notifications."""

        def _callback(_sender: Any, data: bytearray) -> None:
            self._handle_notification(uuid, bytes(data))

        return _callback

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
            raise DeviceConnectionError(
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
        """Connect to the device, raising DeviceConnectionError on failure."""
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
        self._state.connected_via = None
        self.link_resets += 1

        if client is not None:
            await self._async_drop_client(client)

        if reset_adapter_cache:
            self.adapter_cache_resets += 1
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
                for uuid in self.notify_characteristics:
                    try:
                        await client.stop_notify(uuid)
                    except (BleakError, TimeoutError, EOFError) as err:
                        _LOGGER.debug("%s: stop_notify failed: %s", self.address, err)
                self._notifications_started = False
            try:
                await client.disconnect()
            except (BleakError, TimeoutError, EOFError) as err:
                _LOGGER.debug("%s: disconnect failed: %s", self.address, err)
            self._state.connected = False
            self._state.connected_via = None

    # -- GATT primitives (lock must already be held) -----------------------

    async def _read(self, uuid: str) -> bytes | None:
        """Read a characteristic, returning None if it is unavailable."""
        client = await self._ensure_client()
        try:
            return bytes(await client.read_gatt_char(uuid))
        except (BleakError, TimeoutError) as err:
            # Kept so a link that answers nothing can report why: a stale
            # service table (bleak says the characteristic was not found) and
            # a device that has stopped responding (a timeout) look the same
            # otherwise.
            self._last_read_error = f"{uuid}: {err}"
            _LOGGER.debug("%s: read %s failed: %s", self.address, uuid, err)
            return None

    async def _write(self, uuid: str, data: bytes) -> None:
        """Write a characteristic, rebuilding a link that has lost it once.

        A required characteristic going missing means the service table is
        stale, not that the device lacks it, so the link is rebuilt and the
        write tried again rather than failing the button press. Optional ones
        really are absent on some firmware and fail straight through to their
        caller's fallback.
        """
        try:
            await self._write_once(uuid, data)
        except DeviceConnectionError as err:
            if uuid not in self.required_characteristics or not (
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
            raise DeviceConnectionError(
                f"Write to {uuid} failed: {err}", ConnectionFailure.WRITE_FAILED
            ) from err
