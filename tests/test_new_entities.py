"""Tests for the entities added in 4.1: Ready, hvac_action and diagnostics."""

from __future__ import annotations

from homeassistant.components.climate import ATTR_HVAC_ACTION
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.storzandbickel_ble.volcano import (
    CHAR_MAINS_VOLTAGE,
    CHAR_STATUS_REGISTER,
    MASK_HEATER,
    MASK_TARGET_REACHED,
)

from .conftest import FakeBleakClient

CLIMATE = "climate.s_b_volcano_h"
READY = "binary_sensor.s_b_volcano_h_ready"


async def _poll(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Run one poll and let the states settle."""
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


async def test_ready_follows_the_devices_own_signal(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fake_client: FakeBleakClient
) -> None:
    """Ready is the device's reached bit, and only while heating."""
    fake_client.reads[CHAR_STATUS_REGISTER] = (
        MASK_HEATER | MASK_TARGET_REACHED
    ).to_bytes(2, "little")
    await _poll(hass, loaded_entry)
    assert hass.states.get(READY).state == "on"

    fake_client.reads[CHAR_STATUS_REGISTER] = MASK_TARGET_REACHED.to_bytes(2, "little")
    await _poll(hass, loaded_entry)
    assert hass.states.get(READY).state == "off"


async def test_ready_is_unknown_before_register_1(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """No reading is not "not ready"."""
    coordinator = loaded_entry.runtime_data
    coordinator.data.status1 = None
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    assert hass.states.get(READY).state == "unknown"


async def test_climate_falls_back_to_temperatures_without_the_bit(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Before register 1 is read, the action comes from the temperatures."""
    coordinator = loaded_entry.runtime_data
    data = coordinator.data
    data.heater_on = True
    data.status1 = None
    data.current_temperature = 150.0
    data.target_temperature = 205.0
    coordinator.async_update_listeners()
    await hass.async_block_till_done()
    assert hass.states.get(CLIMATE).attributes[ATTR_HVAC_ACTION] == "heating"

    data.current_temperature = 205.0
    coordinator.async_update_listeners()
    await hass.async_block_till_done()
    assert hass.states.get(CLIMATE).attributes[ATTR_HVAC_ACTION] == "idle"


async def test_new_diagnostic_sensors(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fake_client: FakeBleakClient
) -> None:
    """Signal strength, connected-via and mains voltage read through."""
    registry = er.async_get(hass)
    for entity_id in (
        "sensor.s_b_volcano_h_signal_strength",
        "sensor.s_b_volcano_h_connected_via",
        "sensor.s_b_volcano_h_mains_voltage",
    ):
        registry.async_update_entity(entity_id, disabled_by=None)
    fake_client.reads[CHAR_MAINS_VOLTAGE] = b"230VAC"
    await hass.config_entries.async_reload(loaded_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.s_b_volcano_h_mains_voltage").state == "230VAC"
    assert hass.states.get("sensor.s_b_volcano_h_signal_strength") is not None
    assert hass.states.get("sensor.s_b_volcano_h_connected_via") is not None


async def test_auto_off_cannot_be_set_below_twenty_minutes(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """The slider starts at 20 minutes."""
    state = hass.states.get("number.s_b_volcano_h_auto_off_time")
    assert state.attributes["min"] == 20
