"""D27-D32 — turning an action list or a desired state into service calls.

This is the only module in the integration that writes to the outside world.
`engine/` produces `Transition` values and fires nothing (see
`engine/transition.py`); `tick.py` decides *when*; this decides *what*, and does
it. Keeping the three apart is what makes the dry run possible: a timeline is the
engine run with this module never called.

**Three properties are load-bearing, and all three come from D32.**

*Nothing here raises.* Every action is executed inside its own `try`, and a
failure becomes an `ActionResult` with `status=FAILED` rather than an exception.
D32's sentence is "a failed or dropped action never blocks state application" —
a failed PA announcement must not stop the lights, and coupling them makes the
least reliable action the reliability ceiling for the whole rule. `BaseException`
is deliberately *not* caught, so `CancelledError` still unwinds: a shutting-down
Home Assistant is not an action failure.

*Every action reports separately.* The return value is one result per action, in
the order the list was written, which is what step 6's D49 event payload
("per-action results") is built from. An aggregate boolean would lose exactly the
information the execution log exists to add.

*A dropped script is not a fired script.* D31: a `mode: single` script that is
already running writes "Already running" to the Python log and returns `None`
(A.10, verified below). A naive engine records *fired* when nothing ran, which is
the failure that is invisible in Home Assistant today. So a script call is
pre-flighted against the entity's published `mode` / `current` / `max`
attributes and recorded as `DROPPED` when the call would be swallowed.

**Verified against the installed 2026.9.4 package**, because three details here
are easy to get wrong from memory:

- `ATTR_CUR` and `ATTR_MAX` live in `homeassistant.helpers.script`, not in
  `homeassistant.components.script.const`. They are imported from the helper so
  that this integration does not depend on the `script` *component*.
- `max` is published **only** for `queued` and `parallel` modes
  (`ScriptEntity.extra_state_attributes` gates it on `script.supports_max`, which
  is `mode in (parallel, queued)`). A pre-flight that requires `max` would treat
  every `single` script as unreadable, so its absence is normal, not an error.
- `helpers/script.py` drops a run silently in **two** cases, not one: `single`
  while running, and `queued`/`parallel` once `runs == max_runs` ("Maximum number
  of runs exceeded"). A.10 records the first; the second is the same defect and is
  pre-flighted the same way.
- `ServiceRegistry.async_call` does `service_data.update(target)` on the dict it
  was handed. Passing a stored `data` dict straight in would write the target into
  the *schedule's own storage payload*. Every call here passes a copy.

**Nothing here reads a clock (D64).** There is no `now` parameter either, because
nothing here needs an instant: a `wait` timeout is a duration handed to
`asyncio.timeout`, which is the event loop's monotonic clock and not a civil one.
If a future action shape needs to know the time, it takes `now` as an argument.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
import logging
from typing import Any

from homeassistant.const import ATTR_ENTITY_ID, ATTR_MODE, STATE_ON
from homeassistant.core import Context, HomeAssistant, State, callback
from homeassistant.helpers.script import (
    ATTR_CUR,
    ATTR_MAX,
    SCRIPT_MODE_PARALLEL,
    SCRIPT_MODE_QUEUED,
    SCRIPT_MODE_RESTART,
    SCRIPT_MODE_SINGLE,
)
from homeassistant.helpers.state import async_reproduce_state

from .const import (
    ACTION_SCRIPT,
    ACTION_SERVICE,
    CONF_ATTRIBUTES,
    CONF_DATA,
    CONF_ENTITIES,
    CONF_ENTITY_ID,
    CONF_FIELDS,
    CONF_KIND,
    CONF_OVERRIDE,
    CONF_SCRIPT,
    CONF_SERVICE,
    CONF_STATE,
    CONF_TARGET,
    CONF_TIMEOUT,
    CONF_WAIT,
    SCRIPT_DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# `script.turn_on`'s payload key for script fields. A.10: the handler reads
# `service.data.get(ATTR_VARIABLES)` and passes it through as `variables=`.
_ATTR_VARIABLES = "variables"

# `script.turn_on` is the non-blocking path (D30's default) and a direct
# `script.<object_id>` call is the waiting one. Both are in the `script` domain;
# the difference is entirely which service name is used, which is why the two
# paths below look so similar.
_SERVICE_TURN_ON = "turn_on"


class ActionStatus(StrEnum):
    """What became of one action.

    Three values, because D32 asks for three distinguishable outcomes and the
    distinction between the last two is the whole point of D31: *dropped* means
    Home Assistant accepted the call and did nothing, which is neither a success
    nor an error and is the case no existing scheduler reports.
    """

    SUCCEEDED = "succeeded"
    DROPPED = "dropped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ActionResult:
    """One action's outcome, in the shape step 6 puts on the wire.

    `index` is the action's position in the list it came from, so a report can be
    read against the stored rule without matching on target strings. `target` is
    the human-facing name of what was called — `light.turn_on` or
    `script.pa_announce` — chosen over a structured field because the logbook line
    and the event payload both want one string.
    """

    index: int
    kind: str
    target: str
    status: ActionStatus
    detail: str | None = None

    @property
    def ok(self) -> bool:
        """Whether this action did what it said. Dropped is not ok."""
        return self.status is ActionStatus.SUCCEEDED

    def as_dict(self) -> dict[str, Any]:
        """JSON scalars only — this goes into the runtime store and into events."""
        return {
            "index": self.index,
            "kind": self.kind,
            "target": self.target,
            "status": str(self.status),
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    """Everything one occurrence did, with state kept apart from actions.

    The two lists are separate rather than concatenated because D32's guarantee is
    directional: actions must not block state, so "did the state get applied"
    has to be answerable without scanning past unrelated failures. It is also what
    D46's third axis needs — see `succeeded`.
    """

    actions: tuple[ActionResult, ...] = ()
    state: tuple[ActionResult, ...] = ()

    @property
    def results(self) -> tuple[ActionResult, ...]:
        """Everything, state first, for a caller that just wants to log it all."""
        return self.state + self.actions

    @property
    def attempted(self) -> bool:
        """Whether anything was tried at all.

        An `At` rule with no actions is legal — it is how a schedule that exists
        only to be seen on the timeline is written — and D46's `actions_succeeded`
        must not count it as a success, because nothing succeeded.
        """
        return bool(self.state or self.actions)

    @property
    def succeeded(self) -> bool:
        """Whether *everything* attempted worked — D46's `actions_succeeded`.

        All, not any. The axis exists so that "delete after it triggers" can mean
        "after it actually did its job", and a rule that half-fired has not done
        its job. A rule with nothing to do returns `False` rather than vacuously
        `True`, so a schedule counting successful actions never finishes on an
        occurrence that performed none.
        """
        return self.attempted and all(result.ok for result in self.results)

    def as_dict(self) -> dict[str, Any]:
        """JSON scalars only."""
        return {
            "actions": [result.as_dict() for result in self.actions],
            "state": [result.as_dict() for result in self.state],
        }


@dataclass(frozen=True, slots=True)
class StateSnapshot:
    """What the world looked like before a `During` rule touched it — D3's `restore`.

    Taken at ENTER and persisted with the held interval, because the rule that
    produced it may be edited away before the interval ends: `ExitCause.GONE`
    exists precisely for that, and a restore that has to re-read the rule to know
    what to put back cannot honour it. This mirrors `HeldInterval.end`, which
    carries the interval's own end for the same reason.

    Only the attributes the desired state *names* are captured. Snapshotting the
    whole attribute dict would send read-only values like `current_temperature`
    back through `reproduce_state`, which at best is noise and at worst is a
    validation error on restore; and "put back what I changed" is the promise D3
    makes, not "put back everything".
    """

    entity_id: str
    state: str | None
    attributes: Mapping[str, Any] = field(default_factory=dict)

    @property
    def existed(self) -> bool:
        """Whether the entity was there to snapshot.

        A `None` state means the entity did not exist at ENTER. Restoring it is
        not possible and is not an error either — reporting it as `DROPPED` says
        what happened without claiming a failure the user can act on.
        """
        return self.state is not None

    def as_dict(self) -> dict[str, Any]:
        """JSON scalars only — this is persisted in the runtime store (D35)."""
        return {
            "entity_id": self.entity_id,
            "state": self.state,
            "attributes": dict(self.attributes),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> StateSnapshot:
        """Rebuild from the runtime store, tolerating a record written by an
        earlier version that lacked a key."""
        return cls(
            entity_id=str(data[CONF_ENTITY_ID]),
            state=data.get(CONF_STATE),
            attributes=dict(data.get(CONF_ATTRIBUTES) or {}),
        )


# --- actions ---------------------------------------------------------------


def _describe(action: Mapping[str, Any]) -> str:
    """The one string that names what an action calls."""
    if action[CONF_KIND] == ACTION_SCRIPT:
        return str(action[CONF_SCRIPT])
    return str(action[CONF_SERVICE])


async def async_run_actions(
    hass: HomeAssistant,
    actions: Sequence[Mapping[str, Any]],
    *,
    context: Context,
) -> tuple[ActionResult, ...]:
    """Run an action list in order, reporting each one separately (D32).

    Sequential, not gathered. D27 calls the list "heterogeneous, multiple per
    rule" and says nothing about concurrency, but the order is the user's and a
    `wait: true` script (D30) only means anything if what follows it waits —
    "prepare, then act" is the reason waiting is offered at all. Running the list
    concurrently would make that setting silently useless.

    `context` is threaded into every call so D50's causation chain reads
    correctly, including *through* a script: both call paths propagate
    `context=service.context` into the script run, and the script's own service
    calls inherit it (A.10).
    """
    results: list[ActionResult] = []
    for index, action in enumerate(actions):
        results.append(await _async_run_one(hass, index, action, context=context))
    return tuple(results)


async def _async_run_one(
    hass: HomeAssistant,
    index: int,
    action: Mapping[str, Any],
    *,
    context: Context,
) -> ActionResult:
    """Run one action and turn anything it does into a result, never an exception."""
    kind = str(action[CONF_KIND])
    target = _describe(action)
    try:
        if kind == ACTION_SCRIPT:
            return await _async_run_script(hass, index, action, context=context)
        return await _async_call_service(hass, index, action, context=context)
    except Exception as err:  # noqa: BLE001 - D32: one action's failure is its own
        # `Exception`, not `BaseException`: `CancelledError` must keep unwinding,
        # because Home Assistant shutting down is not this action failing and
        # swallowing it would leave the tick unkillable.
        _LOGGER.error("almanac action %s (%s) failed: %s", index, target, err)
        return ActionResult(
            index=index, kind=kind, target=target, status=ActionStatus.FAILED, detail=str(err)
        )


async def _async_call_service(
    hass: HomeAssistant,
    index: int,
    action: Mapping[str, Any],
    *,
    context: Context,
) -> ActionResult:
    """Make one service call, blocking so that a failure is observable.

    `blocking=True` matches what core's own script engine does for a service step
    (`helpers/script.py`, the `_async_call_service_step` path), and it is what
    makes D32's per-action result mean anything: a fire-and-forget call cannot
    fail in a way this function could report.
    """
    domain, _, service = str(action[CONF_SERVICE]).partition(".")
    # A copy, because `ServiceRegistry.async_call` does `service_data.update(target)`
    # on the mapping it is handed — passing the stored dict would write the target
    # into the schedule's own storage payload.
    data = dict(action[CONF_DATA])
    target = action[CONF_TARGET]
    await hass.services.async_call(
        domain,
        service,
        data,
        blocking=True,
        context=context,
        target=dict(target) if target else None,
    )
    return ActionResult(
        index=index,
        kind=ACTION_SERVICE,
        target=f"{domain}.{service}",
        status=ActionStatus.SUCCEEDED,
    )


@callback
def script_drop_reason(hass: HomeAssistant, entity_id: str) -> str | None:
    """Why calling this script now would do nothing — D31's pre-flight.

    Returns `None` when the call should go ahead. The check is made against the
    script entity's published attributes rather than by asking the script
    integration, which has no public "would this run" API.

    The four modes, from `helpers/script.py`'s own guard:

    | mode | already running |
    | --- | --- |
    | `single` | logs "Already running", returns `None` — **dropped** |
    | `restart` | stops the running copy and starts again — proceed |
    | `queued` | queues until `max` runs exist, then drops — check `current` |
    | `parallel` | runs alongside until `max`, then drops — check `current` |

    **Accepted race, not a provisional choice.** The attributes are read here and
    the call is made a moment later, so a script that starts in between is called
    anyway and dropped silently. Closing it would need core to tell us whether a
    run started, which it does not: `async_turn_on` returns `None` either way.
    D31 asks for the truth to be logged in the common case, and the common case is
    a script that was already running when the tick began.
    """
    if (state := hass.states.get(entity_id)) is None:
        # Not a drop — a missing script is a broken reference, and reporting it as
        # "dropped" would file a configuration error under "worked as intended".
        return None
    if state.state != STATE_ON:
        return None

    mode = state.attributes.get(ATTR_MODE, SCRIPT_MODE_SINGLE)
    if mode == SCRIPT_MODE_SINGLE:
        return "script is already running and its mode is single"
    if mode == SCRIPT_MODE_RESTART:
        return None
    if mode in (SCRIPT_MODE_QUEUED, SCRIPT_MODE_PARALLEL):
        # `max` is published only for these two modes, so its absence above is
        # expected and its absence *here* means the entity is not reporting what
        # it should. Proceeding is the safer reading: a missed drop is a log line
        # that says "succeeded" when nothing ran, but a wrongly reported drop
        # stops an action that would have worked.
        limit = state.attributes.get(ATTR_MAX)
        current = state.attributes.get(ATTR_CUR, 0)
        if isinstance(limit, int) and isinstance(current, int) and current >= limit:
            return f"script is at its run limit ({current} of {limit}, mode {mode})"
    return None


async def _async_run_script(
    hass: HomeAssistant,
    index: int,
    action: Mapping[str, Any],
    *,
    context: Context,
) -> ActionResult:
    """Call a script, pre-flighting the drop (D31) and honouring D30's `wait`."""
    entity_id = str(action[CONF_SCRIPT])
    fields = dict(action[CONF_FIELDS])

    if hass.states.get(entity_id) is None:
        # Checked here rather than left to the service call, because an entity
        # service call with an unmatched `entity_id` is not an error in Home
        # Assistant — it matches nothing and returns. Without this, a script the
        # user renamed would be logged as having run successfully, which is the
        # same class of lie D31 exists to stop.
        detail = "no such script entity"
        _LOGGER.error("almanac action %s (%s) failed: %s", index, entity_id, detail)
        return ActionResult(
            index=index,
            kind=ACTION_SCRIPT,
            target=entity_id,
            status=ActionStatus.FAILED,
            detail=detail,
        )

    if (reason := script_drop_reason(hass, entity_id)) is not None:
        # The log line D31 asks for. It is an `info`, not a warning: two schedules
        # sharing one PA announcement script is a real configuration, not a fault,
        # and the record that matters is the execution log, not the Python one.
        _LOGGER.info("almanac dropped %s: %s", entity_id, reason)
        return ActionResult(
            index=index,
            kind=ACTION_SCRIPT,
            target=entity_id,
            status=ActionStatus.DROPPED,
            detail=reason,
        )

    if not action[CONF_WAIT]:
        # D30's default. `script.turn_on` starts the script and returns (A.10:
        # the handler passes `wait=False`), so `blocking=True` here waits only for
        # the service call itself — long enough to see a `ServiceNotFound`, not
        # long enough for a `delay` inside the script to matter.
        await hass.services.async_call(
            SCRIPT_DOMAIN,
            _SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: entity_id, _ATTR_VARIABLES: fields},
            blocking=True,
            context=context,
        )
        return ActionResult(
            index=index,
            kind=ACTION_SCRIPT,
            target=entity_id,
            status=ActionStatus.SUCCEEDED,
        )

    # D30's opt-in wait. Calling `script.<object_id>` as a service waits for the
    # script to finish and passes `variables=service.data`, so the fields go in as
    # the payload itself rather than under a `variables` key (A.10).
    #
    # The schema guarantees a timeout is present whenever `wait` is true, so this
    # is never an unbounded await. `asyncio.timeout` uses the loop's monotonic
    # clock, which is not a civil clock and so not D64's concern.
    object_id = entity_id.partition(".")[2]
    timeout = float(action[CONF_TIMEOUT])
    try:
        async with asyncio.timeout(timeout):
            await hass.services.async_call(
                SCRIPT_DOMAIN, object_id, fields, blocking=True, context=context
            )
    except TimeoutError:
        # The script is still running — nothing was cancelled, because the service
        # call was awaited, not owned. Reported as FAILED rather than DROPPED: the
        # call was accepted and did something, it just did not finish in the time
        # the user allowed, and the next action must not be told it succeeded.
        detail = f"script did not finish within {timeout:g}s and is still running"
        _LOGGER.warning("almanac %s: %s", entity_id, detail)
        return ActionResult(
            index=index,
            kind=ACTION_SCRIPT,
            target=entity_id,
            status=ActionStatus.FAILED,
            detail=detail,
        )
    return ActionResult(
        index=index, kind=ACTION_SCRIPT, target=entity_id, status=ActionStatus.SUCCEEDED
    )


