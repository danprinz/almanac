"""D145's read — the pick-list the anchor editor renders, and what it has to say.

Three properties are checked here, and only the first is about the payload's
shape.

- **The two halves partition the registry.** `offerings` and `parametric` are
  computed as complements, so a resolver that registers and lists nothing lands
  in exactly one of them. Without that, a `clock` whose `offerings()` returned
  `[]` — which is the whole design of a parametric resolver — would simply be
  absent from the catalogue, and the editor would stop offering a typed time with
  no error anywhere.
- **`roles` is the editor's test for a second edge.** §5.1 defines the day-set
  role as a predicate over a span's interior, so `Role.DAY_SET in roles` is what
  distinguishes an offering with a `start` and an `end` that are different
  instants from one that is a single instant. `wire.ts` says so and the editor
  will read it, so the two sides of that inference are pinned here: an instant
  offering declares the anchor role alone, and a span offering declares both.
- **It is not admin-only.** Deliberate, and worth a test rather than a comment,
  because the three *writes* beside it are unconditionally admin-only and the
  obvious tidy is to make the set uniform.

`horizon_through` is `null` for every offering that exists today. The field is
on the wire because `Horizon.until` is a real constructor with a real bound, and
a shape that omitted it would let a reader believe the declaration is one word.
A fake resolver declares an `UNTIL` horizon here so that the serialisation is
checked rather than assumed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.almanac.const import (
    HDATE_CANDLE_LIGHTING,
    HDATE_ISSUR_MELACHA,
    RESOLVER_CLOCK,
    RESOLVER_ENTITY_TIME,
    RESOLVER_HDATE,
    RESOLVER_SUN,
    SUN_SUNSET,
    WS_RESOLVERS,
)
from custom_components.almanac.resolver import Horizon, Offering, Role
from custom_components.almanac.storage import AlmanacData
from custom_components.almanac.websocket import catalogue_payload, offering_payload

NY = ZoneInfo("America/New_York")


def row(catalogue: dict[str, Any], domain: str, key: str) -> dict[str, Any]:
    """One row of the flat list, found the way the editor would find it."""
    for offering in catalogue["offerings"]:
        if offering["domain"] == domain and offering["key"] == key:
            return offering
    raise AssertionError(f"{domain}/{key} is not in the catalogue")


async def _ws(
    client: Any, msg_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    await client.send_json({"id": msg_id, **payload})
    return await client.receive_json()


async def test_the_catalogue_is_flat_and_every_row_names_its_own_domain(
    hass: HomeAssistant,
    setup_almanac: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """D16 is found by typing "havdalah", not by knowing which library computes it.

    So the wire form is one list with the domain on each row, rather than the
    mapping `async_offerings()` happens to store. The editor can still group.
    """
    client = await hass_ws_client(hass)
    result = await _ws(client, 1, {"type": WS_RESOLVERS})
    assert result["success"], result
    catalogue = result["result"]

    assert isinstance(catalogue["offerings"], list)
    domains = {offering["domain"] for offering in catalogue["offerings"]}
    assert domains == {RESOLVER_SUN, RESOLVER_HDATE}
    assert row(catalogue, RESOLVER_SUN, SUN_SUNSET)["display_name"]


async def test_the_two_halves_partition_the_registry(
    almanac_data: AlmanacData,
) -> None:
    """A resolver that lists nothing is still a resolver the editor must know of.

    `parametric` is the complement rather than its own list, which is what makes
    this true by construction: there is no third state a domain can be in.
    """
    catalogue = catalogue_payload(almanac_data.resolvers)
    listed = {offering["domain"] for offering in catalogue["offerings"]}
    parametric = set(catalogue["parametric"])

    assert parametric == {RESOLVER_CLOCK, RESOLVER_ENTITY_TIME}
    assert listed & parametric == set()
    assert listed | parametric == set(almanac_data.resolvers.async_domains())


async def test_an_instant_offering_declares_the_anchor_role_alone(
    almanac_data: AlmanacData,
) -> None:
    """The negative half of the editor's edge test.

    A sunset and a candle lighting are instants. §5.1 says a day set is a
    predicate over an interior, and these have none, so neither declares the role
    — and the editor therefore offers no `edge` choice for them.
    """
    catalogue = catalogue_payload(almanac_data.resolvers)
    for domain, key in (
        (RESOLVER_SUN, SUN_SUNSET),
        (RESOLVER_HDATE, HDATE_CANDLE_LIGHTING),
    ):
        assert row(catalogue, domain, key)["roles"] == [Role.ANCHOR.value]


async def test_a_span_offering_declares_both_roles(
    almanac_data: AlmanacData,
) -> None:
    """The positive half. Shabbat has a start and an end, a day apart.

    `issur_melacha` is the offering D122 and D124 are both about: its `start` edge
    is candle lighting and its `end` edge is havdalah, which is why the editor
    has to offer the choice at all.
    """
    catalogue = catalogue_payload(almanac_data.resolvers)
    roles = row(catalogue, RESOLVER_HDATE, HDATE_ISSUR_MELACHA)["roles"]
    assert roles == sorted([Role.ANCHOR.value, Role.DAY_SET.value])


async def test_every_shipped_horizon_is_unbounded_and_says_so_in_two_fields(
    almanac_data: AlmanacData,
) -> None:
    """D13's declaration, and the field that is null for a reason rather than missing."""
    catalogue = catalogue_payload(almanac_data.resolvers)
    assert {offering["horizon"] for offering in catalogue["offerings"]} == {
        "unbounded"
    }
    assert all(
        offering["horizon_through"] is None for offering in catalogue["offerings"]
    )


