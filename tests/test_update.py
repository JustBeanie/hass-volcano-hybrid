"""Tests for the firmware update entity."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.storzandbickel_ble.firmware import (
    FIRMWARE_UPDATE_URL,
    LATEST_KNOWN_FIRMWARE,
    format_firmware_version,
    latest_firmware_version,
    parse_firmware_version,
)
from custom_components.storzandbickel_ble.volcano import CHAR_FIRMWARE

from .conftest import FakeBleakClient

FIRMWARE = "update.s_b_volcano_h_firmware"


async def _enable(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Enable the update entity, which is off by default."""
    er.async_get(hass).async_update_entity(FIRMWARE, disabled_by=None)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()


async def test_disabled_by_default(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Never checked against a device that was behind, so it starts off."""
    entry = er.async_get(hass).async_get(FIRMWARE)
    assert entry is not None
    assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get(FIRMWARE) is None


async def test_reports_the_installed_version(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """V01.03 is the newest known, so there is nothing to install."""
    await _enable(hass, loaded_entry)

    state = hass.states.get(FIRMWARE)
    assert state.state == "off"
    assert state.attributes["installed_version"] == "V01.03"
    assert state.attributes["latest_version"] == "V01.03"
    assert state.attributes["release_url"] == FIRMWARE_UPDATE_URL


async def test_an_older_device_is_offered_the_known_version(
    hass: HomeAssistant,
    mock_bluetooth: object,
    config_entry: MockConfigEntry,
    fake_client: FakeBleakClient,
) -> None:
    """A device behind the recorded release shows an update."""
    fake_client.reads[CHAR_FIRMWARE] = b"V01.02.00.00"
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await _enable(hass, config_entry)

    state = hass.states.get(FIRMWARE)
    assert state.state == "on"
    assert state.attributes["installed_version"] == "V01.02"
    assert state.attributes["latest_version"] == "V01.03"


async def test_unknown_firmware_is_unavailable(
    hass: HomeAssistant,
    mock_bluetooth: object,
    config_entry: MockConfigEntry,
    fake_client: FakeBleakClient,
) -> None:
    """Without a reported version there is nothing to compare."""
    del fake_client.reads[CHAR_FIRMWARE]
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await _enable(hass, config_entry)

    assert hass.states.get(FIRMWARE).state == "unavailable"
    entity = hass.data["entity_components"]["update"].get_entity(FIRMWARE)
    assert entity.installed_version is None
    assert entity.latest_version is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("V01.03.00.00", (1, 3)),
        ("V2.10", (2, 10)),
        ("V01", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_firmware_version(
    raw: str | None, expected: tuple[int, int] | None
) -> None:
    """Only the major and minor numbers identify a release."""
    assert parse_firmware_version(raw) == expected


def test_a_newer_installed_version_is_never_downgraded() -> None:
    """A device flashed after this release is its own latest."""
    newer = (LATEST_KNOWN_FIRMWARE[0], LATEST_KNOWN_FIRMWARE[1] + 1)
    assert latest_firmware_version(newer) == newer
    assert latest_firmware_version((1, 0)) == LATEST_KNOWN_FIRMWARE
    assert format_firmware_version((1, 3)) == "V01.03"
