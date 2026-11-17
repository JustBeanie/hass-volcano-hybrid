"""Serve the dashboard card, and have every dashboard load it.

The card lives in ``frontend/`` as plain JavaScript modules. Home Assistant
serves that folder and adds the card to the modules the frontend loads, so
there is no dashboard resource for the user to add and none written to their
configuration on their behalf.
"""

from __future__ import annotations

from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN

URL_BASE = f"/{DOMAIN}/frontend"
CARD_FILE = "storzandbickel-card.js"
FRONTEND_DIR = Path(__file__).parent / "frontend"


async def async_register_card(hass: HomeAssistant) -> None:
    """Make the card available to every dashboard, where there are dashboards.

    An installation without the frontend has nothing to show a card on, so
    this does nothing there rather than pulling the frontend in.
    """
    if not {"http", "frontend"} <= hass.config.components:
        return

    # Imported here: neither is loaded on an installation without them.
    from homeassistant.components.frontend import add_extra_js_url
    from homeassistant.components.http import StaticPathConfig

    integration = await async_get_integration(hass, DOMAIN)
    await hass.http.async_register_static_paths(
        [
            # Not cached blindly: the card imports its second module by a
            # plain relative path, which the version below cannot reach.
            StaticPathConfig(URL_BASE, str(FRONTEND_DIR), cache_headers=False)
        ]
    )
    # The version makes a browser fetch the new card after an update instead
    # of running the one it cached.
    add_extra_js_url(hass, f"{URL_BASE}/{CARD_FILE}?v={integration.version}")
