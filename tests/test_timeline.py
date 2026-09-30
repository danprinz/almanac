"""D63's one view — the enumerated future, the recorded past, and the join.

Three things are being asserted here, and they fail in different ways:

- **the future half is the engine's, unfiltered.** D12 says an occurrence dropped
  by D11's precise stage has to reach the screen *as dropped*, so the test that
  matters most is the one where a row is present and says `outside_set`. A
  timeline that quietly omitted it would pass every other test in this file.
- **the past half is D107's two events put back together on their shared
  `Context`.** That join runs against the real recorder — `recorder_mock` is the
  harness's in-memory SQLite instance, not a stub — because the parts most likely
  to be wrong are the ones that only exist in SQL: which columns are live
  (`event_type` and `event_data` on `events` are `UNUSED_LEGACY_COLUMN`), and
  whether a custom event is persisted at all without an allow-list (A.4 says yes,
  and this is where that is checked rather than believed).
- **no clock is read.** Every instant in this file is written down, including the
  pivot, which is what `tests/test_design_constraints.py` enforces from the other
  side. The recorder tests are the exception and cannot be otherwise: the
  recorder stamps `time_fired` off the real wall clock, so their windows are the
  only ones here built from `dt_util.now()` — and they are built in the *test*,
  which is allowed to know what time it is, never in the code under test.

The schedule shape, the 2027 date window and the NY zone are `test_tick.py`'s, and
are reused rather than re-invented so that a reader comparing the two files is
comparing behaviour and not scaffolding.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from homeassistant.core import Context, HomeAssistant
from homeassistant.util import dt as dt_util

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.almanac.actions import ActionResult, ActionStatus, ExecutionReport
from custom_components.almanac.const import (
    ANCHOR_CLOCK,
    ANCHOR_ENTITY_TIME,
    ATTR_RESULT,
    END_DURATION,
    RECUR_DAY_SET,
    RECUR_WEEKDAYS,
    RESULT_FIRED,
    RULE_AT,
    RULE_DURING,
    SOURCE_OFFERING,
    WS_DRY_RUN,
    WS_TIMELINE,
)
from custom_components.almanac.engine.transition import Transition, TransitionKind
from custom_components.almanac.events import async_fire_execution, async_fire_occurrence
from custom_components.almanac.resolver import (
    BaseResolver,
    Horizon,
    InvalidationKind,
    InvalidationSignal,
    Offering,
    Role,
    Span,
    Window,
)
from custom_components.almanac.storage import AlmanacData
from custom_components.almanac.timeline import Coverage, async_timeline

NY = ZoneInfo("America/New_York")

LATITUDE = 40.7128
LONGITUDE = -74.0060

WINDOW = {"from": "2027-10-01", "until": "2027-10-31"}

SHABBAT = "shabbat"


# --- helpers ---------------------------------------------------------------


def ny(*args: int) -> datetime:
    """A New York instant, written out."""
    return datetime(*args, tzinfo=NY)  # type: ignore[arg-type]


def daily() -> dict[str, Any]:
    """Every day."""
    return {
        "kind": RECUR_WEEKDAYS,
        "weekdays": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
    }


def clock(at: str) -> dict[str, Any]:
    """A clock anchor."""
    return {"kind": ANCHOR_CLOCK, "at": at}


def at_rule(rule_id: str = "r1", at: str = "17:00", **extra: Any) -> dict[str, Any]:
    """An `At` rule (D2)."""
    return {"kind": RULE_AT, "id": rule_id, "anchor": clock(at), **extra}


def during_rule(rule_id: str = "r1", at: str = "17:00") -> dict[str, Any]:
    """A `During` rule (D2), a clock start and a duration end."""
    return {
        "kind": RULE_DURING,
        "id": rule_id,
        "start_anchor": clock(at),
        "end": {"kind": END_DURATION, "duration": 3600},
    }


class WeekendResolver(BaseResolver):
    """`test_day_sets`'s stub source, reused verbatim: Friday 18:45 to Saturday 19:40.

    A resolver-backed set rather than a weekday one because only a span with real
    extent gives stage two something to reject — which is the row D12 is about.
    Registered into the *live* registry rather than swapped for it, so the timeline
    under test is reading `AlmanacData.resolvers` exactly as it does in service.
    """

    domain = "stub"

    def offering(self, key: str) -> Offering | None:
        """One key, with the day-set role."""
        if key == SHABBAT:
            return Offering(
                key=SHABBAT,
                display_name="Shabbat",
                roles=frozenset({Role.DAY_SET}),
                horizon=Horizon.unbounded(),
            )
        return None

    def invalidation(self, key: str) -> InvalidationSignal:
        """Pure arithmetic, so nothing is watched (D43)."""
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
    """Put `hass` in New York before anything is set up."""
    await hass.config.async_set_time_zone("America/New_York")
    await hass.config.async_update(latitude=LATITUDE, longitude=LONGITUDE, elevation=0)


@pytest.fixture(autouse=True)
def mock_recorder_before_hass(recorder_db_url: str) -> None:
    """Resolve the recorder's database url before the `hass` fixture builds.

    The harness ships this fixture as a no-op and asserts inside `recorder_db_url`
    that `hass` has not been built yet; requesting `recorder_mock` from a test
    signature alone trips that assertion. Overriding it here, autouse, is the
    harness's own documented way round and costs the tests that do not use the
    recorder nothing — the url is resolved, the recorder is not set up.
    """


async def create(hass: HomeAssistant, data: AlmanacData, name: str, **body: Any) -> str:
    """Store a schedule and return its id."""
    item = await data.schedules.async_create_item(
        {"name": name, "date_window": WINDOW, "recurrence": daily(), **body}
    )
    await hass.async_block_till_done()
    return str(item["id"])


def occurrences(result: dict[str, Any], index: int = 0) -> list[dict[str, Any]]:
    """The enumerated half of one schedule's row."""
    return result["schedules"][index]["plan"]["occurrences"]


