"""What the schema accepts and, more usefully, what it refuses.

The rejections are the point. D34 makes the schema the constraining artifact for
the whole product — no field may exist that the visual editor cannot render —
so a test that only proves the happy path proves the least interesting half.
"""

from __future__ import annotations

from typing import Any

import pytest
import voluptuous as vol

from custom_components.almanac.schema import (
    ANCHOR_SCHEMA,
    CREATE_SCHEMA,
    RULE_SCHEMA,
    UPDATE_SCHEMA,
    suggested_object_id,
)


# --- anchors (D6, D7) ------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            {"kind": "clock", "at": "17:00"},
            {"kind": "clock", "at": "17:00:00"},
        ),
        (
            {"kind": "entity_time", "entity_id": "sensor.candle_lighting"},
            {
                "kind": "entity_time",
                "entity_id": "sensor.candle_lighting",
                "offset": 0,
            },
        ),
        (
            {
                "kind": "entity_time",
                "entity_id": "sensor.candle_lighting",
                "offset": -45 * 60,
            },
            {
                "kind": "entity_time",
                "entity_id": "sensor.candle_lighting",
                "offset": -2700,
            },
        ),
        (
            {"kind": "resolver", "domain": "sun", "key": "sunset", "offset": -1200},
            {
                "kind": "resolver",
                "domain": "sun",
                "key": "sunset",
                "offset": -1200,
                "edge": "start",
            },
        ),
        (
            {
                "kind": "resolver",
                "domain": "hdate",
                "key": "candle_lighting",
                "offset": -2700,
                "edge": "end",
            },
            {
                "kind": "resolver",
                "domain": "hdate",
                "key": "candle_lighting",
                "offset": -2700,
                "edge": "end",
            },
        ),
    ],
)
def test_anchor_accepts_all_three_kinds(
    payload: dict[str, Any], expected: dict[str, Any]
) -> None:
    """D6's three kinds, each normalised to a JSON-storable shape."""
    assert ANCHOR_SCHEMA(payload) == expected


def test_anchor_offset_has_no_magnitude_clamp() -> None:
    """D7 — the card's MAX_OFFFSET_HOURS has nothing behind it (A.3).

    A three-day offset is accepted. The editor warns past 24h; the schema does
    not get a vote.
    """
    anchor = ANCHOR_SCHEMA(
        {"kind": "resolver", "domain": "sun", "key": "sunset", "offset": 3 * 86400}
    )
    assert anchor["offset"] == 259200


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "solar", "at": "17:00"},
        {"kind": "clock", "at": "not a time"},
        {"kind": "clock"},
        {"kind": "entity_time", "entity_id": "not_an_entity_id"},
        {"kind": "resolver", "domain": "sun"},
        # An offset is a whole number of seconds; bool is an int in Python and
        # must not sneak through.
        {"kind": "resolver", "domain": "sun", "key": "sunset", "offset": True},
        {"kind": "resolver", "domain": "sun", "key": "sunset", "edge": "middle"},
    ],
)
def test_anchor_rejects(payload: dict[str, Any]) -> None:
    """Anything that is not one of D6's three shapes."""
    with pytest.raises(vol.Invalid):
        ANCHOR_SCHEMA(payload)


# --- rules (D2-D5) ---------------------------------------------------------


def test_at_rule_defaults() -> None:
    """An `At` rule is an anchor plus the defaults D26 and D41 chose."""
    rule = RULE_SCHEMA({"kind": "at", "anchor": {"kind": "clock", "at": "17:00"}})
    assert rule["enabled"] is True
    assert rule["condition_policy"] == {"kind": "skip"}
    # D41 — a missed occurrence is logged and not fired. The grace window is
    # off, and "off" is the absence of a window rather than a zero one.
    assert rule["grace"] is None
    assert rule["id"]


def test_rule_ids_are_minted_and_distinct() -> None:
    """D45's `run_now` takes a rule reference, so a rule needs an identity."""
    first = RULE_SCHEMA({"kind": "at", "anchor": {"kind": "clock", "at": "17:00"}})
    second = RULE_SCHEMA({"kind": "at", "anchor": {"kind": "clock", "at": "17:00"}})
    assert first["id"] != second["id"]


def test_rule_id_is_preserved_when_supplied() -> None:
    """Re-saving a schedule must not renumber its rules."""
    rule = RULE_SCHEMA(
        {"kind": "at", "id": "rule-1", "anchor": {"kind": "clock", "at": "17:00"}}
    )
    assert rule["id"] == "rule-1"


def test_during_rule_with_duration_end() -> None:
    """The simple interval: an anchor and a length."""
    rule = RULE_SCHEMA(
        {
            "kind": "during",
            "start_anchor": {"kind": "clock", "at": "17:00"},
            "end": {"kind": "duration", "duration": 3600},
        }
    )
    assert rule["end"] == {"kind": "duration", "duration": 3600}
    # D3 and D4 — both explicit, both stored, neither inferred.
    assert rule["on_exit"] == {"kind": "leave"}
    assert rule["latch"] is False


