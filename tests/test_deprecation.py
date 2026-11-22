"""Tests for the repairs on the seven sensors deprecated in 4.4.0."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.storzandbickel_ble.const import (
    DOMAIN,
    ISSUE_DEPRECATED_SENSOR,
    ISSUE_DEPRECATED_SENSOR_USED,
)
from custom_components.storzandbickel_ble.deprecation import (
    DEPRECATED_SENSORS,
    LEARN_MORE_URL,
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


def _issue_id(entry: MockConfigEntry, key: str) -> str:
    return f"{ISSUE_DEPRECATED_SENSOR}_{key}_{entry.entry_id}"


def _our_issues(hass: HomeAssistant) -> list[ir.IssueEntry]:
    return [
        issue
        for (domain, issue_id), issue in ir.async_get(hass).issues.items()
        if domain == DOMAIN and issue_id.startswith(ISSUE_DEPRECATED_SENSOR)
    ]


async def _enable(hass: HomeAssistant, entry: MockConfigEntry, entity_id: str) -> None:
    er.async_get(hass).async_update_entity(entity_id, disabled_by=None)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()


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
async def test_an_enabled_sensor_raises_a_repair_that_clears_on_disable(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, key: str, entity_id: str
) -> None:
    """Enabled and unused: say what replaces it, and clear once it is disabled."""
    await _enable(hass, loaded_entry, entity_id)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, _issue_id(loaded_entry, key))
    assert issue is not None
    assert issue.translation_key == ISSUE_DEPRECATED_SENSOR
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.is_fixable is False
    assert issue.learn_more_url == LEARN_MORE_URL
    assert issue.translation_placeholders == {
        "entity_id": entity_id,
        "name": "S&B VOLCANO H",
        "replacement": DEPRECATED_SENSORS[key],
    }
    assert len(_our_issues(hass)) == 1

    # No restart: the registry update alone clears it.
    er.async_get(hass).async_update_entity(
        entity_id, disabled_by=er.RegistryEntryDisabler.USER
    )
    await hass.async_block_till_done()
    assert _our_issues(hass) == []


async def test_a_deleted_sensor_clears_its_repair(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Deleting the sensor counts the same as disabling it."""
    entity_id = ENTITY_IDS["serial_number"]
    await _enable(hass, loaded_entry, entity_id)
    assert len(_our_issues(hass)) == 1

    er.async_get(hass).async_remove(entity_id)
    await hass.async_block_till_done()

    assert _our_issues(hass) == []


async def test_a_renamed_sensor_keeps_its_repair_current(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """A new entity ID is followed, and the repair names it."""
    entity_id = ENTITY_IDS["fan_status"]
    await _enable(hass, loaded_entry, entity_id)
    registry = er.async_get(hass)

    registry.async_update_entity(entity_id, new_entity_id="sensor.old_fan_text")
    await hass.async_block_till_done()
    (issue,) = _our_issues(hass)
    assert issue.translation_placeholders["entity_id"] == "sensor.old_fan_text"

    registry.async_update_entity(
        "sensor.old_fan_text", disabled_by=er.RegistryEntryDisabler.USER
    )
    await hass.async_block_till_done()
    assert _our_issues(hass) == []


async def test_other_registry_changes_are_ignored(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Only the deprecated sensors' own rows are worth re-checking."""
    await _enable(hass, loaded_entry, ENTITY_IDS["heater_status"])
    ir.async_get(hass).async_delete(DOMAIN, _issue_id(loaded_entry, "heater_status"))

    er.async_get(hass).async_update_entity(
        "sensor.s_b_volcano_h_temperature", name="Block"
    )
    await hass.async_block_till_done()

    # Nothing re-raised it: the change was not to a deprecated sensor.
    assert _our_issues(hass) == []


async def test_a_sensor_in_use_lists_what_uses_it(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Automations and scripts that still use it are named in the repair."""
    entity_id = ENTITY_IDS["heater_status"]
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "id": "heater_text",
                "alias": "Heater text",
                "triggers": {"trigger": "state", "entity_id": entity_id},
                "actions": [],
            }
        },
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
    await hass.async_block_till_done()

    await _enable(hass, loaded_entry, entity_id)

    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, _issue_id(loaded_entry, "heater_status")
    )
    assert issue is not None
    assert issue.translation_key == ISSUE_DEPRECATED_SENSOR_USED
    assert issue.translation_placeholders["items"] == (
        "- `automation.heater_text`\n- `script.heater_check`"
    )


async def test_removing_the_entry_removes_its_repairs(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """The repairs belong to the entry."""
    await _enable(hass, loaded_entry, ENTITY_IDS["connection_status"])
    assert len(_our_issues(hass)) == 1

    await hass.config_entries.async_remove(loaded_entry.entry_id)
    await hass.async_block_till_done()

    assert _our_issues(hass) == []
