"""The Storz & Bickel BLE integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.bluetooth import (
    BluetoothCallbackMatcher,
    BluetoothChange,
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
    async_ble_device_from_address,
    async_register_callback,
)
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_AUTO_CONNECT,
    CONF_FAN_ON_CONNECT,
    CONF_INITIAL_TEMP,
    DOMAIN,
    MANUFACTURER_ID,
)
from .coordinator import VolcanoConfigEntry, VolcanoDataUpdateCoordinator
from .migration import async_adopt_legacy, async_import_legacy_entries
from .volcano import VolcanoHybrid

_LOGGER = logging.getLogger(__name__)

# The integration is config-entry only. This rejects a stray `storzandbickel_ble:`
# block in configuration.yaml rather than silently ignoring it.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.UPDATE,
]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Watch for a Volcano coming back while an entry is stuck retrying setup.

    Setup needs the vaporiser to be reachable, so unplugging it and plugging it
    back in around a restart leaves the entry in ``SETUP_RETRY`` waiting out an
    exponential backoff -- observed at 29 minutes on a real instance. Nothing
    else notices the device return: the per-entry advertisement callback below
    is torn down along with the failed setup, so the watcher has to live at
    component scope, above any individual entry.
    """

    @callback
    def _async_device_seen(
        service_info: BluetoothServiceInfoBleak, change: BluetoothChange
    ) -> None:
        """Retry any entry for this address the moment it advertises again."""
        seen = dr.format_mac(service_info.address)
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry.state is not ConfigEntryState.SETUP_RETRY:
                continue
            address = entry.data.get(CONF_ADDRESS)
            if address and dr.format_mac(address) == seen:
                hass.config_entries.async_schedule_reload(entry.entry_id)

    async_register_callback(
        hass,
        _async_device_seen,
        BluetoothCallbackMatcher(manufacturer_id=MANUFACTURER_ID, connectable=True),
        BluetoothScanningMode.PASSIVE,
    )

    # Entries left under the pre-rename domain are brought across before
    # anything else, so their entity_ids survive.
    await async_import_legacy_entries(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: VolcanoConfigEntry) -> bool:
    """Set up Volcano Hybrid from a config entry."""
    address: str = entry.data[CONF_ADDRESS]

    # Before anything can fail or register an entity: adoption has to land on
    # an entry with no entities of its own yet.
    await async_adopt_legacy(hass, entry)

    ble_device = async_ble_device_from_address(hass, address, connectable=True)
    # With auto-connect off nobody expects a link, so an absent vaporizer is
    # not a reason to hold setup back.
    if ble_device is None and entry.options.get(CONF_AUTO_CONNECT, True):
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="not_in_range",
            translation_placeholders={"address": address},
        )

    device = VolcanoHybrid(address)
    device.set_ble_device(ble_device)

    # The first refresh below connects through _ensure_client, so connecting
    # here as well was a second round trip and a second, differently worded
    # failure path for the same problem.
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

    coordinator.reload_options = _reload_relevant(entry)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    # Applied once per entry setup rather than per BLE reconnect: a dropped
    # link used to silently restart the fan and reset the target temperature.
    if entry.options.get(CONF_FAN_ON_CONNECT):
        await coordinator.async_turn_fan_on()
    if (initial_temp := entry.options.get(CONF_INITIAL_TEMP)) is not None:
        await coordinator.async_set_temperature(initial_temp)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _reload_relevant(entry: VolcanoConfigEntry) -> dict[str, Any]:
    """Return the options whose change needs a reload to take effect.

    Auto-connect is applied live by its switch; reloading for it would drop
    and rebuild every entity just to flip one flag.
    """
    return {k: v for k, v in entry.options.items() if k != CONF_AUTO_CONNECT}


async def _async_options_updated(
    hass: HomeAssistant, entry: VolcanoConfigEntry
) -> None:
    """Reload so changed startup behaviour takes effect immediately."""
    if entry.runtime_data.reload_options == _reload_relevant(entry):
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: VolcanoConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.async_shutdown_device()
    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Refuse entries from a newer version than this code understands.

    Pre-rename entries are not migrated here: they live under the old domain
    and are adopted by migration.py instead.
    """
    return entry.version <= 1
