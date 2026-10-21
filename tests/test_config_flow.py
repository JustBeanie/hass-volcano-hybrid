"""Config flow tests. These must cover config_flow.py completely."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_USER
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.storzandbickel_ble.ble_device import (
    ConnectionFailure,
    DeviceConnectionError,
)
from custom_components.storzandbickel_ble.const import (
    CONF_FAN_ON_CONNECT,
    CONF_INITIAL_TEMP,
    DOMAIN,
)

from .conftest import (
    ADDRESS,
    DEVICE_NAME,
    FORMATTED_MAC,
    NOT_VOLCANO_SERVICE_INFO,
    SERVICE_INFO,
)


async def test_bluetooth_discovery_creates_entry(
    hass: HomeAssistant, mock_bluetooth: AsyncMock
) -> None:
    """A discovered Volcano can be confirmed and set up."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=SERVICE_INFO
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"
    assert result["description_placeholders"] == {"name": DEVICE_NAME}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "options"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_INITIAL_TEMP: 185, CONF_FAN_ON_CONNECT: True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == DEVICE_NAME
    # Behaviour settings live in options so they stay editable afterwards.
    assert result["data"] == {CONF_ADDRESS: ADDRESS, "model": "volcano_hybrid"}
    assert result["options"] == {
        CONF_INITIAL_TEMP: 185,
        CONF_FAN_ON_CONNECT: True,
    }
    assert result["result"].unique_id == FORMATTED_MAC
    assert result["result"].version == 1


async def test_bluetooth_discovery_without_options(
    hass: HomeAssistant, mock_bluetooth: AsyncMock
) -> None:
    """Leaving the optional fields empty omits them from the entry data."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=SERVICE_INFO
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ADDRESS: ADDRESS, "model": "volcano_hybrid"}
    assert result["options"] == {CONF_FAN_ON_CONNECT: False}


async def test_bluetooth_discovery_of_another_device_is_rejected(
    hass: HomeAssistant, mock_bluetooth: AsyncMock
) -> None:
    """A non-Volcano advertisement is aborted rather than set up."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=NOT_VOLCANO_SERVICE_INFO
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_supported"


async def test_bluetooth_discovery_aborts_when_already_configured(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """Discovering a configured device does not offer it twice."""
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=SERVICE_INFO
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_bluetooth_confirm_handles_a_failed_connection(
    hass: HomeAssistant, mock_bluetooth: AsyncMock
) -> None:
    """A device that will not connect shows an error and can be retried."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=SERVICE_INFO
    )

    with patch(
        "custom_components.storzandbickel_ble.ble_device.StorzBickelDevice.async_connect",
        side_effect=DeviceConnectionError("boom", ConnectionFailure.CONNECT_FAILED),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"
    assert result["errors"] == {"base": "cannot_connect"}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "options"


async def test_bluetooth_confirm_handles_a_device_out_of_range(
    hass: HomeAssistant, mock_bluetooth: AsyncMock
) -> None:
    """A device that has since gone away reports not_in_range."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=SERVICE_INFO
    )

    with patch(
        "custom_components.storzandbickel_ble.config_flow.async_ble_device_from_address",
        return_value=None,
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "not_in_range"}


async def test_user_flow_creates_entry(
    hass: HomeAssistant, mock_bluetooth: AsyncMock
) -> None:
    """The manual flow lists devices Home Assistant already sees."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "options"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ADDRESS] == ADDRESS


async def test_user_flow_without_devices_asks_for_an_address(
    hass: HomeAssistant, mock_bluetooth: AsyncMock
) -> None:
    """With nothing in range the flow asks for the address instead of giving up."""
    with patch(
        "custom_components.storzandbickel_ble.config_flow.async_discovered_service_info",
        return_value=[NOT_VOLCANO_SERVICE_INFO],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual"


async def test_user_flow_hides_configured_devices(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """An already-configured Volcano is filtered out of the picker."""
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual"


async def test_user_flow_handles_a_failed_connection(
    hass: HomeAssistant, mock_bluetooth: AsyncMock
) -> None:
    """A connection failure in the manual flow re-shows the picker."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    with patch(
        "custom_components.storzandbickel_ble.ble_device.StorzBickelDevice.async_connect",
        side_effect=DeviceConnectionError("boom", ConnectionFailure.CONNECT_FAILED),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ADDRESS: ADDRESS}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_aborts_on_duplicate(
    hass: HomeAssistant, mock_bluetooth: AsyncMock
) -> None:
    """Selecting a device that got configured mid-flow aborts cleanly."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    MockConfigEntry(
        domain=DOMAIN, unique_id=FORMATTED_MAC, data={CONF_ADDRESS: ADDRESS}
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("S&B VOLCANO H", "volcano_hybrid"),
        ("s&b volcano h", "volcano_hybrid"),
        ("VOLCANO HYBRID", "volcano_hybrid"),
        ("S&B VY123456", "venty"),
        ("S&B VZ123456", "veazy"),
        ("STORZ&BICKEL", "crafty"),
        ("Storz&Bickel", "crafty"),
        ("Kitchen Scale", None),
        ("", None),
    ],
)
def test_family_of(name: str, expected: str | None) -> None:
    """The name picks the family, case-insensitively; anything else is not ours."""
    from custom_components.storzandbickel_ble.config_flow import _family_of

    from .conftest import make_service_info

    assert _family_of(make_service_info(name=name)) == expected
