"""Actions for the Storz & Bickel BLE integration.

Entity services cover the simple commands; these are the ones a script needs
that entities cannot give it: set a temperature and wait until the vaporizer
says it is there, connect and wait for the link, and hand the vaporizer back
with heat and air confirmed off. Each fails loudly, so a script stops instead
of carrying on past a step that did not happen.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_DEVICE_ID, ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.service import verify_domain_control
import voluptuous as vol

from .const import DOMAIN, MAX_TEMP, MIN_TEMP
from .coordinator import VolcanoConfigEntry, VolcanoDataUpdateCoordinator

SERVICE_SET_TEMPERATURE = "set_temperature"
SERVICE_CONNECT = "connect"
SERVICE_DISCONNECT = "disconnect"

ATTR_WAIT_UNTIL_REACHED = "wait_until_reached"
ATTR_WAIT = "wait"
ATTR_TIMEOUT = "timeout"

DEVICE_SCHEMA = vol.Schema({vol.Optional(ATTR_DEVICE_ID): cv.string})

SET_TEMPERATURE_SCHEMA = DEVICE_SCHEMA.extend(
    {
        vol.Required(ATTR_TEMPERATURE): vol.All(
            vol.Coerce(float), vol.Range(min=MIN_TEMP, max=MAX_TEMP)
        ),
        vol.Optional(ATTR_WAIT_UNTIL_REACHED, default=False): cv.boolean,
        # A cold block takes several minutes to reach the top of the range.
        vol.Optional(ATTR_TIMEOUT, default=600): vol.All(
            vol.Coerce(float), vol.Range(min=1, max=3600)
        ),
    }
)

CONNECT_SCHEMA = DEVICE_SCHEMA.extend(
    {
        vol.Optional(ATTR_WAIT, default=True): cv.boolean,
        vol.Optional(ATTR_TIMEOUT, default=60): vol.All(
            vol.Coerce(float), vol.Range(min=1, max=900)
        ),
    }
)


def _coordinator(
    hass: HomeAssistant, call: ServiceCall
) -> VolcanoDataUpdateCoordinator:
    """Return the coordinator the call is for.

    With a single vaporizer the device can be left out; with more, the call
    has to say which, rather than guess.
    """
    loaded: list[VolcanoConfigEntry] = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]

    if (device_id := call.data.get(ATTR_DEVICE_ID)) is None:
        if len(loaded) == 1:
            return loaded[0].runtime_data
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="no_device" if not loaded else "ambiguous_device",
        )

    device = dr.async_get(hass).async_get(device_id)
    for entry in loaded:
        if device is not None and entry.entry_id in device.config_entries:
            return entry.runtime_data
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="invalid_device",
        translation_placeholders={"device_id": device_id},
    )


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the actions. Called once, from async_setup."""

    async def _set_temperature(call: ServiceCall) -> None:
        coordinator = _coordinator(hass, call)
        await coordinator.async_set_temperature(call.data[ATTR_TEMPERATURE])
        if call.data[ATTR_WAIT_UNTIL_REACHED]:
            await coordinator.async_wait_until_reached(call.data[ATTR_TIMEOUT])

    async def _connect(call: ServiceCall) -> None:
        coordinator = _coordinator(hass, call)
        await coordinator.async_set_auto_connect(True)
        if call.data[ATTR_WAIT]:
            await coordinator.async_wait_connected(call.data[ATTR_TIMEOUT])

    async def _disconnect(call: ServiceCall) -> None:
        await _coordinator(hass, call).async_release()

    handlers: tuple[
        tuple[str, Callable[[ServiceCall], Awaitable[None]], vol.Schema], ...
    ] = (
        (SERVICE_SET_TEMPERATURE, _set_temperature, SET_TEMPERATURE_SCHEMA),
        (SERVICE_CONNECT, _connect, CONNECT_SCHEMA),
        (SERVICE_DISCONNECT, _disconnect, DEVICE_SCHEMA),
    )
    for name, handler, schema in handlers:
        # Read-only users may not drive the vaporizer, the same as its
        # entities: a user needs control of this integration's entities.
        guarded: Callable[[ServiceCall], Any] = verify_domain_control(DOMAIN)(handler)
        hass.services.async_register(DOMAIN, name, guarded, schema=schema)