def test_during_rule_with_anchor_end_spans_midnight_without_saying_so() -> None:
    """D38 — the end anchor resolves to its first occurrence at or after the start.

    Nothing in the schema checks that the end is later than the start, because
    by construction there is no inversion to detect. The candle-lighting to
    havdalah pairing is the same shape as 23:00 -> 02:00.
    """
    rule = RULE_SCHEMA(
        {
            "kind": "during",
            "start_anchor": {
                "kind": "resolver",
                "domain": "hdate",
                "key": "candle_lighting",
                "offset": -2700,
            },
            "end": {
                "kind": "anchor",
                "anchor": {
                    "kind": "resolver",
                    "domain": "hdate",
                    "key": "havdalah",
                    "offset": 1800,
                },
            },
        }
    )
    assert rule["end"]["anchor"]["key"] == "havdalah"


def test_during_rule_has_no_condition_policy() -> None:
    """D25 — for a `During` rule the conditions are the activation predicate.

    The absence of the field is the decision: there is no per-rule choice to
    make, so offering one would invite the `track_conditions` confusion back in
    under a new name (§7.3).
    """
    with pytest.raises(vol.Invalid):
        RULE_SCHEMA(
            {
                "kind": "during",
                "start_anchor": {"kind": "clock", "at": "17:00"},
                "end": {"kind": "duration", "duration": 3600},
                "condition_policy": {"kind": "skip"},
            }
        )


def test_wait_until_policy_requires_a_deadline() -> None:
    """D26 — an unbounded wait is what today's toggle silently does."""
    with pytest.raises(vol.Invalid):
        RULE_SCHEMA(
            {
                "kind": "at",
                "anchor": {"kind": "clock", "at": "17:00"},
                "condition_policy": {"kind": "wait_until"},
            }
        )
    rule = RULE_SCHEMA(
        {
            "kind": "at",
            "anchor": {"kind": "clock", "at": "17:00"},
            "condition_policy": {"kind": "wait_until", "deadline": 1800},
        }
    )
    assert rule["condition_policy"]["deadline"] == 1800


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "moment", "anchor": {"kind": "clock", "at": "17:00"}},
        {"kind": "at"},
        {"kind": "during", "start_anchor": {"kind": "clock", "at": "17:00"}},
    ],
)
def test_rule_rejects(payload: dict[str, Any]) -> None:
    """D2 — a rule is one of exactly two shapes, and both need their anchors."""
    with pytest.raises(vol.Invalid):
        RULE_SCHEMA(payload)


@pytest.mark.parametrize(
    ("field", "value", "step"),
    [
        ("actions", [{"service": "light.turn_on"}], "5"),
        ("conditions", [{"entity_id": "binary_sensor.home"}], "4"),
    ],
)
def test_later_steps_are_reserved_not_open(
    field: str, value: Any, step: str
) -> None:
    """A field whose contents a later step owns accepts nothing but empty.

    The place is reserved so the storage shape does not change when step 4 and
    step 5 land; the contents are refused so nothing is stored before the step
    that owns the UI has decided what it means (D34).
    """
    payload = {"kind": "at", "anchor": {"kind": "clock", "at": "17:00"}, field: value}
    with pytest.raises(vol.Invalid, match=f"build step {step}"):
        RULE_SCHEMA(payload)


# --- the schedule (§2) -----------------------------------------------------


def test_create_fills_in_every_default() -> None:
    """A stored schedule is fully populated, so the code view has nothing to hide."""
    schedule = CREATE_SCHEMA({"name": "Shabbat lights"})
    assert schedule["enabled"] is True
    assert schedule["description"] == ""
    assert schedule["rules"] == []
    assert schedule["date_window"] == {"from": None, "until": None}
    assert schedule["recurrence"]["kind"] == "weekdays"
    assert schedule["recurrence"]["weekdays"] == [
        "mon",
        "tue",
        "wed",
        "thu",
        "fri",
        "sat",
        "sun",
    ]
    # D46's three axes, each with a visible value rather than an absence.
    assert schedule["completion"] == {
        "finished_when": {"kind": "never"},
        "then": {"kind": "keep"},
        "count_on": "scheduled",
    }


def test_create_requires_a_name() -> None:
    """A nameless schedule cannot be found, which is the complaint D54 answers."""
    with pytest.raises(vol.Invalid):
        CREATE_SCHEMA({})
    with pytest.raises(vol.Invalid):
        CREATE_SCHEMA({"name": ""})


def test_labels_and_areas_are_not_ours_to_store() -> None:
    """D56 — HA's own registries replace bespoke tags, so there is no field here."""
    for field in ("labels", "categories", "area", "tags"):
        with pytest.raises(vol.Invalid):
            CREATE_SCHEMA({"name": "Shabbat lights", field: ["x"]})


def test_update_does_not_fill_in_defaults() -> None:
    """An update carries only what changed.

    If the update schema applied defaults, arming a schedule — which is the
    single-field write `{"enabled": False}` the switch makes — would also
    replace its rules with the empty list.
    """
    update = UPDATE_SCHEMA({"enabled": False})
    assert update == {"enabled": False}


