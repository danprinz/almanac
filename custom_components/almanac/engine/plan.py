"""Enumerating a schedule: D2's two shapes, D38's pairing, D39's bound, D44's budget.

This is the function the product's headline claim rests on. "What will happen
between now and Friday night" is `async_enumerate` called with a window, and
because it takes the window as a parameter rather than reading a clock (D64), the
timeline, the dry run and the live engine are the same call at three different
instants. There is nothing to keep in step, which is the only way a timeline can
be trusted.

Three things here are less obvious than they look.

**The search over start dates is wider than the window.** D1 says recurrence
picks the dates on which rules *begin*, and says nothing about where they end, so
a rule that began before the window can still be running inside it — that is the
entire point of the decision, and the reason 23:00 → 02:00 needs no special case.
The padding is computed rather than guessed: the longest interval the schedule can
hold, plus the largest offset in either direction, because an offset is allowed to
carry a resolved instant across midnight (D7) and a search that ignored that would
lose the occurrence it moved.

**D38's pairing is a forward search, bounded by D39.** An interval's end anchor
resolves to its first occurrence at or after the resolved start — so candle
lighting on Friday pairs with havdalah on Saturday with no calendar-day rule, both
edges drift seasonally on their own, and inversion is not a case to detect because
it cannot be constructed. The search stops one recurrence period past the start,
because an end found later would describe an interval D39 forbids; that is why
there is no "how many days ahead do we look" constant here.

**Two horizons, not one.** §10.6 is explicit that our compute budget (D44) and a
source's own honesty (D13) are different facts, so the plan reports both.
`computed_through` is how far we were willing to enumerate; `known_through` is how
far the anchors claim to see. The timeline needs them apart: *not computed* is a
promise that asking again with a wider window would answer, and *unknown* is a
promise that it would not.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any

from homeassistant.const import CONF_ID

from ..const import (
    CONF_ANCHOR,
    CONF_DATE_WINDOW,
    CONF_DURATION,
    CONF_ENABLED,
    CONF_END,
    CONF_FROM,
    CONF_KIND,
    CONF_OFFSET,
    CONF_RECURRENCE,
    CONF_RULES,
    CONF_START_ANCHOR,
    CONF_UNTIL,
    END_DURATION,
    ENUMERATION_HORIZON_DAYS,
    RULE_AT,
    RULE_DURING,
)
from ..resolver import (
    Horizon,
    ResolverRegistry,
    Span,
    Unresolved,
    UnresolvedReason,
    Window,
    anchor_horizon,
    async_resolve_anchor_on_date,
    known_through as horizon_known_through,
)
from .occurrence import Occurrence, OccurrenceStatus, absolute
from .recurrence import recurrence_period, start_dates

_LOGGER = logging.getLogger(__name__)

_BUDGET = timedelta(days=ENUMERATION_HORIZON_DAYS)
_NO_TIME = timedelta()
# A sort placeholder for an occurrence with no resolved instant. It is never
# compared against a real one, because the tuple's first element separates them.
_NO_INSTANT = datetime.min.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Plan:
    """Everything a schedule will do in a window, including what it cannot do.

    A plan is a value and a complete answer: a caller never has to ask a second
    question to find out whether the silence in a stretch of the window means
    *nothing is scheduled*, *we did not compute that far* or *the source cannot
    see that far*. Those three are `occurrences`, `computed_through` and
    `known_through` respectively, and §10.6 insists on the distinction because a
    timeline that conflates them is lying in the situation where being wrong is
    most visible.
    """

    schedule_id: str
    window: Window
    occurrences: tuple[Occurrence, ...]
    computed_through: datetime
    known_through: datetime
    problem: Unresolved | None = None

    @property
    def fully_computed(self) -> bool:
        """Whether D44's budget covered the whole window that was asked for."""
        return self.computed_through >= self.window.end

    @property
    def fully_known(self) -> bool:
        """Whether every anchor's declared horizon covers the window (D13)."""
        return self.known_through >= self.window.end

    def runnable(self) -> tuple[Occurrence, ...]:
        """The occurrences that are expected to do something."""
        return tuple(occ for occ in self.occurrences if occ.will_run)


