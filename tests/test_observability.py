"""§12 — the events, the logbook line and the display cache (D48-D52).

Almost every assertion here is about **order**, and that is the subject rather
than an accident of how the tests are written. D50's deliverable is a sentence —
"turned on by Shabbat lights" — and whether Home Assistant can produce it depends
entirely on which row carrying a given context id the recorder saw first. So the
event and the service calls it explains go into one list, in one order, and the
tests assert the list. Two separate recorders could not state the property.

The schedule shape, the 2027 window and the two-tick `prime` dance are
`test_tick.py`'s and are reused deliberately: these tests are about what the same
transitions *say*, so they should be produced the same way.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from homeassistant.const import ATTR_ENTITY_ID, ATTR_NAME
from homeassistant.core import Event, HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv

from custom_components.almanac.const import (
    ANCHOR_CLOCK,
    ATTR_ACTIONS,
    ATTR_AT,
    ATTR_BLOCKING,
    ATTR_CAUSE,
    ATTR_KIND,
    ATTR_LATENESS,
    ATTR_RECENT_EXECUTIONS,
    ATTR_RESULT,
    ATTR_RULE_ID,
    ATTR_SCHEDULE_ID,
    CONDITION_COMPARISON,
    DOMAIN,
    END_DURATION,
    EVENT_EXECUTION,
    EVENT_OCCURRENCE,
    EXECUTION_CACHE_SIZE,
    ON_EXIT_RESTORE,
    OPERAND_CONSTANT,
    RECUR_WEEKDAYS,
    RESULT_DROPPED,
    RESULT_FAILED,
    RESULT_FIRED,
    RESULT_NOTHING,
    RULE_AT,
    RULE_DURING,
)
from custom_components.almanac.logbook import async_describe_events
from custom_components.almanac.storage import AlmanacData
from custom_components.almanac.switch import AlmanacScheduleSwitch
from custom_components.almanac.tick import SERVICE_RUN_NOW, AlmanacTick

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


def is_on(entity_id: str, **extra: Any) -> dict[str, Any]:
    """A comparison condition — "this entity is on"."""
    return {
        "kind": CONDITION_COMPARISON,
        "entity_id": entity_id,
        "operator": "eq",
        "value": {"kind": OPERAND_CONSTANT, "value": "on"},
        **extra,
    }


@pytest.fixture(autouse=True)
async def _located(hass: HomeAssistant) -> None:
    """Put `hass` in New York before anything is set up."""
    await hass.config.async_set_time_zone("America/New_York")
    await hass.config.async_update(latitude=LATITUDE, longitude=LONGITUDE, elevation=0)


@dataclass
class Timeline:
    """Everything almanac did, events and service calls interleaved.

    One list, because the property under test is that the event comes *first*.
    Keeping the events in one list and the calls in another would let both
    assertions pass with the two in the wrong order relative to each other, which
    is the only failure mode that matters here.
    """

    steps: list[str] = field(default_factory=list)
    occurrences: list[dict[str, Any]] = field(default_factory=list)
    executions: list[dict[str, Any]] = field(default_factory=list)
    contexts: list[str] = field(default_factory=list)


@pytest.fixture
def timeline(hass: HomeAssistant) -> Timeline:
    """Record almanac's events and its service calls into one ordered list."""
    seen = Timeline()

    @callback
    def occurrence(event: Event[dict[str, Any]]) -> None:
        seen.steps.append(EVENT_OCCURRENCE)
        seen.occurrences.append(dict(event.data))
        seen.contexts.append(event.context.id)

    @callback
    def execution(event: Event[dict[str, Any]]) -> None:
        seen.steps.append(EVENT_EXECUTION)
        seen.executions.append(dict(event.data))
        seen.contexts.append(event.context.id)

    hass.bus.async_listen(EVENT_OCCURRENCE, occurrence)
    hass.bus.async_listen(EVENT_EXECUTION, execution)

    async def record(call: ServiceCall) -> None:
        seen.steps.append(f"{call.domain}.{call.service}")
        seen.contexts.append(call.context.id)

    async def turn_on(call: ServiceCall) -> None:
        seen.steps.append("light.turn_on")
        seen.contexts.append(call.context.id)
        for entity_id in cv.ensure_list(call.data.get("entity_id", [])):
            hass.states.async_set(entity_id, "on")

    async def turn_off(call: ServiceCall) -> None:
        seen.steps.append("light.turn_off")
        seen.contexts.append(call.context.id)
        for entity_id in cv.ensure_list(call.data.get("entity_id", [])):
            hass.states.async_set(entity_id, "off")

    async def boom(call: ServiceCall) -> None:
        seen.steps.append("notify.boom")
        raise RuntimeError("the announcement failed")

    hass.services.async_register("light", "turn_on", turn_on)
    hass.services.async_register("light", "turn_off", turn_off)
    hass.services.async_register("notify", "fired", record)
    hass.services.async_register("notify", "enter", record)
    hass.services.async_register("notify", "exit", record)
    hass.services.async_register("notify", "boom", boom)
    hass.services.async_register("script", "turn_on", record)
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
    """One tick at 16:00 on the test day, before anything is due."""
    await tick.async_tick(ny(2027, 10, 2, 16, 0))


