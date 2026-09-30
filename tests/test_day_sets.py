"""Day sets — D18–D22, and D11's two stages over them.

The file is in two halves, because a day set is two things. The first half is the
*object*: its own store, its own websocket CRUD, its own `binary_sensor`, and the
two directions of D19's one-level rule. The second half is the *evaluation*:
`async_candidate_dates` generating generously and `async_covers` filtering
exactly, which is the mechanism that stops "at 22:00 on Shabbat" from firing
twice.

The Shabbat case is written out with a stub resolver rather than mocked, because
it is the motivating example and because it is the shape no date-granular model
can express: a span from Friday evening to Saturday evening touches two civil
days, and everything interesting about D11 follows from that.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.almanac.const import (
    COMPOSE_INTERSECT,
    COMPOSE_MINUS,
    COMPOSE_UNION,
    RECUR_DAY_SET,
    SOURCE_COMPOSITION,
    SOURCE_DATES,
    SOURCE_OFFERING,
    SOURCE_WEEKDAYS,
)
from custom_components.almanac.engine import (
    OccurrenceStatus,
    async_enumerate,
)
from custom_components.almanac.engine.day_set import (
    async_candidate_dates,
    async_covers,
    day_set_references,
    day_window,
)
from custom_components.almanac.resolver import (
    BaseResolver,
    Horizon,
    InvalidationKind,
    InvalidationSignal,
    Offering,
    ResolverRegistry,
    Role,
    Span,
    Unresolved,
    UnresolvedReason,
    Window,
    async_create_registry,
)
from custom_components.almanac.schema import STORAGE_SCHEMA
from custom_components.almanac.storage import AlmanacData

NY = ZoneInfo("America/New_York")

SHABBAT = "shabbat"


# --- helpers ---------------------------------------------------------------


def ny(*args: int) -> datetime:
    """A New York instant, written out."""
    return datetime(*args, tzinfo=NY)  # type: ignore[arg-type]


class StubDaySets:
    """A `DaySetLookup` over a literal dict, which is all the protocol asks for.

    The engine takes the protocol rather than the collection precisely so that
    this is possible (see `DaySetLookup`), and using it here rather than a real
    collection keeps the evaluation tests independent of storage.
    """

    def __init__(self, items: dict[str, dict[str, Any]]) -> None:
        """Hold the items."""
        self.items = items

    def async_get_day_set(self, day_set_id: str) -> dict[str, Any] | None:
        """The item, or `None`."""
        return self.items.get(day_set_id)


class WeekendResolver(BaseResolver):
    """A stub day-set source whose spans deliberately cross a civil midnight.

    Friday 18:45 to Saturday 19:40 local, which is §5.4's example with the
    numbers it uses. The point of a *resolver*-backed set rather than a weekday
    one is that the span has a real extent, so stage one yields two days and
    stage two has something to filter.
    """

    domain = "stub"

    def offering(self, key: str) -> Offering | None:
        """Two keys: one with the day-set role, one deliberately without it."""
        if key == SHABBAT:
            return Offering(
                key=SHABBAT,
                display_name="Shabbat",
                roles=frozenset({Role.DAY_SET}),
                horizon=Horizon.unbounded(),
            )
        if key == "instant":
            return Offering(
                key="instant",
                display_name="An instant",
                roles=frozenset({Role.ANCHOR}),
                horizon=Horizon.unbounded(),
            )
        return None

    def invalidation(self, key: str) -> InvalidationSignal:
        """Pure arithmetic on the calendar, so nothing has to be watched (D43)."""
        return InvalidationSignal(InvalidationKind.DETERMINISTIC)

    async def forecast(self, key: str, window: Window) -> list[Span]:
        """Every Friday-evening-to-Saturday-evening span touching the window."""
        spans: list[Span] = []
        day = window.start.astimezone(NY).date() - timedelta(days=2)
        last = window.end.astimezone(NY).date() + timedelta(days=1)
        while day <= last:
            if day.weekday() == 4:  # Friday
                spans.append(
                    Span(
                        datetime.combine(day, time(18, 45), tzinfo=NY),
                        datetime.combine(
                            day + timedelta(days=1), time(19, 40), tzinfo=NY
                        ),
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
    registry.async_register(WeekendResolver(hass))
    return registry


def offering_source(key: str = SHABBAT) -> dict[str, Any]:
    """A `kind: offering` day-set source addressing the stub."""
    return {"kind": SOURCE_OFFERING, "domain": "stub", "key": key}


def weekday_source(*names: str) -> dict[str, Any]:
    """A date-granular source, for which stage two is a no-op."""
    return {"kind": SOURCE_WEEKDAYS, "weekdays": list(names)}


def dates_source(*days: str) -> dict[str, Any]:
    """A listed-dates source."""
    return {"kind": SOURCE_DATES, "dates": list(days)}


def composition(operator: str, *members: str) -> dict[str, Any]:
    """A one-level composition (D19)."""
    return {"kind": SOURCE_COMPOSITION, "operator": operator, "members": list(members)}


def stored(**items: dict[str, Any]) -> StubDaySets:
    """Day sets as the store holds them, keyed by id."""
    return StubDaySets(
        {
            key: {"id": key, "name": key, "object_id": key, "source": source}
            for key, source in items.items()
        }
    )


# --- D18: a day set is its own object ---------------------------------------


async def test_day_sets_have_their_own_store_and_crud(
    almanac_data: AlmanacData,
) -> None:
    """D18's whole content: shared definition, edited in one place.

    A day set stored inside the schedules that use it would make "edit *Shabbat*
    once" impossible by construction, which is why this is a second collection
    rather than a field.
    """
    collection = almanac_data.day_sets
    assert collection.async_items() == []

    item = await collection.async_create_item(
        {"name": "Shabbat", "source": weekday_source("fri", "sat")}
    )
    assert item["object_id"] == "shabbat"
    assert item["source"]["weekdays"] == ["fri", "sat"]

    updated = await collection.async_update_item(
        item["id"], {"source": weekday_source("sat")}
    )
    assert updated["source"]["weekdays"] == ["sat"]
    assert updated["name"] == "Shabbat"

    await collection.async_delete_item(item["id"])
    assert collection.async_items() == []


async def test_a_day_set_records_an_owner_and_does_not_enforce_it(
    almanac_data: AlmanacData,
) -> None:
    """D18 records `owner`; D22 is the mitigation, not a lock.

    The field exists so a user about to edit an integration's day set can see
    that before they do. Refusing the edit was the rejected alternative: it would
    make a set shipped by an integration permanently un-fixable by the person
    whose calendar it is wrong about.
    """
    collection = almanac_data.day_sets
    item = await collection.async_create_item(
        {"name": "Yom Tov", "source": dates_source("2026-10-02"), "owner": "hdate"}
    )

    updated = await collection.async_update_item(
        item["id"], {"source": dates_source("2026-10-03")}
    )

    assert updated["owner"] == "hdate"
    assert updated["source"]["dates"] == ["2026-10-03"]


async def test_deleting_a_referenced_day_set_is_permitted(
    almanac_data: AlmanacData,
) -> None:
    """A dangling reference is visible and repairable; a refusal is a dead end.

    The schedule that referenced it degrades to `Unresolved` (D12), which the
    timeline renders. Refusing the delete was rejected because it leaves a user
    unable to clean up after an experiment, and because the reference may be in a
    schedule they can no longer see.
    """
    day_set = await almanac_data.day_sets.async_create_item(
        {"name": "Shabbat", "source": weekday_source("sat")}
    )
    await almanac_data.schedules.async_create_item(
        {
            "name": "Uses it",
            "recurrence": {"kind": RECUR_DAY_SET, "day_set_id": day_set["id"]},
        }
    )

    await almanac_data.day_sets.async_delete_item(day_set["id"])

    assert almanac_data.day_sets.async_items() == []


# --- D19: one level of set algebra, both directions -------------------------


async def test_a_composition_of_a_composition_is_refused(
    almanac_data: AlmanacData,
) -> None:
    """D19's first direction: a member may not itself be a combination."""
    collection = almanac_data.day_sets
    first = await collection.async_create_item(
        {"name": "Weekend", "source": weekday_source("sat", "sun")}
    )
    second = await collection.async_create_item(
        {"name": "Friday", "source": weekday_source("fri")}
    )
    combined = await collection.async_create_item(
        {
            "name": "Long weekend",
            "source": composition(COMPOSE_UNION, first["id"], second["id"]),
        }
    )

    with pytest.raises(vol.Invalid, match="combination"):
        await collection.async_create_item(
            {
                "name": "Deeper",
                "source": composition(COMPOSE_UNION, combined["id"], second["id"]),
            }
        )


