"""Tests for the diagnostics dump."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.volcano_hybrid.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import ADDRESS, SERVICE_INFO


@pytest.fixture
async def loaded_entry(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, config_entry: MockConfigEntry
) -> MockConfigEntry:
    """Return a fully set up config entry."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry


async def test_diagnostics_include_the_raw_wire_values(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """The raw bytes are the point of the dump.

    Every decoding bug found so far was diagnosed from these, not from states.
    """
    result = await async_get_config_entry_diagnostics(hass, loaded_entry)

    assert result["entry"]["version"] == 3
    assert result["state"]["raw"]["current_temperature"] == "4cffffff"
    assert result["state"]["raw"]["target_temperature"] == "02080000"
    assert result["state"]["target_temperature"] == 205.0
    assert result["coordinator"]["last_update_success"] is True
    assert result["coordinator"]["consecutive_failures"] == 0
    assert result["connection"]["connected"] is True


async def test_diagnostics_redact_hardware_identifiers(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Diagnostics get pasted into public issues, so the MAC must not survive."""
    # Populate the connection block, otherwise its identifiers are None and
    # redaction has nothing to prove.
    with patch(
        "custom_components.volcano_hybrid.diagnostics.async_last_service_info",
        return_value=SERVICE_INFO,
    ):
        result = await async_get_config_entry_diagnostics(hass, loaded_entry)

    assert result["entry"]["data"]["address"] == "**REDACTED**"
    assert result["state"]["serial_number"] == "**REDACTED**"
    assert result["connection"]["source"] == "**REDACTED**"
    assert result["connection"]["rssi"] == -60

    assert ADDRESS not in str(result)
    assert "VH38NHG700" not in str(result)