# --- D49/D50: the event, and when it is fired ------------------------------


async def test_the_occurrence_event_precedes_the_service_calls_it_explains(
    hass: HomeAssistant,
    tick: AlmanacTick,
    almanac_data: AlmanacData,
    timeline: Timeline,
) -> None:
    """D50, and the order the whole causation chain rests on.

    `components/logbook/processor.py::_humanify` memoises the **first** row it
    sees carrying a context id and treats it as the cause of every later row with
    the same one. Fire the event after the service calls and the earliest row is
    the `call_service` one, which `ContextAugmenter.augment` renders as "caused by
    service light.turn_on" — the uninformative line this project exists to
    replace. The event has to go first, and one `assert` on an ordered list is the
    only way to say so that cannot pass by accident.
    """
    await create(
        hass,
        almanac_data,
        "Fires",
        rules=[at_rule(actions=[service("notify.fired")])],
    )

    await prime(tick)
    await prime(tick)
    await tick.async_tick(ny(2027, 10, 2, 17, 30))

    assert timeline.steps == [EVENT_OCCURRENCE, "notify.fired", EVENT_EXECUTION]


async def test_a_manual_run_records_its_execution_and_announces_no_occurrence(
    hass: HomeAssistant,
    tick: AlmanacTick,
    almanac_data: AlmanacData,
    timeline: Timeline,
) -> None:
    """D116 and D109, which pull in opposite directions and are both satisfied.

    D109 refuses the *occurrence* event for a manual run, and the reason is about
    the logbook: `_humanify` memoises the first row carrying a context id, so an
    occurrence event would win that slot and overwrite "*Daniel* ran this by hand"
    with "almanac decided this". That argument is about one row and one memo, and
    it does not reach the execution event, which has no logbook description at all.

    D116 is the other half. `run_now` ran real actions against real devices, and
    with neither event fired it left no trace in the one place D63 built to show
    what actually happened — so the single kind of occurrence the engine genuinely
    did not predict was the one kind the view for unpredicted things could not
    draw. One event, not two: the cause named honestly, the effect recorded.

    The ordered list is the assertion because the ordering is the property. The
    execution event goes out *after* the actions, exactly as it does on the
    scheduled path, so the `call_service` row stays the earliest row under this
    context and keeps the logbook attribution D109 is protecting.
    """
    schedule_id = await create(
        hass,
        almanac_data,
        "Run me",
        rules=[at_rule(actions=[service("notify.fired")])],
    )

    await hass.services.async_call(
        DOMAIN, SERVICE_RUN_NOW, {"schedule_id": schedule_id}, blocking=True
    )

    assert timeline.steps == ["notify.fired", EVENT_EXECUTION]
    assert EVENT_OCCURRENCE not in timeline.steps
    assert timeline.executions[0][ATTR_SCHEDULE_ID] == schedule_id
    assert timeline.executions[0][ATTR_RESULT] == RESULT_FIRED


