"""The tick — D64's one clock reader, and the consumer that fires things.

`engine/transition.py` decides *what should happen* and deliberately does nothing
about it; `actions.py` changes the world but is told exactly what to change.
`tick.py` is the seam, and almost everything that can go wrong here is a matter of
**order** rather than of arithmetic:

- desired state before the enter actions (D5), so "the lights are on" is not
  announced before they are;
- EXIT before ENTER at the same instant (`_ORDER` in `transition.py`), so
  back-to-back intervals hand over rather than overlap;
- the exit promise written *before* the state is applied, so D3's `restore` has
  something to restore to;
- D46's *Then* after the runtime record is saved, and outside the lock.

So the service calls are recorded into **one** list, not one per service: separate
recorders cannot assert an ordering, and the ordering is the deliverable.

Every tick is driven at a written-down instant, which is the whole point of D64 —
`tick.async_tick(now)` is public and takes the instant precisely so that the dry
run and these tests are the same call the live engine makes. The schedules sit in a
2027 date window so that the automatic evaluation a collection write triggers (at
the real wall clock) is a no-op, and every assertion is about a tick this file
asked for. The exceptions are the `run_now` tests, which go through the service and
therefore read the real clock: those use rules whose behaviour does not depend on
the instant, and say so.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from custom_components.almanac.const import (
    ANCHOR_CLOCK,
    CONDITION_COMPARISON,
    DOMAIN,
    END_DURATION,
    FINISHED_ONE_RULE_FIRED,
    ON_EXIT_APPLY,
    ON_EXIT_LEAVE,
    ON_EXIT_RESTORE,
    OPERAND_CONSTANT,
    RECUR_WEEKDAYS,
    RULE_AT,
    RULE_DURING,
    THEN_ACTION,
    THEN_DELETE,
    THEN_DISABLE,
)
from custom_components.almanac.storage import AlmanacData
from custom_components.almanac.tick import SERVICE_RUN_NOW, AlmanacTick

NY = ZoneInfo("America/New_York")

LATITUDE = 40.7128
LONGITUDE = -74.0060

# A window far enough ahead that the real wall clock is never inside it, so the
# tick that a collection write triggers has nothing to do and the only evaluations
# that matter are the ones a test asks for by name.
WINDOW = {"from": "2027-10-01", "until": "2027-10-31"}

HALL = "light.hall"


# --- helpers ---------------------------------------------------------------


def ny(*args: int) -> datetime:
    """A New York instant, written out."""
    return datetime(*args, tzinfo=NY)  # type: ignore[arg-type]


def daily() -> dict[str, Any]:
    """Every day."""
    return {
        "kind": RECUR_WEEKDAYS,
        "weekdays": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
    }


def clock(at: str) -> dict[str, Any]:
    """A clock anchor."""
    return {"kind": ANCHOR_CLOCK, "at": at}


def service(name: str, **extra: Any) -> dict[str, Any]:
    """A `kind: service` action."""
    return {"kind": "service", "service": name, **extra}


def at_rule(rule_id: str = "r1", at: str = "17:00", **extra: Any) -> dict[str, Any]:
    """An `At` rule (D2)."""
    return {"kind": RULE_AT, "id": rule_id, "anchor": clock(at), **extra}


def during_rule(
    rule_id: str = "r1", at: str = "17:00", seconds: int = 3600, **extra: Any
) -> dict[str, Any]:
    """A `During` rule (D2), a clock start and a duration end."""
    return {
        "kind": RULE_DURING,
        "id": rule_id,
        "start_anchor": clock(at),
        "end": {"kind": END_DURATION, "duration": seconds},
        **extra,
    }


def hall_on() -> dict[str, Any]:
    """A desired state naming one light (D28)."""
    return {"entities": [{"entity_id": HALL, "state": "on"}]}


def is_on(entity_id: str) -> dict[str, Any]:
    """A comparison condition — "this entity is on"."""
    return {
        "kind": CONDITION_COMPARISON,
        "entity_id": entity_id,
        "operator": "eq",
        "value": {"kind": OPERAND_CONSTANT, "value": "on"},
    }


@pytest.fixture(autouse=True)
async def _located(hass: HomeAssistant) -> None:
    """Put `hass` in New York before anything is set up.

    Autouse, and therefore ahead of `setup_almanac`: the tick evaluates at the
    registry's zone from its first pass, so setting it afterwards would mean the
    engine and the assertions disagreed about what "17:00" is.
    """
    await hass.config.async_set_time_zone("America/New_York")
    await hass.config.async_update(latitude=LATITUDE, longitude=LONGITUDE, elevation=0)


@pytest.fixture
def calls(hass: HomeAssistant) -> list[str]:
    """Every service call, in order, as `domain.service`.

    One list, because ordering is what these tests are for. The `light` handlers
    write the state machine as well as recording, and that is load-bearing rather
    than tidy: `light/reproduce_state.py` returns early when the entity is already
    in the state asked for, so a recorder that left the world untouched would make
    D3's `restore` at exit look like a no-op it is not.
    """
    seen: list[str] = []

    async def record(call: ServiceCall) -> None:
        seen.append(f"{call.domain}.{call.service}")

    async def turn_on(call: ServiceCall) -> None:
        seen.append("light.turn_on")
        for entity_id in cv.ensure_list(call.data.get("entity_id", [])):
            hass.states.async_set(entity_id, "on")

    async def turn_off(call: ServiceCall) -> None:
        seen.append("light.turn_off")
        for entity_id in cv.ensure_list(call.data.get("entity_id", [])):
            hass.states.async_set(entity_id, "off")

    hass.services.async_register("light", "turn_on", turn_on)
    hass.services.async_register("light", "turn_off", turn_off)
    hass.services.async_register("notify", "enter", record)
    hass.services.async_register("notify", "exit", record)
    hass.services.async_register("notify", "fired", record)
    hass.services.async_register("notify", "finished", record)
    return seen


@pytest.fixture
def tick(almanac_data: AlmanacData) -> AlmanacTick:
    """The running tick, as `async_setup_entry` attached it."""
    assert almanac_data.tick is not None
    return almanac_data.tick


async def create(hass: HomeAssistant, data: AlmanacData, name: str, **body: Any) -> str:
    """Store a schedule and return its id.

    `async_block_till_done` because a collection write notifies its listeners, one
    of which is the tick: the evaluation it triggers must be finished before a test
    drives one of its own, or the two would interleave.
    """
    item = await data.schedules.async_create_item(
        {"name": name, "date_window": WINDOW, "recurrence": daily(), **body}
    )
    await hass.async_block_till_done()
    return str(item["id"])


async def prime(tick: AlmanacTick) -> None:
    """One tick at 16:00 on the test day, before anything is due.

    Every test that expects a transition needs this first, for a reason worth
    writing down: D44 caps enumeration at ninety days from the *start* of the
    window, and a reconciliation's window starts at `evaluated_through` — which for
    a freshly created schedule is the real wall clock. So the first tick a test
    drives computes nothing at all; what it does is move `evaluated_through` next to
    the instants the test is about. It is also what makes a FIRE possible rather
    than a MISSED, because `transition.py` only fires an instant it can say was
    observed.
    """
    await tick.async_tick(ny(2027, 10, 2, 16, 0))


def engine_state(data: AlmanacData, schedule_id: str) -> dict[str, Any]:
    """The engine's half of the runtime record.

    Read by its stored key rather than through an import, because the key *is* the
    on-disk format: a rename is a migration, and a test that would not notice one is
    not asserting the thing that matters.
    """
    return dict(data.runtime.async_get(schedule_id).get("engine") or {})


# --- D41: fired, missed, skipped -------------------------------------------


async def test_a_fire_transition_runs_the_rules_actions(
    hass: HomeAssistant, tick: AlmanacTick, almanac_data: AlmanacData, calls: list[str]
) -> None:
    """The base case, and the two-tick shape every other `At` test reuses.

    The first tick is before the anchor and records that the engine got that far;
    the second is after it, so `transition.py` can say the instant was *observed*
    and fire it. One tick straight to 17:30 would fire too — `evaluated_through` is
    behind — but only by accident of what the setup pass happened to write, and a
    test that depends on that is asserting the wrong thing.
    """
    schedule_id = await create(
        hass,
        almanac_data,
        "Fires",
        rules=[at_rule(actions=[service("notify.fired")])],
    )

    await prime(tick)
    assert calls == []

    await prime(tick)
    await tick.async_tick(ny(2027, 10, 2, 17, 30))
    assert calls == ["notify.fired"]
    assert engine_state(almanac_data, schedule_id)["decided"]


async def test_a_recovery_pass_does_not_fire_what_it_did_not_observe(
    hass: HomeAssistant, tick: AlmanacTick, almanac_data: AlmanacData, calls: list[str]
) -> None:
    """D41's asymmetry, from the consumer's side.

    `live=False` is the restart path: the engine cannot claim to have watched 17:00
    go by, so the occurrence is MISSED — and MISSED has no side effects at all,
    which is the decision D41 states and not an omission in `_async_execute_one`.
    """
    await create(
        hass,
        almanac_data,
        "Missed",
        rules=[at_rule(actions=[service("notify.fired")])],
    )

    await prime(tick)
    await tick.async_tick(ny(2027, 10, 2, 17, 30), live=False)

    assert calls == []


async def test_a_skipped_occurrence_runs_nothing(
    hass: HomeAssistant, tick: AlmanacTick, almanac_data: AlmanacData, calls: list[str]
) -> None:
    """D26's default policy. The occurrence is recorded, and nothing happens."""
    hass.states.async_set("binary_sensor.home", "off")
    await create(
        hass,
        almanac_data,
        "Skipped",
        rules=[
            at_rule(
                actions=[service("notify.fired")],
                conditions=[is_on("binary_sensor.home")],
            )
        ],
    )

    await prime(tick)
    await tick.async_tick(ny(2027, 10, 2, 17, 30))

    assert calls == []


