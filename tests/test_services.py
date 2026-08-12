"""Tests for the integration actions."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.const import ATTR_AREA_ID, ATTR_DEVICE_ID, ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import area_registry as ar, device_registry as dr
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import voluptuous as vol

from custom_components.volcano_hybrid.const import (
    ATTR_ANIMATION_TYPE,
    ATTR_DURATION,
    ATTR_TURN_OFF_HEAT,
    ATTR_TURN_OFF_SCREEN,
    DOMAIN,
    SERVICE_FAN_TIMER,
    SERVICE_SCREEN_ANIMATION,
)
from custom_components.volcano_hybrid.volcano import (
    CHAR_BRIGHTNESS,
    CHAR_FAN_OFF,
    CHAR_FAN_ON,
    CHAR_HEAT_OFF,
)

from .conftest import FakeBleakClient

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


async def test_actions_are_registered_without_an_entry(
    hass: HomeAssistant, mock_bluetooth: AsyncMock
) -> None:
    """The actions exist even when no device is configured.

    This is the action-setup quality-scale rule: services must be registered
    from async_setup, not async_setup_entry.
    """
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    assert hass.services.has_service(DOMAIN, SERVICE_FAN_TIMER)
    assert hass.services.has_service(DOMAIN, SERVICE_SCREEN_ANIMATION)


async def test_fan_timer_runs_and_stops_the_fan(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fake_client: FakeBleakClient
) -> None:
    """The fan timer starts the fan, then stops it and cleans up."""
    with patch("custom_components.volcano_hybrid.coordinator.asyncio.sleep") as sleep:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_FAN_TIMER,
            {
                ATTR_ENTITY_ID: CLIMATE,
                ATTR_DURATION: 36,
                ATTR_TURN_OFF_HEAT: True,
                ATTR_TURN_OFF_SCREEN: True,
            },
            blocking=True,
        )
        await hass.async_block_till_done()

    # Patching the module attribute patches asyncio.sleep globally, so assert
    # our own call rather than the total count.
    sleep.assert_any_await(36)
    written = {uuid for uuid, _ in fake_client.writes}
    assert CHAR_FAN_ON in written
    assert CHAR_FAN_OFF in written
    assert CHAR_HEAT_OFF in written
    assert (CHAR_BRIGHTNESS, (0).to_bytes(2, "little")) in fake_client.writes


async def test_fan_timer_is_cancelled_on_unload(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fake_client: FakeBleakClient
) -> None:
    """A pending fan timer does not operate the device after unload."""
    await hass.services.async_call(
        DOMAIN,
        SERVICE_FAN_TIMER,
        {ATTR_ENTITY_ID: CLIMATE, ATTR_DURATION: 3600},
        blocking=True,
    )
    await hass.async_block_till_done()

    coordinator = loaded_entry.runtime_data
    assert coordinator._fan_timer_task is not None

    assert await hass.config_entries.async_unload(loaded_entry.entry_id)
    await hass.async_block_till_done()

    assert CHAR_FAN_OFF not in {uuid for uuid, _ in fake_client.writes}


async def test_fan_timer_rejects_a_bad_duration(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Duration is required and bounded, so it can never reach asyncio.sleep."""
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN, SERVICE_FAN_TIMER, {ATTR_ENTITY_ID: CLIMATE}, blocking=True
        )

    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_FAN_TIMER,
            {ATTR_ENTITY_ID: CLIMATE, ATTR_DURATION: 100000},
            blocking=True,
        )


async def test_screen_animation_starts_and_stops(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fake_client: FakeBleakClient
) -> None:
    """An animation drives the brightness and 'none' restores the default."""
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SCREEN_ANIMATION,
        {ATTR_ENTITY_ID: CLIMATE, ATTR_ANIMATION_TYPE: "breathing"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert loaded_entry.runtime_data._animation_task is not None

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SCREEN_ANIMATION,
        {ATTR_ENTITY_ID: CLIMATE, ATTR_ANIMATION_TYPE: "none"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert loaded_entry.runtime_data._animation_task is None
    assert (CHAR_BRIGHTNESS, (70).to_bytes(2, "little")) in fake_client.writes


async def test_screen_animation_rejects_an_unknown_type(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Only the documented animations are accepted."""
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SCREEN_ANIMATION,
            {ATTR_ENTITY_ID: CLIMATE, ATTR_ANIMATION_TYPE: "disco"},
            blocking=True,
        )


async def test_actions_target_by_device_and_area(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fake_client: FakeBleakClient
) -> None:
    """Device and area targets resolve, not just entity ids."""
    device_registry = dr.async_get(hass)
    area_registry = ar.async_get(hass)
    device = next(
        iter(dr.async_entries_for_config_entry(device_registry, loaded_entry.entry_id))
    )
    area = area_registry.async_get_or_create("Lounge")
    device_registry.async_update_device(device.id, area_id=area.id)

    with patch("custom_components.volcano_hybrid.coordinator.asyncio.sleep"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_FAN_TIMER,
            {ATTR_DEVICE_ID: [device.id], ATTR_DURATION: 5},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert CHAR_FAN_ON in {uuid for uuid, _ in fake_client.writes}

        fake_client.writes.clear()
        await hass.services.async_call(
            DOMAIN,
            SERVICE_FAN_TIMER,
            {ATTR_AREA_ID: [area.id], ATTR_DURATION: 5},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert CHAR_FAN_ON in {uuid for uuid, _ in fake_client.writes}


async def test_action_without_any_configured_device(
    hass: HomeAssistant, mock_bluetooth: AsyncMock
) -> None:
    """Calling an action with nothing set up is a validation error, not a crash."""
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SCREEN_ANIMATION,
            {ATTR_ANIMATION_TYPE: "blinking"},
            blocking=True,
        )


async def test_action_against_an_unloaded_entry(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """An unloaded entry produces a clear error rather than an attribute error."""
    assert await hass.config_entries.async_unload(loaded_entry.entry_id)
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SCREEN_ANIMATION,
            {ATTR_ANIMATION_TYPE: "blinking"},
            blocking=True,
        )
