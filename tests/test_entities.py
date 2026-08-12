"""Tests for the entity platforms."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.components.climate import (
    ATTR_FAN_MODE,
    ATTR_HVAC_ACTION,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_FAN_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACMode,
)
from homeassistant.components.light import ATTR_BRIGHTNESS, DOMAIN as LIGHT_DOMAIN
from homeassistant.components.number import (
    ATTR_VALUE,
    DOMAIN as NUMBER_DOMAIN,
    SERVICE_SET_VALUE,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.volcano_hybrid.volcano import (
    CHAR_AUTO_OFF_SETTING,
    CHAR_BRIGHTNESS,
    CHAR_CURRENT_TEMP,
    CHAR_FAN_OFF,
    CHAR_FAN_ON,
    CHAR_HEAT_OFF,
    CHAR_HEAT_ON,
    CHAR_TARGET_TEMP,
)

from .conftest import RAW_CURRENT_TEMP_HOT, FakeBleakClient

CLIMATE = "climate.s_b_volcano_h"
SCREEN = "light.s_b_volcano_h_screen"


@pytest.fixture
async def loaded_entry(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, config_entry: MockConfigEntry
) -> MockConfigEntry:
    """Return a fully set up config entry."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry


async def test_all_entities_are_created(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Every platform contributes its entities to one device."""
    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, loaded_entry.entry_id)
    by_platform: dict[str, int] = {}
    for entity in entities:
        by_platform[entity.domain] = by_platform.get(entity.domain, 0) + 1

    assert by_platform == {
        Platform.BINARY_SENSOR: 1,
        Platform.CLIMATE: 1,
        Platform.LIGHT: 1,
        Platform.NUMBER: 2,
        Platform.SENSOR: 11,
        Platform.SWITCH: 4,
    }
    assert len({entity.device_id for entity in entities}) == 1


async def test_unique_ids_are_mac_based(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Unique ids survive the entry being removed and re-added."""
    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, loaded_entry.entry_id)
    assert entities
    assert all(entity.unique_id.startswith("aa:bb:cc:dd:ee:ff_") for entity in entities)


async def test_climate_reports_idle_temperature_as_unknown(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """The -18 C sentinel is not surfaced as a temperature."""
    state = hass.states.get(CLIMATE)
    assert state is not None
    assert state.attributes["current_temperature"] is None
    assert state.attributes["temperature"] == 205.0
    assert state.state == HVACMode.OFF
    assert state.attributes[ATTR_HVAC_ACTION] == "off"


async def test_climate_heating_action(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fake_client: FakeBleakClient
) -> None:
    """Turning the heater on below setpoint reports the heating action."""
    fake_client.reads[CHAR_CURRENT_TEMP] = (1500).to_bytes(4, "little")

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        "set_hvac_mode",
        {ATTR_ENTITY_ID: CLIMATE, "hvac_mode": HVACMode.HEAT},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert (CHAR_HEAT_ON, b"\x00") in fake_client.writes
    state = hass.states.get(CLIMATE)
    assert state.state == HVACMode.HEAT
    assert state.attributes["current_temperature"] == 150.0
    assert state.attributes[ATTR_HVAC_ACTION] == "heating"

    fake_client.reads[CHAR_CURRENT_TEMP] = RAW_CURRENT_TEMP_HOT
    await loaded_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(CLIMATE).attributes[ATTR_HVAC_ACTION] == "idle"


async def test_climate_turn_off_and_fan_mode(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fake_client: FakeBleakClient
) -> None:
    """The climate entity drives the heater and the fan."""
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        "set_hvac_mode",
        {ATTR_ENTITY_ID: CLIMATE, "hvac_mode": HVACMode.OFF},
        blocking=True,
    )
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_FAN_MODE,
        {ATTR_ENTITY_ID: CLIMATE, ATTR_FAN_MODE: "on"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert (CHAR_HEAT_OFF, b"\x00") in fake_client.writes
    assert (CHAR_FAN_ON, b"\x00") in fake_client.writes
    assert hass.states.get(CLIMATE).attributes[ATTR_FAN_MODE] == "on"

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_FAN_MODE,
        {ATTR_ENTITY_ID: CLIMATE, ATTR_FAN_MODE: "off"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert (CHAR_FAN_OFF, b"\x00") in fake_client.writes


async def test_climate_set_temperature(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fake_client: FakeBleakClient
) -> None:
    """Setting a temperature writes tenths of a degree as a 32-bit value."""
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: CLIMATE, ATTR_TEMPERATURE: 190},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert (CHAR_TARGET_TEMP, (1900).to_bytes(4, "little")) in fake_client.writes
    assert hass.states.get(CLIMATE).attributes["temperature"] == 190.0


async def test_light_brightness_round_trips(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fake_client: FakeBleakClient
) -> None:
    """Brightness survives the 0-255 to 0-100 conversion and is read back."""
    state = hass.states.get(SCREEN)
    assert state.state == "on"
    assert state.attributes[ATTR_BRIGHTNESS] == 178  # 70 %

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: SCREEN, ATTR_BRIGHTNESS: 255},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert (CHAR_BRIGHTNESS, (100).to_bytes(2, "little")) in fake_client.writes
    assert hass.states.get(SCREEN).attributes[ATTR_BRIGHTNESS] == 255


