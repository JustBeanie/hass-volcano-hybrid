"""Tests for adopting entries left under the pre-rename domain."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntryState
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.loader import IntegrationNotFound
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.storzandbickel_ble.const import (
    CONF_FAN_ON_CONNECT,
    CONF_INITIAL_TEMP,
    CONF_MAC_ADDRESS,
    DOMAIN,
    ISSUE_LEGACY_INSTALLED,
    LEGACY_DOMAIN,
)
from custom_components.storzandbickel_ble.migration import (
    CORE_NOT_FOUND_ISSUE,
    LEGACY_CODEOWNER,
    async_import_legacy_entries,
    is_own_legacy_entry,
    legacy_options,
    normalise_unique_id,
)

from .conftest import ADDRESS, DEVICE_NAME, FORMATTED_MAC

# (entity domain, key, object_id) for a few of the entities a real install has.
LEGACY_ENTITIES = (
    ("climate", "climate", "s_b_volcano_h"),
    ("switch", "heater", "s_b_volcano_h_heater"),
    ("sensor", "temperature", "s_b_volcano_h_temperature"),
)

INTEGRATION = "custom_components.storzandbickel_ble.migration.async_get_integration"


def _legacy_unique_id(entry: MockConfigEntry, key: str) -> str:
    """Return the unique_id the old integration gave an entity."""
    prefix = entry.entry_id if entry.version == 1 else FORMATTED_MAC
    return f"{prefix}_{key}"


def _register_legacy(hass: HomeAssistant, entry: MockConfigEntry) -> dr.DeviceEntry:
    """Recreate what an old install leaves in the registries after the rename."""
    entry.add_to_hass(hass)
    identifier = entry.entry_id if entry.version == 1 else FORMATTED_MAC
    area = ar.async_get(hass).async_create("Living Room")
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(LEGACY_DOMAIN, identifier)},
        name=DEVICE_NAME,
    )
    device = device_registry.async_update_device(device.id, area_id=area.id)
    assert device is not None

    entity_registry = er.async_get(hass)
    for domain, key, object_id in LEGACY_ENTITIES:
        row = entity_registry.async_get_or_create(
            domain,
            LEGACY_DOMAIN,
            _legacy_unique_id(entry, key),
            suggested_object_id=object_id,
            config_entry=entry,
            device_id=device.id,
        )
        # Customisation that only survives if the row itself is moved.
        entity_registry.async_update_entity(row.entity_id, labels={"volcano"})
        # What Home Assistant writes for an entity whose integration is gone.
        hass.states.async_set(
            f"{domain}.{object_id}", "unavailable", {"restored": True}
        )
    return device


async def _start(hass: HomeAssistant) -> None:
    """Set the integration up the way Home Assistant does at startup."""
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()


@pytest.mark.parametrize(
    "fixture", ["legacy_v1_entry", "legacy_v2_entry", "legacy_v3_entry"]
)
async def test_legacy_entry_is_adopted_with_its_entity_ids(
    hass: HomeAssistant,
    mock_bluetooth: AsyncMock,
    request: pytest.FixtureRequest,
    fixture: str,
) -> None:
    """Every old-entry shape comes across keeping entity_ids, device and area."""
    legacy: MockConfigEntry = request.getfixturevalue(fixture)
    device = _register_legacy(hass, legacy)
    entity_registry = er.async_get(hass)
    row_ids = {
        f"{domain}.{object_id}": entity_registry.async_get(f"{domain}.{object_id}").id
        for domain, _key, object_id in LEGACY_ENTITIES
    }

    await _start(hass)

    assert hass.config_entries.async_entries(LEGACY_DOMAIN) == []
    (entry,) = hass.config_entries.async_entries(DOMAIN)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.unique_id == FORMATTED_MAC
    assert entry.title == DEVICE_NAME
    assert entry.data == {CONF_ADDRESS: ADDRESS}

    moved = dr.async_get(hass).async_get(device.id)
    assert moved is not None
    assert moved.identifiers == {(DOMAIN, FORMATTED_MAC)}
    assert moved.config_entries == {entry.entry_id}
    assert moved.area_id == device.area_id

    for domain, key, object_id in LEGACY_ENTITIES:
        entity_id = f"{domain}.{object_id}"
        row = entity_registry.async_get(entity_id)
        assert row is not None, f"{entity_id} was renamed by the adoption"
        # The same row, moved -- not a new one that happened to get the id.
        assert row.id == row_ids[entity_id]
        assert row.labels == {"volcano"}
        assert row.platform == DOMAIN
        assert row.config_entry_id == entry.entry_id
        assert row.unique_id == f"{FORMATTED_MAC}_{key}"
        assert hass.states.get(entity_id).state != "unavailable"
        # Nothing was duplicated under a _2 suffix.
        assert entity_registry.async_get(f"{entity_id}_2") is None


async def test_startup_options_survive_adoption(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, legacy_v2_entry: MockConfigEntry
) -> None:
    """Behaviour kept in data by a version 2 entry arrives as options."""
    _register_legacy(hass, legacy_v2_entry)

    await _start(hass)

    (entry,) = hass.config_entries.async_entries(DOMAIN)
    assert entry.options == {CONF_FAN_ON_CONNECT: True, CONF_INITIAL_TEMP: 180}


async def test_a_disabled_entity_stays_disabled(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, legacy_v3_entry: MockConfigEntry
) -> None:
    """A user's choice to disable an entity is carried across."""
    _register_legacy(hass, legacy_v3_entry)
    entity_registry = er.async_get(hass)
    hass.states.async_remove("switch.s_b_volcano_h_heater")
    entity_registry.async_update_entity(
        "switch.s_b_volcano_h_heater", disabled_by=er.RegistryEntryDisabler.USER
    )

    await _start(hass)

    row = entity_registry.async_get("switch.s_b_volcano_h_heater")
    assert row is not None
    assert row.platform == DOMAIN
    assert row.disabled_by is er.RegistryEntryDisabler.USER