async def test_a_manual_run_that_did_nothing_fires_no_event(
    hass: HomeAssistant,
    tick: AlmanacTick,
    almanac_data: AlmanacData,
    timeline: Timeline,
) -> None:
    """The `attempted` gate, which D116 inherits rather than invents.

    `_async_execute` skips the execution event when nothing was attempted, because
    an occurrence that ran no actions has no results and the event would repeat
    what the occurrence event already said. A manual run has no occurrence event to
    repeat, but the reasoning that matters survives: a rule with no actions did
    nothing, and a row saying nothing happened is a row per manual run that says
    nothing. D52's cache still takes it — see the next test.
    """
    schedule_id = await create(hass, almanac_data, "Empty", rules=[at_rule()])

    await hass.services.async_call(
        DOMAIN, SERVICE_RUN_NOW, {"schedule_id": schedule_id}, blocking=True
    )

    assert timeline.steps == []


async def test_a_manual_run_reaches_the_status_cache(
    hass: HomeAssistant,
    tick: AlmanacTick,
    almanac_data: AlmanacData,
    timeline: Timeline,
) -> None:
    """D52's cache takes every transition, and a manual run is one.

    "Why did the light come on at 14:32" is the question this cache is opened to
    answer, and "someone ran it by hand" is an answer. Leaving it out would make
    the more-info dialog contradict the timeline, which reads the same fact from
    the recorder — two surfaces showing *actual*, disagreeing.
    """
    schedule_id = await create(
        hass,
        almanac_data,
        "Run me",
        rules=[at_rule(actions=[service("notify.fired")])],
    )

    await hass.services.async_call(
        DOMAIN, SERVICE_RUN_NOW, {"schedule_id": schedule_id}, blocking=True
    )

    entries = almanac_data.status.async_recent(schedule_id)
    assert len(entries) == 1
    assert entries[0][ATTR_RESULT] == RESULT_FIRED


async def test_the_event_and_the_service_calls_share_one_context(
    hass: HomeAssistant,
    tick: AlmanacTick,
    almanac_data: AlmanacData,
    timeline: Timeline,
) -> None:
    """D50 — the *same* context, not a parent and a child.

    Core's own arrangement, in `components/automation/__init__.py`: one
    `trigger_context` passed both to `async_fire_internal(
    EVENT_AUTOMATION_TRIGGERED, ...)` and to `action_script.async_run(...)`. A
    child context would fail on the *target* entity's logbook page, because that
    page's context-id set is built from the light's own rows plus events whose
    JSON `entity_id` matches the light — and a parent event carrying the
    schedule's entity_id is not in it.
    """
    hass.states.async_set(HALL, "off")
    await create(
        hass,
        almanac_data,
        "Enters",
        rules=[
            during_rule(state=hall_on(), enter_actions=[service("notify.enter")])
        ],
    )

    await prime(tick)
    await tick.async_tick(ny(2027, 10, 2, 17, 30))

    assert timeline.steps == [
        EVENT_OCCURRENCE,
        "light.turn_on",
        "notify.enter",
        EVENT_EXECUTION,
    ]
    assert len(set(timeline.contexts)) == 1


async def test_one_occurrence_event_carries_the_decision(
    hass: HomeAssistant,
    tick: AlmanacTick,
    almanac_data: AlmanacData,
    timeline: Timeline,
) -> None:
    """D49's payload, minus the execution outcomes that do not exist yet.

    The `entity_id` is asserted at the **top level** and spelled exactly, because
    that is not cosmetic: `components/recorder/db_schema.py` matches events to an
    entity with `ENTITY_ID_IN_EVENT = EVENT_DATA_JSON["entity_id"]`, so a nested
    or differently-named key would produce an event the logbook can never attach
    to the schedule.
    """
    await create(
        hass,
        almanac_data,
        "Shabbat lights",
        rules=[at_rule(actions=[service("notify.fired")])],
    )

    await prime(tick)
    await prime(tick)
    await tick.async_tick(ny(2027, 10, 2, 17, 4))

    (payload,) = timeline.occurrences
    assert payload[ATTR_ENTITY_ID] == "switch.shabbat_lights"
    assert payload[ATTR_NAME] == "Shabbat lights"
    assert payload[ATTR_RULE_ID] == "r1"
    assert payload[ATTR_KIND] == "fire"
    assert payload[ATTR_AT] == ny(2027, 10, 2, 17, 0).isoformat()
    # "The 17:00 occurrence, handled at 17:04" — both instants, which is the pair
    # `Transition` keeps for exactly this line.
    assert payload[ATTR_LATENESS] == 4 * 60
    assert payload[ATTR_BLOCKING] == []
    assert payload[ATTR_CAUSE] is None


