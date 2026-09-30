"""Constants for the almanac integration.

Names in here are a compatibility surface in the same sense D9 gives resolver
domains and offering keys: a stored schedule refers to them, so renaming one
without a migration silently changes what a saved schedule means.
"""

from __future__ import annotations

from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "almanac"

# D54 and D55: one switch and one sensor per schedule. D21: one binary_sensor
# per day set, whose state is whether the set covers *now*.
PLATFORMS: Final[list[Platform]] = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.SWITCH,
]

# D37 — the schema version exists from the first commit so that the first
# migration is arithmetic rather than archaeology. It is carried as the store's
# major version: `.storage` is the only place a schedule is persisted, so a
# second version field on each item would be a copy that can disagree.
SCHEMA_VERSION: Final = 1
SCHEMA_VERSION_MINOR: Final = 1

STORAGE_KEY_SCHEDULES: Final = f"{DOMAIN}.schedules"

# D18 — a day set is a first-class object, so it gets its own store rather than
# living inside the schedules that reference it. That is the whole of D18: a set
# edited in one place changes every schedule using it, which is impossible if
# each schedule carries its own copy. The schema version is shared with the
# schedule store because it versions *the integration's* storage shape; two
# independently drifting numbers would make the first cross-store migration
# guesswork of exactly the kind D37 exists to prevent.
STORAGE_KEY_DAY_SETS: Final = f"{DOMAIN}.day_sets"

# D35 — definition and runtime state are separate stores, not separate keys in
# one object. Counters and `last_fired` are written by the engine on a hot path;
# the definition is written by a human in an editor. Sharing a store means an
# edit saved at the wrong moment resets a completion counter, and there is no
# way to make that race visible to either writer.
STORAGE_KEY_RUNTIME: Final = f"{DOMAIN}.runtime"
RUNTIME_VERSION: Final = 1

# D65 — single instance, so there is exactly one entry and its version is only
# ever bumped by a migration of the entry's own (currently empty) data.
CONFIG_ENTRY_VERSION: Final = 1

# Websocket CRUD (D33). The prefix is part of the frontend's contract with the
# integration, so it is a constant rather than a literal at the call site.
WS_PREFIX_SCHEDULE: Final = f"{DOMAIN}/schedule"
WS_PREFIX_DAY_SET: Final = f"{DOMAIN}/day_set"

# --- schedule fields -------------------------------------------------------

CONF_COMPLETION: Final = "completion"
CONF_DATE_WINDOW: Final = "date_window"
CONF_DESCRIPTION: Final = "description"
CONF_ENABLED: Final = "enabled"
CONF_NAME: Final = "name"
CONF_OBJECT_ID: Final = "object_id"
CONF_RECURRENCE: Final = "recurrence"
CONF_RULES: Final = "rules"

CONF_FROM: Final = "from"
CONF_UNTIL: Final = "until"

# --- rule fields -----------------------------------------------------------

CONF_ACTIONS: Final = "actions"
CONF_ANCHOR: Final = "anchor"
CONF_CONDITIONS: Final = "conditions"
CONF_CONDITION_POLICY: Final = "condition_policy"
CONF_END: Final = "end"
CONF_ENTER_ACTIONS: Final = "enter_actions"
CONF_EXIT_ACTIONS: Final = "exit_actions"
CONF_GRACE: Final = "grace"
CONF_KIND: Final = "kind"
CONF_LATCH: Final = "latch"
CONF_ON_EXIT: Final = "on_exit"
CONF_START_ANCHOR: Final = "start_anchor"
CONF_STATE: Final = "state"

RULE_AT: Final = "at"
RULE_DURING: Final = "during"

# D26 — the two readings of "the conditions were false at the instant" are named
# by the user rather than inferred from a toggle. See §7.3.
POLICY_SKIP: Final = "skip"
POLICY_WAIT_UNTIL: Final = "wait_until"
CONF_DEADLINE: Final = "deadline"

# D3 — `on_exit` is three-valued and explicit.
ON_EXIT_APPLY: Final = "apply"
ON_EXIT_RESTORE: Final = "restore"
ON_EXIT_LEAVE: Final = "leave"

