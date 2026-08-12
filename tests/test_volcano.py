"""Unit tests for the BLE decoding layer.

The captured wire values in ``conftest`` come from a real device; these tests
lock in the decoding bugs that were fixed in the rewrite.
"""

from __future__ import annotations

import pytest

from custom_components.volcano_hybrid.volcano import (
    CHAR_AUTO_OFF_SETTING,
    CHAR_BRIGHTNESS,
    CHAR_CURRENT_TEMP,
    CHAR_STATUS_REGISTER,
    CHAR_TARGET_TEMP,
    MASK_FAN,
    MASK_HEATER,
    VolcanoConnectionError,
    VolcanoHybrid,
    _decode_firmware,
    _decode_int,
    _decode_string,
    _decode_temperature,
)

from .conftest import (
    ADDRESS,
    DEFAULT_READS,
    RAW_CURRENT_TEMP_HOT,
    RAW_CURRENT_TEMP_IDLE,
    RAW_STATUS_FAN_AND_HEAT,
    RAW_STATUS_HEATING,
    FakeBleakClient,
    make_ble_device,
)


def test_current_temperature_is_signed() -> None:
    """The idle sentinel decodes to -18.0 C, not 6535.6 C.

    Reading these four bytes as an unsigned 16-bit value is what produced the
    "Received unreasonable temperature reading: 6536C" log flood.
    """
    assert _decode_temperature(RAW_CURRENT_TEMP_IDLE) == -18.0
    assert _decode_temperature(RAW_CURRENT_TEMP_HOT) == 205.0


@pytest.mark.parametrize(
    ("raw", "celsius", "fahrenheit"),
    [
        ("4cffffff", -18.0, -0.4),  # idle, probe has no reading
        ("70030000", 88.0, 190.4),  # cooling down after a session
        ("3a070000", 185.0, 365.0),  # target temperature
    ],
)
def test_captured_wire_values(raw: str, celsius: float, fahrenheit: float) -> None:
    """Decode bytes captured off a real device against what it displayed.

    The 88.0 C and 185.0 C samples were read from a Volcano Hybrid at the same
    moment its Home Assistant card showed 190.4 F and 365 F, so these pin the
    decoder to observed hardware behaviour rather than to our own assumptions.
    """
    decoded = _decode_temperature(bytes.fromhex(raw))
    assert decoded == celsius
    assert round(decoded * 9 / 5 + 32, 1) == fahrenheit


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (b"", None),
        (None, None),
        (bytes.fromhex("0208"), 205.0),
        (bytes.fromhex("02080000"), 205.0),
        (bytes.fromhex("4cff"), -18.0),
    ],
)
def test_decode_temperature_widths(raw: bytes | None, expected: float | None) -> None:
    """Temperatures decode from either width the device may send."""
    assert _decode_temperature(raw) == expected


def test_decode_int_and_strings() -> None:
    """Integers, strings and firmware versions decode as expected."""
    assert _decode_int((1200).to_bytes(2, "little")) == 1200
    assert _decode_int(None) is None
    assert _decode_string(b"VH38NHG700\x00\x00") == "VH38NHG700"
    assert _decode_string(b"") is None
    assert _decode_firmware(b"V01.03.00.00") == "V01.03.00.00"
    assert _decode_firmware(bytes([1, 3, 0])) == "V01.03.0"
    assert _decode_firmware(b"\x01") == "V01"
    assert _decode_firmware(None) is None


async def test_status_register_never_yields_a_temperature() -> None:
    """The status register sets heater/fan only.

    0x2823 has the fan bit set, which puts 0x28 (= 40) in the high byte. The
    old code read that as a 40 C temperature.
    """
    device = VolcanoHybrid(ADDRESS)
    device._state.current_temperature = 205.0

    assert device._apply_status_register(RAW_STATUS_FAN_AND_HEAT) is True
    assert device.state.heater_on is True
    assert device.state.fan_on is True
    assert device.state.current_temperature == 205.0

    assert device._apply_status_register(RAW_STATUS_HEATING) is True
    assert device.state.heater_on is True
    assert device.state.fan_on is False
    assert device.state.current_temperature == 205.0