async def test_a_skipped_occurrence_names_the_condition_that_blocked_it(
    hass: HomeAssistant,
    tick: AlmanacTick,
    almanac_data: AlmanacData,
    timeline: Timeline,
) -> None:
    """D49's headline payload field, and D24's reason for existing.

    "Skipped: cleaning crew present" is actionable and "skipped" is not. The label
    is the user's own string and cannot be reconstructed later from the stored
    rule alone, so it goes in the event at the moment the decision is made.

    A skipped occurrence gets an occurrence event and **no** execution event:
    nothing ran, so there are no per-action results to report and a second row
    would say nothing the first did not.
    """
    hass.states.async_set("binary_sensor.crew", "off")
    await create(
        hass,
        almanac_data,
        "Announcement",
        rules=[
            at_rule(
                actions=[service("notify.fired")],
                conditions=[is_on("binary_sensor.crew", label="cleaning crew present")],
            )
        ],
    )

    await prime(tick)
    await tick.async_tick(ny(2027, 10, 2, 17, 30))

    assert timeline.steps == [EVENT_OCCURRENCE]
    (payload,) = timeline.occurrences
    assert payload[ATTR_KIND] == "skipped"
    assert payload[ATTR_BLOCKING] == ["cleaning crew present"]


async def test_an_exit_says_why_the_interval_ended(
    hass: HomeAssistant,
    tick: AlmanacTick,
    almanac_data: AlmanacData,
    timeline: Timeline,
) -> None:
    """`ExitCause` reaches the payload — D3's three exit paths, four reasons.

    Turning the switch off is D91's `DISARMED`, and "the schedule was turned off"
    is a different sentence from "the window ended" for somebody standing in a
    room whose lights just changed.
    """
    hass.states.async_set(HALL, "off")
    schedule_id = await create(
        hass,
        almanac_data,
        "Evening",
        rules=[during_rule(state=hall_on(), on_exit={"kind": ON_EXIT_RESTORE})],
    )

    await prime(tick)
    await tick.async_tick(ny(2027, 10, 2, 17, 30))
    timeline.occurrences.clear()

    await almanac_data.schedules.async_update_item(schedule_id, {"enabled": False})
    await hass.async_block_till_done()
    # Two ticks, for the reason `test_tick.py` writes out at length: the collection
    # write re-evaluated at the real clock and pulled `evaluated_through` back to
    # today, so D44's ninety-day budget hides the 2027 occurrence until a tick has
    # moved it forward again. D91's exit needs the occurrence to exist, because its
    # cause is a statement about one.
    await tick.async_tick(ny(2027, 10, 2, 17, 40))
    await tick.async_tick(ny(2027, 10, 2, 17, 45))

    assert ("exit", "disarmed") in [
        (payload[ATTR_KIND], payload[ATTR_CAUSE]) for payload in timeline.occurrences
    ]


# --- D49: the execution event ----------------------------------------------


async def test_the_execution_event_carries_every_action_separately(
    hass: HomeAssistant,
    tick: AlmanacTick,
    almanac_data: AlmanacData,
    timeline: Timeline,
) -> None:
    """D32's per-action results, in D49's payload, and the reason they are split.

    An aggregate boolean would lose exactly what the log exists to add: the second
    action failed, the first did not, and D32 guarantees the first ran anyway.
    """
    await create(
        hass,
        almanac_data,
        "Two actions",
        rules=[
            at_rule(actions=[service("notify.fired"), service("notify.boom")]),
        ],
    )

    await prime(tick)
    await prime(tick)
    await tick.async_tick(ny(2027, 10, 2, 17, 30))

    (payload,) = timeline.executions
    assert payload[ATTR_RESULT] == RESULT_FAILED
    assert [action["status"] for action in payload[ATTR_ACTIONS]] == [
        "succeeded",
        "failed",
    ]
    assert [action["index"] for action in payload[ATTR_ACTIONS]] == [0, 1]
    assert payload[ATTR_SCHEDULE_ID] == timeline.occurrences[0][ATTR_SCHEDULE_ID]


