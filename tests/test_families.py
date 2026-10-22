"""Tests for the experimental Crafty, Venty and Veazy support in Home Assistant."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_USER, ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er, issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.storzandbickel_ble.ble_device import (
    ConnectionFailure,
    DeviceConnectionError,
    DeviceState,
    StorzBickelDevice,
)
from custom_components.storzandbickel_ble.const import CONF_MODEL, DOMAIN
from custom_components.storzandbickel_ble.crafty import (
    CHAR_BOOST,
    CHAR_FIRMWARE,
    CHAR_STATUS2,
    CRAFTY_SERVICE,
    CraftyDevice,
)
from custom_components.storzandbickel_ble.families import (
    Family,
    create_device,
    detect_family,
)
from custom_components.storzandbickel_ble.qvap import (
    QVAP_SERVICE,
    VeazyDevice,
    VentyDevice,
)
from custom_components.storzandbickel_ble.volcano import VolcanoHybrid

from .conftest import ADDRESS, FORMATTED_MAC, make_service_info
from .test_crafty import FakeCraftyClient
from .test_qvap import FakeQvapClient

COORDINATOR = "custom_components.storzandbickel_ble.coordinator"


def _entry(family: Family) -> MockConfigEntry:
    """Return an entry for a device of ``family``."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=f"My {family}",
        unique_id=FORMATTED_MAC,
        data={CONF_ADDRESS: ADDRESS, CONF_MODEL: family.value},
        options={"auto_connect_delay": 0.0},
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _entity_ids(hass: HomeAssistant, entry: MockConfigEntry) -> set[str]:
    registry = er.async_get(hass)
    return {
        row.entity_id.split(".", 1)[0]
        + "."
        + row.unique_id.removeprefix(f"{FORMATTED_MAC}_")
        for row in er.async_entries_for_config_entry(registry, entry.entry_id)
    }


# -- detection ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "uuids", "expected"),
    [
        (None, [CRAFTY_SERVICE], Family.CRAFTY),
        # Sharing a service UUID, the Venty and Veazy need their names.
        (None, [QVAP_SERVICE], None),
        ("Some Light", [], None),
    ],
)
def test_detect_family_by_service(
    name: str | None, uuids: list[str], expected: Family | None
) -> None:
    """A service UUID alone identifies a Crafty, never a Venty or Veazy."""
    assert detect_family(name, uuids) == expected


def test_each_family_gets_its_own_client() -> None:
    """The right protocol for the right device."""
    assert isinstance(create_device(Family.VOLCANO_HYBRID, ADDRESS), VolcanoHybrid)
    assert isinstance(create_device(Family.CRAFTY, ADDRESS), CraftyDevice)
    assert isinstance(create_device(Family.VENTY, ADDRESS), VentyDevice)
    assert isinstance(create_device(Family.VEAZY, ADDRESS), VeazyDevice)


async def test_the_base_class_only_shares_the_link() -> None:
    """Anything a family must provide is missing until it does."""
    state = DeviceState()
    device = StorzBickelDevice(ADDRESS, state)
    assert device.state is state
    device._handle_notification("uuid", b"")
    for method, args in (
        ("async_update", ()),
        ("async_turn_heater_on", ()),
        ("async_turn_heater_off", ()),
        ("async_set_target_temperature", (180,)),
        ("async_turn_fan_on", ()),
        ("async_turn_fan_off", ()),
        ("async_set_brightness", (5,)),
        ("async_set_vibration", (True,)),
        ("async_set_display_fahrenheit", (True,)),
    ):
        with pytest.raises(NotImplementedError):
            await getattr(device, method)(*args)

    state = DeviceState(heater_on=True)
    assert state.target_reached is None
    assert state.fault_known is False
    assert state.any_fault is False
    assert state.last_fault is None
    assert state.heat_or_air_reported_on is True


# -- the Crafty in Home Assistant ----------------------------------------------


@pytest.fixture
def fake_client(request: pytest.FixtureRequest) -> Any:
    """Return a Crafty by default, or a Venty for tests that ask for one."""
    if getattr(request, "param", None) == "venty":
        return FakeQvapClient()
    return FakeCraftyClient()