def test_status_masks() -> None:
    """The documented bit masks match the captured register values."""
    heating = int.from_bytes(RAW_STATUS_HEATING[:2], "little")
    assert heating & MASK_HEATER
    assert not heating & MASK_FAN

    both = int.from_bytes(RAW_STATUS_FAN_AND_HEAT[:2], "little")
    assert both & MASK_HEATER
    assert both & MASK_FAN


async def test_update_maps_the_idle_sentinel_to_unknown(
    mock_establish_connection: object, fake_client: FakeBleakClient
) -> None:
    """An idle device reports no temperature rather than -18 C."""
    device = VolcanoHybrid(ADDRESS)
    device.set_ble_device(make_ble_device())

    state = await device.async_update()

    assert state.current_temperature is None
    assert state.target_temperature == 205.0
    assert state.brightness == 70
    assert state.serial_number == "VH38NHG700"
    assert state.firmware_version == "V01.03.00.00"
    assert state.hours_of_operation == 2721
    assert state.auto_off_minutes == 20
    assert state.raw["current_temperature"] == "4cffffff"


async def test_update_reports_a_real_temperature(
    mock_establish_connection: object, fake_client: FakeBleakClient
) -> None:
    """A genuine reading passes straight through."""
    fake_client.reads[CHAR_CURRENT_TEMP] = RAW_CURRENT_TEMP_HOT
    device = VolcanoHybrid(ADDRESS)
    device.set_ble_device(make_ble_device())

    state = await device.async_update()

    assert state.current_temperature == 205.0


async def test_commands_do_not_deadlock(
    mock_establish_connection: object, fake_client: FakeBleakClient
) -> None:
    """Every public coroutine can run back to back on one lock.

    The previous implementation held the connection lock while reconnecting,
    which wedged the command path permanently.
    """
    device = VolcanoHybrid(ADDRESS)
    device.set_ble_device(make_ble_device())

    await device.async_update()
    await device.async_turn_heater_on()
    await device.async_turn_fan_on()
    await device.async_set_target_temperature(190)
    await device.async_set_brightness(50)
    await device.async_set_auto_off_minutes(30)
    await device.async_turn_fan_off()
    await device.async_turn_heater_off()
    await device.async_update()

    assert device.state.target_temperature == 190
    assert device.state.brightness == 50
    assert device.state.auto_off_minutes == 30

    written = dict(fake_client.writes)
    assert written[CHAR_TARGET_TEMP] == (1900).to_bytes(4, "little")
    assert written[CHAR_BRIGHTNESS] == (50).to_bytes(2, "little")
    assert written[CHAR_AUTO_OFF_SETTING] == (1800).to_bytes(2, "little")


async def test_optimistic_window_survives_a_stale_notification(
    mock_establish_connection: object, fake_client: FakeBleakClient
) -> None:
    """A status register that has not caught up does not undo a command."""
    device = VolcanoHybrid(ADDRESS)
    device.set_ble_device(make_ble_device())
    await device.async_update()

    await device.async_turn_heater_on()
    assert device.state.heater_on is True

    # The device is still reporting "off" a beat later.
    device._apply_status_register(b"\x00\x00")
    assert device.state.heater_on is True


async def test_connect_without_a_ble_device_raises() -> None:
    """Connecting with no advertisement in hand is an explicit error."""
    device = VolcanoHybrid(ADDRESS)
    with pytest.raises(VolcanoConnectionError):
        await device.async_connect()


async def test_disconnect_is_safe_when_never_connected() -> None:
    """Disconnecting an unconnected device is a no-op."""
    device = VolcanoHybrid(ADDRESS)
    await device.async_disconnect()
    assert device.connected is False


async def test_missing_characteristics_are_tolerated(
    mock_establish_connection: object, fake_client: FakeBleakClient
) -> None:
    """A firmware without the optional registers still produces a state."""
    required = {CHAR_CURRENT_TEMP, CHAR_TARGET_TEMP, CHAR_STATUS_REGISTER}
    fake_client.reads = {
        uuid: value for uuid, value in DEFAULT_READS.items() if uuid in required
    }

    device = VolcanoHybrid(ADDRESS)
    device.set_ble_device(make_ble_device())
    state = await device.async_update()

    assert state.connected is True
    assert state.serial_number is None
    assert state.register2 is None
