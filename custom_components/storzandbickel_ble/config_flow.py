"""Config flow for the Storz & Bickel BLE integration."""

from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_ble_device_from_address,
    async_discovered_service_info,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.helpers.device_registry import format_mac
import voluptuous as vol

from .ble_device import DeviceConnectionError
from .const import (
    CONF_AUTO_CONNECT,
    CONF_AUTO_CONNECT_DELAY,
    CONF_DELAYED_RECONNECT_DELAY,
    CONF_FAN_ON_CONNECT,
    CONF_INITIAL_TEMP,
    CONF_MODEL,
    DEFAULT_AUTO_CONNECT_DELAY,
    DEFAULT_DELAYED_RECONNECT_DELAY,
    DEFAULT_NAME,
    DOMAIN,
    MAX_TEMP,
    MIN_TEMP,
    TEMP_STEP,
)
from .families import FAMILY_NAMES, Family, create_device, detect_family

_LOGGER = logging.getLogger(__name__)

OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_INITIAL_TEMP): vol.All(
            vol.Coerce(int), vol.Range(min=MIN_TEMP, max=MAX_TEMP)
        ),
        vol.Optional(CONF_FAN_ON_CONNECT, default=False): bool,
    }
)

# The connection timing is only worth changing once the device is set up, so
# it is offered in the options flow and not while adding the device.
CONNECTION_SCHEMA = OPTIONS_SCHEMA.extend(
    {
        vol.Optional(
            CONF_AUTO_CONNECT_DELAY, default=DEFAULT_AUTO_CONNECT_DELAY
        ): vol.All(vol.Coerce(float), vol.Range(min=0, max=30)),
        vol.Optional(
            CONF_DELAYED_RECONNECT_DELAY, default=DEFAULT_DELAYED_RECONNECT_DELAY
        ): vol.All(vol.Coerce(float), vol.Range(min=1, max=120)),
    }
)

# Six colon-separated hex pairs, any case.
MAC_PATTERN = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
MANUAL_ENTRY = "manual"


def _family_of(service_info: BluetoothServiceInfoBleak) -> Family | None:
    """Return which Storz & Bickel family an advertisement is, if any."""
    return detect_family(service_info.name, service_info.service_uuids)


def _clean_options(user_input: dict[str, Any]) -> dict[str, Any]:
    """Normalise the options form, dropping a blank initial temperature."""
    options: dict[str, Any] = {
        CONF_FAN_ON_CONNECT: user_input.get(CONF_FAN_ON_CONNECT, False)
    }
    if (initial_temp := user_input.get(CONF_INITIAL_TEMP)) is not None:
        options[CONF_INITIAL_TEMP] = initial_temp
    for key in (CONF_AUTO_CONNECT_DELAY, CONF_DELAYED_RECONNECT_DELAY):
        if key in user_input:
            options[key] = user_input[key]
    return options


class StorzBickelOptionsFlow(OptionsFlow):
    """Let the startup behaviour be changed without re-adding the device."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and save the options."""
        if user_input is not None:
            options = _clean_options(user_input)
            # Set by its switch, not this form, so it must not be dropped.
            if CONF_AUTO_CONNECT in self.config_entry.options:
                options[CONF_AUTO_CONNECT] = self.config_entry.options[
                    CONF_AUTO_CONNECT
                ]
            return self.async_create_entry(data=options)

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                CONNECTION_SCHEMA, self.config_entry.options
            ),
            description_placeholders={
                "min_temp": str(MIN_TEMP),
                "max_temp": str(MAX_TEMP),
                "step": str(TEMP_STEP),
            },
        )


class StorzBickelConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for a Storz & Bickel device."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow."""
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered: dict[str, BluetoothServiceInfoBleak] = {}
        # What the entry is created for; from discovery or typed by hand.
        self._address: str | None = None
        self._name = DEFAULT_NAME
        self._family = Family.VOLCANO_HYBRID

    # -- automatic discovery ----------------------------------------------

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a device discovered by the Bluetooth integration."""
        await self.async_set_unique_id(format_mac(discovery_info.address))

        # A Bluetooth address never changes, so there is no network info to
        # update. What is worth acting on is a configured entry sitting in
        # setup_retry: the device is clearly back, so retry now instead of
        # waiting out the coordinator's backoff.
        if entry := self.hass.config_entries.async_entry_for_domain_unique_id(
            DOMAIN, self.unique_id or ""
        ):
            if entry.state is ConfigEntryState.SETUP_RETRY:
                self.hass.config_entries.async_schedule_reload(entry.entry_id)
            return self.async_abort(reason="already_configured")

        if (family := _family_of(discovery_info)) is None:
            return self.async_abort(reason="not_supported")
        self._family = family

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

    # -- adoption of a pre-rename entry -------------------------------------

    async def async_step_import(self, import_data: dict[str, Any]) -> ConfigFlowResult:
        """Create the entry an old-domain entry is adopted into.

        Started by migration.py. The device and entity rows move across when
        this entry sets up; here only the entry itself is made, carrying the
        old title and startup options.
        """
        address: str = import_data[CONF_ADDRESS]
        await self.async_set_unique_id(format_mac(address))
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=import_data["title"],
            data={CONF_ADDRESS: address},
            options=import_data["options"],
        )

    # -- manual setup ------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick from the devices Home Assistant can already see."""
        errors: dict[str, str] = {}

        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            if address == MANUAL_ENTRY:
                return await self.async_step_manual()
            await self.async_set_unique_id(format_mac(address), raise_on_progress=False)
            self._abort_if_unique_id_configured()
            self._discovery_info = self._discovered[address]
            self._family = _family_of(self._discovery_info) or Family.VOLCANO_HYBRID
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
            if _family_of(service_info) is not None
            and format_mac(service_info.address) not in current_addresses
        }

        # Nothing heard yet is no reason to give up: the address can be typed.
        if not self._discovered:
            return await self.async_step_manual()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {
                            **{
                                address: f"{info.name} ({address})"
                                for address, info in self._discovered.items()
                            },
                            MANUAL_ENTRY: "Enter an address manually",
                        }
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a vaporizer by its Bluetooth address.

        One that no adapter can hear yet is still accepted: the entry waits in
        setup-retry, and the advertisement watcher brings it up the moment the
        vaporizer is switched on in range. One that can be heard has to accept
        a connection first, the same as a discovered one.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            address = user_input[CONF_ADDRESS].strip()
            self._family = Family(user_input[CONF_MODEL])
            if not MAC_PATTERN.match(address):
                errors[CONF_ADDRESS] = "invalid_address"
            else:
                address = address.upper()
                await self.async_set_unique_id(
                    format_mac(address), raise_on_progress=False
                )
                self._abort_if_unique_id_configured()
                visible = async_ble_device_from_address(
                    self.hass, address, connectable=True
                )
                if visible is not None and (
                    error := await self._async_test_connection(address)
                ):
                    errors["base"] = error
                else:
                    self._address = address
                    self._name = FAMILY_NAMES[self._family]
                    return await self.async_step_options()

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): str,
                    # Nothing to detect it from without an advertisement.
                    vol.Required(CONF_MODEL, default=Family.VOLCANO_HYBRID): vol.In(
                        {family.value: FAMILY_NAMES[family] for family in Family}
                    ),
                }
            ),
            errors=errors,
        )

    # -- shared final step -------------------------------------------------

    async def async_step_options(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the optional startup behaviour and create the entry."""
        if self._discovery_info is not None:
            self._address = self._discovery_info.address
            self._name = self._discovery_info.name
        assert self._address is not None

        if user_input is not None:
            return self.async_create_entry(
                title=self._name,
                data={CONF_ADDRESS: self._address, CONF_MODEL: self._family.value},
                options=_clean_options(user_input),
            )

        return self.async_show_form(
            step_id="options",
            data_schema=OPTIONS_SCHEMA,
            description_placeholders={
                "name": self._name,
                "address": self._address,
                "min_temp": str(MIN_TEMP),
                "max_temp": str(MAX_TEMP),
                "step": str(TEMP_STEP),
            },
        )

    # -- reconfigure -------------------------------------------------------

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-test the stored address.

        A device's identity is a fixed MAC, so there is nothing here to point
        somewhere else. What is useful is confirming the vaporizer is reachable
        right now, which is otherwise only observable by watching the log.
        """
        entry = self._get_reconfigure_entry()
        address: str = entry.data[CONF_ADDRESS]
        self._family = Family(entry.data.get(CONF_MODEL, Family.VOLCANO_HYBRID))

        if user_input is not None:
            if error := await self._async_test_connection(address):
                return self.async_show_form(
                    step_id="reconfigure",
                    errors={"base": error},
                    description_placeholders={"name": entry.title, "address": address},
                )
            return self.async_update_reload_and_abort(entry, data_updates={})

        return self.async_show_form(
            step_id="reconfigure",
            description_placeholders={"name": entry.title, "address": address},
        )

    # -- options -----------------------------------------------------------

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> StorzBickelOptionsFlow:
        """Return the options flow."""
        return StorzBickelOptionsFlow()

    async def _async_test_connection(self, address: str) -> str | None:
        """Prove we can talk to the device; return an error key on failure."""
        ble_device = async_ble_device_from_address(self.hass, address, connectable=True)
        if ble_device is None:
            return "not_in_range"

        device = create_device(self._family, address)
        device.set_ble_device(ble_device)
        try:
            await device.async_connect()
        except DeviceConnectionError as err:
            _LOGGER.debug("Could not connect to %s: %s", address, err)
            return "cannot_connect"
        finally:
            await device.async_disconnect()
        return None
