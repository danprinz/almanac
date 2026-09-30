"""The rule engine — D2–D5 and D38–D41, at written-down instants.

Every assertion here names its `now`. That is D64 working as intended rather than
test hygiene: the decisions most likely to be wrong are at-or-after pairing across
midnight (D38), the DST rules (D40) and the recovery asymmetry (D41), and none of
them can be checked at all by a system that reads the wall clock internally. The
DST cases use `America/New_York`, forward on 2026-03-08 and back on 2026-11-01,
matching `test_resolvers.py` so the two layers are asserted at the same transitions.

Schedules are built through `STORAGE_SCHEMA` wherever the point is engine
behaviour, so the tests exercise the same fully-defaulted dictionaries the store
holds. The few that bypass it do so deliberately, to construct a schedule D39 now
refuses at save but which an older store could still contain.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import voluptuous as vol

from homeassistant.core import HomeAssistant

from custom_components.almanac.const import (
    ANCHOR_CLOCK,
    ANCHOR_ENTITY_TIME,
    ANCHOR_RESOLVER,
    ENUMERATION_HORIZON_DAYS,
    RECUR_DATES,
    RECUR_DAY_SET,
    RECUR_EVERY_N,
    RECUR_NTH_WEEKDAY,
    RECUR_WEEKDAYS,
    RESOLVER_SUN,
    RULE_AT,
    RULE_DURING,
    SUN_SUNSET,
)
from custom_components.almanac.engine import (
    EngineState,
    ExitCause,
    HeldInterval,
    OccurrenceStatus,
    TransitionKind,
    async_enumerate,
    async_plan_recovery,
    async_plan_tick,
    interval_problems,
    next_transition_at,
    recurrence_period,
    start_dates,
)
from custom_components.almanac.resolver import (
    ResolverRegistry,
    Unresolved,
    UnresolvedReason,
    Window,
    async_create_registry,
)
from custom_components.almanac.schema import STORAGE_SCHEMA

NY = ZoneInfo("America/New_York")

LATITUDE = 40.7128
LONGITUDE = -74.0060

CANDLE = "sensor.candle_lighting"
HAVDALAH = "sensor.havdalah"


# --- helpers ---------------------------------------------------------------


def ny(*args: int) -> datetime:
    """A New York instant, written out."""
    return datetime(*args, tzinfo=NY)  # type: ignore[arg-type]


def window(start: datetime, days: int) -> Window:
    """A window of whole civil days from a written-down instant."""
    return Window(start, start + timedelta(days=days))


def schedule(**body: Any) -> dict[str, Any]:
    """A stored schedule, validated, so tests see the same defaults the store has."""
    return dict(
        STORAGE_SCHEMA(
            {
                "id": "sched",
                "name": "Test schedule",
                "object_id": "test_schedule",
                **body,
            }
        )
    )


def clock_anchor(at: str, **extra: Any) -> dict[str, Any]:
    """A `kind: clock` anchor. D6 gives it no offset field."""
    return {"kind": ANCHOR_CLOCK, "at": at, **extra}


def entity_anchor(entity_id: str, offset: int = 0) -> dict[str, Any]:
    """A `kind: entity_time` anchor."""
    return {"kind": ANCHOR_ENTITY_TIME, "entity_id": entity_id, "offset": offset}


def at_rule(anchor: dict[str, Any], rule_id: str = "r1", **extra: Any) -> dict[str, Any]:
    """An `At` rule (D2)."""
    return {"kind": RULE_AT, "id": rule_id, "anchor": anchor, **extra}


def during_rule(
    start_anchor: dict[str, Any],
    end: dict[str, Any],
    rule_id: str = "r1",
    **extra: Any,
) -> dict[str, Any]:
    """A `During` rule (D2)."""
    return {
        "kind": RULE_DURING,
        "id": rule_id,
        "start_anchor": start_anchor,
        "end": end,
        **extra,
    }


def for_seconds(seconds: int) -> dict[str, Any]:
    """A duration end."""
    return {"kind": "duration", "duration": seconds}


def until_anchor(anchor: dict[str, Any]) -> dict[str, Any]:
    """An anchor end — the half D38 is about."""
    return {"kind": "anchor", "anchor": anchor}


def daily() -> dict[str, Any]:
    """Every day."""
    return {"kind": RECUR_WEEKDAYS, "weekdays": list("mon tue wed thu fri sat sun".split())}


def on_weekdays(*names: str) -> dict[str, Any]:
    """A weekday recurrence."""
    return {"kind": RECUR_WEEKDAYS, "weekdays": list(names)}


@pytest.fixture(autouse=True)
async def _located(hass: HomeAssistant) -> None:
    """Put `hass` in New York, which is all the engine reads from it."""
    await hass.config.async_set_time_zone("America/New_York")
    await hass.config.async_update(latitude=LATITUDE, longitude=LONGITUDE, elevation=0)


@pytest.fixture
def resolvers(hass: HomeAssistant) -> ResolverRegistry:
    """The in-tree resolvers, wired as the config entry wires them."""
    return async_create_registry(hass)


# --- D1: recurrence picks start dates --------------------------------------


def test_weekday_recurrence_picks_only_the_named_days() -> None:
    """The base case every other recurrence assertion departs from."""
    days = start_dates(on_weekdays("fri", "sat"), date(2026, 10, 1), date(2026, 10, 11))

    assert days == [
        date(2026, 10, 2),
        date(2026, 10, 3),
        date(2026, 10, 9),
        date(2026, 10, 10),
    ]


def test_a_date_range_is_inclusive_at_both_ends() -> None:
    """Unlike a `Window`. A date a person typed names a day they meant to include."""
    days = start_dates(on_weekdays("thu"), date(2026, 10, 1), date(2026, 10, 1))

    assert days == [date(2026, 10, 1)]


def test_listed_dates_are_clipped_and_sorted() -> None:
    """A `dates` recurrence is a set, not a sequence, so order is not the user's."""
    recurrence = {
        "kind": RECUR_DATES,
        "dates": ["2026-10-09", "2026-10-02", "2026-12-25"],
    }

    days = start_dates(recurrence, date(2026, 10, 1), date(2026, 10, 31))

    assert days == [date(2026, 10, 2), date(2026, 10, 9)]


def test_nth_weekday_counts_within_the_month() -> None:
    """The 2nd Tuesday of October 2026 is the 13th."""
    recurrence = {"kind": RECUR_NTH_WEEKDAY, "nth": 2, "weekday": "tue"}

    days = start_dates(recurrence, date(2026, 10, 1), date(2026, 10, 31))

    assert days == [date(2026, 10, 13)]


def test_nth_weekday_minus_one_is_the_last_one() -> None:
    """`-1` is the last such weekday, which is not the 4th in a five-week month."""
    recurrence = {"kind": RECUR_NTH_WEEKDAY, "nth": -1, "weekday": "fri"}

    days = start_dates(recurrence, date(2026, 10, 1), date(2026, 10, 31))

    assert days == [date(2026, 10, 30)]


def test_a_month_without_a_fifth_weekday_yields_nothing_for_it() -> None:
    """Not silently the fourth. The user asked for a date that does not exist."""
    recurrence = {"kind": RECUR_NTH_WEEKDAY, "nth": 5, "weekday": "sun"}

    days = start_dates(recurrence, date(2026, 10, 1), date(2026, 11, 30))

    # October 2026 has four Sundays and produces nothing. November begins on one,
    # so it has five and the 29th is picked.
    assert days == [date(2026, 11, 29)]


