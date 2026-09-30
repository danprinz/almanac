"""Completion — D46, D47 and D83, at written-down instants.

D46's three axes are independent, so they are tested independently: what counts as
an occurrence, when a schedule is finished, and what happens then. The one place
they interact is D47 — **termination never takes effect mid-interval** — and that
has its own section, because it is the decision with a failure mode attached: a
schedule that disables itself while holding the lights on leaves them on.

Every assertion names its `now`, for D64's reason rather than for tidiness: the
date axis and the condition axis are both evaluated at an instant, and neither can
be tested at all by code that reads the wall clock internally.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from homeassistant.core import HomeAssistant

from custom_components.almanac.actions import ActionResult, ActionStatus, ExecutionReport
from custom_components.almanac.completion import (
    CompletionState,
    async_settle,
    then_is_a_no_op,
)
from custom_components.almanac.const import (
    ANCHOR_CLOCK,
    CONDITION_COMPARISON,
    COUNT_ON_ACTIONS_SUCCEEDED,
    COUNT_ON_CONDITIONS_PASSED,
    COUNT_ON_SCHEDULED,
    OPERAND_CONSTANT,
    RECUR_WEEKDAYS,
    RULE_AT,
)
from custom_components.almanac.engine import HeldInterval, TransitionKind
from custom_components.almanac.engine.transition import Transition
from custom_components.almanac.resolver import ResolverRegistry, async_create_registry
from custom_components.almanac.schema import STORAGE_SCHEMA

NY = ZoneInfo("America/New_York")


# --- helpers ---------------------------------------------------------------


def ny(*args: int) -> datetime:
    """A New York instant, written out."""
    return datetime(*args, tzinfo=NY)  # type: ignore[arg-type]


def daily() -> dict[str, Any]:
    """Every day."""
    return {"kind": RECUR_WEEKDAYS, "weekdays": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]}


def at_rule(rule_id: str = "r1", at: str = "17:00", **extra: Any) -> dict[str, Any]:
    """An `At` rule with a clock anchor — the shape none of these tests vary."""
    return {
        "kind": RULE_AT,
        "id": rule_id,
        "anchor": {"kind": ANCHOR_CLOCK, "at": at},
        **extra,
    }


def schedule(**body: Any) -> dict[str, Any]:
    """A stored schedule, validated, so the completion defaults are the real ones."""
    return dict(
        STORAGE_SCHEMA(
            {
                "id": "sched",
                "name": "Test schedule",
                "object_id": "test_schedule",
                "recurrence": daily(),
                "rules": [at_rule()],
                **body,
            }
        )
    )


def is_on(entity_id: str) -> dict[str, Any]:
    """A comparison condition — "this entity is on" — as the store holds it."""
    return {
        "kind": CONDITION_COMPARISON,
        "entity_id": entity_id,
        "operator": "eq",
        "value": {"kind": OPERAND_CONSTANT, "value": "on"},
    }


def fire(rule_id: str = "r1", at: datetime | None = None) -> Transition:
    """A FIRE transition for one rule."""
    return Transition(
        kind=TransitionKind.FIRE,
        schedule_id="sched",
        rule_id=rule_id,
        start_date=date(2026, 10, 2),
        at=at or ny(2026, 10, 2, 17, 0),
    )


def skipped(rule_id: str = "r1") -> Transition:
    """A SKIPPED transition — D26's recorded "did not run because"."""
    return Transition(
        kind=TransitionKind.SKIPPED,
        schedule_id="sched",
        rule_id=rule_id,
        start_date=date(2026, 10, 2),
        at=ny(2026, 10, 2, 17, 0),
    )


def exited(rule_id: str = "r1") -> Transition:
    """An EXIT transition, which never counts on any axis: an interval's occurrence
    was already counted when it was entered, and counting the exit would double it."""
    held = HeldInterval(
        schedule_id="sched",
        rule_id=rule_id,
        start_date=date(2026, 10, 2),
        start=ny(2026, 10, 2, 17, 0),
        end=ny(2026, 10, 2, 18, 0),
        entered_at=ny(2026, 10, 2, 17, 0),
    )
    return Transition(
        kind=TransitionKind.EXIT,
        schedule_id="sched",
        rule_id=rule_id,
        start_date=date(2026, 10, 2),
        at=ny(2026, 10, 2, 18, 0),
        held=held,
    )


def ok_report() -> ExecutionReport:
    """A report whose one action succeeded."""
    return ExecutionReport(
        actions=(
            ActionResult(
                index=0, kind="service", target="light.turn_on", status=ActionStatus.SUCCEEDED
            ),
        )
    )