async def test_the_same_instant_twice_does_not_fire_twice(
    hass: HomeAssistant, tick: AlmanacTick, almanac_data: AlmanacData, calls: list[str]
) -> None:
    """Every wake mechanism can coincide: a timer, a state change and an edit can
    all land on one instant, so idempotence is not optional."""
    await create(
        hass,
        almanac_data,
        "Once",
        rules=[at_rule(actions=[service("notify.fired")])],
    )

    await prime(tick)
    await tick.async_tick(ny(2027, 10, 2, 17, 30))
    await prime(tick)
    await tick.async_tick(ny(2027, 10, 2, 17, 30))

    assert calls == ["notify.fired"]


async def test_the_next_instant_is_written_to_the_runtime_record(
    hass: HomeAssistant, tick: AlmanacTick, almanac_data: AlmanacData, calls: list[str]
) -> None:
    """D55's sensor reads this, and step 5 is where it starts being written.

    Recorded rather than recomputed on demand, because the sensor must not enumerate
    a schedule to answer "when next" on every state read.
    """
    schedule_id = await create(
        hass,
        almanac_data,
        "Next",
        rules=[at_rule(actions=[service("notify.fired")])],
    )

    await prime(tick)
    await tick.async_tick(ny(2027, 10, 2, 17, 30))

    record = almanac_data.runtime.async_get(schedule_id)
    assert record["next_at"] == ny(2027, 10, 3, 17, 0).isoformat()