# --- the future half: the engine's output, unfiltered ----------------------


async def test_the_timeline_enumerates_the_whole_window_not_just_the_future(
    hass: HomeAssistant, almanac_data: AlmanacData
) -> None:
    """D63 puts past and future in *one* view, so both halves are enumerated.

    The pivot sits in the middle of the window and the occurrence before it is
    still present. This is the property that makes "predicted versus actual"
    checkable at all: without the left-hand prediction there is nothing for the
    recorded row to disagree with, and D63's stated reason for merging the two
    views is that the disagreement should be visible without going looking.
    """
    await create(hass, almanac_data, "Lights", rules=[at_rule(at="17:00")])

    timeline = await async_timeline(
        hass,
        almanac_data,
        Window(ny(2027, 10, 1), ny(2027, 10, 4)),
        at=ny(2027, 10, 2, 20, 0),
    )

    starts = [occ.start for occ in timeline.schedules[0].plan.occurrences]
    assert starts == [ny(2027, 10, 1, 17), ny(2027, 10, 2, 17), ny(2027, 10, 3, 17)]
    assert timeline.at == ny(2027, 10, 2, 20, 0)


async def test_an_occurrence_outside_the_day_set_is_drawn_not_dropped(
    hass: HomeAssistant, almanac_data: AlmanacData
) -> None:
    """D12, which is the whole reason `OccurrenceStatus` has a fourth value.

    D11 offers candidate dates generously and then asks `covers` precisely; the
    dates the second stage rejects are exactly the ones a user cannot otherwise
    tell from a bug. The timeline does nothing to produce this — `plan.py` does —
    and the assertion is that the timeline does nothing to *lose* it either, which
    is a claim about a filter that must not exist. It is asserted on the *wire*
    form, because the serialisation boundary is where an omission would actually
    be introduced.

    22:00 on the Friday is inside the span; 22:00 on the Saturday is two and a
    quarter hours after it ended. Upstream that second row does not exist, and the
    absence is indistinguishable from the day set being wrong.
    """
    almanac_data.resolvers.async_register(WeekendResolver(hass))
    day_set = await almanac_data.day_sets.async_create_item(
        {
            "name": "Shabbat",
            "source": {"kind": SOURCE_OFFERING, "domain": "stub", "key": SHABBAT},
        }
    )
    await create(
        hass,
        almanac_data,
        "Announce",
        recurrence={"kind": RECUR_DAY_SET, "day_set_id": day_set["id"]},
        rules=[at_rule(at="22:00")],
    )

    timeline = await async_timeline(
        hass,
        almanac_data,
        Window(ny(2027, 10, 1), ny(2027, 10, 4)),
        at=ny(2027, 10, 1),
    )

    rendered = {
        occ["start_date"]: occ
        for occ in timeline.schedules[0].as_dict()["plan"]["occurrences"]
    }
    assert rendered["2027-10-01"]["status"] == "scheduled"
    assert rendered["2027-10-02"]["status"] == "outside_set"
    assert rendered["2027-10-02"]["will_run"] is False
    # The instant that failed the precise stage is kept, because "Saturday 22:00
    # was dropped because Shabbat had already ended" needs it to be sayable.
    assert rendered["2027-10-02"]["start"] == ny(2027, 10, 2, 22).isoformat()


