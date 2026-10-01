"""Anchor-span day sets — D124, which is §6.1's layer two.

Layer two is the one the owner asked for by name: *"with that I can define any
span I want"*. Before D124 a day set could name a prebuilt resolver offering or
compose sets that already existed, so `issur_melacha` was sayable and *"from
candle lighting to havdalah"* was not — and the second is the one a user can
write without a resolver author's help.

The file is in four parts, which are the four things the source touches. The
**schema** half checks the shape is stored and that half a span is refused. The
**evaluation** half is D11's two stages over a span whose edges are anchors,
including the case the whole feature is for: a span crossing midnight, which is
every real one. The **horizon** half is why this was not a half-day change —
before D124 no day set had an anchor, so no day set could limit D13's
`known_through`, and now one can. The **index** half is the other edge of the
same fact: an entity read for a span edge has a referrer that is not a schedule,
which D57's index had never seen.

The payoff test is `test_the_flagship_scenario_from_a_user_defined_span`. It is
`test_flagship.py`'s Scenario A with the prebuilt `issur_melacha` offering
replaced by a span the user wrote from `candle_lighting` to `havdalah`, and it
has to produce the same five Fridays. If it ever does not, layer two is not a
way of saying what layer three says.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import voluptuous as vol

from homeassistant.core import HomeAssistant

from custom_components.almanac.const import (
    ANCHOR_CLOCK,
    ANCHOR_ENTITY_TIME,
    ANCHOR_RESOLVER,
    COMPOSE_UNION,
    CONF_DOMAIN,
    CONF_END_ANCHOR,
    CONF_KEY,
    CONF_KIND,
    CONF_OFFSET,
    CONF_START_ANCHOR,
    END_ANCHOR,
    HDATE_CANDLE_LIGHTING,
    HDATE_HAVDALAH,
    RECUR_DAY_SET,
    RESOLVER_HDATE,
    RULE_AT,
    RULE_DURING,
    SOURCE_ANCHOR_SPAN,
    SOURCE_COMPOSITION,
    SOURCE_WEEKDAYS,
)
from custom_components.almanac.engine import (
    OccurrenceStatus,
    async_enumerate,
    day_set_anchors,
)
from custom_components.almanac.engine.day_set import (
    async_candidate_dates,
    async_covers,
    day_window,
)
from custom_components.almanac.index import Reference, Usage, build_index
from custom_components.almanac.resolver import (
    ResolverRegistry,
    Unresolved,
    UnresolvedReason,
    Window,
    async_create_registry,
)
from custom_components.almanac.schema import DAY_SET_STORAGE_SCHEMA, STORAGE_SCHEMA
from custom_components.almanac.storage import AlmanacData

NY = ZoneInfo("America/New_York")

CANDLE = "sensor.candle_lighting"
HAVDALAH = "sensor.havdalah"

# October 2026, the same month `test_flagship.py` measures, so the two files'
# numbers can be compared by eye.
FRIDAYS = ("2026-10-02", "2026-10-09", "2026-10-16", "2026-10-23", "2026-10-30")


# --- helpers ---------------------------------------------------------------


def ny(*args: int) -> datetime:
    """A New York instant, written out."""
    return datetime(*args, tzinfo=NY)  # type: ignore[arg-type]


def clock(at: str) -> dict[str, Any]:
    """A clock anchor. D6 gives it no offset — the time *is* the offset."""
    return {CONF_KIND: ANCHOR_CLOCK, "at": at}


def entity_time(entity_id: str, offset: int = 0) -> dict[str, Any]:
    """An `entity_time` anchor, the one anchor kind that names an entity."""
    return {CONF_KIND: ANCHOR_ENTITY_TIME, "entity_id": entity_id, "offset": offset}


def hdate_anchor(key: str, offset: int = 0) -> dict[str, Any]:
    """A `kind: resolver` anchor on the Jewish calendar."""
    return {
        CONF_KIND: ANCHOR_RESOLVER,
        CONF_DOMAIN: RESOLVER_HDATE,
        CONF_KEY: key,
        CONF_OFFSET: offset,
    }


def span_source(start: dict[str, Any], end: dict[str, Any]) -> dict[str, Any]:
    """D124's source: two anchors and nothing else."""
    return {
        CONF_KIND: SOURCE_ANCHOR_SPAN,
        CONF_START_ANCHOR: start,
        CONF_END_ANCHOR: end,
    }