def test_update_cannot_carry_an_object_id() -> None:
    """D66, structurally.

    Not "we avoid re-deriving the slug" but "an update has nowhere to put one".
    The merge in `_update_data` therefore has nothing to overwrite, and the
    guarantee survives the next person who writes `{**item, **updates}`.
    """
    with pytest.raises(vol.Invalid):
        UPDATE_SCHEMA({"object_id": "something_else"})


def test_create_can_carry_an_object_id() -> None:
    """D66 — editable at creation, which is the other half of the decision."""
    schedule = CREATE_SCHEMA(
        {"name": "Shabbat — sanctuary lights & PA", "object_id": "shabbat_sanctuary"}
    )
    assert schedule["object_id"] == "shabbat_sanctuary"


def test_suggested_object_id_is_a_suggestion_not_a_good_name() -> None:
    """The example from D66, which is why the two strings are allowed to differ."""
    assert suggested_object_id("Shabbat — sanctuary lights & PA") == (
        "shabbat_sanctuary_lights_pa"
    )


@pytest.mark.parametrize(
    "recurrence",
    [
        {"kind": "weekdays", "weekdays": ["fri"]},
        {"kind": "dates", "dates": ["2026-10-02", "2026-10-09"]},
        {"kind": "nth_weekday", "nth": -1, "weekday": "sun"},
        {"kind": "every_n", "interval": 3, "from": "2026-10-01"},
        {"kind": "day_set", "day_set_id": "cleaning_days"},
    ],
)
def test_recurrence_kinds(recurrence: dict[str, Any]) -> None:
    """D1 and D18 — recurrence picks the dates on which rules begin."""
    schedule = CREATE_SCHEMA({"name": "x", "recurrence": recurrence})
    assert schedule["recurrence"]["kind"] == recurrence["kind"]


@pytest.mark.parametrize(
    "recurrence",
    [
        {"kind": "weekdays", "weekdays": []},
        {"kind": "weekdays", "weekdays": ["friday"]},
        {"kind": "nth_weekday", "nth": 0, "weekday": "sun"},
        {"kind": "every_n", "interval": 3},
        {"kind": "cron", "expr": "0 17 * * 5"},
    ],
)
def test_recurrence_rejects(recurrence: dict[str, Any]) -> None:
    """D58 — a grammar this small is what makes enumeration a type property."""
    with pytest.raises(vol.Invalid):
        CREATE_SCHEMA({"name": "x", "recurrence": recurrence})


def test_completion_axes_are_independent() -> None:
    """D46 — three axes, not three magic names.

    The third is the one today's model cannot express at all: a one-shot
    schedule skipped by a condition has, under one reading, not yet done its
    job.
    """
    schedule = CREATE_SCHEMA(
        {
            "name": "x",
            "completion": {
                "finished_when": {"kind": "occurrences", "count": 3},
                "then": {"kind": "delete"},
                "count_on": "actions_succeeded",
            },
        }
    )
    assert schedule["completion"]["finished_when"] == {
        "kind": "occurrences",
        "count": 3,
    }
    assert schedule["completion"]["then"] == {"kind": "delete"}
    assert schedule["completion"]["count_on"] == "actions_succeeded"


@pytest.mark.parametrize(
    "completion",
    [
        {"finished_when": {"kind": "occurrences"}},
        {"finished_when": {"kind": "occurrences", "count": 0}},
        {"finished_when": {"kind": "after_it_triggers"}},
        {"count_on": "whenever"},
        {"then": {"kind": "run_a_script"}},
    ],
)
def test_completion_rejects(completion: dict[str, Any]) -> None:
    """Including the incumbent's undefined "after it triggers"."""
    with pytest.raises(vol.Invalid):
        CREATE_SCHEMA({"name": "x", "completion": completion})


def test_the_flagship_schedule_validates() -> None:
    """Candle lighting to havdalah, with the stages D5 exists for.

    One `During` for the interval and one `At` hanging off the same anchor —
    the shape §16.1's track draws. The action lists are empty because step 5
    owns them; the anchors and the pairing are what step 1 has to get right.
    """
    schedule = CREATE_SCHEMA(
        {
            "name": "Shabbat — sanctuary",
            "object_id": "shabbat_sanctuary",
            "recurrence": {"kind": "weekdays", "weekdays": ["fri"]},
            "rules": [
                {
                    "kind": "during",
                    "start_anchor": {
                        "kind": "resolver",
                        "domain": "hdate",
                        "key": "candle_lighting",
                        "offset": -2700,
                    },
                    "end": {
                        "kind": "anchor",
                        "anchor": {
                            "kind": "resolver",
                            "domain": "hdate",
                            "key": "havdalah",
                            "offset": 1800,
                        },
                    },
                    "on_exit": {"kind": "restore"},
                    "latch": True,
                },
                {
                    "kind": "at",
                    "anchor": {
                        "kind": "resolver",
                        "domain": "hdate",
                        "key": "candle_lighting",
                        "offset": -3600,
                    },
                },
            ],
        }
    )
    assert len(schedule["rules"]) == 2
    assert schedule["rules"][0]["on_exit"] == {"kind": "restore"}
    assert schedule["rules"][0]["latch"] is True
