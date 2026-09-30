"""D41 — deciding what to *do*, given a plan and what we were already holding.

`plan.py` answers "what does this schedule do in this window". This module answers
the harder question: given that answer and the record of what the engine is
already holding, what changes right now? It is the whole of restart recovery, and
it is a pure function of its arguments — `now` is a parameter (D64), the engine
state is passed in and handed back rather than mutated, and nothing here touches a
store, fires a service or subscribes to anything. The caller owns the clock and
the persistence; this module owns the decisions.

**The asymmetry is the point.** D41 refuses to give both rule shapes the same
recovery policy, because they are not equally idempotent. Restoring a light that
should be on is straightforwardly right, so a `During` rule reconciles
unconditionally. Replaying an announcement three hours late is worse than never
making it, so a missed `At` occurrence is logged and dropped unless its rule opted
into a grace window, which is off by default.

**One predicate carries that asymmetry, and D42's late arrivals with it.** An
occurrence was *observed* if the engine was alive and its instant fell after the
last evaluation. Everything else — a restart, a resolver that could not answer at
the time and can now, a schedule loaded for the first time — is an unobserved
instant, and unobserved instants go through the grace test. D42 asks explicitly
that a late arrival reuse D41's machinery rather than get a second policy, and a
single `observed` flag is the smallest shape that guarantees they cannot disagree.

**Two things are deliberately remembered.** `EngineState.held` is what the engine
must be able to exit even if the schedule that created it has since been edited
away — an interval must never outlive its own recorded end, because the failure
that matters is lights left on indefinitely (the concern D47 states for
termination). `EngineState.decided` is which `At` occurrences have already been
ruled on, which is what makes re-running the same evaluation twice produce nothing
the second time. Without it, widening the lookback to catch a late arrival would
re-fire everything inside the grace window on every tick.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any, Self

from homeassistant.const import CONF_ID

from ..const import (
    CONF_ENABLED,
    CONF_GRACE,
    CONF_KIND,
    CONF_RULES,
    RULE_AT,
)
from ..resolver import ResolverRegistry, Window
from .occurrence import Occurrence, OccurrenceStatus, absolute
from .plan import Plan, async_enumerate

_LOGGER = logging.getLogger(__name__)

_ZERO = timedelta()


class TransitionKind(StrEnum):
    """What the engine is being asked to do about one occurrence.

    `RESUME` is the one that is not obvious. D41 says a `During` rule on start
    should "evaluate *should this be active now?* and reconcile", and reconciling
    an interval the engine was already holding is not the same event as entering
    one: the desired state (D28) may need re-applying because the world changed
    while we were down, but the enter actions (D5) are a one-shot side effect that
    has already happened. Collapsing the two would make every restart during an
    interval re-announce it. **Provisional** — D41 says "reconcile" without
    naming the distinction; see the note in the module that owns the actions.
    """

    FIRE = "fire"
    MISSED = "missed"
    ENTER = "enter"
    RESUME = "resume"
    EXIT = "exit"


class ExitCause(StrEnum):
    """Why an interval is ending. Every value changes what the caller should do.

    D3 makes the exit path three-valued and explicit, and these are the reasons
    that path can be reached. They are kept apart because the log has to be able
    to say which one happened (D49): "the window ended" and "you turned it off"
    and "the rule no longer exists" are three different things to have to explain
    to somebody whose lights just changed.
    """

    WINDOW_END = "window_end"
    DISARMED = "disarmed"
    GONE = "gone"


# Exits are emitted before entries at the same instant, so that a caller applying
# a batch in order hands over rather than overlapping: the outgoing interval's
# `on_exit` runs before the incoming one's desired state, which is the only
# ordering under which back-to-back intervals do not fight over the same entity.
_ORDER: dict[TransitionKind, int] = {
    TransitionKind.EXIT: 0,
    TransitionKind.MISSED: 1,
    TransitionKind.FIRE: 2,
    TransitionKind.RESUME: 3,
    TransitionKind.ENTER: 4,
}


@dataclass(frozen=True, slots=True)
class HeldInterval:
    """A `During` occurrence the engine has entered and not yet exited.

    It carries its own `end` rather than looking one up, and that redundancy is
    load-bearing. The schedule can be edited, its anchor entity can go
    unavailable, its resolver can be uninstalled — and none of that may leave an
    interval running forever, because the thing this integration must never do is
    hold a state it created with no way back out. The recorded end is the promise
    the engine made when it entered, and it is honoured even when the schedule
    that produced it can no longer be evaluated.

    `start_date` is the recurrence date (D1), not `start.date()`, because that is
    the key an occurrence is identified by across evaluations — an offset may have
    carried `start` onto the following day (D7), and matching on the shifted date
    would fail to recognise the interval it is already holding.
    """

    schedule_id: str
    rule_id: str
    start_date: date
    start: datetime
    end: datetime
    entered_at: datetime

    @property
    def key(self) -> tuple[str, date]:
        """What identifies this occurrence across evaluations."""
        return (self.rule_id, self.start_date)

    def as_dict(self) -> dict[str, Any]:
        """Serialise for the runtime store (D35), ISO strings throughout."""
        return {
            "schedule_id": self.schedule_id,
            "rule_id": self.rule_id,
            "start_date": self.start_date.isoformat(),
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "entered_at": self.entered_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Rebuild from the runtime store.

        `fromisoformat` rather than a parsing helper: these bytes were written by
        `as_dict` in a previous run of this same class, so a lenient parser would
        only hide a migration that failed to happen (D37).
        """
        return cls(
            schedule_id=data["schedule_id"],
            rule_id=data["rule_id"],
            start_date=date.fromisoformat(data["start_date"]),
            start=datetime.fromisoformat(data["start"]),
            end=datetime.fromisoformat(data["end"]),
            entered_at=datetime.fromisoformat(data["entered_at"]),
        )