# D2 — a `During` rule's end is a duration or a second anchor. D38 pairs an end
# anchor to its first occurrence at or after the resolved start, which is what
# makes the second form work across midnight.
END_DURATION: Final = "duration"
END_ANCHOR: Final = "anchor"
CONF_DURATION: Final = "duration"

# --- anchor fields (D6) ----------------------------------------------------

ANCHOR_CLOCK: Final = "clock"
ANCHOR_ENTITY_TIME: Final = "entity_time"
ANCHOR_RESOLVER: Final = "resolver"

CONF_AT: Final = "at"
CONF_DOMAIN: Final = "domain"
CONF_EDGE: Final = "edge"
CONF_ENTITY_ID: Final = "entity_id"
CONF_KEY: Final = "key"
CONF_OFFSET: Final = "offset"

EDGE_START: Final = "start"
EDGE_END: Final = "end"

# D7 — offsets have no magnitude clamp; past this the editor warns and still
# saves. The card's `MAX_OFFFSET_HOURS = 4` had nothing behind it (A.3), so
# there is nothing to preserve — but an offset of days is more often a typo than
# an intent, and saying so costs nothing.
OFFSET_WARN_SECONDS: Final = 24 * 60 * 60

# --- resolvers (D9, D13, D14, D16) -----------------------------------------
#
# D9 makes a resolver domain and an offering key a permanent compatibility
# surface: a stored schedule addresses them by these exact strings, so a rename
# needs an alias table and never a silent substitution.
#
# `clock` and `entity_time` share their names with the anchor kinds above, and
# that is the point rather than a coincidence — one concept, one spelling. They
# are *parametric*: their key is a wall-clock time and an entity_id
# respectively, so they publish no pick-list and D16's "users pick from a list"
# does not apply to them. A `kind: resolver` anchor can therefore not address
# them, which keeps the two spellings from diverging.

RESOLVER_CLOCK: Final = ANCHOR_CLOCK
RESOLVER_ENTITY_TIME: Final = ANCHOR_ENTITY_TIME
RESOLVER_SUN: Final = "sun"

# Named here, implemented in build step 7. D15 fixes the domain now so that no
# schedule is ever written against a name we later have to alias away.
RESOLVER_HDATE: Final = "hdate"

# The six astral events `helpers/sun.py::get_astral_event_date` computes, which
# are also the six `sensor.sun_next_*` entities (A.2). The keys are astral's own
# function names because that helper dispatches with `getattr(astral.sun, event)`
# — which is a second reason D16's fixed set is not merely a UI convenience.
#
# `dawn`, `dusk` and `noon` are here deliberately: today's scheduler whitelists
# only sunrise and sunset despite `sun.sun` publishing all six (A.3), and that
# gap is one of the things this project exists to close.
SUN_DAWN: Final = "dawn"
SUN_SUNRISE: Final = "sunrise"
SUN_NOON: Final = "noon"
SUN_SUNSET: Final = "sunset"
SUN_DUSK: Final = "dusk"
SUN_MIDNIGHT: Final = "midnight"

# D17 — timeouts live in the contract, not in each implementation. Nothing
# in-tree goes near this: `sun` is arithmetic and `entity_time` is a state read.
# It is here for the resolver somebody writes later, so that the first slow one
# degrades to *unresolved* rather than stalling every unrelated schedule.
RESOLVER_TIMEOUT: Final = 5

# --- recurrence fields (D1, D18) -------------------------------------------

RECUR_DAY_SET: Final = "day_set"
RECUR_DATES: Final = "dates"
RECUR_EVERY_N: Final = "every_n"
RECUR_NTH_WEEKDAY: Final = "nth_weekday"
RECUR_WEEKDAYS: Final = "weekdays"

CONF_DAY_SET_ID: Final = "day_set_id"
CONF_INTERVAL: Final = "interval"
CONF_NTH: Final = "nth"
CONF_WEEKDAY: Final = "weekday"