# --- desired state (D28) ---------------------------------------------------


def _desired_states(hass: HomeAssistant, entities: Iterable[Mapping[str, Any]]) -> tuple[
    list[State], list[ActionResult]
]:
    """Build the `State` objects `async_reproduce_state` wants, and the failures.

    A desired-state row may omit `state` and name only attributes, which is the
    case D28's override exists for — "set the temperature, leave the mode alone".
    A `State` needs a state string regardless, so the entity's current one is
    used. When the entity does not exist there is nothing to borrow, and the row
    becomes a FAILED result instead of a guess.
    """
    states: list[State] = []
    failures: list[ActionResult] = []
    for index, entry in enumerate(entities):
        entity_id = str(entry[CONF_ENTITY_ID])
        wanted = entry.get(CONF_STATE)
        if wanted is None:
            if (current := hass.states.get(entity_id)) is None:
                failures.append(
                    ActionResult(
                        index=index,
                        kind=CONF_STATE,
                        target=entity_id,
                        status=ActionStatus.FAILED,
                        detail=(
                            "no state given and the entity does not exist, so there "
                            "is no current state to keep"
                        ),
                    )
                )
                continue
            wanted = current.state
        states.append(State(entity_id, str(wanted), dict(entry.get(CONF_ATTRIBUTES) or {})))
    return states, failures