def test_every_n_is_not_extended_backwards_past_its_origin() -> None:
    """"Every third day from the 4th" names a start, not a phase."""
    recurrence = {"kind": RECUR_EVERY_N, "interval": 3, "from": "2026-10-04"}

    days = start_dates(recurrence, date(2026, 10, 1), date(2026, 10, 12))

    assert days == [date(2026, 10, 4), date(2026, 10, 7), date(2026, 10, 10)]


def test_a_day_set_recurrence_is_not_computed_rather_than_wrong() -> None:
    """A day set is never approximated by the pure date generator.

    Step 3 reported NOT_IMPLEMENTED here. Step 4 implements it — in
    `engine/day_set.py`, because both of D11's stages need the day-set collection
    and the resolver registry — so this function keeps a guard rather than a gap.
    Either way the contract that matters is the same one: `start_dates` does not
    guess. Returned rather than raised because this function always returns a
    value.
    """
    result = start_dates({"kind": RECUR_DAY_SET, "day_set_id": "shabbat"}, date(2026, 10, 1), date(2026, 10, 7))

    assert isinstance(result, Unresolved)
    assert result.reason is UnresolvedReason.ERROR
    assert "day_set.py" in result.detail


@pytest.mark.parametrize(
    ("recurrence", "expected"),
    [
        (on_weekdays("fri"), timedelta(days=7)),
        # {fri, sat} has a one-day gap and a six-day one. D39 must reject on the
        # strength of the short one.
        (on_weekdays("fri", "sat"), timedelta(days=1)),
        (on_weekdays("mon", "thu"), timedelta(days=3)),
        ({"kind": RECUR_EVERY_N, "interval": 3, "from": "2026-10-04"}, timedelta(days=3)),
        ({"kind": RECUR_NTH_WEEKDAY, "nth": 1, "weekday": "thu"}, timedelta(days=28)),
        ({"kind": RECUR_DATES, "dates": ["2026-10-02"]}, None),
        ({"kind": RECUR_DATES, "dates": ["2026-10-02", "2026-10-04"]}, timedelta(days=2)),
        ({"kind": RECUR_DAY_SET, "day_set_id": "shabbat"}, None),
    ],
)
def test_the_recurrence_period_is_the_shortest_possible_gap(
    recurrence: dict[str, Any], expected: timedelta | None
) -> None:
    """Shortest, not typical: both D38 and D39 need the conservative figure."""
    assert recurrence_period(recurrence) == expected


# --- D2: the two rule shapes -----------------------------------------------


async def test_an_at_rule_resolves_to_one_instant_per_recurrence_date(
    resolvers: ResolverRegistry,
) -> None:
    """D2's first shape, and the baseline for everything after it."""
    item = schedule(recurrence=daily(), rules=[at_rule(clock_anchor("17:00:00"))])

    plan = await async_enumerate(resolvers, item, window(ny(2026, 10, 2), 3))

    assert [occ.start for occ in plan.occurrences] == [
        ny(2026, 10, 2, 17),
        ny(2026, 10, 3, 17),
        ny(2026, 10, 4, 17),
    ]
    assert all(occ.status is OccurrenceStatus.SCHEDULED for occ in plan.occurrences)
    assert all(occ.end is None for occ in plan.occurrences)


async def test_a_during_rule_resolves_to_an_interval(
    resolvers: ResolverRegistry,
) -> None:
    """D2's second shape. An interval, not two events to be re-paired later."""
    item = schedule(
        recurrence=daily(),
        rules=[during_rule(clock_anchor("18:00:00"), for_seconds(3600))],
    )

    plan = await async_enumerate(resolvers, item, window(ny(2026, 10, 2), 1))

    (occ,) = plan.occurrences
    assert occ.is_interval
    assert (occ.start, occ.end) == (ny(2026, 10, 2, 18), ny(2026, 10, 2, 19))


async def test_an_interval_crossing_midnight_is_one_occurrence_on_one_date(
    resolvers: ResolverRegistry,
) -> None:
    """D1 — 23:00 to 02:00 needs no special case, because recurrence picks starts."""
    item = schedule(
        recurrence=daily(),
        rules=[during_rule(clock_anchor("23:00:00"), for_seconds(3 * 3600))],
    )

    plan = await async_enumerate(resolvers, item, window(ny(2026, 10, 2), 1))

    # Two, and both belong here: the window's first two hours are the tail of the
    # occurrence that began the night before, which is the same fact the next test
    # isolates.
    assert [(occ.start_date, occ.start, occ.end) for occ in plan.occurrences] == [
        (date(2026, 10, 1), ny(2026, 10, 1, 23), ny(2026, 10, 2, 2)),
        (date(2026, 10, 2), ny(2026, 10, 2, 23), ny(2026, 10, 3, 2)),
    ]


async def test_an_interval_that_began_before_the_window_is_still_enumerated(
    resolvers: ResolverRegistry,
) -> None:
    """The reason the search range is wider than the window (D1).

    A window covering only the small hours of the 3rd must still see the interval
    that began at 23:00 on the 2nd, or "what is running right now" is wrong for
    three hours of every night.
    """
    item = schedule(
        recurrence=daily(),
        rules=[during_rule(clock_anchor("23:00:00"), for_seconds(3 * 3600))],
    )

    plan = await async_enumerate(
        resolvers, item, Window(ny(2026, 10, 3, 0), ny(2026, 10, 3, 6))
    )

    (occ,) = plan.occurrences
    assert occ.start_date == date(2026, 10, 2)
    assert occ.holds_at(ny(2026, 10, 3, 1))


