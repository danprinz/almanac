"""The dry run — D64's claim, stated as a test that could fail.

The claim is narrow and worth restating exactly: *the dry run is `async_tick`'s
own reasoning at a supplied instant.* Not a simulation of it, not a second
enumeration written for the panel. D64 exists to make that structurally true —
`now` is a parameter everywhere below the tick, so there is nothing for a parallel
implementation to drift from, because there is no parallel implementation.

A test cannot assert "these are the same code path" directly. What it can assert
is the consequence, and that is what this file does: the transitions a dry run
reports at an instant are the transitions the live tick then acts on at that same
instant, in the same order, with the same kinds. If the two ever diverged, the
feature would be worth less than nothing — a preview that is confidently wrong is
worse than no preview.

The other half is what the dry run must *not* do. No service calls, no events, no
runtime record. That is asserted separately, because "same answer" and "no side
effects" fail independently and a single test covering both would not say which.

Scaffolding — the 2027 window, the two-tick `prime` dance, the `calls` recorder —
is `test_tick.py`'s, deliberately: these tests are about the same transitions seen
from a different entry point, so they are produced the same way.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from homeassistant.core import Event, HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.almanac.const import (
    ANCHOR_CLOCK,
    END_DURATION,
    EVENT_EXECUTION,
    EVENT_OCCURRENCE,
    RECUR_WEEKDAYS,
    RULE_AT,
    RULE_DURING,
    WS_DRY_RUN,
)
from custom_components.almanac.engine.transition import TransitionKind
from custom_components.almanac.storage import AlmanacData
from custom_components.almanac.tick import AlmanacTick

NY = ZoneInfo("America/New_York")

LATITUDE = 40.7128
LONGITUDE = -74.0060

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


def during_rule(rule_id: str = "r1", at: str = "17:00", **extra: Any) -> dict[str, Any]:
    """A `During` rule (D2), a clock start and a duration end."""
    return {
        "kind": RULE_DURING,
        "id": rule_id,
        "start_anchor": clock(at),
        "end": {"kind": END_DURATION, "duration": 3600},
        **extra,
    }


def hall_on() -> dict[str, Any]:
    """A desired state naming one light (D28)."""
    return {"entities": [{"entity_id": HALL, "state": "on"}]}


@pytest.fixture(autouse=True)
async def _located(hass: HomeAssistant) -> None:
    """New York, before anything is set up."""
    await hass.config.async_set_time_zone("America/New_York")
    await hass.config.async_update(latitude=LATITUDE, longitude=LONGITUDE, elevation=0)


@pytest.fixture
def calls(hass: HomeAssistant) -> list[str]:
    """Every service call, in order — the dry run's must stay empty."""
    seen: list[str] = []

    async def record(call: ServiceCall) -> None:
        seen.append(f"{call.domain}.{call.service}")

    async def turn_on(call: ServiceCall) -> None:
        seen.append("light.turn_on")
        for entity_id in cv.ensure_list(call.data.get("entity_id", [])):
            hass.states.async_set(entity_id, "on")

    hass.services.async_register("light", "turn_on", turn_on)
    hass.services.async_register("notify", "fired", record)
    hass.services.async_register("notify", "enter", record)
    return seen


@pytest.fixture
def events(hass: HomeAssistant) -> list[Event]:
    """Both of D107's event types, in one list, for the same reason §12 keeps one."""
    seen: list[Event] = []

    @callback
    def collect(event: Event) -> None:
        seen.append(event)

    hass.bus.async_listen(EVENT_OCCURRENCE, collect)
    hass.bus.async_listen(EVENT_EXECUTION, collect)
    return seen


@pytest.fixture
def tick(almanac_data: AlmanacData) -> AlmanacTick:
    """The running tick, as `async_setup_entry` attached it."""
    assert almanac_data.tick is not None
    return almanac_data.tick


async def create(hass: HomeAssistant, data: AlmanacData, name: str, **body: Any) -> str:
    """Store a schedule and return its id."""
    item = await data.schedules.async_create_item(
        {"name": name, "date_window": WINDOW, "recurrence": daily(), **body}
    )
    await hass.async_block_till_done()
    return str(item["id"])


async def prime(tick: AlmanacTick) -> None:
    """One tick at 16:00, before anything is due — see `test_tick.prime`."""
    await tick.async_tick(ny(2027, 10, 2, 16, 0))


def engine_state(data: AlmanacData, schedule_id: str) -> dict[str, Any]:
    """The engine's half of the runtime record, by its stored key."""
    return dict(data.runtime.async_get(schedule_id).get("engine") or {})


def kinds(reconciliation: Any) -> list[str]:
    """The transition kinds one evaluation produced, in order."""
    return [str(transition.kind) for transition in reconciliation.transitions]


# --- the claim: the same answer the live tick gives ------------------------


