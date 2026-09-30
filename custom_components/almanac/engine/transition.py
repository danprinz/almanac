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

**Conditions are evaluated here, once per rule per pass.** D25 makes a `During`
rule's conditions part of an always-live activation predicate rather than a gate
checked at the start, so the same predicate decides entry, decides whether a held
interval survives, and decides whether an `At` occurrence fires. Those three
readings have to agree inside one evaluation — a motion sensor that flipped between
two of them could otherwise produce an exit and an entry for the same rule in the
same pass — so `_Conditions` evaluates each rule once and caches the answer for the
rest of the pass. That cache is per-evaluation and deliberately not persisted: the
predicate is about *now*, and a stale answer is worse than a fresh unknown.

**Three things are deliberately remembered.** `EngineState.held` is what the engine
must be able to exit even if the schedule that created it has since been edited
away — an interval must never outlive its own recorded end, because the failure
that matters is lights left on indefinitely (the concern D47 states for
termination). `EngineState.decided` is which `At` occurrences have already been
ruled on, which is what makes re-running the same evaluation twice produce nothing
the second time. Without it, widening the lookback to catch a late arrival would
re-fire everything inside the grace window on every tick. `EngineState.waiting`
is D26's `wait_until` policy made durable: an occurrence whose conditions failed
but whose rule asked to wait is neither fired nor decided, and the promise to keep
looking until a deadline has to survive a restart or the policy would silently mean
"wait, unless something happens".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any, Self

from homeassistant.const import CONF_ID

