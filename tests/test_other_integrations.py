"""Tests for moving a device over from another Storz & Bickel integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.storzandbickel_ble.const import (
    CONF_MODEL,
    DOMAIN,
    ISSUE_MIGRATE_FROM_OTHER,
    LEGACY_DOMAIN,
)
from custom_components.storzandbickel_ble.families import Family
from custom_components.storzandbickel_ble.other_integrations import (
    CONF_MOVE_FROM,
    OTHER_INTEGRATIONS,
    async_finish_move,
    async_offer_moves,
    async_start_move,
)
from custom_components.storzandbickel_ble.repairs import (
    MoveRepairFlow,
    async_create_fix_flow,
)

from .conftest import ADDRESS, FORMATTED_MAC

CHUFFNUGGET = "volcano_integration"
SAVAGENL, CHUFF = OTHER_INTEGRATIONS


def _savagenl_entry(model: str = "volcano_hybrid") -> MockConfigEntry:
    """Return an entry the way SavageNL's integration makes one."""
    return MockConfigEntry(
        domain=LEGACY_DOMAIN,
        title="Volcano Hybrid",
        version=1,
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS, CONF_MODEL: model},
    )


def _chuffnugget_entry() -> MockConfigEntry:
    """Return an entry the way Chuffnugget's integration makes one."""
    return MockConfigEntry(
        domain=CHUFFNUGGET,
        title="S&B VOLCANO H",
        version=2,
        unique_id=ADDRESS,
        data={"bt_address": ADDRESS, "device_name": "S&B VOLCANO H"},
    )


def _register(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    identifier: str,
    rows: list[tuple[str, str, str]],
) -> tuple[dr.DeviceEntry, dict[str, str]]:
    """Recreate the other integration's device and entity rows.

    Returns the device and each entity_id's registry id, which a moved row
    keeps and a recreated one would not.
    """
    entry.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(entry.domain, identifier)},
        name="Volcano",
    )
    area = ar.async_get(hass).async_create("Living Room")
    device = device_registry.async_update_device(device.id, area_id=area.id)
    assert device is not None
    entity_registry = er.async_get(hass)
    ids = {}
    for platform, unique_id, object_id in rows:
        row = entity_registry.async_get_or_create(
            platform,
            entry.domain,
            unique_id,
            suggested_object_id=object_id,
            config_entry=entry,
            device_id=device.id,
        )
        ids[row.entity_id] = row.id
    return device, ids