async def async_apply_state(
    hass: HomeAssistant,
    desired: Mapping[str, Any],
    *,
    context: Context,
) -> tuple[ActionResult, ...]:
    """Apply a desired state — D28.

    `override`, when the rule set one, replaces the `reproduce_state` call
    entirely: those actions are run instead. That is the escape hatch D28 requires
    for climate, where core's helper issues up to seven sequential blocking calls
    and does not skip attributes that already match current state (A.5). The
    `entities` list is still stored and still meaningful in that case — the
    timeline reads it to say what the interval does, and D3's `restore` needs it
    to know what to snapshot.

    **One result per entity, and they are all SUCCEEDED or all FAILED.**
    `async_reproduce_state` reports a domain it cannot handle as a
    `_LOGGER.warning` and returns normally — verified in the installed package:
    both "Trying to reproduce state for unknown integration" and "Integration %s
    does not support reproduce state" are warnings followed by a plain return, and
    the per-domain calls run under `asyncio.gather`. So a caller cannot attribute
    a failure to an entity, and pretending otherwise would put a fabricated
    per-entity verdict into the execution log. This opacity is itself part of why
    D28 carries an override.
    """
    if override := desired[CONF_OVERRIDE]:
        return await async_run_actions(hass, override, context=context)

    entities = desired[CONF_ENTITIES]
    states, failures = _desired_states(hass, entities)
    if not states:
        return tuple(failures)

    try:
        await async_reproduce_state(hass, states, context=context)
    except Exception as err:  # noqa: BLE001 - D32 again: this must not raise upward
        _LOGGER.error("almanac failed to apply desired state: %s", err)
        return tuple(failures) + tuple(
            ActionResult(
                index=index,
                kind=CONF_STATE,
                target=state.entity_id,
                status=ActionStatus.FAILED,
                detail=str(err),
            )
            for index, state in enumerate(states)
        )

    return tuple(failures) + tuple(
        ActionResult(
            index=index,
            kind=CONF_STATE,
            target=state.entity_id,
            status=ActionStatus.SUCCEEDED,
        )
        for index, state in enumerate(states)
    )


