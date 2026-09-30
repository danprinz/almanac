"""D48-D52 — what almanac says about itself, and the surfaces it says it on.

D48 settles the storage question by refusing it: there is no private audit store,
because Home Assistant already has one. The recorder subscribes `MATCH_ALL`
(`components/recorder/core.py`, `async_listen(MATCH_ALL, _event_listener)`), so a
custom event is persisted by default — there is no allow-list to join and no
logbook registration gate to pass. The accepted consequence is that retention is
the user's: `purge_keep_days` is global, and an event carrying a top-level
`entity_id` is additionally subject to their recorder entity filter (A.4).

Three things below are load-bearing and all three were verified against the
installed 2026.9.4 package rather than recalled.

**The event has to be fired *before* the side effects.** This is the one place
where step 5's note in `tick.py` was wrong: it said the D49 event would adopt the
transition's context *as its parent*, so that the service calls read as caused by
the event. The logbook does not work that way.

- `components/logbook/queries/entities.py::entities_stmt` ends `.order_by(
  Events.time_fired_ts)`, and `components/logbook/processor.py::_humanify`
  memoises `context_lookup[context_id_bin] = row` for the **first** row carrying
  each context id. Whatever happens first with a given context *is* the cause, as
  far as every later row with that context is concerned.
- `ContextAugmenter.augment` then describes that row, and only reaches
  `CONTEXT_NAME` / `CONTEXT_MESSAGE` / `CONTEXT_DOMAIN` when its event type is in
  `external_events` — i.e. when some integration's `logbook.py` described it.
  That is exactly what D51 buys, and it is why the platform is not optional
  decoration: without it the row is memoised and then ignored.
- So: event first, and the light's row reads "turned on — triggered by Shabbat
  Lights". Event last, and the earliest row with that context is the
  `call_service` row, which `augment` describes as `CONTEXT_DOMAIN: light`,
  `CONTEXT_SERVICE: turn_on` — the light ends up attributed to the service call
  that changed it, which is exactly the uninformative line this integration
  exists to replace.

**The event and the service calls must share one `Context`, not a parent/child
pair.** Core's own model is `components/automation/__init__.py`: one
`trigger_context`, passed both to `async_fire_internal(EVENT_AUTOMATION_TRIGGERED,
..., context=trigger_context)` and to `action_script.async_run(variables,
trigger_context, ...)`. A child context fails for a concrete, checkable reason: a
*target* entity's logbook page builds its context-id set from that entity's own
States rows plus events whose JSON `entity_id` matches it (`ENTITY_ID_IN_EVENT =
EVENT_DATA_JSON["entity_id"]` in `components/recorder/db_schema.py`), so a parent
event carrying the *schedule's* entity_id is never in the result set and the
parent walk in `_humanify` finds nothing to walk to.

**Two event types, and D49 asks for one.** D49's payload is "result (fired /
skipped / dropped / failed) ... and per-action results", and every one of those
is knowable only *after* the actions have run. D50's rendering, per the above,
needs the event *before* them. Both cannot be one event, and this is the gap the
design does not settle. The reading implemented here:

- `almanac_occurrence` fires before the side effects, with the transition's
  `Context`, carrying everything the *decision* consists of — which is also
  everything D49 asks for that is not an execution outcome, including D24's
  blocking-condition label, which is the payload field the brief most wanted.
  This is the event D51 describes, so it is the one logbook row per occurrence
  and the one causation anchor.
- `almanac_execution` fires after, with the same `Context`, carrying `result` and
  the per-action results (D32). It is deliberately **not** described to the
  logbook: `_humanify` skips an event type absent from `external_events`
  (`else: continue`), so it is recorded and queryable without adding a second
  narrative row per occurrence for a schedule that runs hourly.

The alternatives rejected: firing a single event *after* everything, which keeps
D49's letter and loses D50's stated purpose (verified above, not assumed); and
dropping the per-action results so that one event can fire first, which would
leave a failed or *dropped* action — D31's whole point, the failure no existing
scheduler reports — recorded nowhere durable at all. This needs an owner ruling
on D49's wording; nothing else about the shape changes if the ruling goes the
other way.

**`ScheduleStatus` is not an audit store.** It is D52's display cache: the last
few executions, in memory, per schedule, fed to the switch through
`_unrecorded_attributes` so that a churning attribute stays live in the UI and
out of the database. It also carries D55's `next_at`, because both are the same
thing — a value the tick computed and the entities render — and because one
notification path is one thing to get right rather than two. Nothing here is
persisted; the durable copies are the recorder's (D48) and the runtime store's
(D35).

**Nothing here reads a clock (D64).** Every instant in a payload came off the
`Transition` the engine produced, which took `now` as an argument.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from homeassistant.const import ATTR_ENTITY_ID, ATTR_NAME
from homeassistant.core import Context, HomeAssistant, callback

from .actions import ActionStatus, ExecutionReport
from .const import (
    ATTR_ACTIONS,
    ATTR_AT,
    ATTR_BLOCKING,
    ATTR_CAUSE,
    ATTR_KIND,
    ATTR_LATENESS,
    ATTR_RESULT,
    ATTR_RULE_ID,
    ATTR_SCHEDULE_ID,
    CONF_NAME,
    CONF_OBJECT_ID,
    EVENT_EXECUTION,
    EVENT_OCCURRENCE,
    EXECUTION_CACHE_SIZE,
    RESULT_DROPPED,
    RESULT_FAILED,
    RESULT_FIRED,
    RESULT_NOTHING,
    SCHEDULE_PLATFORM,
)
from .engine.transition import Transition
from .resolver.contract import absolute

# --- payloads --------------------------------------------------------------


@callback
def schedule_entity_id(schedule: Mapping[str, Any]) -> str:
    """The entity_id D49's event points at — D54's switch.

    Built from the stored `object_id` rather than looked up in the entity
    registry, for the same reason `entity.py` builds it that way: D66 fixes it at
    creation, so the string is a fact about the stored schedule and not about
    what happens to exist right now. A registry lookup would also return `None`
    during the recovery pass for a schedule whose entities are still being added,
    and an event with no `entity_id` is one the logbook cannot attach to
    anything.

    Carrying it at the top level of the payload is what makes the attachment
    work: `components/recorder/db_schema.py` matches events to an entity with
    `ENTITY_ID_IN_EVENT = EVENT_DATA_JSON["entity_id"]`, so the key has to be
    exactly `entity_id` and exactly at the top. A.4's consequence is accepted
    along with it — an event with an `entity_id` passes through the user's
    recorder entity filter, and one without would not.
    """
    return f"{SCHEDULE_PLATFORM}.{schedule[CONF_OBJECT_ID]}"


@callback
def occurrence_payload(
    schedule: Mapping[str, Any], transition: Transition
) -> dict[str, Any]:
    """D49's event data for one occurrence, as of the moment it was decided.

    `at` and `lateness` are both present because `Transition` keeps them both, and
    for the reason written there: "the 17:00 occurrence, handled at 17:04" is the
    line that makes an incident take a minute instead of an hour, and a payload
    naming only one of the two instants cannot produce it.

    `blocking` is D24's labels, verbatim as the user wrote them. It is the reason
    this event exists rather than a log line: "skipped because *cleaning crew
    present*" is a sentence the recorder can be asked for six weeks later.
    """
    return {
        ATTR_SCHEDULE_ID: transition.schedule_id,
        ATTR_NAME: schedule.get(CONF_NAME),
        # Top level, spelled exactly this way — see `schedule_entity_id`.
        ATTR_ENTITY_ID: schedule_entity_id(schedule),
        ATTR_RULE_ID: transition.rule_id,
        # D49's "result" for the two kinds that have already finished when this
        # fires: a missed occurrence and a skipped one both execute nothing, so
        # the decision *is* the outcome and there is no execution event to come.
        ATTR_KIND: str(transition.kind),
        ATTR_AT: transition.at.isoformat(),
        ATTR_LATENESS: transition.lateness.total_seconds(),
        ATTR_BLOCKING: (
            list(transition.conditions.blocking)
            if transition.conditions is not None
            else []
        ),
        ATTR_CAUSE: str(transition.cause) if transition.cause is not None else None,
    }


@callback
def execution_result(report: ExecutionReport | None) -> str | None:
    """D49's `result`, reduced from the per-action results.

    Ordered worst-first on purpose. A rule whose lights came on and whose
    announcement was swallowed by a `mode: single` script that was already
    running has *not* fired cleanly, and D31's entire argument is that reporting
    it as fired is the defect. So any failure makes the occurrence failed, any
    drop makes it dropped, and only an occurrence where everything did what it
    said is `fired`.
    """
    if report is None:
        return None
    if not report.attempted:
        return RESULT_NOTHING
    statuses = {result.status for result in report.results}
    if ActionStatus.FAILED in statuses:
        return RESULT_FAILED
    if ActionStatus.DROPPED in statuses:
        return RESULT_DROPPED
    return RESULT_FIRED


@callback
def execution_payload(
    schedule: Mapping[str, Any],
    transition: Transition,
    report: ExecutionReport,
) -> dict[str, Any]:
    """What the occurrence actually did — D49's per-action results (D32).

    Repeats the four identifiers rather than expecting a reader to join on the
    context id. The recorder is a table of events, and a query for "everything
    this schedule did in October" should not have to be a self-join to be
    answerable; that is the whole argument D48 makes for using the recorder at
    all.
    """
    return {
        ATTR_SCHEDULE_ID: transition.schedule_id,
        ATTR_ENTITY_ID: schedule_entity_id(schedule),
        ATTR_RULE_ID: transition.rule_id,
        ATTR_KIND: str(transition.kind),
        ATTR_AT: transition.at.isoformat(),
        ATTR_RESULT: execution_result(report),
        **report.as_dict(),
    }


@callback
def cache_entry(
    transition: Transition, report: ExecutionReport | None
) -> dict[str, Any]:
    """One line of D52's display cache.

    Flattens `ExecutionReport`'s two lists into one, which is the opposite of what
    the report itself does and is right for the opposite reason: the report keeps
    them apart so that D32's "actions never block state" stays answerable, and a
    person reading a more-info dialog wants to know what happened in the order it
    happened.

    Skipped and missed occurrences are recorded here too, with a `result` of
    `None`. "Why did nothing happen at 17:00" is the question this cache is most
    often opened to answer, and a cache that only lists the times it did work
    cannot answer it.
    """
    return {
        ATTR_AT: transition.at.isoformat(),
        ATTR_KIND: str(transition.kind),
        ATTR_RULE_ID: transition.rule_id,
        ATTR_RESULT: execution_result(report),
        ATTR_BLOCKING: (
            list(transition.conditions.blocking)
            if transition.conditions is not None
            else []
        ),
        ATTR_ACTIONS: (
            [result.as_dict() for result in report.results]
            if report is not None
            else []
        ),
    }


# --- firing ----------------------------------------------------------------


@callback
def async_fire_occurrence(
    hass: HomeAssistant,
    schedule: Mapping[str, Any],
    transition: Transition,
    *,
    context: Context,
) -> None:
    """Announce an occurrence **before** anything is done about it (D49, D50).

    The order is the decision — see the module docstring. Called for every
    transition, including the ones with no side effects, because D49's unit is the
    occurrence and not the service call: a skipped occurrence is the one this is
    most valuable for.
    """
    hass.bus.async_fire(
        EVENT_OCCURRENCE, occurrence_payload(schedule, transition), context=context
    )


@callback
def async_fire_execution(
    hass: HomeAssistant,
    schedule: Mapping[str, Any],
    transition: Transition,
    report: ExecutionReport,
    *,
    context: Context,
) -> None:
    """Record what the occurrence did, once it has done it (D49's results, D48)."""
    hass.bus.async_fire(
        EVENT_EXECUTION,
        execution_payload(schedule, transition, report),
        context=context,
    )


# --- the live read-out -----------------------------------------------------


@dataclass(slots=True)
class _ScheduleView:
    """Everything the engine has last told the entities about one schedule."""

    next_at: datetime | None = None
    recent: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=EXECUTION_CACHE_SIZE)
    )
    listeners: list[Callable[[], None]] = field(default_factory=list)


