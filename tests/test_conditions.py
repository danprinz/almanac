"""Conditions — D23–D26, and the typed comparison ladder they rest on.

Two halves. The first evaluates condition lists directly, because that is where
the decisions with a wrong-looking alternative live: unknown is not false, an
ordering comparison of two strings is refused rather than answered
lexicographically, and `for:` measures the entity rather than the comparison. The
second drives the same predicates through `transition.py`, where D25's
always-live `During` reading and D26's `skip` / `wait_until` policy actually take
effect.

Every `now` is written down, which is D64 doing the work it was adopted for: the
`for:` boundary and the `wait_until` deadline are both off-by-one-instant
questions, and neither can be asked at all of a system that reads the wall clock
internally. The two `for:` tests are the exception that proves it — they derive
`now` from the state machine's own `last_changed`, because that is the one instant
the test cannot choose.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from homeassistant.core import HomeAssistant

from custom_components.almanac.const import (
    CMP_ABOVE,
    CMP_AT_LEAST,
    CMP_BELOW,
    CMP_EQUAL,
    CMP_IN,
    CMP_NOT_IN,
    CONDITION_COMPARISON,
    CONDITION_DAY_SET,
    CONDITION_GROUP,
    GROUP_AND,
    GROUP_OR,
    OPERAND_CONSTANT,
    OPERAND_ENTITY,
    POLICY_SKIP,
    POLICY_WAIT_UNTIL,
    RECUR_WEEKDAYS,
    SOURCE_WEEKDAYS,
)
from custom_components.almanac.engine import (
    ConditionOutcome,
    EngineState,
    ExitCause,
    PendingAt,
    TransitionKind,
    async_evaluate,
    async_plan_recovery,
    async_plan_tick,
)
from custom_components.almanac.resolver import (
    ResolverRegistry,
    Unresolved,
    UnresolvedReason,
    async_create_registry,
)
from custom_components.almanac.schema import CONDITION_SCHEMA, STORAGE_SCHEMA

NY = ZoneInfo("America/New_York")

HOME = "binary_sensor.home"
GUEST = "binary_sensor.guest"
THERMOSTAT = "climate.hall"
CANDLE = "sensor.candle_lighting"
ZMAN = "sensor.zman"


# --- helpers ---------------------------------------------------------------


def ny(*args: int) -> datetime:
    """A New York instant, written out."""
    return datetime(*args, tzinfo=NY)  # type: ignore[arg-type]


class StubDaySets:
    """A `DaySetLookup` over a literal dict, which is all the protocol asks for."""

    def __init__(self, items: dict[str, dict[str, Any]]) -> None:
        """Hold the items."""
        self.items = items

    def async_get_day_set(self, day_set_id: str) -> dict[str, Any] | None:
        """The item, or `None`."""
        return self.items.get(day_set_id)


def weekend_sets() -> StubDaySets:
    """One date-granular day set, named `weekend`, covering Saturdays."""
    return StubDaySets(
        {
            "weekend": {
                "id": "weekend",
                "name": "Weekend",
                "object_id": "weekend",
                "source": {"kind": SOURCE_WEEKDAYS, "weekdays": ["sat"]},
            }
        }
    )


def constant(value: Any) -> dict[str, Any]:
    """A constant operand."""
    return {"kind": OPERAND_CONSTANT, "value": value}


def entity_operand(entity_id: str, offset: float = 0) -> dict[str, Any]:
    """An entity operand, with D23's signed offset."""
    return {"kind": OPERAND_ENTITY, "entity_id": entity_id, "offset": offset}


def comparison(
    entity_id: str, operator: str, value: Any, **extra: Any
) -> dict[str, Any]:
    """A validated `comparison` condition, so the test sees the stored defaults."""
    operand = value if isinstance(value, dict) else constant(value)
    return dict(
        CONDITION_SCHEMA(
            {
                "kind": CONDITION_COMPARISON,
                "entity_id": entity_id,
                "operator": operator,
                "value": operand,
                **extra,
            }
        )
    )


def in_day_set(day_set_id: str, **extra: Any) -> dict[str, Any]:
    """A validated `day_set` condition (D20)."""
    return dict(
        CONDITION_SCHEMA(
            {"kind": CONDITION_DAY_SET, "day_set_id": day_set_id, **extra}
        )
    )