async def test_a_window_past_the_budget_reports_not_computed(
    hass: HomeAssistant, almanac_data: AlmanacData
) -> None:
    """D44 — beyond ninety days the timeline says *not computed*, never guesses.

    The anchor is a `clock`, which declares `UNBOUNDED` (D13), so the only limit
    that bites here is ours — and the plan has to say so in both of its fields,
    not just in the one the renderer happens to read.

    That is the regression this test holds down. `_Horizons` used to be built over
    the *clamped* window, which made `known_through` unable to exceed
    `computed_through`: `fully_known` was false for any window wider than the
    budget however unbounded every source had declared itself, so a caller reading
    it would conclude the source had run out when the truth is that we declined to
    look. D44 says in as many words that the two are different facts — "one is our
    compute budget, the other is the source's honesty" — and `solid_through`
    exists to combine them, which it can only do if they arrive uncombined.
    """
    await create(hass, almanac_data, "Lights", rules=[at_rule(at="17:00")])

    window = Window(ny(2027, 10, 1), ny(2028, 10, 1))
    timeline = await async_timeline(hass, almanac_data, window, at=ny(2027, 10, 1))

    row = timeline.schedules[0]
    assert row.as_dict()["coverage"] == Coverage.NOT_COMPUTED
    assert row.plan.computed_through == ny(2027, 10, 1) + timedelta(days=90)
    assert not row.plan.fully_computed
    # The budget ran out; the source did not. Two facts, two answers.
    assert row.plan.known_through == window.end
    assert row.plan.fully_known
    # And `solid_through` is where they are deliberately put back together.
    assert row.plan.solid_through == row.plan.computed_through


async def test_a_window_inside_the_budget_reports_known(
    hass: HomeAssistant, almanac_data: AlmanacData
) -> None:
    """The ordinary case, asserted so the previous test is a contrast."""
    await create(hass, almanac_data, "Lights", rules=[during_rule(at="17:00")])

    timeline = await async_timeline(
        hass,
        almanac_data,
        Window(ny(2027, 10, 1), ny(2027, 10, 4)),
        at=ny(2027, 10, 2),
    )
    assert timeline.schedules[0].as_dict()["coverage"] == Coverage.KNOWN


async def test_a_source_that_will_not_commit_reports_unknown(
    hass: HomeAssistant, almanac_data: AlmanacData
) -> None:
    """D13's limit, and the one that is *not* D44's — the contrast that matters.

    An `entity_time` anchor is `NEXT_ONLY` (A.2): the sensor holds one value and
    nothing past it is claimed. The window is three days, well inside the compute
    budget, so `fully_computed` is true and the only exhausted limit is the
    source's. That is `unknown`, and a renderer that showed it as `not_computed`
    would tell the user to widen a window when the fix is to look at a sensor.
    """
    hass.states.async_set("sensor.candle_lighting", "2027-10-01T18:12:00-04:00")
    await create(
        hass,
        almanac_data,
        "Candles",
        rules=[
            {
                "kind": RULE_AT,
                "id": "r1",
                "anchor": {
                    "kind": ANCHOR_ENTITY_TIME,
                    "entity_id": "sensor.candle_lighting",
                    "offset": 0,
                },
            }
        ],
    )

    timeline = await async_timeline(
        hass,
        almanac_data,
        Window(ny(2027, 10, 1), ny(2027, 10, 4)),
        at=ny(2027, 10, 1),
    )

    row = timeline.schedules[0]
    assert row.plan.fully_computed
    assert not row.plan.fully_known
    assert row.as_dict()["coverage"] == Coverage.UNKNOWN