async def test_light_turn_on_without_brightness_keeps_the_current_level(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fake_client: FakeBleakClient
) -> None:
    """Turning the screen back on restores the level it was last set to."""
    await hass.services.async_call(
        LIGHT_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: SCREEN}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(SCREEN).state == "off"

    await hass.services.async_call(
        LIGHT_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: SCREEN}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(SCREEN).state == "on"
    assert (CHAR_BRIGHTNESS, (70).to_bytes(2, "little")) in fake_client.writes


async def test_switches_drive_the_device(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fake_client: FakeBleakClient
) -> None:
    """Each switch writes its characteristic."""
    registry = er.async_get(hass)
    for entity_id in (
        "switch.s_b_volcano_h_register_3",
        "switch.s_b_volcano_h_register_2",
    ):
        registry.async_update_entity(entity_id, disabled_by=None)
    await hass.config_entries.async_reload(loaded_entry.entry_id)
    await hass.async_block_till_done()

    for entity_id, service in (
        ("switch.s_b_volcano_h_heater", SERVICE_TURN_ON),
        ("switch.s_b_volcano_h_fan", SERVICE_TURN_ON),
        ("switch.s_b_volcano_h_register_3", SERVICE_TURN_OFF),
        ("switch.s_b_volcano_h_register_2", SERVICE_TURN_OFF),
    ):
        await hass.services.async_call(
            "switch", service, {ATTR_ENTITY_ID: entity_id}, blocking=True
        )
    await hass.async_block_till_done()

    written = {uuid for uuid, _ in fake_client.writes}
    assert CHAR_HEAT_ON in written
    assert CHAR_FAN_ON in written
    assert hass.states.get("switch.s_b_volcano_h_register_3").state == "off"
    assert hass.states.get("switch.s_b_volcano_h_register_2").state == "off"


async def test_numbers_write_to_the_device(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fake_client: FakeBleakClient
) -> None:
    """The number entities set the target temperature and auto-off delay."""
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: "number.s_b_volcano_h_target_temperature", ATTR_VALUE: 180},
        blocking=True,
    )
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: "number.s_b_volcano_h_auto_off_time", ATTR_VALUE: 45},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert (CHAR_TARGET_TEMP, (1800).to_bytes(4, "little")) in fake_client.writes
    assert (CHAR_AUTO_OFF_SETTING, (2700).to_bytes(2, "little")) in fake_client.writes
    assert hass.states.get("number.s_b_volcano_h_auto_off_time").state == "45"


async def test_diagnostic_sensors(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Device information sensors report the decoded values."""
    registry = er.async_get(hass)
    for entity_id in (
        "sensor.s_b_volcano_h_raw_register",
        "sensor.s_b_volcano_h_connection_status",
    ):
        registry.async_update_entity(entity_id, disabled_by=None)
    await hass.config_entries.async_reload(loaded_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.s_b_volcano_h_serial_number").state == "VH38NHG700"
    assert (
        hass.states.get("sensor.s_b_volcano_h_firmware_version").state == "V01.03.00.00"
    )
    assert hass.states.get("sensor.s_b_volcano_h_hours_of_operation").state == "2721"
    assert hass.states.get("sensor.s_b_volcano_h_brightness").state == "70"
    assert (
        hass.states.get("sensor.s_b_volcano_h_connection_status").state == "Connected"
    )

    # The raw register sensor used to be stuck on "unknown" forever because its
    # work sat in async_update, which a CoordinatorEntity never calls.
    raw = hass.states.get("sensor.s_b_volcano_h_raw_register")
    assert raw.state not in (None, "unknown")
    assert raw.attributes["current_temperature"] == "4cffffff"