async def test_a_member_cannot_become_a_composition_afterwards(
    almanac_data: AlmanacData,
) -> None:
    """D19's second direction, which is the one that is easy to forget.

    Refusing nesting only at creation would let a user reach the same forbidden
    shape by editing a leaf into a combination after it had been used as a
    member, and nothing downstream would notice until the evaluator refused it.
    """
    collection = almanac_data.day_sets
    leaf = await collection.async_create_item(
        {"name": "Saturday", "source": weekday_source("sat")}
    )
    other = await collection.async_create_item(
        {"name": "Friday", "source": weekday_source("fri")}
    )
    await collection.async_create_item(
        {
            "name": "Weekend",
            "source": composition(COMPOSE_UNION, leaf["id"], other["id"]),
        }
    )

    with pytest.raises(vol.Invalid, match="built from this day set"):
        await collection.async_update_item(
            leaf["id"],
            {"source": composition(COMPOSE_UNION, other["id"], other["id"])},
        )


async def test_a_day_set_cannot_contain_itself(almanac_data: AlmanacData) -> None:
    """The degenerate cycle, refused at the one place it can be created."""
    collection = almanac_data.day_sets
    item = await collection.async_create_item(
        {"name": "Weekend", "source": weekday_source("sat", "sun")}
    )
    other = await collection.async_create_item(
        {"name": "Friday", "source": weekday_source("fri")}
    )

    with pytest.raises(vol.Invalid, match="itself"):
        await collection.async_update_item(
            item["id"], {"source": composition(COMPOSE_UNION, item["id"], other["id"])}
        )