async def test_the_dry_run_predicts_exactly_what_the_live_tick_then_does(
    hass: HomeAssistant, tick: AlmanacTick, almanac_data: AlmanacData, calls: list[str]
) -> None:
    """D64's whole point, asserted as a sequence: predict, then act, then compare.

    The dry run runs first and the live tick second, at the *same* written-down
    instant and from the same engine state. If the dry run were a second
    implementation this is where it would show: the same schedule, the same
    instant, two answers.
    """
    schedule_id = await create(
        hass,
        almanac_data,
        "Lights",
        rules=[at_rule(actions=[service("notify.fired")])],
    )
    await prime(tick)

    predicted = await tick.async_dry_run(at=ny(2027, 10, 2, 17, 30))
    assert kinds(predicted[schedule_id]) == [str(TransitionKind.FIRE)]
    assert calls == []

    await tick.async_tick(ny(2027, 10, 2, 17, 30))
    assert calls == ["notify.fired"]


async def test_the_dry_run_predicts_an_interval_it_has_not_entered(
    hass: HomeAssistant, tick: AlmanacTick, almanac_data: AlmanacData, calls: list[str]
) -> None:
    """A `During` rule, where the prediction is the more valuable one.

    "At 17:00 the hall light goes on and stays on for an hour" is the thing a
    person most wants to see before committing to it, and it is the one upstream
    cannot show at all. The ENTER is predicted, and predicting it changes nothing.
    """
    # The light has to exist: `light/reproduce_state.py` logs "Unable to find
    # entity" and returns for one that does not, so a missing entity would make
    # D28's desired state look like a no-op it is not.
    hass.states.async_set(HALL, "off")
    schedule_id = await create(
        hass,
        almanac_data,
        "Hall",
        rules=[during_rule(state=hall_on(), enter_actions=[service("notify.enter")])],
    )
    await prime(tick)

    predicted = await tick.async_dry_run(at=ny(2027, 10, 2, 17, 30))
    assert kinds(predicted[schedule_id]) == [str(TransitionKind.ENTER)]
    assert calls == []
    assert engine_state(almanac_data, schedule_id).get("held") in (None, [], ())

    await tick.async_tick(ny(2027, 10, 2, 17, 30))
    assert calls == ["light.turn_on", "notify.enter"]


# --- the other half: it must change nothing --------------------------------


async def test_the_dry_run_fires_no_events_and_writes_no_record(
    hass: HomeAssistant,
    tick: AlmanacTick,
    almanac_data: AlmanacData,
    calls: list[str],
    events: list[Event],
) -> None:
    """D48 makes the recorder the audit trail, so a dry run that fired would lie.

    An `almanac_occurrence` row is a statement that something happened, and it is
    durable, logbook-described and joined to whatever the schedule touched. A
    preview that wrote one would put a fiction in the audit trail — and D109
    already treats "did this actually occur" as the question the event answers.

    The runtime record matters for a different reason: it is what `evaluated_through`
    lives in, and advancing it would mean a dry run at next Friday made the live
    engine believe it had already watched the intervening week go by. Every
    occurrence in between would then be unobserved and therefore MISSED.
    """
    schedule_id = await create(
        hass,
        almanac_data,
        "Lights",
        rules=[at_rule(actions=[service("notify.fired")])],
    )
    await prime(tick)
    before = engine_state(almanac_data, schedule_id)
    events.clear()

    await tick.async_dry_run(at=ny(2027, 10, 2, 17, 30))
    await hass.async_block_till_done()

    assert calls == []
    assert events == []
    assert engine_state(almanac_data, schedule_id) == before


async def test_a_dry_run_leaves_the_live_engine_able_to_fire(
    hass: HomeAssistant, tick: AlmanacTick, almanac_data: AlmanacData, calls: list[str]
) -> None:
    """The consequence of the record being untouched, stated on its own.

    Previewing next Friday and then letting the week run must not turn the week's
    occurrences into misses. This is the same property as the record assertion
    above, but it is the one a user would actually notice, so it is worth failing
    separately.
    """
    await create(
        hass,
        almanac_data,
        "Lights",
        rules=[at_rule(actions=[service("notify.fired")])],
    )
    await prime(tick)

    await tick.async_dry_run(at=ny(2027, 10, 9, 17, 30))
    await tick.async_tick(ny(2027, 10, 2, 17, 30))

    assert calls == ["notify.fired"]


# --- the flags -------------------------------------------------------------