def day_set(day_set_id: str, source: dict[str, Any]) -> dict[str, Any]:
    """A stored day set, validated, so evaluation sees the stored shape."""
    return dict(
        DAY_SET_STORAGE_SCHEMA(
            {
                "id": day_set_id,
                "name": day_set_id,
                "object_id": day_set_id,
                "source": source,
            }
        )
    )


def stored(**items: dict[str, Any]) -> _Lookup:
    """Day sets as the store holds them, keyed by id."""
    return _Lookup({key: day_set(key, source) for key, source in items.items()})


class _Lookup:
    """A `DaySetLookup` over a literal dict, like `test_day_sets.StubDaySets`."""

    def __init__(self, items: dict[str, dict[str, Any]]) -> None:
        """Hold the mapping, which is all the protocol needs."""
        self._items = items

    def async_get_day_set(self, day_set_id: str) -> dict[str, Any] | None:
        """The stored day set, or `None` for a dangling reference (D17)."""
        return self._items.get(day_set_id)


@pytest.fixture(autouse=True)
async def _located(hass: HomeAssistant) -> None:
    """New York, because candle lighting is a fact about a place."""
    await hass.config.async_set_time_zone("America/New_York")
    await hass.config.async_update(latitude=40.7128, longitude=-74.0060, elevation=0)


@pytest.fixture
def resolvers(hass: HomeAssistant) -> ResolverRegistry:
    """The in-tree resolvers. No stub — every edge here is a real anchor."""
    return async_create_registry(hass)


# --- the shape, D124 ------------------------------------------------------


def test_a_span_source_is_stored_with_both_edges() -> None:
    """The seventh `source.kind`, validated and round-tripped."""
    item = day_set("night", span_source(clock("22:00:00"), clock("06:00:00")))

    source = item["source"]
    assert source[CONF_KIND] == SOURCE_ANCHOR_SPAN
    assert source[CONF_START_ANCHOR][CONF_KIND] == ANCHOR_CLOCK
    assert source[CONF_END_ANCHOR]["at"] == "06:00:00"


def test_half_a_span_is_refused() -> None:
    """Both edges are required. A set with one edge has no interior to be in."""
    with pytest.raises(vol.Invalid):
        day_set(
            "night",
            {CONF_KIND: SOURCE_ANCHOR_SPAN, CONF_START_ANCHOR: clock("22:00:00")},
        )


def test_the_edges_are_full_anchors_not_times() -> None:
    """`ANCHOR_SCHEMA` by reference, so every anchor kind is an edge.

    This is the whole of the owner's *"it must work the anchor and backwards"*:
    an edge carries an offset because it is an anchor, and nothing about the day
    set had to learn what an offset is.
    """
    item = day_set(
        "shabbat",
        span_source(
            hdate_anchor(HDATE_CANDLE_LIGHTING, offset=-45 * 60),
            entity_time(HAVDALAH, offset=30 * 60),
        ),
    )

    source = item["source"]
    assert source[CONF_START_ANCHOR][CONF_OFFSET] == -45 * 60
    assert source[CONF_END_ANCHOR]["entity_id"] == HAVDALAH


# --- D11's two stages over a span ------------------------------------------