async def test_a_dropped_script_is_reported_as_dropped_not_fired(
    hass: HomeAssistant,
    tick: AlmanacTick,
    almanac_data: AlmanacData,
    timeline: Timeline,
) -> None:
    """D31 reaching the audit trail, which is the point of D31.

    A `mode: single` script that is already running logs "Already running" and
    returns, so the call looks like a success to anybody who is not checking. An
    occurrence whose only action was swallowed is *not* `fired`, and the event has
    to be the place that says so — otherwise the recorder holds a history that
    agrees with the scheduler Home Assistant already has.
    """
    hass.states.async_set("script.announce", "on", {"mode": "single", "current": 1})
    await create(
        hass,
        almanac_data,
        "Announce",
        rules=[at_rule(actions=[{"kind": "script", "script": "script.announce"}])],
    )

    await prime(tick)
    await prime(tick)
    await tick.async_tick(ny(2027, 10, 2, 17, 30))

    (payload,) = timeline.executions
    assert payload[ATTR_RESULT] == RESULT_DROPPED
    assert payload[ATTR_ACTIONS][0]["status"] == "dropped"
    # Nothing was called, which is what "dropped" means.
    assert not [step for step in timeline.steps if step.startswith("script.")]


async def test_an_occurrence_that_does_nothing_fires_no_execution_event(
    hass: HomeAssistant,
    tick: AlmanacTick,
    almanac_data: AlmanacData,
    timeline: Timeline,
) -> None:
    """A rule with no actions is legal — it is how a timeline-only schedule is
    written — and it is not an execution.

    It still reaches the display cache, as `nothing` rather than as `fired`. D46
    makes the same distinction for the same reason: an occurrence that performed
    no actions has not succeeded at anything, and a read-out that called it
    `fired` would be the "it said it ran" problem one layer up.
    """
    schedule_id = await create(hass, almanac_data, "Marker", rules=[at_rule()])

    await prime(tick)
    await prime(tick)
    await tick.async_tick(ny(2027, 10, 2, 17, 30))

    assert timeline.steps == [EVENT_OCCURRENCE]
    (entry, *_) = almanac_data.status.async_recent(schedule_id)
    assert entry[ATTR_RESULT] == RESULT_NOTHING


# --- D51: the logbook platform ---------------------------------------------


@dataclass(frozen=True)
class _Row:
    """The two attributes `async_describe_occurrence` reads off a logbook row.

    A stand-in rather than a real `LazyEventPartialState`, which is built from a
    recorder row and needs a database. The contract being checked is the one core
    states: the callback is handed something with `.data` and `.context_id`.
    """

    data: dict[str, Any]
    context_id: str | None = "01J0"


def describe(payload: dict[str, Any]) -> dict[str, Any]:
    """Run almanac's logbook platform over one event payload."""
    registered: dict[str, Any] = {}

    @callback
    def async_describe_event(domain: str, event_type: str, describer: Any) -> None:
        registered[event_type] = (domain, describer)

    async_describe_events(None, async_describe_event)  # type: ignore[arg-type]
    # D51 describes the occurrence event and deliberately not the execution one —
    # `_humanify` skips an unregistered type, so the results stay recorded and
    # queryable without a second logbook row per occurrence.
    assert set(registered) == {EVENT_OCCURRENCE}
    domain, describer = registered[EVENT_OCCURRENCE]
    assert domain == DOMAIN
    return describer(_Row(payload))


def occurrence(**payload: Any) -> dict[str, Any]:
    """A minimal occurrence payload with the keys the describer reads."""
    return {
        ATTR_NAME: "Shabbat lights",
        ATTR_ENTITY_ID: "switch.shabbat_lights",
        ATTR_SCHEDULE_ID: "01J",
        ATTR_KIND: "fire",
        ATTR_LATENESS: 0.0,
        ATTR_BLOCKING: [],
        ATTR_CAUSE: None,
        **payload,
    }


def test_the_logbook_line_names_the_schedule_and_what_it_did() -> None:
    """D51's whole purpose: a row a person can read, attached to the schedule."""
    entry = describe(occurrence())

    assert entry["name"] == "Shabbat lights"
    assert entry["message"] == "fired"
    assert entry["entity_id"] == "switch.shabbat_lights"
    assert entry["context_id"] == "01J0"


def test_the_logbook_line_carries_the_blocking_label() -> None:
    """D24 again, on the surface a person actually looks at."""
    entry = describe(
        occurrence(kind="skipped", blocking=["cleaning crew present", "guests"])
    )

    assert entry["message"] == "was skipped: cleaning crew present, guests"