class ScheduleStatus:
    """D52's display cache, and D55's next-trigger value, in memory.

    In memory and not in the runtime store, and that is D48 rather than
    convenience: a persisted last-N list would be a private audit store, which is
    the thing D48 refuses. This survives nothing, and is not meant to — the
    durable copies are the recorder's events and, for `next_at`, the runtime
    record the tick writes on every pass.

    The listener list is what turns a value the tick computed into a state write.
    It is per schedule rather than global because a house with forty schedules
    ticking once a minute would otherwise re-render eighty entities to change one.
    """

    def __init__(self) -> None:
        """Set up an empty read-out."""
        self._views: dict[str, _ScheduleView] = {}

    def _view(self, schedule_id: str) -> _ScheduleView:
        return self._views.setdefault(schedule_id, _ScheduleView())

    @callback
    def async_add_listener(
        self, schedule_id: str, listener: Callable[[], None]
    ) -> Callable[[], None]:
        """Call `listener` whenever this schedule's read-out changes.

        Returns the unsubscribe callable, so an entity can hand it straight to
        `async_on_remove` and a removed entity cannot be written to.
        """
        listeners = self._view(schedule_id).listeners
        listeners.append(listener)

        @callback
        def _unsubscribe() -> None:
            if listener in listeners:
                listeners.remove(listener)

        return _unsubscribe

    @callback
    def _async_notify(self, view: _ScheduleView) -> None:
        # Iterated over a copy: a listener is free to unsubscribe itself, which is
        # exactly what an entity being removed mid-tick does.
        for listener in list(view.listeners):
            listener()

    @callback
    def async_record(
        self, schedule_id: str, transition: Transition, report: ExecutionReport | None
    ) -> None:
        """Add one occurrence to the front of the display cache (D52)."""
        view = self._view(schedule_id)
        # Newest first, because the cache is read top-down in a more-info dialog
        # and `deque(maxlen=...)` then discards the oldest from the far end.
        view.recent.appendleft(cache_entry(transition, report))
        self._async_notify(view)

    @callback
    def async_set_next_at(self, schedule_id: str, next_at: datetime | None) -> None:
        """Publish D55's next trigger, as computed by the tick at a written-down
        instant.

        D64 is why this is a setter and not a property that works it out: the
        sensor cannot ask what time it is, so the engine tells it. Unchanged
        values are dropped rather than re-notified — the tick recomputes this on
        every pass, and a state write per schedule per minute for a value that did
        not move is noise in the very recorder D48 relies on.

        A.13 — "unchanged" is an *instant* comparison. Two aware datetimes sharing
        a `ZoneInfo` object compare by wall clock, so the two halves of a DST fold
        are `==` to each other; comparing them directly would suppress the one
        state write in the year that most needs making.
        """
        view = self._view(schedule_id)
        current = view.next_at
        if current is None and next_at is None:
            return
        if (
            current is not None
            and next_at is not None
            and absolute(current) == absolute(next_at)
        ):
            return
        view.next_at = next_at
        self._async_notify(view)

    @callback
    def async_next_at(self, schedule_id: str) -> datetime | None:
        """The next instant this schedule does something, or `None` if unknown.

        `None` is D13's *unknown* and is rendered as such rather than guessed:
        beyond D44's budget the honest answer is that it has not been computed.
        """
        return self._views[schedule_id].next_at if schedule_id in self._views else None

    @callback
    def async_recent(self, schedule_id: str) -> list[dict[str, Any]]:
        """The last few occurrences, newest first — D52's attribute value."""
        if (view := self._views.get(schedule_id)) is None:
            return []
        return [dict(entry) for entry in view.recent]

    @callback
    def async_discard(self, schedule_id: str) -> None:
        """Forget a schedule that no longer exists."""
        self._views.pop(schedule_id, None)
