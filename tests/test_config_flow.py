"""D65 — one instance, and the manifest is what enforces it."""

from __future__ import annotations

import json
from pathlib import Path

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.almanac.const import CONFIG_ENTRY_VERSION, DOMAIN

MANIFEST = json.loads(
    (
        Path(__file__).parent.parent / "custom_components" / "almanac" / "manifest.json"
    ).read_text(encoding="utf-8")
)


def test_manifest_declares_the_decisions_that_cannot_be_retrofitted() -> None:
    """D53, D65, D81 — each of these is one line and expensive to change later.

    D81 pins the supported Home Assistant version to the one D82's harness was
    built against, so this assertion is what turns a harness bump into a visible
    failure rather than a silent divergence.
    """
    assert MANIFEST["domain"] == DOMAIN == "almanac"
    assert MANIFEST["single_config_entry"] is True
    assert MANIFEST["homeassistant"] == "2026.9.4"
    assert MANIFEST["config_flow"] is True


def test_manifest_matches_hacs_json() -> None:
    """D81 says the version lives in two files; this is the second one."""
    hacs = json.loads(
        (Path(__file__).parent.parent / "hacs.json").read_text(encoding="utf-8")
    )
    assert hacs["homeassistant"] == MANIFEST["homeassistant"]


async def test_user_flow_creates_the_one_entry(hass: HomeAssistant) -> None:
    """There is nothing to ask, so the flow asks nothing."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "almanac"
    assert result["data"] == {}
    assert result["result"].version == CONFIG_ENTRY_VERSION


async def test_second_entry_is_refused(hass: HomeAssistant) -> None:
    """D65 — a second entry would be a second empty store with no way to tell them apart.

    HA aborts on the manifest key before the flow handler is consulted, which is
    the point: the guarantee does not depend on anybody remembering to check.
    """
    MockConfigEntry(domain=DOMAIN, data={}).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"