async def test_a_nested_composition_in_the_store_is_reported_not_evaluated(
    resolvers: ResolverRegistry,
) -> None:
    """A store written by an older build still has to produce a value, not a crash.

    The collection refuses to create this, so reaching it means hand-edited
    storage — exactly the case D12 wants rendered as *not computed*.
    """
    day_sets = stored(
        a=weekday_source("fri"),
        b=weekday_source("sat"),
        inner=composition(COMPOSE_UNION, "a", "b"),
        outer=composition(COMPOSE_UNION, "inner", "a"),
    )

    result = await async_candidate_dates(
        resolvers, day_sets, "outer", day_window(date(2026, 10, 1), date(2026, 10, 7), NY)
    )

    assert isinstance(result, Unresolved)
    assert result.reason is UnresolvedReason.ERROR
    assert "D19" in result.detail


def test_day_set_references_reads_one_level() -> None:
    """What D57's index needs from a source, and no more."""
    assert day_set_references(composition(COMPOSE_MINUS, "a", "b")) == ("a", "b")
    assert day_set_references(weekday_source("fri")) == ()


# --- D21: the published binary_sensor ---------------------------------------


async def test_each_day_set_publishes_a_binary_sensor(
    hass: HomeAssistant, almanac_data: AlmanacData
) -> None:
    """D21 — the entity that makes a day set useful outside almanac."""
    item = await almanac_data.day_sets.async_create_item(
        {"name": "Shabbat", "source": weekday_source("sat"), "description": "Sabbath"}
    )
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.shabbat")
    assert state is not None
    assert state.attributes["description"] == "Sabbath"
    assert er.async_get(hass).async_get("binary_sensor.shabbat") is not None
    assert er.async_get(hass).async_get("binary_sensor.shabbat").unique_id == item["id"]


