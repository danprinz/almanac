"""The three resolvers step 2 ships, and the anchor layer over them.

Everything is evaluated at a written-down instant. D64 is not a style rule here:
the dry run, the timeline and the live engine are meant to be one code path at
three different `now`s, and a test that reached for the wall clock would be
unable to tell whether that is true.

The DST cases are the ones worth reading. D40's two rules — skip a nonexistent
local time, fire an ambiguous one once — are silently wrong in most schedulers,
and "the 02:30 schedule fired twice" is the failure this product exists to
avoid. They are checked here at the transitions in `America/New_York`: forward
on 2026-03-08, back on 2026-11-01.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.helpers import sun as sun_helper
from homeassistant.util import dt as dt_util

from custom_components.almanac.const import (
    ANCHOR_CLOCK,
    ANCHOR_ENTITY_TIME,
    ANCHOR_RESOLVER,
    RESOLVER_SUN,
    SUN_DAWN,
    SUN_DUSK,
    SUN_NOON,
    SUN_SUNSET,
)
from custom_components.almanac.resolver import (
    Horizon,
    InvalidationKind,
    ResolverRegistry,
    Role,
    Unresolved,
    UnresolvedReason,
    Window,
    async_create_registry,
    async_forecast_anchor,
)

NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

# New York City, so that the sun assertions are about a place rather than about
# whatever the harness defaults to.
LATITUDE = 40.7128
LONGITUDE = -74.0060

ANCHOR_SENSOR = "sensor.candle_lighting"


def ny_window(start: datetime, days: int) -> Window:
    """A window of whole civil days, written out rather than derived from now."""
    return Window(start, start + timedelta(days=days))


@pytest.fixture(autouse=True)
async def _located(hass: HomeAssistant) -> None:
    """Put `hass` somewhere with a timezone, which is all these resolvers read."""
    await hass.config.async_set_time_zone("America/New_York")
    await hass.config.async_update(
        latitude=LATITUDE, longitude=LONGITUDE, elevation=0
    )


@pytest.fixture
def resolvers(hass: HomeAssistant) -> ResolverRegistry:
    """The three in-tree resolvers, wired exactly as the config entry wires them."""
    return async_create_registry(hass)


# --- clock (D40) -----------------------------------------------------------


async def test_a_clock_anchor_fires_once_per_civil_day(
    resolvers: ResolverRegistry,
) -> None:
    """The ordinary case, and the shape every other assertion here departs from."""
    window = ny_window(datetime(2026, 10, 2, 0, 0, tzinfo=NY), 3)
    forecast = await resolvers.async_forecast("clock", "17:00:00", window)

    assert not isinstance(forecast, Unresolved)
    assert [span.start for span in forecast.spans] == [
        datetime(2026, 10, 2, 17, 0, tzinfo=NY),
        datetime(2026, 10, 3, 17, 0, tzinfo=NY),
        datetime(2026, 10, 4, 17, 0, tzinfo=NY),
    ]
    # D13 — arithmetic, so there is no date it cannot answer for.
    assert forecast.horizon == Horizon.unbounded()
    assert forecast.fully_known


async def test_a_nonexistent_local_time_is_skipped_and_logged(
    resolvers: ResolverRegistry, caplog: pytest.LogCaptureFixture
) -> None:
    """D40 — on 2026-03-08 the clocks jump 02:00 to 03:00, so 02:30 never happens.

    The surrounding days still fire. A resolver that returned nothing for the
    whole window, or that quietly slid the occurrence to 03:30, would both be
    defensible-looking and wrong.
    """
    window = ny_window(datetime(2026, 3, 7, 0, 0, tzinfo=NY), 3)
    forecast = await resolvers.async_forecast("clock", "02:30:00", window)

    assert not isinstance(forecast, Unresolved)
    assert [span.start.date() for span in forecast.spans] == [
        date(2026, 3, 7),
        date(2026, 3, 9),
    ]
    assert "no such local time" in caplog.text


async def test_an_ambiguous_local_time_fires_on_its_first_occurrence(
    resolvers: ResolverRegistry,
) -> None:
    """D40 — on 2026-11-01 01:30 happens twice; exactly one occurrence fires.

    "The 02:30 schedule fired twice" is the complaint this rule is an answer to,
    so the assertion is on the count first and the instant second.
    """
    window = ny_window(datetime(2026, 11, 1, 0, 0, tzinfo=NY), 1)
    forecast = await resolvers.async_forecast("clock", "01:30:00", window)

    assert not isinstance(forecast, Unresolved)
    assert len(forecast.spans) == 1
    # The earlier of the two, i.e. still on EDT (UTC-4), not EST (UTC-5).
    assert forecast.spans[0].start.astimezone(UTC) == datetime(
        2026, 11, 1, 5, 30, tzinfo=UTC
    )


async def test_a_clock_day_is_23_or_25_hours_long_and_that_is_fine(
    resolvers: ResolverRegistry,
) -> None:
    """D40 — clock anchors are wall-clock, so 17:00 stays 17:00 across a transition.

    The instants either side differ by 23 hours, and that is the correct answer
    rather than a rounding error: a wall-clock anchor tracks the wall.
    """
    window = ny_window(datetime(2026, 3, 7, 0, 0, tzinfo=NY), 2)
    forecast = await resolvers.async_forecast("clock", "17:00:00", window)

    assert not isinstance(forecast, Unresolved)
    first, second = (span.start for span in forecast.spans)
    assert first.hour == second.hour == 17
    assert second.astimezone(UTC) - first.astimezone(UTC) == timedelta(hours=23)


async def test_clock_is_not_addressable_as_a_resolver_anchor(
    resolvers: ResolverRegistry,
) -> None:
    """D6 gives it its own kind, and D9 makes a spelling permanent once stored.

    Allowing both `{kind: clock, at: "17:00"}` and
    `{kind: resolver, domain: clock, key: "17:00"}` would be two spellings of
    one concept, and the editor would have to render whichever it was saved as.
    """
    result = resolvers.async_resolve_selectable("clock", "17:00:00")
    assert isinstance(result, Unresolved)
    assert result.reason is UnresolvedReason.NOT_SELECTABLE


def test_a_clock_key_that_is_not_a_time_is_not_an_offering(
    resolvers: ResolverRegistry,
) -> None:
    """The schema normalises on the way in; this is the guard for what got past it."""
    clock = resolvers.async_get("clock")
    assert clock is not None
    assert clock.offering("dinner time") is None
    assert clock.offering("17:00") is not None


# --- entity_time (D42, D13) ------------------------------------------------


async def test_an_entity_anchor_resolves_to_the_instant_it_holds(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """The feature upstream refused (A.8), and the reason this project exists."""
    hass.states.async_set(ANCHOR_SENSOR, "2026-10-02T18:34:00-04:00")
    window = ny_window(datetime(2026, 10, 2, 0, 0, tzinfo=NY), 3)

    forecast = await resolvers.async_forecast("entity_time", ANCHOR_SENSOR, window)
    assert not isinstance(forecast, Unresolved)
    assert [span.start for span in forecast.spans] == [
        datetime(2026, 10, 2, 18, 34, tzinfo=NY)
    ]


async def test_an_entity_anchor_sees_exactly_one_occurrence_ahead(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """D13 / §5.5 — NEXT_ONLY, and the horizon is where the honesty lives.

    The sensor holds one value (A.2). Everything past it is unknown, and the
    timeline has to say so rather than repeating the value weekly — which is
    precisely the situation in which being wrong is most visible.
    """
    hass.states.async_set(ANCHOR_SENSOR, "2026-10-02T18:34:00-04:00")
    window = ny_window(datetime(2026, 10, 2, 0, 0, tzinfo=NY), 7)

    forecast = await resolvers.async_forecast("entity_time", ANCHOR_SENSOR, window)
    assert not isinstance(forecast, Unresolved)
    assert forecast.horizon == Horizon.next_only()
    assert forecast.known_through == datetime(2026, 10, 2, 18, 34, tzinfo=NY)
    assert not forecast.fully_known


async def test_a_value_outside_the_window_is_a_known_nothing(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """§5.5's central distinction, from the other side.

    The source is working and has nothing in range, so this is not `Unresolved`.
    It is still not *fully known*, because NEXT_ONLY means the window was never
    covered in the first place — and those two facts together are what the
    timeline renders as an empty stretch marked unknown rather than as an empty
    stretch marked quiet.
    """
    hass.states.async_set(ANCHOR_SENSOR, "2026-12-25T16:00:00-05:00")
    window = ny_window(datetime(2026, 10, 2, 0, 0, tzinfo=NY), 3)

    forecast = await resolvers.async_forecast("entity_time", ANCHOR_SENSOR, window)
    assert not isinstance(forecast, Unresolved)
    assert forecast.spans == ()
    assert forecast.known_through == window.start
    assert not forecast.fully_known


@pytest.mark.parametrize(
    "state",
    ["unknown", "unavailable", "not a timestamp"],
)
async def test_an_unusable_entity_is_unresolved_not_empty(
    hass: HomeAssistant, resolvers: ResolverRegistry, state: str
) -> None:
    """D42 / §10.4 — skipped and logged, and distinguishable from *nothing*.

    An anchor entity that is `unknown` for ninety seconds after a restart is the
    normal case. Returning an empty forecast would be a positive claim that
    nothing is scheduled, and the late arrival would have nothing to recover.
    """
    hass.states.async_set(ANCHOR_SENSOR, state)
    window = ny_window(datetime(2026, 10, 2, 0, 0, tzinfo=NY), 3)

    result = await resolvers.async_forecast("entity_time", ANCHOR_SENSOR, window)
    assert isinstance(result, Unresolved)
    assert result.reason is UnresolvedReason.UNAVAILABLE


async def test_an_entity_that_does_not_exist_is_unresolved(
    resolvers: ResolverRegistry,
) -> None:
    """Same treatment: the schedule renders as unresolved, it does not fail to load."""
    window = ny_window(datetime(2026, 10, 2, 0, 0, tzinfo=NY), 3)
    result = await resolvers.async_forecast("entity_time", "sensor.gone", window)
    assert isinstance(result, Unresolved)
    assert result.reason is UnresolvedReason.UNAVAILABLE


async def test_a_naive_entity_state_is_read_as_local_wall_time(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """`input_datetime` stores a naive civil datetime; local is what was typed."""
    hass.states.async_set(ANCHOR_SENSOR, "2026-10-02 18:34:00")
    window = ny_window(datetime(2026, 10, 2, 0, 0, tzinfo=NY), 1)

    forecast = await resolvers.async_forecast("entity_time", ANCHOR_SENSOR, window)
    assert not isinstance(forecast, Unresolved)
    assert forecast.spans[0].start.astimezone(UTC) == datetime(
        2026, 10, 2, 22, 34, tzinfo=UTC
    )


async def test_an_entity_anchor_stays_subscribed_while_it_is_unavailable(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """D42 and D43 are the same mechanism seen from two ends.

    The subscription is named by the offering, not by whether the entity
    currently has a value, so the recovery arrives at all.
    """
    hass.states.async_set(ANCHOR_SENSOR, "unknown")
    signal = resolvers.async_invalidation("entity_time", ANCHOR_SENSOR)
    assert not isinstance(signal, Unresolved)
    assert signal.kind is InvalidationKind.OBSERVATIONAL
    assert signal.entity_ids == frozenset({ANCHOR_SENSOR})
    assert not signal.cacheable

    fired: list[int] = []
    unsub = resolvers.async_subscribe(
        "entity_time", ANCHOR_SENSOR, lambda: fired.append(1)
    )
    hass.states.async_set(ANCHOR_SENSOR, "2026-10-02T18:34:00-04:00")
    await hass.async_block_till_done()
    unsub()

    assert fired == [1]


# --- sun (§4, D13, A.2) ----------------------------------------------------


def test_sun_offers_the_three_anchors_upstream_refuses(
    resolvers: ResolverRegistry,
) -> None:
    """A.3 — `validate_time` whitelists sunrise and sunset only.

    `sun.sun` has published dawn, dusk and noon the whole time. Closing the gap
    costs one tuple, and the gap is one of the things this project is for.
    """
    offerings = resolvers.async_offerings()[RESOLVER_SUN]
    keys = {offering.key for offering in offerings}
    assert {SUN_DAWN, SUN_DUSK, SUN_NOON} <= keys
    assert len(keys) == 6
    assert all(offering.roles == frozenset({Role.ANCHOR}) for offering in offerings)
    assert all(
        offering.horizon == Horizon.unbounded() for offering in offerings
    )


async def test_sun_computes_any_date_locally(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """A.2, re-verified — `get_astral_event_date(hass, event, date)` is arithmetic.

    Cross-checked against the helper directly rather than against a literal, so
    this asserts that the resolver applies the helper to the right civil days
    rather than that astral is correct.
    """
    window = ny_window(datetime(2026, 10, 2, 0, 0, tzinfo=NY), 3)
    forecast = await resolvers.async_forecast(RESOLVER_SUN, SUN_SUNSET, window)

    assert not isinstance(forecast, Unresolved)
    assert [span.start for span in forecast.spans] == [
        dt_util.as_utc(
            sun_helper.get_astral_event_date(hass, SUN_SUNSET, day)
        ).astimezone(NY)
        for day in (date(2026, 10, 2), date(2026, 10, 3), date(2026, 10, 4))
    ]


async def test_sun_answers_for_a_date_a_year_out(
    resolvers: ResolverRegistry,
) -> None:
    """The UNBOUNDED declaration, exercised rather than merely asserted.

    This is the asymmetry behind D6: `sensor.sun_next_setting` could not answer
    this question at all, which is why the two are different anchor kinds
    instead of one routed through `entity_time`.
    """
    window = ny_window(datetime(2027, 10, 2, 0, 0, tzinfo=NY), 1)
    forecast = await resolvers.async_forecast(RESOLVER_SUN, SUN_SUNSET, window)

    assert not isinstance(forecast, Unresolved)
    assert len(forecast.spans) == 1
    assert forecast.fully_known


async def test_a_polar_day_yields_a_known_nothing(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """§5.5 again, and the cleanest illustration of why the horizon is separate.

    Above the Arctic circle in June the sun does not set. The forecast is empty
    and *fully known*: the source examined every day and reports that nothing
    happens, which is a different sentence from "I cannot see that far".
    """
    await hass.config.async_update(latitude=78.2232, longitude=15.6267)
    window = ny_window(datetime(2026, 6, 15, 0, 0, tzinfo=NY), 3)

    forecast = await resolvers.async_forecast(RESOLVER_SUN, SUN_SUNSET, window)
    assert not isinstance(forecast, Unresolved)
    assert forecast.spans == ()
    assert forecast.fully_known


async def test_a_sun_key_that_does_not_exist_is_unresolved(
    resolvers: ResolverRegistry,
) -> None:
    """D16's fixed set is a safety property too.

    The helper dispatches with `getattr(astral.sun, event)`, so an unchecked key
    would reach for an arbitrary attribute of a third-party module.
    """
    window = ny_window(datetime(2026, 10, 2, 0, 0, tzinfo=NY), 1)
    result = await resolvers.async_forecast(RESOLVER_SUN, "time_of_transit", window)
    assert isinstance(result, Unresolved)
    assert result.reason is UnresolvedReason.UNKNOWN_KEY


async def test_sun_is_deterministic_and_cacheable(
    resolvers: ResolverRegistry,
) -> None:
    """D43 — a year of sunsets needs no state subscription at all."""
    signal = resolvers.async_invalidation(RESOLVER_SUN, SUN_SUNSET)
    assert not isinstance(signal, Unresolved)
    assert signal.cacheable


# --- the anchor layer (D6, D7, D40) ----------------------------------------


async def test_an_offset_is_applied_as_an_absolute_shift(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """D7 and D40 together, at the one line where they are either true or not.

    `aware + timedelta` is wall-clock arithmetic in Python: it moves the naive
    part and keeps the offset. Applying it directly would make "sunset minus 20
    minutes" come out an hour wrong across a DST transition, twice a year, on
    the anchor most people use.
    """
    window = ny_window(datetime(2026, 11, 1, 0, 0, tzinfo=NY), 1)
    anchor = {
        "kind": ANCHOR_RESOLVER,
        "domain": RESOLVER_SUN,
        "key": SUN_SUNSET,
        "offset": -20 * 60,
        "edge": "start",
    }
    resolved = await async_forecast_anchor(resolvers, anchor, window)
    assert not isinstance(resolved, Unresolved)

    sunset = dt_util.as_utc(
        sun_helper.get_astral_event_date(hass, SUN_SUNSET, date(2026, 11, 1))
    )
    assert resolved.instants[0].astimezone(UTC) == sunset - timedelta(minutes=20)


async def test_a_negative_offset_does_not_lose_the_last_occurrence(
    resolvers: ResolverRegistry
) -> None:
    """The window has to be padded by the offset before forecasting, not after.

    Forecasting the unpadded window and shifting afterwards drops every
    occurrence whose source instant sits just past the edge — which, for a
    negative offset, is the last day of every window.
    """
    # Ends at 17:00 local; sunset on 2 October is around 18:35, so the source
    # instant is outside the window and the resolved one is inside it.
    window = Window(
        datetime(2026, 10, 2, 0, 0, tzinfo=NY), datetime(2026, 10, 2, 17, 0, tzinfo=NY)
    )
    anchor = {
        "kind": ANCHOR_RESOLVER,
        "domain": RESOLVER_SUN,
        "key": SUN_SUNSET,
        "offset": -2 * 3600,
        "edge": "start",
    }
    resolved = await async_forecast_anchor(resolvers, anchor, window)
    assert not isinstance(resolved, Unresolved)
    assert len(resolved.instants) == 1
    assert window.contains(resolved.instants[0])


async def test_a_clock_anchor_carries_no_offset(
    resolvers: ResolverRegistry,
) -> None:
    """The schema gives it none: you type a different time instead."""
    window = ny_window(datetime(2026, 10, 2, 0, 0, tzinfo=NY), 1)
    resolved = await async_forecast_anchor(
        resolvers, {"kind": ANCHOR_CLOCK, "at": "17:00:00"}, window
    )
    assert not isinstance(resolved, Unresolved)
    assert resolved.instants == (datetime(2026, 10, 2, 17, 0, tzinfo=NY),)


async def test_an_entity_anchor_offset_is_the_motivating_case(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """"45 minutes before candle lighting" — the request upstream declined (A.8)."""
    hass.states.async_set(ANCHOR_SENSOR, "2026-10-02T18:34:00-04:00")
    window = ny_window(datetime(2026, 10, 2, 0, 0, tzinfo=NY), 1)
    anchor = {
        "kind": ANCHOR_ENTITY_TIME,
        "entity_id": ANCHOR_SENSOR,
        "offset": -45 * 60,
    }

    resolved = await async_forecast_anchor(resolvers, anchor, window)
    assert not isinstance(resolved, Unresolved)
    assert resolved.instants == (datetime(2026, 10, 2, 17, 49, tzinfo=NY),)
    # The horizon survives the offset, and so does the honesty it buys.
    assert resolved.horizon == Horizon.next_only()
    assert not resolved.fully_known


async def test_both_edges_of_a_zero_length_span_agree(
    resolvers: ResolverRegistry,
) -> None:
    """§5.1 — an anchor takes one edge of one span, and a zman has only one.

    The branch exists for step 7, where `edge: end` is havdalah. Checking it now
    is what makes that step an implementation rather than a change to the
    contract.
    """
    window = ny_window(datetime(2026, 10, 2, 0, 0, tzinfo=NY), 1)
    base = {"kind": ANCHOR_RESOLVER, "domain": RESOLVER_SUN, "key": SUN_SUNSET}

    start = await async_forecast_anchor(resolvers, base | {"edge": "start"}, window)
    end = await async_forecast_anchor(resolvers, base | {"edge": "end"}, window)
    assert not isinstance(start, Unresolved)
    assert not isinstance(end, Unresolved)
    assert start.instants == end.instants


async def test_an_anchor_for_a_resolver_that_is_not_present_is_unresolved(
    resolvers: ResolverRegistry,
) -> None:
    """The assumption `schema.py` states, checked against the registry that arrived.

    A stored anchor naming a domain this build does not have must load and render
    as unresolved. Failing the schedule instead would mean one absent resolver
    took unrelated schedules with it — the failure D17 rejects.

    The example was `hdate` until step 7 shipped it, which is the point: the
    schema deliberately does not consult the registry, so *any* domain can be
    absent, including one a later step adds or an earlier one removes. Rewritten
    to a domain nothing plans to build rather than deleted, because the property
    outlives whichever resolver happens to be missing today.
    """
    window = ny_window(datetime(2026, 10, 2, 0, 0, tzinfo=NY), 1)
    anchor = {
        "kind": ANCHOR_RESOLVER,
        "domain": "tides",
        "key": "high_water",
        "offset": -2700,
        "edge": "start",
    }
    result = await async_forecast_anchor(resolvers, anchor, window)
    assert isinstance(result, Unresolved)
    assert result.reason is UnresolvedReason.UNKNOWN_DOMAIN


async def test_an_anchor_of_no_known_kind_is_unresolved(
    resolvers: ResolverRegistry,
) -> None:
    """D6's union is closed; anything else is a store from the future."""
    window = ny_window(datetime(2026, 10, 2, 0, 0, tzinfo=NY), 1)
    result = await async_forecast_anchor(resolvers, {"kind": "moon"}, window)
    assert isinstance(result, Unresolved)


# --- wiring ----------------------------------------------------------------


async def test_the_config_entry_carries_a_registry(setup_almanac: object) -> None:
    """D14 — one internal registry, with the same lifetime as the schedules.

    Four domains since step 7, and two of them selectable: `async_offerings()`
    lists only the resolvers with a pick-list (D86), so `clock` and `entity_time`
    are absent from it by construction rather than by omission.
    """
    data = setup_almanac.runtime_data  # type: ignore[attr-defined]
    assert set(data.resolvers.async_domains()) == {
        "clock",
        "entity_time",
        "hdate",
        "sun",
    }
    assert set(data.resolvers.async_offerings()) == {"hdate", "sun"}
