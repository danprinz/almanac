"""Actions, desired state, scripts and restore — D27–D32 and D3.

These are the only tests in the suite that assert on the world changing, because
`actions.py` is the only module that changes it. Two things are being pinned down
rather than merely exercised:

- **D32's three properties.** A failing action does not stop the ones after it, it
  does not stop the desired state, and every action gets its own result. Each has
  its own test, because each is a promise the completion axes read.
- **D31's pre-flight.** A.10 found that an already-running `single`-mode script
  logs "Already running" and returns without raising, so a caller cannot tell it
  from success. Every mode is asserted here, because the whole point of the
  pre-flight is that the *log* distinguishes them.

Service calls are recorded with `async_mock_service`, which registers a real
service, so `blocking=True` means what it means in production.
"""

from __future__ import annotations

from typing import Any

import pytest
import voluptuous as vol

from homeassistant.core import Context, HomeAssistant, ServiceCall

from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.almanac.actions import (
    ActionStatus,
    ExecutionReport,
    StateSnapshot,
    async_apply_state,
    async_restore,
    async_run_actions,
    capture_state,
    script_drop_reason,
)
from custom_components.almanac.schema import ACTIONS_SCHEMA, DESIRED_STATE_SCHEMA

# --- helpers ---------------------------------------------------------------


