"""Tests for what the device layer added in 4.1: off confirmation and details."""

from __future__ import annotations

from unittest.mock import AsyncMock

from bleak.backends.device import BLEDevice

from custom_components.storzandbickel_ble.volcano import (
    CHAR_AUTO_OFF_SETTING,
    CHAR_FAN_OFF,
    CHAR_HEAT_OFF,
    CHAR_MAINS_VOLTAGE,
    CHAR_MODEL,
    CHAR_STATUS_REGISTER,
    MASK_FAN,
    MASK_HEATER,
    MASK_TARGET_REACHED,
    VolcanoHybrid,
    VolcanoState,
    _ble_device_source,
)

from .conftest import ADDRESS, FakeBleakClient, make_ble_device


def _device() -> VolcanoHybrid:
    """Return a client pointed at the test device."""
    device = VolcanoHybrid(ADDRESS)
    device.set_ble_device(make_ble_device())
    return device


async def test_an_off_that_does_not_take_is_sent_again(
    fake_client: FakeBleakClient, mock_establish_connection: AsyncMock
) -> None:
    """An off the device ignores is re-sent once."""
    fake_client.reads[CHAR_STATUS_REGISTER] = MASK_HEATER.to_bytes(2, "little")
    real_write = fake_client.write_gatt_char

    async def _drop_heat_off(uuid: str, data: bytes, response: bool = True) -> None:
        """Record a heater-off but leave the heater bit set."""
        if uuid == CHAR_HEAT_OFF:
            fake_client.writes.append((uuid, bytes(data)))
            return
        await real_write(uuid, data, response)

    fake_client.write_gatt_char = _drop_heat_off  # type: ignore[method-assign]

    await _device().async_turn_heater_off()

    assert [uuid for uuid, _ in fake_client.writes].count(CHAR_HEAT_OFF) == 2


async def test_an_off_that_took_is_not_repeated(
    fake_client: FakeBleakClient, mock_establish_connection: AsyncMock
) -> None:
    """Once the register shows the fan off, nothing more is written."""
    fake_client.reads[CHAR_STATUS_REGISTER] = MASK_FAN.to_bytes(2, "little")

    await _device().async_turn_fan_off()

    assert [uuid for uuid, _ in fake_client.writes] == [CHAR_FAN_OFF]


async def test_model_and_mains_are_read(
    fake_client: FakeBleakClient, mock_establish_connection: AsyncMock
) -> None:
    """The device's own model class and mains rating are read once per link."""
    fake_client.reads[CHAR_MODEL] = b"HYBRID"
    fake_client.reads[CHAR_MAINS_VOLTAGE] = b"230VAC"

    state = await _device().async_update()

    assert state.model == "HYBRID"
    assert state.mains_voltage == "230VAC"


async def test_the_link_records_its_adapter(
    fake_client: FakeBleakClient, mock_establish_connection: AsyncMock
) -> None:
    """The proxy a link goes through is shown, and forgotten when it drops."""
    device = VolcanoHybrid(ADDRESS)
    device.set_ble_device(BLEDevice(ADDRESS, "S&B VOLCANO H", {"source": "proxy-1"}))

    await device.async_connect()
    assert device.state.connected_via == "proxy-1"

    await device.async_disconnect()
    assert device.state.connected_via is None


def test_a_device_without_a_source_has_no_adapter() -> None:
    """Anything that is not Home Assistant's stack names no adapter."""
    assert _ble_device_source(BLEDevice(ADDRESS, None, None)) is None
    assert _ble_device_source(BLEDevice(ADDRESS, None, {"source": 7})) is None


def test_target_reached_reads_bit_10() -> None:
    """Unknown until register 1 is read, then the bit itself."""
    assert VolcanoState().target_reached is None
    assert VolcanoState(status1=MASK_TARGET_REACHED).target_reached is True
    assert VolcanoState(status1=MASK_HEATER).target_reached is False


async def test_an_auto_off_below_the_offered_floor_is_still_shown(
    fake_client: FakeBleakClient, mock_establish_connection: AsyncMock
) -> None:
    """Set to 5 minutes elsewhere, the device's own value is reported."""
    fake_client.reads[CHAR_AUTO_OFF_SETTING] = (300).to_bytes(2, "little")

    state = await _device().async_update()

    assert state.auto_off_minutes == 5