def test_the_logbook_line_distinguishes_the_reasons_an_interval_ended() -> None:
    """Four causes, four sentences. "The window ended" and "you turned it off"
    are not the same thing to explain."""
    assert describe(occurrence(kind="exit", cause="window_end"))["message"] == "ended"
    assert (
        describe(occurrence(kind="exit", cause="disarmed"))["message"]
        == "ended because the schedule was turned off"
    )
    assert (
        describe(occurrence(kind="exit", cause="conditions"))["message"]
        == "ended because its conditions stopped holding"
    )


def test_the_logbook_line_says_how_late_the_engine_was() -> None:
    """Sub-second lateness is the cost of a timer and is not worth a word."""
    assert describe(occurrence(lateness=0.2))["message"] == "fired"
    assert describe(occurrence(lateness=240.0))["message"] == "fired (240s late)"


def test_a_resume_is_not_described_as_a_start() -> None:
    """D90 — a restart mid-interval re-applies state and announces nothing, and a
    line calling that "started" would send a reader hunting for a schedule change
    that never happened."""
    assert (
        describe(occurrence(kind="resume"))["message"] == "resumed after a restart"
    )


# --- D52: the display cache ------------------------------------------------


async def test_the_switch_shows_the_last_occurrences_and_keeps_them_out_of_the_db(
    hass: HomeAssistant,
    tick: AlmanacTick,
    almanac_data: AlmanacData,
    timeline: Timeline,
) -> None:
    """D52 — a churning attribute, live in the UI and excluded from the recorder.

    `_unrecorded_attributes` is asserted on the class rather than by inspecting a
    database, because that frozenset *is* the mechanism: `helpers/entity.py`
    unions it across the hierarchy in `__init_subclass__` and publishes it through
    `state_info`, which the recorder reads to drop the keys. Nothing else about
    the entity opts out.
    """
    assert ATTR_RECENT_EXECUTIONS in AlmanacScheduleSwitch._unrecorded_attributes

    await create(
        hass,
        almanac_data,
        "Shabbat lights",
        rules=[at_rule(actions=[service("notify.fired")])],
    )

    await prime(tick)
    await prime(tick)
    await tick.async_tick(ny(2027, 10, 2, 17, 30))
    await hass.async_block_till_done()

    state = hass.states.get("switch.shabbat_lights")
    assert state is not None
    (entry, *_) = state.attributes[ATTR_RECENT_EXECUTIONS]
    assert entry[ATTR_KIND] == "fire"
    assert entry[ATTR_RESULT] == RESULT_FIRED
    assert entry[ATTR_RULE_ID] == "r1"
    assert entry[ATTR_ACTIONS][0]["target"] == "notify.fired"


async def test_the_display_cache_records_what_did_not_happen(
    hass: HomeAssistant,
    tick: AlmanacTick,
    almanac_data: AlmanacData,
    timeline: Timeline,
) -> None:
    """"Why did nothing happen at 17:00" is what the dialog is opened to answer."""
    hass.states.async_set("binary_sensor.crew", "off")
    await create(
        hass,
        almanac_data,
        "Announcement",
        rules=[
            at_rule(
                actions=[service("notify.fired")],
                conditions=[is_on("binary_sensor.crew", label="cleaning crew present")],
            )
        ],
    )

    await prime(tick)
    await tick.async_tick(ny(2027, 10, 2, 17, 30))
    await hass.async_block_till_done()

    state = hass.states.get("switch.announcement")
    assert state is not None
    (entry, *_) = state.attributes[ATTR_RECENT_EXECUTIONS]
    assert entry[ATTR_KIND] == "skipped"
    assert entry[ATTR_RESULT] is None
    assert entry[ATTR_BLOCKING] == ["cleaning crew present"]


