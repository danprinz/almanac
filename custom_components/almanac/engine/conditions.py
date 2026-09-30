"""D23's structured conditions, evaluated at an instant the caller chooses.

Three leaf shapes and one level of grouping, which is §7.1 exactly:

| shape | reads |
| --- | --- |
| `comparison` | an entity's state or attribute, against a constant or another entity |
| `day_set` | whether a day set covers the evaluation instant (D20) |
| `group` | two or more leaves, `and` or `or` (§7.1's one level) |

A rule's `conditions` list is itself an AND, so a group is only ever needed for an
OR. That is why there is no operator field at the top level, and it is also why
*(A and B) or (C and D)* cannot be written here. D23's last row is deliberate about
that: the escape hatch is the user's own template `binary_sensor` helper, compared
with `eq` / `on`. Handing arbitrary nesting to the rule editor would buy one
expressible sentence and lose the property this project exists for — that a rule's
conditions can be summarised in a line the timeline has room to print.

**Three things here are worth reading before changing any of it.**

*Unknown is not false.* An entity that does not exist, is `unavailable`, or lacks
the attribute named makes the predicate **undetermined**, and an undetermined
predicate never causes a change of state: a held interval does not exit and a
pending one does not enter. The alternative — treating unknown as false — turns a
motion sensor restarting for four seconds into lights going off, and the `latch`
escape (D4) would not help because the user did not ask for a latch, they asked
for a condition. This is the same instinct as D42's "skip, log, keep the
subscription live", and it is reported as `ConditionOutcome.problem` rather than
folded into `passed` so the transition layer can tell the two apart. **Provisional
— see the note on `ConditionOutcome`.**

*The comparison ladder is typed, not stringly.* HA states are strings, so
`"9" > "20"` is true and a temperature threshold written as a string silently
inverts. So every ordering comparison tries timestamps, then numbers, and
**refuses** anything else rather than falling back on lexicographic order. A
string can be compared for equality and membership, which is what `on` / `off` /
`home` need, and nothing more.

*Nothing here reads a clock (D64).* `now` is a parameter, including for `for:` —
the "has held for N seconds" test — which is the one condition shape that genuinely
needs an instant. Core's own `condition.state` helper calls `dt_util.utcnow()` on
that path (verified in the installed 2026.9.4 package,
`homeassistant/helpers/condition.py`), which is why this is an in-tree evaluator
rather than a call into core.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util

from ..const import (
    CMP_ABOVE,
    CMP_AT_LEAST,
    CMP_AT_MOST,
    CMP_BELOW,
    CMP_EQUAL,
    CMP_IN,
    CMP_NOT_EQUAL,
    CMP_NOT_IN,
    CONDITION_COMPARISON,
    CONDITION_DAY_SET,
    CONDITION_GROUP,
    CONF_ATTRIBUTE,
    CONF_CONDITIONS,
    CONF_DAY_SET_ID,
    CONF_ENTITY_ID,
    CONF_FOR,
    CONF_KIND,
    CONF_LABEL,
    CONF_NEGATE,
    CONF_OFFSET,
    CONF_OPERATOR,
    CONF_VALUE,
    GROUP_AND,
    LIST_OPERATORS,
    OPERAND_CONSTANT,
)
from ..resolver import ResolverRegistry, Unresolved, UnresolvedReason
from ..resolver.contract import absolute
from .day_set import DaySetLookup, async_covers

_LOGGER = logging.getLogger(__name__)

_MISSING = object()


@dataclass(frozen=True, slots=True)
class ConditionOutcome:
    """Whether a rule's conditions hold, and what to say if they do not.

    Three states, not two, and the third is the point:

    - `passed` is true — the predicate holds.
    - `passed` is false and `problem` is `None` — the predicate definitely does
      not hold, and `blocking` names the conditions that said so (D24).
    - `problem` is not `None` — the predicate is **undetermined**. Nothing is
      concluded from it: see the module docstring.

    *This third state is provisional and wants an owner's ruling.* §7 settles what
    conditions are and when they are evaluated (D25) but not what an unreadable
    one means, and the three candidate readings differ in what they break. The one
    implemented here — unknown changes nothing — was chosen because the failure it
    produces is "the light stayed as it was", which a user can see and correct,
    where fail-closed produces "the light went off at 3am because the sensor
    rebooted" and fail-open produces "the condition I added did not stop it". The
    rejected alternative worth naming is **fail-closed**, i.e. treating unknown as
    false, which is what core's `condition.state` effectively does and what makes
    upstream's `track_conditions` (§7.3, card#878) so unpleasant to live with.

    `blocking` is populated even when the outcome is undetermined, because the log
    line D49 wants to write is the same either way.
    """

    passed: bool
    blocking: tuple[str, ...] = ()
    problem: Unresolved | None = None

    def __post_init__(self) -> None:
        """An undetermined outcome is never a pass. Asserted, not documented."""
        if self.problem is not None and self.passed:
            raise ValueError("an undetermined predicate cannot have passed")

    @property
    def determined(self) -> bool:
        """Whether anything at all may be concluded from this outcome."""
        return self.problem is None

    @property
    def summary(self) -> str:
        """One line, for D24's "Skipped: *cleaning crew present*"."""
        if self.passed:
            return "conditions met"
        if self.blocking:
            return ", ".join(self.blocking)
        return str(self.problem) if self.problem is not None else "conditions failed"


