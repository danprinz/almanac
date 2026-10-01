"""The rule engine — §15 steps 3 and 4, implementing D2–D5, D11, D19, D23–D26 and D38–D41.

Six modules, in dependency order:

- `occurrence` — one rule on one recurrence date, resolved. A value.
- `recurrence` — D1's start dates, and the period D38 and D39 both bound on.
- `day_set` — D11's two stages over D18's first-class day sets and D19's single
  level of set algebra: `async_candidate_dates` generates generously and
  `async_covers` filters exactly.
- `conditions` — D23's structured predicate, evaluated in-tree because core's
  condition helpers read the clock for themselves.
- `plan` — `async_enumerate`: a schedule plus a window becomes a list of
  occurrences, with D44's compute budget and D13's source horizon reported apart.
- `transition` — a plan plus what is already held becomes a list of things to do,
  which is where D41's restart asymmetry, D42's late arrivals, D25's always-live
  `During` predicate and D26's `skip` / `wait_until` policy live.

**Nothing in here reads a clock.** D64 is enforced by an AST sweep in
`tests/test_design_constraints.py` with an empty allow-list, and this package adds
no entry to it: `now` is a parameter of every public function, and the two that
need a stretch of time take a `Window`. That is what makes the timeline, the dry
run and the live engine one code path rather than three implementations that agree
until they do not.

The split between `plan` and `transition` is the other structural decision worth
naming. Enumeration is the product's headline feature and has to be callable at any
hypothetical instant with no side effects at all; reconciliation is stateful in the
sense that it consumes and produces an `EngineState`, but it is still pure. Neither
fires anything. The tick that supplies `now`, persists the state and executes the
transitions arrives with actions in step 5, and by then the decisions are already
made and already tested.
"""

from __future__ import annotations

from .conditions import ConditionOutcome, async_evaluate
from .day_set import (
    DaySetLookup,
    async_candidate_dates,
    async_covers,
    day_set_anchors,
    day_set_references,
    day_window,
)
from .occurrence import Occurrence, OccurrenceStatus
from .plan import Plan, async_enumerate, interval_problems
from .recurrence import recurrence_period, start_dates
from .transition import (
    AtRecord,
    EngineState,
    ExitCause,
    HeldInterval,
    PendingAt,
    Reconciliation,
    Transition,
    TransitionKind,
    async_plan_recovery,
    async_plan_tick,
    next_transition_at,
)

__all__ = [
    "AtRecord",
    "ConditionOutcome",
    "DaySetLookup",
    "EngineState",
    "ExitCause",
    "HeldInterval",
    "Occurrence",
    "OccurrenceStatus",
    "PendingAt",
    "Plan",
    "Reconciliation",
    "Transition",
    "TransitionKind",
    "async_candidate_dates",
    "async_covers",
    "async_enumerate",
    "async_evaluate",
    "async_plan_recovery",
    "async_plan_tick",
    "day_set_anchors",
    "day_set_references",
    "day_window",
    "interval_problems",
    "next_transition_at",
    "recurrence_period",
    "start_dates",
]