# --- D5, D28: entering an interval ----------------------------------------


async def test_entering_applies_the_state_before_the_enter_actions(
    hass: HomeAssistant, tick: AlmanacTick, almanac_data: AlmanacData, calls: list[str]
) -> None:
    """D5's own example is "set the lights *and* announce", in that order.

    D32 is what makes the order safe to fix: a failed announcement does not stop the
    state, so there is no reason to run the announcement first as insurance.
    """
    hass.states.async_set(HALL, "off")
    schedule_id = await create(
        hass,
        almanac_data,
        "Evening",
        rules=[
            during_rule(
                state=hall_on(),
                enter_actions=[service("notify.enter")],
            )
        ],
    )

    await prime(tick)
    await tick.async_tick(ny(2027, 10, 2, 17, 30))

    assert calls == ["light.turn_on", "notify.enter"]
    assert len(engine_state(almanac_data, schedule_id)["held"]) == 1


async def test_a_held_interval_and_its_promise_are_persisted(
    hass: HomeAssistant, tick: AlmanacTick, almanac_data: AlmanacData, calls: list[str]
) -> None:
    """The promise is what makes D3's exit survive anything — a restart, an edit,
    the rule being deleted. It is written when the interval is entered, because that
    is the only moment the pre-interval state still exists to be snapshotted."""
    hass.states.async_set(HALL, "off")
    schedule_id = await create(
        hass,
        almanac_data,
        "Promised",
        rules=[during_rule(state=hall_on(), on_exit={"kind": ON_EXIT_RESTORE})],
    )

    await prime(tick)
    await tick.async_tick(ny(2027, 10, 2, 17, 30))

    promises = almanac_data.runtime.async_get(schedule_id)["promises"]
    assert len(promises) == 1
    promise = next(iter(promises.values()))
    assert promise["on_exit"] == {"kind": ON_EXIT_RESTORE}
    # The state *before* the interval touched it, which is the only useful snapshot.
    assert promise["restore"][0]["state"] == "off"


