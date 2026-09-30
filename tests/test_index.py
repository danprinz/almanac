"""The reverse index and the day-set impact preview — D57 and D22.

D57 puts the "what depends on this" graph in-tree because core's cannot be
extended: `ItemType` in the search helper is a closed enum and the five components
it knows are imported by name, so there is no registration hook to use. That makes
this module the only answer to the question, which is why it is tested for
completeness rather than for the happy path — a reference the index misses does not
fail, it under-reports, and D22's preview is built on top of it.

The preview half asserts the property that makes it trustworthy: it is the engine
run twice, over the caller's window, and the second run differs only in which day
set definition the lookup hands back. D64 is what makes that possible at all, and
the tests are written as two enumerations and a diff for the same reason the
implementation is.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from homeassistant.core import HomeAssistant

from custom_components.almanac.const import (
    ANCHOR_CLOCK,
    ANCHOR_ENTITY_TIME,
    ANCHOR_RESOLVER,
    CMP_ABOVE,
    CMP_EQUAL,
    COMPOSE_UNION,
    CONDITION_COMPARISON,
    CONDITION_DAY_SET,
    CONDITION_GROUP,
    END_ANCHOR,
    FINISHED_CONDITION,
    GROUP_OR,
    OPERAND_CONSTANT,
    OPERAND_ENTITY,
    RECUR_DAY_SET,
    RECUR_WEEKDAYS,
    SOURCE_COMPOSITION,
    SOURCE_OFFERING,
    SOURCE_WEEKDAYS,
)
from custom_components.almanac.engine import OccurrenceStatus
from custom_components.almanac.impact import (
    affected_schedules,
    async_preview_day_set_change,
)
from custom_components.almanac.index import Reference, Usage, build_index
from custom_components.almanac.resolver import (
    BaseResolver,
    Horizon,
    InvalidationKind,
    InvalidationSignal,
    Offering,
    ResolverRegistry,
    Role,
    Span,
    Window,
    async_create_registry,
)
from custom_components.almanac.schema import DAY_SET_STORAGE_SCHEMA, STORAGE_SCHEMA

NY = ZoneInfo("America/New_York")

CANDLE = "sensor.candle_lighting"
HAVDALAH = "sensor.havdalah"
ZMAN = "sensor.zman"
HOME = "binary_sensor.home"
DOOR = "binary_sensor.door"

SHABBAT = "shabbat"


# --- helpers ---------------------------------------------------------------


def ny(*args: int) -> datetime:
    """A New York instant, written out."""
    return datetime(*args, tzinfo=NY)  # type: ignore[arg-type]


class SaturdayEveningResolver(BaseResolver):
    """A day-set offering with real extent: Saturday 18:00 to 20:00 local.

    Chosen so that stage one yields the Saturday and stage two rejects an instant
    earlier the same day. That is what produces an *altered* occurrence in the
    preview — same recurrence date, different D12 status — which no date-granular
    source can do and which is therefore the case worth writing out.
    """

    domain = "stub"

    def offering(self, key: str) -> Offering | None:
        """One key, with the day-set role."""
        if key != SHABBAT:
            return None
        return Offering(
            key=SHABBAT,
            display_name="Shabbat",
            roles=frozenset({Role.DAY_SET}),
            horizon=Horizon.unbounded(),
        )

    def invalidation(self, key: str) -> InvalidationSignal:
        """Pure arithmetic on the calendar, so nothing has to be watched (D43)."""
        return InvalidationSignal(InvalidationKind.DETERMINISTIC)

    async def forecast(self, key: str, window: Window) -> list[Span]:
        """Every Saturday evening touching the window."""
        spans: list[Span] = []
        day = window.start.astimezone(NY).date() - timedelta(days=1)
        last = window.end.astimezone(NY).date() + timedelta(days=1)
        while day <= last:
            if day.weekday() == 5:  # Saturday
                spans.append(
                    Span(
                        datetime.combine(day, time(18), tzinfo=NY),
                        datetime.combine(day, time(20), tzinfo=NY),
                    )
                )
            day += timedelta(days=1)
        return spans


@pytest.fixture(autouse=True)
async def _located(hass: HomeAssistant) -> None:
    """New York, matching the other engine tests."""
    await hass.config.async_set_time_zone("America/New_York")
    await hass.config.async_update(latitude=40.7128, longitude=-74.0060, elevation=0)


@pytest.fixture
def resolvers(hass: HomeAssistant) -> ResolverRegistry:
    """The in-tree resolvers plus the stub day-set source."""
    registry = async_create_registry(hass)
    registry.async_register(SaturdayEveningResolver(hass))
    return registry


def clock(at: str) -> dict[str, Any]:
    """A clock anchor."""
    return {"kind": ANCHOR_CLOCK, "at": at}


def entity_time(entity_id: str, offset: int = 0) -> dict[str, Any]:
    """An `entity_time` anchor — the index's one entity-bearing anchor kind."""
    return {"kind": ANCHOR_ENTITY_TIME, "entity_id": entity_id, "offset": offset}


