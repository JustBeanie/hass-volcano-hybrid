"""Tests for coordinator behaviour that entity tests do not reach."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.volcano_hybrid.const import DOMAIN, ISSUE_CONNECTION_REFUSED
from custom_components.volcano_hybrid.volcano import VolcanoConnectionError

CLIMATE = "climate.s_b_volcano_h"


@pytest.fixture
async def loaded_entry(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, config_entry: MockConfigEntry
) -> MockConfigEntry:
    """Return a fully set up config entry."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry


async def test_command_failure_raises_a_translated_error(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """A failed write surfaces as a HomeAssistantError with a translation key."""
    coordinator = loaded_entry.runtime_data

    with (
        patch.object(
            coordinator.device,
            "async_turn_heater_on",
            side_effect=VolcanoConnectionError("nope"),
        ),
        pytest.raises(HomeAssistantError) as err,
    ):
        await coordinator.async_turn_heater_on()

    assert err.value.translation_key == "command_failed"
    assert err.value.translation_domain == DOMAIN


async def test_unavailable_is_logged_once_and_recovery_once(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One WARNING when the link drops, one INFO when it comes back.

    The Silver rule is explicitly about not logging per poll.
    """
    coordinator = loaded_entry.runtime_data
    caplog.clear()

    with (
        caplog.at_level(logging.INFO, "custom_components.volcano_hybrid.coordinator"),
        patch.object(
            coordinator.device,
            "async_update",
            side_effect=VolcanoConnectionError("gone"),
        ),
    ):
        for _ in range(4):
            await coordinator.async_refresh()

        assert caplog.text.count("Lost connection to the Volcano Hybrid") == 1

        await coordinator.async_refresh()
        assert caplog.text.count("Lost connection to the Volcano Hybrid") == 1

    with caplog.at_level(logging.INFO, "custom_components.volcano_hybrid.coordinator"):
        await coordinator.async_refresh()
        await coordinator.async_refresh()

    assert caplog.text.count("Reconnected to the Volcano Hybrid") == 1
    assert hass.states.get(CLIMATE).state != "unavailable"


async def test_contention_raises_a_repair_issue(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Advertising but refusing to connect points at the phone app."""
    coordinator = loaded_entry.runtime_data
    issue_id = f"{ISSUE_CONNECTION_REFUSED}_{loaded_entry.entry_id}"
    registry = ir.async_get(hass)

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

    issue = registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.is_fixable is True
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.translation_key == ISSUE_CONNECTION_REFUSED

    # It clears itself as soon as the device lets us in again.
    await coordinator.async_refresh()
    assert registry.async_get_issue(DOMAIN, issue_id) is None


async def test_no_repair_issue_when_the_device_is_simply_away(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """An out-of-range vaporizer is not something closing an app would fix."""
    coordinator = loaded_entry.runtime_data
    issue_id = f"{ISSUE_CONNECTION_REFUSED}_{loaded_entry.entry_id}"

    with (
        patch(
            "custom_components.volcano_hybrid.coordinator.async_address_present",
            return_value=False,
        ),
        patch.object(
            coordinator.device,
            "async_update",
            side_effect=VolcanoConnectionError("gone"),
        ),
    ):
        for _ in range(5):
            await coordinator.async_refresh()

    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_contention_can_be_raised_more_than_once(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """A second episode raises the issue again.

    The failure counter has to reset on success. It previously did not -- the
    reset assigned a differently-spelled attribute -- so the `== THRESHOLD`
    check could only ever match once in a Home Assistant lifetime, and every
    later contention went unreported.
    """
    coordinator = loaded_entry.runtime_data
    issue_id = f"{ISSUE_CONNECTION_REFUSED}_{loaded_entry.entry_id}"
    registry = ir.async_get(hass)

    async def _episode() -> None:
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

    await _episode()
    assert registry.async_get_issue(DOMAIN, issue_id) is not None

    await coordinator.async_refresh()
    assert registry.async_get_issue(DOMAIN, issue_id) is None
    assert coordinator.consecutive_failures == 0

    await _episode()
    assert registry.async_get_issue(DOMAIN, issue_id) is not None