async def test_schedule_ids_narrows_the_view_and_forgives_a_stale_one(
    hass: HomeAssistant, almanac_data: AlmanacData
) -> None:
    """A deleted schedule between two renders is a race, not an error.

    The panel holds ids it read a moment ago, and failing the whole screen because
    one of them has since gone would make a delete look like a crash.
    """
    first = await create(hass, almanac_data, "One", rules=[at_rule(at="17:00")])
    await create(hass, almanac_data, "Two", rules=[at_rule(at="18:00")])

    timeline = await async_timeline(
        hass,
        almanac_data,
        Window(ny(2027, 10, 1), ny(2027, 10, 2)),
        at=ny(2027, 10, 1),
        schedule_ids=[first, "a schedule that no longer exists"],
    )
    assert [row.schedule_id for row in timeline.schedules] == [first]


# --- the past half: no recorder --------------------------------------------


async def test_without_a_recorder_the_past_is_absent_not_empty(
    hass: HomeAssistant, almanac_data: AlmanacData
) -> None:
    """D48 makes the recorder the audit trail, and it is optional.

    `manifest.json` deliberately does not depend on it, so the timeline has to
    distinguish *nothing happened* from *nothing was written down*. An empty left
    half with no explanation is the more damaging of the two readings, because it
    is the one a user would believe.
    """
    await create(hass, almanac_data, "Lights", rules=[at_rule(at="17:00")])

    timeline = await async_timeline(
        hass,
        almanac_data,
        Window(ny(2027, 10, 1), ny(2027, 10, 4)),
        at=ny(2027, 10, 3),
    )
    assert timeline.recorded is False
    assert timeline.as_dict()["recorded"] is False
    assert timeline.schedules[0].past == ()


# --- the past half: the real recorder --------------------------------------


def _fire_pair(
    hass: HomeAssistant, schedule: dict[str, Any], at: datetime, *, executed: bool
) -> Context:
    """Fire D107's two events the way the tick does: one context, in order.

    Through `events.py`'s own producers rather than `bus.async_fire` with a
    hand-written dict, because the payload spelling is what the join reads back
    and a test that wrote its own would assert against itself.
    """
    context = Context()
    transition = Transition(
        kind=TransitionKind.FIRE,
        schedule_id=schedule["id"],
        rule_id="r1",
        start_date=at.date(),
        at=at,
    )
    async_fire_occurrence(hass, schedule, transition, context=context)
    if executed:
        report = ExecutionReport(
            actions=(
                ActionResult(
                    index=0,
                    kind="service",
                    target="light.turn_on",
                    status=ActionStatus.SUCCEEDED,
                ),
            )
        )
        async_fire_execution(hass, schedule, transition, report, context=context)
    return context