async def async_evaluate(
    registry: ResolverRegistry,
    day_sets: DaySetLookup | None,
    conditions: Sequence[dict[str, Any]],
    *,
    now: datetime,
) -> ConditionOutcome:
    """Evaluate a rule's whole condition list at `now`.

    The list is an AND. Every member is evaluated rather than short-circuited,
    because `blocking` is a list of reasons and a user debugging a rule that did
    not fire wants all of them — the cost is a few state-machine reads, and there
    is no side effect to avoid.

    A definite `False` anywhere makes the *outcome* definite even if another member
    is undetermined, which is why `problem` is only reported when no member
    settled the question. Without that, one broken sensor would make a rule with
    an obviously-false condition look unreadable rather than blocked.
    """
    if not conditions:
        # The overwhelmingly common case, and the one that must cost nothing: an
        # unconditional rule is not a rule with a trivially true predicate.
        return ConditionOutcome(True)

    blocking: list[str] = []
    problem: Unresolved | None = None
    definitely_false = False

    for condition in conditions:
        result = await _async_condition(registry, day_sets, condition, now=now)
        if isinstance(result, Unresolved):
            blocking.append(_label(condition))
            if problem is None:
                problem = result
            continue
        if not result:
            blocking.append(_label(condition))
            definitely_false = True

    if definitely_false:
        return ConditionOutcome(False, tuple(blocking))
    if problem is not None:
        return ConditionOutcome(False, tuple(blocking), problem)
    return ConditionOutcome(True)


async def _async_condition(
    registry: ResolverRegistry,
    day_sets: DaySetLookup | None,
    condition: dict[str, Any],
    *,
    now: datetime,
) -> bool | Unresolved:
    """One condition of any of the three shapes."""
    kind = condition[CONF_KIND]

    if kind == CONDITION_COMPARISON:
        return _comparison(registry.hass, condition, now=now)

    if kind == CONDITION_DAY_SET:
        # D20's payoff: the condition and the recurrence ask the same object the
        # same question, so "only on Shabbat" means the same thing in both places
        # without a second date predicate to keep in step.
        covered = await async_covers(
            registry, day_sets, condition[CONF_DAY_SET_ID], now
        )
        if isinstance(covered, Unresolved):
            return covered
        return not covered if condition[CONF_NEGATE] else covered

    if kind == CONDITION_GROUP:
        return await _async_group(registry, day_sets, condition, now=now)

    return Unresolved(  # pragma: no cover - the schema is a closed union
        UnresolvedReason.ERROR, f"{kind!r} is not a condition shape (D23)"
    )


async def _async_group(
    registry: ResolverRegistry,
    day_sets: DaySetLookup | None,
    group: dict[str, Any],
    *,
    now: datetime,
) -> bool | Unresolved:
    """§7.1's one level of and/or over leaves.

    An undetermined member is only fatal when it is *decisive*: an `or` with one
    member definitely true is true whatever the rest say, and an `and` with one
    member definitely false is false. Reporting unknown in those cases would be
    less informative and no safer, and it would make the answer depend on the
    order the user happened to list the members in.
    """
    conjunction = group[CONF_OPERATOR] == GROUP_AND
    problem: Unresolved | None = None
    decisive = False

    for member in group[CONF_CONDITIONS]:
        result = await _async_condition(registry, day_sets, member, now=now)
        if isinstance(result, Unresolved):
            if problem is None:
                problem = result
            continue
        if result is not conjunction:
            # `False` inside an `and`, or `True` inside an `or`. Either settles the
            # group on its own, but the loop runs on so a second broken member is
            # still logged rather than hidden behind an early return.
            decisive = True

    if decisive:
        return not conjunction
    if problem is not None:
        return problem
    return conjunction