async def test_a_crafty_gets_its_entities(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, fake_client: FakeCraftyClient
) -> None:
    """The shared entities plus the Crafty's own, and none of the Volcano's."""
    entry = _entry(Family.CRAFTY)
    await _setup(hass, entry)

    ids = _entity_ids(hass, entry)
    for expected in (
        "climate.climate",
        "sensor.battery",
        "sensor.auto_off_countdown",
        "sensor.hours_operation",
        "binary_sensor.boost",
        "binary_sensor.superboost",
        "binary_sensor.error",
        "binary_sensor.ready",
        "number.boost_temperature",
        "number.led_brightness",
        "switch.vibration",
        "switch.charge_led",
        "switch.auto_ble_shutdown",
        "switch.heater",
        "button.find",
        "sensor.link_health",
    ):
        assert expected in ids, expected
    for absent in (
        "switch.fan",
        "light.screen",
        "update.firmware",
        "sensor.last_fault",
    ):
        assert absent not in ids, absent

    climate = hass.states.get("climate.my_crafty")
    assert climate.attributes["max_temp"] == 210
    assert "fan_modes" not in climate.attributes
    assert (
        hass.states.get("number.my_crafty_target_temperature").attributes["max"] == 210
    )
    assert hass.states.get("sensor.my_crafty_battery").state == "76"


async def test_a_plain_crafty_cannot_be_found(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, fake_client: FakeCraftyClient
) -> None:
    """Find is a Crafty+ feature; a Crafty shows the button unavailable."""
    await _setup(hass, _entry(Family.CRAFTY))

    assert hass.states.get("button.my_crafty_find_device").state == "unavailable"


async def test_old_crafty_firmware_is_monitor_only(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, fake_client: FakeCraftyClient
) -> None:
    """Below V02.51 the controls are unavailable rather than failing."""
    fake_client.reads[CHAR_FIRMWARE] = b"V02.40"
    await _setup(hass, _entry(Family.CRAFTY))

    assert hass.states.get("switch.my_crafty_heater").state == "unavailable"
    assert hass.states.get("switch.my_crafty_vibration").state == "unavailable"
    assert hass.states.get("sensor.my_crafty_temperature").state == "179.5"


async def test_crafty_controls_reach_the_device(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, fake_client: FakeCraftyClient
) -> None:
    """Numbers, switches and the find button drive the Crafty."""
    fake_client.reads[CHAR_FIRMWARE] = b"V03.10"
    await _setup(hass, _entry(Family.CRAFTY))

    await hass.services.async_call(
        "number",
        "set_value",
        {ATTR_ENTITY_ID: "number.my_crafty_boost_temperature", "value": 12},
        blocking=True,
    )
    await hass.services.async_call(
        "switch",
        "turn_off",
        {ATTR_ENTITY_ID: "switch.my_crafty_charge_led"},
        blocking=True,
    )
    await hass.services.async_call(
        "button",
        "press",
        {ATTR_ENTITY_ID: "button.my_crafty_find_device"},
        blocking=True,
    )

    written = [uuid for uuid, _ in fake_client.writes]
    assert CHAR_BOOST in written
    assert written.count(CHAR_STATUS2) == 2


