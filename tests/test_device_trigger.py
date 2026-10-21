"""Tests for the device triggers and the events behind them."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.components import automation
from homeassistant.components.device_automation import DeviceAutomationType
from homeassistant.core import Event, HomeAssistant, ServiceCall, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_get_device_automations,
    async_mock_service,
)

from custom_components.storzandbickel_ble.const import (
    DOMAIN,
    EVENT_DEVICE,
    TRIGGER_TYPES,
)
from custom_components.storzandbickel_ble.volcano import (
    CHAR_STATUS_REGISTER,
    MASK_HEATER,
    MASK_HEATER_PUMP_FAULT,
    MASK_TARGET_REACHED,
    ConnectionFailure,
    VolcanoConnectionError,
)

from .conftest import FORMATTED_MAC, FakeBleakClient

UPDATE = "custom_components.storzandbickel_ble.volcano.VolcanoHybrid.async_update"


def _device_id(hass: HomeAssistant) -> str:
    """Return the registry id of the test vaporizer."""
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, FORMATTED_MAC)})
    assert device is not None
    return device.id


def _push(fake_client: FakeBleakClient, value: int) -> None:
    """Deliver a status register notification the way the device does."""
    fake_client.reads[CHAR_STATUS_REGISTER] = value.to_bytes(2, "little")
    fake_client.notify_callbacks[CHAR_STATUS_REGISTER](
        None, bytearray(value.to_bytes(2, "little"))
    )


@pytest.fixture
def events(hass: HomeAssistant) -> list[dict[str, object]]:
    """Collect this integration's device events."""
    seen: list[dict[str, object]] = []

    @callback
    def _record(event: Event) -> None:
        seen.append(dict(event.data))

    hass.bus.async_listen(EVENT_DEVICE, _record)
    return seen


async def test_every_trigger_is_listed(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """The automation editor offers all four under the device."""
    triggers = await async_get_device_automations(
        hass, DeviceAutomationType.TRIGGER, _device_id(hass)
    )

    assert {
        trigger["type"] for trigger in triggers if trigger["domain"] == DOMAIN
    } == set(TRIGGER_TYPES)


async def test_an_automation_fires_on_its_trigger(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fake_client: FakeBleakClient
) -> None:
    """A device trigger runs the automation, and only for its own type."""
    calls: list[ServiceCall] = async_mock_service(hass, "test", "automation")
    device_id = _device_id(hass)
    assert await async_setup_component(
        hass,
        automation.DOMAIN,
        {
            automation.DOMAIN: [
                {
                    "trigger": {
                        "platform": "device",
                        "domain": DOMAIN,
                        "device_id": device_id,
                        "type": trigger_type,
                    },
                    "action": {
                        "service": "test.automation",
                        "data": {"type": trigger_type},
                    },
                }
                for trigger_type in TRIGGER_TYPES
            ]
        },
    )

    # Heating: the bit has been read clear, then the device reports reached.
    _push(fake_client, MASK_HEATER)
    _push(fake_client, MASK_HEATER | MASK_TARGET_REACHED)
    await hass.async_block_till_done()

    assert [call.data["type"] for call in calls] == ["target_reached"]


async def test_target_reached_fires_once_per_edge(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    fake_client: FakeBleakClient,
    events: list[dict[str, object]],
) -> None:
    """Flicker of the same state fires nothing; a fresh rise fires again."""
    _push(fake_client, MASK_HEATER)
    _push(fake_client, MASK_HEATER | MASK_TARGET_REACHED)
    _push(fake_client, MASK_HEATER | MASK_TARGET_REACHED | 0x0001)
    await hass.async_block_till_done()
    assert [event["type"] for event in events] == ["target_reached"]
    assert events[0]["device_id"] == _device_id(hass)
    assert events[0]["temperature"] == 205.0

    _push(fake_client, MASK_HEATER)
    _push(fake_client, MASK_HEATER | MASK_TARGET_REACHED)
    await hass.async_block_till_done()
    assert [event["type"] for event in events] == ["target_reached"] * 2


async def test_target_reached_needs_the_heater_on(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    fake_client: FakeBleakClient,
    events: list[dict[str, object]],
) -> None:
    """The bit outliving the heater is not "ready"."""
    _push(fake_client, 0)
    _push(fake_client, MASK_TARGET_REACHED)
    await hass.async_block_till_done()

    assert events == []


async def test_nothing_fires_for_the_state_found_at_startup(
    hass: HomeAssistant,
    mock_bluetooth: AsyncMock,
    config_entry: MockConfigEntry,
    fake_client: FakeBleakClient,
    events: list[dict[str, object]],
) -> None:
    """Already hot and faulted when Home Assistant starts is not a new edge."""
    fake_client.reads[CHAR_STATUS_REGISTER] = (
        MASK_HEATER | MASK_TARGET_REACHED | MASK_HEATER_PUMP_FAULT
    ).to_bytes(2, "little")
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert events == []


async def test_a_new_fault_fires_with_its_code(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    fake_client: FakeBleakClient,
    events: list[dict[str, object]],
) -> None:
    """The fault trigger carries the newest code from the error log."""
    fake_client.reads["10100015-5354-4f52-5a26-4249434b454c"] = b"6100000000000000"
    _push(fake_client, MASK_HEATER_PUMP_FAULT)
    await loaded_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert [event["type"] for event in events] == ["fault_raised"]
    assert events[0]["last_fault"] == 61


async def test_lost_and_restored_fire_once_per_outage(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    events: list[dict[str, object]],
) -> None:
    """Repeated failures are one outage; the recovery says how long it was."""
    coordinator = loaded_entry.runtime_data
    failing = AsyncMock(
        side_effect=VolcanoConnectionError("gone", ConnectionFailure.NOT_VISIBLE)
    )
    with patch(UPDATE, failing):
        await coordinator.async_refresh()
        await coordinator.async_refresh()
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert [event["type"] for event in events] == [
        "connection_lost",
        "connection_restored",
    ]
    assert events[0]["kind"] == "not_visible"
    assert isinstance(events[1]["outage_seconds"], float)


async def test_no_lost_trigger_for_a_device_never_connected(
    hass: HomeAssistant,
    mock_bluetooth: AsyncMock,
    config_entry: MockConfigEntry,
    events: list[dict[str, object]],
) -> None:
    """A vaporizer unplugged at startup has lost nothing."""
    config_entry.add_to_hass(hass)
    failing = AsyncMock(
        side_effect=VolcanoConnectionError("gone", ConnectionFailure.CONNECT_FAILED)
    )
    with patch(UPDATE, failing):
        assert not await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert events == []


async def test_no_event_without_a_registered_device(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    fake_client: FakeBleakClient,
    events: list[dict[str, object]],
) -> None:
    """Without a device to name, an event could not be matched; none is sent."""
    registry = dr.async_get(hass)
    registry.async_remove_device(_device_id(hass))

    _push(fake_client, MASK_HEATER)
    _push(fake_client, MASK_HEATER | MASK_TARGET_REACHED)
    await hass.async_block_till_done()

    assert events == []