async def _start(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()


async def _accept(hass: HomeAssistant, entry_id: str) -> None:
    """Accept the repair the way a user would, then let setup finish."""
    flow = await async_create_fix_flow(
        hass, f"{ISSUE_MIGRATE_FROM_OTHER}_{entry_id}", {"entry_id": entry_id}
    )
    flow.hass = hass
    result = await flow.async_step_init()
    assert result["step_id"] == "confirm"
    result = await flow.async_step_confirm({})
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()


def _issue(hass: HomeAssistant, entry_id: str) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(
        DOMAIN, f"{ISSUE_MIGRATE_FROM_OTHER}_{entry_id}"
    )


# -- SavageNL -----------------------------------------------------------------


async def test_a_savagenl_volcano_is_offered_then_moved(
    hass: HomeAssistant, mock_bluetooth: AsyncMock
) -> None:
    """Matching entities keep their IDs; the rest go with the old entry."""
    other = _savagenl_entry()
    device, ids = _register(
        hass,
        other,
        ADDRESS,
        [
            ("climate", f"{ADDRESS}-volcano", "volcano_hybrid"),
            ("switch", f"{ADDRESS}-vibration", "volcano_hybrid_vibration"),
            ("sensor", f"{ADDRESS}-rssi", "volcano_hybrid_rssi"),
            ("binary_sensor", f"{ADDRESS}-at_temperature", "volcano_hybrid_ready"),
            # No counterpart here: a heater-running binary sensor.
            ("binary_sensor", f"{ADDRESS}-heater", "volcano_hybrid_heater"),
        ],
    )

    await _start(hass)
    issue = _issue(hass, other.entry_id)
    assert issue is not None
    assert issue.translation_placeholders == {
        "name": "Volcano Hybrid",
        "integration": "Volcano Hybrid (SavageNL)",
    }
    # Offered, not taken: nothing has moved yet.
    assert hass.config_entries.async_entries(DOMAIN) == []

    await _accept(hass, other.entry_id)

    (entry,) = hass.config_entries.async_entries(DOMAIN)
    assert entry.data == {CONF_ADDRESS: ADDRESS, CONF_MODEL: "volcano_hybrid"}
    assert hass.config_entries.async_get_entry(other.entry_id) is None
    assert _issue(hass, other.entry_id) is None

    entity_registry = er.async_get(hass)
    for entity_id, key in (
        ("climate.volcano_hybrid", "climate"),
        ("switch.volcano_hybrid_vibration", "register3"),
        ("sensor.volcano_hybrid_rssi", "rssi"),
        ("binary_sensor.volcano_hybrid_ready", "ready"),
    ):
        row = entity_registry.async_get(entity_id)
        assert row is not None, entity_id
        assert row.id == ids[entity_id]
        assert row.platform == DOMAIN
        assert row.unique_id == f"{FORMATTED_MAC}_{key}"
    assert entity_registry.async_get("binary_sensor.volcano_hybrid_heater") is None

    moved = dr.async_get(hass).async_get(device.id)
    assert moved is not None
    assert (DOMAIN, FORMATTED_MAC) in moved.identifiers
    assert moved.config_entries == {entry.entry_id}
    assert moved.area_id == device.area_id


async def test_a_savagenl_crafty_keeps_its_family(
    hass: HomeAssistant, mock_bluetooth: AsyncMock
) -> None:
    """A Crafty comes across as a Crafty, and its vibration switch maps to ours."""
    other = _savagenl_entry("crafty")
    _register(
        hass,
        other,
        ADDRESS,
        [("switch", f"{ADDRESS}-vibration", "crafty_vibration")],
    )
    await _start(hass)

    await _accept(hass, other.entry_id)

    (entry,) = hass.config_entries.async_entries(DOMAIN)
    assert entry.data[CONF_MODEL] == "crafty"
    row = er.async_get(hass).async_get("switch.crafty_vibration")
    assert row is not None
    assert row.unique_id == f"{FORMATTED_MAC}_vibration"


async def test_an_unknown_family_is_read_as_a_volcano(
    hass: HomeAssistant, mock_bluetooth: AsyncMock
) -> None:
    """A family name we do not know falls back to the device both started with."""
    assert SAVAGENL.family(_savagenl_entry("mighty")) == "volcano_hybrid"


# -- Chuffnugget -----------------------------------------------------------------


async def test_a_chuffnugget_volcano_is_moved(
    hass: HomeAssistant, mock_bluetooth: AsyncMock
) -> None:
    """Chuffnugget's address-suffixed unique_ids map across too."""
    other = _chuffnugget_entry()
    _register(
        hass,
        other,
        ADDRESS,
        [
            (
                "number",
                f"volcano_heater_temperature_setpoint_{ADDRESS}",
                "volcano_setpoint",
            ),
            (
                "sensor",
                f"volcano_current_temperature_{ADDRESS.lower()}",
                "volcano_temp",
            ),
            ("button", f"volcano_pump_on_button_{ADDRESS}", "volcano_pump_on"),
            # Not one of theirs at all.
            ("sensor", "something_else", "volcano_other"),
        ],
    )
    await _start(hass)
    assert _issue(hass, other.entry_id) is not None

    await _accept(hass, other.entry_id)

    entity_registry = er.async_get(hass)
    setpoint = entity_registry.async_get("number.volcano_setpoint")
    assert setpoint is not None
    assert setpoint.unique_id == f"{FORMATTED_MAC}_target_temperature"
    temperature = entity_registry.async_get("sensor.volcano_temp")
    assert temperature is not None
    assert temperature.unique_id == f"{FORMATTED_MAC}_temperature"
    assert entity_registry.async_get("button.volcano_pump_on") is None
    assert entity_registry.async_get("sensor.volcano_other") is None


# -- when nothing is offered ------------------------------------------------------


async def test_a_device_already_here_is_not_offered(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """Nothing to move when this integration has the device already."""
    config_entry.add_to_hass(hass)
    other = _chuffnugget_entry()
    other.add_to_hass(hass)

    await _start(hass)

    assert _issue(hass, other.entry_id) is None


async def test_our_own_old_entries_are_not_offered(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, legacy_v3_entry: MockConfigEntry
) -> None:
    """A pre-rename entry is adopted automatically, never offered as a move."""
    legacy_v3_entry.add_to_hass(hass)

    await _start(hass)

    assert not [
        issue_id
        for (domain, issue_id) in ir.async_get(hass).issues
        if domain == DOMAIN and issue_id.startswith(ISSUE_MIGRATE_FROM_OTHER)
    ]


async def test_an_entry_without_an_address_is_not_offered(
    hass: HomeAssistant,
) -> None:
    """There is no device to move without an address."""
    other = MockConfigEntry(domain=CHUFFNUGGET, data={})
    other.add_to_hass(hass)

    async_offer_moves(hass)

    assert _issue(hass, other.entry_id) is None
    assert CHUFF.address(other) is None
    assert not await async_start_move(hass, other.entry_id)


async def test_a_stale_offer_is_withdrawn(hass: HomeAssistant) -> None:
    """An offer whose entry is gone does not linger."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"{ISSUE_MIGRATE_FROM_OTHER}_gone",
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_MIGRATE_FROM_OTHER,
    )

    async_offer_moves(hass)

    assert _issue(hass, "gone") is None


# -- the move itself ------------------------------------------------------------------


async def test_a_loaded_entry_is_unloaded_first(
    hass: HomeAssistant, mock_bluetooth: AsyncMock
) -> None:
    """The other integration lets go of the Bluetooth link before the move."""
    other = _chuffnugget_entry()
    other.add_to_hass(hass)
    other.mock_state(hass, ConfigEntryState.LOADED)

    with patch.object(hass.config_entries, "async_unload", AsyncMock()) as unload:
        assert await async_start_move(hass, other.entry_id)
        await hass.async_block_till_done()

    # First, before anything moves; removing the old entry later unloads again.
    assert unload.await_args_list[0].args == (other.entry_id,)


async def test_a_vanished_entry_ends_the_repair(hass: HomeAssistant) -> None:
    """Accepting an offer for an entry since removed says so."""
    flow = MoveRepairFlow("gone")
    flow.hass = hass

    result = await flow.async_step_confirm({})

    assert result["type"] == "abort"
    assert result["reason"] == "entry_gone"


async def test_an_entry_of_ours_is_not_a_source(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Only the other integrations' entries can be moved from."""
    config_entry.add_to_hass(hass)

    assert not await async_start_move(hass, config_entry.entry_id)


async def test_a_move_whose_source_vanished_is_cleared(
    hass: HomeAssistant, mock_bluetooth: AsyncMock
) -> None:
    """If the other entry went before setup, the pending move is dropped."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=FORMATTED_MAC,
        data={CONF_ADDRESS: ADDRESS, CONF_MOVE_FROM: "gone"},
    )
    entry.add_to_hass(hass)

    await async_finish_move(hass, entry)

    assert entry.data == {CONF_ADDRESS: ADDRESS}


@pytest.mark.parametrize(
    ("platform", "key", "family", "expected"),
    [
        ("number", "led_brightness", "crafty", "led_brightness"),
        # The Volcano's LED brightness is a light here, not a number.
        ("number", "led_brightness", "volcano_hybrid", None),
        ("switch", "showing_celsius", "volcano_hybrid", None),
        ("switch", "vibration", "venty", "vibration"),
    ],
)
def test_family_dependent_keys(
    platform: str, key: str, family: str, expected: str | None
) -> None:
    """Some keys only match on some families, or not at all."""
    assert SAVAGENL.our_key(platform, key, Family(family)) == expected
