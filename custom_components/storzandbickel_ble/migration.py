"""Adopt config entries left behind under the integration's old domain.

Up to 3.x this integration was ``volcano_hybrid``, a domain another HACS
integration also uses. Renaming it would otherwise orphan every entity and
re-create them with new entity_ids, breaking the automations, scripts and
dashboards that reference them. Instead each old entry is imported under the
new domain, and its device and entity registry rows are moved across in place
before any platform is set up, so they keep their entity_ids, areas, labels,
aliases and voice exposure.
"""

from __future__ import annotations

from collections.abc import Callable
import logging
from typing import Any

from homeassistant.config_entries import (
    SOURCE_IMPORT,
    ConfigEntry,
    ConfigEntryState,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.loader import IntegrationNotFound, async_get_integration

from .const import (
    CONF_FAN_ON_CONNECT,
    CONF_INITIAL_TEMP,
    CONF_MAC_ADDRESS,
    DOMAIN,
    ISSUE_LEGACY_INSTALLED,
    LEGACY_DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# Our own manifest has always listed this codeowner. The other integration on
# the old domain does not, and its entries are never ours to take.
LEGACY_CODEOWNER = "@JustBeanie"

# Raised by Home Assistant for an entry whose integration is no longer
# installed -- exactly the state the rename leaves the old entries in.
CORE_NOT_FOUND_ISSUE = f"integration_not_found.{LEGACY_DOMAIN}"

BEHAVIOUR_KEYS = (CONF_INITIAL_TEMP, CONF_FAN_ON_CONNECT)


def is_own_legacy_entry(entry: ConfigEntry) -> bool:
    """Return whether an old-domain entry was created by this integration.

    Ours were version 1 with a bespoke ``mac_address`` key, then versions 2 and
    3. The other ``volcano_hybrid`` integration stores ``address`` at version
    1, so the two shapes never overlap.
    """
    if entry.version in (2, 3):
        return True
    return entry.version == 1 and CONF_MAC_ADDRESS in entry.data


def legacy_address(entry: ConfigEntry) -> str | None:
    """Return the Bluetooth address an old entry points at."""
    address: str | None = entry.data.get(CONF_ADDRESS) or entry.data.get(
        CONF_MAC_ADDRESS
    )
    return address


def legacy_options(entry: ConfigEntry) -> dict[str, Any]:
    """Return an old entry's startup behaviour as options.

    Version 3 already keeps it in options; versions 1 and 2 kept it in data,
    and version 1 stored an unset initial temperature as an explicit None,
    which must not come across as a populated-but-empty option.
    """
    options = dict(entry.options)
    for key in BEHAVIOUR_KEYS:
        if key not in options and entry.data.get(key) is not None:
            options[key] = entry.data[key]
    options.setdefault(CONF_FAN_ON_CONNECT, False)
    return options


def normalise_unique_id(unique_id: str, legacy_entry_id: str, mac: str) -> str:
    """Return the unique_id an old entity has under the new domain.

    Version 1 keyed unique_ids on the config entry id; every later version
    uses the formatted MAC, which is what the platforms here generate.
    """
    prefix = f"{legacy_entry_id}_"
    if unique_id.startswith(prefix):
        return f"{mac}_{unique_id[len(prefix) :]}"
    return unique_id


def _legacy_entries(hass: HomeAssistant) -> list[ConfigEntry]:
    """Return the old-domain entries this integration owns."""
    return [
        entry
        for entry in hass.config_entries.async_entries(LEGACY_DOMAIN)
        if is_own_legacy_entry(entry) and legacy_address(entry)
    ]


async def _async_legacy_code_installed(hass: HomeAssistant) -> bool:
    """Return whether our own pre-rename code is still in custom_components.

    HACS installs the renamed folder on update, but may leave the old one in
    place. Both would then try to hold the vaporizer's single connection.
    """
    try:
        integration = await async_get_integration(hass, LEGACY_DOMAIN)
    except IntegrationNotFound:
        return False
    return LEGACY_CODEOWNER in integration.manifest.get("codeowners", [])


async def async_import_legacy_entries(hass: HomeAssistant) -> None:
    """Start an import flow for every old entry, unless the old code remains."""
    if not (legacy := _legacy_entries(hass)):
        return

    if await _async_legacy_code_installed(hass):
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_LEGACY_INSTALLED,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_LEGACY_INSTALLED,
            translation_placeholders={"legacy_domain": LEGACY_DOMAIN},
        )
        return
    ir.async_delete_issue(hass, DOMAIN, ISSUE_LEGACY_INSTALLED)

    for entry in legacy:
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": SOURCE_IMPORT},
                data={
                    CONF_ADDRESS: legacy_address(entry),
                    "title": entry.title,
                    "options": legacy_options(entry),
                },
            ),
            f"{DOMAIN} import of {entry.entry_id}",
        )