async def test_a_negative_offset_can_pull_an_occurrence_back_into_the_window(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """The other half of the padding, and the motivating case for it.

    "Forty-five minutes before candle lighting" resolves onto the day *before* the
    recurrence date whenever candle lighting is early enough. A search range equal
    to the window would lose it.
    """
    hass.states.async_set(CANDLE, "2026-10-03T00:20:00-04:00")
    item = schedule(
        recurrence=on_weekdays("sat"),
        rules=[at_rule(entity_anchor(CANDLE, offset=-45 * 60))],
    )

    plan = await async_enumerate(
        resolvers, item, Window(ny(2026, 10, 2, 0), ny(2026, 10, 3, 0))
    )

    (occ,) = plan.occurrences
    assert occ.start_date == date(2026, 10, 3)
    assert occ.start == ny(2026, 10, 2, 23, 35)


# --- D38: at-or-after pairing ----------------------------------------------


async def test_an_end_anchor_pairs_forward_across_midnight(
    resolvers: ResolverRegistry,
) -> None:
    """D38 — 02:00 is not on the same calendar day, and is still the right end."""
    item = schedule(
        recurrence=daily(),
        rules=[
            during_rule(clock_anchor("23:00:00"), until_anchor(clock_anchor("02:00:00")))
        ],
    )

    plan = await async_enumerate(resolvers, item, window(ny(2026, 10, 2), 1))

    assert [(occ.start, occ.end) for occ in plan.occurrences] == [
        (ny(2026, 10, 1, 23), ny(2026, 10, 2, 2)),
        (ny(2026, 10, 2, 23), ny(2026, 10, 3, 2)),
    ]


async def test_candle_lighting_pairs_with_havdalah_the_following_evening(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """The flagship case, and the reason ends could not be relative offsets.

    Two independent sources, a day apart, each drifting seasonally on its own. The
    engine is told nothing about Shabbat: D38's "first occurrence at or after the
    resolved start" is the entire mechanism.
    """
    hass.states.async_set(CANDLE, "2026-10-02T18:23:00-04:00")
    hass.states.async_set(HAVDALAH, "2026-10-03T19:22:00-04:00")
    item = schedule(
        recurrence=on_weekdays("fri"),
        rules=[during_rule(entity_anchor(CANDLE), until_anchor(entity_anchor(HAVDALAH)))],
    )

    plan = await async_enumerate(resolvers, item, window(ny(2026, 10, 2), 3))

    (occ,) = plan.occurrences
    assert occ.start_date == date(2026, 10, 2)
    assert (occ.start, occ.end) == (ny(2026, 10, 2, 18, 23), ny(2026, 10, 3, 19, 22))
    assert occ.holds_at(ny(2026, 10, 3, 4))


async def test_an_end_anchor_earlier_in_the_day_is_not_taken(
    resolvers: ResolverRegistry,
) -> None:
    """"At or after" (D38) — never the same day's earlier occurrence.

    Structurally this is why inversion cannot be constructed: there is no branch
    that produces an end before its start, so there is none to detect.
    """
    item = schedule(
        recurrence=daily(),
        rules=[
            during_rule(clock_anchor("20:00:00"), until_anchor(clock_anchor("06:00:00")))
        ],
    )

    plan = await async_enumerate(resolvers, item, window(ny(2026, 10, 2), 1))

    ends = {occ.start_date: occ.end for occ in plan.occurrences}
    assert ends[date(2026, 10, 2)] == ny(2026, 10, 3, 6)
    assert all(
        occ.end > occ.start  # type: ignore[operator]
        for occ in plan.occurrences
    )


async def test_an_end_exactly_at_the_start_is_accepted_and_holds_nowhere(
    resolvers: ResolverRegistry,
) -> None:
    """"At or after" includes "at". A zero-length interval never activates."""
    item = schedule(
        recurrence=daily(),
        rules=[
            during_rule(clock_anchor("06:00:00"), until_anchor(clock_anchor("06:00:00")))
        ],
    )

    plan = await async_enumerate(resolvers, item, window(ny(2026, 10, 2), 1))

    (occ,) = plan.occurrences
    assert occ.start == occ.end == ny(2026, 10, 2, 6)
    assert not occ.holds_at(ny(2026, 10, 2, 6))
    assert plan.runnable() == (occ,)


async def test_an_end_anchor_beyond_the_recurrence_period_does_not_pair(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """D39 bounds D38's search, so an overlong interval never forms.

    It renders as unresolved rather than silently running long (D12), and the
    reason says *pairing* rather than *unavailable* -- the resolver answered
    perfectly well.
    """
    hass.states.async_set(CANDLE, "2026-10-02T18:23:00-04:00")
    hass.states.async_set(HAVDALAH, "2026-11-20T19:22:00-05:00")
    item = schedule(
        recurrence=daily(),
        rules=[during_rule(entity_anchor(CANDLE), until_anchor(entity_anchor(HAVDALAH)))],
    )

    plan = await async_enumerate(resolvers, item, window(ny(2026, 10, 2), 1))

    (occ,) = plan.occurrences
    assert occ.status is OccurrenceStatus.UNRESOLVED
    assert occ.problem is not None
    assert occ.problem.reason is UnresolvedReason.NO_PAIRING
    assert not occ.will_run


# --- D39: intervals may not outlast their recurrence ----------------------


def test_an_interval_longer_than_its_recurrence_is_refused_at_save() -> None:
    """D39 — rejected, not coalesced. Predictability is the product."""
    item = schedule(
        recurrence=daily(),
        rules=[during_rule(clock_anchor("08:00:00"), for_seconds(30 * 3600))],
    )

    problems = interval_problems(item)

    assert len(problems) == 1
    assert "D39" in problems[0]


def test_an_interval_shorter_than_its_recurrence_is_accepted() -> None:
    """The ordinary 23:00 to 02:00 case is nowhere near the bound."""
    item = schedule(
        recurrence=daily(),
        rules=[during_rule(clock_anchor("23:00:00"), for_seconds(3 * 3600))],
    )

    assert interval_problems(item) == []


def test_a_weekend_pair_is_bounded_by_its_short_gap() -> None:
    """{fri, sat} repeats after one day, so a 25-hour interval is a D39 violation."""
    item = schedule(
        recurrence=on_weekdays("fri", "sat"),
        rules=[during_rule(clock_anchor("20:00:00"), for_seconds(25 * 3600))],
    )

    assert interval_problems(item) != []


def test_a_single_listed_date_has_no_period_to_violate() -> None:
    """`None` from `recurrence_period` means unknown, and unknown rejects nothing."""
    item = schedule(
        recurrence={"kind": RECUR_DATES, "dates": ["2026-10-02"]},
        rules=[during_rule(clock_anchor("08:00:00"), for_seconds(72 * 3600))],
    )

    assert interval_problems(item) == []


async def test_an_overlapping_occurrence_is_marked_rather_than_merged(
    resolvers: ResolverRegistry,
) -> None:
    """D39's backstop, for a schedule stored before the save-time check existed.

    Built by hand, because the collection now refuses this. The earlier interval
    keeps running and the later occurrence is marked, so the timeline can show the
    collision (D12) instead of coalescing it out of sight.
    """
    item = schedule(
        recurrence=daily(),
        rules=[during_rule(clock_anchor("08:00:00"), for_seconds(3600))],
    )
    item["rules"][0]["end"]["duration"] = 30 * 3600

    plan = await async_enumerate(resolvers, item, window(ny(2026, 10, 2), 3))

    # A 30-hour interval on a daily recurrence can only run on alternate days, so
    # every second occurrence is refused rather than merged into its predecessor.
    statuses = [occ.status for occ in plan.occurrences]
    assert OccurrenceStatus.SCHEDULED in statuses
    assert OccurrenceStatus.OVERLAPS_PREVIOUS in statuses
    for earlier, later in zip(statuses, statuses[1:], strict=False):
        assert earlier is not later
    assert not all(occ.will_run for occ in plan.occurrences)


async def test_the_collection_refuses_a_d39_violation_on_create(
    hass: HomeAssistant, almanac_data: Any
) -> None:
    """The save-time half of D39, through the real CRUD path."""
    with pytest.raises(vol.Invalid, match="D39"):
        await almanac_data.schedules.async_create_item(
            {
                "name": "Too long",
                "recurrence": daily(),
                "rules": [during_rule(clock_anchor("08:00:00"), for_seconds(30 * 3600))],
            }
        )


async def test_the_collection_checks_d39_against_the_merged_update(
    hass: HomeAssistant, almanac_data: Any
) -> None:
    """Either half of the relation can break it while arriving alone and valid."""
    item = await almanac_data.schedules.async_create_item(
        {
            "name": "Twice weekly",
            "recurrence": on_weekdays("mon", "thu"),
            "rules": [during_rule(clock_anchor("08:00:00"), for_seconds(48 * 3600))],
        }
    )

    with pytest.raises(vol.Invalid, match="D39"):
        await almanac_data.schedules.async_update_item(
            item["id"], {"recurrence": daily()}
        )


# --- D40: DST ---------------------------------------------------------------


async def test_a_clock_anchor_on_a_nonexistent_local_time_yields_no_occurrence(
    resolvers: ResolverRegistry,
) -> None:
    """D40 — on 2026-03-08 the clocks jump 02:00 to 03:00, so 02:30 never happens.

    A known nothing, not an unresolved occurrence: there is nothing the user could
    fix, and D40 says skipped and logged.
    """
    item = schedule(recurrence=daily(), rules=[at_rule(clock_anchor("02:30:00"))])

    plan = await async_enumerate(resolvers, item, window(ny(2026, 3, 7), 3))

    assert [occ.start_date for occ in plan.occurrences] == [
        date(2026, 3, 7),
        date(2026, 3, 9),
    ]


async def test_a_clock_anchor_on_an_ambiguous_local_time_fires_once(
    resolvers: ResolverRegistry,
) -> None:
    """D40 — 01:30 happens twice on 2026-11-01, and fires on the first.

    "The 02:30 schedule fired twice" is the complaint this rule answers.
    """
    item = schedule(recurrence=daily(), rules=[at_rule(clock_anchor("01:30:00"))])

    plan = await async_enumerate(
        resolvers, item, Window(ny(2026, 11, 1, 0), ny(2026, 11, 2, 0))
    )

    (occ,) = plan.occurrences
    assert occ.start is not None
    assert occ.start.utcoffset() == timedelta(hours=-4)


async def test_a_duration_is_elapsed_time_and_not_wall_clock(
    resolvers: ResolverRegistry,
) -> None:
    """D40 makes only *clock anchors* wall-clock. A length is a length.

    Four hours from 23:00 on the night the clocks go forward ends at 04:00 local,
    not 03:00: the interval is four hours long in both March and November, which is
    what "for four hours" means. Wall-clock addition would make it three hours in
    one direction and five in the other while still reading as four in the editor.
    """
    item = schedule(
        recurrence=daily(),
        rules=[during_rule(clock_anchor("23:00:00"), for_seconds(4 * 3600))],
    )

    plan = await async_enumerate(
        resolvers, item, Window(ny(2026, 3, 7, 12), ny(2026, 3, 8, 12))
    )

    (occ,) = plan.occurrences
    assert occ.start is not None and occ.end is not None
    # Compared in UTC on purpose. Python subtracts two aware datetimes that share a
    # `tzinfo` object by their naive values, so the wall-clock reading of this
    # interval is five hours and its actual length is four.
    assert occ.end.astimezone(UTC) - occ.start.astimezone(UTC) == timedelta(hours=4)
    assert occ.end == ny(2026, 3, 8, 4)
    assert occ.end - occ.start == timedelta(hours=5)


async def test_an_interval_ending_inside_the_repeated_hour_is_not_inverted(
    resolvers: ResolverRegistry,
) -> None:
    """The case that makes `absolute()` load-bearing rather than tidy.

    Fifteen minutes from 01:45 EDT on 2026-11-01 ends at 01:00 EST -- a *later*
    instant whose wall-clock reading is 45 minutes *earlier*. Python compares two
    aware datetimes sharing one `tzinfo` object by their naive values and `ZoneInfo`
    interns instances, so a direct comparison calls this interval inverted and
    `Occurrence.__post_init__` raises. Every comparison in the engine goes through
    `absolute()` because of this hour, not as a style.
    """
    item = schedule(
        recurrence=daily(),
        rules=[during_rule(clock_anchor("01:45:00"), for_seconds(15 * 60))],
    )

    plan = await async_enumerate(
        resolvers, item, Window(ny(2026, 11, 1, 0), ny(2026, 11, 2, 0))
    )

    (occ,) = plan.occurrences
    assert occ.start is not None and occ.end is not None
    assert occ.start.utcoffset() == timedelta(hours=-4)
    assert occ.end.utcoffset() == timedelta(hours=-5)
    # Reads as going backwards, and does not.
    assert occ.end.replace(tzinfo=None) < occ.start.replace(tzinfo=None)
    assert occ.holds_at(ny(2026, 11, 1, 1, 50))  # 01:50 EDT, inside
    assert occ.end.astimezone(UTC) - occ.start.astimezone(UTC) == timedelta(minutes=15)


async def test_a_resolver_anchor_is_unaffected_by_a_transition(
    resolvers: ResolverRegistry,
) -> None:
    """D40 — sun and entity_time anchors are absolute instants."""
    item = schedule(
        recurrence=daily(),
        rules=[
            at_rule({"kind": ANCHOR_RESOLVER, "domain": RESOLVER_SUN, "key": SUN_SUNSET})
        ],
    )

    plan = await async_enumerate(resolvers, item, window(ny(2026, 3, 7), 3))

    assert len(plan.occurrences) == 3
    assert all(occ.status is OccurrenceStatus.SCHEDULED for occ in plan.occurrences)


# --- D12 / D77: what the timeline has to be able to show -------------------


async def test_an_unresolvable_anchor_produces_a_visible_occurrence(
    resolvers: ResolverRegistry,
) -> None:
    """D12 — an occurrence that vanished is indistinguishable from a bug."""
    item = schedule(
        recurrence=on_weekdays("fri"),
        rules=[at_rule(entity_anchor("sensor.never_existed"))],
    )

    plan = await async_enumerate(resolvers, item, window(ny(2026, 10, 2), 1))

    (occ,) = plan.occurrences
    assert occ.status is OccurrenceStatus.UNRESOLVED
    assert occ.problem is not None
    assert occ.start is None
    assert not occ.will_run


async def test_a_disarmed_rule_still_appears_and_will_not_run(
    resolvers: ResolverRegistry,
) -> None:
    """D77 — "this would fire but the rule is off" is its own row in a list."""
    item = schedule(
        recurrence=daily(),
        rules=[at_rule(clock_anchor("17:00:00"), enabled=False)],
    )

    plan = await async_enumerate(resolvers, item, window(ny(2026, 10, 2), 1))

    (occ,) = plan.occurrences
    assert occ.status is OccurrenceStatus.SCHEDULED
    assert not occ.armed
    assert not occ.will_run
    assert plan.runnable() == ()


async def test_a_disabled_schedule_disarms_every_rule(
    resolvers: ResolverRegistry,
) -> None:
    """The switch (D54) disarms the whole thing without hiding it."""
    item = schedule(
        recurrence=daily(), enabled=False, rules=[at_rule(clock_anchor("17:00:00"))]
    )

    plan = await async_enumerate(resolvers, item, window(ny(2026, 10, 2), 1))

    assert plan.occurrences
    assert not any(occ.armed for occ in plan.occurrences)


async def test_a_date_window_bounds_the_schedule_inclusively(
    resolvers: ResolverRegistry,
) -> None:
    """`until: 2026-10-03` names a day the schedule still runs on."""
    item = schedule(
        recurrence=daily(),
        date_window={"from": "2026-10-03", "until": "2026-10-04"},
        rules=[at_rule(clock_anchor("17:00:00"))],
    )

    plan = await async_enumerate(resolvers, item, window(ny(2026, 10, 1), 7))

    assert [occ.start_date for occ in plan.occurrences] == [
        date(2026, 10, 3),
        date(2026, 10, 4),
    ]


async def test_a_closed_date_window_is_an_empty_plan_not_an_unknown_one(
    resolvers: ResolverRegistry,
) -> None:
    """Provably nothing, which is different from not computed and from unknown."""
    item = schedule(
        recurrence=daily(),
        date_window={"until": "2026-09-01"},
        rules=[at_rule(clock_anchor("17:00:00"))],
    )

    plan = await async_enumerate(resolvers, item, window(ny(2026, 10, 1), 7))

    assert plan.occurrences == ()
    assert plan.fully_computed
    assert plan.fully_known


# --- D44 and D13: two horizons, kept apart --------------------------------


async def test_the_compute_budget_clamps_the_window_and_says_so(
    resolvers: ResolverRegistry,
) -> None:
    """D44 — beyond 90 days the timeline says *not computed* rather than guessing."""
    item = schedule(recurrence=daily(), rules=[at_rule(clock_anchor("17:00:00"))])
    start = ny(2026, 10, 1)

    plan = await async_enumerate(resolvers, item, window(start, 200))

    assert plan.computed_through == start + timedelta(days=ENUMERATION_HORIZON_DAYS)
    assert not plan.fully_computed
    assert len(plan.occurrences) == ENUMERATION_HORIZON_DAYS


async def test_a_next_only_source_limits_what_is_known_not_what_was_computed(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """§10.6 — our budget and the source's honesty are different facts (D13, D44)."""
    hass.states.async_set(CANDLE, "2026-10-02T18:23:00-04:00")
    item = schedule(recurrence=daily(), rules=[at_rule(entity_anchor(CANDLE))])

    plan = await async_enumerate(resolvers, item, window(ny(2026, 10, 1), 10))

    assert plan.fully_computed
    assert not plan.fully_known
    assert plan.known_through == ny(2026, 10, 2, 18, 23)


async def test_a_deterministic_source_is_known_to_the_end_of_the_window(
    resolvers: ResolverRegistry,
) -> None:
    """Sun is arithmetic, so it declares an unbounded horizon."""
    item = schedule(
        recurrence=daily(),
        rules=[
            at_rule({"kind": ANCHOR_RESOLVER, "domain": RESOLVER_SUN, "key": SUN_SUNSET})
        ],
    )
    requested = window(ny(2026, 10, 1), 10)

    plan = await async_enumerate(resolvers, item, requested)

    assert plan.fully_known
    assert plan.known_through == requested.end


async def test_an_unevaluatable_recurrence_is_reported_once_on_the_plan(
    resolvers: ResolverRegistry,
) -> None:
    """The fault is the schedule's, not any rule's, so it is not copied per rule."""
    item = schedule(
        recurrence={"kind": RECUR_DAY_SET, "day_set_id": "shabbat"},
        rules=[at_rule(clock_anchor("22:00:00")), at_rule(clock_anchor("23:00:00"), "r2")],
    )

    plan = await async_enumerate(resolvers, item, window(ny(2026, 10, 1), 7))

    assert plan.occurrences == ()
    assert plan.problem is not None
    # Enumerated with no day-set collection, which is the cleanest way to make a
    # recurrence unevaluatable: the fault is one level above every rule, so the
    # test is about where it is reported rather than about what went wrong.
    assert plan.problem.reason is UnresolvedReason.ERROR


# --- D41: At recovery ------------------------------------------------------


async def test_an_observed_instant_fires(resolvers: ResolverRegistry) -> None:
    """The ordinary tick. Everything after `evaluated_through` was watched."""
    item = schedule(recurrence=daily(), rules=[at_rule(clock_anchor("17:00:00"))])
    state = EngineState(evaluated_through=ny(2026, 10, 2, 16, 59))

    result = await async_plan_tick(resolvers, item, state, now=ny(2026, 10, 2, 17, 0, 30))

    (transition,) = result.transitions
    assert transition.kind is TransitionKind.FIRE
    assert transition.at == ny(2026, 10, 2, 17)
    assert transition.lateness == timedelta(seconds=30)


async def test_a_slow_tick_still_fires_rather_than_needing_a_grace_window(
    resolvers: ResolverRegistry,
) -> None:
    """A late tick is a slow engine, not a missed occurrence.

    Applying the grace test to an instant the engine watched go by would make
    `grace` mandatory for a punctual system, which is the opposite of D41's intent.
    """
    item = schedule(recurrence=daily(), rules=[at_rule(clock_anchor("17:00:00"))])
    state = EngineState(evaluated_through=ny(2026, 10, 2, 16, 59))

    result = await async_plan_tick(resolvers, item, state, now=ny(2026, 10, 2, 17, 20))

    (transition,) = result.transitions
    assert transition.kind is TransitionKind.FIRE
    assert transition.lateness == timedelta(minutes=20)


async def test_a_missed_at_occurrence_is_recorded_and_not_fired(
    resolvers: ResolverRegistry,
) -> None:
    """D41 — the grace window is off by default, and a miss is still reported."""
    item = schedule(recurrence=daily(), rules=[at_rule(clock_anchor("17:00:00"))])
    state = EngineState(evaluated_through=ny(2026, 10, 2, 16))

    result = await async_plan_recovery(resolvers, item, state, now=ny(2026, 10, 2, 20))

    (transition,) = result.transitions
    assert transition.kind is TransitionKind.MISSED
    assert transition.lateness == timedelta(hours=3)


async def test_a_grace_window_fires_a_missed_occurrence(
    resolvers: ResolverRegistry,
) -> None:
    """D41 — opt in, and a restart inside the window replays the occurrence."""
    item = schedule(
        recurrence=daily(),
        rules=[at_rule(clock_anchor("17:00:00"), grace=4 * 3600)],
    )
    state = EngineState(evaluated_through=ny(2026, 10, 2, 16))

    result = await async_plan_recovery(resolvers, item, state, now=ny(2026, 10, 2, 20))

    (transition,) = result.transitions
    assert transition.kind is TransitionKind.FIRE
    assert transition.lateness == timedelta(hours=3)


async def test_a_grace_window_that_has_expired_still_misses(
    resolvers: ResolverRegistry,
) -> None:
    """The window is a bound, not a switch."""
    item = schedule(
        recurrence=daily(),
        rules=[at_rule(clock_anchor("17:00:00"), grace=3600)],
    )
    state = EngineState(evaluated_through=ny(2026, 10, 2, 16))

    result = await async_plan_recovery(resolvers, item, state, now=ny(2026, 10, 2, 20))

    (transition,) = result.transitions
    assert transition.kind is TransitionKind.MISSED


async def test_a_schedule_seen_for_the_first_time_fires_nothing_retroactively(
    resolvers: ResolverRegistry,
) -> None:
    """No `evaluated_through` means no unobserved stretch, however long the grace."""
    item = schedule(
        recurrence=daily(),
        rules=[at_rule(clock_anchor("17:00:00"), grace=12 * 3600)],
    )

    result = await async_plan_recovery(
        resolvers, item, EngineState(), now=ny(2026, 10, 2, 20)
    )

    assert result.transitions == ()
    assert result.state.evaluated_through == ny(2026, 10, 2, 20)


async def test_a_decided_occurrence_is_not_decided_twice(
    resolvers: ResolverRegistry,
) -> None:
    """Idempotence, which is what makes widening the lookback safe at all."""
    item = schedule(
        recurrence=daily(),
        rules=[at_rule(clock_anchor("17:00:00"), grace=6 * 3600)],
    )
    first = await async_plan_tick(
        resolvers, item, EngineState(evaluated_through=ny(2026, 10, 2, 16)),
        now=ny(2026, 10, 2, 18),
    )

    second = await async_plan_tick(
        resolvers, item, first.state, now=ny(2026, 10, 2, 18, 5)
    )

    assert [t.kind for t in first.transitions] == [TransitionKind.FIRE]
    assert second.transitions == ()


async def test_a_missed_occurrence_stays_missed(resolvers: ResolverRegistry) -> None:
    """A miss is recorded for idempotence, not for the audit trail.

    Without the record, the next evaluation inside the same lookback would
    re-decide it and eventually announce it late — the outcome D41's default exists
    to prevent.
    """
    item = schedule(
        recurrence=daily(),
        rules=[at_rule(clock_anchor("17:00:00"), grace=6 * 3600)],
    )
    first = await async_plan_recovery(
        resolvers, item, EngineState(evaluated_through=ny(2026, 10, 2, 10)),
        now=ny(2026, 10, 3, 1),
    )
    assert [t.kind for t in first.transitions] == [TransitionKind.MISSED]

    second = await async_plan_tick(resolvers, item, first.state, now=ny(2026, 10, 3, 2))

    assert second.transitions == ()


async def test_a_disarmed_at_occurrence_cannot_fire_retroactively(
    resolvers: ResolverRegistry,
) -> None:
    """D77's disarmed occurrence did not happen, so re-enabling must not replay it."""
    disabled = schedule(
        recurrence=daily(),
        rules=[at_rule(clock_anchor("17:00:00"), enabled=False, grace=6 * 3600)],
    )
    first = await async_plan_tick(
        resolvers, disabled, EngineState(evaluated_through=ny(2026, 10, 2, 16)),
        now=ny(2026, 10, 2, 18),
    )
    assert first.transitions == ()

    enabled = schedule(
        recurrence=daily(),
        rules=[at_rule(clock_anchor("17:00:00"), grace=6 * 3600)],
    )
    second = await async_plan_tick(
        resolvers, enabled, first.state, now=ny(2026, 10, 2, 19)
    )

    assert second.transitions == ()


# --- D42: late arrivals reuse D41's machinery -----------------------------


async def test_an_anchor_that_recovers_late_reuses_the_grace_window(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """D42 — an unresolved occurrence is not *decided*, so it can arrive late.

    This is the whole reason the lookback reaches past `evaluated_through`: by the
    time the anchor can answer, its instant is already behind us.
    """
    item = schedule(
        recurrence=daily(),
        rules=[at_rule(entity_anchor(CANDLE), grace=4 * 3600)],
    )
    hass.states.async_set(CANDLE, "unknown")
    first = await async_plan_tick(
        resolvers, item, EngineState(evaluated_through=ny(2026, 10, 2, 17)),
        now=ny(2026, 10, 2, 19),
    )
    assert first.transitions == ()

    hass.states.async_set(CANDLE, "2026-10-02T18:23:00-04:00")
    second = await async_plan_tick(
        resolvers, item, first.state, now=ny(2026, 10, 2, 19, 5)
    )

    (transition,) = second.transitions
    assert transition.kind is TransitionKind.FIRE
    assert transition.at == ny(2026, 10, 2, 18, 23)


async def test_an_anchor_recovering_without_a_grace_window_fires_nothing(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """D42 defers to D41's default, which is off — so the default is to log it."""
    item = schedule(recurrence=daily(), rules=[at_rule(entity_anchor(CANDLE))])
    hass.states.async_set(CANDLE, "unknown")
    first = await async_plan_tick(
        resolvers, item, EngineState(evaluated_through=ny(2026, 10, 2, 17)),
        now=ny(2026, 10, 2, 19),
    )

    hass.states.async_set(CANDLE, "2026-10-02T18:23:00-04:00")
    second = await async_plan_tick(
        resolvers, item, first.state, now=ny(2026, 10, 2, 19, 5)
    )

    # Nothing, either time. D41's default is not to fire, and the engine will not
    # emit a `MISSED` transition for an occurrence it could never place -- see the
    # note in `_async_reconcile`. The plan is where the user sees it (D12).
    assert first.transitions == ()
    assert second.transitions == ()
    (occ,) = first.plan.occurrences
    assert occ.status is OccurrenceStatus.UNRESOLVED
    assert occ.problem is not None


async def test_an_interval_whose_anchor_recovers_mid_window_enters_late(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """D42 — intervals are idempotent, so this is simply correct and needs no opt-in."""
    item = schedule(
        recurrence=daily(),
        rules=[during_rule(entity_anchor(CANDLE), for_seconds(6 * 3600))],
    )
    hass.states.async_set(CANDLE, "unavailable")
    first = await async_plan_tick(
        resolvers, item, EngineState(evaluated_through=ny(2026, 10, 2, 18)),
        now=ny(2026, 10, 2, 19),
    )
    assert first.transitions == ()

    hass.states.async_set(CANDLE, "2026-10-02T18:23:00-04:00")
    second = await async_plan_tick(
        resolvers, item, first.state, now=ny(2026, 10, 2, 19, 30)
    )

    (transition,) = second.transitions
    assert transition.kind is TransitionKind.ENTER
    assert transition.lateness == timedelta(minutes=67)
    assert len(second.state.held) == 1


# --- D41: During reconciliation -------------------------------------------


async def test_an_interval_is_entered_and_then_exited(
    resolvers: ResolverRegistry,
) -> None:
    """The base case, and the state hand-off between two ticks."""
    item = schedule(
        recurrence=daily(),
        rules=[during_rule(clock_anchor("22:00:00"), for_seconds(4 * 3600))],
    )

    entered = await async_plan_tick(
        resolvers, item, EngineState(evaluated_through=ny(2026, 10, 2, 21, 59)),
        now=ny(2026, 10, 2, 22, 0, 10),
    )
    exited = await async_plan_tick(
        resolvers, item, entered.state, now=ny(2026, 10, 3, 2, 0, 5)
    )

    assert [t.kind for t in entered.transitions] == [TransitionKind.ENTER]
    assert len(entered.state.held) == 1
    (transition,) = exited.transitions
    assert transition.kind is TransitionKind.EXIT
    assert transition.cause is ExitCause.WINDOW_END
    assert transition.at == ny(2026, 10, 3, 2)
    assert exited.state.held == ()


async def test_a_running_interval_produces_no_transition_on_an_ordinary_tick(
    resolvers: ResolverRegistry,
) -> None:
    """Nothing happens in the middle of an interval, and nothing should."""
    item = schedule(
        recurrence=daily(),
        rules=[during_rule(clock_anchor("22:00:00"), for_seconds(4 * 3600))],
    )
    entered = await async_plan_tick(
        resolvers, item, EngineState(evaluated_through=ny(2026, 10, 2, 21, 59)),
        now=ny(2026, 10, 2, 22, 0, 10),
    )

    middle = await async_plan_tick(resolvers, item, entered.state, now=ny(2026, 10, 3, 0))

    assert middle.transitions == ()
    assert len(middle.state.held) == 1


async def test_a_restart_mid_interval_resumes_rather_than_re_entering(
    resolvers: ResolverRegistry,
) -> None:
    """D41's "reconcile" is not "enter".

    The desired state may need re-applying because the world changed while we were
    down, but the enter actions already ran. **Provisional** — D41 does not name
    the distinction; collapsing the two would re-announce every interval a restart
    happens inside.
    """
    item = schedule(
        recurrence=daily(),
        rules=[during_rule(clock_anchor("22:00:00"), for_seconds(4 * 3600))],
    )
    held = HeldInterval(
        schedule_id="sched",
        rule_id="r1",
        start_date=date(2026, 10, 2),
        start=ny(2026, 10, 2, 22),
        end=ny(2026, 10, 3, 2),
        entered_at=ny(2026, 10, 2, 22),
    )
    state = EngineState(held=(held,), evaluated_through=ny(2026, 10, 2, 23))

    result = await async_plan_recovery(resolvers, item, state, now=ny(2026, 10, 3, 0))

    (transition,) = result.transitions
    assert transition.kind is TransitionKind.RESUME
    assert transition.at == ny(2026, 10, 2, 22)
    assert len(result.state.held) == 1


async def test_a_restart_inside_an_interval_it_never_entered_enters_it(
    resolvers: ResolverRegistry,
) -> None:
    """D41 — "should this be active now?" does not consult history."""
    item = schedule(
        recurrence=daily(),
        rules=[during_rule(clock_anchor("22:00:00"), for_seconds(4 * 3600))],
    )

    result = await async_plan_recovery(
        resolvers, item, EngineState(evaluated_through=ny(2026, 10, 1, 12)),
        now=ny(2026, 10, 3, 0),
    )

    (transition,) = result.transitions
    assert transition.kind is TransitionKind.ENTER
    assert transition.lateness == timedelta(hours=2)


async def test_an_interval_that_ended_while_we_were_down_exits_late(
    resolvers: ResolverRegistry,
) -> None:
    """The exit is owed, however late. Failing to run it leaves the lights on."""
    item = schedule(
        recurrence=daily(),
        rules=[during_rule(clock_anchor("22:00:00"), for_seconds(4 * 3600))],
    )
    held = HeldInterval(
        schedule_id="sched",
        rule_id="r1",
        start_date=date(2026, 10, 2),
        start=ny(2026, 10, 2, 22),
        end=ny(2026, 10, 3, 2),
        entered_at=ny(2026, 10, 2, 22),
    )
    state = EngineState(held=(held,), evaluated_through=ny(2026, 10, 2, 23))

    result = await async_plan_recovery(resolvers, item, state, now=ny(2026, 10, 3, 9))

    (transition,) = result.transitions
    assert transition.kind is TransitionKind.EXIT
    assert transition.cause is ExitCause.WINDOW_END
    assert transition.at == ny(2026, 10, 3, 2)
    assert transition.lateness == timedelta(hours=7)
    assert result.state.held == ()


async def test_disarming_a_rule_mid_interval_exits_it(
    resolvers: ResolverRegistry,
) -> None:
    """**Provisional** — D47 settles the completion case and not this one.

    Read as immediate, because otherwise turning a schedule off appears to do
    nothing until an hour the user cannot see. The exit path runs either way, so
    D47's actual concern — never exiting while holding a state we created — is met.
    """
    running = schedule(
        recurrence=daily(),
        rules=[during_rule(clock_anchor("22:00:00"), for_seconds(4 * 3600))],
    )
    entered = await async_plan_tick(
        resolvers, running, EngineState(evaluated_through=ny(2026, 10, 2, 21, 59)),
        now=ny(2026, 10, 2, 22, 0, 10),
    )
    switched_off = schedule(
        recurrence=daily(),
        enabled=False,
        rules=[during_rule(clock_anchor("22:00:00"), for_seconds(4 * 3600))],
    )

    result = await async_plan_tick(
        resolvers, switched_off, entered.state, now=ny(2026, 10, 3, 0)
    )

    (transition,) = result.transitions
    assert transition.kind is TransitionKind.EXIT
    assert transition.cause is ExitCause.DISARMED
    assert transition.at == ny(2026, 10, 3, 0)
    assert result.state.held == ()


async def test_deleting_a_rule_mid_interval_still_runs_its_exit(
    resolvers: ResolverRegistry,
) -> None:
    """D3's exit path runs even for a rule that no longer exists.

    D47 states the principle for termination and it applies just as much to an
    edit: a schedule must not vanish leaving the world in a state it created.
    """
    item = schedule(recurrence=daily(), rules=[at_rule(clock_anchor("08:00:00"))])
    held = HeldInterval(
        schedule_id="sched",
        rule_id="deleted",
        start_date=date(2026, 10, 2),
        start=ny(2026, 10, 2, 22),
        end=ny(2026, 10, 3, 2),
        entered_at=ny(2026, 10, 2, 22),
    )
    state = EngineState(held=(held,), evaluated_through=ny(2026, 10, 2, 23))

    result = await async_plan_tick(resolvers, item, state, now=ny(2026, 10, 3, 0))

    exits = [t for t in result.transitions if t.kind is TransitionKind.EXIT]
    assert [t.cause for t in exits] == [ExitCause.GONE]
    assert result.state.held == ()


async def test_a_held_interval_honours_its_recorded_end_when_the_anchor_breaks(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """The reason `HeldInterval` carries its own end.

    The end anchor going unavailable mid-interval must not make the interval
    immortal, and must not cut it short either: the engine honours the promise it
    made when it entered.
    """
    item = schedule(
        recurrence=daily(),
        rules=[during_rule(clock_anchor("22:00:00"), until_anchor(entity_anchor(HAVDALAH)))],
    )
    hass.states.async_set(HAVDALAH, "2026-10-03T02:00:00-04:00")
    entered = await async_plan_tick(
        resolvers, item, EngineState(evaluated_through=ny(2026, 10, 2, 21, 59)),
        now=ny(2026, 10, 2, 22, 0, 10),
    )
    assert [t.kind for t in entered.transitions] == [TransitionKind.ENTER]

    hass.states.async_set(HAVDALAH, "unavailable")
    middle = await async_plan_tick(resolvers, item, entered.state, now=ny(2026, 10, 3, 1))
    after = await async_plan_tick(resolvers, item, middle.state, now=ny(2026, 10, 3, 3))

    assert middle.transitions == ()
    assert len(middle.state.held) == 1
    (transition,) = after.transitions
    assert transition.kind is TransitionKind.EXIT
    assert transition.cause is ExitCause.WINDOW_END
    assert transition.at == ny(2026, 10, 3, 2)


async def test_an_end_anchor_that_moves_moves_the_exit(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """D43's invalidation, seen from the engine: the enumerated end wins."""
    item = schedule(
        recurrence=daily(),
        rules=[during_rule(clock_anchor("22:00:00"), until_anchor(entity_anchor(HAVDALAH)))],
    )
    hass.states.async_set(HAVDALAH, "2026-10-03T02:00:00-04:00")
    entered = await async_plan_tick(
        resolvers, item, EngineState(evaluated_through=ny(2026, 10, 2, 21, 59)),
        now=ny(2026, 10, 2, 22, 0, 10),
    )

    hass.states.async_set(HAVDALAH, "2026-10-03T00:30:00-04:00")
    result = await async_plan_tick(resolvers, item, entered.state, now=ny(2026, 10, 3, 1))

    (transition,) = result.transitions
    assert transition.kind is TransitionKind.EXIT
    assert transition.at == ny(2026, 10, 3, 0, 30)


async def test_a_zero_length_interval_is_never_entered(
    resolvers: ResolverRegistry,
) -> None:
    """D38 permits an end at the start, and an instant has no interior."""
    item = schedule(
        recurrence=daily(),
        rules=[
            during_rule(clock_anchor("06:00:00"), until_anchor(clock_anchor("06:00:00")))
        ],
    )

    result = await async_plan_tick(
        resolvers, item, EngineState(evaluated_through=ny(2026, 10, 2, 5, 59)),
        now=ny(2026, 10, 2, 6, 0, 1),
    )

    assert result.transitions == ()
    assert result.state.held == ()


async def test_a_handover_exits_before_it_enters(resolvers: ResolverRegistry) -> None:
    """Back-to-back intervals must not overlap at the instant they change hands.

    The outgoing rule's exit path has to run before the incoming one's desired
    state, or the two fight over the same entity and the winner is whichever was
    enumerated first.
    """
    item = schedule(
        recurrence=daily(),
        rules=[
            during_rule(clock_anchor("20:00:00"), for_seconds(2 * 3600), "evening"),
            during_rule(clock_anchor("22:00:00"), for_seconds(3600), "night"),
        ],
    )
    entered = await async_plan_tick(
        resolvers, item, EngineState(evaluated_through=ny(2026, 10, 2, 19, 59)),
        now=ny(2026, 10, 2, 20, 0, 1),
    )

    result = await async_plan_tick(resolvers, item, entered.state, now=ny(2026, 10, 2, 22))

    assert [(t.kind, t.rule_id) for t in result.transitions] == [
        (TransitionKind.EXIT, "evening"),
        (TransitionKind.ENTER, "night"),
    ]


async def test_consecutive_occurrences_of_one_rule_hand_over(
    resolvers: ResolverRegistry,
) -> None:
    """Different recurrence dates are different keys, so a hand-over is possible.

    An interval running from 23:00 to 02:00 every night is held under the date it
    began on, which is what stops the next night's occurrence from being mistaken
    for the one already held.
    """
    item = schedule(
        recurrence=daily(),
        rules=[during_rule(clock_anchor("23:00:00"), for_seconds(3 * 3600))],
    )
    first = await async_plan_tick(
        resolvers, item, EngineState(evaluated_through=ny(2026, 10, 2, 22, 59)),
        now=ny(2026, 10, 2, 23, 0, 1),
    )
    assert first.state.held[0].start_date == date(2026, 10, 2)

    second = await async_plan_tick(resolvers, item, first.state, now=ny(2026, 10, 3, 23, 1))

    assert [t.kind for t in second.transitions] == [
        TransitionKind.EXIT,
        TransitionKind.ENTER,
    ]
    assert [held.start_date for held in second.state.held] == [date(2026, 10, 3)]


# --- what the sensor and the tick scheduler read --------------------------


async def test_the_next_transition_is_the_earliest_future_edge(
    resolvers: ResolverRegistry,
) -> None:
    """D55's sensor, and pure at a parameterised `now` (D64)."""
    item = schedule(
        recurrence=daily(),
        rules=[during_rule(clock_anchor("22:00:00"), for_seconds(4 * 3600))],
    )
    plan = await async_enumerate(resolvers, item, window(ny(2026, 10, 2), 2))

    assert next_transition_at(plan, EngineState(), ny(2026, 10, 2, 12)) == ny(
        2026, 10, 2, 22
    )
    assert next_transition_at(plan, EngineState(), ny(2026, 10, 2, 23)) == ny(
        2026, 10, 3, 2
    )


async def test_the_next_transition_includes_a_held_end_the_plan_no_longer_has(
    resolvers: ResolverRegistry,
) -> None:
    """The exit is owed even when the schedule can no longer produce it."""
    item = schedule(recurrence=daily(), rules=[at_rule(clock_anchor("08:00:00"))])
    plan = await async_enumerate(resolvers, item, window(ny(2026, 10, 2), 1))
    held = HeldInterval(
        schedule_id="sched",
        rule_id="deleted",
        start_date=date(2026, 10, 2),
        start=ny(2026, 10, 2, 22),
        end=ny(2026, 10, 3, 2),
        entered_at=ny(2026, 10, 2, 22),
    )

    assert next_transition_at(
        plan, EngineState(held=(held,)), ny(2026, 10, 2, 23)
    ) == ny(2026, 10, 3, 2)


async def test_a_reconciliation_reports_the_next_transition_it_leaves_behind(
    resolvers: ResolverRegistry,
) -> None:
    """The tick gets its own next wake-up out of the same result."""
    item = schedule(
        recurrence=daily(),
        rules=[during_rule(clock_anchor("22:00:00"), for_seconds(4 * 3600))],
    )

    result = await async_plan_tick(
        resolvers, item, EngineState(evaluated_through=ny(2026, 10, 2, 21, 59)),
        now=ny(2026, 10, 2, 22, 0, 10),
    )

    assert result.next_at == ny(2026, 10, 3, 2)


# --- D35: the runtime state round-trips -----------------------------------


def test_engine_state_survives_the_runtime_store() -> None:
    """D35 keeps this in its own store, so it has to serialise exactly."""
    state = EngineState(
        held=(
            HeldInterval(
                schedule_id="sched",
                rule_id="r1",
                start_date=date(2026, 10, 2),
                start=ny(2026, 10, 2, 23),
                end=ny(2026, 10, 3, 2),
                entered_at=ny(2026, 10, 2, 23, 0, 4),
            ),
        ),
        evaluated_through=ny(2026, 10, 3, 1),
    )

    assert EngineState.from_dict(state.as_dict()) == state


def test_an_engine_state_written_before_a_field_existed_still_loads() -> None:
    """A forward migration adds keys, so an absent one is legitimate (D37)."""
    assert EngineState.from_dict({}) == EngineState()


async def test_decided_records_are_pruned_to_the_lookback(
    resolvers: ResolverRegistry,
) -> None:
    """Unbounded runtime state is the other way this design could rot."""
    item = schedule(recurrence=daily(), rules=[at_rule(clock_anchor("17:00:00"))])
    state = EngineState(evaluated_through=ny(2026, 10, 2, 16))

    first = await async_plan_tick(resolvers, item, state, now=ny(2026, 10, 2, 18))
    later = await async_plan_tick(resolvers, item, first.state, now=ny(2026, 10, 9, 18))

    assert len(first.state.decided) == 1
    # A tick spanning seven days decides all seven occurrences, and the record of
    # the one from before the new lookback is dropped rather than kept forever.
    decided = [record.start_date for record in later.state.decided]
    assert date(2026, 10, 2) not in decided
    assert decided == [date(2026, 10, day) for day in range(3, 10)]