async def async_enumerate(
    registry: ResolverRegistry, schedule: dict[str, Any], window: Window
) -> Plan:
    """Enumerate one schedule over one window. The whole engine, as a value.

    Pure in `(schedule, window, currently-known entity state)`. It resolves
    anchors, so it is `async` and it can see the state machine; it does not read
    the clock, mutate anything, or fire anything. What to *do* about the result is
    `transition.py`'s question, and keeping the two apart is what lets the dry run
    use this one unchanged.
    """
    zone = registry.zone
    schedule_id = schedule.get(CONF_ID, "")
    computed_through = min(window.end, window.start + _BUDGET)
    computed = Window(window.start, computed_through)

    recurrence = schedule[CONF_RECURRENCE]
    period = recurrence_period(recurrence)
    rules: list[dict[str, Any]] = list(schedule.get(CONF_RULES, []))

    span = _search_range(schedule, rules, computed, zone, period)
    if span is None:
        # The date window (§2) closes before the requested window opens. Not a
        # failure and not an unknown: the schedule provably does nothing here.
        return Plan(schedule_id, window, (), computed_through, computed_through)

    first, last = span
    dates = start_dates(recurrence, first, last)
    if isinstance(dates, Unresolved):
        # The recurrence itself could not be evaluated, so no rule has a date to
        # begin on. Reported once, on the plan, rather than copied onto an
        # occurrence per rule: the fault is the schedule's, not any rule's.
        return Plan(
            schedule_id, window, (), computed_through, window.start, problem=dates
        )

    armed = bool(schedule.get(CONF_ENABLED, True))
    horizons = _Horizons(computed, zone)
    occurrences: list[Occurrence] = []

    for rule in rules:
        occurrences.extend(
            await _async_enumerate_rule(
                registry,
                schedule_id=schedule_id,
                rule=rule,
                dates=dates,
                armed=armed and bool(rule.get(CONF_ENABLED, True)),
                period=period,
                zone=zone,
                horizons=horizons,
            )
        )

    kept = sorted(
        (occ for occ in occurrences if occ.overlaps(computed, zone)),
        key=lambda occ: (absolute(_when(occ, zone)), occ.rule_id),
    )
    return Plan(
        schedule_id=schedule_id,
        window=window,
        occurrences=tuple(kept),
        computed_through=computed_through,
        known_through=horizons.known_through(),
    )


def interval_problems(schedule: dict[str, Any]) -> list[str]:
    """D39 — an interval longer than its recurrence period, rejected at save.

    *Why rejected rather than coalesced:* silent coalescing produces a timeline
    the user cannot predict, and predictability is the product. Two intervals of
    one rule overlapping has no defined meaning — the second entry would either
    be dropped, or re-enter a rule already held, and either way what fires stops
    being readable off the schedule.

    Only a **duration** end can be checked here, and that is not a shortfall
    being tolerated: an interval ending on a second anchor has no length until
    both anchors resolve, which needs a date and the resolver registry, and
    neither exists at save. That half is enforced where it becomes knowable — the
    enumerator bounds D38's forward search by this same period, so an end that
    would overrun simply never pairs, and the occurrence renders as unresolved
    (D12) instead of silently running long.
    """
    period = recurrence_period(schedule[CONF_RECURRENCE])
    if period is None:
        return []

    problems: list[str] = []
    for index, rule in enumerate(schedule.get(CONF_RULES, [])):
        if rule.get(CONF_KIND) != RULE_DURING:
            continue
        end = rule.get(CONF_END, {})
        if end.get(CONF_KIND) != END_DURATION:
            continue
        if (length := timedelta(seconds=end[CONF_DURATION])) > period:
            problems.append(
                f"rule {index + 1} lasts {length} but its recurrence repeats "
                f"every {period}, so occurrences would overlap (D39)"
            )
    return problems