def resolver_anchor(domain: str = "sun", key: str = "sunset") -> dict[str, Any]:
    """A `kind: resolver` anchor, which names an offering and not an entity."""
    return {"kind": ANCHOR_RESOLVER, "domain": domain, "key": key}


def comparison(
    entity_id: str, operator: str, value: Any, **extra: Any
) -> dict[str, Any]:
    """A comparison condition, taking a raw operand dict or a constant."""
    if not isinstance(value, dict):
        value = {"kind": OPERAND_CONSTANT, "value": value}
    return {
        "kind": CONDITION_COMPARISON,
        "entity_id": entity_id,
        "operator": operator,
        "value": value,
        **extra,
    }


def entity_operand(entity_id: str, offset: int = 0) -> dict[str, Any]:
    """An entity operand — the right-hand side the index must not miss."""
    return {"kind": OPERAND_ENTITY, "entity_id": entity_id, "offset": offset}


def day_set_condition(day_set_id: str) -> dict[str, Any]:
    """A `day_set` condition (D20)."""
    return {"kind": CONDITION_DAY_SET, "day_set_id": day_set_id}


def schedule(schedule_id: str, **body: Any) -> dict[str, Any]:
    """A stored schedule, validated, so the index sees the stored shape."""
    return dict(
        STORAGE_SCHEMA(
            {
                "id": schedule_id,
                "name": schedule_id,
                "object_id": schedule_id,
                **body,
            }
        )
    )


def day_set(day_set_id: str, source: dict[str, Any]) -> dict[str, Any]:
    """A stored day set, validated."""
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


def weekdays(*names: str) -> dict[str, Any]:
    """A date-granular day-set source."""
    return {"kind": SOURCE_WEEKDAYS, "weekdays": list(names)}


def union(*members: str) -> dict[str, Any]:
    """A one-level composition (D19)."""
    return {
        "kind": SOURCE_COMPOSITION,
        "operator": COMPOSE_UNION,
        "members": list(members),
    }


def offering_source(key: str = SHABBAT) -> dict[str, Any]:
    """A `kind: offering` day-set source addressing the stub."""
    return {"kind": SOURCE_OFFERING, "domain": "stub", "key": key}


def on_weekdays(*names: str) -> dict[str, Any]:
    """An ordinary weekday recurrence."""
    return {"kind": RECUR_WEEKDAYS, "weekdays": list(names)}


def on_day_set(day_set_id: str) -> dict[str, Any]:
    """A day-set recurrence (D20's first face)."""
    return {"kind": RECUR_DAY_SET, "day_set_id": day_set_id}


