"""Tests for the Crafty protocol, decoded from the vendor app (no hardware)."""

from __future__ import annotations

import struct
from unittest.mock import AsyncMock

import pytest

from custom_components.storzandbickel_ble.ble_device import (
    ConnectionFailure,
    DeviceConnectionError,
)
from custom_components.storzandbickel_ble.crafty import (
    CHAR_AUTO_OFF_COUNTDOWN,
    CHAR_BATTERY,
    CHAR_BOOST,
    CHAR_CURRENT_TEMP,
    CHAR_FIRMWARE,
    CHAR_HEAT_OFF,
    CHAR_HEAT_ON,
    CHAR_HOURS,
    CHAR_LED_BRIGHTNESS,
    CHAR_MINUTES,
    CHAR_SERIAL_NUMBER,
    CHAR_STATUS1,
    CHAR_STATUS2,
    CHAR_TARGET_TEMP,
    MASK_AUTO_BLE_SHUTDOWN,
    MASK_BOOST,
    MASK_CHARGE_LED_OFF,
    MASK_ERROR,
    MASK_FIND,
    MASK_HEATER,
    MASK_NEEDS_FACTORY_RESET,
    MASK_SUPERBOOST,
    MASK_TARGET_REACHED,
    MASK_VIBRATION_OFF,
    CraftyDevice,
    CraftyState,
)

from .conftest import ADDRESS, FakeBleakClient, make_ble_device


def _u16(value: int) -> bytes:
    return struct.pack("<H", value)


CRAFTY_READS: dict[str, bytes] = {
    CHAR_CURRENT_TEMP: _u16(1795),
    CHAR_TARGET_TEMP: _u16(1850),
    CHAR_BOOST: _u16(100),
    CHAR_BATTERY: _u16(76),
    CHAR_LED_BRIGHTNESS: _u16(60),
    CHAR_AUTO_OFF_COUNTDOWN: _u16(95),
    CHAR_STATUS1: _u16(MASK_HEATER),
    CHAR_STATUS2: _u16(MASK_TARGET_REACHED),
    CHAR_SERIAL_NUMBER: b"CY12345678XYZ",
    CHAR_FIRMWARE: b"V02.51",
    CHAR_HOURS: _u16(42),
    CHAR_MINUTES: _u16(17),
}


class FakeCraftyClient(FakeBleakClient):
    """A Crafty: writes are stored as the new value, like the device does."""

    def __init__(self) -> None:
        """Start from a Crafty on current firmware, heating and at target."""
        super().__init__(reads=CRAFTY_READS)

    async def write_gatt_char(
        self, uuid: str, data: bytes, response: bool = True
    ) -> None:
        """Record the write and reflect it back."""
        self.writes.append((uuid, bytes(data)))
        if uuid not in (CHAR_HEAT_ON, CHAR_HEAT_OFF):
            self.reads[uuid] = bytes(data)


@pytest.fixture
def fake_client() -> FakeCraftyClient:
    """Hand the connection mock a Crafty."""
    return FakeCraftyClient()


def _device() -> CraftyDevice:
    device = CraftyDevice(ADDRESS)
    device.set_ble_device(make_ble_device())
    return device


async def test_a_poll_decodes_everything(
    fake_client: FakeCraftyClient, mock_establish_connection: AsyncMock
) -> None:
    """Every value lands where it should, in degrees and percent."""
    state = await _device().async_update()

    assert state.current_temperature == 179.5
    assert state.target_temperature == 185.0
    assert state.boost_offset == 10.0
    assert state.battery == 76
    assert state.led_brightness == 60
    assert state.auto_off_countdown == 95
    assert state.heater_on is True
    assert state.target_reached is True
    # The app keeps the first eight characters of the serial.
    assert state.serial_number == "CY123456"
    assert state.firmware_version == "V02.51"
    assert state.model == "Crafty"
    assert state.hours_of_operation == 42
    assert state.minutes_of_operation == 17
    assert state.controllable is True
    assert state.is_plus is False


async def test_a_fahrenheit_target_is_converted(
    fake_client: FakeCraftyClient, mock_establish_connection: AsyncMock
) -> None:
    """A Crafty showing Fahrenheit reports its target in tenths of a degree F."""
    fake_client.reads[CHAR_TARGET_TEMP] = _u16(3650)  # 365.0 F

    state = await _device().async_update()

    assert state.target_temperature == 185.0


async def test_a_crafty_plus_is_recognised(
    fake_client: FakeCraftyClient, mock_establish_connection: AsyncMock
) -> None:
    """Firmware major 3 and up is the Crafty+."""
    fake_client.reads[CHAR_FIRMWARE] = b"V03.10"

    state = await _device().async_update()

    assert state.is_plus is True
    assert state.model == "Crafty+"


async def test_old_firmware_cannot_be_controlled(
    fake_client: FakeCraftyClient, mock_establish_connection: AsyncMock
) -> None:
    """Below V02.51 the heater and settings are out of reach over Bluetooth."""
    fake_client.reads[CHAR_FIRMWARE] = b"V02.40"
    device = _device()
    await device.async_update()

    assert device.state.controllable is False
    with pytest.raises(DeviceConnectionError) as err:
        await device.async_turn_heater_on()
    assert err.value.kind is ConnectionFailure.WRITE_FAILED
    assert fake_client.writes == []


async def test_a_link_that_answers_nothing_is_rebuilt(
    fake_client: FakeCraftyClient, mock_establish_connection: AsyncMock
) -> None:
    """The same guard as the Volcano: no temperatures means a dead link."""
    fake_client.reads.clear()
    device = _device()

    with pytest.raises(DeviceConnectionError) as err:
        await device.async_update()

    assert err.value.kind is ConnectionFailure.NO_READS
    assert device.link_resets == 1