async def test_the_promise_is_pruned_when_the_interval_ends(
    hass: HomeAssistant, tick: AlmanacTick, almanac_data: AlmanacData, calls: list[str]
) -> None:
    """Pruned from the state the engine returned, not when the exit ran: a crash
    between the exit and the save must not lose the promise for something still
    held."""
    hass.states.async_set(HALL, "off")
    schedule_id = await create(
        hass,
        almanac_data,
        "Pruned",
        rules=[during_rule(state=hall_on(), on_exit={"kind": ON_EXIT_RESTORE})],
    )

    await prime(tick)
    await tick.async_tick(ny(2027, 10, 2, 17, 30))
    await tick.async_tick(ny(2027, 10, 2, 18, 30))

    record = almanac_data.runtime.async_get(schedule_id)
    assert record["promises"] == {}
    assert record["engine"]["held"] == []


# --- D3: the three exit behaviours ----------------------------------------


async def test_exit_restore_puts_back_what_was_there(
    hass: HomeAssistant, tick: AlmanacTick, almanac_data: AlmanacData, calls: list[str]
) -> None:
    """The window end, and the snapshot taken at the start being used at the end."""
    hass.states.async_set(HALL, "off")
    await create(
        hass,
        almanac_data,
        "Restores",
        rules=[
            during_rule(
                state=hall_on(),
                on_exit={"kind": ON_EXIT_RESTORE},
                exit_actions=[service("notify.exit")],
            )
        ],
    )

    await prime(tick)
    await tick.async_tick(ny(2027, 10, 2, 17, 30))
    assert calls == ["light.turn_on"]

    await tick.async_tick(ny(2027, 10, 2, 18, 30))
    assert calls == ["light.turn_on", "light.turn_off", "notify.exit"]


async def test_exit_apply_sets_the_state_the_rule_named(
    hass: HomeAssistant, tick: AlmanacTick, almanac_data: AlmanacData, calls: list[str]
) -> None:
    """D3's second value. The light was already on before the interval, so a
    `restore` here would do nothing and `apply` is the only thing that can turn it
    off — which is exactly why the two are separate values rather than one guess."""
    hass.states.async_set(HALL, "on")
    await create(
        hass,
        almanac_data,
        "Applies",
        rules=[
            during_rule(
                state={"entities": [{"entity_id": HALL, "state": "on"}]},
                on_exit={
                    "kind": ON_EXIT_APPLY,
                    "state": {"entities": [{"entity_id": HALL, "state": "off"}]},
                },
            )
        ],
    )

    await prime(tick)
    await tick.async_tick(ny(2027, 10, 2, 17, 30))
    await tick.async_tick(ny(2027, 10, 2, 18, 30))

    assert calls == ["light.turn_off"]


async def test_exit_leave_changes_nothing_and_still_runs_exit_actions(
    hass: HomeAssistant, tick: AlmanacTick, almanac_data: AlmanacData, calls: list[str]
) -> None:
    """`leave` is a choice the user made, not the absence of one — so the exit path
    still runs, and the exit actions with it."""
    hass.states.async_set(HALL, "off")
    await create(
        hass,
        almanac_data,
        "Leaves",
        rules=[
            during_rule(
                state=hall_on(),
                on_exit={"kind": ON_EXIT_LEAVE},
                exit_actions=[service("notify.exit")],
            )
        ],
    )

    await prime(tick)
    await tick.async_tick(ny(2027, 10, 2, 17, 30))
    await tick.async_tick(ny(2027, 10, 2, 18, 30))

    assert calls == ["light.turn_on", "notify.exit"]


async def test_the_exit_promise_survives_the_rule_being_deleted(
    hass: HomeAssistant, tick: AlmanacTick, almanac_data: AlmanacData, calls: list[str]
) -> None:
    """`ExitCause.GONE`, and the reason the promise is stored at all.

    Reading the exit behaviour from the rule would mean a schedule could be edited
    into leaving the lights on for ever. It would also mean an edit silently changed
    the exit behaviour of an interval that is *currently* held, which is the same
    class of surprise D47 forbids for termination.

    No explicit tick here: the edit itself is what re-evaluates, which is the other
    half of what this asserts.
    """
    hass.states.async_set(HALL, "off")
    schedule_id = await create(
        hass,
        almanac_data,
        "Deleted rule",
        rules=[during_rule(state=hall_on(), on_exit={"kind": ON_EXIT_RESTORE})],
    )

    await prime(tick)
    await tick.async_tick(ny(2027, 10, 2, 17, 30))
    assert calls == ["light.turn_on"]

    await almanac_data.schedules.async_update_item(schedule_id, {"rules": []})
    await hass.async_block_till_done()

    assert calls == ["light.turn_on", "light.turn_off"]
    assert engine_state(almanac_data, schedule_id)["held"] == []


