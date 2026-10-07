"""Unit tests for the BLE decoding layer.

The captured wire values in ``conftest`` come from a real device; these tests
lock in the decoding bugs that were fixed in the rewrite.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from bleak.exc import BleakError
import pytest

from custom_components.volcano_hybrid.volcano import (
    ADAPTER_CACHE_RESET_AFTER,
    CHAR_AUTO_OFF_SETTING,
    CHAR_BRIGHTNESS,
    CHAR_CURRENT_TEMP,
    CHAR_FAN_ON,
    CHAR_HISTORY1,
    CHAR_HISTORY2,
    CHAR_REGISTER2,
    CHAR_REGISTER3,
    CHAR_SERIAL_NUMBER,
    CHAR_STATUS_REGISTER,
    CHAR_TARGET_TEMP,
    MASK_DISPLAY_COOLING_OFF,
    MASK_FAHRENHEIT,
    MASK_FAN,
    MASK_HEATER,
    MASK_HEATER_FAULT,
    MASK_HEATER_PUMP_FAULT,
    MASK_PUMP_INTERLOCK,
    MASK_R1_ERR,
    MASK_R2_ERR,
    MASK_SERVICE_MODE,
    MASK_VIBRATION_OFF,
    R2_ERROR_FLAGS,
    ConnectionFailure,
    VolcanoConnectionError,
    VolcanoHybrid,
    _decode_firmware,
    _decode_history,
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


async def test_losing_sight_of_the_device_stops_connection_attempts(
    mock_establish_connection: AsyncMock, fake_client: FakeBleakClient
) -> None:
    """A cleared BLEDevice fails immediately rather than working a retry ladder.

    Nothing can be reached through a BLEDevice no adapter or proxy can still
    hear, so attempting it just holds the GATT lock -- which every command also
    needs -- for the length of the connection ladder.
    """
    device = VolcanoHybrid(ADDRESS)
    device.set_ble_device(make_ble_device())
    await device.async_update()

    device._handle_disconnect(fake_client)
    device.set_ble_device(None)
    before = mock_establish_connection.call_count

    with pytest.raises(VolcanoConnectionError, match="not currently visible"):
        await device.async_update()

    assert mock_establish_connection.call_count == before


async def test_a_link_that_answers_nothing_is_treated_as_disconnected(
    mock_establish_connection: object, fake_client: FakeBleakClient
) -> None:
    """Every read failing means the link is dead, whatever bleak still thinks.

    A proxy can drop the device without firing the disconnect callback. Saying
    "connected" then stranded every entity on stale values, because only the
    coordinator's failure path recovers a link.
    """
    device = VolcanoHybrid(ADDRESS)
    device.set_ble_device(make_ble_device())
    await device.async_update()

    with (
        patch.object(fake_client, "read_gatt_char", side_effect=BleakError("gone")),
        pytest.raises(VolcanoConnectionError, match="answered no reads") as err,
    ):
        await device.async_update()

    assert device.state.connected is False
    assert err.value.kind is ConnectionFailure.NO_READS
    # The underlying bleak error rides along, so one WARNING is enough to tell a
    # stale service table from a device that has stopped answering. Losing it to
    # a debug line left a real outage unexplainable after the fact.
    assert "gone" in str(err.value)


async def test_a_link_that_answers_nothing_is_rebuilt_from_scratch(
    mock_establish_connection: AsyncMock, fake_client: FakeBleakClient
) -> None:
    """The dead client is thrown away, so the next poll can actually recover.

    It used to be left in place. While bleak still called it connected,
    ``_ensure_client`` handed the same dead client back on every later poll, so
    the integration could never recover on its own -- the config entry had to be
    reloaded by hand.
    """
    device = VolcanoHybrid(ADDRESS)
    device.set_ble_device(make_ble_device())
    await device.async_update()
    before = mock_establish_connection.call_count

    with (
        patch.object(fake_client, "read_gatt_char", side_effect=BleakError("gone")),
        pytest.raises(VolcanoConnectionError),
    ):
        await device.async_update()

    assert device._client is None
    assert fake_client.caches_cleared == 1

    # The next poll builds a fresh link rather than reusing the dead one.
    state = await device.async_update()
    assert mock_establish_connection.call_count == before + 1
    assert state.connected is True


async def test_a_persistently_dead_link_clears_the_adapter_cache(
    mock_establish_connection: AsyncMock, fake_client: FakeBleakClient
) -> None:
    """Reconnecting fixes most of these; a stale table on the adapter needs more."""
    device = VolcanoHybrid(ADDRESS)
    device.set_ble_device(make_ble_device())

    with (
        patch.object(fake_client, "read_gatt_char", side_effect=BleakError("gone")),
        patch(
            "custom_components.volcano_hybrid.volcano.clear_adapter_cache",
            AsyncMock(return_value=True),
        ) as clear_adapter,
    ):
        for _ in range(ADAPTER_CACHE_RESET_AFTER):
            with pytest.raises(VolcanoConnectionError):
                await device.async_update()

    # Only once the plain reconnect has been given its chances.
    assert clear_adapter.call_count == 1
    assert clear_adapter.await_args.args == (ADDRESS,)

    # A working poll puts it back to square one, so a later episode gets the
    # same escalation rather than clearing the adapter cache immediately.
    await device.async_update()
    assert device._no_read_failures == 0


async def test_static_device_information_is_read_once_per_connection(
    mock_establish_connection: object, fake_client: FakeBleakClient
) -> None:
    """Serial and firmware cannot change on a live link, so re-reading is waste."""
    device = VolcanoHybrid(ADDRESS)
    device.set_ble_device(make_ble_device())

    reads: list[str] = []
    original = fake_client.read_gatt_char

    async def _record(uuid: str) -> bytes:
        reads.append(uuid)
        return await original(uuid)

    with patch.object(fake_client, "read_gatt_char", _record):
        await device.async_update()
        assert reads.count(CHAR_SERIAL_NUMBER) == 1

        # Force the slow cycle to come round again on the same connection.
        device._device_info_read_at = None
        await device.async_update()
        assert reads.count(CHAR_SERIAL_NUMBER) == 1

        # A fresh link may be a different device, so it reads them again.
        device._handle_disconnect(fake_client)
        device._device_info_read_at = None
        await device.async_update()
        assert reads.count(CHAR_SERIAL_NUMBER) == 2


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
    assert state.status2 is None
    assert state.status5 is None
    assert state.last_fault is None
    assert state.vibration is None


async def test_connect_failure_is_wrapped(
    mock_establish_connection: AsyncMock, fake_client: FakeBleakClient
) -> None:
    """A bleak failure surfaces as VolcanoConnectionError, not a bleak type."""
    device = VolcanoHybrid(ADDRESS)
    device.set_ble_device(make_ble_device())

    mock_establish_connection.side_effect = BleakError("no route")
    with pytest.raises(VolcanoConnectionError, match="Could not connect"):
        await device.async_connect()


async def test_notifications_are_optional(
    mock_establish_connection: object, fake_client: FakeBleakClient
) -> None:
    """A device that refuses notify still connects; polling covers it."""
    device = VolcanoHybrid(ADDRESS)
    device.set_ble_device(make_ble_device())

    with patch.object(fake_client, "start_notify", side_effect=BleakError("nope")):
        await device.async_connect()

    assert device.connected is True
    assert device._notifications_started is False


async def test_disconnect_tolerates_a_failing_client(
    mock_establish_connection: object, fake_client: FakeBleakClient
) -> None:
    """Teardown never raises, even if the link is already gone."""
    device = VolcanoHybrid(ADDRESS)
    device.set_ble_device(make_ble_device())
    await device.async_connect()

    with (
        patch.object(fake_client, "stop_notify", side_effect=BleakError("gone")),
        patch.object(fake_client, "disconnect", side_effect=BleakError("gone")),
    ):
        await device.async_disconnect()

    assert device.state.connected is False


async def test_write_failure_is_wrapped(
    mock_establish_connection: object, fake_client: FakeBleakClient
) -> None:
    """A failed GATT write surfaces as VolcanoConnectionError."""
    device = VolcanoHybrid(ADDRESS)
    device.set_ble_device(make_ble_device())
    await device.async_update()

    with (
        patch.object(fake_client, "write_gatt_char", side_effect=BleakError("nope")),
        pytest.raises(VolcanoConnectionError, match="Write to"),
    ):
        await device.async_turn_heater_on()


async def test_notification_handler_notifies_listeners(
    mock_establish_connection: object, fake_client: FakeBleakClient
) -> None:
    """A status notification that changes state reaches the listener."""
    device = VolcanoHybrid(ADDRESS)
    device.set_ble_device(make_ble_device())
    await device.async_update()

    seen: list[bool] = []
    unregister = device.register_callback(lambda state: seen.append(state.heater_on))

    device._notification_handler(None, bytearray(RAW_STATUS_HEATING))
    assert seen == [True]

    # An identical repeat changes nothing, so it does not re-notify.
    device._notification_handler(None, bytearray(RAW_STATUS_HEATING))
    assert seen == [True]

    unregister()
    device._notification_handler(None, bytearray(b"\x00\x00"))
    assert seen == [True]


async def test_auto_off_falls_back_to_the_second_characteristic(
    mock_establish_connection: object, fake_client: FakeBleakClient
) -> None:
    """Firmware without the setting characteristic uses the other one."""
    device = VolcanoHybrid(ADDRESS)
    device.set_ble_device(make_ble_device())

    original = fake_client.write_gatt_char

    async def _fail_setting(uuid: str, data: bytes, response: bool = True) -> None:
        if uuid == CHAR_AUTO_OFF_SETTING:
            raise BleakError("not here")
        await original(uuid, data, response)

    with patch.object(fake_client, "write_gatt_char", _fail_setting):
        await device.async_set_auto_off_minutes(45)

    assert device.state.auto_off_minutes == 45


def test_spec_masks() -> None:
    """The masks match the published spec, and the ERR masks are their ORs."""
    assert (MASK_HEATER_FAULT, MASK_HEATER_PUMP_FAULT, MASK_PUMP_INTERLOCK) == (
        0x0008,
        0x0010,
        0x4000,
    )
    assert MASK_R1_ERR == (
        MASK_HEATER_FAULT | MASK_HEATER_PUMP_FAULT | MASK_PUMP_INTERLOCK
    )
    r2_err = 0
    for mask in R2_ERROR_FLAGS:
        r2_err |= mask
    assert r2_err == MASK_R2_ERR == 0x003B
    assert (MASK_SERVICE_MODE, MASK_FAHRENHEIT, MASK_DISPLAY_COOLING_OFF) == (
        0x0040,
        0x0200,
        0x1000,
    )
    assert MASK_VIBRATION_OFF == 0x0400


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0000000000000000", []),
        ("6100000000000000", [61]),
        ("6165530000000000", [61, 65, 53]),
        ("6100720000000000", [61, 72]),
        ("ABCD", None),
        ("123", None),
        ("", None),
        (None, None),
    ],
)
def test_decode_history(text: str | None, expected: list[int] | None) -> None:
    """Two-digit decimal fields, 00 is an empty slot, anything else is unknown."""
    assert _decode_history(text) == expected


async def test_history_reads_both_logs_newest_first(
    mock_establish_connection: object, fake_client: FakeBleakClient
) -> None:
    """History 1 comes before history 2, and 0 means an empty log."""
    device = VolcanoHybrid(ADDRESS)
    device.set_ble_device(make_ble_device())
    state = await device.async_update()
    assert state.fault_log == []
    assert state.last_fault == 0

    fake_client.reads[CHAR_HISTORY1] = b"4500000000000000"
    fake_client.reads[CHAR_HISTORY2] = b"6000000000000000"
    device._device_info_read_at = None
    state = await device.async_update()
    assert state.fault_log == [45, 60]
    assert state.last_fault == 45


async def test_register2_is_read_every_poll(
    mock_establish_connection: object, fake_client: FakeBleakClient
) -> None:
    """Service mode lives in register 2, so it is not left to the slow cycle."""
    device = VolcanoHybrid(ADDRESS)
    device.set_ble_device(make_ble_device())
    await device.async_update()
    assert device.state.service_mode is False

    fake_client.reads[CHAR_REGISTER2] = MASK_SERVICE_MODE.to_bytes(4, "little")
    await device.async_update()
    assert device.state.service_mode is True


async def test_a_new_fault_rereads_the_history(
    mock_establish_connection: object, fake_client: FakeBleakClient
) -> None:
    """A fault appearing between slow cycles refreshes the log straight away."""
    device = VolcanoHybrid(ADDRESS)
    device.set_ble_device(make_ble_device())
    await device.async_update()
    assert device.state.last_fault == 0

    fake_client.reads[CHAR_STATUS_REGISTER] = MASK_HEATER_PUMP_FAULT.to_bytes(
        4, "little"
    )
    fake_client.reads[CHAR_HISTORY1] = b"6100000000000000"
    await device.async_update()
    assert device.state.heater_pump_fault is True
    assert device.state.last_fault == 61

    # Still faulted: no further re-read until the slow cycle.
    fake_client.reads[CHAR_HISTORY1] = b"6861000000000000"
    await device.async_update()
    assert device.state.last_fault == 61


@pytest.mark.parametrize(
    ("method", "enabled", "uuid", "word"),
    [
        ("async_set_vibration", True, CHAR_REGISTER3, 0x00010400),
        ("async_set_vibration", False, CHAR_REGISTER3, 0x00000400),
        ("async_set_display_while_cooling", True, CHAR_REGISTER2, 0x00011000),
        ("async_set_display_while_cooling", False, CHAR_REGISTER2, 0x00001000),
        ("async_set_display_fahrenheit", True, CHAR_REGISTER2, 0x00000200),
        ("async_set_display_fahrenheit", False, CHAR_REGISTER2, 0x00010200),
    ],
)
async def test_setting_writes_the_set_clear_word(
    mock_establish_connection: object,
    fake_client: FakeBleakClient,
    method: str,
    enabled: bool,
    uuid: str,
    word: int,
) -> None:
    """Settings are a 4-byte set/clear word, and the state is read back."""
    device = VolcanoHybrid(ADDRESS)
    device.set_ble_device(make_ble_device())
    await device.async_update()

    await getattr(device, method)(enabled)

    assert fake_client.writes[-1] == (uuid, word.to_bytes(4, "little"))
    attribute = method.removeprefix("async_set_")
    assert getattr(device.state, attribute) is enabled


async def test_a_rejected_setting_is_not_reported_as_applied(
    mock_establish_connection: object, fake_client: FakeBleakClient
) -> None:
    """The read-back shows what the device latched, not what was asked."""
    device = VolcanoHybrid(ADDRESS)
    device.set_ble_device(make_ble_device())
    await device.async_update()
    fake_client.write_gatt_char = AsyncMock()  # accepts, changes nothing

    await device.async_set_display_fahrenheit(True)

    assert device.state.display_fahrenheit is False


async def test_status_register_1_is_never_written(
    mock_establish_connection: object, fake_client: FakeBleakClient
) -> None:
    """Register 1 rejects the set/clear convention and corrupts on a write."""
    device = VolcanoHybrid(ADDRESS)
    device.set_ble_device(make_ble_device())
    await device.async_update()

    async with device._lock:
        with pytest.raises(ValueError):
            await device._write_register_bits(CHAR_STATUS_REGISTER, MASK_HEATER, True)
    assert not fake_client.writes


async def test_a_partial_service_table_is_rediscovered_on_connect(
    mock_establish_connection: AsyncMock, fake_client: FakeBleakClient
) -> None:
    """A link missing the control service is rebuilt before anything uses it.

    The 2026-10-06 outage: status pushes arrived, but no temperature and every
    command failed "characteristic not found" until the entry was reloaded.
    """
    fake_client.missing = {CHAR_CURRENT_TEMP, CHAR_TARGET_TEMP, CHAR_FAN_ON}
    device = VolcanoHybrid(ADDRESS)
    device.set_ble_device(make_ble_device())

    state = await device.async_update()

    assert fake_client.caches_cleared == 1
    assert mock_establish_connection.call_count == 2
    assert (
        mock_establish_connection.await_args_list[0].kwargs["use_services_cache"]
        is True
    )
    assert (
        mock_establish_connection.await_args_list[1].kwargs["use_services_cache"]
        is False
    )
    assert state.current_temperature is None  # the idle sentinel, read fine
    assert state.target_temperature == 205.0
    await device.async_turn_fan_on()
    assert state.fan_on is True


async def test_a_service_table_that_stays_partial_is_reported(
    mock_establish_connection: AsyncMock, fake_client: FakeBleakClient
) -> None:
    """Rediscovery that does not help fails as STALE_GATT, not as contention."""
    fake_client.missing = {CHAR_FAN_ON}
    fake_client.missing_after_rediscovery = {CHAR_FAN_ON}
    device = VolcanoHybrid(ADDRESS)
    device.set_ble_device(make_ble_device())

    with (
        patch(
            "custom_components.volcano_hybrid.volcano.clear_adapter_cache",
            AsyncMock(return_value=True),
        ) as clear_adapter,
        pytest.raises(VolcanoConnectionError, match=CHAR_FAN_ON) as err,
    ):
        await device.async_update()

    assert err.value.kind is ConnectionFailure.STALE_GATT
    assert clear_adapter.await_count == 1
    assert device._client is None
    assert device.state.connected is False


async def test_a_command_rebuilds_a_link_that_lost_its_characteristic(
    mock_establish_connection: AsyncMock, fake_client: FakeBleakClient
) -> None:
    """The button press succeeds instead of erroring until a reload."""
    device = VolcanoHybrid(ADDRESS)
    device.set_ble_device(make_ble_device())
    await device.async_update()
    before = mock_establish_connection.call_count

    # The table goes stale under a live link; a rediscovery restores it.
    fake_client.missing = {CHAR_FAN_ON}
    with patch(
        "custom_components.volcano_hybrid.volcano.clear_adapter_cache",
        AsyncMock(return_value=True),
    ):
        await device.async_turn_fan_on()

    assert fake_client.caches_cleared == 1
    assert mock_establish_connection.call_count == before + 1
    assert (CHAR_FAN_ON, bytes([0])) in fake_client.writes
    assert device.state.fan_on is True


async def test_an_ordinary_write_failure_is_not_retried(
    mock_establish_connection: AsyncMock, fake_client: FakeBleakClient
) -> None:
    """Only a missing characteristic earns a rebuilt link."""
    device = VolcanoHybrid(ADDRESS)
    device.set_ble_device(make_ble_device())
    await device.async_update()

    with (
        patch.object(
            fake_client, "write_gatt_char", side_effect=BleakError("nope")
        ) as write,
        pytest.raises(VolcanoConnectionError),
    ):
        await device.async_turn_fan_on()

    assert write.await_count == 1
    assert fake_client.caches_cleared == 0


async def test_status_without_temperatures_is_a_stale_table(
    mock_establish_connection: AsyncMock, fake_client: FakeBleakClient
) -> None:
    """Status answering on its own no longer counts as a working link."""
    device = VolcanoHybrid(ADDRESS)
    device.set_ble_device(make_ble_device())
    await device.async_update()

    del fake_client.reads[CHAR_CURRENT_TEMP]
    del fake_client.reads[CHAR_TARGET_TEMP]
    with pytest.raises(VolcanoConnectionError, match="no temperature") as err:
        await device.async_update()

    assert err.value.kind is ConnectionFailure.STALE_GATT
    assert device._client is None
    assert device._no_read_failures == 1
    assert fake_client.caches_cleared == 1