def by_id(*items: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Storage's own shape: `DictStorageCollection.data`, keyed by id."""
    return {item["id"]: item for item in items}


# --- D57: every reference, and nothing that is not one ----------------------


def test_a_day_set_recurrence_belongs_to_the_schedule_not_to_a_rule() -> None:
    """Empty `rule_id`, because there is no rule to point the user at."""
    index = build_index(
        by_id(
            schedule(
                "sched",
                recurrence=on_day_set("weekend"),
                rules=[{"kind": "at", "id": "r1", "anchor": clock("07:00:00")}],
            )
        )
    )

    assert index.references_to_day_set("weekend") == (
        Reference("sched", "", Usage.RECURRENCE),
    )
    assert index.schedules_using_day_set("weekend") == ("sched",)


def test_an_entity_anchor_is_found_in_all_three_positions() -> None:
    """`anchor`, `start_anchor` and an `end` of `kind: anchor`.

    Written as one test over the three fields because the failure mode is a field
    the walker forgot: a rename that broke an interval's *end* anchor and nothing
    else would be invisible, and the index is the only thing that could have
    warned.
    """
    index = build_index(
        by_id(
            schedule(
                "sched",
                recurrence=on_weekdays("fri"),
                rules=[
                    {"kind": "at", "id": "at", "anchor": entity_time(CANDLE)},
                    {
                        "kind": "during",
                        "id": "during",
                        "start_anchor": entity_time(CANDLE, offset=-2700),
                        "end": {"kind": END_ANCHOR, "anchor": entity_time(HAVDALAH)},
                    },
                ],
            )
        )
    )

    assert index.references_to_entity(CANDLE) == (
        Reference("sched", "at", Usage.ANCHOR),
        Reference("sched", "during", Usage.ANCHOR),
    )
    assert index.references_to_entity(HAVDALAH) == (
        Reference("sched", "during", Usage.ANCHOR),
    )


def test_a_resolver_anchor_contributes_no_entity() -> None:
    """The dependency is on the offering (D9), not on whatever backs it today.

    Claiming an entity edge here would be wrong the moment an offering is computed
    rather than read — step 7's `hdate` resolver needs no entity at all — and a
    wrong edge is worse than a missing one because the user acts on it.
    """
    index = build_index(
        by_id(
            schedule(
                "sched",
                recurrence=on_weekdays("fri"),
                rules=[{"kind": "at", "id": "r1", "anchor": resolver_anchor()}],
            )
        )
    )

    assert index.entity_users == {}
    assert index.entities_used == {}


def test_both_operands_of_a_comparison_are_indexed() -> None:
    """The right-hand side is D23's "before candle lighting" written as a condition.

    Indexing only the subject would make the index disagree with the evaluator
    about what a rule reads, and the evaluator is the one that is right.
    """
    index = build_index(
        by_id(
            schedule(
                "sched",
                recurrence=on_weekdays("fri"),
                rules=[
                    {
                        "kind": "at",
                        "id": "r1",
                        "anchor": clock("07:00:00"),
                        "conditions": [
                            comparison(ZMAN, CMP_ABOVE, entity_operand(CANDLE, -2700))
                        ],
                    }
                ],
            )
        )
    )

    assert index.schedules_using_entity(CANDLE) == ("sched",)
    assert index.entities_used["sched"] == (CANDLE, ZMAN)


def test_a_group_leaf_is_indexed_as_the_rule_that_holds_it() -> None:
    """§7.1's one level, flattened: the group is not a location a user can be sent to."""
    index = build_index(
        by_id(
            schedule(
                "sched",
                recurrence=on_weekdays("fri"),
                rules=[
                    {
                        "kind": "at",
                        "id": "r1",
                        "anchor": clock("07:00:00"),
                        "conditions": [
                            {
                                "kind": CONDITION_GROUP,
                                "operator": GROUP_OR,
                                "conditions": [
                                    comparison(HOME, CMP_EQUAL, "on"),
                                    day_set_condition("holiday"),
                                ],
                            }
                        ],
                    }
                ],
            )
        )
    )

    assert index.references_to_entity(HOME) == (
        Reference("sched", "r1", Usage.CONDITION),
    )
    assert index.references_to_day_set("holiday") == (
        Reference("sched", "r1", Usage.CONDITION),
    )


def test_a_completion_condition_is_indexed_as_the_schedules_own() -> None:
    """D46's `finished_when: condition` is a dependency that can break termination.

    Left out, a rename would stop a schedule ever finishing and the index would
    have said the entity was unused — which is the specific way an incomplete
    index does harm.
    """
    index = build_index(
        by_id(
            schedule(
                "sched",
                recurrence=on_weekdays("fri"),
                rules=[{"kind": "at", "id": "r1", "anchor": clock("07:00:00")}],
                completion={
                    "finished_when": {
                        "kind": FINISHED_CONDITION,
                        "conditions": [comparison(DOOR, CMP_EQUAL, "off")],
                    }
                },
            )
        )
    )

    assert index.references_to_entity(DOOR) == (
        Reference("sched", "", Usage.COMPLETION),
    )


def test_an_empty_rule_id_means_the_schedule_and_never_an_unnamed_rule() -> None:
    """The schema mints a rule id at validation, so `""` is unambiguous.

    That is what lets the frontend branch on the empty string to decide whether to
    link to a rule or to the schedule: a rule the author did not name still has an
    id by the time it reaches storage, so the two cases cannot be confused.
    """
    item = schedule(
        "sched",
        recurrence=on_day_set("weekend"),
        rules=[{"kind": "at", "anchor": entity_time(CANDLE)}],
    )
    minted = item["rules"][0]["id"]
    index = build_index(by_id(item))

    assert minted
    assert index.references_to_entity(CANDLE) == (
        Reference("sched", minted, Usage.ANCHOR),
    )
    # The schedule-level reference is the only one carrying the empty id.
    assert index.references_to_day_set("weekend") == (
        Reference("sched", "", Usage.RECURRENCE),
    )


def test_a_composition_edge_is_a_day_set_referrer_not_a_schedule() -> None:
    """D19's edge, and the two questions kept apart.

    `schedules_using_day_set` is asked by something about to list schedules, so a
    day set appearing in that list would be a type error the caller could not see.
    """
    index = build_index(
        by_id(
            schedule(
                "sched",
                recurrence=on_day_set("long_weekend"),
                rules=[{"kind": "at", "id": "r1", "anchor": clock("07:00:00")}],
            )
        ),
        by_id(
            day_set("weekend", weekdays("sat")),
            day_set("fridays", weekdays("fri")),
            day_set("long_weekend", union("weekend", "fridays")),
        ),
    )

    assert index.day_sets_using_day_set("weekend") == ("long_weekend",)
    assert index.schedules_using_day_set("weekend") == ()
    assert index.schedules_using_day_set("long_weekend") == ("sched",)
    assert index.day_sets_used["long_weekend"] == ("fridays", "weekend")


def test_the_same_reference_three_times_is_one_schedule_to_warn_about() -> None:
    """Deduplicated and sorted, which is what makes the impact diff a comparison."""
    index = build_index(
        by_id(
            schedule(
                "b",
                recurrence=on_day_set("weekend"),
                rules=[
                    {
                        "kind": "at",
                        "id": "r1",
                        "anchor": clock("07:00:00"),
                        "conditions": [day_set_condition("weekend")],
                    },
                    {
                        "kind": "at",
                        "id": "r2",
                        "anchor": clock("08:00:00"),
                        "conditions": [day_set_condition("weekend")],
                    },
                ],
            ),
            schedule(
                "a",
                recurrence=on_weekdays("fri"),
                rules=[
                    {
                        "kind": "at",
                        "id": "r1",
                        "anchor": clock("07:00:00"),
                        "conditions": [day_set_condition("weekend")],
                    }
                ],
            ),
        )
    )

    assert index.schedules_using_day_set("weekend") == ("a", "b")
    # Uses and schedules are different numbers, and both are worth showing.
    assert len(index.references_to_day_set("weekend")) == 4


def test_an_index_of_nothing_answers_rather_than_raises() -> None:
    """Every accessor is total. The frontend asks about ids that may not exist."""
    index = build_index({})

    assert index.schedules_using_entity(CANDLE) == ()
    assert index.schedules_using_day_set("weekend") == ()
    assert index.day_sets_using_day_set("weekend") == ()
    assert index.references_to_entity(CANDLE) == ()
    assert index.references_to_day_set("weekend") == ()


def test_day_sets_are_optional_so_the_entity_half_is_usable_early() -> None:
    """Loading order is not a reason for the index to be absent."""
    schedules = by_id(
        schedule(
            "sched",
            recurrence=on_weekdays("fri"),
            rules=[{"kind": "at", "id": "r1", "anchor": entity_time(CANDLE)}],
        )
    )

    assert build_index(schedules) == build_index(schedules, {})


def test_two_indexes_of_equal_input_compare_equal() -> None:
    """The property D22's diff rests on, asserted directly."""
    schedules = by_id(
        schedule(
            "sched",
            recurrence=on_day_set("weekend"),
            rules=[{"kind": "at", "id": "r1", "anchor": entity_time(CANDLE)}],
        )
    )
    sets = by_id(day_set("weekend", weekdays("sat")))

    assert build_index(schedules, sets) == build_index(schedules, sets)


# --- D22: one hop out, because D19 only allows one --------------------------


def test_editing_a_member_reaches_the_schedules_using_the_container() -> None:
    """The case a direct-references-only preview would miss.

    Nobody uses *weekend* directly; the schedule uses *long_weekend*, which
    contains it. Under-reporting here is exactly the failure D22 exists to
    prevent, since the user would commit the edit believing nothing depended on it.
    """
    index = build_index(
        by_id(
            schedule(
                "container_user",
                recurrence=on_day_set("long_weekend"),
                rules=[{"kind": "at", "id": "r1", "anchor": clock("07:00:00")}],
            ),
            schedule(
                "unrelated",
                recurrence=on_weekdays("mon"),
                rules=[{"kind": "at", "id": "r1", "anchor": clock("07:00:00")}],
            ),
        ),
        by_id(
            day_set("weekend", weekdays("sat")),
            day_set("fridays", weekdays("fri")),
            day_set("long_weekend", union("weekend", "fridays")),
        ),
    )

    reached, containers = affected_schedules(index, "weekend")

    assert reached == ("container_user",)
    assert containers == ("long_weekend",)


def test_the_traversal_stops_after_one_hop() -> None:
    """There is no third level to look for, and none is invented.

    The collection refuses a composition of a composition at write time, so a
    stored two-level chain is already impossible; this asserts the traversal does
    not manufacture one from a store that somehow held it.
    """
    index = build_index(
        by_id(
            schedule(
                "outer_user",
                recurrence=on_day_set("outer"),
                rules=[{"kind": "at", "id": "r1", "anchor": clock("07:00:00")}],
            )
        ),
        by_id(
            day_set("leaf", weekdays("sat")),
            day_set("mid", union("leaf", "leaf")),
            day_set("outer", union("mid", "mid")),
        ),
    )

    reached, containers = affected_schedules(index, "leaf")

    assert containers == ("mid",)
    # `mid` is used by no schedule, so nothing is reached — the schedule that uses
    # `outer` is two hops away and deliberately not reported.
    assert reached == ()


# --- D22: the preview is the engine, run twice ------------------------------


def week() -> Window:
    """Thursday 1 October 2026 to the following Thursday, half-open (D88)."""
    return Window(ny(2026, 10, 1), ny(2026, 10, 8))


def preview_fixture() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Four schedules over three day sets, covering every way of reaching one.

    `morning` uses *weekend* directly as a recurrence, `container_user` reaches it
    through *long_weekend*, `mentions_it` names it only in a condition, and
    `unrelated` does not touch it.
    """
    schedules = by_id(
        schedule(
            "morning",
            recurrence=on_day_set("weekend"),
            rules=[{"kind": "at", "id": "r1", "anchor": clock("07:00:00")}],
        ),
        schedule(
            "container_user",
            recurrence=on_day_set("long_weekend"),
            rules=[{"kind": "at", "id": "r1", "anchor": clock("07:00:00")}],
        ),
        schedule(
            "mentions_it",
            recurrence=on_weekdays("mon"),
            rules=[
                {
                    "kind": "at",
                    "id": "r1",
                    "anchor": clock("07:00:00"),
                    "conditions": [day_set_condition("weekend")],
                }
            ],
        ),
        schedule(
            "unrelated",
            recurrence=on_weekdays("tue"),
            rules=[{"kind": "at", "id": "r1", "anchor": clock("07:00:00")}],
        ),
    )
    sets = by_id(
        day_set("weekend", weekdays("sat")),
        day_set("fridays", weekdays("fri")),
        day_set("long_weekend", union("weekend", "fridays")),
    )
    return schedules, sets


async def test_widening_a_day_set_adds_occurrences_in_both_directions(
    resolvers: ResolverRegistry,
) -> None:
    """Saturdays to Saturdays-and-Sundays, previewed before it is committed.

    The added Sunday shows up for the schedule that uses the set directly *and*
    for the one that reaches it through a union, which is the whole argument for
    running the engine rather than approximating.
    """
    schedules, sets = preview_fixture()

    impact = await async_preview_day_set_change(
        resolvers,
        schedules,
        sets,
        day_set_id="weekend",
        proposed=day_set("weekend", weekdays("sat", "sun")),
        window=week(),
    )

    assert impact.schedules == ("container_user", "mentions_it", "morning")
    assert impact.day_sets == ("long_weekend",)
    # A recurrence, a composition member and a condition: three uses, three
    # schedules-or-sets, and the two numbers measure different things.
    assert impact.references == 3
    assert impact.removed == ()
    assert impact.unavailable == ()
    assert [(change.schedule_id, change.start_date) for change in impact.added] == [
        ("container_user", date(2026, 10, 4)),
        ("morning", date(2026, 10, 4)),
    ]
    assert all(
        change.after is not None
        and change.after.status is OccurrenceStatus.SCHEDULED
        and change.after.start == ny(2026, 10, 4, 7)
        for change in impact.added
    )


async def test_narrowing_a_day_set_removes_occurrences(
    resolvers: ResolverRegistry,
) -> None:
    """The other direction, and the reason `before` is carried rather than a verb.

    "This Saturday 07:00 stops happening" needs the occurrence that stops, not a
    count.
    """
    schedules, sets = preview_fixture()

    impact = await async_preview_day_set_change(
        resolvers,
        schedules,
        sets,
        day_set_id="weekend",
        proposed=day_set("weekend", weekdays("sun")),
        window=week(),
    )

    removed = {(change.schedule_id, change.start_date) for change in impact.removed}
    added = {(change.schedule_id, change.start_date) for change in impact.added}

    assert ("morning", date(2026, 10, 3)) in removed
    assert ("morning", date(2026, 10, 4)) in added
    (gone,) = [c for c in impact.removed if c.schedule_id == "morning"]
    assert gone.before is not None
    assert gone.before.start == ny(2026, 10, 3, 7)
    assert gone.after is None
    assert gone.removed is True


async def test_a_condition_only_user_is_reported_with_no_occurrence_changes(
    resolvers: ResolverRegistry,
) -> None:
    """A real limit of the preview, and the honest way to present it.

    Conditions are evaluated when a transition is decided, not when occurrences
    are enumerated (D25, D26), so editing a set a schedule only *mentions* changes
    nothing about its timeline — it changes whether those occurrences will run.
    The schedule is still listed, because "this edit touches four schedules" is
    the warning; claiming an occurrence change would be false.
    """
    schedules, sets = preview_fixture()

    impact = await async_preview_day_set_change(
        resolvers,
        schedules,
        sets,
        day_set_id="weekend",
        proposed=day_set("weekend", weekdays("sat", "sun")),
        window=week(),
    )

    assert "mentions_it" in impact.schedules
    assert all(change.schedule_id != "mentions_it" for change in impact.changes)


async def test_an_untouched_schedule_is_never_enumerated(
    resolvers: ResolverRegistry,
) -> None:
    """The index is what bounds the cost, which is the other reason D57 exists."""
    schedules, sets = preview_fixture()

    impact = await async_preview_day_set_change(
        resolvers,
        schedules,
        sets,
        day_set_id="weekend",
        proposed=day_set("weekend", weekdays("sat", "sun")),
        window=week(),
    )

    assert "unrelated" not in impact.schedules


async def test_previewing_a_deletion_says_the_schedule_breaks(
    resolvers: ResolverRegistry,
) -> None:
    """`proposed=None`, and `broken` rather than a bare list of removals.

    An unevaluable recurrence is the schedule's fault rather than any rule's, so
    `plan.py` reports it once on the plan and the occurrences leave the diff as
    removals. Without `broken` the preview would render a narrowed set and a
    deleted one identically, which is the difference between "fewer occurrences"
    and "this schedule stops working".
    """
    schedules, sets = preview_fixture()

    impact = await async_preview_day_set_change(
        resolvers,
        schedules,
        sets,
        day_set_id="weekend",
        proposed=None,
        window=week(),
    )

    assert impact.broken == ("container_user", "morning")
    assert impact.added == ()
    # The container breaks too: its member is gone, so D19's one level cannot be
    # evaluated either.
    assert {change.schedule_id for change in impact.removed} == {
        "container_user",
        "morning",
    }
    # And a schedule that only names it in a condition is not broken by its
    # deletion as far as enumeration goes.
    assert "mentions_it" not in impact.broken


async def test_a_set_that_gains_extent_alters_rather_than_removes(
    resolvers: ResolverRegistry,
) -> None:
    """D11's two stages, seen through the preview, and D12's statuses doing the work.

    Replacing plain Saturdays with a Saturday-evening offering keeps the same
    recurrence date and changes the answer: 07:00 is no longer inside the set. The
    occurrence is *altered*, not removed, because `Occurrence.key` is the
    recurrence date rather than the resolved instant — which is what makes "this
    one moved" expressible at all.
    """
    schedules, sets = preview_fixture()

    impact = await async_preview_day_set_change(
        resolvers,
        schedules,
        sets,
        day_set_id="weekend",
        proposed=day_set("weekend", offering_source()),
        window=week(),
    )

    (altered,) = [c for c in impact.altered if c.schedule_id == "morning"]
    assert altered.start_date == date(2026, 10, 3)
    assert altered.before is not None
    assert altered.before.status is OccurrenceStatus.SCHEDULED
    assert altered.after is not None
    assert altered.after.status is OccurrenceStatus.OUTSIDE_SET
    # D12 — the instant is retained, so the timeline can draw the row greyed out
    # rather than silently dropping it.
    assert altered.after.start == ny(2026, 10, 3, 7)
    assert altered.after.will_run is False


async def test_a_no_op_edit_changes_nothing(resolvers: ResolverRegistry) -> None:
    """The reassurance case, and a guard against the diff reporting churn.

    An implementation that keyed the diff on object identity, or that compared
    plans by rendering them, would report every occurrence as altered here.
    """
    schedules, sets = preview_fixture()

    impact = await async_preview_day_set_change(
        resolvers,
        schedules,
        sets,
        day_set_id="weekend",
        proposed=day_set("weekend", weekdays("sat")),
        window=week(),
    )

    assert impact.changes == ()
    assert impact.broken == ()
    assert impact.schedules == ("container_user", "mentions_it", "morning")


async def test_a_disabled_schedule_is_still_previewed(
    resolvers: ResolverRegistry,
) -> None:
    """A schedule that is off today is one the user may turn on tomorrow.

    Reporting "nothing changes" because nothing is running would be true and
    useless, and the user would find out when they re-enabled it.
    """
    schedules = by_id(
        schedule(
            "morning",
            enabled=False,
            recurrence=on_day_set("weekend"),
            rules=[{"kind": "at", "id": "r1", "anchor": clock("07:00:00")}],
        )
    )
    sets = by_id(day_set("weekend", weekdays("sat")))

    impact = await async_preview_day_set_change(
        resolvers,
        schedules,
        sets,
        day_set_id="weekend",
        proposed=day_set("weekend", weekdays("sat", "sun")),
        window=week(),
    )

    assert [change.start_date for change in impact.added] == [date(2026, 10, 4)]
    assert impact.added[0].after is not None
    assert impact.added[0].after.armed is False


async def test_the_preview_window_is_the_callers(
    resolvers: ResolverRegistry,
) -> None:
    """D64's dividend: a hypothesis and the present are the same call.

    The frontend asks about the fortnight it is showing. Narrowing the window to a
    single day must narrow the answer, with no second code path and nothing
    cached.
    """
    schedules, sets = preview_fixture()
    proposed = day_set("weekend", weekdays("sat", "sun"))

    whole_week = await async_preview_day_set_change(
        resolvers,
        schedules,
        sets,
        day_set_id="weekend",
        proposed=proposed,
        window=week(),
    )
    just_saturday = await async_preview_day_set_change(
        resolvers,
        schedules,
        sets,
        day_set_id="weekend",
        proposed=proposed,
        window=Window(ny(2026, 10, 3), ny(2026, 10, 4)),
    )

    assert whole_week.added != ()
    assert just_saturday.changes == ()
    # Same reach either way: which schedules an edit touches is not a question
    # about a window.
    assert just_saturday.schedules == whole_week.schedules


async def test_nothing_is_written_by_a_preview(resolvers: ResolverRegistry) -> None:
    """What separates a preview from a save followed by an undo.

    The overlay is a lookup, so the stored collection cannot be touched; asserted
    because the alternative implementation — write, enumerate, roll back — is the
    obvious one and would leave a window in which the day set was wrong for
    everything else reading it.
    """
    schedules, sets = preview_fixture()
    before = {key: dict(value) for key, value in sets.items()}

    await async_preview_day_set_change(
        resolvers,
        schedules,
        sets,
        day_set_id="weekend",
        proposed=day_set("weekend", weekdays("sun")),
        window=week(),
    )

    assert sets == before