async def test_a_missing_promise_leaves_the_world_alone(
    hass: HomeAssistant, tick: AlmanacTick, almanac_data: AlmanacData, calls: list[str]
) -> None:
    """An interval held across an upgrade that predates the promise store.

    `leave` is the only defensible reading: with no snapshot there is nothing to
    restore, and inventing a state to apply would be worse than doing nothing. The
    warning is the deliverable.
    """
    hass.states.async_set(HALL, "off")
    schedule_id = await create(
        hass,
        almanac_data,
        "No promise",
        rules=[during_rule(state=hall_on(), on_exit={"kind": ON_EXIT_RESTORE})],
    )

    await prime(tick)
    await tick.async_tick(ny(2027, 10, 2, 17, 30))
    record = dict(almanac_data.runtime.async_get(schedule_id))
    record["promises"] = {}
    almanac_data.runtime.async_set(schedule_id, record)

    await tick.async_tick(ny(2027, 10, 2, 18, 30))

    assert calls == ["light.turn_on"]


# --- D90: resume is not enter ---------------------------------------------


async def test_resume_reapplies_the_state_and_does_not_re_announce(
    hass: HomeAssistant, tick: AlmanacTick, almanac_data: AlmanacData, calls: list[str]
) -> None:
    """D90, which is a decision about restarts and about IR bursts.

    Treating RESUME as ENTER would re-run the enter actions once per Home Assistant
    restart, for no scheduled reason. The state *is* re-applied, because the engine
    cannot know whether something changed it while the engine was not running.
    """
    hass.states.async_set(HALL, "off")
    await create(
        hass,
        almanac_data,
        "Resumes",
        rules=[during_rule(state=hall_on(), enter_actions=[service("notify.enter")])],
    )

    await prime(tick)
    await tick.async_tick(ny(2027, 10, 2, 17, 30))
    assert calls == ["light.turn_on", "notify.enter"]

    # Somebody turned it off by hand while the engine was down.
    hass.states.async_set(HALL, "off")
    await tick.async_tick(ny(2027, 10, 2, 17, 45), live=False)

    assert calls == ["light.turn_on", "notify.enter", "light.turn_on"]


# --- D91: disarming exits immediately -------------------------------------


async def test_disabling_a_schedule_exits_a_held_interval(
    hass: HomeAssistant, tick: AlmanacTick, almanac_data: AlmanacData, calls: list[str]
) -> None:
    """The failure this prevents is a switch reading *off* with the lights still on.

    It is also why `async_tick` evaluates disabled schedules rather than skipping
    them: the exit can only be produced by looking at one.
    """
    hass.states.async_set(HALL, "off")
    schedule_id = await create(
        hass,
        almanac_data,
        "Disarmed",
        rules=[during_rule(state=hall_on(), on_exit={"kind": ON_EXIT_RESTORE})],
    )

    await prime(tick)
    await tick.async_tick(ny(2027, 10, 2, 17, 30))
    await almanac_data.schedules.async_update_item(schedule_id, {"enabled": False})
    await hass.async_block_till_done()
    # That write re-evaluated the schedule at the *real* clock, because a collection
    # change is one of the things the tick subscribes to — which set
    # `evaluated_through` back to today. D44 then caps the next tick's enumeration
    # at ninety days from there, so a tick in 2027 would see no occurrence at all,
    # and D91's exit needs one: the cause is "the occurrence is no longer armed",
    # which cannot be said about an occurrence that is not there. The tick below
    # enumerates nothing for that reason and does only what `prime` does — move
    # `evaluated_through` back alongside the instants this test is about.
    await tick.async_tick(ny(2027, 10, 2, 17, 40))
    await tick.async_tick(ny(2027, 10, 2, 17, 45))

    assert calls == ["light.turn_on", "light.turn_off"]
    assert engine_state(almanac_data, schedule_id)["held"] == []


# --- D46, D47: what happens then ------------------------------------------


async def test_then_action_runs_once_the_schedule_is_finished(
    hass: HomeAssistant, tick: AlmanacTick, almanac_data: AlmanacData, calls: list[str]
) -> None:
    """The completion axes are `completion.py`'s; carrying the `then` out is here."""
    await create(
        hass,
        almanac_data,
        "Then action",
        rules=[at_rule(actions=[service("notify.fired")])],
        completion={
            "finished_when": {"kind": FINISHED_ONE_RULE_FIRED},
            "then": {"kind": THEN_ACTION, "actions": [service("notify.finished")]},
        },
    )

    await prime(tick)
    await tick.async_tick(ny(2027, 10, 2, 17, 30))

    assert calls == ["notify.fired", "notify.finished"]


