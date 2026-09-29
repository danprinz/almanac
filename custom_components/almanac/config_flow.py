"""D65 — the integration is single-instance, and the flow has nothing to ask.

Schedules and day sets are collection items, not config entries, so there is
nothing per-instance to configure. A second entry would create a second empty
store and a second set of services with no way to tell them apart — which is
why core marks integrations of this shape with `single_config_entry` rather than
leaving the user to discover the duplicate.

The manifest key is what actually enforces uniqueness: HA aborts a second flow
with `single_instance_allowed` before this class is asked anything. The check in
`async_step_user` is here anyway, because a manifest key is invisible at the
point someone edits this file.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import CONFIG_ENTRY_VERSION, DOMAIN


class AlmanacConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set almanac up. There are no options."""

    VERSION = CONFIG_ENTRY_VERSION

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the one entry, with no questions asked."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        return self.async_create_entry(title="almanac", data={})