async def test_the_display_cache_is_capped_and_newest_first(
    hass: HomeAssistant,
    tick: AlmanacTick,
    almanac_data: AlmanacData,
    timeline: Timeline,
) -> None:
    """D52 is a *cache*, not a log. The durable copy is the recorder's (D48), so
    this is bounded and the bound is small — every entry is re-serialised to every
    connected frontend whenever anything fires."""
    hours = range(17, 17 + EXECUTION_CACHE_SIZE + 2)
    schedule_id = await create(
        hass,
        almanac_data,
        "Hourly",
        rules=[
            at_rule(f"r{hour}", f"{hour}:00", actions=[service("notify.fired")])
            for hour in hours
        ],
    )

    await prime(tick)
    await prime(tick)
    for hour in hours:
        await tick.async_tick(ny(2027, 10, 2, hour, 30))
    await hass.async_block_till_done()

    entries = almanac_data.status.async_recent(schedule_id)
    assert len(entries) == EXECUTION_CACHE_SIZE
    ats = [entry[ATTR_AT] for entry in entries]
    assert ats == sorted(ats, reverse=True)


async def test_the_cache_is_not_persisted(
    hass: HomeAssistant, tick: AlmanacTick, almanac_data: AlmanacData
) -> None:
    """D48 — no private audit store. The runtime record holds the engine's state
    and nothing that duplicates an event."""
    schedule_id = await create(
        hass, almanac_data, "Fires", rules=[at_rule(actions=[service("notify.fired")])]
    )

    await prime(tick)
    await prime(tick)
    await tick.async_tick(ny(2027, 10, 2, 17, 30))

    record = almanac_data.runtime.async_get(schedule_id)
    assert ATTR_RECENT_EXECUTIONS not in record
    assert ATTR_ACTIONS not in record


# --- D55: the next-trigger sensor ------------------------------------------


async def test_the_sensor_shows_the_instant_the_tick_computed(
    hass: HomeAssistant, tick: AlmanacTick, almanac_data: AlmanacData
) -> None:
    """D55 fed by D64's one clock reader.

    The entity does not work this out; the engine hands it over. That is what
    makes the sensor, the timeline and the live engine one answer rather than
    three implementations that can disagree.
    """
    await create(hass, almanac_data, "Shabbat lights", rules=[at_rule()])

    await tick.async_tick(ny(2027, 10, 2, 16, 0))
    await hass.async_block_till_done()

    state = hass.states.get("sensor.shabbat_lights_next_trigger")
    assert state is not None
    # Compared as an instant, not as a string. `SensorEntity` renders a
    # `device_class: timestamp` value through `util/dt.py::as_utc`, so the state
    # is "…T21:00:00+00:00" while the engine computed 17:00 in New York. A.13's
    # discipline, in the one place a test is most likely to fall for it: those two
    # spellings are the same moment, and a string comparison would report a bug
    # that is not there and hide a DST one that would be.
    assert datetime.fromisoformat(state.state) == ny(2027, 10, 2, 17, 0)


async def test_the_sensor_is_unknown_when_nothing_is_scheduled(
    hass: HomeAssistant, tick: AlmanacTick, almanac_data: AlmanacData
) -> None:
    """D13's *unknown*, rendered honestly rather than guessed at.

    A schedule whose window has closed has no next trigger, and `None` is the
    answer rather than the last one it had.
    """
    await create(
        hass,
        almanac_data,
        "Past",
        date_window={"from": "2020-01-01", "until": "2020-01-02"},
        rules=[at_rule()],
    )

    await tick.async_tick(ny(2027, 10, 2, 16, 0))
    await hass.async_block_till_done()

    state = hass.states.get("sensor.past_next_trigger")
    assert state is not None
    assert state.state == "unknown"


async def test_the_sensor_is_not_rewritten_when_nothing_moved(
    hass: HomeAssistant, tick: AlmanacTick, almanac_data: AlmanacData
) -> None:
    """A tick per minute per schedule must not be a state write per minute.

    The recorder stores every state change, and D48 leans on it for the audit
    trail — filling it with rows that repeat the previous row would be this
    integration degrading the thing it depends on.
    """
    await create(hass, almanac_data, "Shabbat lights", rules=[at_rule()])

    await tick.async_tick(ny(2027, 10, 2, 16, 0))
    await hass.async_block_till_done()
    before = hass.states.get("sensor.shabbat_lights_next_trigger")
    assert before is not None

    await tick.async_tick(ny(2027, 10, 2, 16, 1))
    await hass.async_block_till_done()
    after = hass.states.get("sensor.shabbat_lights_next_trigger")
    assert after is not None
    assert after.last_updated == before.last_updated
