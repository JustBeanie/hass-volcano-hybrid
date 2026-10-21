"""Tests for adding a vaporizer by address, and options that must survive."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.storzandbickel_ble.ble_device import (
    ConnectionFailure,
    DeviceConnectionError,
)
from custom_components.storzandbickel_ble.config_flow import MANUAL_ENTRY
from custom_components.storzandbickel_ble.const import (
    CONF_AUTO_CONNECT,
    CONF_AUTO_CONNECT_DELAY,
    DOMAIN,
)

from .conftest import ADDRESS, FORMATTED_MAC

FLOW = "custom_components.storzandbickel_ble.config_flow"


async def _manual(hass: HomeAssistant) -> dict[str, Any]:
    """Open the manual step, the way it opens with nothing discovered."""
    with patch(f"{FLOW}.async_discovered_service_info", return_value=[]):
        return await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )


async def test_the_picker_offers_manual_entry(
    hass: HomeAssistant, mock_bluetooth: AsyncMock
) -> None:
    """Choosing "enter an address" from the picker opens the manual step."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: MANUAL_ENTRY}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual"


@pytest.mark.parametrize("typed", ["not-a-mac", "AA:BB:CC:DD:EE", "GG:BB:CC:DD:EE:FF"])
async def test_a_bad_address_is_turned_back(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, typed: str
) -> None:
    """Anything but six hex pairs is refused with an explanation."""
    result = await _manual(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: typed}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_ADDRESS: "invalid_address"}


async def test_a_visible_device_is_added(
    hass: HomeAssistant, mock_bluetooth: AsyncMock
) -> None:
    """A device in range must accept a connection, then is created normally."""
    result = await _manual(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: " aa:bb:cc:dd:ee:ff "}
    )
    assert result["step_id"] == "options"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Volcano Hybrid"
    assert result["data"] == {CONF_ADDRESS: ADDRESS, "model": "volcano_hybrid"}
    assert result["result"].unique_id == FORMATTED_MAC


async def test_a_visible_device_that_refuses_is_not_added(
    hass: HomeAssistant, mock_bluetooth: AsyncMock
) -> None:
    """One in range that will not connect stays on the manual step."""
    result = await _manual(hass)
    with patch(
        "custom_components.storzandbickel_ble.ble_device.StorzBickelDevice.async_connect",
        side_effect=DeviceConnectionError("boom", ConnectionFailure.CONNECT_FAILED),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ADDRESS: ADDRESS}
        )

    assert result["step_id"] == "manual"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_a_device_out_of_range_is_still_added(
    hass: HomeAssistant, mock_bluetooth: AsyncMock
) -> None:
    """One no adapter hears yet is added, to come up when it is in range."""
    result = await _manual(hass)
    with patch(f"{FLOW}.async_ble_device_from_address", return_value=None):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ADDRESS: "11:22:33:44:55:66"}
        )
    assert result["step_id"] == "options"

    with patch(
        "custom_components.storzandbickel_ble.async_ble_device_from_address",
        return_value=None,
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_ADDRESS: "11:22:33:44:55:66",
        "model": "volcano_hybrid",
    }


async def test_a_configured_device_is_a_duplicate_in_any_case(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """The same vaporizer typed in lower case is still rejected."""
    config_entry.add_to_hass(hass)
    result = await _manual(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS.lower()}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_the_options_form_keeps_auto_connect(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Saving the form must not turn auto-connect back on behind the user."""
    hass.config_entries.async_update_entry(
        loaded_entry, options={**loaded_entry.options, CONF_AUTO_CONNECT: False}
    )
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_AUTO_CONNECT_DELAY: 2.5}
    )
    await hass.async_block_till_done()

    assert loaded_entry.options[CONF_AUTO_CONNECT] is False
    assert loaded_entry.options[CONF_AUTO_CONNECT_DELAY] == 2.5
