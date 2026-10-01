"""Fixtures shared by the almanac test suite.

D82 — the harness is `pytest-homeassistant-custom-component`, pinned exactly,
and the pin is the statement of which Home Assistant version is supported. The
fixtures below are thin on purpose: anything that needs a `hass` builds it from
the harness's own, so a version bump shows up as a test failure rather than as
divergence between our scaffolding and core's.
"""

from __future__ import annotations

import pathlib
from collections.abc import AsyncGenerator
from typing import Any

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.almanac import frontend_setup
from custom_components.almanac.const import (
    BUNDLE_CARD,
    BUNDLE_PANEL,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
)
from custom_components.almanac.storage import AlmanacData


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> None:
    """Let the loader see `custom_components/almanac`."""


@pytest.fixture(autouse=True)
def built_frontend(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> pathlib.Path:
    """Stand in for `frontend/dist`, which D71 does not commit.

    Autouse, and the reason is determinism rather than convenience. `dist/` is
    gitignored, so whether it exists depends on whether somebody has run
    `npm run build` on the machine -- which would make every test that sets
    almanac up take one of two different paths through
    `frontend_setup.async_register_frontend` depending on a file nobody declared.
    Two one-line stubs make the registration path the one that runs everywhere,
    and `tests/test_frontend_assets.py` is where the other path is exercised on
    purpose.

    The contents are deliberately not the real bundles. What the Python side has
    to get right is which URLs it serves and when it registers them; what the
    bundles contain is Rollup's problem and `npm run typecheck`'s.
    """
    dist = tmp_path / "dist"
    dist.mkdir()
    for name in (BUNDLE_PANEL, BUNDLE_CARD):
        (dist / name).write_text("export default null;\n", encoding="utf-8")
    monkeypatch.setattr(frontend_setup, "_dist_dir", lambda: dist)
    return dist


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
