"""Fixtures shared by the almanac test suite.

D82 — the harness is `pytest-homeassistant-custom-component`, pinned exactly,
and the pin is the statement of which Home Assistant version is supported. The
fixtures below are thin on purpose: anything that needs a `hass` builds it from
the harness's own, so a version bump shows up as a test failure rather than as
divergence between our scaffolding and core's.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.almanac.const import (
    CONFIG_ENTRY_VERSION,
    DOMAIN,
)
from custom_components.almanac.storage import AlmanacData


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> None:
    """Let the loader see `custom_components/almanac`."""


@pytest.fixture
async def websocket_api(hass: HomeAssistant) -> None:
    """The manifest depends on websocket_api, so the tests need it loaded."""
    assert await async_setup_component(hass, "websocket_api", {})


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """An almanac config entry, not yet added to hass."""
    return MockConfigEntry(
        domain=DOMAIN, title="almanac", data={}, version=CONFIG_ENTRY_VERSION
    )


@pytest.fixture
async def setup_almanac(
    hass: HomeAssistant, websocket_api: None, config_entry: MockConfigEntry
) -> AsyncGenerator[MockConfigEntry]:
    """Set almanac up and hand back its entry."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    yield config_entry


@pytest.fixture
def almanac_data(setup_almanac: MockConfigEntry) -> AlmanacData:
    """The loaded collection and runtime store."""
    return setup_almanac.runtime_data


@pytest.fixture
def schedule_payload() -> dict[str, Any]:
    """A minimal valid create payload."""
    return {"name": "Shabbat lights"}
