"""D63's one view: what will happen, what did happen, and where they disagree.

The timeline is the screen the brief asks for, and D63 fixes its shape — past and
future in one view with *now* as the divider, predicted to the right, actual (from
the recorder) to the left. This module is that query. It is the first thing in the
package that *reads* the engine's output rather than producing it, and it is
deliberately thin: everything it returns is either an engine value serialised by
the engine's own `as_dict`, or a recorder row echoed as it was stored.

Three properties are worth stating, because each one is a decision:

**It reads no clock, and it is not allowed to.** D64 names `tick.py` as the only
module in this package that may, and `tests/test_design_constraints.py` enforces it
by an AST sweep. So the pivot instant — the `at` that D63 divides the view at — is a
*parameter*, supplied by the caller, exactly as `now` is threaded through the
engine. That is not a workaround. It is the same property that makes the dry run
possible: a timeline pivoted at a hypothetical instant and a timeline pivoted at the
real one are one function called twice. Core's own read APIs are shaped the same
way — `components/history/websocket_api.py` and `components/logbook/websocket_api.py`
both take `start_time` from the frontend rather than sampling a clock for it.

**The future half is enumerated over the whole window, not from the pivot
forward.** D63's claim is that "divergence between prediction and reality is visible
rather than something the user has to go looking for", and that is only true if the
left-hand side carries both — what the engine *would* have said about yesterday, and
what the recorder says actually happened. Enumerating only forward of the pivot would
make the left half a log, which is the screen this product already has a complaint
about.

**Nothing is omitted, and nothing is estimated.** D12: an occurrence dropped by
D11's precise stage is carried with `status = outside_set`, never left out —
`plan.py` produces it and `Occurrence.as_dict` emits it, so this module has to do
nothing but refrain from filtering. Section 5.5: the three states are rendered from
the declared horizon *alone*, and this module declines to synthesise the middle one.
See `Coverage` for what that costs and why the cost is the right one.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
import json
import logging
from typing import Any

from homeassistant.const import ATTR_ENTITY_ID, ATTR_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.recorder import DATA_INSTANCE, get_instance, session_scope

from .const import (
    ATTR_AT,
    ATTR_SCHEDULE_ID,
    CONF_NAME,
    EVENT_EXECUTION,
    EVENT_OCCURRENCE,
)
from .engine import Plan, async_enumerate
from .events import schedule_entity_id
from .resolver.contract import Window, absolute
from .storage import AlmanacData

_LOGGER = logging.getLogger(__name__)


class Coverage(StrEnum):
    """How far into the window the timeline is entitled to draw solid lines — D119.

    Section 5.5 names three states — **known**, **estimated**, **unknown** —
    "driven by this field alone", the field being D13's declared horizon. Two of
    the three map onto something the engine actually produces; the third does not,
    and this is a gap step 8 found rather than one it invented.

    `HorizonKind` is `UNBOUNDED` / `UNTIL` / `NEXT_ONLY`, and `Plan` reduces the
    lot to one instant, `known_through`. From one instant you get *before it* and
    *after it*. There is no third region, because there is no declaration that
    means "we can see this far and then it gets vague" — and inventing one is
    precisely what section 5.5 calls lying: a `NEXT_ONLY` source's tail rendered as
    *estimated* would be the silent forward projection of a sensor's current value,
    in the one situation where being wrong is most visible.

    So the three states here are **known**, **not computed** (D44 — our own budget
    ran out and we declined to look) and **unknown** (D13 — we looked and the
    source would not commit). The first two are different sentences with different
    remedies, which is exactly why section 10.6 insists the two limits are separate
    facts, and a renderer that collapsed them would tell a user to fix their sensor
    when the answer is to widen the window.

    **Provisional, and wants an owner's ruling.** The alternative rejected is
    mapping `NEXT_ONLY`'s tail to *estimated* so that section 5.5's three words are
    used verbatim. It was rejected because it makes the word *estimated* mean "we
    have one real value and then guesswork", which no part of the engine is
    prepared to produce and which section 5.5's own reasoning forbids. The other
    reading available to an owner is to add a fourth `HorizonKind` — something like
    `ESTIMATED_UNTIL` — and let a resolver declare it; that is a contract change,
    not a rendering one, which is why it is not being made here.
    """

    KNOWN = "known"
    NOT_COMPUTED = "not_computed"
    UNKNOWN = "unknown"


def coverage_of(plan: Plan) -> Coverage:
    """Which of the three states the *end* of this plan's window is in.

    Ties go to `NOT_COMPUTED`. When both limits land on the same instant the honest
    answer is the one the user can do something about: D44's ninety days is a
    setting, while a resolver's horizon is a property of the world.
    """
    if plan.fully_computed and plan.fully_known:
        return Coverage.KNOWN
    if not plan.fully_computed and absolute(plan.computed_through) <= absolute(
        plan.known_through
    ):
        return Coverage.NOT_COMPUTED
    return Coverage.UNKNOWN


@dataclass(frozen=True, slots=True)
class PastOccurrence:
    """One occurrence the recorder saw: D107's two events, put back together.

    D107 fires `almanac_occurrence` before the side effects and `almanac_execution`
    after, and says in as many words that "they share a `Context`, which is what
    makes the join possible". That is the join performed here — on the context id,
    not on the `(schedule_id, rule_id, at)` triple the two payloads happen to have
    in common. The triple would be a re-derivation of identity in a third place,
    and it is not even unique: a recovery pass (D41) can legitimately produce a
    second transition for the same occurrence at the same `at`.

    Both payloads are carried **verbatim**, not remapped. `events.py` owns what an
    occurrence looks like to a reader of the recorder, and a timeline that
    published its own spelling of the same fact would be a second definition that
    could drift from the stored one — and the stored one is the durable copy (D48).
    The only field added is the context id itself, which is what lets a frontend
    line a row up with the logbook entry D51 draws from the same context.
    """

    at: datetime
    recorded_at: datetime
    context_id: str
    occurrence: Mapping[str, Any]
    execution: Mapping[str, Any] | None = None

    @property
    def schedule_id(self) -> str | None:
        """Which schedule this row belongs to, from whichever half is present.

        Both payloads carry it — `execution_payload` repeats the four identifiers
        deliberately, "rather than expecting a reader to join on the context id" —
        and either half can be the one that is missing. An occurrence event is
        missing when the window opened between D107's pair, or when the run was
        out of band (D116); an execution event is missing when nothing was
        attempted, or when the window closed between the pair.
        """
        for payload in (self.occurrence, self.execution or {}):
            value = payload.get(ATTR_SCHEDULE_ID)
            if isinstance(value, str):
                return value
        return None

    def as_dict(self) -> dict[str, Any]:
        """Merge the pair, the later event's fields winning.

        The overlap is exact and intentional: `execution_payload` repeats
        `schedule_id`, `entity_id`, `rule_id`, `kind` and `at` from
        `occurrence_payload` with identical values, and adds `result` and D32's
        per-action results — the half of D49 that cannot exist until the actions
        have run. So the merge is a union, and the precedence only matters if the
        two ever disagree, in which case the post-execution record is the one that
        observed more.

        `executed` is not redundant with `result`. An occurrence that attempted
        nothing fires no execution event at all (`ExecutionReport.attempted`), and
        a reader has to be able to tell "nothing was attempted" from "the second
        event has not been written yet" — which is what the boundary case at the
        window's edge produces.

        `announced` is its mirror, and it is the flag that distinguishes a manual
        run from a scheduled one. D116 has `run_now` fire the execution event and
        not the occurrence event, so a row with `announced` false is one the engine
        did not predict — which is exactly the divergence D63 built this view to
        make visible, and a renderer that drew it identically to a predicted row
        would be hiding the one thing on the screen worth seeing.
        """
        return {
            **self.occurrence,
            **(self.execution or {}),
            "context_id": self.context_id,
            "recorded_at": self.recorded_at.isoformat(),
            "announced": bool(self.occurrence),
            "executed": self.execution is not None,
        }


@dataclass(frozen=True, slots=True)
class ScheduleTimeline:
    """One schedule's row of D63's view: predicted and actual over one window."""

    schedule_id: str
    name: str | None
    entity_id: str
    plan: Plan
    past: tuple[PastOccurrence, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """The wire form.

        `coverage` is emitted beside the two instants it is derived from rather
        than instead of them. A frontend that wants to draw the edge needs the
        instant; a frontend that wants to word the tooltip needs to know which
        limit produced it; and neither should have to re-implement the comparison,
        which per A.13 is not the obvious one.
        """
        return {
            ATTR_SCHEDULE_ID: self.schedule_id,
            ATTR_NAME: self.name,
            ATTR_ENTITY_ID: self.entity_id,
            "plan": self.plan.as_dict(),
            "coverage": str(coverage_of(self.plan)),
            "past": [entry.as_dict() for entry in self.past],
        }


@dataclass(frozen=True, slots=True)
class Timeline:
    """D63's view over one window, pivoted at one instant.

    `recorded` is the honest answer to "why is the left-hand side empty". The
    recorder is optional in Home Assistant, `manifest.json` does not depend on it,
    and D48's whole argument is that the recorder *is* the audit trail — so a user
    who has turned it off has no past, and the timeline has to say so rather than
    draw a blank half and let it read as "nothing happened".
    """

    window: Window
    at: datetime
    schedules: tuple[ScheduleTimeline, ...] = ()
    recorded: bool = True
    truncated: bool = False

    def as_dict(self) -> dict[str, Any]:
        """The wire form."""
        return {
            "window": {
                "start": self.window.start.isoformat(),
                "end": self.window.end.isoformat(),
            },
            ATTR_AT: self.at.isoformat(),
            "recorded": self.recorded,
            "truncated": self.truncated,
            "schedules": [entry.as_dict() for entry in self.schedules],
        }


# How many recorder rows one timeline query will read before it stops and says so.
# The pair of events is one occurrence, so this is roughly five thousand
# occurrences — far more than any window a person would look at, and a bound on
# what a merely enthusiastic window size can pull into memory. It is a guard rail
# rather than a decision anything depends on; `Timeline.truncated` is what makes
# hitting it visible instead of silent, which is the argument D12 makes about
# dropped occurrences, applied to the past half.
_MAX_ROWS = 10_000


async def async_timeline(
    hass: HomeAssistant,
    data: AlmanacData,
    window: Window,
    *,
    at: datetime,
    schedule_ids: Sequence[str] | None = None,
) -> Timeline:
    """D63's view: enumerate the window, and read back what the recorder saw.

    `at` is the divider and nothing else. It does not bound the enumeration — see
    the module docstring — and it bounds the recorder read only as an optimisation,
    because nothing can have been recorded after it.

    Pure in `(schedules, window, at, recorder contents)`. It resolves anchors, so it
    is `async`; it writes nothing, fires nothing and reads no clock (D64).
    """
    selected = _selected(data, schedule_ids)

    # The recorder read happens first, and once for the whole set rather than once
    # per schedule. It is the only I/O here, it is a single indexed scan either
    # way, and N round trips through the recorder's executor to answer one screen
    # would be the shape that makes a timeline feel slow at exactly the moment a
    # user is scrolling it.
    past, truncated = await async_past_occurrences(hass, window, at=at)
    recorded = past is not None
    by_schedule = _by_schedule(past or ())

    entries: list[ScheduleTimeline] = []
    for schedule_id, schedule in selected:
        plan = await async_enumerate(
            data.resolvers, schedule, window, day_sets=data.day_sets
        )
        entries.append(
            ScheduleTimeline(
                schedule_id=schedule_id,
                name=schedule.get(CONF_NAME),
                entity_id=schedule_entity_id(schedule),
                plan=plan,
                past=tuple(by_schedule.get(schedule_id, ())),
            )
        )

    return Timeline(
        window=window,
        at=at,
        schedules=tuple(entries),
        recorded=recorded,
        truncated=truncated,
    )


def _selected(
    data: AlmanacData, schedule_ids: Sequence[str] | None
) -> list[tuple[str, dict[str, Any]]]:
    """The schedules to draw, in collection order.

    An id that names nothing is skipped rather than raised on. A timeline is a
    read, and the frontend asking about a schedule that was deleted between one
    render and the next is an ordinary race, not an error worth failing a whole
    screen for.
    """
    items: dict[str, dict[str, Any]] = data.schedules.data
    if schedule_ids is None:
        return list(items.items())
    wanted = set(schedule_ids)
    return [(key, value) for key, value in items.items() if key in wanted]


def _by_schedule(past: Iterable[PastOccurrence]) -> dict[str, list[PastOccurrence]]:
    """Group recorder rows by the schedule they belong to.

    A row whose payloads carry no `schedule_id` between them is dropped: every
    event this package fires has one, so such a row is either a foreign event that
    happens to share the type name or a payload from a version that predates the
    field, and neither can be attributed to a schedule on this screen.
    """
    grouped: dict[str, list[PastOccurrence]] = {}
    for entry in past:
        if (schedule_id := entry.schedule_id) is None:
            continue
        grouped.setdefault(schedule_id, []).append(entry)
    return grouped


async def async_past_occurrences(
    hass: HomeAssistant,
    window: Window,
    *,
    at: datetime,
) -> tuple[tuple[PastOccurrence, ...] | None, bool]:
    """Read D107's event pairs back out of the recorder for `window`.

    Returns `(rows, truncated)`, with `rows` **`None`** — not empty — when there is
    no recorder to ask. The distinction is the one `Plan` insists on for the future
    half: an empty answer and an unanswerable question are different facts, and a
    view that conflates them tells a user nothing happened when the truth is that
    nothing was written down.

    The query runs in the recorder's own executor. That is not merely "off the
    event loop": `get_instance(hass).async_add_executor_job` is core's own pattern
    for recorder reads — `components/recorder/history/modern.py` and
    `components/logbook/websocket_api.py` both go through it — and the recorder's
    executor is the pool its session pool is sized for.
    """
    if DATA_INSTANCE not in hass.data:
        # `manifest.json` declares no hard dependency on the recorder, deliberately:
        # almanac schedules things whether or not anything is writing history down,
        # and making history a requirement would be a worse product than a timeline
        # with an empty left-hand side that says why. `get_instance` is `lru_cache`d
        # and raises `KeyError` on a miss, so this guard is the check rather than a
        # `try` around the call.
        return None, False

    # Nothing can have been recorded after the pivot, so the read stops there.
    # `min` over two aware datetimes compares them, hence `key=absolute` (A.13).
    upper = min(window.end, at, key=absolute)
    if absolute(upper) <= absolute(window.start):
        return (), False

    rows = await get_instance(hass).async_add_executor_job(
        _read_events, hass, window.start, upper
    )
    truncated = len(rows) >= _MAX_ROWS
    return _join(rows), truncated


@dataclass(slots=True)
class _Row:
    """One recorder row, decoded off the event loop."""

    event_type: str
    context_id: str
    recorded_at: datetime
    data: Mapping[str, Any] = field(default_factory=dict)


def _read_events(hass: HomeAssistant, start: datetime, end: datetime) -> list[_Row]:
    """The blocking half. Runs in the recorder's executor; never on the loop.

    The imports are function-local on purpose. `homeassistant.helpers.recorder` is
    a helper and safe to import anywhere, but `components.recorder.db_schema` is a
    component module and pulls SQLAlchemy's ORM with it; importing it at module
    scope would make every almanac set-up pay for a screen most users are not
    looking at, and would put a component almanac does not depend on into its
    import graph.

    Verified against the installed 2026.9.4 package rather than recalled:

    - `Events.event_type` and `Events.event_data` are `UNUSED_LEGACY_COLUMN`. The
      type lives in `event_types` and the payload in `event_data`, reached by the
      two joins below — a query written against the legacy columns compiles and
      returns nothing.
    - `ix_events_event_type_id_time_fired_ts` is the index this filter is shaped
      for, and its comment in `db_schema.py` says in as many words that it is "used
      for fetching events at a specific time, see logbook".
    - `time_fired_ts` is a float epoch, so the bounds are converted with
      `datetime.timestamp()`, which is offset-correct for any aware datetime and
      sidesteps A.13 entirely.
    - `context_id` is `UNUSED_LEGACY_COLUMN` too; the live column is
      `context_id_bin`, and `bytes_to_ulid_or_none` is core's own decoder for it.

    **The filter is on `time_fired_ts`, not on the payload's `at`.** They differ by
    `lateness`, and after a restart D41's recovery pass can record an occurrence
    materially later than the instant it is about. Filtering on the payload would
    need a JSON predicate against an unindexed column; filtering on the row's own
    time keeps the index and costs a known, bounded skew, which the caller can see
    because both instants are in the result.
    """
    # Imported here rather than at module scope — see the docstring.
    from sqlalchemy import select  # noqa: PLC0415

    from homeassistant.components.recorder.db_schema import (  # noqa: PLC0415
        EventData,
        Events,
        EventTypes,
    )
    from homeassistant.components.recorder.models import (  # noqa: PLC0415
        bytes_to_ulid_or_none,
    )

    statement = (
        select(
            EventTypes.event_type,
            Events.time_fired_ts,
            Events.context_id_bin,
            EventData.shared_data,
        )
        .outerjoin(EventTypes, Events.event_type_id == EventTypes.event_type_id)
        .outerjoin(EventData, Events.data_id == EventData.data_id)
        .where(EventTypes.event_type.in_((EVENT_OCCURRENCE, EVENT_EXECUTION)))
        .where(Events.time_fired_ts >= start.timestamp())
        .where(Events.time_fired_ts < end.timestamp())
        .order_by(Events.time_fired_ts)
        .limit(_MAX_ROWS)
    )

    rows: list[_Row] = []
    with session_scope(hass=hass, read_only=True) as session:
        for event_type, time_fired_ts, context_id_bin, shared_data in session.execute(
            statement
        ):
            context_id = bytes_to_ulid_or_none(context_id_bin)
            if context_id is None or time_fired_ts is None:
                # D50 gives every transition a `Context`, so neither can happen for
                # a row almanac wrote. If it does, the row cannot be joined to its
                # partner and has no identity on the screen.
                continue
            rows.append(
                _Row(
                    event_type=event_type,
                    context_id=context_id,
                    recorded_at=datetime.fromtimestamp(time_fired_ts, tz=UTC),
                    data=_decode(shared_data),
                )
            )
    return rows


def _decode(shared_data: str | None) -> Mapping[str, Any]:
    """Parse one stored payload, tolerating a row that is not what we expect.

    The recorder stores event data as JSON text and does not guarantee that a row
    with our event type was written by us — the type is a string in a shared table.
    A row that will not parse into a mapping is dropped by the caller rather than
    raised on, because one bad row must not take down the screen that exists to
    explain what went wrong.
    """
    if not shared_data:
        return {}
    try:
        parsed = json.loads(shared_data)
    except ValueError:
        _LOGGER.debug("almanac could not parse a recorded payload; skipping it")
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _join(rows: Sequence[_Row]) -> tuple[PastOccurrence, ...]:
    """Put D107's pairs back together, on the shared context id.

    Ordered by the payload's `at` rather than by the row's own time. Those are the
    same order in the ordinary case and differ exactly when D41's recovery pass
    handled something late — and on a timeline the row belongs where the occurrence
    was due, which is where a person will look for it.

    Both instants survive into the result. `recorded_at` is the row's own
    `time_fired`, and it is emitted because it is the one the *query* was filtered
    on — see `_read_events`. A row placed at an `at` outside the requested window is
    therefore possible, and a reader that can see both instants can tell that is
    what happened rather than suspecting the sort.
    """
    occurrences: dict[str, _Row] = {}
    executions: dict[str, _Row] = {}
    for row in rows:
        target = occurrences if row.event_type == EVENT_OCCURRENCE else executions
        # First wins. A context is created per transition and never reused, so a
        # second row with the same type and context is a duplicate, not an update.
        target.setdefault(row.context_id, row)

    entries: list[PastOccurrence] = []
    for context_id, row in occurrences.items():
        if not row.data:
            continue
        execution = executions.pop(context_id, None)
        at = _parse(row.data.get(ATTR_AT))
        if at is None:
            _LOGGER.debug(
                "almanac recorded an occurrence with no readable instant; skipping it"
            )
            continue
        entries.append(
            PastOccurrence(
                at=at,
                recorded_at=row.recorded_at,
                context_id=context_id,
                occurrence=row.data,
                execution=execution.data if execution is not None else None,
            )
        )

    # An execution with no occurrence arrives two ways, and both are rendered.
    #
    # D116's manual run fires this event and not its partner, so an unpaired
    # execution is the *normal* shape of a `run_now`, not a damaged pair. And a
    # window whose start falls between D107's two events clips the first of them,
    # which is the boundary case a view that promises to omit nothing may not lose.
    #
    # Placing it needs no guesswork: `execution_payload` repeats `schedule_id`,
    # `entity_id`, `rule_id`, `kind` and `at`, deliberately, "rather than expecting
    # a reader to join on the context id". `as_dict`'s `announced` flag is what
    # tells a renderer which of the two it is looking at.
    for context_id, row in executions.items():
        at = _parse(row.data.get(ATTR_AT))
        if at is None:
            continue
        entries.append(
            PastOccurrence(
                at=at,
                recorded_at=row.recorded_at,
                context_id=context_id,
                occurrence={},
                execution=row.data,
            )
        )

    entries.sort(key=lambda entry: absolute(entry.at))
    return tuple(entries)


def _parse(value: Any) -> datetime | None:
    """Read an instant back out of a stored payload.

    `datetime.fromisoformat` rather than a lenient parser: these strings were
    written by `events.py` calling `isoformat()`, so anything it rejects was not
    written by this integration, and guessing at it would be inventing a row.
    """
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


__all__ = [
    "Coverage",
    "PastOccurrence",
    "ScheduleTimeline",
    "Timeline",
    "async_past_occurrences",
    "async_timeline",
    "coverage_of",
]