async def test_the_binary_sensor_is_unknown_until_the_tick_speaks(
    hass: HomeAssistant, almanac_data: AlmanacData
) -> None:
    """D64 leaves this entity no way to compute "covers *now*" for itself.

    `unknown` rather than `off`, because "today is not in this set" and "nobody
    has asked yet" are different facts and an automation gated on the entity is
    entitled to tell them apart across a restart.
    """
    await almanac_data.day_sets.async_create_item(
        {"name": "Shabbat", "source": weekday_source("sat")}
    )
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.shabbat").state == "unknown"


async def test_the_day_set_entity_id_collides_with_nothing_silently(
    hass: HomeAssistant, almanac_data: AlmanacData
) -> None:
    """D66's one constraint, shared with schedules through one helper."""
    await almanac_data.day_sets.async_create_item(
        {"name": "Shabbat", "source": weekday_source("sat")}
    )
    await hass.async_block_till_done()

    with pytest.raises(vol.Invalid, match="already taken"):
        await almanac_data.day_sets.async_create_item(
            {"name": "Shabbat", "source": weekday_source("fri")}
        )


async def test_day_set_websocket_crud(
    hass: HomeAssistant,
    setup_almanac: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """D33 again, for the second collection: the UI's only path to a day set."""
    client = await hass_ws_client(hass)

    await client.send_json(
        {
            "id": 1,
            "type": "almanac/day_set/create",
            "name": "Shabbat",
            "source": weekday_source("sat"),
        }
    )
    created = await client.receive_json()
    assert created["success"], created
    day_set_id = created["result"]["id"]

    await client.send_json({"id": 2, "type": "almanac/day_set/list"})
    listed = await client.receive_json()
    assert [item["id"] for item in listed["result"]] == [day_set_id]

    await client.send_json(
        {
            "id": 3,
            "type": "almanac/day_set/update",
            "day_set_id": day_set_id,
            "description": "Friday night to Saturday night",
        }
    )
    updated = await client.receive_json()
    assert updated["success"], updated
    assert updated["result"]["description"] == "Friday night to Saturday night"

    await client.send_json(
        {"id": 4, "type": "almanac/day_set/delete", "day_set_id": day_set_id}
    )
    deleted = await client.receive_json()
    assert deleted["success"], deleted


# --- D11: the two stages ----------------------------------------------------


async def test_stage_one_is_generous_and_stage_two_is_exact(
    resolvers: ResolverRegistry,
) -> None:
    """§5.4, written out. This is the whole argument for two stages.

    A span from Friday 18:45 to Saturday 19:40 touches two civil days, so a date
    predicate marks both. Stage two is what knows that 22:00 on Saturday is after
    havdalah.
    """
    day_sets = stored(shabbat=offering_source())
    window = day_window(date(2026, 10, 2), date(2026, 10, 3), NY)

    days = await async_candidate_dates(resolvers, day_sets, SHABBAT, window)

    assert days == [date(2026, 10, 2), date(2026, 10, 3)]
    assert await async_covers(resolvers, day_sets, SHABBAT, ny(2026, 10, 2, 22)) is True
    assert await async_covers(resolvers, day_sets, SHABBAT, ny(2026, 10, 3, 22)) is False


async def test_a_date_granular_set_makes_stage_two_a_no_op(
    resolvers: ResolverRegistry,
) -> None:
    """The common case: the whole civil day is in force, at any hour of it."""
    day_sets = stored(weekend=weekday_source("sat", "sun"))

    assert await async_covers(resolvers, day_sets, "weekend", ny(2026, 10, 3, 3)) is True
    assert (
        await async_covers(resolvers, day_sets, "weekend", ny(2026, 10, 3, 23, 59))
        is True
    )
    assert (
        await async_covers(resolvers, day_sets, "weekend", ny(2026, 10, 2, 12)) is False
    )


async def test_an_at_rule_on_a_day_set_fires_once_not_twice(
    resolvers: ResolverRegistry,
) -> None:
    """The failure the two stages exist to prevent, asserted end to end.

    Without stage two this plan has two occurrences and the schedule announces
    Shabbat two hours after it ended. With it, the Saturday row is still present
    and marked `OUTSIDE_SET` — D12, because a row that vanished would be
    indistinguishable from a bug.
    """
    day_sets = stored(shabbat=offering_source())
    item = dict(
        STORAGE_SCHEMA(
            {
                "id": "sched",
                "name": "Announce",
                "object_id": "announce",
                "recurrence": {"kind": RECUR_DAY_SET, "day_set_id": SHABBAT},
                "rules": [
                    {
                        "kind": "at",
                        "id": "r1",
                        "anchor": {"kind": "clock", "at": "22:00:00"},
                    }
                ],
            }
        )
    )

    plan = await async_enumerate(
        resolvers,
        item,
        Window(ny(2026, 10, 2), ny(2026, 10, 4)),
        day_sets=day_sets,
    )

    by_date = {occ.start_date: occ for occ in plan.occurrences}
    assert by_date[date(2026, 10, 2)].status is OccurrenceStatus.SCHEDULED
    assert by_date[date(2026, 10, 3)].status is OccurrenceStatus.OUTSIDE_SET
    # The instant that failed the test is retained: "Saturday 22:00 was dropped
    # because Shabbat had already ended" needs it to be sayable.
    assert by_date[date(2026, 10, 3)].start == ny(2026, 10, 3, 22)
    assert by_date[date(2026, 10, 3)].will_run is False


async def test_a_day_set_recurrence_without_a_collection_is_a_value(
    resolvers: ResolverRegistry,
) -> None:
    """A caller that forgot the day sets gets a plan, not an exception (D12)."""
    item = dict(
        STORAGE_SCHEMA(
            {
                "id": "sched",
                "name": "Announce",
                "object_id": "announce",
                "recurrence": {"kind": RECUR_DAY_SET, "day_set_id": SHABBAT},
                "rules": [
                    {
                        "kind": "at",
                        "id": "r1",
                        "anchor": {"kind": "clock", "at": "22:00:00"},
                    }
                ],
            }
        )
    )

    plan = await async_enumerate(resolvers, item, Window(ny(2026, 10, 2), ny(2026, 10, 4)))

    assert plan.occurrences == ()
    assert plan.problem is not None
    assert plan.problem.reason is UnresolvedReason.ERROR


async def test_a_missing_day_set_is_unknown_key_not_empty(
    resolvers: ResolverRegistry,
) -> None:
    """A dangling reference resolves to nothing-computed, never to no-days.

    The distinction is the whole of D12 here: a set that returned an empty date
    list would make the schedule silently never run, which looks exactly like a
    schedule the user configured to never run.
    """
    result = await async_candidate_dates(
        resolvers,
        stored(),
        SHABBAT,
        day_window(date(2026, 10, 1), date(2026, 10, 7), NY),
    )

    assert isinstance(result, Unresolved)
    assert result.reason is UnresolvedReason.UNKNOWN_KEY


async def test_an_offering_with_no_day_set_role_is_refused(
    resolvers: ResolverRegistry,
) -> None:
    """An anchor-only offering has no honest answer to `covers`.

    Almost always false, because an instant has no interior — which is
    indistinguishable from a day set that is simply out of force. The registry
    refuses it on the role rather than letting the answer look like data.
    """
    day_sets = stored(instant=offering_source("instant"))

    result = await async_covers(resolvers, day_sets, "instant", ny(2026, 10, 2, 22))

    assert isinstance(result, Unresolved)
    assert result.reason is UnresolvedReason.ROLE_NOT_OFFERED


# --- D19: what the three operators mean -------------------------------------


@pytest.mark.parametrize(
    ("operator", "expected"),
    [
        (COMPOSE_UNION, [date(2026, 10, 2), date(2026, 10, 3), date(2026, 10, 9)]),
        (COMPOSE_INTERSECT, [date(2026, 10, 2)]),
        # `minus` returns the first member's days unfiltered on purpose: a day the
        # subtrahend covers only part of is still a day the difference can cover,
        # and stage two is where it gets narrowed.
        (COMPOSE_MINUS, [date(2026, 10, 2), date(2026, 10, 9)]),
    ],
)
async def test_composition_of_candidate_dates(
    resolvers: ResolverRegistry, operator: str, expected: list[date]
) -> None:
    """Stage one composes generously; the argument is in the module docstring."""
    day_sets = stored(
        fridays=weekday_source("fri"),
        second=dates_source("2026-10-02", "2026-10-03"),
        combined=composition(operator, "fridays", "second"),
    )

    days = await async_candidate_dates(
        resolvers,
        day_sets,
        "combined",
        day_window(date(2026, 10, 1), date(2026, 10, 10), NY),
    )

    assert days == expected


@pytest.mark.parametrize(
    ("operator", "friday", "saturday"),
    [
        (COMPOSE_UNION, True, True),
        (COMPOSE_INTERSECT, True, False),
        (COMPOSE_MINUS, False, True),
    ],
)
async def test_composition_of_covers_is_exact(
    resolvers: ResolverRegistry, operator: str, friday: bool, saturday: bool
) -> None:
    """Stage two composes exactly, which is where `minus` earns its keep.

    The pair is {fri, sat} composed with {fri}: the union is both days, the
    intersection is Friday, and the difference is Saturday. Nothing here is
    approximate, unlike stage one.
    """
    day_sets = stored(
        weekend=weekday_source("fri", "sat"),
        fridays=weekday_source("fri"),
        combined=composition(operator, "weekend", "fridays"),
    )

    assert (
        await async_covers(resolvers, day_sets, "combined", ny(2026, 10, 2, 12))
        is friday
    )
    assert (
        await async_covers(resolvers, day_sets, "combined", ny(2026, 10, 3, 12))
        is saturday
    )


async def test_an_unresolvable_member_makes_the_composition_unresolvable(
    resolvers: ResolverRegistry,
) -> None:
    """Propagated, never substituted with an empty set.

    Substituting "no days" for a broken member turns a repairable reference into
    a schedule that silently never runs, which is the failure D12 exists to make
    visible.
    """
    day_sets = stored(
        fridays=weekday_source("fri"),
        combined=composition(COMPOSE_INTERSECT, "fridays", "gone"),
    )

    result = await async_candidate_dates(
        resolvers,
        day_sets,
        "combined",
        day_window(date(2026, 10, 1), date(2026, 10, 7), NY),
    )

    assert isinstance(result, Unresolved)
    assert result.reason is UnresolvedReason.UNKNOWN_KEY


def test_a_day_window_ends_at_the_midnight_after_the_last_day() -> None:
    """D88's half-open end, applied to an inclusive range of civil days.

    Inclusive in, exclusive out. The off-by-one here is the one that would either
    lose the last day or claim the following one, and both are silent.
    """
    window = day_window(date(2026, 10, 2), date(2026, 10, 3), NY)

    assert window.start == ny(2026, 10, 2)
    assert window.end == ny(2026, 10, 4)
