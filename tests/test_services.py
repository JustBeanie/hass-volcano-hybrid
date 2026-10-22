"""Tests for the set_temperature, connect and disconnect actions."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from homeassistant.auth.models import User
from homeassistant.const import ATTR_DEVICE_ID, ATTR_TEMPERATURE
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import (
    HomeAssistantError,
    ServiceValidationError,
    Unauthorized,
)
from homeassistant.helpers import device_registry as dr
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.storzandbickel_ble.ble_device import (
    ConnectionFailure,
    DeviceConnectionError,
)
from custom_components.storzandbickel_ble.const import CONF_AUTO_CONNECT, DOMAIN
from custom_components.storzandbickel_ble.volcano import (
    CHAR_CURRENT_TEMP,
    CHAR_FAN_OFF,
    CHAR_HEAT_OFF,
    CHAR_STATUS_REGISTER,
    CHAR_TARGET_TEMP,
    MASK_FAN,
    MASK_HEATER,
    MASK_TARGET_REACHED,
)

from .conftest import ADDRESS, FORMATTED_MAC, FakeBleakClient

UPDATE = "custom_components.storzandbickel_ble.volcano.VolcanoHybrid.async_update"


def _device_id(hass: HomeAssistant) -> str:
    """Return the registry id of the test vaporizer."""
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, FORMATTED_MAC)})
    assert device is not None
    return device.id


def _status(fake_client: FakeBleakClient, value: int) -> None:
    """Set what the status register reads back."""
    fake_client.reads[CHAR_STATUS_REGISTER] = value.to_bytes(2, "little")


async def _call(hass: HomeAssistant, service: str, **data: object) -> None:
    """Call one of the actions and wait for it to finish."""
    await hass.services.async_call(DOMAIN, service, data, blocking=True)


# -- device selection --------------------------------------------------------


async def test_the_device_can_be_left_out_with_one_vaporizer(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fake_client: FakeBleakClient
) -> None:
    """A single Volcano needs no device picked."""
    await _call(hass, "set_temperature", **{ATTR_TEMPERATURE: 190})

    assert (CHAR_TARGET_TEMP, (1900).to_bytes(4, "little")) in fake_client.writes


async def test_the_device_can_be_named(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fake_client: FakeBleakClient
) -> None:
    """Naming the device works the same."""
    await _call(
        hass,
        "set_temperature",
        **{ATTR_DEVICE_ID: _device_id(hass), ATTR_TEMPERATURE: 185},
    )

    assert (CHAR_TARGET_TEMP, (1850).to_bytes(4, "little")) in fake_client.writes


async def test_an_unknown_device_is_refused(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """A device that is not a loaded Volcano is a validation error."""
    with pytest.raises(ServiceValidationError) as err:
        await _call(
            hass,
            "set_temperature",
            **{ATTR_DEVICE_ID: "nope", ATTR_TEMPERATURE: 185},
        )
    assert err.value.translation_key == "invalid_device"


async def test_no_loaded_vaporizer_is_refused(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """With nothing loaded there is nothing to act on."""
    assert await hass.config_entries.async_unload(loaded_entry.entry_id)

    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "disconnect")
    assert err.value.translation_key == "no_device"


async def test_two_vaporizers_need_a_device(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """With more than one, the call has to say which."""
    second = MockConfigEntry(
        domain=DOMAIN,
        unique_id="11:22:33:44:55:66",
        data={"address": "11:22:33:44:55:66"},
    )
    second.add_to_hass(hass)
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "disconnect")
    assert err.value.translation_key == "ambiguous_device"


async def test_a_read_only_user_cannot_drive_the_vaporizer(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, hass_read_only_user: User
) -> None:
    """Actions respect permissions, the same as the entities."""
    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            DOMAIN,
            "disconnect",
            {},
            blocking=True,
            context=Context(user_id=hass_read_only_user.id),
        )


# -- set_temperature with wait ----------------------------------------------


async def test_waiting_finishes_when_the_device_reports_reached(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fake_client: FakeBleakClient
) -> None:
    """The wait ends on the device's own signal, read after the change."""
    _status(fake_client, MASK_HEATER)
    fake_client.reads[CHAR_CURRENT_TEMP] = (1500).to_bytes(4, "little")
    await loaded_entry.runtime_data.async_refresh()

    # Untracked, so waiting for Home Assistant to settle does not wait on it.
    task = asyncio.create_task(
        _call(
            hass,
            "set_temperature",
            **{ATTR_TEMPERATURE: 190, "wait_until_reached": True, "timeout": 30},
        )
    )
    for _ in range(5):
        await asyncio.sleep(0)
    assert not task.done()

    fake_client.reads[CHAR_CURRENT_TEMP] = (1900).to_bytes(4, "little")
    _status(fake_client, MASK_HEATER | MASK_TARGET_REACHED)
    await loaded_entry.runtime_data.async_refresh()
    await task


async def test_a_stale_reached_bit_does_not_end_the_wait(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fake_client: FakeBleakClient
) -> None:
    """A small raise keeps the bit set; the temperature must get there too."""
    _status(fake_client, MASK_HEATER | MASK_TARGET_REACHED)
    fake_client.reads[CHAR_CURRENT_TEMP] = (2050).to_bytes(4, "little")

    with pytest.raises(HomeAssistantError) as err:
        await _call(
            hass,
            "set_temperature",
            **{ATTR_TEMPERATURE: 207, "wait_until_reached": True, "timeout": 1},
        )
    assert err.value.translation_key == "wait_timeout"