from ..const import (
    CONF_CONDITION_POLICY,
    CONF_CONDITIONS,
    CONF_DEADLINE,
    CONF_ENABLED,
    CONF_GRACE,
    CONF_KIND,
    CONF_LATCH,
    CONF_RULES,
    POLICY_WAIT_UNTIL,
    RULE_AT,
)
from ..resolver import ResolverRegistry, Window
from .conditions import ConditionOutcome, async_evaluate
from .day_set import DaySetLookup
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
    interval re-announce it. D90 records the distinction, which D41's "reconcile"
    left unnamed; see the note in the module that owns the actions.
    """

    FIRE = "fire"
    MISSED = "missed"
    # D26's `skip`, and the far end of a `wait_until` that ran out of time. Kept
    # apart from MISSED because the two are different sentences and D24 supplies
    # the words for this one: "the 22:00 announcement did not run because the
    # cleaning crew were in" is actionable, and "missed" is not. A transition
    # rather than a log line only, because §12's audit trail has to be able to
    # show a decision the engine made deliberately.
    SKIPPED = "skipped"
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
    # D25 with D4 — the conditions stopped holding while the interval was in
    # force, and the rule did not latch. This is the cause upstream cannot express:
    # `track_conditions` re-fires when any single condition flips rather than when
    # the overall and/or result does (§7.3, card#878), so it has no notion of the
    # predicate as a whole having become false.
    CONDITIONS = "conditions"


# Exits are emitted before entries at the same instant, so that a caller applying
# a batch in order hands over rather than overlapping: the outgoing interval's
# `on_exit` runs before the incoming one's desired state, which is the only
# ordering under which back-to-back intervals do not fight over the same entity.
_ORDER: dict[TransitionKind, int] = {
    TransitionKind.EXIT: 0,
    # SKIPPED and MISSED come next because neither does anything to the world.
    # Ordering them before the acting kinds keeps a batch's log readable: what did
    # not happen is stated before what did.
    TransitionKind.SKIPPED: 1,
    TransitionKind.MISSED: 2,
    TransitionKind.FIRE: 3,
    TransitionKind.RESUME: 4,
    TransitionKind.ENTER: 5,
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
class PendingAt:
    """An `At` occurrence whose conditions failed and whose rule asked to wait (D26).

    D26 gives an `At` rule two readings of "the conditions were false at the
    moment it was due": `skip` treats the occurrence as gone, and
    `wait_until(deadline)` keeps it alive so that "turn the heating on at 06:00,
    but only once somebody is home" behaves the way it reads.

    `deadline` is stored as the absolute instant the wait expires, computed once
    when the wait begins, rather than recomputed from the rule on each pass. That
    is a decision, not a convenience: a promise the engine made at 06:00 should not
    change because somebody edited the policy at 07:00, and recomputing would let
    an edit extend or retroactively expire a wait already in progress. Editing the
    rule changes what *future* occurrences promise, which is the same rule the rest
    of the engine follows.
    """

    rule_id: str
    start_date: date
    at: datetime
    deadline: datetime

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
            "deadline": self.deadline.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Rebuild from the runtime store."""
        return cls(
            rule_id=data["rule_id"],
            start_date=date.fromisoformat(data["start_date"]),
            at=datetime.fromisoformat(data["at"]),
            deadline=datetime.fromisoformat(data["deadline"]),
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
    waiting: tuple[PendingAt, ...] = ()
    evaluated_through: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        """Serialise for the runtime store (D35)."""
        return {
            "held": [item.as_dict() for item in self.held],
            "decided": [item.as_dict() for item in self.decided],
            "waiting": [item.as_dict() for item in self.waiting],
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
            waiting=tuple(
                PendingAt.from_dict(item) for item in data.get("waiting", [])
            ),
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
    # D24 — the outcome that produced this transition, when conditions were
    # involved. Carried rather than reduced to a boolean because the label a user
    # wrote is the whole value of the decision: "Skipped: *cleaning crew present*"
    # is what the log and the timeline print, and neither can reconstruct it later.
    conditions: ConditionOutcome | None = None

    @property
    def key(self) -> tuple[str, date]:
        """What identifies the occurrence this transition is about."""
        return (self.rule_id, self.start_date)

    def as_dict(self) -> dict[str, Any]:
        """The wire form, for the dry run (D64).

        Deliberately close to `events.occurrence_payload`, because a dry run's
        whole claim is that it shows what the live engine would have done, and a
        reader comparing a dry-run row against the recorder row it predicts should
        not have to translate between two spellings of the same transition. It is
        not identical: `occurrence_payload` is addressed to the logbook and so
        carries the schedule's name and entity_id, which the dry run's caller
        already has from the envelope, and it flattens `conditions` to D24's
        blocking labels alone. Here the outcome is carried whole, because "the
        conditions could not be read" (`determined` false) is a different sentence
        from "this condition said no", and a dry run exists precisely to show the
        difference before it happens.

        `lateness` is emitted in seconds, matching `occurrence_payload`. In a dry
        run it is almost always zero — the hypothetical instant is the instant the
        engine is told about, so nothing is behind — and it is emitted anyway so
        that the recovery-pass case (`live=False`, D41) has somewhere to say so.
        """
        return {
            "kind": str(self.kind),
            "schedule_id": self.schedule_id,
            "rule_id": self.rule_id,
            "start_date": self.start_date.isoformat(),
            "at": self.at.isoformat(),
            "lateness": self.lateness.total_seconds(),
            "occurrence": (
                self.occurrence.as_dict() if self.occurrence is not None else None
            ),
            "held": self.held.as_dict() if self.held is not None else None,
            "cause": str(self.cause) if self.cause is not None else None,
            "conditions": (
                {
                    "passed": self.conditions.passed,
                    "determined": self.conditions.determined,
                    "blocking": list(self.conditions.blocking),
                    "problem": (
                        {
                            "reason": str(self.conditions.problem.reason),
                            "detail": self.conditions.problem.detail,
                        }
                        if self.conditions.problem is not None
                        else None
                    ),
                }
                if self.conditions is not None
                else None
            ),
        }


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

    def as_dict(self) -> dict[str, Any]:
        """The wire form — what a dry run at one instant returns for one schedule.

        The plan comes with it rather than being a separate query. D63 wants one
        view, and the transitions alone do not answer "and what else is in this
        window" — an occurrence that is neither due nor held produces no
        transition at all, and is exactly the row a person opened the dry run to
        see.

        `next_at` here is `transition.py`'s own answer over the plan it was given,
        which is a narrower window than the tick's `_async_next_wake` searches. It
        is emitted as what this evaluation concluded, not as a promise about the
        live scheduler's next wake.
        """
        return {
            "plan": self.plan.as_dict(),
            "transitions": [transition.as_dict() for transition in self.transitions],
            "state": self.state.as_dict(),
            "next_at": self.next_at.isoformat() if self.next_at is not None else None,
        }


class _Conditions:
    """Each rule's condition outcome, evaluated once per pass.

    The cache is the decision, not an optimisation. D25 makes one predicate answer
    three questions inside a single evaluation — may this interval be entered, may
    the held one survive, does this `At` occurrence fire — and a sensor that changed
    between two of those reads could produce an exit and an entry for the same rule
    in the same pass. Evaluating once makes that impossible rather than unlikely.

    Keyed by rule id, which is also why a rule with no id (`""`) shares one entry
    with every other such rule: the schema gives every rule an id, so that case only
    arises for hand-written test fixtures, and sharing an answer there is harmless.
    """

    def __init__(
        self,
        registry: ResolverRegistry,
        day_sets: DaySetLookup | None,
        *,
        now: datetime,
    ) -> None:
        """Set up the cache for one evaluation at one instant."""
        self._registry = registry
        self._day_sets = day_sets
        self._now = now
        self._outcomes: dict[str, ConditionOutcome] = {}

    async def async_for(
        self, rule_id: str, rule: dict[str, Any]
    ) -> ConditionOutcome:
        """The outcome for one rule, computed at most once."""
        if rule_id not in self._outcomes:
            self._outcomes[rule_id] = await async_evaluate(
                self._registry,
                self._day_sets,
                rule.get(CONF_CONDITIONS, ()),
                now=self._now,
            )
        return self._outcomes[rule_id]


@dataclass(frozen=True, slots=True)
class _AtDecision:
    """What to do about one `At` occurrence, and whether the question is settled.

    `settled` is the field that matters. An occurrence recorded in `decided` is
    never reconsidered, which is what makes re-evaluating idempotent — so a
    `wait_until` that is still waiting must *not* be recorded, and that is the one
    case where a decision produces no transition and no record.
    """

    transition: Transition | None = None
    settled: bool = True
    pending: PendingAt | None = None


async def async_plan_tick(
    registry: ResolverRegistry,
    schedule: dict[str, Any],
    state: EngineState,
    *,
    now: datetime,
    day_sets: DaySetLookup | None = None,
) -> Reconciliation:
    """Evaluate a schedule as a running engine: we were watching since last time.

    Everything whose instant falls after `state.evaluated_through` was observed as
    it happened, so an `At` occurrence in that stretch simply fires. An occurrence
    *older* than that which the engine has not ruled on before is a late arrival —
    D42's recovered anchor — and goes through the grace test like any other
    unobserved instant.
    """
    return await _async_reconcile(
        registry, schedule, state, now=now, live=True, day_sets=day_sets
    )


async def async_plan_recovery(
    registry: ResolverRegistry,
    schedule: dict[str, Any],
    state: EngineState,
    *,
    now: datetime,
    day_sets: DaySetLookup | None = None,
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
    return await _async_reconcile(
        registry, schedule, state, now=now, live=False, day_sets=day_sets
    )


def next_transition_at(
    plan: Plan, state: EngineState, now: datetime
) -> datetime | None:
    """The next instant this schedule has something to do, or `None`.

    D55's sensor renders this, and a tick scheduler wants it too. Pure, and it
    reads `now` from a parameter (D64) — which is what lets the timeline ask "what
    is next, as of Friday evening" and get the same answer the engine will.

    Held ends are included even when the occurrence no longer enumerates, for the
    same reason `HeldInterval` carries its end: the exit is owed regardless.

    What is *not* here is the instant a `for:` condition would become satisfiable.
    A `During` rule gated on "motion clear for 5 minutes" becomes enterable at a
    computable instant, and computing it would let the engine wake exactly then
    instead of on the following tick. That is a refinement the design does not
    require and D43's invalidation partly covers, and adding it would mean the
    scheduler had a second reason to wake that the plan cannot explain.
    """
    moment = absolute(now)
    candidates = [held.end for held in state.held if absolute(held.end) > moment]
    # A pending wait's deadline is a scheduled event in its own right (D26): it is
    # the instant the engine gives up and records the skip. Without it here, the
    # skip would be logged whenever the next unrelated tick happened to run, which
    # would make the deadline approximate for no reason.
    candidates.extend(
        entry.deadline for entry in state.waiting if absolute(entry.deadline) > moment
    )
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
    day_sets: DaySetLookup | None = None,
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
    # Absolute throughout (A.13). `since` and `now` can sit forty-five minutes
    # apart inside the repeated hour and compare the wrong way round, which would
    # pick the later instant as the window's start and hand `Window` an interval it
    # refuses. The lookback is a duration, so it is subtracted absolutely for D95's
    # reason: "twelve hours of grace" means twelve hours.
    horizon = (
        now
        if first_ever
        else min(since, absolute(now) - _longest_lookback(schedule), key=absolute)
    )
    window = Window(
        min(horizon, now, key=absolute), absolute(now) + timedelta.resolution
    )

    plan = await async_enumerate(registry, schedule, window, day_sets=day_sets)
    rules = {
        rule.get(CONF_ID) or "": rule for rule in schedule.get(CONF_RULES, [])
    }
    conditions = _Conditions(registry, day_sets, now=now)

    transitions: list[Transition] = []
    decided = [
        record for record in state.decided
        if absolute(record.at) >= absolute(window.start)
    ]
    already = {record.key for record in decided}
    at_occurrences = {
        occ.key: occ for occ in plan.occurrences if not occ.is_interval
    }
    pending: list[PendingAt] = []

    # D26's outstanding waits, handled before the plan's own occurrences so that a
    # waiting occurrence is never re-decided by the loop below. They are driven
    # from the recorded wait rather than from the plan on purpose: the promise is
    # owed even if the occurrence has stopped enumerating, for the same reason
    # `HeldInterval` carries its own end.
    for entry in state.waiting:
        already.add(entry.key)
        rule = rules.get(entry.rule_id)
        if rule is None:
            # The rule was edited away mid-wait. Nothing is owed: unlike a held
            # interval, a wait has created no state in the world to undo.
            _LOGGER.info(
                "Abandoning the wait for rule %s on %s: the rule no longer exists",
                entry.rule_id,
                entry.start_date,
            )
            continue
        outcome = await conditions.async_for(entry.rule_id, rule)
        occ = at_occurrences.get(entry.key)
        common: dict[str, Any] = {
            "schedule_id": schedule_id,
            "rule_id": entry.rule_id,
            "start_date": entry.start_date,
            "at": entry.at,
            "lateness": absolute(now) - absolute(entry.at),
            "occurrence": occ,
            "conditions": outcome,
        }
        if outcome.passed:
            _LOGGER.debug(
                "Rule %s fires late: its conditions came good %s after it was due",
                entry.rule_id,
                common["lateness"],
            )
            transitions.append(Transition(kind=TransitionKind.FIRE, **common))
        elif absolute(now) >= absolute(entry.deadline):
            # Checked *after* the conditions, so that a tick landing exactly on the
            # deadline with the conditions finally true fires rather than skips.
            # `next_transition_at` schedules exactly such a tick, so this is not a
            # hypothetical ordering.
            _LOGGER.info(
                "Rule %s waited until %s and its conditions never held: %s",
                entry.rule_id,
                entry.deadline.isoformat(),
                outcome.summary,
            )
            transitions.append(Transition(kind=TransitionKind.SKIPPED, **common))
        else:
            pending.append(entry)
            continue
        decided.append(AtRecord(entry.rule_id, entry.start_date, entry.at))

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
        already.add(occ.key)
        if not occ.will_run:
            decided.append(AtRecord(occ.rule_id, occ.start_date, occ.start))
            continue
        decision = await _async_decide_at(
            occ, rules.get(occ.rule_id), since, now, live, conditions
        )
        if decision.transition is not None:
            transitions.append(decision.transition)
        if decision.pending is not None:
            pending.append(decision.pending)
        if decision.settled:
            # Not recorded while a `wait_until` is still waiting, which is the
            # whole mechanism: `decided` means "ruled on", and an occurrence the
            # engine is still watching has not been ruled on.
            decided.append(AtRecord(occ.rule_id, occ.start_date, occ.start))

    held, interval_transitions = await _async_reconcile_intervals(
        plan, state, rules, conditions, schedule_id=schedule_id, now=now, live=live
    )
    transitions.extend(interval_transitions)
    transitions.sort(key=lambda t: (absolute(t.at), _ORDER[t.kind], t.rule_id))

    new_state = EngineState(
        held=held,
        decided=tuple(decided),
        waiting=tuple(pending),
        evaluated_through=now,
    )
    return Reconciliation(
        plan=plan,
        transitions=tuple(transitions),
        state=new_state,
        next_at=next_transition_at(plan, new_state, now),
    )


async def _async_decide_at(
    occ: Occurrence,
    rule: dict[str, Any] | None,
    since: datetime,
    now: datetime,
    live: bool,
    conditions: _Conditions,
) -> _AtDecision:
    """D41, D42, D25 and D26 for one `At` occurrence whose instant has passed.

    Two tests, in this order, and the order is the decision.

    *Was it observed?* An instant the engine watched go by fires, however far
    behind the evaluation has fallen — a tick two minutes late is a slow engine,
    not a missed occurrence, and applying the grace test to it would silently make
    `grace` mandatory for a punctual system. Anything unobserved is D41's question,
    and D42 sends its late arrivals down the same path on purpose so that
    "something showed up late" has one answer.

    *Do the conditions hold?* Asked only once the occurrence has survived the first
    test, because an occurrence that was missed never reaches the point where its
    conditions matter, and evaluating them anyway would put "Skipped: nobody home"
    in the log for something that was actually missed by six hours.
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

    grace = (rule or {}).get(CONF_GRACE)
    if not (live and absolute(occ.start) > absolute(since)):
        if grace is None or lateness > timedelta(seconds=grace):
            # Logged as missed and not fired (D41). The log line is the deliverable
            # here: Keep #13 is satisfied by reconciling what can be reconciled and
            # *recording* the rest, which is not the same as dropping it.
            _LOGGER.info(
                "Rule %s was due at %s and was not observed. Missed by %s%s",
                occ.rule_id,
                occ.start.isoformat(),
                lateness,
                "" if grace is None else f" (grace window {timedelta(seconds=grace)})",
            )
            return _AtDecision(Transition(kind=TransitionKind.MISSED, **common))
        _LOGGER.debug(
            "Rule %s is inside its grace window, %s late", occ.rule_id, lateness
        )

    outcome = await conditions.async_for(occ.rule_id, rule or {})
    common["conditions"] = outcome
    if outcome.passed:
        return _AtDecision(Transition(kind=TransitionKind.FIRE, **common))

    # D26 — the rule's own policy decides what a failed predicate means. `skip` is
    # the default (the schema fills it in), so an author who wrote no policy gets
    # the reading that cannot surprise them: the occurrence did not happen.
    policy = (rule or {}).get(CONF_CONDITION_POLICY) or {}
    if policy.get(CONF_KIND) == POLICY_WAIT_UNTIL:
        deadline = occ.start + timedelta(seconds=policy[CONF_DEADLINE])
        if absolute(now) < absolute(deadline):
            _LOGGER.debug(
                "Rule %s is waiting until %s for its conditions: %s",
                occ.rule_id,
                deadline.isoformat(),
                outcome.summary,
            )
            return _AtDecision(
                settled=False,
                pending=PendingAt(
                    rule_id=occ.rule_id,
                    start_date=occ.start_date,
                    at=occ.start,
                    deadline=deadline,
                ),
            )
        # The deadline had already passed by the time the engine first looked —
        # a restart across the whole wait. Skipped without ever having waited,
        # which is the same outcome the wait would have produced.

    _LOGGER.info(
        "Rule %s was due at %s and did not run: %s",
        occ.rule_id,
        occ.start.isoformat(),
        outcome.summary,
    )
    return _AtDecision(Transition(kind=TransitionKind.SKIPPED, **common))


async def _async_reconcile_intervals(
    plan: Plan,
    state: EngineState,
    rules: dict[str, dict[str, Any]],
    conditions: _Conditions,
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

    D25 puts conditions inside this loop rather than in front of it. A `During`
    rule's predicate is *always live*: it is asked again on every pass, and a held
    interval whose predicate has gone false exits with `ExitCause.CONDITIONS`
    unless D4's `latch` is set. That is the behaviour upstream's `track_conditions`
    was reaching for and could not express, because it watches each condition
    rather than the overall and/or result (§7.3, card#878).
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
            # D91 — immediate, and it runs `on_exit`. D47 settles only the
            # *completion* case, where a schedule that finishes mid-interval runs
            # the interval out first; a person flipping the switch off at 22:00 with
            # the lights held on is a different thing and takes effect at once.
            # The alternative — letting the interval finish — is named in D91 and
            # rejected there, because it makes *off* mean two things depending on
            # when it was pressed. D47's actual concern, never exiting while
            # holding a state we created, is met either way: the exit path runs.
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

        outcome = await conditions.async_for(held.rule_id, rules[held.rule_id])
        if not outcome.determined:
            # Unknown changes nothing. A condition entity that has gone away, or
            # is mid-restart, must not turn the lights off — see the note on
            # `ConditionOutcome`, which records this as provisional.
            _LOGGER.debug(
                "Keeping rule %s held: its conditions cannot be read (%s)",
                held.rule_id,
                outcome.problem,
            )
        elif not outcome.passed and not rules[held.rule_id].get(CONF_LATCH, False):
            # D4 — `latch` is what decides this line. Unlatched, the predicate
            # governs the exit and the interval ends when it stops holding.
            # Latched, the interval runs to its window end regardless, which is
            # why the flag exists: "once the lights are on for the evening, leave
            # them on even if the motion sensor gives up".
            transitions.append(
                Transition(
                    at=now,
                    cause=ExitCause.CONDITIONS,
                    conditions=outcome,
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
        # D25's other half. An interval whose conditions do not hold is not
        # entered, and it is not recorded as anything either — the predicate is
        # live, so a later pass in the same window enters it the moment the
        # conditions come good. That is the difference from an `At` rule, where
        # the instant passes and the chance is gone.
        outcome = await conditions.async_for(occ.rule_id, rules.get(occ.rule_id, {}))
        if not outcome.passed:
            _LOGGER.debug(
                "Not entering rule %s: %s", occ.rule_id, outcome.summary
            )
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
                conditions=outcome,
            )
        )

    surviving.sort(key=lambda held: (absolute(held.start), held.rule_id))
    return tuple(surviving), transitions


def _longest_lookback(schedule: dict[str, Any]) -> timedelta:
    """The furthest back an `At` occurrence could still be worth looking at.

    Two reaches, and the wider wins:

    - D41's **grace window**, the furthest back a late arrival could still fire.
    - D26's **wait deadline**, the furthest back an occurrence could still be
      waiting on its conditions. This one is needed even though `EngineState.waiting`
      drives the wait itself, because the occurrence has to stay inside the
      enumerated window for its `Transition.occurrence` to be reportable.

    Zero for the default schedule, which is the point: D41 turns the grace window
    off by default and `skip` is the default policy, so the ordinary engine looks
    back exactly as far as it last evaluated and no further.
    """
    reach = [_ZERO]
    for rule in schedule.get(CONF_RULES, []):
        if rule.get(CONF_KIND) != RULE_AT or not rule.get(CONF_ENABLED, True):
            continue
        if (grace := rule.get(CONF_GRACE)) is not None:
            reach.append(timedelta(seconds=grace))
        policy = rule.get(CONF_CONDITION_POLICY) or {}
        if policy.get(CONF_KIND) == POLICY_WAIT_UNTIL:
            reach.append(timedelta(seconds=policy[CONF_DEADLINE]))
    return max(reach)


__all__ = [
    "AtRecord",
    "EngineState",
    "ExitCause",
    "HeldInterval",
    "PendingAt",
    "Reconciliation",
    "Transition",
    "TransitionKind",
    "async_plan_recovery",
    "async_plan_tick",
    "next_transition_at",
]
