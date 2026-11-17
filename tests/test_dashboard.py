"""Tests for serving the dashboard card."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.storzandbickel_ble.const import DOMAIN
from custom_components.storzandbickel_ble.dashboard import (
    CARD_FILE,
    FRONTEND_DIR,
    URL_BASE,
    async_register_card,
)

MANIFEST = json.loads(
    (FRONTEND_DIR.parent / "manifest.json").read_text(encoding="utf-8")
)


async def test_the_card_is_served_and_loaded_by_every_dashboard(
    hass: HomeAssistant,
) -> None:
    """The folder is served, and the card is added to what the frontend loads."""
    hass.config.components.update({"http", "frontend"})
    hass.http = MagicMock(async_register_static_paths=AsyncMock())
    urls: set[str] = set()
    hass.data[DATA_EXTRA_MODULE_URL] = MagicMock(add=urls.add)

    await async_register_card(hass)

    ((static,),) = hass.http.async_register_static_paths.await_args.args
    assert static.url_path == URL_BASE
    assert Path(static.path) == FRONTEND_DIR
    # The card's second module is imported by a bare relative path, so a
    # long-lived cache would keep serving the old one after an update.
    assert static.cache_headers is False
    # The version is what makes a browser fetch the card again after one.
    assert urls == {f"{URL_BASE}/{CARD_FILE}?v={MANIFEST['version']}"}


async def test_nothing_is_registered_without_a_frontend(hass: HomeAssistant) -> None:
    """A headless installation has no dashboard to put a card on."""
    hass.config.components.add("http")
    hass.http = MagicMock(async_register_static_paths=AsyncMock())

    await async_register_card(hass)

    hass.http.async_register_static_paths.assert_not_awaited()


async def test_setting_the_integration_up_registers_the_card(
    hass: HomeAssistant,
) -> None:
    """The card is registered once, when the integration itself starts."""
    with patch("custom_components.storzandbickel_ble.async_register_card") as register:
        assert await async_setup_component(hass, DOMAIN, {})

    register.assert_awaited_once_with(hass)


def test_every_file_the_card_needs_is_in_the_package() -> None:
    """The card and the module it imports ship inside the integration."""
    card = (FRONTEND_DIR / CARD_FILE).read_text(encoding="utf-8")
    assert (FRONTEND_DIR / "card-logic.js").is_file()
    # Imported next to the card, carrying the card's own ?v=<version>.
    assert "new URL(`./card-logic.js${new URL(import.meta.url).search}`" in card
    # Nothing is fetched from anywhere else.
    assert "http://" not in card.replace("https://github.com/JustBeanie", "")
    assert "https://" not in card.replace("https://github.com/JustBeanie", "")


def test_the_manifest_waits_for_the_frontend() -> None:
    """Setup must run after the frontend, or there is nothing to add the card to."""
    assert {"frontend", "http"} <= set(MANIFEST["after_dependencies"])