async def _async_enumerate_rule(
    registry: ResolverRegistry,
    *,
    schedule_id: str,
    rule: dict[str, Any],
    dates: list[date],
    armed: bool,
    period: timedelta | None,
    zone: tzinfo,
    horizons: _Horizons,
) -> list[Occurrence]:
    """One rule's occurrences, one per recurrence date that resolves."""
    rule_id = rule.get(CONF_ID) or ""
    kind = rule[CONF_KIND]
    if kind == RULE_AT:
        start_anchor = rule[CONF_ANCHOR]
    elif kind == RULE_DURING:
        start_anchor = rule[CONF_START_ANCHOR]
    else:
        return []

    horizons.declare(rule_id, "start", registry, start_anchor)
    if kind == RULE_DURING and (end := rule[CONF_END])[CONF_KIND] != END_DURATION:
        horizons.declare(rule_id, "end", registry, end[CONF_ANCHOR])

    out: list[Occurrence] = []
    for day in dates:
        resolved = await async_resolve_anchor_on_date(registry, start_anchor, day)
        if isinstance(resolved, Unresolved):
            out.append(
                Occurrence(
                    schedule_id=schedule_id,
                    rule_id=rule_id,
                    kind=kind,
                    start_date=day,
                    status=OccurrenceStatus.UNRESOLVED,
                    armed=armed,
                    problem=resolved,
                )
            )
            continue
        if resolved is None:
            # A known nothing: the anchor has no occurrence on this date, as at a
            # polar midsummer. Distinct from unresolved, and correctly invisible —
            # there is nothing the user could fix.
            continue
        horizons.note(rule_id, "start", resolved)

        if kind == RULE_AT:
            out.append(
                Occurrence(
                    schedule_id=schedule_id,
                    rule_id=rule_id,
                    kind=kind,
                    start_date=day,
                    status=OccurrenceStatus.SCHEDULED,
                    armed=armed,
                    start=resolved,
                )
            )
            continue

        end_instant = await _async_pair_end(
            registry, rule[CONF_END], resolved, period, zone, horizons, rule_id
        )
        if isinstance(end_instant, Unresolved):
            out.append(
                Occurrence(
                    schedule_id=schedule_id,
                    rule_id=rule_id,
                    kind=kind,
                    start_date=day,
                    status=OccurrenceStatus.UNRESOLVED,
                    armed=armed,
                    start=resolved,
                    problem=end_instant,
                )
            )
            continue
        out.append(
            Occurrence(
                schedule_id=schedule_id,
                rule_id=rule_id,
                kind=kind,
                start_date=day,
                status=OccurrenceStatus.SCHEDULED,
                armed=armed,
                start=resolved,
                end=end_instant,
            )
        )

    return _mark_overlaps(out)


async def _async_pair_end(
    registry: ResolverRegistry,
    end: dict[str, Any],
    start: datetime,
    period: timedelta | None,
    zone: tzinfo,
    horizons: _Horizons,
    rule_id: str,
) -> datetime | Unresolved:
    """D38 — the end of this interval, given its resolved start."""
    if end[CONF_KIND] == END_DURATION:
        # Absolute, not wall-clock. A duration is a length of elapsed time, so
        # "for four hours" is four hours on both DST nights of the year; wall-clock
        # addition would make it three hours in March and five in November while
        # still reading as four in the editor. This is the same arithmetic, and
        # the same reasoning, as an offset (`anchor._shift`), and it is the
        # counterpart of D40's rule that only *clock anchors* are wall-clock.
        return (
            start.astimezone(UTC) + timedelta(seconds=end[CONF_DURATION])
        ).astimezone(start.tzinfo)

    anchor = end[CONF_ANCHOR]
    # D39 bounds the search: an interval may not outlast its recurrence period, so
    # an end anchor that has not occurred within one period of the start cannot
    # produce a valid interval and there is nothing to be gained by looking
    # further. Where no period can be stated — a single listed date — D44's
    # compute budget is the only honest bound left.
    limit = start + (period if period is not None else _BUDGET)
    day = start.astimezone(zone).date()
    last_day = limit.astimezone(zone).date()

    while day <= last_day:
        resolved = await async_resolve_anchor_on_date(registry, anchor, day)
        if isinstance(resolved, Unresolved):
            # Stop rather than skip to the next day. A day whose answer is unknown
            # might hold an *earlier* end than any later day, so accepting a later
            # one would silently lengthen the interval; D42 would rather the whole
            # occurrence arrive late, once the anchor recovers, than run wrong now.
            return resolved
        if resolved is not None:
            horizons.note(rule_id, "end", resolved)
            # "At or after" (D38), so an end exactly at the start is permitted. It
            # yields a zero-length interval, which `Occurrence.holds_at` never
            # activates — the same answer `Span.covers` gives a zero-length span,
            # because an instant has no interior.
            if absolute(start) <= absolute(resolved) <= absolute(limit):
                return resolved
        day += timedelta(days=1)

    return Unresolved(
        UnresolvedReason.NO_PAIRING,
        f"no occurrence of the end anchor between {start.isoformat()} and "
        f"{limit.isoformat()} (D38, bounded by D39)",
    )


