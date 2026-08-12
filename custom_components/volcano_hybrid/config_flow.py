"""Config flow for the Volcano Hybrid integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_ble_device_from_address,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS
from homeassistant.helpers.device_registry import format_mac
import voluptuous as vol

from .const import (
    CONF_FAN_ON_CONNECT,
    CONF_INITIAL_TEMP,
    DOMAIN,
    MAX_TEMP,
    MIN_TEMP,
    TEMP_STEP,
)
from .volcano import VolcanoConnectionError, VolcanoHybrid

_LOGGER = logging.getLogger(__name__)

# The Volcano Hybrid advertises as "S&B VOLCANO H".
NAME_PREFIX = "S&B VOLCANO"

OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_INITIAL_TEMP): vol.All(
            vol.Coerce(int), vol.Range(min=MIN_TEMP, max=MAX_TEMP)
        ),
        vol.Optional(CONF_FAN_ON_CONNECT, default=False): bool,
    }
)


def _is_volcano(service_info: BluetoothServiceInfoBleak) -> bool:
    """Return whether an advertisement looks like a Volcano Hybrid."""
    return bool(service_info.name) and "VOLCANO" in service_info.name.upper()


class VolcanoHybridConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Volcano Hybrid."""

    VERSION = 2

    def __init__(self) -> None:
        """Initialise the flow."""
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered: dict[str, BluetoothServiceInfoBleak] = {}

    # -- automatic discovery ----------------------------------------------

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a device discovered by the Bluetooth integration."""
        await self.async_set_unique_id(format_mac(discovery_info.address))
        self._abort_if_unique_id_configured()

        if not _is_volcano(discovery_info):
            return self.async_abort(reason="not_supported")

        self._discovery_info = discovery_info
        self.context["title_placeholders"] = {"name": discovery_info.name}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the user to confirm a discovered device."""
        assert self._discovery_info is not None
        discovery_info = self._discovery_info

        if user_input is not None:
            if error := await self._async_test_connection(discovery_info.address):
                return self.async_show_form(
                    step_id="bluetooth_confirm",
                    errors={"base": error},
                    description_placeholders={"name": discovery_info.name},
                )
            return await self.async_step_options()

        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={"name": discovery_info.name},
        )

    # -- manual setup ------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick from the devices Home Assistant can already see."""
        errors: dict[str, str] = {}

        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(format_mac(address), raise_on_progress=False)
            self._abort_if_unique_id_configured()
            self._discovery_info = self._discovered[address]
            if error := await self._async_test_connection(address):
                errors["base"] = error
            else:
                return await self.async_step_options()

        current_addresses = self._async_current_ids()
        self._discovered = {
            service_info.address: service_info
            for service_info in async_discovered_service_info(
                self.hass, connectable=True
            )
            if _is_volcano(service_info)
            and format_mac(service_info.address) not in current_addresses
        }

        if not self._discovered:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {
                            address: f"{info.name} ({address})"
                            for address, info in self._discovered.items()
                        }
                    )
                }
            ),
            errors=errors,
        )

    # -- shared final step -------------------------------------------------

    async def async_step_options(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the optional startup behaviour and create the entry."""
        assert self._discovery_info is not None
        discovery_info = self._discovery_info

        if user_input is not None:
            data: dict[str, Any] = {CONF_ADDRESS: discovery_info.address}
            if (initial_temp := user_input.get(CONF_INITIAL_TEMP)) is not None:
                data[CONF_INITIAL_TEMP] = initial_temp
            data[CONF_FAN_ON_CONNECT] = user_input.get(CONF_FAN_ON_CONNECT, False)
            return self.async_create_entry(title=discovery_info.name, data=data)

        return self.async_show_form(
            step_id="options",
            data_schema=OPTIONS_SCHEMA,
            description_placeholders={
                "name": discovery_info.name,
                "address": discovery_info.address,
                "min_temp": str(MIN_TEMP),
                "max_temp": str(MAX_TEMP),
                "step": str(TEMP_STEP),
            },
        )

    async def _async_test_connection(self, address: str) -> str | None:
        """Prove we can talk to the device; return an error key on failure."""
        ble_device = async_ble_device_from_address(self.hass, address, connectable=True)
        if ble_device is None:
            return "not_in_range"

        device = VolcanoHybrid(address)
        device.set_ble_device(ble_device)
        try:
            await device.async_connect()
        except VolcanoConnectionError as err:
            _LOGGER.debug("Could not connect to %s: %s", address, err)
            return "cannot_connect"
        finally:
            await device.async_disconnect()
        return None