WEEKDAYS: Final = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# --- day sets (D18-D22) ----------------------------------------------------
#
# D18's source list is `Weekdays | Dates | NthWeekday | EveryN |
# ResolverOffering | Composition`, and the first four are spelled with the same
# strings the recurrence uses. That is not a saving of four constants — it is
# what makes D20 true. A day set usable "as recurrence and as condition" has to
# mean the same thing in both places, and two vocabularies for "every Tuesday"
# is how they drift apart.

SOURCE_WEEKDAYS: Final = RECUR_WEEKDAYS
SOURCE_DATES: Final = RECUR_DATES
SOURCE_NTH_WEEKDAY: Final = RECUR_NTH_WEEKDAY
SOURCE_EVERY_N: Final = RECUR_EVERY_N
SOURCE_OFFERING: Final = "offering"
SOURCE_COMPOSITION: Final = "composition"

CONF_MEMBERS: Final = "members"
CONF_OPERATOR: Final = "operator"
CONF_OWNER: Final = "owner"
CONF_SOURCE: Final = "source"

# D19 — one level of union / intersect / minus, and no nesting. The three
# operators are the whole of the set algebra, deliberately: a deeply composed
# set is not enumerable in bounded time and cannot be explained in the one-line
# summary trigger honesty depends on.
COMPOSE_UNION: Final = "union"
COMPOSE_INTERSECT: Final = "intersect"
COMPOSE_MINUS: Final = "minus"

# --- conditions (D23-D26) --------------------------------------------------
#
# D23 — structured only, no templates, ever. These are the three shapes the
# editor has to be able to render, which under D34 is the same statement as
# "these are the three shapes that may be stored".

CONDITION_COMPARISON: Final = "comparison"
CONDITION_DAY_SET: Final = "day_set"
CONDITION_GROUP: Final = "group"

CONF_ATTRIBUTE: Final = "attribute"
CONF_FOR: Final = "for"
CONF_LABEL: Final = "label"
CONF_NEGATE: Final = "negate"
CONF_VALUE: Final = "value"

# The right-hand side of a comparison. D23's table gives two forms — a constant
# and another entity with an optional offset — and they are a tagged union
# rather than "a scalar, unless it looks like an entity_id", because guessing
# from the shape of a string is how `sensor.foo` becomes a comparison against
# the literal text.
OPERAND_CONSTANT: Final = "constant"
OPERAND_ENTITY: Final = "entity"

CMP_EQUAL: Final = "eq"
CMP_NOT_EQUAL: Final = "ne"
CMP_ABOVE: Final = "gt"
CMP_AT_LEAST: Final = "gte"
CMP_BELOW: Final = "lt"
CMP_AT_MOST: Final = "lte"
CMP_IN: Final = "in"
CMP_NOT_IN: Final = "not_in"

# `in` and `not_in` take a list; everything else takes a scalar. Kept as a
# separate tuple so the schema can enforce that relation rather than leaving the
# evaluator to decide what `gt: [1, 2]` means.
COMPARISON_OPERATORS: Final = (
    CMP_EQUAL,
    CMP_NOT_EQUAL,
    CMP_ABOVE,
    CMP_AT_LEAST,
    CMP_BELOW,
    CMP_AT_MOST,
    CMP_IN,
    CMP_NOT_IN,
)
LIST_OPERATORS: Final = (CMP_IN, CMP_NOT_IN)

# §7.1 — and/or groups, one level of nesting. The rule's `conditions` list is
# itself an AND, so a group is only ever needed for the OR; `and` is offered
# anyway because a group the user typed and then changed their mind about should
# not have to be rebuilt.
GROUP_AND: Final = "and"
GROUP_OR: Final = "or"

# --- action fields (D27, D28, D30-D32) -------------------------------------

# D27 — two kinds only, and which kinds a rule may carry is decided by the rule
# shape, not by the action: an `At` rule's `actions` and a `During` rule's
# `enter_actions`/`exit_actions` take both; a `During` rule's `state` is desired
# state and is not an action list at all. Keeping the action vocabulary this
# small is what makes D34 parity achievable — there is no action the editor
# cannot render.
ACTION_SERVICE: Final = "service"
ACTION_SCRIPT: Final = "script"

