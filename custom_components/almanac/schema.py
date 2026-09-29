"""Storage schema for schedules.

D34 makes this the constraining artifact for the whole product: no field may
exist here that the visual editor cannot render and edit. So a field is added
when the step that owns its UI is reached, not when the shape of it first
becomes obvious. Where a later step owns the *contents* of a field but this step
owns its *place* — actions, conditions, desired state — the field is present and
its value space is empty, guarded by `_reserved_for` below. That keeps the shape
reserved without committing the editor to anything, and makes the omission
visible in an error message rather than in a comment nobody reads.

Everything validated here is stored verbatim by `DictStorageCollection`, so
every validator must return JSON scalars. That is why clock times, dates and
offsets come out as strings and integers rather than as `time`, `date` and
`timedelta` — a validator that returns a rich object stores one shape and loads
another, and the asymmetry only surfaces after a restart.
"""

from __future__ import annotations

from typing import Any, Final

import voluptuous as vol

from homeassistant.const import CONF_ID
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import VolDictType
from homeassistant.util import dt as dt_util, slugify
from homeassistant.util.ulid import ulid_now

from .const import (
    ANCHOR_CLOCK,
    ANCHOR_ENTITY_TIME,
    ANCHOR_RESOLVER,
    CONF_ACTIONS,
    CONF_ANCHOR,
    CONF_AT,
    CONF_COMPLETION,
    CONF_CONDITION_POLICY,
    CONF_CONDITIONS,
    CONF_COUNT,
    CONF_COUNT_ON,
    CONF_DATE,
    CONF_DATE_WINDOW,
    CONF_DAY_SET_ID,
    CONF_DEADLINE,
    CONF_DESCRIPTION,
    CONF_DOMAIN,
    CONF_DURATION,
    CONF_EDGE,
    CONF_ENABLED,
    CONF_END,
    CONF_ENTER_ACTIONS,
    CONF_EXIT_ACTIONS,
    CONF_FINISHED_WHEN,
    CONF_FROM,
    CONF_GRACE,
    CONF_INTERVAL,
    CONF_KEY,
    CONF_KIND,
    CONF_LATCH,
    CONF_NAME,
    CONF_NTH,
    CONF_OBJECT_ID,
    CONF_OFFSET,
    CONF_ON_EXIT,
    CONF_RECURRENCE,
    CONF_RULES,
    CONF_START_ANCHOR,
    CONF_STATE,
    CONF_THEN,
    CONF_UNTIL,
    CONF_WEEKDAY,
    COUNT_ON_ACTIONS_SUCCEEDED,
    COUNT_ON_CONDITIONS_PASSED,
    COUNT_ON_SCHEDULED,
    EDGE_END,
    EDGE_START,
    END_ANCHOR,
    END_DURATION,
    FINISHED_CONDITION,
    FINISHED_CYCLE,
    FINISHED_DATE,
    FINISHED_NEVER,
    FINISHED_OCCURRENCES,
    FINISHED_ONE_RULE_FIRED,
    ON_EXIT_APPLY,
    ON_EXIT_LEAVE,
    ON_EXIT_RESTORE,
    POLICY_SKIP,
    POLICY_WAIT_UNTIL,
    RECUR_DATES,
    RECUR_DAY_SET,
    RECUR_EVERY_N,
    RECUR_NTH_WEEKDAY,
    RECUR_WEEKDAYS,
    RULE_AT,
    RULE_DURING,
    THEN_ACTION,
    THEN_DELETE,
    THEN_DISABLE,
    THEN_KEEP,
    WEEKDAYS,
)


def _reserved_for(step: str, what: str) -> vol.Validator:
    """Accept only an empty value, naming the build step that will fill it in.

    §15's build order is the reason this exists rather than a permissive schema.
    A field left open "for now" gets populated by a caller before the step that
    owns it has decided what the contents mean, and by then the storage shape is
    load-bearing. Refusing the value keeps the field's *place* reserved — which
    is what costs a migration later — while refusing to guess its contents.
    """

    def validator(value: Any) -> Any:
        if not value:
            return value
        raise vol.Invalid(
            f"{what} is not implemented yet: it lands in build step {step} "
            f"(DESIGN.md §15). The field exists so the storage shape does not "
            f"change when it does."
        )

    return validator