async def test_a_span_crossing_midnight_marks_both_days_and_covers_the_night(
    resolvers: ResolverRegistry,
) -> None:
    """The case layer two exists for: 22:00 to 06:00, which no weekday can say.

    Stage one is generous and names both civil days the span touches — the same
    answer `issur_melacha` gives for Friday evening to Saturday evening, reached
    by arithmetic the user wrote rather than by a resolver's. Stage two is what
    knows that 07:00 is after the night ended.

    The third of October is in the answer for a window that asked only about the
    second, because `Window.days` is inclusive at both ends and a `day_window`
    ends at the midnight *after* its last day. That is stage one's documented
    generosity and not a span's doing: a `weekdays` source over the same window
    answers the same way, and `plan.py` clips to the dates it asked about. What
    stage one must never do is *miss* a day.
    """
    day_sets = stored(night=span_source(clock("22:00:00"), clock("06:00:00")))
    window = day_window(date(2026, 10, 2), date(2026, 10, 2), NY)

    days = await async_candidate_dates(resolvers, day_sets, "night", window)

    assert days == [date(2026, 10, 2), date(2026, 10, 3)]
    assert await async_covers(resolvers, day_sets, "night", ny(2026, 10, 2, 23)) is True
    # The small hours of the third belong to the span that began on the second,
    # which is found only because stage two looks back past the instant's own day.
    assert await async_covers(resolvers, day_sets, "night", ny(2026, 10, 3, 2)) is True
    assert await async_covers(resolvers, day_sets, "night", ny(2026, 10, 3, 7)) is False
    noon = await async_covers(resolvers, day_sets, "night", ny(2026, 10, 2, 12))
    assert noon is False