def test_an_until_horizon_serialises_its_bound() -> None:
    """The one shape no registered resolver produces yet (v2: calendars).

    Checked directly on the payload builder, because the alternative is to leave
    the branch unexecuted until the first calendar resolver arrives and discovers
    that an instant was being sent as a `datetime`.
    """
    bound = datetime(2027, 10, 31, 23, 59, tzinfo=NY)
    payload = offering_payload(
        "calendar",
        Offering(
            key="bin_day",
            display_name="Bin day",
            roles=frozenset({Role.ANCHOR}),
            horizon=Horizon.until(bound),
        ),
    )
    assert payload["horizon"] == "until"
    assert payload["horizon_through"] == bound.isoformat()


async def test_the_catalogue_is_not_admin_only(
    hass: HomeAssistant,
    setup_almanac: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
    hass_read_only_access_token: str,
) -> None:
    """Which resolvers are installed is less than the schedule list already says.

    The three writes beside this read *are* admin-only, unconditionally, because
    `helpers/collection.py` wraps them that way with no opt-out. The asymmetry is
    the decision; this is the half of it that a uniform rule would have lost.
    """
    client = await hass_ws_client(hass, hass_read_only_access_token)
    result = await _ws(client, 1, {"type": WS_RESOLVERS})
    assert result["success"], result
    assert result["result"]["offerings"]


async def test_an_unloaded_entry_answers_rather_than_serving_a_dead_registry(
    hass: HomeAssistant,
    setup_almanac: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """`_async_data` is a per-call lookup, and this is the case that needs it.

    The command is registered in `async_setup_entry`, so it does not exist at all
    until almanac loads — which means the interesting failure is not "never set
    up" but "set up, then reloaded". A handler that had captured the registry at
    registration would still answer here, out of a registry whose resolvers are
    no longer subscribed to anything.
    """
    client = await hass_ws_client(hass)
    assert (await _ws(client, 1, {"type": WS_RESOLVERS}))["success"]

    assert await hass.config_entries.async_unload(setup_almanac.entry_id)
    await hass.async_block_till_done()

    result = await _ws(client, 2, {"type": WS_RESOLVERS})
    assert not result["success"]
    assert result["error"]["code"] == "not_found"