def _comparison(
    hass: HomeAssistant, condition: dict[str, Any], *, now: datetime
) -> bool | Unresolved:
    """One entity compared against a constant or another entity."""
    left = _read(hass, condition[CONF_ENTITY_ID], condition[CONF_ATTRIBUTE])
    if isinstance(left, Unresolved):
        return left

    operand = condition[CONF_VALUE]
    if operand[CONF_KIND] == OPERAND_CONSTANT:
        right: Any = operand[CONF_VALUE]
        offset = 0.0
    else:
        right = _read(hass, operand[CONF_ENTITY_ID], operand[CONF_ATTRIBUTE])
        if isinstance(right, Unresolved):
            return right
        offset = float(operand[CONF_OFFSET])

    result = _compare(condition[CONF_OPERATOR], left, right, offset)
    if isinstance(result, Unresolved) or not result:
        return result

    if (held := condition[CONF_FOR]) is None:
        return True
    return _held_for(hass, condition, held, now=now)


def _read(
    hass: HomeAssistant, entity_id: str, attribute: str | None
) -> Any | Unresolved:
    """One side of a comparison, or the reason there isn't one.

    `attribute` being `None` means the state itself, which is a distinct thing
    from an attribute that happens to be spelled `state` — hence the explicit
    `None` rather than a sentinel string.
    """
    state = hass.states.get(entity_id)
    if state is None:
        return Unresolved(
            UnresolvedReason.UNAVAILABLE, f"{entity_id} does not exist"
        )
    if state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
        # Checked even when an attribute was asked for: an unavailable entity's
        # attributes are whatever it published before it went away, and treating
        # those as current is how a condition keeps passing for a device that has
        # been unplugged since Tuesday.
        return Unresolved(
            UnresolvedReason.UNAVAILABLE, f"{entity_id} is {state.state}"
        )
    if attribute is None:
        return state.state
    if (value := state.attributes.get(attribute, _MISSING)) is _MISSING:
        return Unresolved(
            UnresolvedReason.UNKNOWN_KEY,
            f"{entity_id} has no attribute {attribute!r}",
        )
    return value


def _held_for(
    hass: HomeAssistant, condition: dict[str, Any], seconds: int, *, now: datetime
) -> bool | Unresolved:
    """Whether the comparison's subject has been settled for long enough.

    Two details are copied from core's `_state_valid_since` (verified against the
    installed 2026.9.4 `homeassistant/helpers/condition.py`), because a user who
    knows HA will expect them:

    - the anchor is `last_changed` for a state test and `last_updated` for an
      attribute test, since an attribute can move while the state does not
    - the entity, not the comparison, is what is timed. `for:` means "this entity
      has not changed for N seconds *and* the comparison holds now", not "the
      comparison has held for N seconds" — establishing the latter would need a
      history query, which D49's own audit trail deliberately does not provide.

    The one deliberate difference: the test is `>=` where core uses a strict `>`.
    The engine is evaluated at instants it chooses (`next_transition_at`), so a
    tick landing exactly on the boundary is a case that will actually occur, and
    it should see a 60-second condition as met at 60 seconds.
    """
    state = hass.states.get(condition[CONF_ENTITY_ID])
    if state is None:  # pragma: no cover - `_read` already found it
        return Unresolved(UnresolvedReason.UNAVAILABLE, "the entity went away")
    anchor = _anchor(state, condition[CONF_ATTRIBUTE])
    return absolute(now) - absolute(anchor) >= timedelta(seconds=seconds)


def _anchor(state: State, attribute: str | None) -> datetime:
    """The instant a `for:` test measures from."""
    return state.last_changed if attribute is None else state.last_updated


def _compare(
    operator: str, left: Any, right: Any, offset: float
) -> bool | Unresolved:
    """Apply one operator, refusing the comparisons that would be nonsense."""
    if operator in LIST_OPERATORS:
        # The schema pins the right-hand side of a list operator to a constant
        # list, because an entity holds one value and "state in <one value>" is
        # equality spelled confusingly.
        found = any(_equal(left, item, offset) is True for item in right)
        return found if operator == CMP_IN else not found

    if operator in (CMP_EQUAL, CMP_NOT_EQUAL):
        equal = _equal(left, right, offset)
        if isinstance(equal, Unresolved):
            return equal
        return equal if operator == CMP_EQUAL else not equal

    pair = _ordered(left, right, offset)
    if isinstance(pair, Unresolved):
        return pair
    lower, upper = pair
    if operator == CMP_ABOVE:
        return lower > upper
    if operator == CMP_AT_LEAST:
        return lower >= upper
    if operator == CMP_BELOW:
        return lower < upper
    if operator == CMP_AT_MOST:
        return lower <= upper
    return Unresolved(  # pragma: no cover - the schema is a closed set
        UnresolvedReason.ERROR, f"{operator!r} is not a comparison operator"
    )


