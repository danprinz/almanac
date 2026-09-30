"""Constants for the almanac integration.

Names in here are a compatibility surface in the same sense D9 gives resolver
domains and offering keys: a stored schedule refers to them, so renaming one
without a migration silently changes what a saved schedule means.
"""

from __future__ import annotations

from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "almanac"

# D54 and D55: one switch and one sensor per schedule. Day sets get their own
# binary_sensor under D21, which lands with the day-set collection in step 4.
PLATFORMS: Final[list[Platform]] = [Platform.SENSOR, Platform.SWITCH]

# D37 — the schema version exists from the first commit so that the first
# migration is arithmetic rather than archaeology. It is carried as the store's
# major version: `.storage` is the only place a schedule is persisted, so a
# second version field on each item would be a copy that can disagree.
SCHEMA_VERSION: Final = 1
SCHEMA_VERSION_MINOR: Final = 1

STORAGE_KEY_SCHEDULES: Final = f"{DOMAIN}.schedules"

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
