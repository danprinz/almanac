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