def failed_report() -> ExecutionReport:
    """A report whose one action failed."""
    return ExecutionReport(
        actions=(
            ActionResult(
                index=0, kind="service", target="light.turn_on", status=ActionStatus.FAILED
            ),
        )
    )


@pytest.fixture(autouse=True)
async def _located(hass: HomeAssistant) -> None:
    """New York, which is the only thing completion reads from `hass`."""
    await hass.config.async_set_time_zone("America/New_York")


@pytest.fixture
def resolvers(hass: HomeAssistant) -> ResolverRegistry:
    """The in-tree resolvers, wired as the config entry wires them."""
    return async_create_registry(hass)


async def settle(
    resolvers: ResolverRegistry,
    sched: dict[str, Any],
    state: CompletionState,
    outcomes: list[tuple[Transition, ExecutionReport | None]],
    *,
    now: datetime,
    holding: bool = False,
) -> Any:
    """`async_settle` with the day-set lookup the tests do not vary."""
    return await async_settle(
        resolvers, None, sched, state, outcomes, now=now, holding=holding
    )


# --- D83: `never` is the default -------------------------------------------


async def test_a_schedule_with_no_completion_block_never_finishes(
    resolvers: ResolverRegistry,
) -> None:
    """D83 — most schedules are perpetual, so this is the default rather than a
    missing block. The counter still counts, because the timeline reads it."""
    sched = schedule()

    outcome = await settle(
        resolvers, sched, CompletionState(), [(fire(), ok_report())], now=ny(2026, 10, 2, 17, 0)
    )

    assert outcome.state.count == 1
    assert not outcome.finished
    assert outcome.then is None


def test_keep_running_is_a_no_op() -> None:
    """`keep` is a real value on the *Then* axis, not the absence of one, so it
    reaches the caller as a block and this is what says to do nothing with it."""
    assert then_is_a_no_op({"kind": "keep"})
    assert not then_is_a_no_op({"kind": "disable"})


# --- D46, first axis: what counts -----------------------------------------


async def test_scheduled_counts_a_skipped_occurrence(
    resolvers: ResolverRegistry,
) -> None:
    """"Every time it was scheduled" includes the ones the conditions refused.
    That is the axis's whole distinction from the other two."""
    sched = schedule(completion={"count_on": COUNT_ON_SCHEDULED})

    outcome = await settle(
        resolvers, sched, CompletionState(), [(skipped(), None)], now=ny(2026, 10, 2, 17, 0)
    )

    assert outcome.state.count == 1


async def test_conditions_passed_does_not_count_a_skip(
    resolvers: ResolverRegistry,
) -> None:
    """The second axis. A skipped occurrence is one whose conditions did not pass,
    by construction."""
    sched = schedule(completion={"count_on": COUNT_ON_CONDITIONS_PASSED})

    outcome = await settle(
        resolvers, sched, CompletionState(), [(skipped(), None)], now=ny(2026, 10, 2, 17, 0)
    )

    assert outcome.state.count == 0


async def test_actions_succeeded_does_not_count_a_failure(
    resolvers: ResolverRegistry,
) -> None:
    """The third axis — the one today's model cannot express at all, and what
    "delete after it triggers" actually has to mean."""
    sched = schedule(completion={"count_on": COUNT_ON_ACTIONS_SUCCEEDED})

    outcome = await settle(
        resolvers,
        sched,
        CompletionState(),
        [(fire(), failed_report())],
        now=ny(2026, 10, 2, 17, 0),
    )

    assert outcome.state.count == 0


async def test_actions_succeeded_does_not_count_an_occurrence_with_no_actions(
    resolvers: ResolverRegistry,
) -> None:
    """A rule with nothing to do has not succeeded at anything. Stricter than it
    looks, and deliberate: this axis exists so the count means "did the thing"."""
    sched = schedule(completion={"count_on": COUNT_ON_ACTIONS_SUCCEEDED})

    outcome = await settle(
        resolvers,
        sched,
        CompletionState(),
        [(fire(), ExecutionReport())],
        now=ny(2026, 10, 2, 17, 0),
    )

    assert outcome.state.count == 0


async def test_an_exit_never_counts(resolvers: ResolverRegistry) -> None:
    """An interval is one occurrence, counted when it was entered. Counting the
    exit too would make every `During` rule worth two."""
    sched = schedule(completion={"count_on": COUNT_ON_SCHEDULED})

    outcome = await settle(
        resolvers, sched, CompletionState(), [(exited(), None)], now=ny(2026, 10, 2, 18, 0)
    )

    assert outcome.state.count == 0


# --- D46, second axis: finished when --------------------------------------


