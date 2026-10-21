"""Diagnostics for the Volcano Hybrid integration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.bluetooth import (
    async_address_present,
    async_last_service_info,
)
from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant

from .coordinator import VolcanoConfigEntry

# Hardware identifiers. Diagnostics get pasted into public issues.
TO_REDACT = {CONF_ADDRESS, "address", "serial_number", "source"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: VolcanoConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    address = coordinator.address

    service_info = async_last_service_info(hass, address, connectable=True)

    # habluetooth falls back to the address when an advertisement carries no
    # local name, so reporting the name verbatim can leak the MAC past
    # redaction. Drop it in that case rather than redacting a real name, which
    # is useful for spotting a device advertising under an unexpected name.
    advertised_name: str | None = service_info.name if service_info else None
    if advertised_name and advertised_name.upper() == address.upper():
        advertised_name = None

    return async_redact_data(
        {
            "entry": {
                "version": entry.version,
                "data": dict(entry.data),
                "options": dict(entry.options),
            },
            "coordinator": {
                "last_update_success": coordinator.last_update_success,
                "consecutive_failures": coordinator.consecutive_failures,
                "update_interval": str(coordinator.update_interval),
            },
            # The raw wire values are the useful part: every decoding bug found
            # so far was diagnosed from these bytes rather than from the states.
            "state": asdict(coordinator.data) if coordinator.data else None,
            "connection": {
                "connected": coordinator.device.connected,
                "address_present": async_address_present(
                    hass, address, connectable=True
                ),
                "rssi": service_info.rssi if service_info else None,
                "source": service_info.source if service_info else None,
                "advertised_name": advertised_name,
                "manufacturer_data_ids": sorted(service_info.manufacturer_data)
                if service_info
                else [],
            },
        },
        TO_REDACT,
    )