async def test_the_recorded_pair_is_joined_on_its_shared_context(
    recorder_mock: Any,
    hass: HomeAssistant,
    almanac_data: AlmanacData,
) -> None:
    """D107's join, against the real recorder.

    The window is built from the wall clock because the recorder stamps
    `time_fired` from it — see this module's docstring. The payload instants are
    still written down, which is the point of the assertion: the row is *placed*
    at the occurrence's own `at`, in 2027, while having been *found* by its
    recorded time, today. That is the skew `_read_events` documents, made visible.
    """
    schedule_id = await create(
        hass, almanac_data, "Lights", rules=[at_rule(at="17:00")]
    )
    schedule = dict(almanac_data.schedules.data[schedule_id])

    _fire_pair(hass, schedule, ny(2027, 10, 2, 17), executed=True)
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)

    now = dt_util.now()
    timeline = await async_timeline(
        hass,
        almanac_data,
        Window(now - timedelta(hours=1), now + timedelta(hours=1)),
        at=now + timedelta(minutes=1),
    )

    assert timeline.recorded is True
    row = next(
        entry for entry in timeline.schedules if entry.schedule_id == schedule_id
    )
    assert len(row.past) == 1
    entry = row.past[0].as_dict()
    # The occurrence event's fields...
    assert entry["kind"] == str(TransitionKind.FIRE)
    assert entry["at"] == ny(2027, 10, 2, 17).isoformat()
    assert entry["name"] == "Lights"
    # ...and the execution event's, merged on the shared context (D107).
    assert entry["executed"] is True
    assert entry[ATTR_RESULT] == RESULT_FIRED
    assert [action["target"] for action in entry["actions"]] == ["light.turn_on"]
    assert entry["context_id"]
    # Found by `time_fired`, placed by `at`. Both instants survive, so a reader can
    # see which one the query used.
    assert entry["recorded_at"] != entry["at"]


async def test_an_occurrence_that_executed_nothing_is_still_a_past_row(
    recorder_mock: Any,
    hass: HomeAssistant,
    almanac_data: AlmanacData,
) -> None:
    """D107's second event is conditional; the first is not.

    `_async_execute` fires the execution event only when something was attempted,
    so a skipped or empty occurrence leaves exactly one row — and it is still an
    occurrence, and still the answer to "why did nothing happen at 17:00", which
    D52's own note says is what a person opens the schedule to find out.
    """
    schedule_id = await create(
        hass, almanac_data, "Lights", rules=[at_rule(at="17:00")]
    )
    schedule = dict(almanac_data.schedules.data[schedule_id])

    _fire_pair(hass, schedule, ny(2027, 10, 2, 17), executed=False)
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)

    now = dt_util.now()
    timeline = await async_timeline(
        hass,
        almanac_data,
        Window(now - timedelta(hours=1), now + timedelta(hours=1)),
        at=now + timedelta(minutes=1),
    )
    entry = timeline.schedules[0].past[0].as_dict()
    assert entry["executed"] is False
    assert ATTR_RESULT not in entry


async def test_an_execution_with_no_occurrence_is_placed_not_dropped(
    recorder_mock: Any,
    hass: HomeAssistant,
    almanac_data: AlmanacData,
) -> None:
    """An unpaired execution is ordinary, and it carries everything it needs.

    It arrives two ways. D116's manual run fires this event and *only* this event,
    so an unpaired execution is the normal shape of a `run_now` rather than a
    damaged pair. And a window whose start falls between D107's two events clips
    the first of them, which a view promising to omit nothing (D12) may not lose.

    This used to be dropped, on the stated grounds that without its partner the row
    "has no `kind` and no `at` and cannot be placed". That was simply wrong about
    the payload: `execution_payload` repeats `schedule_id`, `entity_id`, `rule_id`,
    `kind` and `at` deliberately, "rather than expecting a reader to join on the
    context id", so every field needed to place the row is already in it.

    `announced` is what tells the two apart on the wire, and it is not cosmetic —
    a renderer that drew a manual run identically to a prediction that came true
    would be hiding the one thing on the screen worth seeing.
    """
    schedule_id = await create(
        hass, almanac_data, "Lights", rules=[at_rule(at="17:00")]
    )
    schedule = dict(almanac_data.schedules.data[schedule_id])

    transition = Transition(
        kind=TransitionKind.FIRE,
        schedule_id=schedule_id,
        rule_id="r1",
        start_date=ny(2027, 10, 2).date(),
        at=ny(2027, 10, 2, 17),
    )
    report = ExecutionReport(
        actions=(
            ActionResult(
                index=0,
                kind="service",
                target="light.turn_on",
                status=ActionStatus.SUCCEEDED,
            ),
        )
    )
    # The execution event alone — no `async_fire_occurrence`. This is what D116's
    # `run_now` leaves behind.
    async_fire_execution(hass, schedule, transition, report, context=Context())
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)

    now = dt_util.now()
    timeline = await async_timeline(
        hass,
        almanac_data,
        Window(now - timedelta(hours=1), now + timedelta(hours=1)),
        at=now + timedelta(minutes=1),
    )

    row = next(
        entry for entry in timeline.schedules if entry.schedule_id == schedule_id
    )
    assert len(row.past) == 1
    entry = row.past[0].as_dict()
    assert entry["announced"] is False
    assert entry["executed"] is True
    # Placed at the payload's own instant, from the execution half alone.
    assert entry["at"] == ny(2027, 10, 2, 17).isoformat()
    assert entry["kind"] == str(TransitionKind.FIRE)
    assert entry[ATTR_RESULT] == RESULT_FIRED


