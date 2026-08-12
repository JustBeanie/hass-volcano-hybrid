"""Tests for the connection-contention repair flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.volcano_hybrid.const import DOMAIN, ISSUE_CONNECTION_REFUSED
from custom_components.volcano_hybrid.repairs import async_create_fix_flow
from custom_components.volcano_hybrid.volcano import VolcanoConnectionError

from .conftest import FakeBleakClient


@pytest.fixture
async def loaded_entry(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, config_entry: MockConfigEntry
) -> MockConfigEntry:
    """Return a fully set up config entry."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry


async def test_fix_flow_reloads_the_entry(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fake_client: FakeBleakClient
) -> None:
    """Confirming the repair retries immediately instead of waiting."""
    issue_id = f"{ISSUE_CONNECTION_REFUSED}_{loaded_entry.entry_id}"
    flow = await async_create_fix_flow(
        hass, issue_id, {"entry_id": loaded_entry.entry_id}
    )
    flow.hass = hass

    result = await flow.async_step_init()
    assert result["type"] == "form"
    assert result["step_id"] == "confirm"

    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as reload:
        result = await flow.async_step_confirm({})

    assert result["type"] == "create_entry"
    reload.assert_awaited_once_with(loaded_entry.entry_id)


async def test_fix_flow_rejects_an_unknown_issue(hass: HomeAssistant) -> None:
    """An issue id this integration did not raise is an error, not a crash."""
    with pytest.raises(ValueError, match="Unknown repair issue"):
        await async_create_fix_flow(hass, "something_else", {"entry_id": "abc"})

    with pytest.raises(ValueError, match="Unknown repair issue"):
        await async_create_fix_flow(hass, ISSUE_CONNECTION_REFUSED, None)


async def test_repair_round_trip(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """The issue is raised, carries the entry id, and clears on recovery."""
    coordinator = loaded_entry.runtime_data
    issue_id = f"{ISSUE_CONNECTION_REFUSED}_{loaded_entry.entry_id}"

    with (
        patch(
            "custom_components.volcano_hybrid.coordinator.async_address_present",
            return_value=True,
        ),
        patch.object(
            coordinator.device,
            "async_update",
            side_effect=VolcanoConnectionError("refused"),
        ),
    ):
        for _ in range(3):
            await coordinator.async_refresh()

    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.data == {"entry_id": loaded_entry.entry_id}

    flow = await async_create_fix_flow(hass, issue_id, issue.data)
    assert flow is not None