def _equal(left: Any, right: Any, offset: float) -> bool | Unresolved:
    """Equality, trying the typed readings before the textual one."""
    if (pair := _as_instants(left, right, offset)) is not None:
        return pair[0] == pair[1]
    if (numbers := _as_numbers(left, right, offset)) is not None:
        return numbers[0] == numbers[1]
    if offset:
        return _offset_refused()
    # `home`, `on`, `heat`: the values HA entities actually hold. A bool constant
    # is rendered as `true` / `false` rather than mapped onto `on` / `off`, because
    # inventing that mapping here would make `eq: true` mean something different
    # from what the entity's own state page shows.
    return _text(left) == _text(right)


def _ordered(
    left: Any, right: Any, offset: float
) -> tuple[Any, Any] | Unresolved:
    """A comparable pair for an ordering operator, or a refusal.

    The refusal is the whole point. HA hands out states as strings, so falling
    back on `str.__lt__` would make `above: 20` true for a temperature of `9`,
    and it would do so silently and only sometimes — exactly the class of bug
    that costs an afternoon. A user who genuinely wants lexicographic order can
    write a template helper and compare its numeric output.
    """
    if (pair := _as_instants(left, right, offset)) is not None:
        return pair
    if (numbers := _as_numbers(left, right, offset)) is not None:
        return numbers
    return Unresolved(
        UnresolvedReason.ERROR,
        f"cannot order {left!r} and {right!r}: an ordering comparison needs two "
        "numbers or two timestamps",
    )


def _as_instants(
    left: Any, right: Any, offset: float
) -> tuple[datetime, datetime] | None:
    """Both sides as absolute instants, or `None` if they are not both timestamps.

    This is what makes "before candle lighting" writable as a condition rather
    than only as an anchor: `sensor.candle_lighting` is a `device_class:
    timestamp` entity (A.4), so both sides parse and the offset is seconds. Both
    are put through `absolute()` because two aware datetimes sharing a `tzinfo`
    object compare by wall clock (A.13), which would make a DST-fall comparison
    wrong for one hour a year and right the rest of the time.
    """
    first = _instant(left)
    second = _instant(right)
    if first is None or second is None:
        return None
    return absolute(first), absolute(second) + timedelta(seconds=offset)


def _instant(value: Any) -> datetime | None:
    """One value as a datetime, if it is one."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return dt_util.parse_datetime(value)
    return None


def _as_numbers(
    left: Any, right: Any, offset: float
) -> tuple[float, float] | None:
    """Both sides as floats, or `None` if either is not numeric.

    `bool` is excluded deliberately. `True` is `1` in Python, so a `binary_sensor`
    compared with `above: 0` would pass for a state of `on` only if `on` were
    coerced to a bool somewhere, and the ladder should not manufacture that
    reading half-way down.
    """
    first = _number(left)
    second = _number(right)
    if first is None or second is None:
        return None
    return first, second + offset


def _number(value: Any) -> float | None:
    """One value as a float, if it is one."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _offset_refused() -> Unresolved:
    """An offset on a pair that is neither numeric nor temporal.

    Reported rather than ignored. An offset the user wrote and the engine
    discarded is a condition that means something other than what it says, and
    the schema cannot catch it because the operand's type is only known once the
    entity has a state.
    """
    return Unresolved(
        UnresolvedReason.ERROR,
        "an offset needs two numbers or two timestamps to add itself to",
    )


def _text(value: Any) -> str:
    """One value as the string a comparison should see."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _label(condition: dict[str, Any]) -> str:
    """D24's phrase for one condition, falling back on a description.

    The label is optional and the fallback has to be intelligible, because "the
    rule did not fire" with no reason is the upstream experience this replaces.
    """
    if label := condition.get(CONF_LABEL):
        return str(label)
    return _describe(condition)


def _describe(condition: dict[str, Any]) -> str:
    """A short, generated phrase for a condition with no label."""
    kind = condition[CONF_KIND]
    if kind == CONDITION_DAY_SET:
        negated = "not " if condition[CONF_NEGATE] else ""
        return f"{negated}in day set {condition[CONF_DAY_SET_ID]}"
    if kind == CONDITION_GROUP:
        joiner = condition[CONF_OPERATOR]
        return f"{joiner} of {len(condition[CONF_CONDITIONS])} conditions"
    subject = condition[CONF_ENTITY_ID]
    if attribute := condition[CONF_ATTRIBUTE]:
        subject = f"{subject}.{attribute}"
    operand = condition[CONF_VALUE]
    target = (
        operand[CONF_VALUE]
        if operand[CONF_KIND] == OPERAND_CONSTANT
        else operand[CONF_ENTITY_ID]
    )
    return f"{subject} {condition[CONF_OPERATOR]} {target}"


__all__ = ["ConditionOutcome", "async_evaluate"]