def actions(*raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Validated actions, so the tests see the defaults the store holds."""
    return list(ACTIONS_SCHEMA(list(raw)))


def desired(**raw: Any) -> dict[str, Any]:
    """A validated desired state (D28)."""
    return dict(DESIRED_STATE_SCHEMA(raw))


def service(name: str, **extra: Any) -> dict[str, Any]:
    """A `kind: service` action."""
    return {"kind": "service", "service": name, **extra}


def script(entity_id: str, **extra: Any) -> dict[str, Any]:
    """A `kind: script` action."""
    return {"kind": "script", "script": entity_id, **extra}


@pytest.fixture
def context() -> Context:
    """One context per occurrence, as the tick makes it (D50)."""
    return Context()


# --- D27, D32: an action list runs in order, one result each ----------------


async def test_actions_run_in_order_with_one_result_each(
    hass: HomeAssistant, context: Context
) -> None:
    """Sequential, not gathered — D30's `wait` is meaningless otherwise."""
    calls = async_mock_service(hass, "light", "turn_on")
    async_mock_service(hass, "notify", "persistent_notification")

    results = await async_run_actions(
        hass,
        actions(
            service("light.turn_on", target={"entity_id": "light.hall"}),
            service("notify.persistent_notification", data={"message": "on"}),
        ),
        context=context,
    )

    assert [result.index for result in results] == [0, 1]
    assert [result.target for result in results] == [
        "light.turn_on",
        "notify.persistent_notification",
    ]
    assert all(result.status is ActionStatus.SUCCEEDED for result in results)
    assert calls[0].data == {"entity_id": ["light.hall"]}


async def test_the_context_reaches_the_service_call(
    hass: HomeAssistant, context: Context
) -> None:
    """D50's causation chain starts here, so the context has to be threaded."""
    calls = async_mock_service(hass, "light", "turn_on")

    await async_run_actions(hass, actions(service("light.turn_on")), context=context)

    assert calls[0].context is context


async def test_a_failing_action_does_not_stop_the_next_one(
    hass: HomeAssistant, context: Context
) -> None:
    """D32's first property. The unresolvable call is a real `ServiceNotFound`."""
    calls = async_mock_service(hass, "light", "turn_on")

    results = await async_run_actions(
        hass,
        actions(service("nosuch.service"), service("light.turn_on")),
        context=context,
    )

    assert results[0].status is ActionStatus.FAILED
    assert results[1].status is ActionStatus.SUCCEEDED
    assert len(calls) == 1


async def test_an_exception_inside_a_service_becomes_a_result(
    hass: HomeAssistant, context: Context
) -> None:
    """`blocking=True` is what makes a handler's failure observable at all."""

    async def _boom(_call: ServiceCall) -> None:
        raise ValueError("the bulb is on fire")

    hass.services.async_register("light", "turn_on", _boom)

    results = await async_run_actions(hass, actions(service("light.turn_on")), context=context)

    assert results[0].status is ActionStatus.FAILED
    assert "on fire" in str(results[0].detail)


async def test_the_stored_data_is_not_mutated_by_the_target_merge(
    hass: HomeAssistant, context: Context
) -> None:
    """Verified against the installed package: `async_call` does
    `service_data.update(target)` on the mapping it is handed.

    Passing the stored `data` dict straight through would write `entity_id` into
    the schedule's own storage payload, so every call site copies. This asserts the
    copy, because the bug it prevents is invisible until the store is next read.
    """
    async_mock_service(hass, "light", "turn_on")
    validated = actions(
        service("light.turn_on", data={"brightness": 120}, target={"entity_id": "light.hall"})
    )

    await async_run_actions(hass, validated, context=context)

    assert validated[0]["data"] == {"brightness": 120}


# --- D30, D31: scripts -----------------------------------------------------


def _script_state(
    hass: HomeAssistant, state: str, **attributes: Any
) -> None:
    """Publish a script entity with the attributes D31's pre-flight reads."""
    hass.states.async_set("script.announce", state, attributes)


def test_an_idle_script_is_not_dropped(hass: HomeAssistant) -> None:
    """The base case the others depart from."""
    _script_state(hass, "off", mode="single")

    assert script_drop_reason(hass, "script.announce") is None


def test_a_running_single_script_is_dropped(hass: HomeAssistant) -> None:
    """A.10 — this is the case core reports as a log line and a `None` return."""
    _script_state(hass, "on", mode="single", current=1)

    reason = script_drop_reason(hass, "script.announce")

    assert reason is not None
    assert "single" in reason


def test_a_running_restart_script_is_not_dropped(hass: HomeAssistant) -> None:
    """`restart` stops the running copy and starts again, so the call does run."""
    _script_state(hass, "on", mode="restart", current=1)

    assert script_drop_reason(hass, "script.announce") is None


@pytest.mark.parametrize("mode", ["queued", "parallel"])
async def test_a_script_at_its_run_limit_is_dropped(hass: HomeAssistant, mode: str) -> None:
    """These two modes drop only once `current` has reached `max`."""
    _script_state(hass, "on", mode=mode, current=2, max=2)

    reason = script_drop_reason(hass, "script.announce")

    assert reason is not None
    assert "run limit" in reason


@pytest.mark.parametrize("mode", ["queued", "parallel"])
def test_a_script_below_its_run_limit_is_not_dropped(
    hass: HomeAssistant, mode: str
) -> None:
    """Below the limit the run is queued or parallel, not discarded."""
    _script_state(hass, "on", mode=mode, current=1, max=3)

    assert script_drop_reason(hass, "script.announce") is None


def test_a_queued_script_not_publishing_max_is_not_dropped(hass: HomeAssistant) -> None:
    """Proceeding is the safer reading of an entity that is under-reporting."""
    _script_state(hass, "on", mode="queued", current=9)

    assert script_drop_reason(hass, "script.announce") is None


def test_a_missing_script_is_not_a_drop(hass: HomeAssistant) -> None:
    """A broken reference must not be filed under "worked as intended"."""
    assert script_drop_reason(hass, "script.gone") is None


async def test_a_missing_script_fails_rather_than_succeeding(
    hass: HomeAssistant, context: Context
) -> None:
    """An entity service call with an unmatched `entity_id` is not an error in
    Home Assistant, so without the explicit check a renamed script would be
    reported as having run."""
    async_mock_service(hass, "script", "turn_on")

    results = await async_run_actions(hass, actions(script("script.gone")), context=context)

    assert results[0].status is ActionStatus.FAILED
    assert results[0].detail == "no such script entity"


async def test_a_dropped_script_is_reported_as_dropped_not_failed(
    hass: HomeAssistant, context: Context
) -> None:
    """D31 — and the distinction is load-bearing for the completion axes: a drop
    is not a success, and it is not a configuration error either."""
    calls = async_mock_service(hass, "script", "turn_on")
    _script_state(hass, "on", mode="single", current=1)

    results = await async_run_actions(
        hass, actions(script("script.announce")), context=context
    )

    assert results[0].status is ActionStatus.DROPPED
    assert not calls


async def test_a_script_is_started_non_blocking_by_default(
    hass: HomeAssistant, context: Context
) -> None:
    """D30's default. `script.turn_on` passes `wait=False` (A.10), and the fields
    go in under `variables` on that path."""
    calls = async_mock_service(hass, "script", "turn_on")
    _script_state(hass, "off", mode="single")

    results = await async_run_actions(
        hass, actions(script("script.announce", fields={"who": "shabbat"})), context=context
    )

    assert results[0].status is ActionStatus.SUCCEEDED
    assert calls[0].data == {
        "entity_id": "script.announce",
        "variables": {"who": "shabbat"},
    }


async def test_waiting_calls_the_script_as_its_own_service(
    hass: HomeAssistant, context: Context
) -> None:
    """A.10 — `script.<object_id>` waits and passes `variables=service.data`, so
    the fields are the payload rather than a key inside it."""
    calls = async_mock_service(hass, "script", "announce")
    _script_state(hass, "off", mode="single")

    results = await async_run_actions(
        hass,
        actions(script("script.announce", fields={"who": "shabbat"}, wait=True, timeout=5)),
        context=context,
    )

    assert results[0].status is ActionStatus.SUCCEEDED
    assert calls[0].data == {"who": "shabbat"}


def test_waiting_without_a_timeout_is_refused_at_the_schema() -> None:
    """D30 — an opt-in wait requires a timeout, so nothing downstream can await
    forever. Asserted here rather than only in `test_schema.py` because it is the
    reason `_async_run_script` may read `timeout` unconditionally."""
    with pytest.raises(vol.Invalid):
        actions(script("script.announce", wait=True))


# --- D28: desired state ----------------------------------------------------


async def test_desired_state_is_applied_through_reproduce_state(
    hass: HomeAssistant, context: Context
) -> None:
    """The default path. One result per entity, all with the same verdict."""
    calls = async_mock_service(hass, "light", "turn_on")
    hass.states.async_set("light.hall", "off")

    results = await async_apply_state(
        hass, desired(entities=[{"entity_id": "light.hall", "state": "on"}]), context=context
    )

    assert [result.status for result in results] == [ActionStatus.SUCCEEDED]
    assert calls[0].data["entity_id"] == "light.hall"


async def test_an_override_replaces_the_reproduce_state_call_entirely(
    hass: HomeAssistant, context: Context
) -> None:
    """D28's escape hatch, and A.5 is why it exists: for `climate`, core's helper
    issues up to seven sequential blocking calls and skips nothing."""
    reproduce = async_mock_service(hass, "climate", "set_temperature")
    hass.states.async_set("climate.lounge", "heat")

    results = await async_apply_state(
        hass,
        desired(
            entities=[{"entity_id": "climate.lounge", "state": "heat"}],
            override=[
                service(
                    "climate.set_temperature",
                    target={"entity_id": "climate.lounge"},
                    data={"temperature": 21, "hvac_mode": "heat"},
                )
            ],
        ),
        context=context,
    )

    assert [result.status for result in results] == [ActionStatus.SUCCEEDED]
    assert len(reproduce) == 1
    assert reproduce[0].data["temperature"] == 21


async def test_an_entity_with_attributes_only_keeps_its_current_state(
    hass: HomeAssistant, context: Context
) -> None:
    """"Set the temperature, leave the mode alone" — the case D28's override was
    added for, expressed without one.

    `climate.set_hvac_mode` has to be mocked too, even though the desired mode is
    the one the entity is already in, and that is A.5 demonstrated rather than
    incidental: `climate/reproduce_state.py` does not skip attributes that already
    match current state. It is the reason D28 carries an override at all.
    """
    async_mock_service(hass, "climate", "set_temperature")
    async_mock_service(hass, "climate", "set_hvac_mode")
    hass.states.async_set("climate.lounge", "cool")

    results = await async_apply_state(
        hass,
        desired(entities=[{"entity_id": "climate.lounge", "attributes": {"temperature": 19}}]),
        context=context,
    )

    assert [result.status for result in results] == [ActionStatus.SUCCEEDED]


async def test_an_attributes_only_row_for_a_missing_entity_fails(
    hass: HomeAssistant, context: Context
) -> None:
    """There is no current state to borrow, and guessing one would be worse."""
    results = await async_apply_state(
        hass,
        desired(entities=[{"entity_id": "climate.gone", "attributes": {"temperature": 19}}]),
        context=context,
    )

    assert [result.status for result in results] == [ActionStatus.FAILED]
    assert "does not exist" in str(results[0].detail)


async def test_a_desired_row_with_neither_state_nor_attributes_is_refused() -> None:
    """A row that says nothing is a row that cannot be rendered on the timeline."""
    with pytest.raises(vol.Invalid):
        desired(entities=[{"entity_id": "light.hall"}])


# --- D3: capture and restore ----------------------------------------------


def test_capture_records_only_the_attributes_the_rule_names(
    hass: HomeAssistant,
) -> None:
    """A snapshot of every attribute would try to put back things no rule touched
    — `friendly_name`, a computed `hvac_action` — and fail on the ones that are
    read-only."""
    hass.states.async_set(
        "climate.lounge",
        "heat",
        {"temperature": 18, "fan_mode": "low", "friendly_name": "Lounge"},
    )

    snapshots = capture_state(
        hass,
        desired(entities=[{"entity_id": "climate.lounge", "attributes": {"temperature": 22}}]),
    )

    assert snapshots[0].state == "heat"
    assert snapshots[0].attributes == {"temperature": 18}


def test_capture_takes_the_snapshot_even_when_an_override_is_set(
    hass: HomeAssistant,
) -> None:
    """The override says *how* to apply the state, not which entities it concerns,
    so a rule with an override and `on_exit: restore` is still undoable."""
    hass.states.async_set("climate.lounge", "heat", {"temperature": 18})

    snapshots = capture_state(
        hass,
        desired(
            entities=[{"entity_id": "climate.lounge", "attributes": {"temperature": 22}}],
            override=[service("climate.set_temperature", data={"temperature": 22})],
        ),
    )

    assert [snapshot.entity_id for snapshot in snapshots] == ["climate.lounge"]


def test_capturing_a_missing_entity_records_that_it_was_missing(
    hass: HomeAssistant,
) -> None:
    """`existed` is what `async_restore` reads to decide between a restore and a
    drop, so the absence has to be recorded rather than skipped."""
    snapshots = capture_state(
        hass, desired(entities=[{"entity_id": "light.gone", "state": "on"}])
    )

    assert snapshots[0].state is None
    assert not snapshots[0].existed


async def test_restore_puts_back_what_was_captured(
    hass: HomeAssistant, context: Context
) -> None:
    """The other half of D3's `restore`."""
    calls = async_mock_service(hass, "light", "turn_off")
    hass.states.async_set("light.hall", "off")
    snapshots = capture_state(
        hass, desired(entities=[{"entity_id": "light.hall", "state": "on"}])
    )
    hass.states.async_set("light.hall", "on")

    results = await async_restore(hass, snapshots, context=context)

    assert [result.status for result in results] == [ActionStatus.SUCCEEDED]
    assert calls[0].data["entity_id"] == "light.hall"


async def test_restoring_an_entity_that_never_existed_is_dropped(
    hass: HomeAssistant, context: Context
) -> None:
    """DROPPED, not FAILED: there is no prior state to return to, and nobody can
    fix it, so a red line in the log would be noise."""
    results = await async_restore(
        hass, [StateSnapshot(entity_id="light.gone", state=None)], context=context
    )

    assert [result.status for result in results] == [ActionStatus.DROPPED]


def test_a_snapshot_round_trips_through_the_runtime_store() -> None:
    """It is persisted alongside the held interval, so it has to be JSON."""
    snapshot = StateSnapshot(
        entity_id="climate.lounge", state="heat", attributes={"temperature": 18}
    )

    assert StateSnapshot.from_dict(snapshot.as_dict()) == snapshot


# --- ExecutionReport: what the completion axes read ------------------------


def test_an_empty_report_has_not_succeeded() -> None:
    """A rule with nothing to do has not succeeded at anything, which is what
    makes `count_on: actions_succeeded` mean "actually did the thing"."""
    assert not ExecutionReport().succeeded
    assert not ExecutionReport().attempted


async def test_a_report_with_one_drop_has_not_succeeded(
    hass: HomeAssistant, context: Context
) -> None:
    """All of them, not any of them. A dropped announcement means the occurrence
    did not do what the user asked."""
    async_mock_service(hass, "script", "turn_on")
    async_mock_service(hass, "light", "turn_on")
    _script_state(hass, "on", mode="single", current=1)

    report = ExecutionReport(
        actions=await async_run_actions(
            hass,
            actions(service("light.turn_on"), script("script.announce")),
            context=context,
        )
    )

    assert report.attempted
    assert not report.succeeded


def test_a_report_serialises_every_result() -> None:
    """§12's audit trail is what this is for, so nothing may be summarised away."""
    report = ExecutionReport(
        actions=(),
        state=(),
    )

    assert report.as_dict() == {"actions": [], "state": []}
