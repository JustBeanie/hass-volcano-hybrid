"""Tests for the repairs on the seven sensors deprecated in 4.4.0.

A repair is raised only while an automation or script uses the sensor (#86).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed

from custom_components.storzandbickel_ble.const import (
    DOMAIN,
    ISSUE_DEPRECATED_SENSOR,
    ISSUE_DEPRECATED_SENSOR_USED,
)
from custom_components.storzandbickel_ble.deprecation import (
    DEPRECATED_SENSORS,
    LEARN_MORE_URL,
    USERS_SETTLE,
)

# Each deprecated sensor's key and the entity ID a Volcano gives it.
ENTITY_IDS = {
    "connection_status": "sensor.s_b_volcano_h_connection_status",
    "heater_status": "sensor.s_b_volcano_h_heater_status",
    "fan_status": "sensor.s_b_volcano_h_fan_status",
    "auto_off_time": "sensor.s_b_volcano_h_auto_off_time",
    "serial_number": "sensor.s_b_volcano_h_serial_number",
    "firmware_version": "sensor.s_b_volcano_h_firmware_version",
    "ble_firmware": "sensor.s_b_volcano_h_ble_firmware_version",
}

LOAD_YAML = "homeassistant.config.load_yaml_config_file"


def _issue_id(entry: MockConfigEntry, key: str) -> str:
    return f"{ISSUE_DEPRECATED_SENSOR}_{key}_{entry.entry_id}"


def _our_issues(hass: HomeAssistant) -> list[ir.IssueEntry]:
    return [
        issue
        for (domain, issue_id), issue in ir.async_get(hass).issues.items()
        if domain == DOMAIN and issue_id.startswith(ISSUE_DEPRECATED_SENSOR)
    ]


def _automation(entity_id: str, automation_id: str = "uses_it") -> dict[str, Any]:
    return {
        "id": automation_id,
        "alias": automation_id,
        "triggers": {"trigger": "state", "entity_id": entity_id},
        "actions": [],
    }


async def _settle(hass: HomeAssistant) -> None:
    """Let automations and scripts settle, so the debounced check runs."""
    await hass.async_block_till_done()
    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=USERS_SETTLE + 1)
    )
    await hass.async_block_till_done()


async def _enable(hass: HomeAssistant, entry: MockConfigEntry, entity_id: str) -> None:
    er.async_get(hass).async_update_entity(entity_id, disabled_by=None)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()


async def _use_in_automation(hass: HomeAssistant, entity_id: str) -> None:
    assert await async_setup_component(
        hass, "automation", {"automation": _automation(entity_id)}
    )
    await _settle(hass)


def test_every_deprecated_sensor_is_listed() -> None:
    """The table and the issue's list agree."""
    assert set(DEPRECATED_SENSORS) == set(ENTITY_IDS)


async def test_a_new_install_raises_nothing(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """All seven start disabled, so a new install has no repair to act on."""
    registry = er.async_get(hass)
    for entity_id in ENTITY_IDS.values():
        entity = registry.async_get(entity_id)
        assert entity is not None, entity_id
        assert entity.disabled, entity_id
    assert _our_issues(hass) == []


@pytest.mark.parametrize(("key", "entity_id"), ENTITY_IDS.items())
async def test_an_enabled_sensor_nothing_uses_raises_nothing(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, key: str, entity_id: str
) -> None:
    """Enabled but unused: there is nothing to fix, so no repair (#86)."""
    await _use_in_automation(hass, "sensor.s_b_volcano_h_temperature")

    await _enable(hass, loaded_entry, entity_id)

    assert _our_issues(hass) == []


@pytest.mark.parametrize(("key", "entity_id"), ENTITY_IDS.items())
async def test_a_sensor_in_use_raises_a_repair_that_clears_on_disable(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, key: str, entity_id: str
) -> None:
    """Used by an automation: say what replaces it and what uses it."""
    await _enable(hass, loaded_entry, entity_id)
    await _use_in_automation(hass, entity_id)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, _issue_id(loaded_entry, key))
    assert issue is not None
    assert issue.translation_key == ISSUE_DEPRECATED_SENSOR_USED
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.is_fixable is False
    assert issue.learn_more_url == LEARN_MORE_URL
    assert issue.translation_placeholders == {
        "entity_id": entity_id,
        "name": "S&B VOLCANO H",
        "replacement": DEPRECATED_SENSORS[key],
        "items": "- `automation.uses_it`",
    }
    assert len(_our_issues(hass)) == 1

    # No restart: the registry update alone clears it.
    er.async_get(hass).async_update_entity(
        entity_id, disabled_by=er.RegistryEntryDisabler.USER
    )
    await hass.async_block_till_done()
    assert _our_issues(hass) == []