async def test_waiting_with_the_heater_off_fails_at_once(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """The target cannot be reached, so the script is stopped."""
    with pytest.raises(HomeAssistantError) as err:
        await _call(
            hass,
            "set_temperature",
            **{ATTR_TEMPERATURE: 190, "wait_until_reached": True},
        )
    assert err.value.translation_key == "wait_heater_off"


async def test_a_changed_target_ends_the_wait(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fake_client: FakeBleakClient
) -> None:
    """Someone else setting the target means this wait can never finish."""
    _status(fake_client, MASK_HEATER)
    coordinator = loaded_entry.runtime_data
    real_refresh = coordinator.async_refresh

    async def _refresh_then_change() -> None:
        await real_refresh()
        coordinator.device.state.target_temperature = 200.0
        coordinator.async_update_listeners()

    with (
        patch.object(coordinator, "async_refresh", _refresh_then_change),
        pytest.raises(HomeAssistantError) as err,
    ):
        await _call(
            hass,
            "set_temperature",
            **{ATTR_TEMPERATURE: 190, "wait_until_reached": True},
        )
    assert err.value.translation_key == "wait_target_changed"


async def test_a_dropped_link_ends_the_wait(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fake_client: FakeBleakClient
) -> None:
    """A lost connection stops the script rather than leaving it hanging."""
    _status(fake_client, MASK_HEATER)
    failing = AsyncMock(
        side_effect=DeviceConnectionError("gone", ConnectionFailure.NO_READS)
    )

    async def _drop(*_args: object) -> None:
        loaded_entry.runtime_data.device.state.connected = False
        await failing()

    await loaded_entry.runtime_data.async_set_temperature(190)
    with (
        patch(UPDATE, AsyncMock(side_effect=_drop)),
        pytest.raises(HomeAssistantError) as err,
    ):
        await loaded_entry.runtime_data.async_wait_until_reached(30)
    assert err.value.translation_key == "wait_disconnected"


# -- connect and disconnect -------------------------------------------------


async def test_connect_turns_auto_connect_on_and_waits(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """After a disconnect, connect brings the link back before returning."""
    coordinator = loaded_entry.runtime_data
    await coordinator.async_set_auto_connect(False)
    assert coordinator.device.connected is False

    await _call(hass, "connect")

    assert loaded_entry.options[CONF_AUTO_CONNECT] is True
    assert coordinator.device.connected is True


async def test_connect_times_out_on_an_absent_device(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """No link within the timeout is an error, not a silent success."""
    coordinator = loaded_entry.runtime_data
    await coordinator.async_set_auto_connect(False)
    failing = AsyncMock(
        side_effect=DeviceConnectionError("away", ConnectionFailure.NOT_VISIBLE)
    )

    with patch(UPDATE, failing), pytest.raises(HomeAssistantError) as err:
        await _call(hass, "connect", timeout=1)
    assert err.value.translation_key == "connect_timeout"


async def test_connect_without_waiting_returns_at_once(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """wait: false only turns auto-connect back on."""
    coordinator = loaded_entry.runtime_data
    await coordinator.async_set_auto_connect(False)

    with patch.object(coordinator, "async_wait_connected") as wait:
        await _call(hass, "connect", wait=False)

    wait.assert_not_called()
    assert loaded_entry.options[CONF_AUTO_CONNECT] is True


async def test_disconnect_switches_off_then_releases(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fake_client: FakeBleakClient
) -> None:
    """Heat and air go off and are confirmed off before the link is dropped."""
    _status(fake_client, MASK_HEATER | MASK_FAN)
    await loaded_entry.runtime_data.async_refresh()

    await _call(hass, "disconnect")

    written = [uuid for uuid, _ in fake_client.writes]
    assert CHAR_HEAT_OFF in written
    assert CHAR_FAN_OFF in written
    assert loaded_entry.options[CONF_AUTO_CONNECT] is False
    assert loaded_entry.runtime_data.device.connected is False


async def test_disconnect_keeps_the_link_if_off_is_not_confirmed(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fake_client: FakeBleakClient
) -> None:
    """A vaporizer still heating is not abandoned without saying so."""
    _status(fake_client, MASK_HEATER)
    await loaded_entry.runtime_data.async_refresh()

    async def _ignore(uuid: str, data: bytes, response: bool = True) -> None:
        fake_client.writes.append((uuid, bytes(data)))

    fake_client.write_gatt_char = _ignore  # type: ignore[method-assign]

    with pytest.raises(HomeAssistantError) as err:
        await _call(hass, "disconnect")
    assert err.value.translation_key == "release_unconfirmed"
    assert loaded_entry.options.get(CONF_AUTO_CONNECT, True) is True
    assert loaded_entry.runtime_data.device.connected is True


async def test_disconnect_with_everything_already_off(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fake_client: FakeBleakClient
) -> None:
    """Nothing to switch off: no commands, just the release."""
    await _call(hass, "disconnect")

    assert fake_client.writes == []
    assert loaded_entry.options[CONF_AUTO_CONNECT] is False


def test_the_address_fixture_is_the_one_used() -> None:
    """Guard: the tests above assume the conftest vaporizer."""
    assert ADDRESS.lower() == FORMATTED_MAC