async def async_adopt_legacy(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Move an old entry's registry rows onto ``entry``, then remove it.

    Runs before any platform is forwarded: a platform setting up first would
    register fresh entities, and the old ones could then only come across with
    a ``_2`` suffix.
    """
    mac = dr.format_mac(entry.data[CONF_ADDRESS])
    for legacy in _legacy_entries(hass):
        # Each new entry takes only the old entry for its own vaporizer, and
        # never one the old code still has running: its entities are live and
        # it holds the Bluetooth connection.
        if (
            dr.format_mac(legacy_address(legacy) or "") != mac
            or legacy.state is ConfigEntryState.LOADED
        ):
            continue
        old_entry_id = legacy.entry_id

        def _unique_id_for(row: er.RegistryEntry, old: str = old_entry_id) -> str:
            return normalise_unique_id(row.unique_id, old, mac)

        move_registry_rows(hass, legacy, entry, mac, _unique_id_for)
        await hass.config_entries.async_remove(legacy.entry_id)
        _LOGGER.info(
            "Adopted %s from the %s integration; entity_ids are unchanged",
            legacy.title,
            LEGACY_DOMAIN,
        )

    if not hass.config_entries.async_entries(LEGACY_DOMAIN):
        ir.async_delete_issue(hass, "homeassistant", CORE_NOT_FOUND_ISSUE)


def move_registry_rows(
    hass: HomeAssistant,
    source: ConfigEntry,
    entry: ConfigEntry,
    mac: str,
    unique_id_for: Callable[[er.RegistryEntry], str | None],
) -> None:
    """Re-home the device and the entity rows of ``source`` onto ``entry``.

    ``unique_id_for`` says what an entity's unique_id becomes here, or None
    for one with no counterpart; those are left behind and go when ``source``
    is removed.

    The order matters. Detaching the old entry from the device makes the
    entity registry delete that entry's entities on the spot, so they have to
    be moved off it first -- otherwise the platforms recreate them as new rows
    and every area, label and disabled flag on them is lost.
    """
    device_registry = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(device_registry, source.entry_id)
    for device in devices:
        identifier = (DOMAIN, mac)
        # Only take the identifier if nothing holds it yet; a collision would
        # raise and leave the adoption half done.
        new_identifiers = (
            {identifier}
            if device_registry.async_get_device(identifiers={identifier}) is None
            else device.identifiers
        )
        device_registry.async_update_device(
            device.id,
            add_config_entry_id=entry.entry_id,
            new_identifiers=new_identifiers,
        )

    entity_registry = er.async_get(hass)
    for row in er.async_entries_for_config_entry(entity_registry, source.entry_id):
        if (new_unique_id := unique_id_for(row)) is None:
            _LOGGER.debug("Leaving %s behind, it has no counterpart", row.entity_id)
            continue
        if entity_registry.async_get_entity_id(row.domain, DOMAIN, new_unique_id):
            _LOGGER.debug("Skipping %s, its unique_id is taken", row.entity_id)
            continue
        # The source is not loaded, so whatever state this entity has is
        # Home Assistant's restored "unavailable" placeholder -- and the
        # registry refuses to move an entity that has a state.
        hass.states.async_remove(row.entity_id)
        # Only a changed unique_id may be passed: the registry treats an
        # unchanged one as a collision with the row being moved.
        changes: dict[str, Any] = (
            {"new_unique_id": new_unique_id} if new_unique_id != row.unique_id else {}
        )
        entity_registry.async_update_entity_platform(
            row.entity_id, DOMAIN, new_config_entry_id=entry.entry_id, **changes
        )

    for device in devices:
        device_registry.async_update_device(
            device.id, remove_config_entry_id=source.entry_id
        )