async def test_old_code_still_installed_raises_a_repair(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, legacy_v3_entry: MockConfigEntry
) -> None:
    """Both copies would fight over the one connection, so nothing moves."""
    _register_legacy(hass, legacy_v3_entry)
    old_code = MagicMock(manifest={"codeowners": [LEGACY_CODEOWNER]})

    with patch(INTEGRATION, AsyncMock(return_value=old_code)):
        await _start(hass)

    assert hass.config_entries.async_entries(DOMAIN) == []
    assert hass.config_entries.async_entries(LEGACY_DOMAIN) == [legacy_v3_entry]
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_LEGACY_INSTALLED)

    # Once the old folder is gone the next startup adopts it and clears this.
    with patch(INTEGRATION, AsyncMock(side_effect=IntegrationNotFound(LEGACY_DOMAIN))):
        await async_import_legacy_entries(hass)
        await hass.async_block_till_done()

    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_LEGACY_INSTALLED) is None
    assert hass.config_entries.async_entries(LEGACY_DOMAIN) == []
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_two_vaporizers_are_each_adopted_by_their_own_entry(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, legacy_v3_entry: MockConfigEntry
) -> None:
    """Each new entry takes only the old entry with its own MAC."""
    _register_legacy(hass, legacy_v3_entry)
    second = MockConfigEntry(
        domain=LEGACY_DOMAIN,
        title="Second Volcano",
        version=3,
        unique_id="11:22:33:44:55:66",
        data={CONF_ADDRESS: "11:22:33:44:55:66"},
    )
    second.add_to_hass(hass)

    await _start(hass)

    assert hass.config_entries.async_entries(LEGACY_DOMAIN) == []
    assert sorted(e.unique_id for e in hass.config_entries.async_entries(DOMAIN)) == [
        "11:22:33:44:55:66",
        FORMATTED_MAC,
    ]


async def test_another_integrations_entries_are_left_alone(
    hass: HomeAssistant, mock_bluetooth: AsyncMock
) -> None:
    """The other volcano_hybrid integration's entries are never taken."""
    foreign = MockConfigEntry(
        domain=LEGACY_DOMAIN,
        version=1,
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS, "model": "volcano_hybrid"},
    )
    foreign.add_to_hass(hass)

    await _start(hass)

    assert hass.config_entries.async_entries(LEGACY_DOMAIN) == [foreign]
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_another_integration_installed_on_the_old_domain_is_ignored(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, legacy_v3_entry: MockConfigEntry
) -> None:
    """Someone else's code on the old domain is not mistaken for ours."""
    _register_legacy(hass, legacy_v3_entry)
    other = MagicMock(manifest={"codeowners": ["@SomeoneElse"]})

    with patch(INTEGRATION, AsyncMock(return_value=other)):
        await _start(hass)

    assert hass.config_entries.async_entries(LEGACY_DOMAIN) == []
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_LEGACY_INSTALLED) is None


