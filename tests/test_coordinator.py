"""Tests for coordinator behaviour that entity tests do not reach."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, patch

from homeassistant.components.bluetooth import BluetoothChange
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.volcano_hybrid.const import (
    ACTIVE_INTERVAL,
    DOMAIN,
    IDLE_INTERVAL,
    ISSUE_CONNECTION_REFUSED,
)
from custom_components.volcano_hybrid.volcano import (
    CHAR_STATUS_REGISTER,
    VolcanoConnectionError,
)

from .conftest import RAW_STATUS_HEATING, FakeBleakClient, make_service_info

CLIMATE = "climate.s_b_volcano_h"


@pytest.fixture
async def loaded_entry(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, config_entry: MockConfigEntry
) -> MockConfigEntry:
    """Return a fully set up config entry."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry


async def test_command_failure_raises_a_translated_error(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """A failed write surfaces as a HomeAssistantError with a translation key."""
    coordinator = loaded_entry.runtime_data

    with (
        patch.object(
            coordinator.device,
            "async_turn_heater_on",
            side_effect=VolcanoConnectionError("nope"),
        ),
        pytest.raises(HomeAssistantError) as err,
    ):
        await coordinator.async_turn_heater_on()

    assert err.value.translation_key == "command_failed"
    assert err.value.translation_domain == DOMAIN


async def test_unavailable_is_logged_once_and_recovery_once(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One WARNING when the link drops, one INFO when it comes back.

    The Silver rule is explicitly about not logging per poll.
    """
    coordinator = loaded_entry.runtime_data
    caplog.clear()

    with (
        caplog.at_level(logging.INFO, "custom_components.volcano_hybrid.coordinator"),
        patch.object(
            coordinator.device,
            "async_update",
            side_effect=VolcanoConnectionError("gone"),
        ),
    ):
        for _ in range(4):
            await coordinator.async_refresh()

        assert caplog.text.count("Lost connection to the Volcano Hybrid") == 1

        await coordinator.async_refresh()
        assert caplog.text.count("Lost connection to the Volcano Hybrid") == 1

    with caplog.at_level(logging.INFO, "custom_components.volcano_hybrid.coordinator"):
        await coordinator.async_refresh()
        await coordinator.async_refresh()

    assert caplog.text.count("Reconnected to the Volcano Hybrid") == 1
    assert hass.states.get(CLIMATE).state != "unavailable"


async def test_contention_raises_a_repair_issue(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Advertising but refusing to connect points at the phone app."""
    coordinator = loaded_entry.runtime_data
    issue_id = f"{ISSUE_CONNECTION_REFUSED}_{loaded_entry.entry_id}"
    registry = ir.async_get(hass)

    with (
        patch(
            "custom_components.volcano_hybrid.coordinator.async_address_present",
            return_value=True,
        ),
        patch.object(
            coordinator.device,
            "async_update",
            side_effect=VolcanoConnectionError("refused"),
        ),
    ):
        for _ in range(3):
            await coordinator.async_refresh()

    issue = registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.is_fixable is True
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.translation_key == ISSUE_CONNECTION_REFUSED

    # It clears itself as soon as the device lets us in again.
    await coordinator.async_refresh()
    assert registry.async_get_issue(DOMAIN, issue_id) is None


async def test_no_repair_issue_when_the_device_is_simply_away(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """An out-of-range vaporizer is not something closing an app would fix."""
    coordinator = loaded_entry.runtime_data
    issue_id = f"{ISSUE_CONNECTION_REFUSED}_{loaded_entry.entry_id}"

    with (
        patch(
            "custom_components.volcano_hybrid.coordinator.async_address_present",
            return_value=False,
        ),
        patch.object(
            coordinator.device,
            "async_update",
            side_effect=VolcanoConnectionError("gone"),
        ),
    ):
        for _ in range(5):
            await coordinator.async_refresh()

    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_contention_can_be_raised_more_than_once(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """A second episode raises the issue again.

    The failure counter has to reset on success. It previously did not -- the
    reset assigned a differently-spelled attribute -- so the `== THRESHOLD`
    check could only ever match once in a Home Assistant lifetime, and every
    later contention went unreported.
    """
    coordinator = loaded_entry.runtime_data
    issue_id = f"{ISSUE_CONNECTION_REFUSED}_{loaded_entry.entry_id}"
    registry = ir.async_get(hass)

    async def _episode() -> None:
        with (
            patch(
                "custom_components.volcano_hybrid.coordinator.async_address_present",
                return_value=True,
            ),
            patch.object(
                coordinator.device,
                "async_update",
                side_effect=VolcanoConnectionError("refused"),
            ),
        ):
            for _ in range(3):
                await coordinator.async_refresh()

    await _episode()
    assert registry.async_get_issue(DOMAIN, issue_id) is not None

    await coordinator.async_refresh()
    assert registry.async_get_issue(DOMAIN, issue_id) is None
    assert coordinator.consecutive_failures == 0

    await _episode()
    assert registry.async_get_issue(DOMAIN, issue_id) is not None


async def test_an_absent_device_is_not_reconnected_to(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    fake_client: FakeBleakClient,
    mock_establish_connection: AsyncMock,
) -> None:
    """Losing sight of the vaporiser clears the cached BLEDevice.

    Keeping the last known one meant every poll of an unplugged Volcano worked
    through a full connection ladder against six proxies while holding the GATT
    lock, so commands queued behind it. There is nothing to reconnect to; the
    poll has to fail immediately instead.
    """
    coordinator = loaded_entry.runtime_data
    coordinator.device._handle_disconnect(fake_client)
    before = mock_establish_connection.call_count

    with patch(
        "custom_components.volcano_hybrid.coordinator.async_ble_device_from_address",
        return_value=None,
    ):
        await coordinator.async_refresh()

    assert coordinator.device._ble_device is None
    assert coordinator.last_update_success is False
    assert mock_establish_connection.call_count == before


async def test_a_live_link_is_kept_even_with_no_advertisement(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """A connected device often stops advertising; that must not drop the link."""
    coordinator = loaded_entry.runtime_data

    with patch(
        "custom_components.volcano_hybrid.coordinator.async_ble_device_from_address",
        return_value=None,
    ):
        await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data.connected is True


async def test_an_advertisement_refreshes_a_dropped_link(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fake_client: FakeBleakClient
) -> None:
    """Hearing the device again reconnects now, not at the next scheduled poll.

    It must bypass async_request_refresh. That debouncer has a ten second
    cooldown which any recent command has already started, and a reconnect
    landing at the end of it measured 10-15s on real hardware.
    """
    coordinator = loaded_entry.runtime_data

    # Put the debouncer into cooldown, exactly as a button press would, and
    # only then drop the link -- priming it reconnects the fake device.
    await coordinator.async_request_refresh()
    await hass.async_block_till_done()
    coordinator.device._handle_disconnect(fake_client)

    with (
        patch.object(coordinator, "async_refresh") as refresh,
        patch.object(coordinator, "async_request_refresh") as debounced,
    ):
        coordinator.async_set_ble_device(
            make_service_info(), BluetoothChange.ADVERTISEMENT
        )
        await hass.async_block_till_done()

    assert refresh.call_count == 1
    assert debounced.call_count == 0


async def test_a_burst_of_advertisements_makes_one_attempt(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fake_client: FakeBleakClient
) -> None:
    """Advertisements arrive continuously; only one reconnect runs at a time."""
    coordinator = loaded_entry.runtime_data
    coordinator.device._handle_disconnect(fake_client)

    # A real reconnect is a GATT connect, so it is in flight while the rest of
    # the burst arrives. Holding it open here is what makes that true in a test.
    calls = 0
    started = asyncio.Event()
    release = asyncio.Event()

    async def _slow_refresh() -> None:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()

    with patch.object(coordinator, "async_refresh", _slow_refresh):
        for _ in range(5):
            coordinator.async_set_ble_device(
                make_service_info(), BluetoothChange.ADVERTISEMENT
            )
        await started.wait()
        assert coordinator._reconnect_pending is True

        release.set()
        await hass.async_block_till_done()

    assert calls == 1

    # The flag clears afterwards, so a later advertisement can try again.
    assert coordinator._reconnect_pending is False


async def test_an_advertisement_while_connected_does_not_refresh(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """A healthy link just adopts the new device; advertisements are frequent."""
    coordinator = loaded_entry.runtime_data

    with patch.object(coordinator, "async_refresh") as refresh:
        service_info = make_service_info()
        coordinator.async_set_ble_device(service_info, BluetoothChange.ADVERTISEMENT)
        await hass.async_block_till_done()

    assert refresh.call_count == 0
    assert coordinator.device._ble_device is service_info.device


async def test_a_disconnect_is_not_recorded_as_a_successful_update(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fake_client: FakeBleakClient
) -> None:
    """A dropped link notifies listeners without claiming the poll succeeded.

    async_set_updated_data sets last_update_success and resets the refresh
    timer, so routing a disconnect through it left the failure counter at zero
    and the diagnostic sensors reporting available right through an outage.
    """
    coordinator = loaded_entry.runtime_data

    with (
        patch.object(coordinator, "async_set_updated_data") as set_data,
        patch.object(coordinator, "async_update_listeners") as notify,
    ):
        coordinator.device._handle_disconnect(fake_client)

    assert set_data.call_count == 0
    assert notify.call_count == 1


async def test_a_connected_push_still_publishes_data(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """A live notification is a real update and still resets the poll timer."""
    coordinator = loaded_entry.runtime_data

    with patch.object(coordinator, "async_set_updated_data") as set_data:
        coordinator.device._notification_handler(None, bytearray(RAW_STATUS_HEATING))

    assert set_data.call_count == 1


async def test_the_interval_follows_whether_the_device_is_working(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fake_client: FakeBleakClient
) -> None:
    """Poll fast while it is heating, slowly once it is sitting idle."""
    coordinator = loaded_entry.runtime_data

    # The fixture device is idle, so the first refresh should already have
    # backed off -- there is no ramp to watch.
    assert coordinator.update_interval == IDLE_INTERVAL

    fake_client.reads[CHAR_STATUS_REGISTER] = RAW_STATUS_HEATING
    await coordinator.async_refresh()
    assert coordinator.update_interval == ACTIVE_INTERVAL

    fake_client.reads[CHAR_STATUS_REGISTER] = bytes.fromhex("00000000")
    await coordinator.async_refresh()
    assert coordinator.update_interval == IDLE_INTERVAL


async def test_a_command_restores_the_fast_interval(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Pressing heat must not leave the card a minute behind the ramp."""
    coordinator = loaded_entry.runtime_data
    assert coordinator.update_interval == IDLE_INTERVAL

    await coordinator.async_turn_heater_on()

    assert coordinator.update_interval == ACTIVE_INTERVAL
