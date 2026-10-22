"""Tests for auto-connect, the reconnect buttons and the link-health sensor."""

from __future__ import annotations

from datetime import timedelta
import time
from unittest.mock import AsyncMock, patch

from homeassistant.components.bluetooth import BluetoothChange
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_OFF, SERVICE_TURN_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.storzandbickel_ble.const import (
    CONF_AUTO_CONNECT,
    CONF_AUTO_CONNECT_DELAY,
    CONF_FAN_ON_CONNECT,
    CONF_INITIAL_TEMP,
    DOMAIN,
    ISSUE_CONNECTION_REFUSED,
    ON_DEMAND_GRACE,
)
from custom_components.storzandbickel_ble.coordinator import CONTENTION_THRESHOLD
from custom_components.storzandbickel_ble.volcano import (
    CHAR_FAN_ON,
    ConnectionFailure,
    VolcanoConnectionError,
)

from .conftest import FakeBleakClient, make_service_info

AUTO_CONNECT = "switch.s_b_volcano_h_auto_connect"
LINK_HEALTH = "sensor.s_b_volcano_h_link_health"
RECONNECT = "button.s_b_volcano_h_reconnect"
DELAYED_RECONNECT = "button.s_b_volcano_h_reconnect_after_delay"
FAN = "switch.s_b_volcano_h_fan"
UPDATE = "custom_components.storzandbickel_ble.VolcanoHybrid.async_update"
PRESENT = "custom_components.storzandbickel_ble.coordinator.async_address_present"


def _failing(kind: ConnectionFailure) -> AsyncMock:
    """Return an update that fails the way ``kind`` does."""
    return AsyncMock(side_effect=VolcanoConnectionError("nope", kind))


async def _enable(
    hass: HomeAssistant, entry: MockConfigEntry, *entity_ids: str
) -> None:
    """Enable entities that are disabled by default, then reload."""
    registry = er.async_get(hass)
    for entity_id in entity_ids:
        registry.async_update_entity(entity_id, disabled_by=None)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()


async def _switch(hass: HomeAssistant, service: str, entity_id: str) -> None:
    """Call a switch service and wait for it to settle."""
    await hass.services.async_call(
        "switch", service, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )
    await hass.async_block_till_done()


# -- auto-connect -----------------------------------------------------------


