"""Tests for the options flow, reconfigure flow and the v2 to v3 migration."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_BLUETOOTH, ConfigEntryState
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.volcano_hybrid.const import (
    CONF_FAN_ON_CONNECT,
    CONF_INITIAL_TEMP,
    DOMAIN,
)
from custom_components.volcano_hybrid.volcano import (
    CHAR_TARGET_TEMP,
    VolcanoConnectionError,
)

from .conftest import ADDRESS, SERVICE_INFO, FakeBleakClient


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
    hass: HomeAssistant, mock_bluetooth: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """A device that will not answer shows the error instead of aborting."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await config_entry.start_reconfigure_flow(hass)
    with patch(
        "custom_components.volcano_hybrid.config_flow.VolcanoHybrid.async_connect",
        side_effect=VolcanoConnectionError("boom"),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_migration_v2_to_v3_moves_settings_to_options(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, v2_config_entry: MockConfigEntry
) -> None:
    """The behaviour settings move out of data without touching anything else."""
    v2_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(v2_config_entry.entry_id)
    await hass.async_block_till_done()

    assert v2_config_entry.version == 3
    assert v2_config_entry.data == {CONF_ADDRESS: ADDRESS}
    assert v2_config_entry.options == {
        CONF_FAN_ON_CONNECT: True,
        CONF_INITIAL_TEMP: 180,
    }
    # Entities are unaffected by the move.
    assert hass.states.get("climate.s_b_volcano_h") is not None


async def test_rediscovery_reloads_a_retrying_entry(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """A device coming back into range retries immediately.

    Without this the entry waits out the config entry backoff even though the
    advertisement proves the vaporizer is available again.
    """
    config_entry.add_to_hass(hass)

    with patch(
        "custom_components.volcano_hybrid.async_ble_device_from_address",
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


async def test_migration_drops_an_unset_initial_temperature(
    hass: HomeAssistant, mock_bluetooth: AsyncMock
) -> None:
    """A v1-era explicit None does not become a populated-but-empty option."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="S&B VOLCANO H",
        version=2,
        unique_id="aa:bb:cc:dd:ee:ff",
        data={
            CONF_ADDRESS: ADDRESS,
            CONF_INITIAL_TEMP: None,
            CONF_FAN_ON_CONNECT: False,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.version == 3
    assert entry.options == {CONF_FAN_ON_CONNECT: False}
