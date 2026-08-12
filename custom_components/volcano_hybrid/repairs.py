"""Repair flows for the Volcano Hybrid integration."""

from __future__ import annotations

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .const import ISSUE_CONNECTION_REFUSED


class ConnectionRefusedRepairFlow(RepairsFlow):
    """Walk the user through freeing up the vaporizer's Bluetooth link."""

    def __init__(self, entry_id: str) -> None:
        """Store the entry this issue belongs to."""
        self._entry_id = entry_id

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        """Start the flow."""
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        """Retry the connection once the user says the link is free."""
        if user_input is not None:
            # Reload rather than waiting out the coordinator's backoff, so the
            # user finds out immediately whether that actually fixed it.
            await self.hass.config_entries.async_reload(self._entry_id)
            return self.async_create_entry(data={})

        return self.async_show_form(step_id="confirm", data_schema=None)


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create the fix flow for a Volcano Hybrid issue."""
    if issue_id.startswith(ISSUE_CONNECTION_REFUSED) and data:
        entry_id = data["entry_id"]
        assert isinstance(entry_id, str)
        return ConnectionRefusedRepairFlow(entry_id)

    raise ValueError(f"Unknown repair issue: {issue_id}")