async def test_core_not_found_repair_is_cleared(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, legacy_v3_entry: MockConfigEntry
) -> None:
    """Home Assistant's own "integration not found" repair goes with the entry."""
    _register_legacy(hass, legacy_v3_entry)
    ir.async_create_issue(
        hass,
        "homeassistant",
        CORE_NOT_FOUND_ISSUE,
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key="integration_not_found",
    )

    await _start(hass)

    assert (
        ir.async_get(hass).async_get_issue("homeassistant", CORE_NOT_FOUND_ISSUE)
        is None
    )


async def test_a_second_startup_is_a_no_op(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, legacy_v3_entry: MockConfigEntry
) -> None:
    """With nothing left on the old domain, startup imports nothing."""
    _register_legacy(hass, legacy_v3_entry)
    await _start(hass)
    (entry,) = hass.config_entries.async_entries(DOMAIN)

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.config_entries.async_entries(DOMAIN) == [entry]


async def test_an_entity_whose_unique_id_is_taken_is_skipped(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, legacy_v3_entry: MockConfigEntry
) -> None:
    """A row already present under the new domain is not overwritten."""
    _register_legacy(hass, legacy_v3_entry)
    entity_registry = er.async_get(hass)
    entity_registry.async_get_or_create(
        "climate",
        DOMAIN,
        f"{FORMATTED_MAC}_climate",
        suggested_object_id="already_here",
    )

    await _start(hass)

    assert entity_registry.async_get("climate.already_here") is not None
    # The old row could not move, so it went with the old entry.
    assert entity_registry.async_get("climate.s_b_volcano_h") is None


async def test_a_device_already_on_the_new_domain_keeps_its_identifier(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, legacy_v3_entry: MockConfigEntry
) -> None:
    """Taking an identifier another device holds would raise; it is left alone."""
    device = _register_legacy(hass, legacy_v3_entry)
    holder = MockConfigEntry(domain="other")
    holder.add_to_hass(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=holder.entry_id, identifiers={(DOMAIN, FORMATTED_MAC)}
    )

    await _start(hass)

    moved = dr.async_get(hass).async_get(device.id)
    assert moved is not None
    assert moved.identifiers == {(LEGACY_DOMAIN, FORMATTED_MAC)}


async def test_import_of_an_already_configured_device_aborts(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """The import flow is idempotent on the device's MAC."""
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_IMPORT},
        data={CONF_ADDRESS: ADDRESS, "title": DEVICE_NAME, "options": {}},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_an_entry_from_a_newer_version_is_refused(
    hass: HomeAssistant, mock_bluetooth: AsyncMock
) -> None:
    """A newer entry than this code understands does not get downgraded."""
    entry = MockConfigEntry(
        domain=DOMAIN, version=2, unique_id=FORMATTED_MAC, data={CONF_ADDRESS: ADDRESS}
    )
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.MIGRATION_ERROR


@pytest.mark.parametrize(
    ("version", "data", "expected"),
    [
        (1, {CONF_MAC_ADDRESS: ADDRESS}, True),
        (2, {CONF_ADDRESS: ADDRESS}, True),
        (3, {CONF_ADDRESS: ADDRESS}, True),
        # The other integration: version 1, plain address.
        (1, {CONF_ADDRESS: ADDRESS}, False),
        (4, {CONF_ADDRESS: ADDRESS}, False),
    ],
)
def test_is_own_legacy_entry(
    version: int, data: dict[str, str], expected: bool
) -> None:
    """Only the shapes this integration created count as its own."""
    entry = MockConfigEntry(domain=LEGACY_DOMAIN, version=version, data=data)
    assert is_own_legacy_entry(entry) is expected


def test_legacy_options_drop_an_explicit_none() -> None:
    """A v1-era unset initial temperature is not carried over as an option."""
    entry = MockConfigEntry(
        domain=LEGACY_DOMAIN,
        version=1,
        data={CONF_MAC_ADDRESS: ADDRESS, CONF_INITIAL_TEMP: None},
    )
    assert legacy_options(entry) == {CONF_FAN_ON_CONNECT: False}


def test_normalise_unique_id() -> None:
    """Entry-id prefixes become the MAC; MAC-keyed ids pass through."""
    assert normalise_unique_id("abc_heater", "abc", FORMATTED_MAC) == (
        f"{FORMATTED_MAC}_heater"
    )
    assert normalise_unique_id(f"{FORMATTED_MAC}_fan", "abc", FORMATTED_MAC) == (
        f"{FORMATTED_MAC}_fan"
    )
