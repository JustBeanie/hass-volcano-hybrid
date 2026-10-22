"""The quieter paths: each test here takes the branch the others do not."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.storzandbickel_ble.ble_device import (
    ConnectionFailure,
    DeviceConnectionError,
    DeviceState,
)
from custom_components.storzandbickel_ble.const import (
    CONF_MODEL,
    DOMAIN,
    EVENT_DEVICE,
    LEGACY_DOMAIN,
)
from custom_components.storzandbickel_ble.migration import CORE_NOT_FOUND_ISSUE
from custom_components.storzandbickel_ble.qvap import CMD_STATUS, VentyDevice
from custom_components.storzandbickel_ble.volcano import (
    CHAR_AUTO_OFF_SETTING,
    VolcanoHybrid,
)

from .conftest import ADDRESS, FORMATTED_MAC, FakeBleakClient, make_ble_device

# -- setup and unload ----------------------------------------------------------


async def test_a_failed_unload_keeps_the_link(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fake_client: FakeBleakClient
) -> None:
    """If a platform will not unload, the device is not torn down under it."""
    with patch.object(
        hass.config_entries, "async_unload_platforms", AsyncMock(return_value=False)
    ):
        assert not await hass.config_entries.async_unload(loaded_entry.entry_id)

    assert fake_client.is_connected is True


# -- the shared link -----------------------------------------------------------


def test_unregistering_twice_is_harmless() -> None:
    """A listener removed already is not removed again."""
    device = VolcanoHybrid(ADDRESS)
    unregister = device.register_callback(lambda _state: None)
    unregister()
    unregister()
    assert device._callbacks == []


async def test_resetting_a_link_that_never_existed() -> None:
    """A reset with no client only clears state and counts it."""
    device = VolcanoHybrid(ADDRESS)

    await device._async_reset_link(reset_adapter_cache=False)

    assert device.link_resets == 1
    assert device.adapter_cache_resets == 0


async def test_disconnecting_without_notifications(
    fake_client: FakeBleakClient, mock_establish_connection: AsyncMock
) -> None:
    """A link that never subscribed has nothing to unsubscribe."""
    device = VolcanoHybrid(ADDRESS)
    device.set_ble_device(make_ble_device())
    await device.async_connect()
    device._notifications_started = False

    await device.async_disconnect()

    assert fake_client.is_connected is False
    assert fake_client.notify_callbacks  # never stopped


# -- climate --------------------------------------------------------------------


async def test_set_temperature_without_a_temperature(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fake_client: FakeBleakClient
) -> None:
    """A climate call that carries no temperature writes nothing."""
    climate = hass.data["entity_components"]["climate"].get_entity(
        "climate.s_b_volcano_h"
    )
    fake_client.writes.clear()

    await climate.async_set_temperature()

    assert fake_client.writes == []


# -- coordinator ------------------------------------------------------------------


async def test_a_first_link_is_not_a_recovery(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, fake_client: FakeBleakClient
) -> None:
    """A portable that wakes for the first time has not "recovered"."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=FORMATTED_MAC,
        data={CONF_ADDRESS: ADDRESS, CONF_MODEL: "venty"},
    )
    entry.add_to_hass(hass)
    restored: list[object] = []
    hass.bus.async_listen(EVENT_DEVICE, restored.append)

    with patch.object(VentyDevice, "async_update", AsyncMock(side_effect=_gone())):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    with patch.object(
        VentyDevice, "async_update", AsyncMock(return_value=entry.runtime_data.data)
    ):
        await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert entry.runtime_data.link_health.recoveries == 0
    assert restored == []


def _gone() -> DeviceConnectionError:
    """Return the failure of a portable that is asleep."""
    return DeviceConnectionError("asleep", ConnectionFailure.NOT_VISIBLE)


async def test_no_edges_without_a_reading(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Before register 1 is read there is no edge to detect, and none fires."""
    coordinator = loaded_entry.runtime_data
    fired: list[object] = []
    hass.bus.async_listen(EVENT_DEVICE, fired.append)
    coordinator.device.state.status1 = None

    coordinator._async_fire_edges()
    await hass.async_block_till_done()

    assert fired == []
    assert coordinator._last_target_reached is not True


# -- migration ----------------------------------------------------------------------


async def test_the_core_repair_stays_while_old_entries_remain(
    hass: HomeAssistant, mock_bluetooth: AsyncMock
) -> None:
    """Home Assistant's own repair is only cleared once nothing is left."""
    MockConfigEntry(
        domain=LEGACY_DOMAIN,
        version=1,
        unique_id="11:22:33:44:55:66",
        data={CONF_ADDRESS: "11:22:33:44:55:66"},  # the other integration's shape
    ).add_to_hass(hass)
    MockConfigEntry(
        domain=LEGACY_DOMAIN,
        version=3,
        unique_id=FORMATTED_MAC,
        data={CONF_ADDRESS: ADDRESS},
    ).add_to_hass(hass)
    ir.async_create_issue(
        hass,
        "homeassistant",
        CORE_NOT_FOUND_ISSUE,
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key="integration_not_found",
    )

    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    assert len(hass.config_entries.async_entries(LEGACY_DOMAIN)) == 1
    assert ir.async_get(hass).async_get_issue("homeassistant", CORE_NOT_FOUND_ISSUE)


# -- Venty frames ---------------------------------------------------------------------


def test_an_unrequested_status_still_applies() -> None:
    """A status nobody is waiting for is applied, not dropped."""
    device = VentyDevice(ADDRESS)
    frame = bytearray(20)
    frame[0], frame[11] = CMD_STATUS, 1

    device._handle_notification("uuid", bytes(frame))

    assert device.state.heater_on is True


async def test_a_reply_after_its_waiter_gave_up() -> None:
    """A late reply to a command that already ended is ignored safely."""
    device = VentyDevice(ADDRESS)
    finished: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()
    finished.set_result(b"earlier")
    device._replies[0x05] = finished

    device._handle_notification("uuid", bytes([0x05]))

    assert finished.result() == b"earlier"


def test_a_short_status_has_no_second_settings_byte() -> None:
    """Firmware that sends 15 bytes leaves settings2 unknown."""
    device = VentyDevice(ADDRESS)
    frame = bytearray(15)
    frame[0], frame[14] = CMD_STATUS, 0x02

    device._apply_status(bytes(frame))

    assert device.state.target_reached is True
    assert device.state.settings2 is None


# -- Volcano decode -------------------------------------------------------------------


async def test_an_impossible_auto_off_is_not_shown(
    fake_client: FakeBleakClient, mock_establish_connection: AsyncMock
) -> None:
    """Zero minutes is not a delay the device can have; the last value stays."""
    fake_client.reads[CHAR_AUTO_OFF_SETTING] = (0).to_bytes(2, "little")
    device = VolcanoHybrid(ADDRESS)
    device.set_ble_device(make_ble_device())
    device.state.auto_off_minutes = 30

    state = await device.async_update()

    assert state.auto_off_minutes == 30


def test_base_state_has_no_fan() -> None:
    """Only the Volcano's state ever sets the air pump."""
    assert DeviceState().heat_or_air_reported_on is False
