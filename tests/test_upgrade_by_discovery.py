"""The first start after upgrading, the way Home Assistant really does it.

Home Assistant only sets up a custom integration that already has a config
entry. Straight after the 4.0 rename this one has none, so its async_setup --
which adopts the old entry and offers moves -- does not run until something
starts it. Bluetooth discovery is what arrives. These tests never call
async_setup_component themselves; the other migration tests do, which is how
4.0.0 shipped without anything adopting the old entry on a real upgrade.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_USER, ConfigEntryState
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.storzandbickel_ble.const import (
    CONF_MODEL,
    DOMAIN,
    ISSUE_LEGACY_INSTALLED,
    ISSUE_MIGRATE_FROM_OTHER,
    LEGACY_DOMAIN,
)
from custom_components.storzandbickel_ble.migration import LEGACY_CODEOWNER

from .conftest import ADDRESS, FORMATTED_MAC, SERVICE_INFO, make_service_info

INTEGRATION = "custom_components.storzandbickel_ble.migration.async_get_integration"


def _old_entry(hass: HomeAssistant) -> tuple[MockConfigEntry, str]:
    """Add a 3.x entry with its climate entity, as the upgrade leaves it."""
    old = MockConfigEntry(
        domain=LEGACY_DOMAIN,
        title="S&B VOLCANO H",
        version=3,
        unique_id=FORMATTED_MAC,
        data={CONF_ADDRESS: ADDRESS},
    )
    old.add_to_hass(hass)
    row = er.async_get(hass).async_get_or_create(
        "climate",
        LEGACY_DOMAIN,
        f"{FORMATTED_MAC}_climate",
        suggested_object_id="s_b_volcano_h",
        config_entry=old,
    )
    return old, row.id


async def _discover(hass: HomeAssistant) -> dict[str, object]:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=SERVICE_INFO
    )
    await hass.async_block_till_done()
    return result


async def test_discovery_adopts_the_old_entry(
    hass: HomeAssistant, mock_bluetooth: AsyncMock
) -> None:
    """With the old folder gone, discovering the device moves it across."""
    old, row_id = _old_entry(hass)
    assert DOMAIN not in hass.config.components

    result = await _discover(hass)

    # Not offered as a new device: starting the integration adopted it, so
    # by the time discovery asks, the device is already configured here.
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    (entry,) = hass.config_entries.async_entries(DOMAIN)
    assert entry.state is ConfigEntryState.LOADED
    assert hass.config_entries.async_get_entry(old.entry_id) is None
    row = er.async_get(hass).async_get("climate.s_b_volcano_h")
    assert row is not None
    assert row.id == row_id
    assert row.platform == DOMAIN
    assert hass.states.get("climate.s_b_volcano_h") is not None
    assert not hass.states.async_entity_ids("climate.s_b_volcano_h_2")


async def test_discovery_with_the_old_folder_still_installed(
    hass: HomeAssistant, mock_bluetooth: AsyncMock
) -> None:
    """The old copy still running: a repair says so, and nothing is touched."""
    old, row_id = _old_entry(hass)
    old.mock_state(hass, ConfigEntryState.LOADED)
    old_code = MagicMock(manifest={"codeowners": [LEGACY_CODEOWNER]})

    with patch(INTEGRATION, AsyncMock(return_value=old_code)):
        result = await _discover(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "moving_from_old_version"
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_LEGACY_INSTALLED)
    assert hass.config_entries.async_entries(DOMAIN) == []
    row = er.async_get(hass).async_get("climate.s_b_volcano_h")
    assert row is not None
    assert row.id == row_id
    assert row.platform == LEGACY_DOMAIN


async def test_a_new_entry_never_takes_from_a_running_old_one(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """Even set up some other way, an entry leaves a loaded old one alone."""
    old, _ = _old_entry(hass)
    old.mock_state(hass, ConfigEntryState.LOADED)
    config_entry.add_to_hass(hass)

    with patch(INTEGRATION, AsyncMock(return_value=MagicMock(manifest={}))):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    row = er.async_get(hass).async_get("climate.s_b_volcano_h")
    assert row is not None
    assert row.platform == LEGACY_DOMAIN
    assert hass.config_entries.async_get_entry(old.entry_id) is old


async def test_discovery_of_a_device_another_integration_has(
    hass: HomeAssistant, mock_bluetooth: AsyncMock
) -> None:
    """It points to the move instead of adding the device a second time."""
    other = MockConfigEntry(
        domain="volcano_integration",
        title="S&B VOLCANO H",
        unique_id=ADDRESS,
        data={"bt_address": ADDRESS},
    )
    other.add_to_hass(hass)

    result = await _discover(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "set_up_elsewhere"
    assert ir.async_get(hass).async_get_issue(
        DOMAIN, f"{ISSUE_MIGRATE_FROM_OTHER}_{other.entry_id}"
    )


async def test_the_picker_leaves_out_a_device_held_elsewhere(
    hass: HomeAssistant, mock_bluetooth: AsyncMock
) -> None:
    """The only device in range is another integration's, so none is listed."""
    MockConfigEntry(
        domain=LEGACY_DOMAIN,
        version=1,
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS, CONF_MODEL: "volcano_hybrid"},
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    assert result["step_id"] == "manual"


async def test_typing_the_address_of_a_device_held_elsewhere(
    hass: HomeAssistant, mock_bluetooth: AsyncMock
) -> None:
    """Typing its address leads to the same answer as discovering it."""
    MockConfigEntry(
        domain="volcano_integration",
        unique_id=ADDRESS,
        data={"bt_address": ADDRESS},
    ).add_to_hass(hass)
    with patch(
        "custom_components.storzandbickel_ble.config_flow.async_discovered_service_info",
        return_value=[],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS, CONF_MODEL: "volcano_hybrid"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "set_up_elsewhere"


async def test_a_second_device_while_the_integration_runs(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, loaded_entry: MockConfigEntry
) -> None:
    """Already running, nothing is started again, and a new device is offered.

    Another integration holding a different device does not stand in its way.
    """
    MockConfigEntry(
        domain="volcano_integration",
        unique_id="11:22:33:44:55:66",
        data={"bt_address": "11:22:33:44:55:66"},
    ).add_to_hass(hass)
    second = make_service_info(address="AA:AA:AA:AA:AA:AA")

    with patch(
        "custom_components.storzandbickel_ble.config_flow.async_setup_component"
    ) as setup:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=second
        )

    setup.assert_not_called()
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"