async def test_a_joined_pair_is_marked_as_announced(
    recorder_mock: Any,
    hass: HomeAssistant,
    almanac_data: AlmanacData,
) -> None:
    """The other side of `announced`, so the flag cannot be a constant."""
    schedule_id = await create(
        hass, almanac_data, "Lights", rules=[at_rule(at="17:00")]
    )
    schedule = dict(almanac_data.schedules.data[schedule_id])

    _fire_pair(hass, schedule, ny(2027, 10, 2, 17), executed=True)
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)

    now = dt_util.now()
    timeline = await async_timeline(
        hass,
        almanac_data,
        Window(now - timedelta(hours=1), now + timedelta(hours=1)),
        at=now + timedelta(minutes=1),
    )

    row = next(
        entry for entry in timeline.schedules if entry.schedule_id == schedule_id
    )
    assert row.past[0].as_dict()["announced"] is True


async def test_past_rows_are_ordered_by_the_instant_they_are_about(
    recorder_mock: Any,
    hass: HomeAssistant,
    almanac_data: AlmanacData,
) -> None:
    """Sorted by the payload's `at`, not by the order the recorder saw them.

    The two agree in the ordinary case. They disagree after a restart, when D41's
    recovery pass records an occurrence materially later than the instant it is
    about — and on a timeline the row belongs where the occurrence was due, which
    is where a person will look for it. Firing in reverse order is the cheap way
    to state that the sort is real.
    """
    schedule_id = await create(
        hass, almanac_data, "Lights", rules=[at_rule(at="17:00")]
    )
    schedule = dict(almanac_data.schedules.data[schedule_id])

    _fire_pair(hass, schedule, ny(2027, 10, 3, 17), executed=False)
    _fire_pair(hass, schedule, ny(2027, 10, 1, 17), executed=False)
    _fire_pair(hass, schedule, ny(2027, 10, 2, 17), executed=False)
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)

    now = dt_util.now()
    timeline = await async_timeline(
        hass,
        almanac_data,
        Window(now - timedelta(hours=1), now + timedelta(hours=1)),
        at=now + timedelta(minutes=1),
    )
    assert [entry.at for entry in timeline.schedules[0].past] == [
        ny(2027, 10, 1, 17),
        ny(2027, 10, 2, 17),
        ny(2027, 10, 3, 17),
    ]


async def test_the_recorder_read_stops_at_the_pivot(
    recorder_mock: Any,
    hass: HomeAssistant,
    almanac_data: AlmanacData,
) -> None:
    """Nothing can have been recorded after `at`, so the query does not look.

    Asserted by pivoting *before* the rows were written: the window still covers
    them, and the past half is empty because the pivot does not. This is what
    makes a dry run's timeline honest — pivot it at next Friday and you get next
    Friday's predictions with today's history, not history from the future.
    """
    schedule_id = await create(
        hass, almanac_data, "Lights", rules=[at_rule(at="17:00")]
    )
    schedule = dict(almanac_data.schedules.data[schedule_id])
    _fire_pair(hass, schedule, ny(2027, 10, 2, 17), executed=True)
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)

    now = dt_util.now()
    timeline = await async_timeline(
        hass,
        almanac_data,
        Window(now - timedelta(hours=2), now + timedelta(hours=1)),
        at=now - timedelta(hours=1),
    )
    assert timeline.recorded is True
    assert timeline.schedules[0].past == ()


