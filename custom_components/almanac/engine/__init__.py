"""The rule engine — §15 step 3, implementing D2–D5 and D38–D41.

Three modules, in dependency order:

- `occurrence` — one rule on one recurrence date, resolved. A value.
- `recurrence` — D1's start dates, and the period D38 and D39 both bound on.
- `plan` — `async_enumerate`: a schedule plus a window becomes a list of
  occurrences, with D44's compute budget and D13's source horizon reported apart.
- `transition` — a plan plus what is already held becomes a list of things to do,
  which is where D41's restart asymmetry and D42's late arrivals live.

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

from .occurrence import Occurrence, OccurrenceStatus
from .plan import Plan, async_enumerate, interval_problems
from .recurrence import recurrence_period, start_dates
from .transition import (
    AtRecord,
    EngineState,
    ExitCause,
    HeldInterval,
    Reconciliation,
    Transition,
    TransitionKind,
    async_plan_recovery,
    async_plan_tick,
    next_transition_at,
)

__all__ = [
    "AtRecord",
    "EngineState",
    "ExitCause",
    "HeldInterval",
    "Occurrence",
    "OccurrenceStatus",
    "Plan",
    "Reconciliation",
    "Transition",
    "TransitionKind",
    "async_enumerate",
    "async_plan_recovery",
    "async_plan_tick",
    "interval_problems",
    "next_transition_at",
    "recurrence_period",
    "start_dates",
]
