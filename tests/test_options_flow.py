"""Tests for the options flow, reconfigure flow and the v2 to v3 migration."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from bleak.exc import BleakError
from homeassistant.config_entries import SOURCE_BLUETOOTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant.common import MockConfigEntry

from custom_components.storzandbickel_ble.const import (
    CONF_AUTO_CONNECT_DELAY,
    CONF_DELAYED_RECONNECT_DELAY,
    CONF_FAN_ON_CONNECT,
    CONF_INITIAL_TEMP,
    DEFAULT_AUTO_CONNECT_DELAY,
    DEFAULT_DELAYED_RECONNECT_DELAY,
    DOMAIN,
)
from custom_components.storzandbickel_ble.volcano import (
    CHAR_TARGET_TEMP,
)

from .conftest import SERVICE_INFO, FakeBleakClient


async def test_options_flow_updates_and_reloads(
    hass: HomeAssistant,
    mock_bluetooth: AsyncMock,
    config_entry: MockConfigEntry,
    fake_client: FakeBleakClient,
) -> None:
    """Changing an option writes it and reloads so it takes effect."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    fake_client.writes.clear()
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_INITIAL_TEMP: 195, CONF_FAN_ON_CONNECT: False},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {
        CONF_INITIAL_TEMP: 195,
        CONF_FAN_ON_CONNECT: False,
        CONF_AUTO_CONNECT_DELAY: DEFAULT_AUTO_CONNECT_DELAY,
        CONF_DELAYED_RECONNECT_DELAY: DEFAULT_DELAYED_RECONNECT_DELAY,
    }
    # The reload re-applied it to the device.
    assert (CHAR_TARGET_TEMP, (1950).to_bytes(4, "little")) in fake_client.writes


async def test_options_flow_can_clear_the_initial_temperature(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """Leaving the field blank removes it rather than storing None."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()

    assert CONF_INITIAL_TEMP not in config_entry.options
    assert config_entry.options[CONF_FAN_ON_CONNECT] is False


async def test_reconfigure_confirms_the_device_is_reachable(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """The reconfigure flow re-tests the stored address."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"


async def test_reconfigure_reports_an_unreachable_device(
    hass: HomeAssistant,
    mock_bluetooth: AsyncMock,
    mock_establish_connection: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """A device that will not answer shows the error instead of aborting."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await config_entry.start_reconfigure_flow(hass)
    mock_establish_connection.side_effect = BleakError("boom")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_reconfigure_retests_a_running_entry_on_its_own_link(
    hass: HomeAssistant,
    mock_bluetooth: AsyncMock,
    mock_establish_connection: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """A running entry is re-tested by rebuilding its link, not with a second.

    It holds the one connection the vaporizer allows, so a second client
    proves nothing: a proxy turns it away from a device that is working.
    """
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = config_entry.runtime_data
    before = mock_establish_connection.call_count

    result = await config_entry.start_reconfigure_flow(hass)
    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["reason"] == "reconfigure_successful"
    # One connect, on the entry's own device, which is left connected...
    assert mock_establish_connection.call_count == before + 1
    assert config_entry.runtime_data is coordinator
    assert coordinator.device.connected is True
    # ...so there is nothing for a reload to pick up.
    reload.assert_not_called()


async def test_reconfigure_reports_a_running_entry_out_of_range(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """A vaporizer nothing can hear is reported as that, not as a refusal."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await config_entry.start_reconfigure_flow(hass)
    with patch(
        "custom_components.storzandbickel_ble.config_flow.async_ble_device_from_address",
        return_value=None,
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "not_in_range"}


async def test_rediscovery_reloads_a_retrying_entry(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """A device coming back into range retries immediately.

    Without this the entry waits out the config entry backoff even though the
    advertisement proves the vaporizer is available again.
    """
    config_entry.add_to_hass(hass)

    with patch(
        "custom_components.storzandbickel_ble.async_ble_device_from_address",
        return_value=None,
    ):
        assert not await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=SERVICE_INFO
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert config_entry.state is ConfigEntryState.LOADED