CONF_SERVICE: Final = "service"
CONF_DATA: Final = "data"
CONF_TARGET: Final = "target"
CONF_SCRIPT: Final = "script"
CONF_FIELDS: Final = "fields"

# D30 — a script runs non-blocking by default, because a script containing a
# `delay` would otherwise hold the engine inside one rule for the length of that
# delay. Waiting is opt-in and *requires* a timeout: "wait forever" is the one
# setting that turns a slow script into a stuck scheduler, so the schema refuses
# it rather than offering it with a warning.
CONF_WAIT: Final = "wait"
CONF_TIMEOUT: Final = "timeout"

# The five target selectors core's service calls accept. They are spelled out
# here rather than deferred to core's own target schema so that the storage
# format is ours and a core change cannot silently widen what a stored schedule
# may contain — D34 again: the editor has to be able to render every one.
CONF_DEVICE_ID: Final = "device_id"
CONF_AREA_ID: Final = "area_id"
CONF_FLOOR_ID: Final = "floor_id"
CONF_LABEL_ID: Final = "label_id"

TARGET_SELECTORS: Final = (
    CONF_ENTITY_ID,
    CONF_DEVICE_ID,
    CONF_AREA_ID,
    CONF_FLOOR_ID,
    CONF_LABEL_ID,
)

# D28 — desired state is a list of entities with a state and/or attributes, and
# it is applied through `async_reproduce_state`. `override` is the escape hatch
# the decision insists on: for climate, core's reproduce_state issues up to seven
# sequential blocking calls and does not skip attributes that already match
# (A.5), so a rule may name the service call to use instead. The override lives
# *inside* the desired-state block rather than on the rule so that `on_exit:
# apply`, which carries a second desired state, gets the same escape hatch.
CONF_ENTITIES: Final = "entities"
CONF_ATTRIBUTES: Final = "attributes"
CONF_OVERRIDE: Final = "override"

# The script integration's domain. Named here because D31's pre-flight reads a
# `script.*` entity's own attributes, and `homeassistant.components.script` must
# not become an import dependency of this integration for the sake of one string.
SCRIPT_DOMAIN: Final = "script"

# --- the engine (D38, D39, D44) --------------------------------------------

# D44 — our compute budget, which §10.6 is careful to distinguish from a
# resolver's own horizon (D13): one is how far we are willing to compute, the
# other is how far the source can honestly see. The figure is "a starting point
# to be revisited against real enumeration cost, not a measured one", so it lives
# here as one named number rather than spread through the enumerator, and the
# plan reports `computed_through` so that beyond it the timeline can say *not
# computed* instead of guessing.
ENUMERATION_HORIZON_DAYS: Final = 90

# --- completion fields (D46) -----------------------------------------------

CONF_COUNT: Final = "count"
CONF_COUNT_ON: Final = "count_on"
CONF_DATE: Final = "date"
CONF_FINISHED_WHEN: Final = "finished_when"
CONF_THEN: Final = "then"

FINISHED_NEVER: Final = "never"
FINISHED_ONE_RULE_FIRED: Final = "one_rule_fired"
FINISHED_CYCLE: Final = "cycle"
FINISHED_OCCURRENCES: Final = "occurrences"
FINISHED_DATE: Final = "date"
FINISHED_CONDITION: Final = "condition"

THEN_KEEP: Final = "keep"
THEN_DISABLE: Final = "disable"
THEN_DELETE: Final = "delete"
THEN_ACTION: Final = "action"

COUNT_ON_SCHEDULED: Final = "scheduled"
COUNT_ON_CONDITIONS_PASSED: Final = "conditions_passed"
COUNT_ON_ACTIONS_SUCCEEDED: Final = "actions_succeeded"

# --- entity naming ---------------------------------------------------------

# D55's sensor needs an object_id of its own, and it is derived from the
# schedule's stored `object_id` rather than from its name, so D66 holds for it
# too: fixed once, at creation, and never recomputed.
SENSOR_OBJECT_ID_SUFFIX: Final = "_next_trigger"