@callback
def capture_state(
    hass: HomeAssistant, desired: Mapping[str, Any]
) -> tuple[StateSnapshot, ...]:
    """Snapshot what a desired state is about to overwrite — D3's `restore`.

    Taken from `entities` even when an `override` is set, because the override
    says how to apply the state, not which entities it concerns. A rule whose exit
    behaviour is `restore` and whose application is an override still has to be
    undoable.
    """
    snapshots: list[StateSnapshot] = []
    for entry in desired[CONF_ENTITIES]:
        entity_id = str(entry[CONF_ENTITY_ID])
        named = tuple(entry.get(CONF_ATTRIBUTES) or {})
        if (current := hass.states.get(entity_id)) is None:
            snapshots.append(StateSnapshot(entity_id=entity_id, state=None))
            continue
        snapshots.append(
            StateSnapshot(
                entity_id=entity_id,
                state=current.state,
                # Only the attributes this rule names — see `StateSnapshot`.
                attributes={
                    key: current.attributes[key] for key in named if key in current.attributes
                },
            )
        )
    return tuple(snapshots)


async def async_restore(
    hass: HomeAssistant,
    snapshots: Sequence[StateSnapshot],
    *,
    context: Context,
) -> tuple[ActionResult, ...]:
    """Put back what `capture_state` recorded — the other half of D3's `restore`.

    Entities that did not exist when the snapshot was taken are reported as
    DROPPED, not FAILED: there is no prior state to return them to, and calling
    that a failure would put a red line in the log for a situation nobody can fix.
    """
    results: list[ActionResult] = []
    states: list[State] = []
    for index, snapshot in enumerate(snapshots):
        if not snapshot.existed:
            results.append(
                ActionResult(
                    index=index,
                    kind="restore",
                    target=snapshot.entity_id,
                    status=ActionStatus.DROPPED,
                    detail="the entity did not exist when the interval began",
                )
            )
            continue
        states.append(
            State(snapshot.entity_id, str(snapshot.state), dict(snapshot.attributes))
        )

    if not states:
        return tuple(results)

    try:
        await async_reproduce_state(hass, states, context=context)
    except Exception as err:  # noqa: BLE001 - D32
        _LOGGER.error("almanac failed to restore state: %s", err)
        status, detail = ActionStatus.FAILED, str(err)
    else:
        status, detail = ActionStatus.SUCCEEDED, None

    results.extend(
        ActionResult(
            index=index,
            kind="restore",
            target=state.entity_id,
            status=status,
            detail=detail,
        )
        for index, state in enumerate(states)
    )
    return tuple(results)