async def test_one_rule_fired_finishes_after_the_first_rule(
    resolvers: ResolverRegistry,
) -> None:
    """The one-shot case — "at 17:00 tomorrow, then forget it"."""
    sched = schedule(completion={"finished_when": {"kind": "one_rule_fired"}})

    outcome = await settle(
        resolvers, sched, CompletionState(), [(fire(), ok_report())], now=ny(2026, 10, 2, 17, 0)
    )

    assert outcome.finished
    assert outcome.state.fired_rules == ("r1",)


async def test_a_cycle_waits_for_every_enabled_rule(
    resolvers: ResolverRegistry,
) -> None:
    """"Once every rule has fired" — the shape a multi-slot scheme needs, and the
    one the current scheduler leaves undocumented."""
    sched = schedule(
        rules=[at_rule("r1", "07:00"), at_rule("r2", "22:00")],
        completion={"finished_when": {"kind": "cycle"}},
    )

    after_first = await settle(
        resolvers,
        sched,
        CompletionState(),
        [(fire("r1"), ok_report())],
        now=ny(2026, 10, 2, 7, 0),
    )
    assert not after_first.finished

    after_second = await settle(
        resolvers,
        sched,
        after_first.state,
        [(fire("r2"), ok_report())],
        now=ny(2026, 10, 2, 22, 0),
    )
    assert after_second.finished


async def test_a_cycle_ignores_a_disabled_rule(resolvers: ResolverRegistry) -> None:
    """D77 — a cycle that waited for a rule the user had switched off would never
    complete, which is the same reading D91 takes of a disarmed rule."""
    sched = schedule(
        rules=[at_rule("r1", "07:00"), at_rule("r2", "22:00", enabled=False)],
        completion={"finished_when": {"kind": "cycle"}},
    )

    outcome = await settle(
        resolvers,
        sched,
        CompletionState(),
        [(fire("r1"), ok_report())],
        now=ny(2026, 10, 2, 7, 0),
    )

    assert outcome.finished


async def test_a_schedule_with_no_enabled_rules_has_not_completed_a_cycle(
    resolvers: ResolverRegistry,
) -> None:
    """It has nothing to complete. Finishing here would delete a schedule the
    moment its last rule was switched off."""
    sched = schedule(
        rules=[at_rule("r1", enabled=False)],
        completion={"finished_when": {"kind": "cycle"}},
    )

    outcome = await settle(resolvers, sched, CompletionState(), [], now=ny(2026, 10, 2, 7, 0))

    assert not outcome.finished


async def test_occurrences_finishes_on_the_nth(resolvers: ResolverRegistry) -> None:
    """"After N times", counted on whichever axis the third field named."""
    sched = schedule(
        completion={"finished_when": {"kind": "occurrences", "count": 2}}
    )

    first = await settle(
        resolvers, sched, CompletionState(), [(fire(), ok_report())], now=ny(2026, 10, 2, 17, 0)
    )
    assert not first.finished

    second = await settle(
        resolvers, sched, first.state, [(fire(), ok_report())], now=ny(2026, 10, 3, 17, 0)
    )
    assert second.finished
    assert second.state.count == 2


async def test_a_date_is_inclusive_of_the_named_day(
    resolvers: ResolverRegistry,
) -> None:
    """D96 applied to this axis: a person who types an end date means the day they
    named, so "finish on 31 December" finishes on 1 January."""
    sched = schedule(
        completion={"finished_when": {"kind": "date", "date": "2026-12-31"}}
    )

    on_the_day = await settle(
        resolvers, sched, CompletionState(), [], now=ny(2026, 12, 31, 23, 30)
    )
    assert not on_the_day.finished

    the_day_after = await settle(
        resolvers, sched, CompletionState(), [], now=ny(2027, 1, 1, 0, 30)
    )
    assert the_day_after.finished


async def test_the_date_axis_is_read_in_the_registry_zone(
    resolvers: ResolverRegistry,
) -> None:
    """01:00 UTC on 1 January is still 31 December in New York, and the schedule
    belongs to the person's civil day, not to UTC."""
    sched = schedule(
        completion={"finished_when": {"kind": "date", "date": "2026-12-31"}}
    )

    outcome = await settle(
        resolvers,
        sched,
        CompletionState(),
        [],
        now=datetime(2027, 1, 1, 1, 0, tzinfo=ZoneInfo("UTC")),
    )

    assert not outcome.finished