def group(operator: str, *members: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """A validated §7.1 group — one level, two members minimum."""
    return dict(
        CONDITION_SCHEMA(
            {
                "kind": CONDITION_GROUP,
                "operator": operator,
                "conditions": list(members),
                **extra,
            }
        )
    )


def schedule(**body: Any) -> dict[str, Any]:
    """A stored schedule, validated, matching `test_engine.py`'s helper."""
    return dict(
        STORAGE_SCHEMA(
            {
                "id": "sched",
                "name": "Test schedule",
                "object_id": "test_schedule",
                **body,
            }
        )
    )


def daily() -> dict[str, Any]:
    """Every day."""
    return {
        "kind": RECUR_WEEKDAYS,
        "weekdays": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
    }


def at_rule(at: str, rule_id: str = "r1", **extra: Any) -> dict[str, Any]:
    """An `At` rule on a clock anchor."""
    return {
        "kind": "at",
        "id": rule_id,
        "anchor": {"kind": "clock", "at": at},
        **extra,
    }


def during_rule(at: str, seconds: int, rule_id: str = "r1", **extra: Any) -> dict[str, Any]:
    """A `During` rule of a fixed length, on a clock anchor."""
    return {
        "kind": "during",
        "id": rule_id,
        "start_anchor": {"kind": "clock", "at": at},
        "end": {"kind": "duration", "duration": seconds},
        **extra,
    }


def wait_until(seconds: int) -> dict[str, Any]:
    """D26's second policy, with its mandatory deadline."""
    return {"kind": POLICY_WAIT_UNTIL, "deadline": seconds}


@pytest.fixture(autouse=True)
async def _located(hass: HomeAssistant) -> None:
    """New York, matching the other engine tests."""
    await hass.config.async_set_time_zone("America/New_York")
    await hass.config.async_update(latitude=40.7128, longitude=-74.0060, elevation=0)


@pytest.fixture
def resolvers(hass: HomeAssistant) -> ResolverRegistry:
    """The in-tree resolvers, wired as the config entry wires them."""
    return async_create_registry(hass)


# --- the AND list, and what "blocked" means ---------------------------------


async def test_no_conditions_is_not_a_trivially_true_condition(
    resolvers: ResolverRegistry,
) -> None:
    """The overwhelmingly common case, and it has to cost nothing."""
    outcome = await async_evaluate(resolvers, None, [], now=ny(2026, 10, 2, 17))

    assert outcome.passed is True
    assert outcome.blocking == ()
    assert outcome.summary == "conditions met"


async def test_an_and_list_names_every_condition_that_blocked(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """D24 — all the reasons, not the first one.

    Short-circuiting would be free and would make the log line useless: a user
    debugging a rule that did not fire wants every reason at once, and there is no
    side effect to avoid by stopping early.
    """
    hass.states.async_set(HOME, "off")
    hass.states.async_set(GUEST, "off")

    outcome = await async_evaluate(
        resolvers,
        None,
        [comparison(HOME, CMP_EQUAL, "on"), comparison(GUEST, CMP_EQUAL, "on")],
        now=ny(2026, 10, 2, 17),
    )

    assert outcome.passed is False
    assert outcome.determined is True
    assert outcome.blocking == (
        f"{HOME} eq on",
        f"{GUEST} eq on",
    )


async def test_a_label_replaces_the_generated_phrase(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """D24's optional label, which is what makes "Skipped: *cleaning crew*" possible."""
    hass.states.async_set(HOME, "off")

    outcome = await async_evaluate(
        resolvers,
        None,
        [comparison(HOME, CMP_EQUAL, "on", label="somebody home")],
        now=ny(2026, 10, 2, 17),
    )

    assert outcome.summary == "somebody home"


async def test_a_definite_false_beats_an_undetermined_sibling(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """One broken sensor must not make an obviously-blocked rule look unreadable.

    The outcome is *determined* — the rule definitely did not run because the
    house is empty — while still naming the unreadable condition in `blocking`,
    because both are things the user should see.
    """
    hass.states.async_set(HOME, "off")

    outcome = await async_evaluate(
        resolvers,
        None,
        [
            comparison("binary_sensor.gone", CMP_EQUAL, "on"),
            comparison(HOME, CMP_EQUAL, "on"),
        ],
        now=ny(2026, 10, 2, 17),
    )

    assert outcome.passed is False
    assert outcome.determined is True
    assert len(outcome.blocking) == 2


async def test_an_unreadable_condition_alone_is_undetermined(
    resolvers: ResolverRegistry,
) -> None:
    """The third state, which is the whole point of `ConditionOutcome`.

    Not a pass and not a fail. `transition.py` is what acts on the difference: a
    held interval stays held, a pending one is not entered.
    """
    outcome = await async_evaluate(
        resolvers,
        None,
        [comparison("binary_sensor.gone", CMP_EQUAL, "on")],
        now=ny(2026, 10, 2, 17),
    )

    assert outcome.passed is False
    assert outcome.determined is False
    assert outcome.problem is not None
    assert outcome.problem.reason is UnresolvedReason.UNAVAILABLE


def test_an_undetermined_outcome_cannot_have_passed() -> None:
    """Asserted in `__post_init__`, because the pair is meaningless."""
    with pytest.raises(ValueError, match="undetermined"):
        ConditionOutcome(
            True, problem=Unresolved(UnresolvedReason.UNAVAILABLE, "gone")
        )


def test_an_unlabelled_undetermined_outcome_still_has_a_summary() -> None:
    """`Unresolved` carries no `message`; the summary prints the value itself.

    Directly constructed, because `async_evaluate` always populates `blocking`
    alongside `problem` — which is exactly why this path went unexercised and had
    to be fixed rather than found in production.
    """
    outcome = ConditionOutcome(
        False, problem=Unresolved(UnresolvedReason.TIMEOUT, "hdate took too long")
    )

    assert "hdate took too long" in outcome.summary


# --- §7.1: one level of and/or ---------------------------------------------


@pytest.mark.parametrize(
    ("operator", "guest", "expected"),
    [
        (GROUP_AND, "on", True),
        (GROUP_AND, "off", False),
        (GROUP_OR, "off", True),
    ],
)
async def test_a_group_is_evaluated_as_a_whole(
    hass: HomeAssistant,
    resolvers: ResolverRegistry,
    operator: str,
    guest: str,
    expected: bool,
) -> None:
    """§7.1 — a group is how an OR is spelled, the top-level list being the AND."""
    hass.states.async_set(HOME, "on")
    hass.states.async_set(GUEST, guest)

    outcome = await async_evaluate(
        resolvers,
        None,
        [
            group(
                operator,
                comparison(HOME, CMP_EQUAL, "on"),
                comparison(GUEST, CMP_EQUAL, "on"),
            )
        ],
        now=ny(2026, 10, 2, 17),
    )

    assert outcome.passed is expected


async def test_a_decisive_member_settles_a_group_over_an_unreadable_one(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """An `or` with one member definitely true is true whatever the rest say.

    Reporting unknown here would be less informative and no safer, and it would
    make the answer depend on the order the user happened to list the members in.
    """
    hass.states.async_set(HOME, "on")

    permissive = await async_evaluate(
        resolvers,
        None,
        [
            group(
                GROUP_OR,
                comparison(HOME, CMP_EQUAL, "on"),
                comparison("binary_sensor.gone", CMP_EQUAL, "on"),
            )
        ],
        now=ny(2026, 10, 2, 17),
    )

    hass.states.async_set(HOME, "off")
    conjunctive = await async_evaluate(
        resolvers,
        None,
        [
            group(
                GROUP_AND,
                comparison(HOME, CMP_EQUAL, "on"),
                comparison("binary_sensor.gone", CMP_EQUAL, "on"),
            )
        ],
        now=ny(2026, 10, 2, 17),
    )

    assert permissive.passed is True
    # Definitely blocked, not unreadable: the `and` was settled by the member that
    # could be read.
    assert conjunctive.passed is False
    assert conjunctive.determined is True


async def test_a_group_with_nothing_decisive_is_undetermined(
    resolvers: ResolverRegistry,
) -> None:
    """Two unreadable members leave the group with no answer to give."""
    outcome = await async_evaluate(
        resolvers,
        None,
        [
            group(
                GROUP_AND,
                comparison("binary_sensor.gone", CMP_EQUAL, "on"),
                comparison("binary_sensor.also_gone", CMP_EQUAL, "on"),
            )
        ],
        now=ny(2026, 10, 2, 17),
    )

    assert outcome.determined is False
    assert outcome.blocking == ("and of 2 conditions",)


# --- D20: the day set as a condition ---------------------------------------


async def test_a_day_set_condition_asks_the_same_object_the_recurrence_does(
    resolvers: ResolverRegistry,
) -> None:
    """D20's payoff. "Only on Shabbat" means one thing, because there is one object."""
    day_sets = weekend_sets()

    saturday = await async_evaluate(
        resolvers, day_sets, [in_day_set("weekend")], now=ny(2026, 10, 3, 12)
    )
    friday = await async_evaluate(
        resolvers, day_sets, [in_day_set("weekend")], now=ny(2026, 10, 2, 12)
    )

    assert saturday.passed is True
    assert friday.passed is False
    assert friday.blocking == ("in day set weekend",)


async def test_negate_is_a_flag_rather_than_a_second_level_of_nesting(
    resolvers: ResolverRegistry,
) -> None:
    """"…unless it is a holiday" is the commonest form, and §7.1 has no `not` group."""
    outcome = await async_evaluate(
        resolvers,
        weekend_sets(),
        [in_day_set("weekend", negate=True)],
        now=ny(2026, 10, 3, 12),
    )

    assert outcome.passed is False
    assert outcome.blocking == ("not in day set weekend",)


async def test_a_dangling_day_set_condition_is_undetermined_not_false(
    resolvers: ResolverRegistry,
) -> None:
    """A deleted day set must not silently satisfy the rule that mentioned it."""
    outcome = await async_evaluate(
        resolvers, weekend_sets(), [in_day_set("gone")], now=ny(2026, 10, 3, 12)
    )

    assert outcome.determined is False
    assert outcome.problem is not None
    assert outcome.problem.reason is UnresolvedReason.UNKNOWN_KEY


# --- the typed comparison ladder -------------------------------------------


async def test_an_ordering_comparison_is_numeric_and_not_lexicographic(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """The bug this ladder exists to prevent, asserted at the value that shows it.

    HA hands out states as strings, so `"9" > "20"` is true. A temperature of 9
    must not satisfy `above: 20`, and the failure would be silent and only
    sometimes — which is what makes it expensive.
    """
    hass.states.async_set("sensor.outside", "9")

    outcome = await async_evaluate(
        resolvers,
        None,
        [comparison("sensor.outside", CMP_ABOVE, 20)],
        now=ny(2026, 10, 2, 17),
    )

    assert outcome.passed is False
    assert outcome.determined is True


async def test_ordering_two_strings_is_refused_rather_than_answered(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """No lexicographic fallback. A refusal the user can see beats a wrong answer."""
    hass.states.async_set("person.dan", "home")

    outcome = await async_evaluate(
        resolvers,
        None,
        [comparison("person.dan", CMP_ABOVE, "work")],
        now=ny(2026, 10, 2, 17),
    )

    assert outcome.determined is False
    assert outcome.problem is not None
    assert outcome.problem.reason is UnresolvedReason.ERROR


async def test_two_timestamps_compare_with_an_offset_in_seconds(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """"45 minutes before candle lighting", written as a condition rather than an anchor.

    Both sensors are `device_class: timestamp` in the motivating system (A.4), so
    both sides parse and the offset is seconds — which is the reading that makes
    the synagogue case expressible at all.
    """
    hass.states.async_set(CANDLE, "2026-10-02T18:23:00-04:00")
    hass.states.async_set(ZMAN, "2026-10-02T17:37:00-04:00")

    before = await async_evaluate(
        resolvers,
        None,
        [comparison(ZMAN, CMP_BELOW, entity_operand(CANDLE, offset=-2700))],
        now=ny(2026, 10, 2, 17),
    )

    hass.states.async_set(ZMAN, "2026-10-02T17:39:00-04:00")
    after = await async_evaluate(
        resolvers,
        None,
        [comparison(ZMAN, CMP_BELOW, entity_operand(CANDLE, offset=-2700))],
        now=ny(2026, 10, 2, 17),
    )

    assert before.passed is True
    assert after.passed is False


async def test_a_boolean_is_not_a_number(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """`True` is `1` in Python and must not become `1` half-way down the ladder.

    A `binary_sensor` compared with `above: 0` would otherwise pass for a state of
    `on` only if `on` were coerced somewhere, and manufacturing that reading is
    what makes a comparison mean something other than what it says.
    """
    hass.states.async_set("sensor.count", "1")

    outcome = await async_evaluate(
        resolvers,
        None,
        [comparison("sensor.count", CMP_AT_LEAST, True)],
        now=ny(2026, 10, 2, 17),
    )

    assert outcome.determined is False


async def test_a_boolean_constant_compares_as_the_text_it_renders_as(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """`eq: true` matches a state of `true`, not a state of `on`.

    Mapping `true` onto `on` here would make the comparison mean something other
    than what the entity's own state page shows.
    """
    hass.states.async_set("input_boolean.flag", "true")
    hass.states.async_set("binary_sensor.other", "on")

    matches = await async_evaluate(
        resolvers,
        None,
        [comparison("input_boolean.flag", CMP_EQUAL, True)],
        now=ny(2026, 10, 2, 17),
    )
    does_not = await async_evaluate(
        resolvers,
        None,
        [comparison("binary_sensor.other", CMP_EQUAL, True)],
        now=ny(2026, 10, 2, 17),
    )

    assert matches.passed is True
    assert does_not.passed is False


async def test_an_offset_with_nothing_to_add_itself_to_is_refused(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """An offset the engine silently discarded is a condition that lies.

    The schema cannot catch it, because the operand's type is only known once the
    entity has a state — which is the argument for reporting it here.
    """
    hass.states.async_set("person.dan", "home")
    hass.states.async_set("person.sam", "home")

    outcome = await async_evaluate(
        resolvers,
        None,
        [comparison("person.dan", CMP_EQUAL, entity_operand("person.sam", offset=60))],
        now=ny(2026, 10, 2, 17),
    )

    assert outcome.determined is False
    assert outcome.problem is not None
    assert outcome.problem.reason is UnresolvedReason.ERROR


@pytest.mark.parametrize(
    ("operator", "expected"),
    [(CMP_IN, True), (CMP_NOT_IN, False)],
)
async def test_membership_operators_take_a_list_of_constants(
    hass: HomeAssistant, resolvers: ResolverRegistry, operator: str, expected: bool
) -> None:
    """What `in` is for: several acceptable states of one entity."""
    hass.states.async_set(THERMOSTAT, "heat")

    outcome = await async_evaluate(
        resolvers,
        None,
        [comparison(THERMOSTAT, operator, ["heat", "heat_cool"])],
        now=ny(2026, 10, 2, 17),
    )

    assert outcome.passed is expected


async def test_an_attribute_is_read_rather_than_the_state(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """D23's attribute picker, and `None` meaning the state itself."""
    hass.states.async_set(THERMOSTAT, "heat", {"temperature": 21})

    outcome = await async_evaluate(
        resolvers,
        None,
        [comparison(THERMOSTAT, CMP_AT_LEAST, 20, attribute="temperature")],
        now=ny(2026, 10, 2, 17),
    )

    assert outcome.passed is True


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        (None, UnresolvedReason.UNAVAILABLE),
        ("unknown", UnresolvedReason.UNAVAILABLE),
        ("unavailable", UnresolvedReason.UNAVAILABLE),
    ],
)
async def test_an_entity_with_nothing_to_say_is_undetermined(
    hass: HomeAssistant,
    resolvers: ResolverRegistry,
    state: str | None,
    reason: UnresolvedReason,
) -> None:
    """Three ways of having no value, and none of them is `False`."""
    if state is not None:
        hass.states.async_set(HOME, state)

    outcome = await async_evaluate(
        resolvers, None, [comparison(HOME, CMP_EQUAL, "on")], now=ny(2026, 10, 2, 17)
    )

    assert outcome.determined is False
    assert outcome.problem is not None
    assert outcome.problem.reason is reason


async def test_a_missing_attribute_is_distinguished_from_a_missing_entity(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """`unknown_key`, not `unavailable`: the fix is a different one in each case."""
    hass.states.async_set(THERMOSTAT, "heat", {"temperature": 21})

    outcome = await async_evaluate(
        resolvers,
        None,
        [comparison(THERMOSTAT, CMP_AT_LEAST, 20, attribute="fan_mode")],
        now=ny(2026, 10, 2, 17),
    )

    assert outcome.problem is not None
    assert outcome.problem.reason is UnresolvedReason.UNKNOWN_KEY


async def test_an_unavailable_entitys_stale_attributes_are_not_read(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """An unplugged thermostat's last-published temperature is not its temperature.

    The state check runs even when an attribute was asked for, which is what stops
    a condition passing for a device that has been off since Tuesday.
    """
    hass.states.async_set(THERMOSTAT, "unavailable", {"temperature": 21})

    outcome = await async_evaluate(
        resolvers,
        None,
        [comparison(THERMOSTAT, CMP_AT_LEAST, 20, attribute="temperature")],
        now=ny(2026, 10, 2, 17),
    )

    assert outcome.problem is not None
    assert outcome.problem.reason is UnresolvedReason.UNAVAILABLE


# --- `for:` — the one condition shape that needs an instant -----------------


async def test_a_for_period_is_met_exactly_at_the_boundary(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """`>=` where core uses a strict `>`, and the difference is deliberate.

    The engine evaluates at instants it chose itself (`next_transition_at`), so a
    tick landing exactly on the boundary is a case that will occur rather than a
    hypothetical, and a 60-second condition should be met at 60 seconds.
    """
    hass.states.async_set(HOME, "on")
    anchor = hass.states.get(HOME).last_changed
    condition = comparison(HOME, CMP_EQUAL, "on", **{"for": 60})

    at_boundary = await async_evaluate(
        resolvers, None, [condition], now=anchor + timedelta(seconds=60)
    )
    just_before = await async_evaluate(
        resolvers, None, [condition], now=anchor + timedelta(seconds=59)
    )

    assert at_boundary.passed is True
    assert just_before.passed is False
    assert just_before.determined is True


async def test_a_for_period_on_an_attribute_measures_from_last_updated(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """The anchor split, copied from core's `_state_valid_since` on purpose.

    An attribute can move while the state does not, so timing an attribute test
    from `last_changed` would report a thermostat's brand-new setpoint as having
    held for an hour. Both conditions are evaluated at the *same* `now`, one second
    after the state last changed: the state test is satisfied and the attribute
    test is not, which is only possible if the two read different anchors.
    """
    hass.states.async_set(THERMOSTAT, "heat", {"temperature": 20})
    changed = hass.states.get(THERMOSTAT).last_changed
    hass.states.async_set(THERMOSTAT, "heat", {"temperature": 21})
    state = hass.states.get(THERMOSTAT)
    # The whole test rests on the second write having moved one timestamp and not
    # the other, so it is asserted rather than assumed.
    assert state.last_changed == changed
    assert state.last_updated > changed

    now = changed + timedelta(seconds=1)
    on_state = await async_evaluate(
        resolvers,
        None,
        [comparison(THERMOSTAT, CMP_EQUAL, "heat", **{"for": 1})],
        now=now,
    )
    on_attribute = await async_evaluate(
        resolvers,
        None,
        [
            comparison(
                THERMOSTAT, CMP_AT_LEAST, 21, attribute="temperature", **{"for": 1}
            )
        ],
        now=now,
    )

    assert on_state.passed is True
    assert on_attribute.passed is False


async def test_a_for_period_is_not_reached_when_the_comparison_itself_fails(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """`for:` narrows a comparison that already holds; it never rescues one."""
    hass.states.async_set(HOME, "off")
    anchor = hass.states.get(HOME).last_changed

    outcome = await async_evaluate(
        resolvers,
        None,
        [comparison(HOME, CMP_EQUAL, "on", **{"for": 1})],
        now=anchor + timedelta(hours=1),
    )

    assert outcome.passed is False
    assert outcome.determined is True


# --- D25: a `During` rule's predicate is always live -----------------------


async def test_an_interval_whose_conditions_do_not_hold_is_not_entered(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """D25's first half. Nothing is recorded, either — the chance is not spent."""
    hass.states.async_set(HOME, "off")
    item = schedule(
        recurrence=daily(),
        rules=[
            during_rule("22:00:00", 4 * 3600, conditions=[comparison(HOME, CMP_EQUAL, "on")])
        ],
    )

    result = await async_plan_tick(
        resolvers,
        item,
        EngineState(evaluated_through=ny(2026, 10, 2, 21, 59)),
        now=ny(2026, 10, 2, 22, 0, 10),
    )

    assert result.transitions == ()
    assert result.state.held == ()


async def test_an_interval_is_entered_late_when_its_conditions_come_good(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """The difference from an `At` rule, and the reason D25 says "always live".

    An interval is a statement about a stretch of time, so entering it half-way
    through is correct rather than a concession — which is what makes the
    predicate worth re-asking on every pass.
    """
    hass.states.async_set(HOME, "off")
    item = schedule(
        recurrence=daily(),
        rules=[
            during_rule("22:00:00", 4 * 3600, conditions=[comparison(HOME, CMP_EQUAL, "on")])
        ],
    )
    blocked = await async_plan_tick(
        resolvers,
        item,
        EngineState(evaluated_through=ny(2026, 10, 2, 21, 59)),
        now=ny(2026, 10, 2, 22, 0, 10),
    )

    hass.states.async_set(HOME, "on")
    entered = await async_plan_tick(
        resolvers, item, blocked.state, now=ny(2026, 10, 2, 22, 30)
    )

    (transition,) = entered.transitions
    assert transition.kind is TransitionKind.ENTER
    assert transition.at == ny(2026, 10, 2, 22)
    assert transition.lateness == timedelta(minutes=30)
    assert transition.conditions is not None
    assert transition.conditions.passed is True


async def test_a_held_interval_exits_when_its_conditions_stop_holding(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """§7.3's fix, stated positively.

    Upstream's `track_conditions` re-fires when any individual condition flips
    rather than when the overall and/or result does (card#878). Here the exit is
    driven by `ConditionOutcome`, which is the result of the whole list.
    """
    hass.states.async_set(HOME, "on")
    item = schedule(
        recurrence=daily(),
        rules=[
            during_rule("22:00:00", 4 * 3600, conditions=[comparison(HOME, CMP_EQUAL, "on")])
        ],
    )
    entered = await async_plan_tick(
        resolvers,
        item,
        EngineState(evaluated_through=ny(2026, 10, 2, 21, 59)),
        now=ny(2026, 10, 2, 22, 0, 10),
    )

    hass.states.async_set(HOME, "off")
    exited = await async_plan_tick(
        resolvers, item, entered.state, now=ny(2026, 10, 2, 23)
    )

    (transition,) = exited.transitions
    assert transition.kind is TransitionKind.EXIT
    assert transition.cause is ExitCause.CONDITIONS
    # The exit is *now*, not the window end: the predicate went false here.
    assert transition.at == ny(2026, 10, 2, 23)
    assert transition.conditions is not None
    assert transition.conditions.blocking == (f"{HOME} eq on",)
    assert exited.state.held == ()


async def test_a_member_of_an_or_flipping_does_not_disturb_a_held_interval(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """card#878 itself, as a regression test.

    Upstream's `track_conditions` re-evaluates per condition, so in `home OR guest`
    the guest leaving re-fires the rule even though somebody is still home and the
    predicate never changed value. It is the clearest single reason this project
    exists rather than contributing, so it gets a test that fails if the engine ever
    starts watching members instead of outcomes.

    Both directions are checked. The `or` stays true across the flip and nothing
    happens; then the *other* member goes false, the predicate finally changes, and
    the interval exits.
    """
    hass.states.async_set(HOME, "on")
    hass.states.async_set(GUEST, "on")
    item = schedule(
        recurrence=daily(),
        rules=[
            during_rule(
                "22:00:00",
                4 * 3600,
                conditions=[
                    group(
                        GROUP_OR,
                        comparison(HOME, CMP_EQUAL, "on"),
                        comparison(GUEST, CMP_EQUAL, "on"),
                    )
                ],
            )
        ],
    )
    entered = await async_plan_tick(
        resolvers,
        item,
        EngineState(evaluated_through=ny(2026, 10, 2, 21, 59)),
        now=ny(2026, 10, 2, 22, 0, 10),
    )
    assert [t.kind for t in entered.transitions] == [TransitionKind.ENTER]

    hass.states.async_set(GUEST, "off")
    undisturbed = await async_plan_tick(
        resolvers, item, entered.state, now=ny(2026, 10, 2, 22, 30)
    )

    assert undisturbed.transitions == ()
    assert len(undisturbed.state.held) == 1

    hass.states.async_set(HOME, "off")
    exited = await async_plan_tick(
        resolvers, item, undisturbed.state, now=ny(2026, 10, 2, 23)
    )

    (transition,) = exited.transitions
    assert transition.kind is TransitionKind.EXIT
    assert transition.cause is ExitCause.CONDITIONS


async def test_an_unreadable_condition_keeps_a_held_interval_held(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """Unknown changes nothing — the reading this design chose, and it is provisional.

    A motion sensor restarting for four seconds must not turn the lights off. The
    rejected alternative is fail-closed, which is what core's `condition.state`
    effectively does; D4's `latch` would not help, because the user did not ask for
    a latch, they asked for a condition.
    """
    hass.states.async_set(HOME, "on")
    item = schedule(
        recurrence=daily(),
        rules=[
            during_rule("22:00:00", 4 * 3600, conditions=[comparison(HOME, CMP_EQUAL, "on")])
        ],
    )
    entered = await async_plan_tick(
        resolvers,
        item,
        EngineState(evaluated_through=ny(2026, 10, 2, 21, 59)),
        now=ny(2026, 10, 2, 22, 0, 10),
    )

    hass.states.async_set(HOME, "unavailable")
    survived = await async_plan_tick(
        resolvers, item, entered.state, now=ny(2026, 10, 2, 23)
    )

    assert survived.transitions == ()
    assert len(survived.state.held) == 1


async def test_latch_keeps_a_held_interval_to_its_window_end(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """D4 — "once the lights are on for the evening, leave them on".

    Same schedule, same failing predicate as the exit test above; only the flag
    differs, which is the point of having it be a stored flag rather than a
    heuristic.
    """
    hass.states.async_set(HOME, "on")
    item = schedule(
        recurrence=daily(),
        rules=[
            during_rule(
                "22:00:00",
                4 * 3600,
                latch=True,
                conditions=[comparison(HOME, CMP_EQUAL, "on")],
            )
        ],
    )
    entered = await async_plan_tick(
        resolvers,
        item,
        EngineState(evaluated_through=ny(2026, 10, 2, 21, 59)),
        now=ny(2026, 10, 2, 22, 0, 10),
    )

    hass.states.async_set(HOME, "off")
    latched = await async_plan_tick(
        resolvers, item, entered.state, now=ny(2026, 10, 2, 23)
    )
    ended = await async_plan_tick(
        resolvers, item, latched.state, now=ny(2026, 10, 3, 2, 0, 5)
    )

    assert latched.transitions == ()
    assert len(latched.state.held) == 1
    (transition,) = ended.transitions
    assert transition.kind is TransitionKind.EXIT
    assert transition.cause is ExitCause.WINDOW_END


# --- D26: what a failed predicate means for an `At` rule -------------------


async def test_skip_is_the_default_policy(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """The schema fills in `skip`, so an author who wrote no policy cannot be surprised."""
    hass.states.async_set(HOME, "off")
    item = schedule(
        recurrence=daily(),
        rules=[at_rule("17:00:00", conditions=[comparison(HOME, CMP_EQUAL, "on")])],
    )
    assert item["rules"][0]["condition_policy"] == {"kind": POLICY_SKIP}

    result = await async_plan_tick(
        resolvers,
        item,
        EngineState(evaluated_through=ny(2026, 10, 2, 16, 59)),
        now=ny(2026, 10, 2, 17, 0, 30),
    )

    (transition,) = result.transitions
    assert transition.kind is TransitionKind.SKIPPED
    assert transition.conditions is not None
    assert transition.conditions.summary == f"{HOME} eq on"
    # Ruled on, so a later tick does not reconsider it.
    assert len(result.state.decided) == 1
    assert result.state.waiting == ()


async def test_wait_until_emits_nothing_and_records_the_wait(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """D26's second policy. The record is the state, not a transition.

    A `DEFERRED` transition kind was considered and rejected: no consumer would
    act on it, and `EngineState.waiting` already says everything a restart needs.
    """
    hass.states.async_set(HOME, "off")
    item = schedule(
        recurrence=daily(),
        rules=[
            at_rule(
                "17:00:00",
                conditions=[comparison(HOME, CMP_EQUAL, "on")],
                condition_policy=wait_until(3600),
            )
        ],
    )

    result = await async_plan_tick(
        resolvers,
        item,
        EngineState(evaluated_through=ny(2026, 10, 2, 16, 59)),
        now=ny(2026, 10, 2, 17, 0, 30),
    )

    assert result.transitions == ()
    assert result.state.decided == ()
    (pending,) = result.state.waiting
    assert pending == PendingAt(
        rule_id="r1",
        start_date=ny(2026, 10, 2).date(),
        at=ny(2026, 10, 2, 17),
        deadline=ny(2026, 10, 2, 18),
    )
    # The deadline is a scheduled event in its own right, so the engine wakes for
    # it rather than logging the skip whenever the next unrelated tick happens.
    assert result.next_at == ny(2026, 10, 2, 18)


async def test_a_wait_fires_when_the_conditions_come_good(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """The point of the policy: late is better than never, within a stated bound."""
    hass.states.async_set(HOME, "off")
    item = schedule(
        recurrence=daily(),
        rules=[
            at_rule(
                "17:00:00",
                conditions=[comparison(HOME, CMP_EQUAL, "on")],
                condition_policy=wait_until(3600),
            )
        ],
    )
    waiting = await async_plan_tick(
        resolvers,
        item,
        EngineState(evaluated_through=ny(2026, 10, 2, 16, 59)),
        now=ny(2026, 10, 2, 17, 0, 30),
    )

    hass.states.async_set(HOME, "on")
    fired = await async_plan_tick(
        resolvers, item, waiting.state, now=ny(2026, 10, 2, 17, 30)
    )

    (transition,) = fired.transitions
    assert transition.kind is TransitionKind.FIRE
    assert transition.at == ny(2026, 10, 2, 17)
    assert transition.lateness == timedelta(minutes=30)
    assert fired.state.waiting == ()
    assert len(fired.state.decided) == 1


async def test_a_wait_that_expires_skips_at_the_deadline(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """And the deadline is tested *after* the conditions, on purpose.

    `next_transition_at` schedules a tick landing exactly on the deadline, so an
    occurrence whose conditions came good at that instant must fire rather than
    skip. Asserted in both directions, because the ordering is invisible otherwise.
    """
    hass.states.async_set(HOME, "off")
    item = schedule(
        recurrence=daily(),
        rules=[
            at_rule(
                "17:00:00",
                conditions=[comparison(HOME, CMP_EQUAL, "on")],
                condition_policy=wait_until(3600),
            )
        ],
    )
    waiting = await async_plan_tick(
        resolvers,
        item,
        EngineState(evaluated_through=ny(2026, 10, 2, 16, 59)),
        now=ny(2026, 10, 2, 17, 0, 30),
    )

    expired = await async_plan_tick(
        resolvers, item, waiting.state, now=ny(2026, 10, 2, 18)
    )

    (transition,) = expired.transitions
    assert transition.kind is TransitionKind.SKIPPED
    assert transition.lateness == timedelta(hours=1)
    assert expired.state.waiting == ()

    hass.states.async_set(HOME, "on")
    rescued = await async_plan_tick(
        resolvers, item, waiting.state, now=ny(2026, 10, 2, 18)
    )
    assert [t.kind for t in rescued.transitions] == [TransitionKind.FIRE]


async def test_a_tick_arriving_after_the_deadline_skips_without_waiting(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """The wait is never entered when there is nothing left to wait for.

    Same outcome the wait would have produced, reached without recording a promise
    that had already expired.
    """
    hass.states.async_set(HOME, "off")
    item = schedule(
        recurrence=daily(),
        rules=[
            at_rule(
                "17:00:00",
                conditions=[comparison(HOME, CMP_EQUAL, "on")],
                condition_policy=wait_until(3600),
            )
        ],
    )

    result = await async_plan_tick(
        resolvers,
        item,
        EngineState(evaluated_through=ny(2026, 10, 2, 16, 59)),
        now=ny(2026, 10, 2, 18, 30),
    )

    (transition,) = result.transitions
    assert transition.kind is TransitionKind.SKIPPED
    assert result.state.waiting == ()


async def test_a_wait_is_abandoned_when_its_rule_is_edited_away(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """Nothing is owed. Unlike a held interval, a wait created no state to undo."""
    hass.states.async_set(HOME, "off")
    item = schedule(
        recurrence=daily(),
        rules=[
            at_rule(
                "17:00:00",
                conditions=[comparison(HOME, CMP_EQUAL, "on")],
                condition_policy=wait_until(3600),
            )
        ],
    )
    waiting = await async_plan_tick(
        resolvers,
        item,
        EngineState(evaluated_through=ny(2026, 10, 2, 16, 59)),
        now=ny(2026, 10, 2, 17, 0, 30),
    )
    assert len(waiting.state.waiting) == 1

    # The replacement rule fires inside the same pass, so the assertion that the
    # wait is gone cannot be satisfied by the pass having done nothing at all.
    edited = schedule(recurrence=daily(), rules=[at_rule("17:15:00", rule_id="r2")])
    after = await async_plan_tick(
        resolvers, edited, waiting.state, now=ny(2026, 10, 2, 17, 30)
    )

    assert [t.rule_id for t in after.transitions] == ["r2"]
    assert after.state.waiting == ()


async def test_a_missed_occurrence_never_reaches_its_conditions(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """The order of the two tests in `_async_decide_at` is itself a decision.

    "Skipped: nobody home" in the log for something that was actually missed by
    three hours would be a false explanation, so the observed test runs first and
    the conditions are never consulted.
    """
    hass.states.async_set(HOME, "off")
    item = schedule(
        recurrence=daily(),
        rules=[at_rule("17:00:00", conditions=[comparison(HOME, CMP_EQUAL, "on")])],
    )

    result = await async_plan_recovery(
        resolvers,
        item,
        EngineState(evaluated_through=ny(2026, 10, 2, 16)),
        now=ny(2026, 10, 2, 20),
    )

    (transition,) = result.transitions
    assert transition.kind is TransitionKind.MISSED
    assert transition.conditions is None


async def test_engine_state_round_trips_a_pending_wait(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """D26's promise has to survive a restart, so it has to survive `.storage`.

    The deadline is stored rather than recomputed: recomputing it from the rule on
    each pass would let an edit made mid-wait extend a promise already given, or
    retroactively expire one.
    """
    hass.states.async_set(HOME, "off")
    item = schedule(
        recurrence=daily(),
        rules=[
            at_rule(
                "17:00:00",
                conditions=[comparison(HOME, CMP_EQUAL, "on")],
                condition_policy=wait_until(3600),
            )
        ],
    )
    waiting = await async_plan_tick(
        resolvers,
        item,
        EngineState(evaluated_through=ny(2026, 10, 2, 16, 59)),
        now=ny(2026, 10, 2, 17, 0, 30),
    )

    restored = EngineState.from_dict(waiting.state.as_dict())

    assert restored == waiting.state


async def test_one_predicate_per_rule_per_pass(
    hass: HomeAssistant, resolvers: ResolverRegistry
) -> None:
    """D25 asks one predicate three questions, and they must not disagree.

    A rule with an `At` and a `During` half cannot be written, so the case this
    guards is the interval loop's own two questions — may the held one survive,
    may the enumerated one be entered — asked of the same rule in one pass. With a
    per-pass cache the two cannot answer differently; without it, a sensor that
    changed between the reads would produce an exit and an entry for the same
    interval in the same evaluation.
    """
    hass.states.async_set(HOME, "on")
    item = schedule(
        recurrence=daily(),
        rules=[
            during_rule("22:00:00", 4 * 3600, conditions=[comparison(HOME, CMP_EQUAL, "on")])
        ],
    )
    entered = await async_plan_tick(
        resolvers,
        item,
        EngineState(evaluated_through=ny(2026, 10, 2, 21, 59)),
        now=ny(2026, 10, 2, 22, 0, 10),
    )

    steady = await async_plan_tick(
        resolvers, item, entered.state, now=ny(2026, 10, 2, 23)
    )

    assert steady.transitions == ()
    assert len(steady.state.held) == 1