async def test_the_repair_follows_automations_being_added_and_removed(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Starting to use the sensor raises it, and it clears once nothing does."""
    entity_id = ENTITY_IDS["heater_status"]
    await _enable(hass, loaded_entry, entity_id)
    assert await async_setup_component(hass, "automation", {"automation": []})
    await hass.async_block_till_done()
    assert _our_issues(hass) == []

    with patch(LOAD_YAML, return_value={"automation": [_automation(entity_id)]}):
        await hass.services.async_call("automation", "reload", blocking=True)
        await _settle(hass)
    assert len(_our_issues(hass)) == 1

    with patch(LOAD_YAML, return_value={"automation": []}):
        await hass.services.async_call("automation", "reload", blocking=True)
        await _settle(hass)
    assert _our_issues(hass) == []


async def test_a_deleted_sensor_clears_its_repair(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Deleting the sensor counts the same as disabling it."""
    entity_id = ENTITY_IDS["serial_number"]
    await _enable(hass, loaded_entry, entity_id)
    await _use_in_automation(hass, entity_id)
    assert len(_our_issues(hass)) == 1

    er.async_get(hass).async_remove(entity_id)
    await hass.async_block_till_done()

    assert _our_issues(hass) == []


async def test_a_renamed_sensor_is_followed(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """After a rename the sensor is still tracked, and disabling it still clears."""
    entity_id = ENTITY_IDS["fan_status"]
    await _enable(hass, loaded_entry, entity_id)
    await _use_in_automation(hass, entity_id)
    assert len(_our_issues(hass)) == 1
    registry = er.async_get(hass)

    # The automation still names the old ID, so nothing uses the new one.
    registry.async_update_entity(entity_id, new_entity_id="sensor.old_fan_text")
    await hass.async_block_till_done()
    assert _our_issues(hass) == []

    registry.async_update_entity(
        "sensor.old_fan_text", disabled_by=er.RegistryEntryDisabler.USER
    )
    await hass.async_block_till_done()
    assert _our_issues(hass) == []


async def test_other_registry_changes_are_ignored(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Only the deprecated sensors' own rows are worth re-checking."""
    entity_id = ENTITY_IDS["heater_status"]
    await _enable(hass, loaded_entry, entity_id)
    await _use_in_automation(hass, entity_id)
    ir.async_get(hass).async_delete(DOMAIN, _issue_id(loaded_entry, "heater_status"))

    er.async_get(hass).async_update_entity(
        "sensor.s_b_volcano_h_temperature", name="Block"
    )
    await hass.async_block_till_done()

    # Nothing re-raised it: the change was not to a deprecated sensor.
    assert _our_issues(hass) == []


async def test_a_sensor_in_use_lists_everything_that_uses_it(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Automations and scripts that still use it are all named."""
    entity_id = ENTITY_IDS["heater_status"]
    await _enable(hass, loaded_entry, entity_id)
    assert await async_setup_component(
        hass,
        "automation",
        {"automation": _automation(entity_id, "heater_text")},
    )
    assert await async_setup_component(
        hass,
        "script",
        {
            "script": {
                "heater_check": {
                    "sequence": [
                        {
                            "condition": "state",
                            "entity_id": entity_id,
                            "state": "On",
                        }
                    ]
                }
            }
        },
    )
    await _settle(hass)

    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, _issue_id(loaded_entry, "heater_status")
    )
    assert issue is not None
    assert issue.translation_placeholders["items"] == (
        "- `automation.heater_text`\n- `script.heater_check`"
    )


async def test_removing_the_entry_removes_its_repairs(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """The repairs belong to the entry."""
    entity_id = ENTITY_IDS["connection_status"]
    await _enable(hass, loaded_entry, entity_id)
    await _use_in_automation(hass, entity_id)
    assert len(_our_issues(hass)) == 1

    await hass.config_entries.async_remove(loaded_entry.entry_id)
    await hass.async_block_till_done()

    assert _our_issues(hass) == []