async def test_a_completion_condition_finishes_when_it_becomes_true(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """The axis upstream has no equivalent for — "until the boiler is serviced"."""
    hass.states.async_set("input_boolean.serviced", "on")
    sched = schedule(
        completion={
            "finished_when": {
                "kind": "condition",
                "conditions": [is_on("input_boolean.serviced")],
            }
        }
    )

    outcome = await settle(resolvers, sched, CompletionState(), [], now=ny(2026, 10, 2, 17, 0))

    assert outcome.finished


async def test_an_undetermined_completion_condition_changes_nothing(
    resolvers: ResolverRegistry,
) -> None:
    """D97 — the entity does not exist, so the predicate is not answered. It must
    not finish the schedule, and it must not be recorded as "not finished" either;
    the next tick asks again."""
    sched = schedule(
        completion={
            "finished_when": {
                "kind": "condition",
                "conditions": [is_on("input_boolean.gone")],
            }
        }
    )

    outcome = await settle(resolvers, sched, CompletionState(), [], now=ny(2026, 10, 2, 17, 0))

    assert not outcome.finished
    assert outcome.state.finished_at is None


# --- D47: termination never takes effect mid-interval ----------------------


async def test_a_finished_schedule_that_is_still_holding_defers(
    resolvers: ResolverRegistry,
) -> None:
    """D47's whole point. `then` is withheld, and `finished_at` is stamped so the
    audit trail shows when the condition was actually met."""
    sched = schedule(
        completion={"finished_when": {"kind": "one_rule_fired"}, "then": {"kind": "delete"}}
    )

    outcome = await settle(
        resolvers,
        sched,
        CompletionState(),
        [(fire(), ok_report())],
        now=ny(2026, 10, 2, 17, 0),
        holding=True,
    )

    assert outcome.finished
    assert outcome.deferred
    assert outcome.then is None
    assert outcome.state.finished_at == ny(2026, 10, 2, 17, 0)
    assert not outcome.state.settled


async def test_the_deferred_termination_happens_once_the_interval_ends(
    resolvers: ResolverRegistry,
) -> None:
    """The second half of the same story, and the tick's `holding` is read from the
    state the engine just returned so that an interval which exited on this very
    tick no longer defers."""
    sched = schedule(
        completion={"finished_when": {"kind": "one_rule_fired"}, "then": {"kind": "delete"}}
    )
    deferred = await settle(
        resolvers,
        sched,
        CompletionState(),
        [(fire(), ok_report())],
        now=ny(2026, 10, 2, 17, 0),
        holding=True,
    )

    outcome = await settle(
        resolvers, sched, deferred.state, [(exited(), None)], now=ny(2026, 10, 2, 18, 0)
    )

    assert outcome.then == {"kind": "delete"}
    assert outcome.state.settled


async def test_finished_at_is_stamped_once_and_not_re_evaluated(
    resolvers: ResolverRegistry,
) -> None:
    """A condition that became true and then false again while D47 held the
    termination back has still *become* true, which is what the axis says."""
    sched = schedule(
        completion={"finished_when": {"kind": "one_rule_fired"}, "then": {"kind": "disable"}}
    )
    first = await settle(
        resolvers,
        sched,
        CompletionState(),
        [(fire(), ok_report())],
        now=ny(2026, 10, 2, 17, 0),
        holding=True,
    )

    second = await settle(
        resolvers, sched, first.state, [], now=ny(2026, 10, 2, 17, 30), holding=True
    )

    assert second.state.finished_at == ny(2026, 10, 2, 17, 0)


async def test_a_settled_schedule_is_never_terminated_twice(
    resolvers: ResolverRegistry,
) -> None:
    """A caller that acts on whatever it is handed must not be able to delete the
    same schedule twice, so `then` is returned on exactly one tick."""
    sched = schedule(
        completion={"finished_when": {"kind": "one_rule_fired"}, "then": {"kind": "delete"}}
    )
    settled = await settle(
        resolvers, sched, CompletionState(), [(fire(), ok_report())], now=ny(2026, 10, 2, 17, 0)
    )
    assert settled.then is not None

    again = await settle(
        resolvers, sched, settled.state, [(fire(), ok_report())], now=ny(2026, 10, 3, 17, 0)
    )

    assert again.then is None
    assert again.state.count == 2


# --- persistence ----------------------------------------------------------


def test_completion_state_round_trips_through_the_runtime_store() -> None:
    """It lives in the runtime store (D35), so it has to be JSON scalars."""
    state = CompletionState(
        count=3,
        fired_rules=("r1", "r2"),
        finished_at=ny(2026, 10, 2, 17, 0),
        settled=True,
    )

    assert CompletionState.from_dict(state.as_dict()) == state


def test_completion_state_tolerates_an_empty_record() -> None:
    """The first tick for a schedule reads a record that does not exist yet, and a
    record written by an older version may be missing fields this one added."""
    assert CompletionState.from_dict({}) == CompletionState()