@dataclass(frozen=True, slots=True)
class AtRecord:
    """An `At` occurrence the engine has already ruled on, fired or missed.

    Missed ones are recorded too, and that is not an oversight: the point of the
    record is *idempotence*, not an audit trail (§12 owns the audit trail). An
    occurrence the engine decided to skip must stay skipped, or the next
    evaluation inside the same lookback would re-decide it and eventually announce
    it late — the precise outcome D41 chose the default to avoid.
    """

    rule_id: str
    start_date: date
    at: datetime

    @property
    def key(self) -> tuple[str, date]:
        """What identifies this occurrence across evaluations."""
        return (self.rule_id, self.start_date)

    def as_dict(self) -> dict[str, Any]:
        """Serialise for the runtime store (D35)."""
        return {
            "rule_id": self.rule_id,
            "start_date": self.start_date.isoformat(),
            "at": self.at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Rebuild from the runtime store."""
        return cls(
            rule_id=data["rule_id"],
            start_date=date.fromisoformat(data["start_date"]),
            at=datetime.fromisoformat(data["at"]),
        )


@dataclass(frozen=True, slots=True)
class EngineState:
    """One schedule's engine memory — everything a restart must not lose.

    Deliberately tiny, and deliberately *not* the schedule. D35 keeps runtime
    state in its own store because the definition is written by a human in an
    editor and this is written by the engine on a hot path; sharing a store means
    an edit saved at the wrong moment silently discards a held interval, and
    neither writer could be told.

    `evaluated_through` is how the engine knows whether it was watching. A caller
    persists the state it is handed and, on start, passes it to
    `async_plan_recovery`, which reads that instant as "everything after this
    happened while we were down". No tolerance, no heuristic and no guess about
    how long a tick is meant to take.
    """

    held: tuple[HeldInterval, ...] = ()
    decided: tuple[AtRecord, ...] = ()
    evaluated_through: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        """Serialise for the runtime store (D35)."""
        return {
            "held": [item.as_dict() for item in self.held],
            "decided": [item.as_dict() for item in self.decided],
            "evaluated_through": (
                None if self.evaluated_through is None
                else self.evaluated_through.isoformat()
            ),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Rebuild from the runtime store, tolerating an absent key.

        Absent keys are tolerated because a *forward* migration adds them: a state
        written before a field existed is legitimately missing it. Malformed
        values are not tolerated — see `HeldInterval.from_dict`.
        """
        through = data.get("evaluated_through")
        return cls(
            held=tuple(HeldInterval.from_dict(item) for item in data.get("held", [])),
            decided=tuple(AtRecord.from_dict(item) for item in data.get("decided", [])),
            evaluated_through=None if through is None else datetime.fromisoformat(through),
        )


@dataclass(frozen=True, slots=True)
class Transition:
    """One thing to do, and everything needed to explain it afterwards.

    `at` is when it *should* have happened and `lateness` is how far behind that
    the engine is acting. Both are recorded rather than just the second, because
    §12's log has to be able to say "the 17:00 occurrence, handled at 17:04" — a
    line that names only one of the two instants is the kind of log entry that
    makes an incident take an hour instead of a minute.
    """

    kind: TransitionKind
    schedule_id: str
    rule_id: str
    start_date: date
    at: datetime
    lateness: timedelta = _ZERO
    occurrence: Occurrence | None = None
    held: HeldInterval | None = None
    cause: ExitCause | None = None

    @property
    def key(self) -> tuple[str, date]:
        """What identifies the occurrence this transition is about."""
        return (self.rule_id, self.start_date)


@dataclass(frozen=True, slots=True)
class Reconciliation:
    """The complete result of one evaluation: what to do, and what to remember.

    The new state is returned rather than derived by the caller from the
    transitions. Deriving it would be a second implementation of the same
    reasoning, living in the component that has the side effects — which is how
    the held set and the world end up disagreeing about what is running.
    """

    plan: Plan
    transitions: tuple[Transition, ...] = ()
    state: EngineState = field(default_factory=EngineState)
    next_at: datetime | None = None


async def async_plan_tick(
    registry: ResolverRegistry,
    schedule: dict[str, Any],
    state: EngineState,
    *,
    now: datetime,
) -> Reconciliation:
    """Evaluate a schedule as a running engine: we were watching since last time.

    Everything whose instant falls after `state.evaluated_through` was observed as
    it happened, so an `At` occurrence in that stretch simply fires. An occurrence
    *older* than that which the engine has not ruled on before is a late arrival —
    D42's recovered anchor — and goes through the grace test like any other
    unobserved instant.
    """
    return await _async_reconcile(registry, schedule, state, now=now, live=True)


async def async_plan_recovery(
    registry: ResolverRegistry,
    schedule: dict[str, Any],
    state: EngineState,
    *,
    now: datetime,
) -> Reconciliation:
    """Evaluate a schedule after a restart: nothing since last time was observed.

    The only difference from a tick, and it is exactly D41: no instant is treated
    as observed, so every `At` occurrence that passed while we were down is missed
    unless its rule opted into a grace window, while every `During` interval that
    is in force right now is reconciled regardless of how long we were away.

    A schedule with no `evaluated_through` — newly created, or first seen after an
    upgrade — has no unobserved stretch at all, so it neither fires nor misses
    anything retroactively. Its intervals still reconcile, because "is this
    interval in force now" never depended on history.
    """
    return await _async_reconcile(registry, schedule, state, now=now, live=False)


def next_transition_at(
    plan: Plan, state: EngineState, now: datetime
) -> datetime | None:
    """The next instant this schedule has something to do, or `None`.

    D55's sensor renders this, and a tick scheduler wants it too. Pure, and it
    reads `now` from a parameter (D64) — which is what lets the timeline ask "what
    is next, as of Friday evening" and get the same answer the engine will.

    Held ends are included even when the occurrence no longer enumerates, for the
    same reason `HeldInterval` carries its end: the exit is owed regardless.
    """
    moment = absolute(now)
    candidates = [held.end for held in state.held if absolute(held.end) > moment]
    for occ in plan.occurrences:
        if not occ.will_run or occ.start is None:
            continue
        if absolute(occ.start) > moment:
            candidates.append(occ.start)
        if occ.end is not None and absolute(occ.end) > moment:
            candidates.append(occ.end)
    return min(candidates, key=absolute, default=None)


async def _async_reconcile(
    registry: ResolverRegistry,
    schedule: dict[str, Any],
    state: EngineState,
    *,
    now: datetime,
    live: bool,
) -> Reconciliation:
    """The one implementation both entry points share.

    Split into two public names rather than exposing the `live` flag, because the
    caller is the only component that knows whether it was alive — and a boolean
    argument at a call site is where that knowledge goes to be forgotten. D41's
    asymmetry deserves to be visible in the function name.
    """
    schedule_id = schedule.get(CONF_ID, "")
    first_ever = state.evaluated_through is None
    since = state.evaluated_through or now
    # The lookback has to reach back past `since`, or a late arrival could never
    # be seen: D42's whole case is an anchor that could not answer at the time and
    # can answer now, and by then its instant is already behind us. How far back
    # is not a choice — it is the longest grace any rule opted into, because
    # beyond that the answer is "missed" whether we look or not.
    #
    # A schedule with no `evaluated_through` gets no lookback at all, grace window
    # or not. It was created a moment ago, or its runtime state was lost, and in
    # either reading there is no history to recover: an occurrence from before the
    # schedule existed did not happen, and firing it because the rule happens to
    # carry a twelve-hour grace would be the engine inventing a past.
    horizon = now if first_ever else min(since, now - _longest_grace(schedule))
    window = Window(min(horizon, now), now + timedelta.resolution)

    plan = await async_enumerate(registry, schedule, window)
    rules = {
        rule.get(CONF_ID) or "": rule for rule in schedule.get(CONF_RULES, [])
    }

    transitions: list[Transition] = []
    decided = [
        record for record in state.decided
        if absolute(record.at) >= absolute(window.start)
    ]
    already = {record.key for record in decided}

    for occ in plan.occurrences:
        if occ.is_interval:
            continue
        if occ.start is None:
            # D42 — skipped and logged, subscription kept live. Deliberately *not*
            # recorded as decided and deliberately not a `MISSED` transition: the
            # engine does not know when this occurrence should have happened, and
            # `Transition.at` would have to be invented to say so. A fabricated
            # instant in the audit log (§12) is worse than a plan that shows the
            # occurrence as unresolved, which D12 already requires it to. Leaving
            # it undecided is also what lets D41's grace window pick it up if the
            # anchor recovers in time.
            _LOGGER.debug(
                "Rule %s on %s cannot be placed yet: %s",
                occ.rule_id,
                occ.start_date,
                occ.problem,
            )
            continue
        if absolute(occ.start) > absolute(now):
            continue
        if occ.key in already:
            continue
        # Recorded whatever is decided below, including a disarmed rule's silence:
        # D77's disarmed occurrence did not happen, and re-enabling the rule an
        # hour later must not make it happen retroactively.
        decided.append(AtRecord(occ.rule_id, occ.start_date, occ.start))
        already.add(occ.key)
        if not occ.will_run:
            continue
        if transition := _decide_at(occ, rules.get(occ.rule_id), since, now, live):
            transitions.append(transition)

    held, interval_transitions = _reconcile_intervals(
        plan, state, rules, schedule_id=schedule_id, now=now, live=live
    )
    transitions.extend(interval_transitions)
    transitions.sort(key=lambda t: (absolute(t.at), _ORDER[t.kind], t.rule_id))

    new_state = EngineState(
        held=held, decided=tuple(decided), evaluated_through=now
    )
    return Reconciliation(
        plan=plan,
        transitions=tuple(transitions),
        state=new_state,
        next_at=next_transition_at(plan, new_state, now),
    )


def _decide_at(
    occ: Occurrence,
    rule: dict[str, Any] | None,
    since: datetime,
    now: datetime,
    live: bool,
) -> Transition | None:
    """D41 and D42 for one `At` occurrence whose instant has passed.

    The `observed` predicate is the whole decision. An instant the engine watched
    go by fires, however far behind the evaluation has fallen — a tick two minutes
    late is a slow engine, not a missed occurrence, and applying the grace test to
    it would silently make `grace` mandatory for a punctual system. Anything
    unobserved is D41's question, and D42 sends its late arrivals down the same
    path on purpose so that "something showed up late" has one answer.
    """
    assert occ.start is not None
    lateness = absolute(now) - absolute(occ.start)
    common: dict[str, Any] = {
        "schedule_id": occ.schedule_id,
        "rule_id": occ.rule_id,
        "start_date": occ.start_date,
        "at": occ.start,
        "lateness": lateness,
        "occurrence": occ,
    }

    if live and absolute(occ.start) > absolute(since):
        return Transition(kind=TransitionKind.FIRE, **common)

    grace = (rule or {}).get(CONF_GRACE)
    if grace is not None and lateness <= timedelta(seconds=grace):
        _LOGGER.debug(
            "Firing rule %s within its grace window, %s late", occ.rule_id, lateness
        )
        return Transition(kind=TransitionKind.FIRE, **common)

    # Logged as missed and not fired (D41). The log line is the deliverable here:
    # Keep #13 is satisfied by reconciling what can be reconciled and *recording*
    # the rest, which is not the same as dropping it.
    _LOGGER.info(
        "Rule %s was due at %s and was not observed; missed by %s%s",
        occ.rule_id,
        occ.start.isoformat(),
        lateness,
        "" if grace is None else f" (grace window {timedelta(seconds=grace)})",
    )
    return Transition(kind=TransitionKind.MISSED, **common)


def _reconcile_intervals(
    plan: Plan,
    state: EngineState,
    rules: dict[str, dict[str, Any]],
    *,
    schedule_id: str,
    now: datetime,
    live: bool,
) -> tuple[tuple[HeldInterval, ...], list[Transition]]:
    """D41's `During` half: *should this be active now?*, then reconcile.

    Exits are decided before entries, and held intervals are never re-entered in
    the same pass, so a rule cannot both exit and enter the same occurrence. Two
    consecutive occurrences of one rule have different recurrence dates and are
    therefore different keys, which is what lets a hand-over work while an
    already-held interval stays held.
    """
    occurrences = {
        occ.key: occ for occ in plan.occurrences if occ.is_interval
    }
    transitions: list[Transition] = []
    surviving: list[HeldInterval] = []

    for held in state.held:
        occ = occurrences.get(held.key)
        # The end the engine will honour. The enumerated one wins when it is
        # available, so that D43's invalidation actually moves the exit when an
        # end anchor's entity changes; the recorded one is the fallback, because
        # an interval whose schedule can no longer be evaluated must still end.
        deadline = (
            occ.end
            if occ is not None and occ.status is OccurrenceStatus.SCHEDULED
            and occ.end is not None
            else held.end
        )
        common: dict[str, Any] = {
            "kind": TransitionKind.EXIT,
            "schedule_id": held.schedule_id or schedule_id,
            "rule_id": held.rule_id,
            "start_date": held.start_date,
            "occurrence": occ,
            "held": held,
        }

        if held.rule_id not in rules:
            # The rule was edited away while it was holding. D3's exit path still
            # runs: a schedule must not vanish leaving the world in a state it
            # created, which is the principle D47 states for termination and
            # applies just as much to deletion.
            transitions.append(
                Transition(at=now, cause=ExitCause.GONE, **common)
            )
            continue

        if occ is not None and not occ.armed:
            # **Provisional.** D47 settles the *completion* case — a schedule that
            # finishes mid-interval runs the interval out first — and says nothing
            # about a person flipping the switch off at 22:00 with the lights held
            # on. Read as immediate, because the alternative is that turning a
            # schedule off appears to do nothing until some hour the user cannot
            # see, and because the exit path runs either way, so D47's actual
            # concern — never exiting while holding a state we created — is met.
            # Needs an owner ruling; the two cases are genuinely different.
            transitions.append(
                Transition(at=now, cause=ExitCause.DISARMED, **common)
            )
            continue

        if absolute(now) >= absolute(deadline):
            transitions.append(
                Transition(
                    at=deadline,
                    lateness=absolute(now) - absolute(deadline),
                    cause=ExitCause.WINDOW_END,
                    **common,
                )
            )
            continue

        survivor = replace(held, end=deadline)
        surviving.append(survivor)
        if not live:
            # D41's "reconcile": the interval is still in force and the engine is
            # picking it up again, so the desired state may need re-applying —
            # but its enter actions already ran, before the restart.
            transitions.append(
                Transition(
                    kind=TransitionKind.RESUME,
                    schedule_id=survivor.schedule_id or schedule_id,
                    rule_id=survivor.rule_id,
                    start_date=survivor.start_date,
                    at=survivor.start,
                    lateness=absolute(now) - absolute(survivor.start),
                    occurrence=occ,
                    held=survivor,
                )
            )

    for key, occ in occurrences.items():
        if key in {held.key for held in state.held} or not occ.holds_at(now):
            continue
        assert occ.start is not None and occ.end is not None
        entered = HeldInterval(
            schedule_id=occ.schedule_id or schedule_id,
            rule_id=occ.rule_id,
            start_date=occ.start_date,
            start=occ.start,
            end=occ.end,
            entered_at=now,
        )
        surviving.append(entered)
        # No grace test and no observed check. D41 is explicit that this is always
        # safe, and D42 adds that an anchor recovering after the would-be start
        # simply enters late — an interval is a statement about a stretch of time,
        # so acting on it halfway through is the correct behaviour and not a
        # concession.
        transitions.append(
            Transition(
                kind=TransitionKind.ENTER,
                schedule_id=entered.schedule_id,
                rule_id=entered.rule_id,
                start_date=entered.start_date,
                at=entered.start,
                lateness=absolute(now) - absolute(entered.start),
                occurrence=occ,
                held=entered,
            )
        )

    surviving.sort(key=lambda held: (absolute(held.start), held.rule_id))
    return tuple(surviving), transitions


def _longest_grace(schedule: dict[str, Any]) -> timedelta:
    """The furthest back a late `At` arrival could still be worth firing.

    Zero for the default schedule, which is the point: D41 turns the grace window
    off by default, so the ordinary engine looks back exactly as far as it last
    evaluated and no further.
    """
    graces = [
        timedelta(seconds=rule[CONF_GRACE])
        for rule in schedule.get(CONF_RULES, [])
        if rule.get(CONF_KIND) == RULE_AT
        and rule.get(CONF_GRACE) is not None
        and rule.get(CONF_ENABLED, True)
    ]
    return max(graces, default=_ZERO)


__all__ = [
    "AtRecord",
    "EngineState",
    "ExitCause",
    "HeldInterval",
    "Reconciliation",
    "Transition",
    "TransitionKind",
    "async_plan_recovery",
    "async_plan_tick",
    "next_transition_at",
]
