"""Tests for the Venty / Veazy protocol, decoded from the vendor app (no hardware)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from custom_components.storzandbickel_ble.ble_device import (
    ConnectionFailure,
    DeviceConnectionError,
)
from custom_components.storzandbickel_ble.qvap import (
    CHAR_CONTROL,
    CMD_DISPLAY,
    CMD_FIND,
    CMD_FIRMWARE,
    CMD_SERIAL,
    CMD_STATUS,
    CMD_USAGE,
    SETTING2_PERMANENT_BLUETOOTH,
    SETTING_BOOST_VISUALISATION,
    SETTING_CHARGE_LIMIT,
    SETTING_CHARGE_OPTIMISATION,
    SETTING_FAHRENHEIT,
    SETTING_TARGET_CHANGED,
    SETTING_TARGET_REACHED,
    QvapState,
    VeazyDevice,
    VentyDevice,
)

from .conftest import ADDRESS, FakeBleakClient, make_ble_device


class FakeQvapClient(FakeBleakClient):
    """A Venty: answers every framed command through the notify callback."""

    def __init__(self, *, bootloader: bool = False, colour: int = 3) -> None:
        """Start idle at 180 C with 185 C set."""
        super().__init__(reads={})
        status = bytearray(20)
        status[0] = CMD_STATUS
        status[2:4] = (1800).to_bytes(2, "little")
        status[4:6] = (1850).to_bytes(2, "little")
        status[6], status[7], status[8] = 10, 15, 80
        status[9:11] = (95).to_bytes(2, "little")
        self.status = status
        flags = 0x00 if bootloader else 0x01
        self.firmware = bytes([CMD_FIRMWARE, flags]) + b"V01.09" + bytes(11)
        self.serial = (
            bytes([CMD_SERIAL]) + bytes(8) + b"123456" + b"VY" + bytes([0, colour])
        )
        self.display = bytearray([CMD_DISPLAY, 0, 5, 0, 0, 1, 0])
        self.usage = (
            bytes([CMD_USAGE])
            + (120).to_bytes(3, "little")
            + (300).to_bytes(3, "little")
            + bytes(13)
        )
        self.silent = False

    async def write_gatt_char(
        self, uuid: str, data: bytes, response: bool = True
    ) -> None:
        """Apply a write the way the device would, then answer it."""
        frame = bytes(data)
        self.writes.append((uuid, frame))
        if self.silent:
            return
        command, mask = frame[0], frame[1]
        if command == CMD_STATUS:
            if mask & 0x02:
                self.status[4:6] = frame[4:6]
            if mask & 0x04:
                self.status[6] = frame[6]
            if mask & 0x08:
                self.status[7] = frame[7]
            if mask & 0x20:
                self.status[11] = frame[11]
            if mask & 0x80:
                for value, which in ((14, 15), (16, 17)):
                    bits = frame[which]
                    self.status[value] = (self.status[value] & ~bits) | (
                        frame[value] & bits
                    )
            reply = bytes(self.status)
        elif command == CMD_DISPLAY:
            for bit, index in ((0x01, 2), (0x08, 5), (0x10, 6)):
                if mask & bit:
                    self.display[index] = frame[index]
            reply = bytes(self.display)
        else:
            reply = {
                CMD_FIRMWARE: self.firmware,
                CMD_SERIAL: self.serial,
                CMD_USAGE: self.usage,
            }.get(command, bytes([command]))
        self.notify_callbacks[CHAR_CONTROL](None, bytearray(reply))


@pytest.fixture
def fake_client() -> FakeQvapClient:
    """Hand the connection mock a Venty."""
    return FakeQvapClient()


def _venty() -> VentyDevice:
    device = VentyDevice(ADDRESS)
    device.set_ble_device(make_ble_device())
    return device


def _sent(fake_client: FakeQvapClient) -> list[bytes]:
    return [frame for _uuid, frame in fake_client.writes]


async def test_a_poll_decodes_the_status_and_identity(
    fake_client: FakeQvapClient, mock_establish_connection: AsyncMock
) -> None:
    """The first poll reads identity once, then status and usage."""
    state = await _venty().async_update()

    assert state.current_temperature == 180.0
    assert state.target_temperature == 185.0
    assert state.boost_offset == 10
    assert state.superboost_offset == 15
    assert state.battery == 80
    assert state.auto_off_countdown == 95
    assert state.heater_mode == "off"
    assert state.heater_on is False
    assert state.charging is False
    assert state.firmware_version == "V01.09"
    assert state.bootloader is False
    assert state.serial_number == "VY123456"
    assert state.brightness == 5
    assert state.vibration is True
    assert state.heater_minutes == 120
    assert state.charge_minutes == 300
    assert state.model == "Venty"
    assert state.colour is None  # only the Veazy reports it
    assert [frame[0] for frame in _sent(fake_client)] == [
        CMD_FIRMWARE,
        CMD_SERIAL,
        CMD_DISPLAY,
        CMD_STATUS,
        CMD_USAGE,
    ]


async def test_later_polls_only_ask_for_status(
    fake_client: FakeQvapClient, mock_establish_connection: AsyncMock
) -> None:
    """Identity is per link and usage is slow-changing."""
    device = _venty()
    await device.async_update()
    fake_client.writes.clear()

    await device.async_update()

    assert [frame[0] for frame in _sent(fake_client)] == [CMD_STATUS]


async def test_a_silent_device_is_a_dead_link(
    fake_client: FakeQvapClient, mock_establish_connection: AsyncMock
) -> None:
    """No reply is the same failure as a Volcano answering no reads."""
    fake_client.silent = True
    device = _venty()

    with (
        patch("custom_components.storzandbickel_ble.qvap.REPLY_TIMEOUT", 0.01),
        pytest.raises(DeviceConnectionError) as err,
    ):
        await device.async_update()

    assert err.value.kind is ConnectionFailure.NO_READS
    assert device.link_resets == 1


async def test_heater_and_target_writes(
    fake_client: FakeQvapClient, mock_establish_connection: AsyncMock
) -> None:
    """Each write sets only its own mask bit and field, and is applied."""
    device = _venty()
    await device.async_update()
    fake_client.writes.clear()

    await device.async_turn_heater_on()
    assert device.state.heater_on is True
    assert device.state.heater_mode == "heating"
    await device.async_set_target_temperature(250)  # clamped to 210
    assert device.state.target_temperature == 210.0
    await device.async_set_boost(120)
    await device.async_set_superboost(0)
    await device.async_turn_heater_off()

    frames = _sent(fake_client)
    assert (frames[0][1], frames[0][11]) == (0x20, 1)
    assert (frames[1][1], frames[1][4:6]) == (0x02, (2100).to_bytes(2, "little"))
    assert (frames[2][1], frames[2][6]) == (0x04, 99)
    assert (frames[3][1], frames[3][7]) == (0x08, 1)
    assert (frames[4][1], frames[4][11]) == (0x20, 0)
    assert device.state.heater_on is False


@pytest.mark.parametrize(
    ("method", "enabled", "byte", "bit"),
    [
        ("async_set_display_fahrenheit", True, 14, SETTING_FAHRENHEIT),
        ("async_set_charge_optimisation", True, 14, SETTING_CHARGE_OPTIMISATION),
        ("async_set_charge_limit", True, 14, SETTING_CHARGE_LIMIT),
        ("async_set_boost_visualisation", True, 14, SETTING_BOOST_VISUALISATION),
        ("async_set_permanent_bluetooth", True, 16, SETTING2_PERMANENT_BLUETOOTH),
    ],
)
async def test_settings_are_masked_writes(
    fake_client: FakeQvapClient,
    mock_establish_connection: AsyncMock,
    method: str,
    enabled: bool,
    byte: int,
    bit: int,
) -> None:
    """A setting names the one bit it changes; the others are left alone."""
    device = _venty()
    await device.async_update()
    fake_client.writes.clear()

    await getattr(device, method)(enabled)

    (frame,) = _sent(fake_client)
    assert frame[1] == 0x80
    assert (frame[byte], frame[byte + 1]) == (bit, bit)


async def test_settings_read_back(
    fake_client: FakeQvapClient, mock_establish_connection: AsyncMock
) -> None:
    """The flags decode from the status the device answers with."""
    fake_client.status[14] = (
        SETTING_FAHRENHEIT
        | SETTING_TARGET_REACHED
        | SETTING_TARGET_CHANGED
        | SETTING_BOOST_VISUALISATION
    )
    fake_client.status[16] = SETTING2_PERMANENT_BLUETOOTH
    fake_client.status[13] = 1
    fake_client.status[11] = 3

    state = await _venty().async_update()

    assert state.display_fahrenheit is True
    assert state.target_reached is True
    assert state.target_changed_on_device is True
    assert state.boost_visualisation is True
    assert state.charge_optimisation is False
    assert state.charge_limit is False
    assert state.permanent_bluetooth is True
    assert state.charging is True
    assert state.heater_mode == "superboost"


async def test_the_veazy_inverts_boost_display_and_has_a_colour(
    mock_establish_connection: AsyncMock, fake_client: FakeQvapClient
) -> None:
    """Same protocol, one inverted bit, and a colour."""
    fake_client.status[14] = SETTING_BOOST_VISUALISATION
    device = VeazyDevice(ADDRESS)
    device.set_ble_device(make_ble_device())

    state = await device.async_update()
    assert state.boost_visualisation is False
    assert state.colour == "pink"
    assert state.model == "Veazy"

    fake_client.writes.clear()
    await device.async_set_boost_visualisation(True)
    (frame,) = _sent(fake_client)
    assert (frame[14], frame[15]) == (0, SETTING_BOOST_VISUALISATION)


async def test_display_settings_use_their_own_frame(
    fake_client: FakeQvapClient, mock_establish_connection: AsyncMock
) -> None:
    """Brightness, vibration and boost timeout go through command 0x06."""
    device = _venty()
    await device.async_update()
    fake_client.writes.clear()

    await device.async_set_brightness(12)  # clamped to 9
    await device.async_set_vibration(False)
    await device.async_set_boost_timeout_disabled(True)

    frames = _sent(fake_client)
    assert [(frame[0], frame[1]) for frame in frames] == [
        (CMD_DISPLAY, 0x01),
        (CMD_DISPLAY, 0x08),
        (CMD_DISPLAY, 0x10),
    ]
    assert all(len(frame) == 7 for frame in frames)
    assert device.state.brightness == 9
    assert device.state.vibration is False
    assert device.state.boost_timeout_disabled is True


async def test_find(
    fake_client: FakeQvapClient, mock_establish_connection: AsyncMock
) -> None:
    """Find is command 0x0D with byte 1 set."""
    device = _venty()
    await device.async_update()
    fake_client.writes.clear()

    await device.async_find()

    (frame,) = _sent(fake_client)
    assert (frame[0], frame[1]) == (CMD_FIND, 0x01)


async def test_a_device_in_its_bootloader_is_not_controlled(
    mock_establish_connection: AsyncMock, fake_client: FakeQvapClient
) -> None:
    """Heating commands mean nothing there; refuse instead of sending them."""
    fake_client.firmware = bytes([CMD_FIRMWARE, 0x00]) + b"V01.09" + bytes(11)
    device = _venty()
    await device.async_update()
    fake_client.writes.clear()

    with pytest.raises(DeviceConnectionError):
        await device.async_turn_heater_on()

    assert device.state.bootloader is True
    assert fake_client.writes == []


def test_short_and_stray_frames_are_ignored() -> None:
    """A truncated status changes nothing; an empty notify is dropped."""
    device = _venty()
    assert device._apply_status(bytes([CMD_STATUS, 0, 1])) is False
    device._handle_notification(CHAR_CONTROL, b"")
    device._apply_firmware(bytes([CMD_FIRMWARE]))
    device._apply_serial(bytes([CMD_SERIAL]))
    device._apply_display(bytes([CMD_DISPLAY]))
    device._apply_usage(bytes([CMD_USAGE]))
    assert device.state.firmware_version is None


def test_an_unknown_heater_mode_is_not_guessed() -> None:
    """A mode byte outside the four known ones reads as unknown, but on."""
    device = _venty()
    frame = bytearray(20)
    frame[0], frame[11] = CMD_STATUS, 7
    device._apply_status(bytes(frame))
    assert device.state.heater_mode is None
    assert device.state.heater_on is True


def test_empty_state_flags() -> None:
    """Nothing read yet is unknown, not off."""
    state = QvapState()
    assert state.target_reached is None
    assert state.boost_visualisation is None
    assert state.permanent_bluetooth is None