async def test_a_sleeping_portable_still_sets_up(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, fake_client: FakeCraftyClient
) -> None:
    """Asleep at startup is normal: the entry loads and waits for it."""
    entry = _entry(Family.CRAFTY)
    with (
        patch(
            "custom_components.storzandbickel_ble.async_ble_device_from_address",
            return_value=None,
        ),
        patch(f"{COORDINATOR}.async_ble_device_from_address", return_value=None),
    ):
        await _setup(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("climate.my_crafty").state == "unavailable"
    assert hass.states.get("sensor.my_crafty_link_health").state == "not_visible"


async def test_a_sleeping_portable_raises_no_repairs(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, fake_client: FakeCraftyClient
) -> None:
    """A portable refusing connections while waking is not an app holding it."""
    entry = _entry(Family.CRAFTY)
    await _setup(hass, entry)
    failing = AsyncMock(
        side_effect=DeviceConnectionError("no", ConnectionFailure.CONNECT_FAILED)
    )

    with (
        patch.object(CraftyDevice, "async_update", failing),
        patch(f"{COORDINATOR}.async_address_present", return_value=True),
    ):
        for _ in range(5):
            await entry.runtime_data.async_refresh()

    assert not [
        issue
        for (domain, _issue_id), issue in ir.async_get(hass).issues.items()
        if domain == DOMAIN
    ]


# -- the Venty in Home Assistant ----------------------------------------------


@pytest.mark.parametrize("fake_client", ["venty"], indirect=True)
async def test_a_venty_gets_its_entities(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, fake_client: FakeQvapClient
) -> None:
    """The Venty's own entities, and its controls work."""
    entry = _entry(Family.VENTY)
    await _setup(hass, entry)

    ids = _entity_ids(hass, entry)
    for expected in (
        "sensor.heater_mode",
        "sensor.heater_runtime",
        "sensor.charging_time",
        "binary_sensor.charging",
        "binary_sensor.bootloader",
        "number.superboost_temperature",
        "number.display_brightness",
        "switch.display_fahrenheit",
        "switch.charge_optimisation",
        "switch.boost_timeout_disabled",
    ):
        assert expected in ids, expected
    assert "switch.permanent_bluetooth" not in ids  # Veazy only
    assert "sensor.colour" not in ids  # Veazy only

    assert hass.states.get("sensor.my_venty_heater_mode").state == "off"
    await hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {ATTR_ENTITY_ID: "climate.my_venty", "hvac_mode": "heat"},
        blocking=True,
    )
    await hass.services.async_call(
        "number",
        "set_value",
        {ATTR_ENTITY_ID: "number.my_venty_display_brightness", "value": 7},
        blocking=True,
    )
    await hass.services.async_call(
        "number",
        "set_value",
        {ATTR_ENTITY_ID: "number.my_venty_superboost_temperature", "value": 20},
        blocking=True,
    )
    await hass.services.async_call(
        "switch",
        "turn_on",
        {ATTR_ENTITY_ID: "switch.my_venty_charge_limit"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert hass.states.get("climate.my_venty").state == "heat"
    assert fake_client.display[2] == 7
    assert fake_client.status[7] == 20


@pytest.mark.parametrize("fake_client", ["venty"], indirect=True)
async def test_a_veazy_adds_its_extras(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, fake_client: FakeQvapClient
) -> None:
    """Permanent Bluetooth and colour are Veazy entities."""
    entry = _entry(Family.VEAZY)
    await _setup(hass, entry)

    ids = _entity_ids(hass, entry)
    assert "switch.permanent_bluetooth" in ids
    assert "sensor.colour" in ids
    assert hass.states.get("sensor.my_veazy_colour").state == "pink"


# -- adding one ------------------------------------------------------------------


async def test_a_venty_is_discovered(
    hass: HomeAssistant, mock_bluetooth: AsyncMock
) -> None:
    """Discovery records the family, which setup then builds from."""
    venty = make_service_info(name="S&B VY123456")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=venty
    )
    assert result["step_id"] == "bluetooth_confirm"
    with patch.object(StorzBickelDevice, "async_connect"):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_MODEL] == "venty"


async def test_a_crafty_can_be_added_by_address(
    hass: HomeAssistant, mock_bluetooth: AsyncMock
) -> None:
    """With no advertisement to go on, the user says which device it is."""
    with patch(
        "custom_components.storzandbickel_ble.config_flow.async_discovered_service_info",
        return_value=[],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
    with patch(
        "custom_components.storzandbickel_ble.config_flow.async_ble_device_from_address",
        return_value=None,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ADDRESS: ADDRESS, CONF_MODEL: "crafty"}
        )
    with patch(
        "custom_components.storzandbickel_ble.async_ble_device_from_address",
        return_value=None,
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Crafty"
    assert result["data"] == {CONF_ADDRESS: ADDRESS, CONF_MODEL: "crafty"}


async def test_reconfigure_tests_with_the_right_protocol(
    hass: HomeAssistant, mock_bluetooth: AsyncMock, fake_client: FakeCraftyClient
) -> None:
    """A Crafty entry is re-tested as a Crafty."""
    entry = _entry(Family.CRAFTY)
    await _setup(hass, entry)

    with patch.object(CraftyDevice, "async_connect") as connect:
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    connect.assert_awaited_once()
    assert result["reason"] == "reconfigure_successful"