def _whole_number(value: Any) -> int:
    """An integer, rejecting the bool that Python would otherwise let through."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise vol.Invalid(f"expected a whole number, got {value!r}")
    return value


def _signed_seconds(value: Any) -> int:
    """A signed offset or duration, in whole seconds.

    D7 puts no magnitude clamp on an offset, so there is no range check here —
    the ±4h limit users hit today lives in the card and has no reason behind it
    (A.3). The warning past `OFFSET_WARN_SECONDS` belongs to the editor, which
    can say so without refusing the save.

    Seconds rather than a `timedelta`: the validated value is what gets written
    to `.storage`, and a `timedelta` does not survive the round trip as itself.
    """
    return _whole_number(value)


def _clock_time(value: Any) -> str:
    """A local wall-clock time, normalised to `HH:MM:SS`.

    D40 makes this wall-clock rather than an instant, which is why it is stored
    as a naive civil time and never as a UTC offset: the DST rules attach at
    resolution, not at authoring.
    """
    if not isinstance(value, str) or (parsed := dt_util.parse_time(value)) is None:
        raise vol.Invalid(f"expected a time like '17:00' or '17:00:30', got {value!r}")
    return parsed.isoformat()


def _iso_date(value: Any) -> str:
    """A civil date, normalised to `YYYY-MM-DD`."""
    if not isinstance(value, str) or (parsed := dt_util.parse_date(value)) is None:
        raise vol.Invalid(f"expected a date like '2026-10-04', got {value!r}")
    return parsed.isoformat()


def _rule_id(value: Any) -> str:
    """A rule's identifier, minted here when the caller did not supply one.

    D45's `run_now` takes a schedule *or a rule* reference and D57's reverse
    index points at the rule that touches an entity, so a rule needs an identity
    that survives reordering. Rules are nested inside a schedule, so the
    collection's `IDManager` does not reach them.
    """
    if value is None:
        return ulid_now()
    if not isinstance(value, str) or not value:
        raise vol.Invalid(f"expected a rule id, got {value!r}")
    return value


# --- anchors (D6, D7) ------------------------------------------------------

_CLOCK_ANCHOR_SCHEMA: Final = vol.Schema(
    {
        vol.Required(CONF_KIND): ANCHOR_CLOCK,
        vol.Required(CONF_AT): _clock_time,
    }
)

_ENTITY_TIME_ANCHOR_SCHEMA: Final = vol.Schema(
    {
        vol.Required(CONF_KIND): ANCHOR_ENTITY_TIME,
        vol.Required("entity_id"): cv.entity_id,
        vol.Optional(CONF_OFFSET, default=0): _signed_seconds,
    }
)

_RESOLVER_ANCHOR_SCHEMA: Final = vol.Schema(
    {
        vol.Required(CONF_KIND): ANCHOR_RESOLVER,
        # D9 — the domain and key pair is a permanent compatibility surface. It
        # is validated as a slug here and not against the resolver registry:
        # the registry does not exist until step 2, and a stored anchor whose
        # resolver is temporarily absent must load and render as *unresolved*
        # (D17) rather than fail the whole schedule at load.
        vol.Required(CONF_DOMAIN): cv.slug,
        vol.Required(CONF_KEY): cv.slug,
        vol.Optional(CONF_OFFSET, default=0): _signed_seconds,
        vol.Optional(CONF_EDGE, default=EDGE_START): vol.In([EDGE_START, EDGE_END]),
    }
)

ANCHOR_SCHEMA: Final = cv.key_value_schemas(
    CONF_KIND,
    {
        ANCHOR_CLOCK: _CLOCK_ANCHOR_SCHEMA,
        ANCHOR_ENTITY_TIME: _ENTITY_TIME_ANCHOR_SCHEMA,
        ANCHOR_RESOLVER: _RESOLVER_ANCHOR_SCHEMA,
    },
)


# --- conditions, actions, desired state ------------------------------------
# Present, empty, and owned by later steps. See `_reserved_for`.

CONDITIONS_SCHEMA: Final = _reserved_for("4", "conditions (D23-D26)")
ACTIONS_SCHEMA: Final = _reserved_for("5", "actions (D27, D30-D32)")
DESIRED_STATE_SCHEMA: Final = _reserved_for("5", "desired state (D28)")


# --- rules (D2-D5) ---------------------------------------------------------

_CONDITION_POLICY_SCHEMA: Final = cv.key_value_schemas(
    CONF_KIND,
    {
        POLICY_SKIP: vol.Schema({vol.Required(CONF_KIND): POLICY_SKIP}),
        POLICY_WAIT_UNTIL: vol.Schema(
            {
                vol.Required(CONF_KIND): POLICY_WAIT_UNTIL,
                # D26 — the deadline is required, because the whole point of
                # naming this policy is that an unbounded wait is the thing
                # today's `track_conditions` toggle silently does.
                vol.Required(CONF_DEADLINE): vol.All(_signed_seconds, vol.Range(min=1)),
            }
        ),
    },
)

_ON_EXIT_SCHEMA: Final = cv.key_value_schemas(
    CONF_KIND,
    {
        ON_EXIT_APPLY: vol.Schema(
            {
                vol.Required(CONF_KIND): ON_EXIT_APPLY,
                vol.Required(CONF_STATE): DESIRED_STATE_SCHEMA,
            }
        ),
        ON_EXIT_RESTORE: vol.Schema({vol.Required(CONF_KIND): ON_EXIT_RESTORE}),
        ON_EXIT_LEAVE: vol.Schema({vol.Required(CONF_KIND): ON_EXIT_LEAVE}),
    },
)

_END_SCHEMA: Final = cv.key_value_schemas(
    CONF_KIND,
    {
        END_DURATION: vol.Schema(
            {
                vol.Required(CONF_KIND): END_DURATION,
                vol.Required(CONF_DURATION): vol.All(
                    _signed_seconds, vol.Range(min=1)
                ),
            }
        ),
        # D38 resolves this to its first occurrence at or after the resolved
        # start, which is why nothing here checks that the end is after the
        # start: by construction there is no inversion to detect.
        END_ANCHOR: vol.Schema(
            {
                vol.Required(CONF_KIND): END_ANCHOR,
                vol.Required(CONF_ANCHOR): ANCHOR_SCHEMA,
            }
        ),
    },
)

_AT_RULE_SCHEMA: Final = vol.Schema(
    {
        vol.Required(CONF_KIND): RULE_AT,
        vol.Optional(CONF_ID, default=None): _rule_id,
        # D77 — a rule carries its own armed state, because a schedule with one
        # stage disabled must not render as plain *on*. It has no name: D75
        # says no surface renders a bare rule, so there is nothing to name it
        # for.
        vol.Optional(CONF_ENABLED, default=True): cv.boolean,
        vol.Required(CONF_ANCHOR): ANCHOR_SCHEMA,
        vol.Optional(CONF_ACTIONS, default=list): ACTIONS_SCHEMA,
        vol.Optional(CONF_CONDITIONS, default=list): CONDITIONS_SCHEMA,
        vol.Optional(
            CONF_CONDITION_POLICY, default=lambda: {CONF_KIND: POLICY_SKIP}
        ): _CONDITION_POLICY_SCHEMA,
        # D41 — a missed `At` occurrence is logged and not fired unless the rule
        # opts in, so the default is the absence of a window rather than a zero
        # one. Replaying a missed announcement three hours late is worse than
        # skipping it; that asymmetry is the rule, not a tuning parameter.
        vol.Optional(CONF_GRACE, default=None): vol.Any(
            None, vol.All(_signed_seconds, vol.Range(min=1))
        ),
    }
)

_DURING_RULE_SCHEMA: Final = vol.Schema(
    {
        vol.Required(CONF_KIND): RULE_DURING,
        vol.Optional(CONF_ID, default=None): _rule_id,
        vol.Optional(CONF_ENABLED, default=True): cv.boolean,
        vol.Required(CONF_START_ANCHOR): ANCHOR_SCHEMA,
        vol.Required(CONF_END): _END_SCHEMA,
        vol.Optional(CONF_STATE, default=None): vol.Any(None, DESIRED_STATE_SCHEMA),
        vol.Optional(
            CONF_ON_EXIT, default=lambda: {CONF_KIND: ON_EXIT_LEAVE}
        ): _ON_EXIT_SCHEMA,
        # D4 — latch governs predicate exit separately from window exit. False
        # is the reading most people describe first ("while someone is home"),
        # and both readings are legitimate, which is why it is a stored field
        # and not a heuristic.
        vol.Optional(CONF_LATCH, default=False): cv.boolean,
        vol.Optional(CONF_ENTER_ACTIONS, default=list): ACTIONS_SCHEMA,
        vol.Optional(CONF_EXIT_ACTIONS, default=list): ACTIONS_SCHEMA,
        # D25 — for a `During` rule the conditions are part of the activation
        # predicate and are always live, so there is no policy field here to
        # match the `At` rule's. The absence is the decision.
        vol.Optional(CONF_CONDITIONS, default=list): CONDITIONS_SCHEMA,
    }
)

RULE_SCHEMA: Final = cv.key_value_schemas(
    CONF_KIND,
    {RULE_AT: _AT_RULE_SCHEMA, RULE_DURING: _DURING_RULE_SCHEMA},
)


# --- recurrence (D1, D18) --------------------------------------------------
# D1: recurrence selects the dates on which rules *begin*, never occurrences.
# A 23:00 -> 02:00 interval is therefore one occurrence beginning on one date.

_RECURRENCE_SCHEMA: Final = cv.key_value_schemas(
    CONF_KIND,
    {
        RECUR_WEEKDAYS: vol.Schema(
            {
                vol.Required(CONF_KIND): RECUR_WEEKDAYS,
                vol.Required(RECUR_WEEKDAYS): vol.All(
                    cv.ensure_list, [vol.In(WEEKDAYS)], vol.Length(min=1)
                ),
            }
        ),
        RECUR_DATES: vol.Schema(
            {
                vol.Required(CONF_KIND): RECUR_DATES,
                vol.Required(RECUR_DATES): vol.All(
                    cv.ensure_list, [_iso_date], vol.Length(min=1)
                ),
            }
        ),
        RECUR_NTH_WEEKDAY: vol.Schema(
            {
                vol.Required(CONF_KIND): RECUR_NTH_WEEKDAY,
                # -1 is the last such weekday of the month; 0 has no reading.
                vol.Required(CONF_NTH): vol.All(
                    _whole_number, vol.Range(min=-1, max=5), vol.NotIn([0])
                ),
                vol.Required(CONF_WEEKDAY): vol.In(WEEKDAYS),
            }
        ),
        RECUR_EVERY_N: vol.Schema(
            {
                vol.Required(CONF_KIND): RECUR_EVERY_N,
                # Days, counted from `from` — which is required, because "every
                # third day" has no meaning without a day to count from.
                vol.Required(CONF_INTERVAL): vol.All(_whole_number, vol.Range(min=1)),
                vol.Required(CONF_FROM): _iso_date,
            }
        ),
        # D20 — a day set is usable as recurrence and as condition, and D11's
        # two-stage evaluation is what keeps those the same object. The set
        # itself is a separate collection item, built in step 4; here it is a
        # reference, and a dangling one renders as unresolved rather than
        # failing the load.
        RECUR_DAY_SET: vol.Schema(
            {
                vol.Required(CONF_KIND): RECUR_DAY_SET,
                vol.Required(CONF_DAY_SET_ID): cv.string,
            }
        ),
    },
)

_DATE_WINDOW_SCHEMA: Final = vol.Schema(
    {
        vol.Optional(CONF_FROM, default=None): vol.Any(None, _iso_date),
        vol.Optional(CONF_UNTIL, default=None): vol.Any(None, _iso_date),
    }
)


# --- completion (D46) ------------------------------------------------------

_FINISHED_WHEN_SCHEMA: Final = cv.key_value_schemas(
    CONF_KIND,
    {
        # `never` is not one of D46's five values; it is the absence of them.
        # A daily schedule has no completion condition, and expressing that as
        # an optional `completion` block would make the default invisible in
        # the code view. See the note in the module docstring of `storage.py`.
        FINISHED_NEVER: vol.Schema({vol.Required(CONF_KIND): FINISHED_NEVER}),
        FINISHED_ONE_RULE_FIRED: vol.Schema(
            {vol.Required(CONF_KIND): FINISHED_ONE_RULE_FIRED}
        ),
        FINISHED_CYCLE: vol.Schema({vol.Required(CONF_KIND): FINISHED_CYCLE}),
        FINISHED_OCCURRENCES: vol.Schema(
            {
                vol.Required(CONF_KIND): FINISHED_OCCURRENCES,
                vol.Required(CONF_COUNT): vol.All(_whole_number, vol.Range(min=1)),
            }
        ),
        FINISHED_DATE: vol.Schema(
            {
                vol.Required(CONF_KIND): FINISHED_DATE,
                vol.Required(CONF_DATE): _iso_date,
            }
        ),
        FINISHED_CONDITION: vol.Schema(
            {
                vol.Required(CONF_KIND): FINISHED_CONDITION,
                vol.Required(CONF_CONDITIONS): CONDITIONS_SCHEMA,
            }
        ),
    },
)

_THEN_SCHEMA: Final = cv.key_value_schemas(
    CONF_KIND,
    {
        THEN_KEEP: vol.Schema({vol.Required(CONF_KIND): THEN_KEEP}),
        # D47 — neither of these takes effect mid-interval. That is engine
        # behaviour (step 5); the schema only has to be able to say it.
        THEN_DISABLE: vol.Schema({vol.Required(CONF_KIND): THEN_DISABLE}),
        THEN_DELETE: vol.Schema({vol.Required(CONF_KIND): THEN_DELETE}),
        THEN_ACTION: vol.Schema(
            {
                vol.Required(CONF_KIND): THEN_ACTION,
                vol.Required(CONF_ACTIONS): ACTIONS_SCHEMA,
            }
        ),
    },
)

_COMPLETION_SCHEMA: Final = vol.Schema(
    {
        vol.Optional(
            CONF_FINISHED_WHEN, default=lambda: {CONF_KIND: FINISHED_NEVER}
        ): _FINISHED_WHEN_SCHEMA,
        vol.Optional(CONF_THEN, default=lambda: {CONF_KIND: THEN_KEEP}): _THEN_SCHEMA,
        # D46's third axis is the one today's model cannot express at all, and
        # it is what "delete after it triggers" actually turns on.
        vol.Optional(CONF_COUNT_ON, default=COUNT_ON_SCHEDULED): vol.In(
            [
                COUNT_ON_SCHEDULED,
                COUNT_ON_CONDITIONS_PASSED,
                COUNT_ON_ACTIONS_SUCCEEDED,
            ]
        ),
    }
)


# --- the schedule itself (§2) ----------------------------------------------
#
# `labels`, `categories` and `area` are deliberately absent: D56 replaces
# bespoke tags with HA's own registries, which are free for any entity carrying
# a `unique_id`. Storing a copy here would be a second source of truth for the
# thing the platform already owns.

_BODY_VALIDATORS: dict[str, Any] = {
    CONF_DESCRIPTION: cv.string,
    CONF_ENABLED: cv.boolean,
    CONF_RECURRENCE: _RECURRENCE_SCHEMA,
    CONF_DATE_WINDOW: _DATE_WINDOW_SCHEMA,
    CONF_RULES: vol.All(cv.ensure_list, [RULE_SCHEMA]),
    CONF_COMPLETION: _COMPLETION_SCHEMA,
}

# Every optional field has a default, so a stored schedule is always fully
# populated. That is what lets the code view render the whole object instead of
# the subset somebody happened to type, and it is why `completion` comes out as
# an explicit never / keep / scheduled rather than as an absence: a default
# nobody can see is a default nobody can disagree with.
_BODY_DEFAULTS: dict[str, Any] = {
    CONF_DESCRIPTION: "",
    CONF_ENABLED: True,
    CONF_RECURRENCE: lambda: {
        CONF_KIND: RECUR_WEEKDAYS,
        RECUR_WEEKDAYS: list(WEEKDAYS),
    },
    CONF_DATE_WINDOW: dict,
    CONF_RULES: list,
    CONF_COMPLETION: dict,
}


def _body_fields(*, defaults: bool) -> VolDictType:
    """The schedule's own fields, with or without their defaults applied.

    An update carries *only* what changed, so its schema must not fill in
    defaults: `vol.Optional(key, default=…)` would turn "rename this schedule"
    into "rename this schedule and replace its rules with the empty list". The
    switch's arm/disarm write (`{"enabled": False}`) is exactly that shape, so
    this is not a hypothetical.
    """
    fields: VolDictType = {}
    for key, validator in _BODY_VALIDATORS.items():
        marker = (
            vol.Optional(key, default=_BODY_DEFAULTS[key])
            if defaults
            else vol.Optional(key)
        )
        fields[marker] = validator
    return fields


# D66 — `object_id` appears in CREATE and in nothing else.
#
# This is the structural half of the decision. Re-slugging on rename would break
# every automation, script, dashboard card and template referencing the old
# entity_id, and it would do it later and without warning. Keeping the field out
# of UPDATE_FIELDS means an update request cannot carry a new one: the schema is
# PREVENT_EXTRA, so it is rejected before `_update_data` merges anything, and
# the merge therefore has nothing to overwrite. Convention would have been
# enough right up until the first person wrote `{**item, **updates}` somewhere
# else.
CREATE_FIELDS: VolDictType = {
    vol.Required(CONF_NAME): vol.All(cv.string, vol.Length(min=1)),
    vol.Optional(CONF_OBJECT_ID): cv.slug,
    **_body_fields(defaults=True),
}

UPDATE_FIELDS: VolDictType = {
    vol.Optional(CONF_NAME): vol.All(cv.string, vol.Length(min=1)),
    **_body_fields(defaults=False),
}

CREATE_SCHEMA: Final = vol.Schema(CREATE_FIELDS)
UPDATE_SCHEMA: Final = vol.Schema(UPDATE_FIELDS)

# The shape as it sits in `.storage`: what CREATE produced, plus the collection's
# own id. Loading goes through this so a store written by an older build is
# validated on the way in rather than on first use.
STORAGE_SCHEMA: Final = vol.Schema(
    {
        vol.Required(CONF_ID): cv.string,
        vol.Required(CONF_NAME): vol.All(cv.string, vol.Length(min=1)),
        vol.Required(CONF_OBJECT_ID): cv.slug,
        **_body_fields(defaults=True),
    }
)


def suggested_object_id(name: str) -> str:
    """Pre-fill a slug from the name, for the editor to offer and the user to overwrite.

    D66 calls this a suggestion rather than a derivation because the display
    name and the identifier have different audiences: *"Shabbat — sanctuary
    lights & PA"* is a good name and a terrible entity_id. Deriving one from the
    other makes the user compromise whichever they care about less.
    """
    return slugify(name)
