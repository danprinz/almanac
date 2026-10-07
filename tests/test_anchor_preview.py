"""`almanac/anchor/preview` -- the time picker's "next: ..." line.

The command exists so the editor can say *when* "45 minutes before candle
lighting" will next happen without the browser owning a copy of the resolvers.
Everything D64 forbids is tested by absence: the instant is a required field,
and `tests/test_design_constraints.py` still passes with an allow-list that did
not grow.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.almanac.const import WS_ANCHOR_PREVIEW

AT = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
CLOCK = {"kind": "clock", "at": "07:00:00"}


async def _ws(client: Any, msg_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    await client.send_json({"id": msg_id, **payload})
    return await client.receive_json()


async def test_a_clock_anchor_yields_the_next_instants_after_at(
    hass: HomeAssistant,
    setup_almanac: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    client = await hass_ws_client(hass)
    result = await _ws(
        client,
        1,
        {"type": WS_ANCHOR_PREVIEW, "anchor": CLOCK, "at": AT.isoformat(), "count": 3},
    )
    assert result["success"], result
    body = result["result"]
    instants = [datetime.fromisoformat(value) for value in body["instants"]]

    assert len(instants) == 3
    assert instants == sorted(instants)
    assert all(instant > AT for instant in instants)
    assert body["unresolved"] is None
    assert datetime.fromisoformat(body["at"]) == AT


async def test_an_offset_resolver_anchor_resolves(
    hass: HomeAssistant,
    setup_almanac: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    client = await hass_ws_client(hass)
    anchor = {"kind": "resolver", "domain": "sun", "key": "sunset", "offset": -1800}
    result = await _ws(
        client, 1, {"type": WS_ANCHOR_PREVIEW, "anchor": anchor, "at": AT.isoformat()}
    )
    assert result["success"], result
    assert len(result["result"]["instants"]) == 3  # `count` defaults to 3
    assert result["result"]["unresolved"] is None


async def test_an_unknown_resolver_is_an_answer_not_an_error(
    hass: HomeAssistant,
    setup_almanac: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """D17: a stored anchor whose resolver is absent degrades; it does not raise."""
    client = await hass_ws_client(hass)
    anchor = {"kind": "resolver", "domain": "nowhere", "key": "never", "offset": 0}
    result = await _ws(
        client, 1, {"type": WS_ANCHOR_PREVIEW, "anchor": anchor, "at": AT.isoformat()}
    )
    assert result["success"], result
    assert result["result"]["instants"] == []
    assert isinstance(result["result"]["unresolved"], str)
    assert result["result"]["unresolved"] != ""


async def test_the_instant_is_required_and_a_naive_one_is_refused(
    hass: HomeAssistant,
    setup_almanac: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """D64 at the boundary: the backend will not supply, or guess, what time it is."""
    client = await hass_ws_client(hass)
    missing = await _ws(client, 1, {"type": WS_ANCHOR_PREVIEW, "anchor": CLOCK})
    assert not missing["success"]
    assert missing["error"]["code"] == "invalid_format"

    naive = await _ws(
        client,
        2,
        {"type": WS_ANCHOR_PREVIEW, "anchor": CLOCK, "at": "2026-10-07T12:00:00"},
    )
    assert not naive["success"]
    assert naive["error"]["code"] == "invalid_format"


async def test_a_count_outside_the_range_is_refused(
    hass: HomeAssistant,
    setup_almanac: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    client = await hass_ws_client(hass)
    result = await _ws(
        client,
        1,
        {"type": WS_ANCHOR_PREVIEW, "anchor": CLOCK, "at": AT.isoformat(), "count": 99},
    )
    assert not result["success"]


async def test_the_preview_is_not_admin_only(
    hass: HomeAssistant,
    setup_almanac: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
    hass_read_only_access_token: str,
) -> None:
    """It resolves an anchor the caller supplies and reads no schedule."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    result = await _ws(
        client, 1, {"type": WS_ANCHOR_PREVIEW, "anchor": CLOCK, "at": AT.isoformat()}
    )
    assert result["success"], result