async def test_heater_commands(
    fake_client: FakeCraftyClient, mock_establish_connection: AsyncMock
) -> None:
    """On and off are two zero bytes to their own characteristics."""
    device = _device()
    await device.async_update()

    await device.async_turn_heater_off()
    assert device.state.heater_on is False
    await device.async_turn_heater_on()
    assert device.state.heater_on is True

    assert fake_client.writes == [
        (CHAR_HEAT_OFF, b"\x00\x00"),
        (CHAR_HEAT_ON, b"\x00\x00"),
    ]


async def test_setting_the_target_rewrites_the_boost(
    fake_client: FakeCraftyClient, mock_establish_connection: AsyncMock
) -> None:
    """The app always follows a target write with the current boost."""
    device = _device()
    await device.async_update()

    await device.async_set_target_temperature(250)  # clamped to 210

    assert fake_client.writes == [
        (CHAR_TARGET_TEMP, _u16(2100)),
        (CHAR_BOOST, _u16(100)),
    ]
    assert device.state.target_temperature == 210.0


async def test_setting_the_target_without_a_known_boost(
    fake_client: FakeCraftyClient, mock_establish_connection: AsyncMock
) -> None:
    """Nothing to preserve, so only the target is written."""
    device = _device()

    await device.async_set_target_temperature(190)

    assert fake_client.writes == [(CHAR_TARGET_TEMP, _u16(1900))]


async def test_the_boost_stays_inside_the_range(
    fake_client: FakeCraftyClient, mock_establish_connection: AsyncMock
) -> None:
    """Target plus boost may not pass 210, and boost is at least 1."""
    device = _device()
    await device.async_update()  # target 185

    await device.async_set_boost(40)
    assert fake_client.writes[-1] == (CHAR_BOOST, _u16(250))
    await device.async_set_boost(0)
    assert fake_client.writes[-1] == (CHAR_BOOST, _u16(10))


async def test_led_brightness(
    fake_client: FakeCraftyClient, mock_establish_connection: AsyncMock
) -> None:
    """Written as a plain percentage, clamped."""
    device = _device()

    await device.async_set_led_brightness(130)

    assert fake_client.writes == [(CHAR_LED_BRIGHTNESS, _u16(100))]
    assert device.state.led_brightness == 100


@pytest.mark.parametrize(
    ("method", "value", "expected"),
    [
        # Inverted bits: "on" clears them.
        ("async_set_vibration", False, MASK_TARGET_REACHED | MASK_VIBRATION_OFF),
        ("async_set_vibration", True, MASK_TARGET_REACHED),
        ("async_set_charge_led", False, MASK_TARGET_REACHED | MASK_CHARGE_LED_OFF),
        (
            "async_set_auto_ble_shutdown",
            True,
            MASK_TARGET_REACHED | MASK_AUTO_BLE_SHUTDOWN,
        ),
    ],
)
async def test_settings_are_a_whole_word_read_modify_write(
    fake_client: FakeCraftyClient,
    mock_establish_connection: AsyncMock,
    method: str,
    value: bool,
    expected: int,
) -> None:
    """Register 2 is read, changed and written back whole, then re-read."""
    device = _device()
    await device.async_update()

    await getattr(device, method)(value)

    assert fake_client.writes == [(CHAR_STATUS2, _u16(expected))]
    assert device.state.status2 == expected


async def test_find_sets_its_bit(
    fake_client: FakeCraftyClient, mock_establish_connection: AsyncMock
) -> None:
    """Find is the same masked write, with bit 3."""
    device = _device()
    await device.async_update()

    await device.async_find()

    assert fake_client.writes == [(CHAR_STATUS2, _u16(MASK_TARGET_REACHED | MASK_FIND))]


async def test_an_unreadable_settings_register_is_not_written(
    fake_client: FakeCraftyClient, mock_establish_connection: AsyncMock
) -> None:
    """Writing a guessed word would clobber the other settings."""
    device = _device()
    await device.async_update()
    del fake_client.reads[CHAR_STATUS2]

    with pytest.raises(DeviceConnectionError):
        await device.async_set_vibration(True)
    assert fake_client.writes == []


async def test_notifications_update_and_tell_listeners(
    fake_client: FakeCraftyClient, mock_establish_connection: AsyncMock
) -> None:
    """Pushed values apply straight away."""
    device = _device()
    await device.async_update()
    pushed: list[CraftyState] = []
    device.register_callback(pushed.append)

    fake_client.notify_callbacks[CHAR_STATUS1](None, bytearray(_u16(0)))
    fake_client.notify_callbacks[CHAR_BATTERY](None, bytearray(_u16(50)))

    assert device.state.heater_on is False
    assert device.state.battery == 50
    assert len(pushed) == 2


def test_state_flags() -> None:
    """Each documented bit reads through; unknown registers stay unknown."""
    empty = CraftyState()
    assert empty.target_reached is None
    assert empty.fault_known is False
    assert empty.any_fault is False
    assert empty.vibration is None
    assert empty.charge_led is None
    assert empty.boost is None
    assert empty.controllable is False
    assert empty.is_plus is False

    busy = CraftyState(
        status1=MASK_BOOST | MASK_SUPERBOOST | MASK_ERROR | MASK_NEEDS_FACTORY_RESET,
        status2=MASK_VIBRATION_OFF | MASK_AUTO_BLE_SHUTDOWN,
    )
    assert busy.fault_known is True
    assert busy.any_fault is True
    assert busy.boost is True
    assert busy.superboost is True
    assert busy.needs_factory_reset is True
    assert busy.vibration is False
    assert busy.charge_led is True
    assert busy.auto_ble_shutdown is True
