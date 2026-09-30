"""The scheduler tick — the one component in almanac that reads a clock.

**D64, stated from the other side.** Every other module takes `now` as a
parameter; this one is where `now` comes from. That is not a concession, it is the
decision: "nothing below the top-level scheduler tick reads a clock" only means
something if there is exactly one thing above it, and `tick.py` is named in
`tests/test_design_constraints.py`'s allow-list so that *who reads the clock* is a
question with a written answer rather than a convention.

Two consequences follow, and both are the reason the constraint exists:

- `async_tick(now)` is a parameter away from being the dry run. Feed it Friday
  evening and it computes what would happen; the only difference between the
  timeline and the engine is whether the transitions are handed to `actions.py`.
- Nothing here is time-dependent except the handful of callbacks that sample the
  clock — `_async_started`, `_async_settle_changed`, `_async_collection_changed`
  and `async_refresh` — and each one does nothing but sample it and hand it to
  `async_tick`. `_async_clock_tick` does not even do that: it uses the instant the
  timer fired for, so the evaluated instant equals the one the plan was woken for.

**The pipeline, once per schedule per tick:**

1. `engine/transition.py` produces `Transition` values and fires nothing.
2. This module turns each one into side effects via `actions.py`, in the order
   D5 implies — desired state first, then the edge's actions.
3. `completion.py` folds the results into the schedule's completion state.
4. The new engine state, the completion state and each held interval's *exit
   promise* are written to the runtime store (D35).
5. The next wake instant is `min` over every schedule's `next_at`.

**The exit promise is the part that is not obvious.** When an interval is entered
the engine records what the world looked like beforehand (D3's `restore`) *and* a
copy of the rule's `on_exit` and `exit_actions`, keyed by the held interval. It
has to be a copy: `ExitCause.GONE` exists for a rule edited away while holding,
and at that point there is no rule left to read the exit behaviour from. This is
the same argument `HeldInterval.end` makes for carrying its own end — the promise
the engine made when it entered is honoured even when the schedule that produced
it can no longer be evaluated.

**Waking up.** Two mechanisms, and they are complements rather than a belt and
braces:

- `next_transition_at` gives the next instant the *plan* has something to do, and
  a single `async_track_point_in_time` fires there. It deliberately excludes the
  instant a `for:` condition becomes satisfiable — see its docstring — which is
  exactly the gap the second mechanism covers.
- A state-change subscription over every entity the reverse index (D57) records as
  feeding a *decision* — conditions, anchors, completion conditions — plus D43's
  resolver invalidation signal, both funnelled through a `Debouncer` so that a
  burst of sensor updates produces one evaluation.

There is no polling interval. A scheduler that re-evaluates every thirty seconds
is one that cannot explain why it woke up, and D12's promise is that every
occurrence is enumerable — so every wake has a reason that can be named.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import Any

import voluptuous as vol

from homeassistant.const import CONF_ENABLED, CONF_ID, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import CALLBACK_TYPE, Context, Event, HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import (
    async_track_point_in_time,
    async_track_state_change_event,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util

from .actions import (
    ActionResult,
    ExecutionReport,
    StateSnapshot,
    async_apply_state,
    async_restore,
    async_run_actions,
    capture_state,
)
from .completion import CompletionOutcome, CompletionState, async_settle, then_is_a_no_op
from .const import (
    ANCHOR_RESOLVER,
    ATTR_RULE_ID,
    ATTR_SCHEDULE_ID,
    CONF_ACTIONS,
    CONF_ANCHOR,
    CONF_CONDITIONS,
    CONF_DOMAIN,
    CONF_END,
    CONF_ENTER_ACTIONS,
    CONF_EXIT_ACTIONS,
    CONF_KEY,
    CONF_KIND,
    CONF_NAME,
    CONF_OBJECT_ID,
    CONF_ON_EXIT,
    CONF_RULES,
    CONF_START_ANCHOR,
    CONF_STATE,
    DOMAIN,
    END_ANCHOR,
    ENUMERATION_HORIZON_DAYS,
    ON_EXIT_APPLY,
    ON_EXIT_RESTORE,
    RULE_AT,
    THEN_ACTION,
    THEN_DELETE,
    THEN_DISABLE,
)
from .engine.conditions import async_evaluate
from .engine.plan import async_enumerate
from .engine.transition import (
    EngineState,
    ExitCause,
    HeldInterval,
    Transition,
    TransitionKind,
    async_plan_recovery,
    async_plan_tick,
    next_transition_at,
)
from .events import async_fire_execution, async_fire_occurrence
from .index import Usage, build_index
from .resolver.contract import Window, absolute
from .storage import AlmanacData

_LOGGER = logging.getLogger(__name__)

SERVICE_RUN_NOW = "run_now"

# The service's field names are the event's field names, and deliberately: "run
# this rule now" and "this rule ran" are the same two identifiers, so a person
# reading the recorder and a person writing a service call should not have to
# learn two spellings. They moved to `const.py` in step 6 because D48 makes the
# event payload a compatibility surface; they are still importable from here.
ATTR_BYPASS_CONDITIONS = "bypass_conditions"

# D45's flag is explicit and defaults to off, which is the whole point: burying
# condition bypass in a second service name would make the audit log read as if
# the conditions had passed.
RUN_NOW_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_SCHEDULE_ID): cv.string,
        vol.Optional("entity_id"): cv.entity_ids,
        vol.Optional(ATTR_RULE_ID): cv.string,
        vol.Optional(ATTR_BYPASS_CONDITIONS, default=False): cv.boolean,
    }
)

# How long a burst of state changes is allowed to settle before the engine
# re-evaluates. One second, because the thing being debounced is a group of
# sensors moving together — a motion sensor and its illuminance partner, or a
# whole Zigbee group reporting after a power cut — and the engine's decisions are
# never sub-second. `immediate=False` so the *last* value in the burst is the one
# evaluated: acting on the first and then again on the last would fire twice on a
# transition that happened once.
_STATE_SETTLE_SECONDS = 1.0

# The floor on how soon the next wake may be scheduled. A `next_at` at or before
# `now` would otherwise schedule a timer for the past, which fires immediately and
# recomputes the same answer — a busy loop that looks like a hung event loop. It
# can happen legitimately: an exit owed at an instant that has just passed, or a
# clock stepped backwards. One second forward turns it into one extra tick.
_MIN_WAKE = timedelta(seconds=1)

# How far ahead the tick looks for its next wake. Two stages, because the cost is
# per day of enumeration and almost every schedule has something to do tomorrow:
# the short window answers for anything daily or weekly, and only a schedule with
# nothing in the next two days pays for the full D44 budget. Anything further out
# than the budget is beyond what this engine claims to be able to compute, and the
# idle wake below is what eventually brings it into range.
_LOOKAHEAD = timedelta(days=2)
_BUDGET = timedelta(days=ENUMERATION_HORIZON_DAYS)

# **Provisional.** With nothing scheduled inside D44's ninety days there is no
# instant to wake for, and a schedule whose only occurrence is a hundred days away
# would then never arm a timer at all — so the engine re-examines once a day rather
# than relying on something else to wake it. The rejected alternative was arming
# nothing: correct about the plan, and it makes a far-future single date depend on
# an unrelated schedule or a restart happening to tick the engine in time.
_IDLE_WAKE = timedelta(days=1)

# The reverse-index usages that can change a *decision*. An action target cannot:
# a light being switched off by hand does not change what the schedule will do,
# and subscribing to it would make every scheduled entity a reason to re-evaluate
# — which is §8.2's reconciliation flag, explicitly deferred to v2.
_DECIDING_USAGES = frozenset({Usage.ANCHOR, Usage.CONDITION, Usage.COMPLETION})

# Runtime record keys. Deliberately short strings rather than reused `CONF_*`
# names: this is the engine's own scratch store (D35), not the schedule schema,
# and giving it separate keys means a schema rename cannot silently invalidate
# saved runtime state.
_RT_ENGINE = "engine"
_RT_COMPLETION = "completion"
_RT_PROMISES = "promises"
_RT_NEXT_AT = "next_at"
# Enough of the schedule to describe an interval whose schedule no longer exists.
# D49's payload needs a name and an entity_id, and after a deletion there is
# nowhere else to read them from: the collection has dropped the item and D66's
# `object_id` is a fact about the stored schedule rather than about the registry.
# Written on every pass so that the copy is never staler than the last evaluation.
_RT_IDENTITY = "identity"


@dataclass(frozen=True, slots=True)
class _ExitPromise:
    """What the engine owes the world for one interval it entered.

    Captured at ENTER and persisted, because the rule may be gone by the time the
    interval ends. See the module docstring.

    `on_exit` is stored as the whole block rather than just its kind, because
    `on_exit: apply` carries a second desired state (D3) and that state is part of
    the promise: a rule edited to a different exit state must not change what a
    *currently held* interval will do on the way out, any more than editing its end
    time should move an exit the engine has already committed to.
    """

    on_exit: Mapping[str, Any]
    exit_actions: tuple[Mapping[str, Any], ...] = ()
    restore: tuple[StateSnapshot, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """JSON scalars only."""
        return {
            "on_exit": dict(self.on_exit),
            "exit_actions": [dict(action) for action in self.exit_actions],
            "restore": [snapshot.as_dict() for snapshot in self.restore],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> _ExitPromise:
        """Rebuild from the runtime store, tolerating a partial record."""
        return cls(
            on_exit=dict(data.get("on_exit") or {}),
            exit_actions=tuple(dict(action) for action in data.get("exit_actions") or ()),
            restore=tuple(
                StateSnapshot.from_dict(entry) for entry in data.get("restore") or ()
            ),
        )


def _promise_key(held: HeldInterval) -> str:
    """The runtime-store key for one held interval.

    `HeldInterval.key` is `(rule_id, start_date)`, which is the engine's identity
    for an interval and the right one here — but a JSON object key has to be a
    string, so the two halves are joined. A `|` because neither a ULID nor an ISO
    date can contain one, so the join is unambiguous without escaping.
    """
    return f"{held.rule_id}|{held.start_date.isoformat()}"


class AlmanacTick:
    """Drives every schedule, and owns the integration's only clock.

    One instance per config entry (D65 means there is only ever one entry), holding
    the timer subscription, the state-change subscriptions and the debouncer. It is
    not re-entrant: `_lock` serialises evaluation so that a state change arriving
    mid-tick queues behind it rather than interleaving two passes over the same
    runtime record — which would let the later pass write a held set the earlier one
    had already superseded.
    """

    def __init__(self, hass: HomeAssistant, data: AlmanacData) -> None:
        """Wire the tick up without starting it."""
        self._hass = hass
        self._data = data
        self._lock = asyncio.Lock()
        self._unsub_timer: CALLBACK_TYPE | None = None
        self._unsub_states: CALLBACK_TYPE | None = None
        self._unsub_resolvers: list[CALLBACK_TYPE] = []
        self._unsubs: list[CALLBACK_TYPE] = []
        self._stopping = False
        self._debouncer = Debouncer(
            hass,
            _LOGGER,
            cooldown=_STATE_SETTLE_SECONDS,
            immediate=False,
            function=self._async_settle_changed,
        )

    # --- lifecycle ---------------------------------------------------------

    async def async_start(self) -> None:
        """Begin driving schedules once Home Assistant has started.

        Deferred to `async_at_started` rather than run at setup, and the reason is
        D41: a recovery pass evaluates every `During` interval that is in force and
        applies its desired state, which needs the entities it targets to exist.
        Running during setup would reconcile against a world that is still being
        assembled and would report half of it as missing.

        `async_at_started` fires immediately if Home Assistant is already running,
        so a config entry reloaded at runtime recovers at once rather than waiting
        for a restart that will not come.
        """
        self._unsubs.append(async_at_started(self._hass, self._async_started))
        self._unsubs.append(
            self._hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._async_stop)
        )
        # A schedule edit changes what the plan says and therefore when the next
        # wake is. Re-evaluating on every change rather than diffing: the engine is
        # idempotent by construction (`EngineState.decided` is what makes it so), so
        # an unnecessary pass costs a computation and can never double-fire.
        self._unsubs.append(
            self._data.schedules.async_add_listener(self._async_collection_changed)
        )
        self._unsubs.append(
            self._data.day_sets.async_add_listener(self._async_collection_changed)
        )

    @callback
    def async_shutdown(self) -> None:
        """Drop every subscription. Called when the config entry unloads.

        Deliberately does **not** run exit paths. An unload is not a schedule
        ending: the intervals are still in force, the runtime store still records
        them, and the next setup resumes them through D90's `RESUME`. Tearing the
        world down on an unload would turn a config-entry reload into the lights
        going off.
        """
        self._stopping = True
        self._async_cancel_timer()
        if self._unsub_states is not None:
            self._unsub_states()
            self._unsub_states = None
        for unsub in self._unsub_resolvers:
            unsub()
        self._unsub_resolvers = []
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []

    async def _async_stop(self, _event: Event) -> None:
        """Stop evaluating when Home Assistant is shutting down.

        The same reasoning as `async_shutdown`: nothing is torn down, because the
        runtime store is what carries the held intervals across the restart and
        D41/D90 are what pick them up again.
        """
        self._stopping = True
        self._async_cancel_timer()

    async def _async_started(self, _hass: HomeAssistant) -> None:
        """The recovery pass (D41), and the only one that is not a tick."""
        await self.async_tick(dt_util.now(), live=False)

    # --- the clock ---------------------------------------------------------

    async def _async_clock_tick(self, now: datetime) -> None:
        """The scheduled wake. `now` comes from the timer, not from a fresh read.

        `async_track_point_in_time` hands the callback the instant it fired for,
        and using it rather than sampling the clock again keeps the evaluated
        instant equal to the instant the plan said something would happen. Sampling
        again would put the engine a few milliseconds past every boundary it was
        woken for, which for a half-open interval is the difference between
        entering and not.
        """
        self._unsub_timer = None
        await self.async_tick(now, live=True)

    async def _async_settle_changed(self) -> None:
        """The debounced re-evaluation, after a burst of state changes settled."""
        await self.async_tick(dt_util.now(), live=True)

    @callback
    def _async_state_changed(self, _event: Event[Any]) -> None:
        """A deciding entity moved; ask the debouncer for one evaluation."""
        self._debouncer.async_schedule_call()

    @callback
    def _async_invalidated(self) -> None:
        """D43's signal fired; the same debounced path as a state change."""
        self._debouncer.async_schedule_call()

    async def _async_collection_changed(
        self, _change_type: str, _item_id: str, _item: Any
    ) -> None:
        """A schedule or day set was created, edited or deleted."""
        if self._stopping:
            return
        await self.async_tick(dt_util.now(), live=True)

    # --- evaluation --------------------------------------------------------

    async def async_refresh(self, *, live: bool = True) -> None:
        """Evaluate every schedule now. The tests' and the services' way in."""
        await self.async_tick(dt_util.now(), live=live)

    async def async_tick(self, now: datetime, *, live: bool = True) -> None:
        """One pass over every schedule at one instant.

        Public, and taking `now`, because that is the shape D64 is for: every caller
        in this module happens to pass the current instant, but nothing about the
        method requires it, and the dry run is this same call at a hypothetical one.
        A private method taking an injected instant would be the same code with the
        claim unstated.

        `live` is D41's distinction, threaded straight through: a tick observed
        everything since it last ran, a recovery pass observed nothing. Nothing else
        differs, which is why there is one function rather than two.

        Every schedule is evaluated, including disabled ones. That is deliberate:
        `plan.py` turns a disabled schedule's occurrences into unarmed ones and
        `transition.py` turns an unarmed occurrence that is being held into D91's
        immediate `DISARMED` exit. Skipping disabled schedules here would be exactly
        the failure `switch.py` warns about — a switch that reads off with the lights
        still on because of it.
        """
        if self._stopping:
            return
        terminations: list[tuple[str, CompletionOutcome]] = []
        async with self._lock:
            wake: list[datetime] = []
            for schedule_id, schedule in list(self._data.schedules.data.items()):
                next_at = await self._async_evaluate_one(
                    schedule_id, schedule, now=now, live=live, terminations=terminations
                )
                if next_at is not None:
                    wake.append(next_at)
            # A.13 — `min` over aware datetimes compares them, and two instants
            # sharing a `ZoneInfo` object compare by wall clock rather than by
            # instant. Every comparison goes through `absolute`, here as the key.
            await self._async_release_orphans(now)
            self._async_schedule_wake(min(wake, key=absolute) if wake else None, now)
            self._async_resubscribe()

        # D46's *Then*, run with the lock released. It has to be outside: `disable`
        # and `delete` write through the schedule collection, core's
        # `ObservableCollection.notify_changes` **awaits** its listeners, and one of
        # those listeners is this class's own `_async_collection_changed` — so a
        # termination inside the lock would block the tick on a lock the same tick
        # holds. Running it here also means every runtime record is already saved
        # before a `then` fires, so the re-evaluation the write triggers sees the
        # state this pass decided on rather than the state it started from.
        for schedule_id, outcome in terminations:
            await self._async_terminate(schedule_id, outcome)

    async def _async_evaluate_one(
        self,
        schedule_id: str,
        schedule: dict[str, Any],
        *,
        now: datetime,
        live: bool,
        terminations: list[tuple[str, CompletionOutcome]],
    ) -> datetime | None:
        """Evaluate one schedule and carry out whatever it asks for.

        Terminations are appended to `terminations` rather than carried out, because
        the caller has to run them with the lock released — see `async_tick`.
        """
        record = dict(self._data.runtime.async_get(schedule_id))
        state = EngineState.from_dict(record.get(_RT_ENGINE) or {})
        completion = CompletionState.from_dict(record.get(_RT_COMPLETION) or {})
        promises = {
            key: _ExitPromise.from_dict(value)
            for key, value in (record.get(_RT_PROMISES) or {}).items()
        }

        planner = async_plan_tick if live else async_plan_recovery
        reconciliation = await planner(
            self._data.resolvers,
            schedule,
            state,
            now=now,
            day_sets=self._data.day_sets,
        )

        outcomes = await self._async_execute(
            schedule, reconciliation.transitions, promises
        )

        # D47's input. "Is anything still in force" is read from the state the
        # engine just returned, not from the one it started with: an interval that
        # exited on this very tick must not defer the termination it no longer
        # holds anything for.
        holding = bool(reconciliation.state.held)
        outcome = await async_settle(
            self._data.resolvers,
            self._data.day_sets,
            schedule,
            completion,
            outcomes,
            now=now,
            holding=holding,
        )

        next_at = await self._async_next_wake(schedule, reconciliation.state, now)
        self._data.runtime.async_set(
            schedule_id,
            {
                # Keys this module does not own are carried through rather than
                # dropped. Two reasons: a downgrade must not destroy a newer
                # version's runtime field, and D35's separation is a claim about
                # *whose* fields each writer may touch — replacing the whole record
                # would make the engine the only thing allowed in it.
                **record,
                _RT_ENGINE: reconciliation.state.as_dict(),
                _RT_COMPLETION: outcome.state.as_dict(),
                # Promises for intervals that are no longer held are dropped here
                # rather than when the exit runs, so that a crash between the exit
                # and the save cannot lose the promise for an interval that is still
                # holding.
                _RT_PROMISES: {
                    key: promise.as_dict()
                    for key, promise in promises.items()
                    if key in {_promise_key(held) for held in reconciliation.state.held}
                },
                _RT_NEXT_AT: next_at.isoformat() if next_at else None,
                _RT_IDENTITY: {
                    CONF_OBJECT_ID: schedule.get(CONF_OBJECT_ID),
                    CONF_NAME: schedule.get(CONF_NAME),
                },
            },
        )
        # D55's sensor, told rather than asked (D64). The same instant is written
        # to the runtime record above and published here: the record is what
        # survives a restart and what the engine reasons about, this is what the
        # entity renders, and they are set together so the two cannot drift.
        self._data.status.async_set_next_at(schedule_id, next_at)

        if outcome.then is not None:
            terminations.append((schedule_id, outcome))
        return next_at

    async def _async_release_orphans(self, now: datetime) -> None:
        """Exit the intervals of schedules that no longer exist, then forget them.

        D47 — "a schedule must not exit leaving the world in a state it created" —
        is settled for the schedule that *ends itself*, because `completion.py`
        defers the termination until nothing is held. It was not settled for the
        schedule a person deletes from the UI. The loop in `async_tick` iterates
        the surviving schedules, so a deleted one holding an interval was simply
        never evaluated again: the lights it turned on stayed on, and the exit
        promise it had every intention of honouring sat in the runtime store with
        nothing left to read it.

        Reached from the tick rather than from the collection listener, and that is
        deliberate. A listener sees the deletion once. If Home Assistant is
        restarted between the delete and the release — or the delete arrives while
        the integration is not loaded at all, which a storage-file edit does — the
        only thing that can still notice is a sweep that compares the two stores.
        The listener path would be an optimisation on a case the sweep already
        covers on the next pass.

        `ExitCause.GONE` rather than a new cause. D101's promise exists precisely
        because "the rule no longer exists" has to be survivable, and a deleted
        schedule is that same sentence one level up — a reader of the logbook is
        being told the thing that scheduled this is gone, which is what they need
        to know either way.

        Runs inside the lock. Unlike D46's *Then* it writes nothing through the
        schedule collection, so it cannot re-enter the tick through A.14's awaited
        listeners, and holding the lock is what stops a concurrent pass seeing a
        half-released record.
        """
        for schedule_id in self._data.runtime.async_ids():
            if schedule_id in self._data.schedules.data:
                continue
            record = self._data.runtime.async_get(schedule_id)
            state = EngineState.from_dict(record.get(_RT_ENGINE) or {})
            promises = {
                key: _ExitPromise.from_dict(value)
                for key, value in (record.get(_RT_PROMISES) or {}).items()
            }
            if state.held:
                identity = record.get(_RT_IDENTITY) or {}
                _LOGGER.info(
                    "almanac is releasing %d interval(s) held by deleted schedule %s",
                    len(state.held),
                    identity.get(CONF_NAME) or schedule_id,
                )
                # Through `_async_execute` rather than straight to `_async_exit`, so
                # that a released interval produces the same event, the same context
                # and the same D52 cache entry as any other exit. An exit nobody can
                # see is the half of this fix that would go wrong silently.
                await self._async_execute(
                    self._orphan_schedule(schedule_id, identity),
                    tuple(
                        self._orphan_exit(held, now) for held in sorted(
                            state.held, key=lambda h: (h.rule_id, h.start_date)
                        )
                    ),
                    promises,
                )
            self._data.runtime.async_discard(schedule_id)
            self._data.status.async_discard(schedule_id)

    @staticmethod
    def _orphan_schedule(
        schedule_id: str, identity: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Enough of a deleted schedule for D49's payload to be well formed.

        `object_id` falls back to the schedule id only for a record written before
        `_RT_IDENTITY` existed. The resulting entity_id points at nothing, which is
        the honest outcome: the alternative is a payload with no `entity_id` at all,
        and A.4 says that one does not reach the recorder through the user's entity
        filter — so the event that explains why their lights just changed would be
        the one event they cannot find.
        """
        return {
            CONF_ID: schedule_id,
            CONF_OBJECT_ID: identity.get(CONF_OBJECT_ID) or schedule_id,
            CONF_NAME: identity.get(CONF_NAME),
            CONF_RULES: (),
        }

    @staticmethod
    def _orphan_exit(held: HeldInterval, now: datetime) -> Transition:
        """The EXIT the engine would have produced had the schedule survived.

        `at` is `now` and not `held.end`: the interval is not ending because it
        reached its end, and D49's `lateness` would otherwise report a delay that
        says nothing about how promptly almanac acted.
        """
        return Transition(
            kind=TransitionKind.EXIT,
            schedule_id=held.schedule_id,
            rule_id=held.rule_id,
            start_date=held.start_date,
            at=now,
            held=held,
            cause=ExitCause.GONE,
        )

    async def _async_next_wake(
        self, schedule: dict[str, Any], state: EngineState, now: datetime
    ) -> datetime | None:
        """When this schedule next has something to do.

        **Not** `Reconciliation.next_at`, and that is the whole point of the method.
        A reconciliation enumerates *backwards*: its window ends at `now`, because
        its job is to rule on what has already happened. The only future instants
        such a plan can see are the ends of intervals already in force and
        outstanding `wait_until` deadlines — so reading the wake from it leaves every
        `At` rule with no timer at all, and a schedule that never wakes is the one
        failure mode nothing else in the system can compensate for.

        Pure in `now` like everything else below the tick (D64): the same call at a
        hypothetical instant is what the timeline will ask.
        """
        for lookahead in (_LOOKAHEAD, _BUDGET):
            plan = await async_enumerate(
                self._data.resolvers,
                schedule,
                Window(start=now, end=now + lookahead),
                day_sets=self._data.day_sets,
            )
            if (next_at := next_transition_at(plan, state, now)) is not None:
                return next_at
        return None

    # --- executing transitions --------------------------------------------

    async def _async_execute(
        self,
        schedule: Mapping[str, Any],
        transitions: Sequence[Transition],
        promises: dict[str, _ExitPromise],
    ) -> list[tuple[Transition, ExecutionReport | None]]:
        """Turn transitions into side effects, in the order they were produced.

        The order is `transition.py`'s, not this module's: `_ORDER` there puts EXIT
        before ENTER at the same instant so that back-to-back intervals hand over
        rather than overlap, and re-sorting here would undo a decision made where
        the reasoning for it is written down.

        Each transition gets a fresh `Context`. One per occurrence rather than one
        per tick, because D50's causation chain is per occurrence — "turned on by
        *Shabbat Lights*" has to name the rule that did it.

        **The order in the loop is the observability decision, and step 5's note
        here had it wrong.** That note said the D49 event would adopt this context
        as its *parent*; the verification in `events.py` shows the logbook does not
        work that way. The event is fired first, with the *same* context the
        service calls get — which is core's own arrangement in
        `components/automation/__init__.py` — because `_humanify` treats the
        earliest row carrying a context id as the cause of every later row that
        shares it. Fire the event last and the light is attributed to the
        `light.turn_on` call that changed it, which is the uninformative line this
        project exists to replace.

        The occurrence event goes out before the rule is even looked up, because it
        is a statement about the *decision*, and a decision the engine made about a
        rule that has since been edited away is still a decision it made.
        """
        rules = {str(rule[CONF_ID]): rule for rule in schedule.get(CONF_RULES, ())}
        outcomes: list[tuple[Transition, ExecutionReport | None]] = []
        for transition in transitions:
            context = Context()
            async_fire_occurrence(self._hass, schedule, transition, context=context)
            report = await self._async_execute_one(
                transition, rules.get(transition.rule_id), promises, context=context
            )
            if report is not None and report.attempted:
                # D49's per-action results, which only exist now. Skipped when
                # nothing was attempted: an occurrence that executed nothing has no
                # results, and an event saying so would be a row per tick that only
                # repeats what the occurrence event already said.
                async_fire_execution(
                    self._hass, schedule, transition, report, context=context
                )
            # D52's cache takes *every* transition, including the ones with no
            # side effects. "Why did nothing happen at 17:00" is what a person
            # opens the schedule to find out, and a cache listing only the times it
            # did something cannot answer it.
            self._data.status.async_record(transition.schedule_id, transition, report)
            outcomes.append((transition, report))
        return outcomes

    async def _async_execute_one(
        self,
        transition: Transition,
        rule: Mapping[str, Any] | None,
        promises: dict[str, _ExitPromise],
        *,
        context: Context,
    ) -> ExecutionReport | None:
        """One transition's side effects, or `None` if it has none by design.

        The context is the caller's, not one made here: D50 needs the event and
        every service call this makes to carry the *same* one. See `_async_execute`.
        """
        if transition.kind in (TransitionKind.MISSED, TransitionKind.SKIPPED):
            # Nothing to do, and that is the decision rather than an omission. D41
            # says a missed `At` occurrence is logged and not fired; D26 says a
            # skipped one is the recorded outcome of a condition that was false.
            # `transition.py` has already logged both, and step 6's event is what
            # surfaces them.
            return None

        if transition.kind is TransitionKind.FIRE:
            if rule is None:
                # The rule was edited away between enumeration and execution. There
                # is nothing owed here — unlike an interval, an `At` occurrence has
                # made no promise about a future instant.
                return None
            actions = await async_run_actions(
                self._hass, rule.get(CONF_ACTIONS, ()), context=context
            )
            return ExecutionReport(actions=actions)

        if transition.kind is TransitionKind.ENTER:
            assert transition.held is not None
            if rule is None:
                return None
            desired = rule.get(CONF_STATE)
            # Captured *before* the state is applied, which is the only order that
            # works, and recorded alongside the rule's exit behaviour so that D3's
            # restore survives the rule being edited away (`ExitCause.GONE`).
            promises[_promise_key(transition.held)] = _ExitPromise(
                on_exit=dict(rule.get(CONF_ON_EXIT) or {}),
                exit_actions=tuple(rule.get(CONF_EXIT_ACTIONS, ())),
                restore=(
                    capture_state(self._hass, desired) if desired is not None else ()
                ),
            )
            state = (
                await async_apply_state(self._hass, desired, context=context)
                if desired is not None
                else ()
            )
            # State first, then the announcement. D5's own example is "set the
            # lights *and* announce", and the announcement that the lights are on
            # should not precede them being on. D32 is what makes the order safe to
            # fix: a failed announcement does not stop the state, so there is no
            # reason to run it first as insurance.
            actions = await async_run_actions(
                self._hass, rule.get(CONF_ENTER_ACTIONS, ()), context=context
            )
            return ExecutionReport(actions=actions, state=tuple(state))

        if transition.kind is TransitionKind.RESUME:
            # D90 — re-apply the desired state and **do not** re-run the enter
            # actions. A consumer that treated RESUME as ENTER would re-send an IR
            # burst and re-dim lights someone had since adjusted by hand, once per
            # Home Assistant restart, for no scheduled reason. The restore snapshot
            # is not retaken either: what the world looked like before the interval
            # began is a fact about the past, and the current state is the interval's
            # own handiwork.
            if rule is None or (desired := rule.get(CONF_STATE)) is None:
                return None
            state = await async_apply_state(self._hass, desired, context=context)
            return ExecutionReport(state=tuple(state))

        if transition.kind is TransitionKind.EXIT:
            assert transition.held is not None
            return await self._async_exit(transition, promises, context=context)

        _LOGGER.warning("almanac does not know how to execute %s", transition.kind)
        return None

    async def _async_exit(
        self,
        transition: Transition,
        promises: dict[str, _ExitPromise],
        *,
        context: Context,
    ) -> ExecutionReport:
        """Run an interval's exit path from the promise made when it was entered.

        Read from the promise rather than from the rule in every case, not just the
        `GONE` one. Two reasons, and the second is the stronger: reading the rule
        would make the exit behaviour of a *currently held* interval change when the
        rule is edited, which is the same class of surprise D47 forbids for
        completion; and a single path means the `GONE` case is not a special branch
        that only runs when something has already gone wrong.
        """
        assert transition.held is not None
        key = _promise_key(transition.held)
        promise = promises.get(key)
        if promise is None:
            # An interval held across an upgrade that predates the promise store, or
            # a runtime record that was lost. `leave` is the only defensible
            # reading: with no snapshot there is nothing to restore, and inventing a
            # state to apply would be worse than leaving the world as it is.
            _LOGGER.warning(
                "almanac has no exit promise for %s; leaving the world as it is", key
            )
            return ExecutionReport()

        kind = str(promise.on_exit.get(CONF_KIND) or "")
        state: tuple[ActionResult, ...] = ()
        if kind == ON_EXIT_RESTORE:
            state = await async_restore(self._hass, promise.restore, context=context)
        elif kind == ON_EXIT_APPLY:
            state = await async_apply_state(
                self._hass, promise.on_exit[CONF_STATE], context=context
            )
        # `leave` does nothing, and does it explicitly: D3 makes the third value a
        # choice the user made, not the absence of one.

        actions = await async_run_actions(
            self._hass, promise.exit_actions, context=context
        )
        return ExecutionReport(actions=actions, state=state)

    # --- completion -------------------------------------------------------

    async def _async_terminate(
        self,
        schedule_id: str,
        outcome: CompletionOutcome,
    ) -> None:
        """Carry out D46's *Then*, having been told it is safe to (D47).

        Reached only when `completion.py` returned a `then`, which it does only when
        nothing is held. So this never has to decide whether it is mid-interval —
        that decision lives in one place, and it is not here.

        Called by `async_tick` with the lock released, which is why the schedule is
        looked up again here rather than passed in: between the pass that decided to
        terminate and this call, somebody may have deleted it from the UI.
        """
        assert outcome.then is not None
        kind = str(outcome.then[CONF_KIND])
        if then_is_a_no_op(outcome.then):
            return
        if kind in (THEN_DISABLE, THEN_DELETE) and schedule_id not in (
            self._data.schedules.data
        ):
            return
        if kind == THEN_ACTION:
            await async_run_actions(
                self._hass, outcome.then[CONF_ACTIONS], context=Context()
            )
            return
        if kind == THEN_DISABLE:
            # Through the collection, like `switch.py`: it is the one write path
            # (D33), it persists, and it reaches the switch entity and any open
            # editor. Setting a flag here would leave the switch reading *on*.
            await self._data.schedules.async_update_item(
                schedule_id, {CONF_ENABLED: False}
            )
            return
        if kind == THEN_DELETE:
            await self._data.schedules.async_delete_item(schedule_id)
            return
        _LOGGER.warning("almanac does not understand then kind %r", kind)

    # --- waking up --------------------------------------------------------

    @callback
    def _async_cancel_timer(self) -> None:
        """Drop the pending wake, if there is one."""
        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None

    @callback
    def _async_schedule_wake(self, next_at: datetime | None, now: datetime) -> None:
        """Arm one timer for the earliest thing any schedule has to do.

        One timer for the whole integration rather than one per schedule: the
        instants come from `next_transition_at`, which is pure, so recomputing the
        minimum on every pass costs nothing and there is no per-schedule timer to
        leak when a schedule is deleted.
        """
        self._async_cancel_timer()
        if self._stopping:
            return
        if next_at is None:
            # Nothing inside D44's budget — see `_IDLE_WAKE`.
            next_at = now + _IDLE_WAKE
        # Clamped forward — see `_MIN_WAKE`.
        when = next_at if absolute(next_at) > absolute(now) else now + _MIN_WAKE
        self._unsub_timer = async_track_point_in_time(
            self._hass, self._async_clock_tick, when
        )

    @callback
    def _async_resubscribe(self) -> None:
        """Rebuild the state-change and invalidation subscriptions.

        Rebuilt wholesale on every pass rather than diffed, for the reason
        `build_index`'s docstring gives about the index itself: the structure is
        small, and an incrementally maintained subscription set is a
        cache-invalidation bug waiting for its first rename.
        """
        if self._unsub_states is not None:
            self._unsub_states()
            self._unsub_states = None
        for unsub in self._unsub_resolvers:
            unsub()
        self._unsub_resolvers = []
        if self._stopping:
            return

        index = build_index(self._data.schedules.data, self._data.day_sets.data)
        watched = sorted(
            entity_id
            for entity_id, references in index.entity_users.items()
            if any(reference.usage in _DECIDING_USAGES for reference in references)
        )
        if watched:
            self._unsub_states = async_track_state_change_event(
                self._hass, watched, self._async_state_changed
            )

        # D43 — a resolver anchor names a domain and a key, not an entity, so the
        # reverse index cannot carry it (see `_anchor_references`). The registry
        # knows what has to change for its answers to change, and `async_subscribe`
        # is a no-op for a key that does not resolve, so there is nothing to branch
        # on here.
        for domain, key in _resolver_anchors(self._data.schedules.data):
            self._unsub_resolvers.append(
                self._data.resolvers.async_subscribe(domain, key, self._async_invalidated)
            )

    # --- D45 ---------------------------------------------------------------

    async def async_run_now(
        self,
        *,
        schedule_id: str,
        rule_id: str | None = None,
        bypass_conditions: bool = False,
        context: Context | None = None,
    ) -> None:
        """Fire a schedule or one of its rules now — D45.

        `context` is the caller's, so the execution is logged with the invoking
        `Context` "like any other" (D45, §12) and the logbook says who asked.

        **What this does to a `During` rule is a provisional reading**, because
        D45 does not say. When an occurrence of the rule is in force at `now`, the
        interval is entered properly: the state is applied, the enter actions run,
        and the interval is recorded in `EngineState.held` so the engine owes the
        exit at the occurrence's own end. When no occurrence is in force there is no
        end to owe an exit at, so only the enter actions run and the state is left
        alone. The rejected alternatives are (a) applying desired state with no
        recorded end, which is the failure D47 exists to prevent — a state the
        engine created and nothing claims responsibility for; and (b) refusing
        `run_now` on `During` rules, which removes the testing case the service was
        added for.
        """
        schedule = self._data.schedules.data.get(schedule_id)
        if schedule is None:
            raise ServiceValidationError(f"no schedule with id {schedule_id!r}")

        rules = {str(rule[CONF_ID]): rule for rule in schedule.get(CONF_RULES, ())}
        if rule_id is not None:
            if rule_id not in rules:
                raise ServiceValidationError(
                    f"schedule {schedule_id!r} has no rule with id {rule_id!r}"
                )
            chosen = [rules[rule_id]]
        else:
            # Every rule, including disabled ones. `run_now` is an explicit
            # instruction naming this schedule, so filtering on `enabled` would make
            # the service silently do nothing for the case it is most used in —
            # testing a rule before arming it.
            chosen = list(rules.values())

        now = dt_util.now()
        async with self._lock:
            for rule in chosen:
                await self._async_run_one_now(
                    schedule_id,
                    schedule,
                    rule,
                    now=now,
                    bypass_conditions=bypass_conditions,
                    context=context or Context(),
                )
            # The manual entry above may have added a held interval, so the next
            # wake has to be recomputed — otherwise the exit it owes would not be
            # scheduled until something else happened to wake the engine.
            await self._async_evaluate_unlocked(now)

    async def _async_evaluate_unlocked(self, now: datetime) -> None:
        """Recompute wakes without taking the lock. Callers already hold it."""
        wake: list[datetime] = []
        for schedule_id, schedule in list(self._data.schedules.data.items()):
            record = self._data.runtime.async_get(schedule_id)
            state = EngineState.from_dict(record.get(_RT_ENGINE) or {})
            if (next_at := await self._async_next_wake(schedule, state, now)) is not None:
                wake.append(next_at)
        self._async_schedule_wake(min(wake, key=absolute) if wake else None, now)

    async def _async_run_one_now(
        self,
        schedule_id: str,
        schedule: Mapping[str, Any],
        rule: Mapping[str, Any],
        *,
        now: datetime,
        bypass_conditions: bool,
        context: Context,
    ) -> None:
        """Fire one rule out of band. See `async_run_now` for the reasoning."""
        rule_id = str(rule[CONF_ID])
        if not bypass_conditions:
            outcome = await async_evaluate(
                self._data.resolvers,
                self._data.day_sets,
                rule.get(CONF_CONDITIONS, ()),
                now=now,
            )
            if not outcome.passed:
                # Logged rather than raised. D45's flag exists so that a user who
                # meant to ignore conditions says so; a user who did not gets the
                # rule's own answer, and an exception would make "the conditions
                # were false" look like a broken service call.
                _LOGGER.info(
                    "almanac run_now skipped rule %s: %s", rule_id, outcome.summary
                )
                return

        if rule.get(CONF_KIND) == RULE_AT:
            await async_run_actions(
                self._hass, rule.get(CONF_ACTIONS, ()), context=context
            )
            return

        record = dict(self._data.runtime.async_get(schedule_id))
        state = EngineState.from_dict(record.get(_RT_ENGINE) or {})
        promises = {
            key: _ExitPromise.from_dict(value)
            for key, value in (record.get(_RT_PROMISES) or {}).items()
        }

        plan = await async_enumerate(
            self._data.resolvers, schedule, _around(now), day_sets=self._data.day_sets
        )
        holding = next(
            (
                occ
                for occ in plan.occurrences
                if occ.rule_id == rule_id and occ.is_interval and occ.holds_at(now)
            ),
            None,
        )
        desired = rule.get(CONF_STATE)

        if holding is None:
            # No occurrence in force, so no end to owe an exit at. Enter actions
            # only — see `async_run_now`'s docstring for why the state is left
            # alone.
            _LOGGER.info(
                "almanac run_now: rule %s has no interval in force, running its "
                "enter actions only and leaving state alone",
                rule_id,
            )
            await async_run_actions(
                self._hass, rule.get(CONF_ENTER_ACTIONS, ()), context=context
            )
            return

        assert holding.start is not None and holding.end is not None
        entered = HeldInterval(
            schedule_id=schedule_id,
            rule_id=rule_id,
            start_date=holding.start_date,
            start=holding.start,
            end=holding.end,
            entered_at=now,
        )
        key = _promise_key(entered)
        already = key in {_promise_key(held) for held in state.held}

        if not already:
            promises[key] = _ExitPromise(
                on_exit=dict(rule.get(CONF_ON_EXIT) or {}),
                exit_actions=tuple(rule.get(CONF_EXIT_ACTIONS, ())),
                restore=capture_state(self._hass, desired) if desired is not None else (),
            )

        if desired is not None:
            await async_apply_state(self._hass, desired, context=context)
        await async_run_actions(
            self._hass, rule.get(CONF_ENTER_ACTIONS, ()), context=context
        )

        if already:
            # Nothing to record: the engine already owes this exit, and overwriting
            # the promise would discard the snapshot taken before the interval first
            # touched the world.
            return

        self._data.runtime.async_set(
            schedule_id,
            {
                **record,
                _RT_ENGINE: EngineState(
                    held=(*state.held, entered),
                    decided=state.decided,
                    waiting=state.waiting,
                    evaluated_through=state.evaluated_through,
                ).as_dict(),
                _RT_PROMISES: {
                    name: promise.as_dict() for name, promise in promises.items()
                },
            },
        )


def _around(now: datetime) -> Window:
    """A one-day window either side of `now`, for a single-occurrence lookup.

    Wide enough to contain any interval that could be in force: D39 refuses
    overlapping occurrences of one rule at save time, so an interval holding at
    `now` began no earlier than the previous occurrence of the same rule, and a day
    covers every recurrence the model can express. Narrow enough that the lookup
    costs one day of enumeration rather than D44's ninety.
    """
    return Window(start=now - timedelta(days=1), end=now + timedelta(days=1))


def _resolver_anchors(
    schedules: Mapping[str, dict[str, Any]],
) -> set[tuple[str, str]]:
    """Every `(domain, key)` a resolver anchor names, across every schedule.

    A set, because one subscription per key is enough however many schedules use
    it — the callback re-evaluates all of them anyway.
    """
    found: set[tuple[str, str]] = set()
    for schedule in schedules.values():
        for rule in schedule.get(CONF_RULES, ()):
            for anchor in _rule_anchors(rule):
                if anchor.get(CONF_KIND) == ANCHOR_RESOLVER:
                    found.add((str(anchor[CONF_DOMAIN]), str(anchor[CONF_KEY])))
    return found


def _rule_anchors(rule: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """A rule's anchors, whichever shape it is.

    The same walk `index._anchors` makes, and for the same reason it is a walk
    rather than a branch on the rule kind: a rule shape added later would silently
    stop being subscribed to, and a schedule that quietly never wakes is worse than
    one that errors.
    """
    anchors = [
        anchor
        for key in (CONF_ANCHOR, CONF_START_ANCHOR)
        if isinstance(anchor := rule.get(key), dict)
    ]
    end = rule.get(CONF_END) or {}
    if end.get(CONF_KIND) == END_ANCHOR:
        anchors.append(end[CONF_ANCHOR])
    return anchors


# --- the service (D45) -----------------------------------------------------


@callback
def async_register_services(hass: HomeAssistant, tick: AlmanacTick) -> None:
    """Register `almanac.run_now`.

    One service, with D45's flag on it rather than a second service name. Registered
    against the config entry's tick because D65 means there is exactly one — if that
    ever changes, this is the line that has to grow a lookup.
    """

    async def _async_run_now(call: Any) -> None:
        """Resolve the service call's reference and hand it to the tick."""
        schedule_ids = set()
        if (schedule_id := call.data.get(ATTR_SCHEDULE_ID)) is not None:
            schedule_ids.add(schedule_id)
        for entity_id in call.data.get("entity_id", ()):
            # A schedule's switch and sensor both carry the collection item id as
            # their `unique_id` (see `entity.py`), so the registry is the mapping
            # from the entity a user can see to the id the engine uses. Offered
            # because "run this schedule now" is a thing said about the entity in
            # front of you, not about a ULID.
            entry = er.async_get(hass).async_get(entity_id)
            if entry is None or entry.platform != DOMAIN:
                raise ServiceValidationError(f"{entity_id} is not an almanac entity")
            schedule_ids.add(entry.unique_id)
        if not schedule_ids:
            raise ServiceValidationError(
                "run_now needs a schedule_id or an entity_id to act on"
            )
        rule_id = call.data.get(ATTR_RULE_ID)
        if rule_id is not None and len(schedule_ids) > 1:
            raise ServiceValidationError(
                "rule_id names a rule inside one schedule, so only one schedule "
                "may be given with it"
            )
        for resolved in sorted(schedule_ids):
            await tick.async_run_now(
                schedule_id=resolved,
                rule_id=rule_id,
                bypass_conditions=call.data[ATTR_BYPASS_CONDITIONS],
                context=call.context,
            )

    hass.services.async_register(DOMAIN, SERVICE_RUN_NOW, _async_run_now, RUN_NOW_SCHEMA)


__all__ = [
    "RUN_NOW_SCHEMA",
    "SERVICE_RUN_NOW",
    "AlmanacTick",
    "async_register_services",
]