def _mark_overlaps(occurrences: list[Occurrence]) -> list[Occurrence]:
    """D39's backstop: refuse, visibly, an occurrence a running one swallows.

    The save-time check catches every case it can see, and the pairing bound
    catches the rest by construction, so reaching this is evidence of a schedule
    written before the check existed or edited outside the UI. What it must not do
    is coalesce: the earlier interval keeps running, and the later occurrence is
    marked rather than deleted, so the timeline can show the user the collision
    they cannot otherwise see (D12).
    """
    marked: list[Occurrence] = []
    holding: Occurrence | None = None
    for occ in sorted(
        occurrences,
        key=lambda o: (
            o.start is None,
            absolute(o.start) if o.start is not None else _NO_INSTANT,
            o.start_date,
        ),
    ):
        if not occ.will_run or not occ.is_interval or occ.end is None:
            marked.append(occ)
            continue
        if holding is not None and holding.end is not None and occ.start is not None:
            if absolute(occ.start) < absolute(holding.end):
                _LOGGER.warning(
                    "Occurrence of rule %s on %s starts before the previous one "
                    "ends; refusing it rather than merging them (D39)",
                    occ.rule_id,
                    occ.start_date,
                )
                marked.append(
                    replace(occ, status=OccurrenceStatus.OVERLAPS_PREVIOUS)
                )
                continue
        holding = occ
        marked.append(occ)
    return marked


def _search_range(
    schedule: dict[str, Any],
    rules: list[dict[str, Any]],
    window: Window,
    zone: tzinfo,
    period: timedelta | None,
) -> tuple[date, date] | None:
    """The inclusive range of start dates worth evaluating for this window.

    Wider than the window at both ends, and for two separate reasons that would
    each be a lost occurrence if forgotten. Behind: an interval that began earlier
    may still be running (D1), and a positive offset can push a resolved instant
    onto a later date. Ahead: a negative offset can pull one onto an earlier date,
    so a start date past the window's end can still fire inside it — "forty-five
    minutes before candle lighting", evaluated on a window ending at midnight.

    `None` when §2's date window excludes the whole range.
    """
    hold = _NO_TIME
    forward = _NO_TIME
    backward = _NO_TIME
    for rule in rules:
        hold = max(hold, _longest_hold(rule, period))
        for anchor in _anchors(rule):
            offset = timedelta(seconds=int(anchor.get(CONF_OFFSET, 0)))
            forward = max(forward, offset)
            backward = max(backward, -offset)

    lookback = min(_whole_days(hold + forward), _BUDGET)
    lookahead = min(_whole_days(backward), _BUDGET)

    first = window.start.astimezone(zone).date() - lookback
    last = window.end.astimezone(zone).date() + lookahead

    date_window = schedule.get(CONF_DATE_WINDOW) or {}
    if (opens := date_window.get(CONF_FROM)) is not None:
        first = max(first, date.fromisoformat(opens))
    if (closes := date_window.get(CONF_UNTIL)) is not None:
        # Inclusive. `until: 2026-12-31` names a day the schedule still runs on;
        # reading it as exclusive would silently drop the last occurrence of every
        # bounded schedule, which is the class of off-by-one D88 exists to argue
        # about for *resolver spans* and which goes the other way for a date a
        # person typed.
        last = min(last, date.fromisoformat(closes))

    return None if first > last else (first, last)