async def test_a_foreign_event_of_the_same_type_does_not_become_a_row(
    recorder_mock: Any,
    hass: HomeAssistant,
    almanac_data: AlmanacData,
) -> None:
    """The recorder's event type is a string in a shared table, not a namespace.

    Anything may fire `almanac_occurrence`, and a payload the timeline cannot read
    must not become a row with invented fields. It is dropped, which is the one
    place this module departs from D12's "render it anyway" — an occurrence with
    no instant cannot be placed on a timeline at all.
    """
    await create(hass, almanac_data, "Lights", rules=[at_rule(at="17:00")])
    hass.bus.async_fire("almanac_occurrence", {"not": "ours"})
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)

    now = dt_util.now()
    timeline = await async_timeline(
        hass,
        almanac_data,
        Window(now - timedelta(hours=1), now + timedelta(hours=1)),
        at=now + timedelta(minutes=1),
    )
    assert timeline.schedules[0].past == ()


# --- the websocket surface -------------------------------------------------


async def _ws(client: Any, msg_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    await client.send_json({"id": msg_id, **payload})
    return await client.receive_json()


async def test_websocket_timeline_returns_the_view(
    hass: HomeAssistant,
    setup_almanac: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """What step 9's panel calls, and the shape it gets back."""
    data: AlmanacData = setup_almanac.runtime_data
    schedule_id = await create(hass, data, "Lights", rules=[at_rule(at="17:00")])
    client = await hass_ws_client(hass)

    result = await _ws(
        client,
        1,
        {
            "type": WS_TIMELINE,
            "start": ny(2027, 10, 1).isoformat(),
            "end": ny(2027, 10, 3).isoformat(),
            "at": ny(2027, 10, 2, 12).isoformat(),
        },
    )
    assert result["success"], result
    view = result["result"]
    assert view["at"] == ny(2027, 10, 2, 12).isoformat()
    assert view["schedules"][0]["schedule_id"] == schedule_id
    assert view["schedules"][0]["entity_id"] == "switch.lights"
    assert [occ["start"] for occ in occurrences(view)] == [
        ny(2027, 10, 1, 17).isoformat(),
        ny(2027, 10, 2, 17).isoformat(),
    ]


async def test_websocket_timeline_refuses_a_naive_instant(
    hass: HomeAssistant,
    setup_almanac: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """D64 at the API boundary: the backend will not guess what time it is.

    A string with no offset does not name an instant, and the only way to make one
    of it is to pick a zone on the caller's behalf. That is a clock read in all but
    spelling, and the frontend — which has the offset — is the component that
    actually knows.
    """
    client = await hass_ws_client(hass)
    result = await _ws(
        client,
        1,
        {
            "type": WS_TIMELINE,
            "start": "2027-10-01T00:00:00",
            "end": "2027-10-03T00:00:00",
            "at": "2027-10-02T12:00:00",
        },
    )
    assert not result["success"]
    assert result["error"]["code"] == "invalid_format"


async def test_websocket_timeline_refuses_a_window_that_does_not_move_forward(
    hass: HomeAssistant,
    setup_almanac: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """A date picker halfway through being used is not a traceback."""
    client = await hass_ws_client(hass)
    result = await _ws(
        client,
        1,
        {
            "type": WS_TIMELINE,
            "start": ny(2027, 10, 3).isoformat(),
            "end": ny(2027, 10, 1).isoformat(),
            "at": ny(2027, 10, 2).isoformat(),
        },
    )
    assert not result["success"]
    assert result["error"]["code"] == "invalid_format"


async def test_websocket_dry_run_is_admin_only(
    hass: HomeAssistant,
    setup_almanac: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
    hass_read_only_access_token: str,
) -> None:
    """The dry run takes the tick's lock, so it is not for every session.

    The timeline is a plain read and is not restricted; this one can make the live
    scheduler wait, and the ability to do that is a maintainer's.
    """
    client = await hass_ws_client(hass, hass_read_only_access_token)
    result = await _ws(
        client, 1, {"type": WS_DRY_RUN, "at": ny(2027, 10, 2, 17).isoformat()}
    )
    assert not result["success"]
    assert result["error"]["code"] == "unauthorized"
