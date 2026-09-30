"""D46, D47, D83 — when a schedule has finished, and what happens then.

Three independent axes, which is what replaces today's three magic names
(`repeat` / `pause` / `single`, surfaced as Stop/Delete, with "after it triggers"
left undefined for multi-slot schemes):

| Axis | Values |
| --- | --- |
| **Finished when** | never (D83, the default) · one rule fired · a cycle · N occurrences · a date · a condition |
| **Then** | keep running · disable self · delete self · run an action |
| **Counter increments on** | every scheduled occurrence · only those whose conditions passed · only those whose actions succeeded |

Independent is the operative word: the three are stored and evaluated separately,
so *"delete after it has actually done its job three times"* is one sentence made
of three choices rather than a fourth magic name.

**Nothing here reads a clock (D64).** `now` is a parameter, and it is used for
exactly two things: comparing against `finished_when: date`, and stamping
`finished_at` when D47 defers a termination. Both are part of what the dry run has
to be able to ask about a hypothetical instant, which is why neither may come from
the ambient clock.

**This module is pure.** It reads state and returns a decision; it disables and
deletes nothing. `tick.py` carries the decision out. That split is what makes D47
testable — "the schedule was finished at 22:00 and terminated at 23:00 when the
interval ended" is two return values, not a sequence of side effects.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Any

from homeassistant.const import CONF_ID

from .actions import ExecutionReport
from .const import (
    CONF_COMPLETION,
    CONF_CONDITIONS,
    CONF_COUNT,
    CONF_COUNT_ON,
    CONF_DATE,
    CONF_ENABLED,
    CONF_FINISHED_WHEN,
    CONF_KIND,
    CONF_RULES,
    CONF_THEN,
    COUNT_ON_ACTIONS_SUCCEEDED,
    COUNT_ON_CONDITIONS_PASSED,
    COUNT_ON_SCHEDULED,
    FINISHED_CONDITION,
    FINISHED_CYCLE,
    FINISHED_DATE,
    FINISHED_NEVER,
    FINISHED_OCCURRENCES,
    FINISHED_ONE_RULE_FIRED,
    THEN_KEEP,
)
from .engine.conditions import async_evaluate
from .engine.day_set import DaySetLookup
from .engine.transition import Transition, TransitionKind
from .resolver import ResolverRegistry

_LOGGER = logging.getLogger(__name__)

# D46's third axis, as a set of transition kinds per value.
#
# `scheduled` — every scheduled occurrence. FIRE, SKIPPED and MISSED are the three
# things that can become of an `At` occurrence, and ENTER is a `During` occurrence
# beginning. RESUME is deliberately absent: it is D90's restart re-entry into an
# interval that was already counted when it began, and counting it would make a
# schedule's occurrence count depend on how often Home Assistant restarted. EXIT is
# absent because an interval is one occurrence, not two.
#
# `conditions_passed` — FIRE and ENTER only, which is exactly the set of kinds that
# cannot be produced while a condition blocks: a blocked `At` occurrence is SKIPPED
# and a blocked `During` occurrence never enters at all (D25).
#
# `actions_succeeded` — the same set, filtered again on the execution report. See
# `_counts`.
_COUNTED: Mapping[str, frozenset[TransitionKind]] = {
    COUNT_ON_SCHEDULED: frozenset(
        {
            TransitionKind.FIRE,
            TransitionKind.SKIPPED,
            TransitionKind.MISSED,
            TransitionKind.ENTER,
        }
    ),
    COUNT_ON_CONDITIONS_PASSED: frozenset({TransitionKind.FIRE, TransitionKind.ENTER}),
    COUNT_ON_ACTIONS_SUCCEEDED: frozenset({TransitionKind.FIRE, TransitionKind.ENTER}),
}


@dataclass(frozen=True, slots=True)
class CompletionState:
    """A schedule's progress towards its own completion, as persisted (D35).

    This is runtime state, not definition, so it lives in the runtime store and is
    lost if that store is lost — which is the right outcome: a counter restored
    from a stale backup would finish a schedule that had not run.

    `fired_rules` is a tuple rather than a set so the record round-trips through
    JSON unchanged and reads in order; membership tests are against a handful of
    ids, so there is nothing to gain from a set.
    """

    count: int = 0
    fired_rules: tuple[str, ...] = ()
    finished_at: datetime | None = None
    settled: bool = False

    def as_dict(self) -> dict[str, Any]:
        """JSON scalars only — ISO strings for instants, as elsewhere in storage."""
        return {
            "count": self.count,
            "fired_rules": list(self.fired_rules),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "settled": self.settled,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CompletionState:
        """Rebuild from the runtime store, tolerating every key being absent.

        A record written before this field existed, or a store that has only ever
        held engine state, both read as a fresh `CompletionState` rather than
        raising — the same tolerance `EngineState.from_dict` applies, and for the
        same reason: a schedule whose runtime record cannot be read should start
        over, not fail to load.
        """
        finished = data.get("finished_at")
        return cls(
            count=int(data.get("count", 0)),
            fired_rules=tuple(str(rule_id) for rule_id in data.get("fired_rules") or ()),
            finished_at=datetime.fromisoformat(finished) if finished else None,
            settled=bool(data.get("settled", False)),
        )


@dataclass(frozen=True, slots=True)
class CompletionOutcome:
    """What this tick did to a schedule's completion, and what the caller must do.

    `then` is the *Then* block to carry out, and it is `None` on every tick that
    did not just settle the schedule — including the tick that finds the condition
    met while an interval is still held, which is D47's whole point.
    """

    state: CompletionState
    finished: bool = False
    deferred: bool = False
    then: Mapping[str, Any] | None = None
    reason: str | None = None


def _enabled_rule_ids(schedule: Mapping[str, Any]) -> tuple[str, ...]:
    """The rules a cycle has to get through.

    Disabled rules are excluded, because D77 makes `enabled` a per-rule field and a
    cycle that waited for a rule the user had switched off would never complete.
    That is the same reading as D91's: a disarmed rule is not participating.
    """
    return tuple(
        str(rule[CONF_ID])
        for rule in schedule.get(CONF_RULES, ())
        if rule.get(CONF_ENABLED, True)
    )


def _counts(
    axis: str, transition: Transition, report: ExecutionReport | None
) -> bool:
    """Whether this transition increments the counter, under the chosen axis."""
    if transition.kind not in _COUNTED[axis]:
        return False
    if axis != COUNT_ON_ACTIONS_SUCCEEDED:
        return True
    # "Only those whose actions succeeded" — all of them, and at least one. A rule
    # with nothing to do has not succeeded at anything (see
    # `ExecutionReport.succeeded`), so a schedule counting successful actions never
    # advances on an occurrence that performed none. That is stricter than it looks
    # and it is the point: this axis exists so that "delete after it triggers" can
    # mean "after it actually did the thing".
    return report is not None and report.succeeded


async def _async_finished(
    registry: ResolverRegistry,
    day_sets: DaySetLookup,
    schedule: Mapping[str, Any],
    state: CompletionState,
    *,
    now: datetime,
) -> tuple[bool, str | None]:
    """Whether the *Finished when* condition is met at `now`, and why."""
    finished_when = schedule[CONF_COMPLETION][CONF_FINISHED_WHEN]
    kind = str(finished_when[CONF_KIND])

    if kind == FINISHED_NEVER:
        # D83. Most schedules are perpetual, and this is the default, so it is the
        # first branch rather than a fall-through.
        return False, None

    if kind == FINISHED_ONE_RULE_FIRED:
        if state.fired_rules:
            return True, f"rule {state.fired_rules[0]} fired"
        return False, None

    if kind == FINISHED_CYCLE:
        expected = _enabled_rule_ids(schedule)
        if not expected:
            # A schedule with no enabled rules has not completed a cycle; it has
            # nothing to complete. Returning True here would delete a schedule the
            # moment its last rule was switched off.
            return False, None
        outstanding = [rule_id for rule_id in expected if rule_id not in state.fired_rules]
        if outstanding:
            return False, None
        return True, f"all {len(expected)} enabled rules fired"

    if kind == FINISHED_OCCURRENCES:
        wanted = int(finished_when[CONF_COUNT])
        if state.count >= wanted:
            return True, f"{state.count} of {wanted} counted occurrences"
        return False, None

    if kind == FINISHED_DATE:
        # Inclusive of the named day, which is D96 applied to this axis: a person
        # who types an end date means the day they named. "Finish on 31 December"
        # therefore finishes on 1 January, not at midnight on the 31st.
        #
        # The comparison is between two civil dates, not two instants, so
        # `absolute()` is not involved — but the conversion to local time is, and
        # it goes through the resolver registry's zone rather than `dt_util` so
        # that a dry run at a hypothetical instant reads the same zone the engine
        # does.
        named = datetime.fromisoformat(str(finished_when[CONF_DATE])).date()
        today = now.astimezone(registry.zone).date()
        if today > named:
            return True, f"the date {named.isoformat()} has passed"
        return False, None

    if kind == FINISHED_CONDITION:
        outcome = await async_evaluate(
            registry, day_sets, finished_when[CONF_CONDITIONS], now=now
        )
        if outcome.problem is not None:
            # D97 — an undetermined predicate changes nothing. A completion
            # condition the engine cannot read must not finish the schedule, and
            # must not be recorded as "not finished" either; it is simply not yet
            # answered, and the next tick asks again.
            _LOGGER.debug(
                "almanac completion condition is undetermined: %s", outcome.problem
            )
            return False, None
        if outcome.passed:
            return True, "the completion condition became true"
        return False, None

    # Unreachable while the schema is the only way in. Kept as a branch rather than
    # an assertion because a storage record written by a newer version is a thing
    # that happens, and refusing to finish is the safe reading of a value we do not
    # understand.
    _LOGGER.warning("almanac does not understand finished_when kind %r", kind)
    return False, None


async def async_settle(
    registry: ResolverRegistry,
    day_sets: DaySetLookup,
    schedule: Mapping[str, Any],
    state: CompletionState,
    outcomes: Sequence[tuple[Transition, ExecutionReport | None]],
    *,
    now: datetime,
    holding: bool,
) -> CompletionOutcome:
    """Fold this tick's transitions into a schedule's completion state.

    `outcomes` pairs each transition with what executing it actually did, because
    D46's third axis cannot be evaluated from the transition alone — "only those
    whose actions succeeded" is a fact about the execution, and the engine that
    produced the transition deliberately fires nothing.

    `holding` is whether the schedule still has an interval in force. It is D47's
    input: **termination never takes effect mid-interval.** A schedule that meets
    its completion condition while holding records `finished_at` and keeps running
    until the interval ends and its exit path has run; only then is the *Then*
    block returned. Without that, a schedule can disable itself while holding the
    lights on and leave them on indefinitely, which is the one failure D47 names.

    Returns the new state and, on the tick that settles it, the *Then* block to
    carry out. On every other tick `then` is `None`, so a caller that simply acts
    on whatever it is handed cannot terminate a schedule twice.
    """
    completion = schedule[CONF_COMPLETION]
    axis = str(completion[CONF_COUNT_ON])

    count = state.count
    fired = list(state.fired_rules)
    for transition, report in outcomes:
        if not _counts(axis, transition, report):
            continue
        count += 1
        if transition.rule_id not in fired:
            # A rule's membership here is governed by the same axis as the counter.
            # That is a deliberate coupling and a **provisional** one: D46 presents
            # the third axis as being about the counter, and "one rule fired" /
            # "a cycle" could reasonably have counted every scheduled occurrence
            # regardless. Sharing the axis is chosen because the alternative leaves
            # those two values unable to express "actually did something", which is
            # precisely the gap the third axis was added to close — and because a
            # schedule where the counter and the cycle disagreed about whether a
            # skipped occurrence counted would be unexplainable on any surface.
            fired.append(transition.rule_id)

    state = CompletionState(
        count=count,
        fired_rules=tuple(fired),
        finished_at=state.finished_at,
        settled=state.settled,
    )

    if state.settled:
        # Already dealt with. A schedule whose *Then* was `keep` stays in this state
        # for the rest of its life, which is why `settled` exists separately from
        # `finished_at`: the counter keeps counting and nothing is decided twice.
        return CompletionOutcome(state=state)

    if state.finished_at is None:
        finished, reason = await _async_finished(
            registry, day_sets, schedule, state, now=now
        )
        if not finished:
            return CompletionOutcome(state=state)
        # Stamped once, at the instant the condition was first met, and never
        # re-evaluated afterwards. A completion condition that became true and then
        # false again while D47 held the termination back has still *become* true,
        # which is what the axis says, and the audit trail should show when.
        state = CompletionState(
            count=state.count,
            fired_rules=state.fired_rules,
            finished_at=now,
            settled=state.settled,
        )
    else:
        reason = "the completion condition was met earlier"

    if holding:
        # D47. Finished, and deliberately not acted on.
        _LOGGER.debug(
            "almanac schedule %s is finished but still holding an interval; "
            "termination deferred (D47)",
            schedule.get("id"),
        )
        return CompletionOutcome(
            state=state, finished=True, deferred=True, reason=reason
        )

    then = completion[CONF_THEN]
    _LOGGER.info(
        "almanac schedule %s finished (%s); then: %s",
        schedule.get("id"),
        reason,
        then[CONF_KIND],
    )
    return CompletionOutcome(
        state=CompletionState(
            count=state.count,
            fired_rules=state.fired_rules,
            finished_at=state.finished_at,
            settled=True,
        ),
        finished=True,
        then=then,
        reason=reason,
    )


def then_is_a_no_op(then: Mapping[str, Any] | None) -> bool:
    """Whether a settled *Then* asks for nothing to be done.

    `keep running` is a real value on the axis rather than the absence of one
    (D83's argument applied to the second axis), so it reaches the caller as a
    block like any other and this is what says it needs no action.
    """
    return then is None or str(then[CONF_KIND]) == THEN_KEEP