def _longest_hold(rule: dict[str, Any], period: timedelta | None) -> timedelta:
    """The longest this rule's interval can last. Zero for an `At` rule."""
    if rule.get(CONF_KIND) != RULE_DURING:
        return _NO_TIME
    end = rule.get(CONF_END, {})
    if end.get(CONF_KIND) == END_DURATION:
        return timedelta(seconds=end[CONF_DURATION])
    # An anchor-ended interval has no length until it resolves, and D39 caps it at
    # one recurrence period — which is exactly the bound the pairing search uses,
    # so the lookback and the search can never disagree about how long an interval
    # is allowed to be.
    return period if period is not None else _BUDGET


def _anchors(rule: dict[str, Any]) -> list[dict[str, Any]]:
    """Every anchor a rule carries, whichever of D2's shapes it is."""
    found: list[dict[str, Any]] = []
    for key in (CONF_ANCHOR, CONF_START_ANCHOR):
        if isinstance(anchor := rule.get(key), dict):
            found.append(anchor)
    end = rule.get(CONF_END) or {}
    if isinstance(anchor := end.get(CONF_ANCHOR), dict):
        found.append(anchor)
    return found


class _Horizons:
    """D13's declarations, collected so the plan can report one `known_through`.

    Per anchor rather than per schedule, because the declarations differ: one rule
    anchored on `sensor.candle_lighting` (NEXT_ONLY) and another on sunset
    (UNBOUNDED) do not see equally far, and the plan can only claim the shorter.
    The instants are collected too because NEXT_ONLY's knowledge ends where its
    single value ends, which is a fact about the data and not about the
    declaration — `contract.known_through` is the one place that combines them, and
    it is reused here rather than reimplemented so a plan and a forecast cannot
    disagree about the same anchor.
    """

    def __init__(self, window: Window, zone: tzinfo) -> None:
        """Start with no declarations, which means nothing limits knowledge."""
        self._window = window
        self._zone = zone
        self._declared: dict[tuple[str, str], Horizon] = {}
        self._seen: dict[tuple[str, str], list[datetime]] = {}

    def declare(
        self,
        rule_id: str,
        slot: str,
        registry: ResolverRegistry,
        anchor: dict[str, Any],
    ) -> None:
        """Record what an anchor's source declared, read from the declaration.

        An anchor whose horizon cannot even be looked up is left out. It does not
        limit knowledge — it *is* an unresolved anchor, and the occurrences carry
        that as a problem of their own; folding it in here would report the whole
        window as unknown because of one broken rule.
        """
        if not isinstance(horizon := anchor_horizon(registry, anchor), Unresolved):
            self._declared[(rule_id, slot)] = horizon

    def note(self, rule_id: str, slot: str, instant: datetime) -> None:
        """Record an instant an anchor actually produced."""
        self._seen.setdefault((rule_id, slot), []).append(instant)

    def known_through(self) -> datetime:
        """The earliest instant past which some anchor stops claiming anything."""
        limits = [
            horizon_known_through(
                self._window,
                tuple(
                    Span(instant, instant)
                    for instant in self._seen.get(key, [])
                    if self._window.contains(instant)
                ),
                horizon,
                self._zone,
            )
            for key, horizon in self._declared.items()
        ]
        return min(limits, default=self._window.end)


def _when(occ: Occurrence, zone: tzinfo) -> datetime:
    """A sortable instant for an occurrence, including one that did not resolve.

    An unresolved occurrence still belongs somewhere in a list, and the only thing
    known about it is the date the recurrence picked — so it sorts at that date's
    midnight, which puts it among the day's occurrences rather than at the end of
    the timeline where nobody would look for it.
    """
    if occ.start is not None:
        return occ.start
    return datetime.combine(occ.start_date, time(), tzinfo=zone)


def _whole_days(delta: timedelta) -> timedelta:
    """Round a padding up to whole days, because start dates are dates."""
    return timedelta(days=-(-delta // timedelta(days=1)))


__all__ = ["Plan", "async_enumerate", "interval_problems"]