async def test_then_disable_writes_through_the_collection(
    hass: HomeAssistant, tick: AlmanacTick, almanac_data: AlmanacData, calls: list[str]
) -> None:
    """Through the collection, like `switch.py`: it is the one write path (D33), it
    persists, and it reaches the switch entity. A private flag would leave the
    switch reading *on* for a schedule that has finished.

    This is also the case that forced the `then` out of the tick's lock: core's
    `ObservableCollection.notify_changes` awaits its listeners, and one of them is
    the tick, so writing here from inside the lock deadlocks the tick against
    itself.
    """
    schedule_id = await create(
        hass,
        almanac_data,
        "Then disable",
        rules=[at_rule(actions=[service("notify.fired")])],
        completion={
            "finished_when": {"kind": FINISHED_ONE_RULE_FIRED},
            "then": {"kind": THEN_DISABLE},
        },
    )

    await prime(tick)
    await tick.async_tick(ny(2027, 10, 2, 17, 30))
    await hass.async_block_till_done()

    assert almanac_data.schedules.data[schedule_id]["enabled"] is False


async def test_then_delete_removes_the_schedule(
    hass: HomeAssistant, tick: AlmanacTick, almanac_data: AlmanacData, calls: list[str]
) -> None:
    """"Delete after it triggers" — the thing today's model expresses by making
    *Stop* and *Delete* mean two different storage values (see the fact block)."""
    schedule_id = await create(
        hass,
        almanac_data,
        "Then delete",
        rules=[at_rule(actions=[service("notify.fired")])],
        completion={
            "finished_when": {"kind": FINISHED_ONE_RULE_FIRED},
            "then": {"kind": THEN_DELETE},
        },
    )

    await prime(tick)
    await tick.async_tick(ny(2027, 10, 2, 17, 30))
    await hass.async_block_till_done()

    assert schedule_id not in almanac_data.schedules.data


async def test_termination_waits_for_the_interval_to_finish(
    hass: HomeAssistant, tick: AlmanacTick, almanac_data: AlmanacData, calls: list[str]
) -> None:
    """D47, end to end, and the failure it exists to prevent.

    A schedule that deletes itself the moment it enters its one interval leaves the
    lights on and removes the only thing that owed the exit. So the first tick
    records that it is finished and keeps going; the second, once the interval has
    run out and the restore has happened, deletes it.
    """
    hass.states.async_set(HALL, "off")
    schedule_id = await create(
        hass,
        almanac_data,
        "Deferred",
        rules=[during_rule(state=hall_on(), on_exit={"kind": ON_EXIT_RESTORE})],
        completion={
            "finished_when": {"kind": FINISHED_ONE_RULE_FIRED},
            "then": {"kind": THEN_DELETE},
        },
    )

    await prime(tick)
    await tick.async_tick(ny(2027, 10, 2, 17, 30))
    await hass.async_block_till_done()
    assert schedule_id in almanac_data.schedules.data
    assert almanac_data.runtime.async_get(schedule_id)["completion"]["finished_at"]

    await tick.async_tick(ny(2027, 10, 2, 18, 30))
    await hass.async_block_till_done()

    assert schedule_id not in almanac_data.schedules.data
    assert calls == ["light.turn_on", "light.turn_off"]


# --- D45: run_now ---------------------------------------------------------
#
# These go through the service, which reads the real clock, so every rule here is
# one whose behaviour does not depend on the instant: `run_now` on an `At` rule is
# an instruction, not a schedule, and the `During` cases pick windows that either
# always hold or never do.


async def test_run_now_fires_an_at_rule(
    hass: HomeAssistant, tick: AlmanacTick, almanac_data: AlmanacData, calls: list[str]
) -> None:
    """D45's whole purpose: test a schedule without waiting for its anchor, and
    without moving it."""
    schedule_id = await create(
        hass,
        almanac_data,
        "Run me",
        rules=[at_rule(actions=[service("notify.fired")])],
    )

    await hass.services.async_call(
        DOMAIN, SERVICE_RUN_NOW, {"schedule_id": schedule_id}, blocking=True
    )

    assert calls == ["notify.fired"]


async def test_run_now_resolves_the_schedule_from_its_entity(
    hass: HomeAssistant, tick: AlmanacTick, almanac_data: AlmanacData, calls: list[str]
) -> None:
    """"Run this one now" is a thing said about the entity in front of you.

    The mapping exists because D37 makes the collection id each entity's
    `unique_id`, so the registry already holds it — there is nothing to store.
    """
    schedule_id = await create(
        hass,
        almanac_data,
        "By entity",
        rules=[at_rule(actions=[service("notify.fired")])],
    )
    entity_id = er.async_get(hass).async_get_entity_id("switch", DOMAIN, schedule_id)
    assert entity_id is not None

    await hass.services.async_call(
        DOMAIN, SERVICE_RUN_NOW, {"entity_id": entity_id}, blocking=True
    )

    assert calls == ["notify.fired"]