async def test_an_offset_on_an_edge_moves_the_edge(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """D122 does not apply *inside* a set, and this is the assertion that says so.

    D122 is about which instant a rule's anchor is tested against. Here the
    offset is the edge itself: "from forty-five minutes before candle lighting"
    has to start at 17:33, or the owner's *"calc 45 minutes offset"* has no
    effect on anything.
    """
    hass.states.async_set(CANDLE, "2026-10-02T18:18:00-04:00")
    hass.states.async_set(HAVDALAH, "2026-10-03T19:14:00-04:00")
    day_sets = stored(
        early=span_source(entity_time(CANDLE, offset=-45 * 60), entity_time(HAVDALAH))
    )

    # 17:33 is inside, 17:32 is not. The boundary is the offset instant, exactly.
    assert (
        await async_covers(resolvers, day_sets, "early", ny(2026, 10, 2, 17, 33))
        is True
    )
    assert (
        await async_covers(resolvers, day_sets, "early", ny(2026, 10, 2, 17, 32))
        is False
    )
    # And the day before candle lighting is now a candidate date, because the
    # span reaches back into it. Nothing special happens: the edge moved.
    days = await async_candidate_dates(
        resolvers,
        day_sets,
        "early",
        day_window(date(2026, 10, 2), date(2026, 10, 3), NY),
    )
    assert days == [date(2026, 10, 2), date(2026, 10, 3)]


async def test_an_end_edge_out_of_reach_does_not_pair(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """The `_SPAN_REACH_DAYS` bound, reported rather than silently truncated.

    D39 bounds a *rule's* interval by its recurrence period, and a day set has no
    recurrence period to be bounded by, so the bound here is a fixed reach. Eight
    days, because the longest real stretch the motivating calendar produces is a
    diaspora Sukkot week. Past it the set is unresolved, which is D12's reading:
    an answer nobody can check is worse than a visible failure.
    """
    hass.states.async_set(CANDLE, "2026-10-02T18:18:00-04:00")
    hass.states.async_set(HAVDALAH, "2026-11-20T19:22:00-05:00")
    day_sets = stored(
        wrong=span_source(entity_time(CANDLE), entity_time(HAVDALAH))
    )

    days = await async_candidate_dates(
        resolvers,
        day_sets,
        "wrong",
        day_window(date(2026, 10, 2), date(2026, 10, 3), NY),
    )

    assert isinstance(days, Unresolved)
    assert days.reason is UnresolvedReason.NO_PAIRING
    assert "no end edge" in days.detail


async def test_an_unresolvable_edge_makes_the_set_unresolvable(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """A broken edge is propagated, not swallowed into "never in force"."""
    hass.states.async_set(CANDLE, "unknown")
    day_sets = stored(broken=span_source(entity_time(CANDLE), clock("06:00:00")))

    covers = await async_covers(resolvers, day_sets, "broken", ny(2026, 10, 2, 23))

    assert isinstance(covers, Unresolved)
    assert covers.reason is not UnresolvedReason.NO_PAIRING


async def test_a_span_composes_with_a_date_granular_set(
    resolvers: ResolverRegistry,
) -> None:
    """D19's one level, with a span as a member. Nothing in it is span-aware.

    The union is the interesting direction: a span member contributes the days it
    touches, a weekday member contributes its own, and stage two asks each in
    turn. `COMPOSE_UNION` over *overlapping* spans is also why a span day set has
    no D39-style collision check — two spans in one set are a union by
    construction, and that is what §6.1 means by "any span I want".
    """
    day_sets = stored(
        night=span_source(clock("22:00:00"), clock("06:00:00")),
        monday={CONF_KIND: SOURCE_WEEKDAYS, "weekdays": ["mon"]},
        either={
            CONF_KIND: SOURCE_COMPOSITION,
            "operator": COMPOSE_UNION,
            "members": ["night", "monday"],
        },
    )
    window = day_window(date(2026, 10, 5), date(2026, 10, 6), NY)

    days = await async_candidate_dates(resolvers, day_sets, "either", window)

    # Monday from the weekday member, the sixth and seventh from the span member
    # — stage one's generosity again, for the reason the midnight test gives.
    assert days == [date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 7)]
    # Monday noon: in, from the weekday member alone.
    monday = await async_covers(resolvers, day_sets, "either", ny(2026, 10, 5, 12))
    assert monday is True
    # Tuesday 23:00: in, from the span member alone.
    tuesday = await async_covers(resolvers, day_sets, "either", ny(2026, 10, 6, 23))
    assert tuesday is True
    # Tuesday noon: in neither.
    assert (
        await async_covers(resolvers, day_sets, "either", ny(2026, 10, 6, 12)) is False
    )


# --- the flagship, said in layer two ---------------------------------------


async def test_the_flagship_scenario_from_a_user_defined_span(
    hass: HomeAssistant, almanac_data: AlmanacData
) -> None:
    """Scenario A, over a Shabbat the user defined rather than one `hdate` ships.

    `test_flagship.py` runs the same rule against the `issur_melacha` offering —
    layer three, a prebuilt set. This is the same five Fridays reached from layer
    two: the span from candle lighting to havdalah, written as two anchors. The
    two have to agree, because layer three is documented as a convenience over
    layer two and not as a second mechanism.

    `hdate` reports no candle lighting on an ordinary Saturday and no havdalah on
    a Friday, so the forward walk pairs each Friday's lighting with the next
    night's havdalah without being told anything about the week. That is D38's
    rule, reused.
    """
    item = await almanac_data.day_sets.async_create_item(
        {
            "name": "Shabbat, by its edges",
            "source": span_source(
                hdate_anchor(HDATE_CANDLE_LIGHTING), hdate_anchor(HDATE_HAVDALAH)
            ),
        }
    )
    day_set_id = str(item["id"])
    schedule = await almanac_data.schedules.async_create_item(
        {
            "name": "Shabbat lights",
            "date_window": {"from": "2026-10-01", "until": "2026-11-30"},
            "recurrence": {"kind": RECUR_DAY_SET, "day_set_id": day_set_id},
            "rules": [
                {
                    "kind": RULE_DURING,
                    "id": "r1",
                    "start_anchor": hdate_anchor(
                        HDATE_CANDLE_LIGHTING, offset=-45 * 60
                    ),
                    "end": {
                        "kind": END_ANCHOR,
                        "anchor": hdate_anchor(HDATE_HAVDALAH, offset=30 * 60),
                    },
                }
            ],
        }
    )
    await hass.async_block_till_done()

    plan = await async_enumerate(
        almanac_data.resolvers,
        dict(almanac_data.schedules.data[str(schedule["id"])]),
        Window(ny(2026, 10, 1), ny(2026, 11, 1)),
        day_sets=almanac_data.day_sets,
    )

    assert plan.problem is None, f"the plan did not resolve: {plan.problem}"
    by_date = {str(occ.start_date): occ for occ in plan.occurrences}
    for friday in FRIDAYS:
        assert by_date[friday].status is OccurrenceStatus.SCHEDULED, friday
        assert by_date[friday].will_run, friday
    # The numbers for the second of October, matching `test_flagship.py` exactly.
    assert by_date["2026-10-02"].start == ny(2026, 10, 2, 17, 33)


async def test_an_at_rule_on_a_span_set_is_filtered_by_the_span(
    resolvers: ResolverRegistry,
) -> None:
    """D11's point, over layer two: the Saturday row survives and is marked.

    The same assertion `test_day_sets` makes against the stub offering, which is
    the one that matters — a span set has to be a day set in the full sense, not
    a date generator that happens to be built from anchors.
    """
    day_sets = stored(night=span_source(clock("20:00:00"), clock("23:00:00")))
    item = dict(
        STORAGE_SCHEMA(
            {
                "id": "sched",
                "name": "Announce",
                "object_id": "announce",
                "recurrence": {"kind": RECUR_DAY_SET, "day_set_id": "night"},
                "rules": [{"kind": RULE_AT, "id": "r1", "anchor": clock("22:00:00")}],
            }
        )
    )

    plan = await async_enumerate(
        resolvers, item, Window(ny(2026, 10, 2), ny(2026, 10, 4)), day_sets=day_sets
    )

    # Every day is a candidate — the span happens daily — and 22:00 is inside it.
    assert [occ.status for occ in plan.occurrences] == [
        OccurrenceStatus.SCHEDULED,
        OccurrenceStatus.SCHEDULED,
    ]

    # Move the window to the hour after the span and the rows invert, still
    # present (D12) and marked with the reason.
    item["rules"][0]["anchor"] = clock("23:30:00")
    plan = await async_enumerate(
        resolvers, item, Window(ny(2026, 10, 2), ny(2026, 10, 4)), day_sets=day_sets
    )
    for occ in plan.occurrences:
        assert occ.status is OccurrenceStatus.OUTSIDE_SET
        assert occ.start is not None and occ.start.hour == 23
        assert not occ.will_run


# --- D13 reaches a day set, for the first time -----------------------------


def test_a_span_sets_anchors_are_reported_for_the_horizon() -> None:
    """`day_set_anchors` — the declaration half, one level deep like D19."""
    day_sets = stored(
        night=span_source(entity_time(CANDLE), entity_time(HAVDALAH)),
        monday={CONF_KIND: SOURCE_WEEKDAYS, "weekdays": ["mon"]},
        either={
            CONF_KIND: SOURCE_COMPOSITION,
            "operator": COMPOSE_UNION,
            "members": ["monday", "night"],
        },
    )

    assert day_set_anchors(day_sets, "monday") == ()
    assert [slot for slot, _ in day_set_anchors(day_sets, "night")] == [
        "day_set:0:start",
        "day_set:0:end",
    ]
    # In a composition the slot carries the member's position, so one member's
    # declaration cannot be combined with another member's instants.
    assert [slot for slot, _ in day_set_anchors(day_sets, "either")] == [
        "day_set:1:start",
        "day_set:1:end",
    ]
    assert day_set_anchors(day_sets, "missing") == ()


async def test_a_next_only_edge_limits_what_the_plan_claims_to_know(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """The gap D124 found: before it, no day set could limit `known_through`.

    An `entity_time` edge is NEXT_ONLY (D13) — the sensor holds one timestamp and
    is mute about every later one — so a plan whose *dates* come from a set built
    on it cannot honestly claim to see past that timestamp. The occurrences are
    still computed, which is the §12.2 distinction: `computed_through` says how
    far we were willing to look and `known_through` says how far a source would
    commit, and they are two facts.
    """
    hass.states.async_set(CANDLE, "2026-10-02T18:18:00-04:00")
    hass.states.async_set(HAVDALAH, "2026-10-03T19:14:00-04:00")
    day_sets = stored(
        shabbat=span_source(entity_time(CANDLE), entity_time(HAVDALAH))
    )
    item = dict(
        STORAGE_SCHEMA(
            {
                "id": "sched",
                "name": "Announce",
                "object_id": "announce",
                "recurrence": {"kind": RECUR_DAY_SET, "day_set_id": "shabbat"},
                "rules": [{"kind": RULE_AT, "id": "r1", "anchor": clock("22:00:00")}],
            }
        )
    )
    window = Window(ny(2026, 10, 1), ny(2026, 10, 31))

    plan = await async_enumerate(resolvers, item, window, day_sets=day_sets)

    # Knowledge ends at the *candle lighting* value, not at the window's end and
    # not at the later havdalah: `known_through` is a minimum across every anchor
    # the plan touched, and the start edge is the one that stops claiming first.
    assert plan.known_through == ny(2026, 10, 2, 18, 18)
    assert not plan.fully_known
    # And the budget is untouched: a month is well inside D44's ninety days.
    assert plan.computed_through == window.end
    assert plan.fully_computed
    assert plan.solid_through == plan.known_through

    # A clock-edged set declares nothing, so the same plan sees the whole window.
    clock_sets = stored(night=span_source(clock("20:00:00"), clock("23:00:00")))
    item["recurrence"] = {"kind": RECUR_DAY_SET, "day_set_id": "night"}
    plan = await async_enumerate(resolvers, item, window, day_sets=clock_sets)
    assert plan.fully_known


# --- D57: an entity whose referrer is not a schedule ------------------------


def test_the_index_reaches_a_span_edges_entity() -> None:
    """A day set reads an entity, which D57's index had never had to record.

    The two halves are separate functions rather than one list with a flag,
    because every caller of either one is about to render a list of one kind of
    thing. A frontend asking "what breaks if this sensor goes away" wants both,
    and asking twice is cheaper than teaching it to filter.
    """
    day_sets = {
        "shabbat": day_set(
            "shabbat", span_source(entity_time(CANDLE), entity_time(HAVDALAH))
        )
    }
    schedules = {
        "sched": dict(
            STORAGE_SCHEMA(
                {
                    "id": "sched",
                    "name": "sched",
                    "object_id": "sched",
                    "recurrence": {"kind": RECUR_DAY_SET, "day_set_id": "shabbat"},
                    "rules": [
                        {"kind": RULE_AT, "id": "r1", "anchor": clock("22:00:00")}
                    ],
                }
            )
        )
    }

    index = build_index(schedules, day_sets)

    assert index.day_sets_using_entity(CANDLE) == ("shabbat",)
    assert index.day_sets_using_entity(HAVDALAH) == ("shabbat",)
    # Not a schedule. The schedule depends on the *day set*, which the day-set
    # half of the index already records, and reporting it here would claim the
    # schedule mentions a sensor it has never heard of.
    assert index.schedules_using_entity(CANDLE) == ()
    assert index.entities_used.get("shabbat", ()) == (CANDLE, HAVDALAH)
    assert Reference("shabbat", "", Usage.DAY_SET_ANCHOR) in index.entity_users[CANDLE]


def test_a_resolver_edge_names_no_entity() -> None:
    """`hdate` is not an entity, so the index records nothing for it.

    The same reading `_anchor_references` already has for a rule's anchor: the
    dependency is on the offering, and whatever the resolver reads to answer is
    the resolver's business (D9).
    """
    day_sets = {
        "shabbat": day_set(
            "shabbat",
            span_source(
                hdate_anchor(HDATE_CANDLE_LIGHTING), hdate_anchor(HDATE_HAVDALAH)
            ),
        )
    }

    index = build_index({}, day_sets)

    assert index.entities_used.get("shabbat", ()) == ()
    assert index.entity_users == {}


def test_a_span_set_is_still_reportable_as_a_composition_member() -> None:
    """The day-set half of the index is unchanged by D124's branch.

    Worth asserting because the branch `continue`s before the composition walk,
    and a span member of a composition reaches both: its own entity edges and its
    membership. One `continue` in the wrong place loses the second.
    """
    day_sets = {
        "night": day_set("night", span_source(entity_time(CANDLE), clock("06:00:00"))),
        "monday": day_set("monday", {CONF_KIND: SOURCE_WEEKDAYS, "weekdays": ["mon"]}),
        "either": day_set(
            "either",
            {
                CONF_KIND: SOURCE_COMPOSITION,
                "operator": COMPOSE_UNION,
                "members": ["night", "monday"],
            },
        ),
    }

    index = build_index({}, day_sets)

    assert index.day_sets_using_entity(CANDLE) == ("night",)
    assert index.day_sets_using_day_set("night") == ("either",)
