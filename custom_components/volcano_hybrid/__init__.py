"""The Volcano Hybrid integration."""

from __future__ import annotations

import logging

from homeassistant.components.bluetooth import (
    BluetoothCallbackMatcher,
    BluetoothScanningMode,
    async_ble_device_from_address,
    async_register_callback,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.typing import ConfigType

from .const import CONF_FAN_ON_CONNECT, CONF_INITIAL_TEMP, CONF_MAC_ADDRESS, DOMAIN
from .coordinator import VolcanoConfigEntry, VolcanoDataUpdateCoordinator
from .services import async_setup_services
from .volcano import VolcanoConnectionError, VolcanoHybrid

_LOGGER = logging.getLogger(__name__)

# The integration is config-entry only; async_setup exists purely to register
# the actions, per the action-setup quality-scale rule.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration actions."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: VolcanoConfigEntry) -> bool:
    """Set up Volcano Hybrid from a config entry."""
    address: str = entry.data[CONF_ADDRESS]

    ble_device = async_ble_device_from_address(hass, address, connectable=True)
    if ble_device is None:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="not_in_range",
            translation_placeholders={"address": address},
        )

    device = VolcanoHybrid(address)
    device.set_ble_device(ble_device)

    try:
        await device.async_connect()
    except VolcanoConnectionError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"address": address, "error": str(err)},
        ) from err

    coordinator = VolcanoDataUpdateCoordinator(hass, entry, device)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    # Follow the device around the mesh: whichever adapter or ESPHome proxy
    # hears it most recently is the one reconnects should go through.
    entry.async_on_unload(
        async_register_callback(
            hass,
            coordinator.async_set_ble_device,
            BluetoothCallbackMatcher(address=address, connectable=True),
            BluetoothScanningMode.PASSIVE,
        )
    )

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    # Applied once per entry setup rather than per BLE reconnect: a dropped
    # link used to silently restart the fan and reset the target temperature.
    if entry.options.get(CONF_FAN_ON_CONNECT):
        await coordinator.async_turn_fan_on()
    if (initial_temp := entry.options.get(CONF_INITIAL_TEMP)) is not None:
        await coordinator.async_set_temperature(initial_temp)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_options_updated(
    hass: HomeAssistant, entry: VolcanoConfigEntry
) -> None:
    """Reload so changed startup behaviour takes effect immediately."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: VolcanoConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.async_shutdown_device()
    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate an old config entry.

    Version 1 stored the address under a bespoke ``mac_address`` key and keyed
    both the device identifier and every entity unique_id on the config entry
    id. Version 2 uses ``CONF_ADDRESS`` and the MAC, so the device survives
    being removed and re-added. Version 3 moves the startup behaviour out of
    ``data`` and into ``options``, so it can be changed without re-adding.

    The registries are updated in place, which keeps existing entity_ids -- and
    therefore existing automations, scripts and dashboards -- working.
    """
    if entry.version > 3:
        return False
    if entry.version == 3:
        return True
    if entry.version == 2:
        return _migrate_v2_to_v3(hass, entry)

    address = entry.data.get(CONF_ADDRESS) or entry.data.get(CONF_MAC_ADDRESS)
    if not address:
        _LOGGER.error("Cannot migrate config entry without an address")
        return False

    formatted = dr.format_mac(address)

    device_registry = dr.async_get(hass)
    if device := device_registry.async_get_device(
        identifiers={(DOMAIN, entry.entry_id)}
    ):
        device_registry.async_update_device(
            device.id, new_identifiers={(DOMAIN, formatted)}
        )
        _LOGGER.debug("Migrated device identifier to %s", formatted)

    entity_registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        prefix = f"{entry.entry_id}_"
        if not entity.unique_id.startswith(prefix):
            continue
        new_unique_id = f"{formatted}_{entity.unique_id[len(prefix) :]}"
        if entity_registry.async_get_entity_id(
            entity.domain, entity.platform, new_unique_id
        ):
            _LOGGER.debug(
                "Skipping %s, target unique_id already taken", entity.entity_id
            )
            continue
        entity_registry.async_update_entity(
            entity.entity_id, new_unique_id=new_unique_id
        )

    new_data = {
        key: value for key, value in entry.data.items() if key != CONF_MAC_ADDRESS
    }
    new_data[CONF_ADDRESS] = address

    hass.config_entries.async_update_entry(
        entry, data=new_data, unique_id=formatted, version=2
    )
    _LOGGER.info("Migrated Volcano Hybrid config entry to version 2")
    return _migrate_v2_to_v3(hass, entry)


@callback
def _migrate_v2_to_v3(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Move the startup behaviour from entry.data to entry.options."""
    behaviour_keys = (CONF_INITIAL_TEMP, CONF_FAN_ON_CONNECT)

    options = dict(entry.options)
    for key in behaviour_keys:
        # Version 1 stored an unset initial temperature as an explicit None.
        # Carrying that across would show as a populated-but-empty option.
        if key in entry.data and key not in options and entry.data[key] is not None:
            options[key] = entry.data[key]
    options.setdefault(CONF_FAN_ON_CONNECT, False)

    data = {
        key: value for key, value in entry.data.items() if key not in behaviour_keys
    }

    hass.config_entries.async_update_entry(entry, data=data, options=options, version=3)
    _LOGGER.info("Migrated Volcano Hybrid config entry to version 3")
    return True