async def test_run_now_respects_conditions_unless_told_not_to(
    hass: HomeAssistant, tick: AlmanacTick, almanac_data: AlmanacData, calls: list[str]
) -> None:
    """A false condition is the rule's own answer, so it is logged, not raised.

    An exception would make "nobody is home" look like a broken service call. The
    flag exists so that a user who meant to ignore the conditions says so.
    """
    hass.states.async_set("binary_sensor.home", "off")
    schedule_id = await create(
        hass,
        almanac_data,
        "Conditional",
        rules=[
            at_rule(
                actions=[service("notify.fired")],
                conditions=[is_on("binary_sensor.home")],
            )
        ],
    )

    await hass.services.async_call(
        DOMAIN, SERVICE_RUN_NOW, {"schedule_id": schedule_id}, blocking=True
    )
    assert calls == []

    await hass.services.async_call(
        DOMAIN,
        SERVICE_RUN_NOW,
        {"schedule_id": schedule_id, "bypass_conditions": True},
        blocking=True,
    )
    assert calls == ["notify.fired"]


async def test_run_now_runs_a_disabled_rule_when_no_rule_is_named(
    hass: HomeAssistant, tick: AlmanacTick, almanac_data: AlmanacData, calls: list[str]
) -> None:
    """Provisional, and reported as such: D45 does not say.

    `run_now` names the schedule explicitly, and the case it is most used in is
    testing a rule *before* arming it — so filtering on `enabled` would make the
    service silently do nothing exactly when it is wanted.
    """
    schedule_id = await create(
        hass,
        almanac_data,
        "Disabled rule",
        rules=[at_rule(enabled=False, actions=[service("notify.fired")])],
    )

    await hass.services.async_call(
        DOMAIN, SERVICE_RUN_NOW, {"schedule_id": schedule_id}, blocking=True
    )

    assert calls == ["notify.fired"]


async def test_run_now_enters_an_interval_that_is_in_force(
    hass: HomeAssistant, tick: AlmanacTick, almanac_data: AlmanacData, calls: list[str]
) -> None:
    """Provisional: the interval is entered *properly*, so the engine owes the exit.

    The window is the whole civil day, so it holds at whatever instant the test
    runs — that is what lets a service call that reads the real clock be asserted at
    all. The rejected alternative was applying the state with no recorded end, which
    is precisely the orphaned state D47 exists to prevent.
    """
    hass.states.async_set(HALL, "off")
    schedule_id = await create(
        hass,
        almanac_data,
        "All day",
        date_window={},
        rules=[
            during_rule(
                at="00:00",
                seconds=86399,
                state=hall_on(),
                enter_actions=[service("notify.enter")],
                on_exit={"kind": ON_EXIT_RESTORE},
            )
        ],
    )
    # An open date window is deliberate: `run_now` reads the real clock, so the only
    # interval it can find in force is one that holds now. The cost is that the tick
    # the create triggered has already entered it — visible in `calls` — so the
    # runtime record is reset here to what an engine that has not seen this schedule
    # holds. Entering and re-entering are genuinely different paths in
    # `_async_run_one_now`, and the next test takes the other one.
    assert calls == ["light.turn_on", "notify.enter"]
    almanac_data.runtime.async_set(schedule_id, {})
    hass.states.async_set(HALL, "off")
    calls.clear()

    await hass.services.async_call(
        DOMAIN, SERVICE_RUN_NOW, {"schedule_id": schedule_id}, blocking=True
    )

    assert calls == ["light.turn_on", "notify.enter"]
    assert len(engine_state(almanac_data, schedule_id)["held"]) == 1
    assert almanac_data.runtime.async_get(schedule_id)["promises"]


