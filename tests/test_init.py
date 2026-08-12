"""Tests for setup, unload and config entry migration."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import AsyncMock, patch

from homeassistant.components.bluetooth import BluetoothChange
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.volcano_hybrid.const import (
    CONF_FAN_ON_CONNECT,
    CONF_INITIAL_TEMP,
    DOMAIN,
)
from custom_components.volcano_hybrid.volcano import (
    CHAR_FAN_ON,
    CHAR_TARGET_TEMP,
    VolcanoConnectionError,
)

from .conftest import (
    ADDRESS,
    DEVICE_NAME,
    FORMATTED_MAC,
    FakeBleakClient,
    make_service_info,
)


async def test_setup_and_unload(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """The entry loads every platform and unloads cleanly."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("climate.s_b_volcano_h") is not None
    assert hass.states.get("switch.s_b_volcano_h_heater") is not None
    assert hass.states.get("binary_sensor.s_b_volcano_h_connection").state == "on"

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_bluetooth_callback_matches_the_manager_signature(
    hass: HomeAssistant,
    mock_bluetooth: AsyncMock,
    config_entry: MockConfigEntry,
    bluetooth_callbacks: list[Callable[..., None]],
) -> None:
    """The registered callback must accept (service_info, change).

    The Bluetooth manager always passes two positional arguments, including
    when it replays advertisement history at registration time. A one-argument
    callback raises TypeError inside the manager and the integration silently
    stops following the device between adapters and proxies.
    """
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert len(bluetooth_callbacks) == 1
    service_info = make_service_info()

    # Exactly how homeassistant/components/bluetooth/manager.py invokes it.
    bluetooth_callbacks[0](service_info, BluetoothChange.ADVERTISEMENT)

    assert config_entry.runtime_data.device._ble_device is service_info.device


async def test_setup_retries_when_out_of_range(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """An unreachable device leaves the entry in setup_retry, not loaded."""
    config_entry.add_to_hass(hass)

    with patch(
        "custom_components.volcano_hybrid.async_ble_device_from_address",
        return_value=None,
    ):
        assert not await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_retries_when_connection_fails(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """A failed connection raises ConfigEntryNotReady rather than half loading."""
    config_entry.add_to_hass(hass)

    with patch(
        "custom_components.volcano_hybrid.VolcanoHybrid.async_connect",
        side_effect=VolcanoConnectionError("nope"),
    ):
        assert not await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_startup_options_are_applied_once(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, fake_client: FakeBleakClient
) -> None:
    """fan_on_connect and initial_temperature run on setup, not per reconnect."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=DEVICE_NAME,
        version=2,
        unique_id=FORMATTED_MAC,
        data={
            CONF_ADDRESS: ADDRESS,
            CONF_FAN_ON_CONNECT: True,
            CONF_INITIAL_TEMP: 180,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    written = fake_client.writes
    assert (CHAR_FAN_ON, b"\x00") in written
    assert (CHAR_TARGET_TEMP, (1800).to_bytes(4, "little")) in written

    # A later poll must not repeat them.
    before = len(written)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert len(fake_client.writes) == before + 2


async def test_migration_preserves_entity_ids(
    hass: HomeAssistant,
    mock_bluetooth: AsyncMock,
    legacy_config_entry: MockConfigEntry,
) -> None:
    """A v1 entry migrates in place, keeping every entity_id intact.

    This is what stops the migration from renaming entities to _2 and breaking
    the automations, scripts and dashboards that reference them.
    """
    entry = legacy_config_entry
    entry.add_to_hass(hass)

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=DEVICE_NAME,
    )
    legacy_entities = {
        "climate": ("climate", f"{entry.entry_id}_climate", "s_b_volcano_h"),
        "heater": ("switch", f"{entry.entry_id}_heater", "s_b_volcano_h_heater"),
        "temperature": (
            "sensor",
            f"{entry.entry_id}_temperature",
            "s_b_volcano_h_temperature",
        ),
    }
    for domain, unique_id, object_id in legacy_entities.values():
        entity_registry.async_get_or_create(
            domain,
            DOMAIN,
            unique_id,
            suggested_object_id=object_id,
            config_entry=entry,
            device_id=device.id,
        )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.version == 2
    assert entry.unique_id == FORMATTED_MAC
    assert entry.data[CONF_ADDRESS] == ADDRESS

    # Same device row, new identifier.
    migrated_device = device_registry.async_get(device.id)
    assert migrated_device is not None
    assert migrated_device.identifiers == {(DOMAIN, FORMATTED_MAC)}

    # Same entity_ids, new unique_ids.
    for domain, _old_unique_id, object_id in legacy_entities.values():
        entity_id = f"{domain}.{object_id}"
        entry_row = entity_registry.async_get(entity_id)
        assert entry_row is not None, f"{entity_id} was renamed by the migration"
        assert entry_row.unique_id.startswith(f"{FORMATTED_MAC}_")

    assert hass.states.get("climate.s_b_volcano_h") is not None
    assert hass.states.get("switch.s_b_volcano_h_heater") is not None
    assert hass.states.get("sensor.s_b_volcano_h_temperature") is not None


async def test_migration_from_a_future_version_is_refused(
    hass: HomeAssistant, mock_bluetooth: AsyncMock
) -> None:
    """A newer entry than this code understands does not get downgraded."""
    entry = MockConfigEntry(
        domain=DOMAIN, version=3, unique_id=FORMATTED_MAC, data={CONF_ADDRESS: ADDRESS}
    )
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.MIGRATION_ERROR


async def test_migration_without_an_address_fails(
    hass: HomeAssistant, mock_bluetooth: AsyncMock
) -> None:
    """A corrupt v1 entry fails migration instead of setting up half-configured."""
    entry = MockConfigEntry(domain=DOMAIN, version=1, data={"name": DEVICE_NAME})
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.MIGRATION_ERROR


async def test_entities_go_unavailable_when_the_device_drops(
    hass: HomeAssistant,
    mock_bluetooth: AsyncMock,
    config_entry: MockConfigEntry,
    fake_client: FakeBleakClient,
) -> None:
    """Losing the link marks entities unavailable but keeps connectivity readable."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = config_entry.runtime_data
    coordinator.device._handle_disconnect(fake_client)
    await hass.async_block_till_done()

    assert hass.states.get("climate.s_b_volcano_h").state == "unavailable"
    assert hass.states.get("binary_sensor.s_b_volcano_h_connection").state == "off"


async def test_platforms_are_all_declared() -> None:
    """Every platform module the integration ships is forwarded."""
    from custom_components.volcano_hybrid import PLATFORMS

    assert set(PLATFORMS) == {
        Platform.BINARY_SENSOR,
        Platform.CLIMATE,
        Platform.LIGHT,
        Platform.NUMBER,
        Platform.SENSOR,
        Platform.SWITCH,
    }