async def test_live_false_gives_the_recovery_reading(
    hass: HomeAssistant, tick: AlmanacTick, almanac_data: AlmanacData
) -> None:
    """D41's asymmetry is a dry-run flag, because it is a real question.

    "What will the engine do when it comes back up?" has a different answer from
    "what will it do while it is running", and the difference is not cosmetic: a
    recovery pass cannot claim to have watched 17:00 go by, so the occurrence is
    MISSED and has no side effects at all. Both readings come from the same two
    planners the tick itself chooses between; the flag selects, it does not
    re-implement.
    """
    schedule_id = await create(
        hass,
        almanac_data,
        "Lights",
        rules=[at_rule(actions=[service("notify.fired")])],
    )
    await prime(tick)

    live = await tick.async_dry_run(at=ny(2027, 10, 2, 17, 30))
    recovery = await tick.async_dry_run(at=ny(2027, 10, 2, 17, 30), live=False)

    assert kinds(live[schedule_id]) == [str(TransitionKind.FIRE)]
    assert kinds(recovery[schedule_id]) == [str(TransitionKind.MISSED)]


async def test_schedule_ids_narrows_the_dry_run(
    hass: HomeAssistant, tick: AlmanacTick, almanac_data: AlmanacData
) -> None:
    """One schedule at a time, for the panel's per-schedule preview."""
    first = await create(hass, almanac_data, "One", rules=[at_rule()])
    await create(hass, almanac_data, "Two", rules=[at_rule()])
    await prime(tick)

    result = await tick.async_dry_run(
        at=ny(2027, 10, 2, 17, 30), schedule_ids=[first]
    )
    assert list(result) == [first]


# --- the provisional reading, written down so it can be overruled ----------


async def test_a_dry_run_far_ahead_is_one_evaluation_not_a_replay(
    hass: HomeAssistant, tick: AlmanacTick, almanac_data: AlmanacData
) -> None:
    """**Provisional.** The gap this test exists to make visible rather than hide.

    A dry run evaluates *once*, at the supplied instant, from the engine state as
    it stands now. It does not step the engine forward through the occurrences in
    between. So a dry run six months out is answering "if the engine woke up then,
    having last looked now, what would it conclude" — which is a true and useful
    sentence, and is not the same as "what will have happened by then".

    The alternative rejected was stepping the engine over successive `next_at`
    values to the target instant. It was rejected because it would be a second
    copy of `async_tick`'s scheduling loop — the exact duplication D64 exists to
    prevent — and because it would be no more true: the intervening conditions are
    entity states, and the engine cannot know what they will be.

    What makes this honest rather than a trap is that the plan says so. The window
    the evaluation covers is bounded by D44's budget from `evaluated_through`, and
    `fully_computed` is false, which is the timeline's `not_computed`. It is
    asserted here so that a reader of a far-future dry run has been told.
    """
    schedule_id = await create(hass, almanac_data, "Lights", rules=[at_rule()])
    await prime(tick)

    far = await tick.async_dry_run(at=ny(2028, 10, 2, 17, 30))
    plan = far[schedule_id].plan
    assert not plan.fully_computed
    assert plan.solid_through < ny(2028, 10, 2)


# --- the websocket surface -------------------------------------------------


async def test_websocket_dry_run_returns_the_transitions(
    hass: HomeAssistant,
    setup_almanac: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """What step 9's "preview" button calls, and the shape it gets back."""
    data: AlmanacData = setup_almanac.runtime_data
    assert data.tick is not None
    schedule_id = await create(hass, data, "Lights", rules=[at_rule()])
    await prime(data.tick)

    client = await hass_ws_client(hass)
    await client.send_json(
        {"id": 1, "type": WS_DRY_RUN, "at": ny(2027, 10, 2, 17, 30).isoformat()}
    )
    result = await client.receive_json()

    assert result["success"], result
    view = result["result"]
    assert view["at"] == ny(2027, 10, 2, 17, 30).isoformat()
    assert view["live"] is True
    row = next(
        entry for entry in view["schedules"] if entry["schedule_id"] == schedule_id
    )
    assert [transition["kind"] for transition in row["transitions"]] == [
        str(TransitionKind.FIRE)
    ]
    # The plan travels with the transitions (D63): an occurrence that is neither
    # due nor held produces no transition and is exactly the row a person opened
    # the preview to see.
    assert row["plan"]["occurrences"]


async def test_websocket_dry_run_carries_the_recovery_flag(
    hass: HomeAssistant,
    setup_almanac: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """`live` reaches the planner selection rather than being echoed back."""
    data: AlmanacData = setup_almanac.runtime_data
    assert data.tick is not None
    schedule_id = await create(hass, data, "Lights", rules=[at_rule()])
    await prime(data.tick)

    client = await hass_ws_client(hass)
    await client.send_json(
        {
            "id": 1,
            "type": WS_DRY_RUN,
            "at": ny(2027, 10, 2, 17, 30).isoformat(),
            "live": False,
            "schedule_ids": [schedule_id],
        }
    )
    result = await client.receive_json()

    assert result["success"], result
    assert result["result"]["live"] is False
    assert [
        transition["kind"] for transition in result["result"]["schedules"][0]["transitions"]
    ] == [str(TransitionKind.MISSED)]