async def test_run_now_on_a_held_interval_keeps_the_original_promise(
    hass: HomeAssistant, tick: AlmanacTick, almanac_data: AlmanacData, calls: list[str]
) -> None:
    """Re-running a rule the engine already holds must not overwrite its snapshot.

    The promise carries the state captured *before* the interval first touched the
    world, and `on_exit: restore` is only truthful while that snapshot is the
    original one. Re-capturing it here would record the state the rule itself
    applied, and the restore would then put back what the rule did — a bug that
    reads as "restore does nothing" and is invisible until someone looks.

    The enter actions do run again, because that is what the user asked for: D45's
    service is "do this rule now", and its one-shot half owes nothing.
    """
    hass.states.async_set(HALL, "off")
    schedule_id = await create(
        hass,
        almanac_data,
        "All day",
        date_window={},
        rules=[
            during_rule(
                at="00:00",
                seconds=86399,
                state=hall_on(),
                enter_actions=[service("notify.enter")],
                on_exit={"kind": ON_EXIT_RESTORE},
            )
        ],
    )
    # No reset this time: the create-triggered tick entered the interval, which is
    # exactly the precondition this test wants.
    assert calls == ["light.turn_on", "notify.enter"]
    promises = dict(almanac_data.runtime.async_get(schedule_id)["promises"])
    assert promises

    await hass.services.async_call(
        DOMAIN, SERVICE_RUN_NOW, {"schedule_id": schedule_id}, blocking=True
    )

    assert calls == ["light.turn_on", "notify.enter", "notify.enter"]
    assert len(engine_state(almanac_data, schedule_id)["held"]) == 1
    assert almanac_data.runtime.async_get(schedule_id)["promises"] == promises


async def test_run_now_on_an_interval_with_nothing_in_force_leaves_state_alone(
    hass: HomeAssistant, tick: AlmanacTick, almanac_data: AlmanacData, calls: list[str]
) -> None:
    """The other half of that provisional reading, and the more important half.

    With no occurrence in force there is no end to owe an exit at, so applying the
    desired state would leave a state the engine created and nothing claims
    responsibility for. The enter actions run — they are one-shot and owe nothing —
    and the state is left alone.
    """
    hass.states.async_set(HALL, "off")
    schedule_id = await create(
        hass,
        almanac_data,
        "Not in force",
        rules=[during_rule(state=hall_on(), enter_actions=[service("notify.enter")])],
    )

    await hass.services.async_call(
        DOMAIN, SERVICE_RUN_NOW, {"schedule_id": schedule_id}, blocking=True
    )

    assert calls == ["notify.enter"]
    assert engine_state(almanac_data, schedule_id).get("held", []) == []


async def test_run_now_refuses_an_unknown_schedule_or_rule(
    hass: HomeAssistant, tick: AlmanacTick, almanac_data: AlmanacData, calls: list[str]
) -> None:
    """Raised, not logged: unlike a false condition, a bad id is the caller's bug."""
    schedule_id = await create(
        hass, almanac_data, "Known", rules=[at_rule(actions=[service("notify.fired")])]
    )

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, SERVICE_RUN_NOW, {"schedule_id": "nope"}, blocking=True
        )

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_RUN_NOW,
            {"schedule_id": schedule_id, "rule_id": "nope"},
            blocking=True,
        )

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, SERVICE_RUN_NOW, {}, blocking=True)

    assert calls == []


async def test_run_now_names_one_rule(
    hass: HomeAssistant, tick: AlmanacTick, almanac_data: AlmanacData, calls: list[str]
) -> None:
    """A multi-stage schedule is the case D2 exists for, so "run stage two" has to
    be sayable."""
    schedule_id = await create(
        hass,
        almanac_data,
        "Two stages",
        rules=[
            at_rule("r1", "17:00", actions=[service("notify.fired")]),
            at_rule("r2", "22:00", actions=[service("notify.finished")]),
        ],
    )

    await hass.services.async_call(
        DOMAIN,
        SERVICE_RUN_NOW,
        {"schedule_id": schedule_id, "rule_id": "r2"},
        blocking=True,
    )

    assert calls == ["notify.finished"]


# --- lifecycle -----------------------------------------------------------


async def test_unloading_the_entry_removes_the_service_and_stops_the_tick(
    hass: HomeAssistant, almanac_data: AlmanacData, calls: list[str]
) -> None:
    """Shutdown deliberately runs no exit paths.

    The runtime store is what carries a held interval across a restart, and D41/D90
    are what pick it up again — so restoring on the way down would undo, once per
    Home Assistant restart, an interval that is still in force.
    """
    hass.states.async_set(HALL, "off")
    tick = almanac_data.tick
    assert tick is not None
    schedule_id = await create(
        hass,
        almanac_data,
        "Across a restart",
        rules=[during_rule(state=hall_on(), on_exit={"kind": ON_EXIT_RESTORE})],
    )
    await prime(tick)
    await tick.async_tick(ny(2027, 10, 2, 17, 30))
    assert calls == ["light.turn_on"]

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.services.has_service(DOMAIN, SERVICE_RUN_NOW)
    assert calls == ["light.turn_on"]
    assert len(engine_state(almanac_data, schedule_id)["held"]) == 1