async def test_auto_connect_is_on_by_default(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """A fresh entry connects on its own, and the switch says so."""
    assert hass.states.get(AUTO_CONNECT).state == "on"
    assert hass.states.get(LINK_HEALTH).state == "connected"


async def test_turning_auto_connect_off_releases_the_link(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fake_client: FakeBleakClient
) -> None:
    """Off hands the vaporizer back straight away, without a reload."""
    coordinator = loaded_entry.runtime_data

    with patch.object(hass.config_entries, "async_reload") as reload:
        await _switch(hass, SERVICE_TURN_OFF, AUTO_CONNECT)

    reload.assert_not_called()
    assert loaded_entry.options[CONF_AUTO_CONNECT] is False
    assert fake_client.is_connected is False
    assert hass.states.get(AUTO_CONNECT).state == "off"
    assert hass.states.get(LINK_HEALTH).state == "auto_connect_off"
    assert loaded_entry.runtime_data is coordinator
    # Controls stay usable, since using one is what connects on demand; a
    # reading would only be stale, so those go unavailable.
    assert hass.states.get(FAN).state != "unavailable"
    assert hass.states.get("climate.s_b_volcano_h").state != "unavailable"
    assert hass.states.get("sensor.s_b_volcano_h_temperature").state == "unavailable"


async def test_with_auto_connect_off_polls_do_not_connect(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    mock_establish_connection: AsyncMock,
) -> None:
    """Nothing asked for the link, so a poll reports and does not reconnect."""
    await _switch(hass, SERVICE_TURN_OFF, AUTO_CONNECT)
    coordinator = loaded_entry.runtime_data
    before = mock_establish_connection.call_count

    await coordinator.async_refresh()

    assert mock_establish_connection.call_count == before
    assert coordinator.last_update_success is True


async def test_with_auto_connect_off_advertisements_are_ignored(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Hearing the vaporizer does not grab the link the user just released."""
    await _switch(hass, SERVICE_TURN_OFF, AUTO_CONNECT)
    coordinator = loaded_entry.runtime_data

    with patch.object(coordinator, "async_refresh") as refresh:
        coordinator.async_set_ble_device(
            make_service_info(), BluetoothChange.ADVERTISEMENT
        )
        await hass.async_block_till_done()

    refresh.assert_not_called()
    assert coordinator._reconnect_pending is False


async def test_a_command_connects_on_demand_then_lets_go(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fake_client: FakeBleakClient
) -> None:
    """A command opens the link; once idle past the grace period it is freed."""
    await _switch(hass, SERVICE_TURN_OFF, AUTO_CONNECT)
    coordinator = loaded_entry.runtime_data

    await _switch(hass, SERVICE_TURN_ON, FAN)
    assert (CHAR_FAN_ON, b"\x00") in fake_client.writes
    assert coordinator.device.connected is True

    # Still inside the grace period: the poll keeps the link and reads it.
    await coordinator.async_refresh()
    assert coordinator.device.connected is True

    coordinator._demanded_at -= ON_DEMAND_GRACE.total_seconds() + 1
    await coordinator.async_refresh()
    assert coordinator.device.connected is False


async def test_turning_auto_connect_back_on_reconnects(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """On again connects without waiting for an advertisement."""
    await _switch(hass, SERVICE_TURN_OFF, AUTO_CONNECT)
    coordinator = loaded_entry.runtime_data
    assert coordinator.device.connected is False

    await _switch(hass, SERVICE_TURN_ON, AUTO_CONNECT)

    assert loaded_entry.options[CONF_AUTO_CONNECT] is True
    assert coordinator.device.connected is True
    assert hass.states.get(LINK_HEALTH).state == "connected"


async def test_auto_connect_off_withdraws_and_suppresses_repairs(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """The user is managing the link, so nothing asks them to close an app."""
    coordinator = loaded_entry.runtime_data
    with (
        patch(UPDATE, _failing(ConnectionFailure.CONNECT_FAILED)),
        patch(PRESENT, return_value=True),
    ):
        for _ in range(CONTENTION_THRESHOLD):
            await coordinator.async_refresh()
    issue_id = f"{ISSUE_CONNECTION_REFUSED}_{loaded_entry.entry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id)

    await _switch(hass, SERVICE_TURN_OFF, AUTO_CONNECT)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None

    # A command-driven poll can still fail; it never raises the repair again.
    coordinator._demanded_at = time.monotonic()
    coordinator.consecutive_refusals = 0
    with (
        patch(UPDATE, _failing(ConnectionFailure.CONNECT_FAILED)),
        patch(PRESENT, return_value=True),
    ):
        for _ in range(CONTENTION_THRESHOLD):
            await coordinator.async_refresh()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_setup_with_auto_connect_off_needs_no_device(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """An entry the user has released loads even with the vaporizer away."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, options={**config_entry.options, CONF_AUTO_CONNECT: False}
    )

    with patch(
        "custom_components.storzandbickel_ble.async_ble_device_from_address",
        return_value=None,
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert hass.states.get(AUTO_CONNECT).state == "off"
    assert hass.states.get(LINK_HEALTH).state == "auto_connect_off"


async def test_a_real_option_change_still_reloads(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Only auto-connect is applied live; anything else reloads as before."""
    with patch.object(hass.config_entries, "async_reload") as reload:
        hass.config_entries.async_update_entry(
            loaded_entry,
            options={**loaded_entry.options, CONF_FAN_ON_CONNECT: True},
        )
        await hass.async_block_till_done()

    reload.assert_called_once_with(loaded_entry.entry_id)


# -- advertisement delay -----------------------------------------------------


async def test_an_advertisement_waits_before_connecting(
    hass: HomeAssistant,
    mock_bluetooth: AsyncMock,
    config_entry: MockConfigEntry,
    fake_client: FakeBleakClient,
) -> None:
    """The connect waits so every proxy can report, then happens once."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_AUTO_CONNECT_DELAY: 1.0}
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = config_entry.runtime_data
    coordinator.device._handle_disconnect(fake_client)

    with patch.object(coordinator, "async_refresh") as refresh:
        for _ in range(3):
            coordinator.async_set_ble_device(
                make_service_info(), BluetoothChange.ADVERTISEMENT
            )
        await hass.async_block_till_done()
        refresh.assert_not_called()

        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
        await hass.async_block_till_done()

    refresh.assert_called_once()
    assert coordinator._reconnect_pending is False


async def test_unloading_cancels_a_scheduled_reconnect(
    hass: HomeAssistant,
    mock_bluetooth: AsyncMock,
    config_entry: MockConfigEntry,
    fake_client: FakeBleakClient,
) -> None:
    """A pending connect does not outlive the entry."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_AUTO_CONNECT_DELAY: 5.0}
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = config_entry.runtime_data
    coordinator.device._handle_disconnect(fake_client)
    coordinator.async_set_ble_device(make_service_info(), BluetoothChange.ADVERTISEMENT)
    assert coordinator._cancel_reconnect is not None

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert coordinator._cancel_reconnect is None


# -- buttons -----------------------------------------------------------------


async def test_the_buttons_are_disabled_by_default(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """They are manual overrides; the link heals itself."""
    registry = er.async_get(hass)
    for entity_id in (RECONNECT, DELAYED_RECONNECT):
        entry = registry.async_get(entity_id)
        assert entry is not None
        assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION


async def test_reconnect_rebuilds_the_link(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    mock_establish_connection: AsyncMock,
) -> None:
    """Pressing reconnect drops the link and connects again at once."""
    await _enable(hass, loaded_entry, RECONNECT)
    before = mock_establish_connection.call_count

    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: RECONNECT}, blocking=True
    )
    await hass.async_block_till_done()

    assert mock_establish_connection.call_count == before + 1
    assert loaded_entry.runtime_data.device.connected is True


async def test_delayed_reconnect_waits_for_a_fresh_advertisement(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """The link goes down now and comes back after the configured wait."""
    await _enable(hass, loaded_entry, DELAYED_RECONNECT)
    coordinator = loaded_entry.runtime_data

    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: DELAYED_RECONNECT}, blocking=True
    )
    await hass.async_block_till_done()
    assert coordinator.device.connected is False
    # Pressable while disconnected: getting back is the whole point.
    assert hass.states.get(DELAYED_RECONNECT).state != "unavailable"

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=12))
    await hass.async_block_till_done()

    assert coordinator.device.connected is True


async def test_delayed_reconnect_respects_auto_connect_off(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """A button press is a demand, so it connects even with auto-connect off."""
    await _enable(hass, loaded_entry, DELAYED_RECONNECT)
    await _switch(hass, SERVICE_TURN_OFF, AUTO_CONNECT)
    coordinator = loaded_entry.runtime_data

    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: DELAYED_RECONNECT}, blocking=True
    )
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=12))
    await hass.async_block_till_done()

    assert coordinator.device.connected is True


# -- link health -------------------------------------------------------------


async def test_link_health_follows_an_outage_and_its_recovery(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """The sensor names the failure, then counts the recovery."""
    coordinator = loaded_entry.runtime_data

    with patch(UPDATE, _failing(ConnectionFailure.NO_READS)):
        await coordinator.async_refresh()
    await hass.async_block_till_done()

    state = hass.states.get(LINK_HEALTH)
    assert state.state == "no_reads"
    assert state.attributes["last_failure"] == "no_reads"
    assert state.attributes["recoveries"] == 0

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    state = hass.states.get(LINK_HEALTH)
    assert state.state == "connected"
    assert state.attributes["recoveries"] == 1
    assert state.attributes["last_outage_seconds"] is not None
    assert state.attributes["last_restored_at"] is not None
    # The last failure is kept for after the fact.
    assert state.attributes["last_failure"] == "no_reads"


async def test_link_health_reports_resets(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fake_client: FakeBleakClient
) -> None:
    """A link rebuilt by the self-heal shows up in the counters."""
    coordinator = loaded_entry.runtime_data
    await coordinator.device._async_reset_link(reset_adapter_cache=True)
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    attributes = hass.states.get(LINK_HEALTH).attributes
    assert attributes["link_resets"] == 1
    assert attributes["adapter_cache_resets"] == 1


async def test_link_health_without_a_recorded_failure_is_not_visible(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fake_client: FakeBleakClient
) -> None:
    """A link that dropped without a failed poll yet reads as out of range."""
    coordinator = loaded_entry.runtime_data
    coordinator.device._handle_disconnect(fake_client)
    await hass.async_block_till_done()

    assert coordinator.health_state == "not_visible"


async def test_startup_options_still_apply_with_auto_connect_off(
    hass: HomeAssistant,
    mock_bluetooth: AsyncMock,
    config_entry: MockConfigEntry,
    fake_client: FakeBleakClient,
) -> None:
    """The user asked for them, so they connect on demand to apply."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry,
        options={CONF_AUTO_CONNECT: False, CONF_INITIAL_TEMP: 180},
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert fake_client.writes
