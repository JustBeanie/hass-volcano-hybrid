"""Tests for setup, unload and config entry migration."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import AsyncMock, patch

from homeassistant.components.bluetooth import BluetoothChange
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.storzandbickel_ble.ble_device import (
    ConnectionFailure,
    DeviceConnectionError,
)
from custom_components.storzandbickel_ble.const import (
    CONF_FAN_ON_CONNECT,
    CONF_INITIAL_TEMP,
    DOMAIN,
)
from custom_components.storzandbickel_ble.volcano import (
    CHAR_FAN_ON,
    CHAR_TARGET_TEMP,
)

from .conftest import (
    ADDRESS,
    DEVICE_NAME,
    FORMATTED_MAC,
    FakeBleakClient,
    make_service_info,
)


async def test_setup_and_unload(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """The entry loads every platform and unloads cleanly."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("climate.s_b_volcano_h") is not None
    assert hass.states.get("switch.s_b_volcano_h_heater") is not None
    assert hass.states.get("binary_sensor.s_b_volcano_h_connection").state == "on"

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_bluetooth_callback_matches_the_manager_signature(
    hass: HomeAssistant,
    mock_bluetooth: AsyncMock,
    config_entry: MockConfigEntry,
    bluetooth_callbacks: list[Callable[..., None]],
) -> None:
    """The registered callback must accept (service_info, change).

    The Bluetooth manager always passes two positional arguments, including
    when it replays advertisement history at registration time. A one-argument
    callback raises TypeError inside the manager and the integration silently
    stops following the device between adapters and proxies.
    """
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    # Two registrations: the component-scope setup-retry watcher, then the
    # per-entry one that follows the device between adapters and proxies.
    assert len(bluetooth_callbacks) == 2
    service_info = make_service_info()

    for registered in bluetooth_callbacks:
        # Exactly how homeassistant/components/bluetooth/manager.py invokes it.
        registered(service_info, BluetoothChange.ADVERTISEMENT)

    assert config_entry.runtime_data.device._ble_device is service_info.device


async def test_setup_retries_when_out_of_range(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """An unreachable device leaves the entry in setup_retry, not loaded."""
    config_entry.add_to_hass(hass)

    with patch(
        "custom_components.storzandbickel_ble.async_ble_device_from_address",
        return_value=None,
    ):
        assert not await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_retries_when_connection_fails(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """A failed first poll raises ConfigEntryNotReady rather than half loading.

    Setup no longer connects on its own -- the coordinator's first refresh does
    it -- so this exercises the path through async_config_entry_first_refresh.
    """
    config_entry.add_to_hass(hass)

    with patch(
        "custom_components.storzandbickel_ble.volcano.VolcanoHybrid.async_update",
        side_effect=DeviceConnectionError("nope", ConnectionFailure.CONNECT_FAILED),
    ):
        assert not await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_an_advertisement_rescues_an_entry_stuck_in_setup_retry(
    hass: HomeAssistant,
    mock_bluetooth: AsyncMock,
    config_entry: MockConfigEntry,
    bluetooth_callbacks: list[Callable[..., None]],
) -> None:
    """Hearing the device again reloads the entry instead of waiting out backoff.

    Setup needs the vaporiser reachable, so unplugging it around a restart parks
    the entry in SETUP_RETRY behind an exponential backoff -- 29 minutes on a
    real instance. The component-scope watcher has to do this because the
    per-entry callback is torn down along with the failed setup.
    """
    config_entry.add_to_hass(hass)

    with patch(
        "custom_components.storzandbickel_ble.async_ble_device_from_address",
        return_value=None,
    ):
        assert not await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY

    # The watcher registered during async_setup, before the entry gave up.
    assert bluetooth_callbacks
    bluetooth_callbacks[0](make_service_info(), BluetoothChange.ADVERTISEMENT)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED


async def test_an_advertisement_for_another_device_is_ignored(
    hass: HomeAssistant,
    mock_bluetooth: AsyncMock,
    config_entry: MockConfigEntry,
    bluetooth_callbacks: list[Callable[..., None]],
) -> None:
    """A different address must not reload an unrelated retrying entry."""
    config_entry.add_to_hass(hass)

    with patch(
        "custom_components.storzandbickel_ble.async_ble_device_from_address",
        return_value=None,
    ):
        assert not await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        bluetooth_callbacks[0](
            make_service_info(address="11:22:33:44:55:66"),
            BluetoothChange.ADVERTISEMENT,
        )
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_startup_options_are_applied_once(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, fake_client: FakeBleakClient
) -> None:
    """fan_on_connect and initial_temperature run on setup, not per reconnect."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=DEVICE_NAME,
        version=1,
        unique_id=FORMATTED_MAC,
        data={CONF_ADDRESS: ADDRESS},
        options={CONF_FAN_ON_CONNECT: True, CONF_INITIAL_TEMP: 180},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    written = fake_client.writes
    assert (CHAR_FAN_ON, b"\x00") in written
    assert (CHAR_TARGET_TEMP, (1800).to_bytes(4, "little")) in written

    # A later poll must not repeat them.
    before = len(written)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert len(fake_client.writes) == before + 2


async def test_entities_go_unavailable_when_the_device_drops(
    hass: HomeAssistant,
    mock_bluetooth: AsyncMock,
    config_entry: MockConfigEntry,
    fake_client: FakeBleakClient,
) -> None:
    """Losing the link marks entities unavailable but keeps connectivity readable."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = config_entry.runtime_data
    coordinator.device._handle_disconnect(fake_client)
    await hass.async_block_till_done()

    assert hass.states.get("climate.s_b_volcano_h").state == "unavailable"
    assert hass.states.get("binary_sensor.s_b_volcano_h_connection").state == "off"


async def test_platforms_are_all_declared() -> None:
    """Every platform module the integration ships is forwarded."""
    from custom_components.storzandbickel_ble import PLATFORMS

    assert set(PLATFORMS) == {
        Platform.BINARY_SENSOR,
        Platform.BUTTON,
        Platform.CLIMATE,
        Platform.LIGHT,
        Platform.NUMBER,
        Platform.SENSOR,
        Platform.SWITCH,
        Platform.UPDATE,
    }
