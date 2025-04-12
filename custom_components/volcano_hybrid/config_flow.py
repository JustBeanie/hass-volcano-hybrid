"""Config flow for Volcano Hybrid integration."""
import logging
from typing import Any, Dict, List, Optional

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.bluetooth import async_discovered_service_info
from homeassistant.const import CONF_NAME
from homeassistant.data_entry_flow import FlowResult

from bleak import BleakScanner
from bleak.exc import BleakError

from .const import (
    CONF_FAN_ON_CONNECT, CONF_INITIAL_TEMP, CONF_MAC_ADDRESS, 
    DEFAULT_NAME, DOMAIN, MIN_TEMP, MAX_TEMP, TEMP_STEP, VERSION
)
from .volcano import VolcanoHybrid

_LOGGER = logging.getLogger(__name__)

class VolcanoHybridConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Volcano Hybrid."""

    VERSION = 1
    
    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovered_devices = []
        self._selected_device = None
        self._device_info = {}

    async def async_step_user(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle the initial step."""
        _LOGGER.debug("Starting config flow for Volcano Hybrid version %s", VERSION)
        if user_input is not None:
            if user_input.get("discovery_method") == "scan":
                return await self.async_step_discovery()
            else:
                return await self.async_step_manual()
                
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("discovery_method", default="scan"): vol.In(
                        {
                            "scan": "Scan for devices",
                            "manual": "Enter MAC address manually"
                        }
                    ),
                }
            ),
        )
        
    async def async_step_discovery(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle discovery step."""
        errors = {}
        
        if user_input is not None:
            if user_input.get("manual_entry"):
                # User wants to enter a MAC address manually
                return await self.async_step_manual()
            elif user_input.get("rescan"):
                # User wants to rescan for devices
                self._discovered_devices = []  # Clear previous results
                # Don't return here, continue to scan below
            elif user_input.get(CONF_MAC_ADDRESS):
                # User selected a device from the dropdown
                self._selected_device = next(
                    (device for device in self._discovered_devices 
                     if device["address"] == user_input[CONF_MAC_ADDRESS]),
                    None
                )
                
                if self._selected_device:
                    # Try to connect to the selected device
                    volcano = VolcanoHybrid(self._selected_device["address"])
                    try:
                        connected = await volcano.connect()
                        if connected:
                            # Disconnect after validation
                            await volcano.disconnect()
                            
                            # Check if device already configured
                            await self.async_set_unique_id(self._selected_device["address"])
                            self._abort_if_unique_id_configured()
                            
                            # Store device info for the next step
                            self._device_info = {
                                CONF_MAC_ADDRESS: self._selected_device["address"],
                                CONF_NAME: self._selected_device["name"],
                            }
                            
                            # Go to info step
                            return await self.async_step_info()
                        else:
                            errors["base"] = "cannot_connect"
                    except Exception as e:
                        _LOGGER.error(f"Error connecting to device: {e}")
                        errors["base"] = "cannot_connect"
                else:
                    errors["base"] = "no_devices_selected"
            else:
                errors["base"] = "no_devices_selected"
                
        # Discover Volcano devices
        try:
            self._discovered_devices = await self._discover_volcano_devices()
        except Exception as e:
            _LOGGER.error(f"Error discovering devices: {e}")
            errors["base"] = "discovery_error"
            self._discovered_devices = []
        
        if not self._discovered_devices:
            return self.async_show_form(
                step_id="discovery",
                data_schema=vol.Schema(
                    {
                        vol.Optional("rescan", default=False): bool,
                        vol.Optional("manual_entry", default=False): bool,
                    }
                ),
                errors={"base": "no_devices_found"},
                description_placeholders={"count": "0"},
            )
        
        device_options = {
            device["address"]: f"{device['name']} ({device['address']})" 
            for device in self._discovered_devices
        }
        
        return self.async_show_form(
            step_id="discovery",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_MAC_ADDRESS): vol.In(device_options),
                    vol.Optional("rescan", default=False): bool,
                    vol.Optional("manual_entry", default=False): bool,
                }
            ),
            errors=errors,
            description_placeholders={"count": str(len(self._discovered_devices))},
        )

    async def async_step_manual(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle manual address entry."""
        errors = {}
        
        if user_input is not None:
            # Validate the MAC address format
            if self._validate_mac(user_input[CONF_MAC_ADDRESS]):
                # Try to connect to the device
                volcano = VolcanoHybrid(user_input[CONF_MAC_ADDRESS])
                try:
                    connected = await volcano.connect()
                    if connected:
                        # Disconnect after validation
                        await volcano.disconnect()
                        
                        # Check if device already configured
                        await self.async_set_unique_id(user_input[CONF_MAC_ADDRESS])
                        self._abort_if_unique_id_configured()
                        
                        # Store device info for the next step
                        self._device_info = {
                            CONF_MAC_ADDRESS: user_input[CONF_MAC_ADDRESS],
                            CONF_NAME: user_input.get(CONF_NAME, DEFAULT_NAME),
                        }
                        
                        # Go to info step
                        return await self.async_step_info()
                    else:
                        errors["base"] = "cannot_connect"
                except Exception as e:
                    _LOGGER.error(f"Error connecting to device: {e}")
                    errors["base"] = "cannot_connect"
            else:
                errors[CONF_MAC_ADDRESS] = "invalid_mac"

        # Show form for manual MAC address entry
        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_MAC_ADDRESS): str,
                    vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                }
            ),
            errors=errors,
        )
        
    async def async_step_info(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle additional device info step."""
        errors = {}
        
        # If we don't have device info stored, abort
        if not self._device_info or CONF_MAC_ADDRESS not in self._device_info:
            _LOGGER.error("No device info available for info step")
            return self.async_abort(reason="no_device_info")
            
        mac_address = self._device_info[CONF_MAC_ADDRESS]
        name = self._device_info.get(CONF_NAME, DEFAULT_NAME)
        
        if user_input is not None:
            try:
                _LOGGER.debug(f"Creating entry with: MAC={mac_address}, Name={name}, Input={user_input}")
                # Create entry with all the collected information
                return self.async_create_entry(
                    title=name,
                    data={
                        CONF_MAC_ADDRESS: mac_address,
                        CONF_NAME: name,
                        CONF_INITIAL_TEMP: user_input.get(CONF_INITIAL_TEMP),
                        CONF_FAN_ON_CONNECT: user_input.get(CONF_FAN_ON_CONNECT, False),
                    },
                )
            except Exception as e:
                _LOGGER.error(f"Error creating entry: {e}")
                errors["base"] = "unknown_error"

        # Show form for additional configuration
        return self.async_show_form(
            step_id="info",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_INITIAL_TEMP): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_TEMP, max=MAX_TEMP)
                    ),
                    vol.Optional(CONF_FAN_ON_CONNECT, default=False): bool,
                }
            ),
            errors=errors,
            description_placeholders={"name": name, "mac": mac_address},
        )

    async def async_step_bluetooth(self, discovery_info) -> FlowResult:
        """Handle bluetooth discovery."""
        try:
            # Check if the discovered device is a Volcano
            if discovery_info.name and "VOLCANO" in discovery_info.name.upper():
                await self.async_set_unique_id(discovery_info.address)
                self._abort_if_unique_id_configured()
                
                # Store device info for the next step
                self._device_info = {
                    CONF_MAC_ADDRESS: discovery_info.address,
                    CONF_NAME: discovery_info.name,
                }
                
                # Jump to the info step
                return await self.async_step_info()
        except Exception as e:
            _LOGGER.error(f"Error in bluetooth discovery step: {e}")
            return self.async_abort(reason="discovery_error")
            
        # Not a Volcano device or couldn't determine
        return self.async_abort(reason="not_volcano_device")

    def _validate_mac(self, mac: str) -> bool:
        """Validate MAC address format."""
        # Simple validation - could be improved
        parts = mac.split(":")
        if len(parts) != 6:
            return False
        
        for part in parts:
            if len(part) != 2:
                return False
            try:
                int(part, 16)
            except ValueError:
                return False
        
        return True
        
    async def _discover_volcano_devices(self) -> List[Dict[str, Any]]:
        """Discover Volcano devices using BleakScanner."""
        discovered_devices = []
        
        try:
            _LOGGER.debug("Starting Volcano device discovery")
            devices = await BleakScanner.discover(timeout=10.0)  # Set a reasonable timeout
            
            for device in devices:
                if device.name and "VOLCANO" in device.name.upper():
                    _LOGGER.debug(f"Found Volcano device: {device.name}, MAC: {device.address}")
                    discovered_devices.append({
                        "name": device.name,
                        "address": device.address,
                    })
                    
            _LOGGER.debug(f"Discovery complete. Found {len(discovered_devices)} Volcano devices")
            return discovered_devices
        except BleakError as e:
            _LOGGER.error(f"BleakError during device discovery: {e}")
            raise
        except Exception as e:
            _LOGGER.error(f"Error during device discovery: {e}")
            raise
