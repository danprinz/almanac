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

# D63's view and D64's dry run — the two reads step 9's panel is built on. Both
# take the instant they are about as a field rather than sampling a clock for it,
# which is D64 reaching the API boundary: the backend has exactly one clock reader
# and it is the tick. Core's own read APIs are shaped the same way
# (`history/history_during_period` and `logbook/event_stream` both take
# `start_time` from the frontend).
WS_TIMELINE: Final = f"{DOMAIN}/timeline"
WS_DRY_RUN: Final = f"{DOMAIN}/dry_run"

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
# D124's other edge. A rule says `end: {kind: anchor, anchor: ...}` because an
# end has three shapes there (D38); a day set's span has exactly one, so the
# anchor sits directly on the source and needs a key of its own.
CONF_END_ANCHOR: Final = "end_anchor"
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

# --- hdate offerings (D8, D9, D15, D16 — build step 7) ----------------------
#
# D9 again, and it bites harder here than it does for `sun`: these keys go into
# stored schedules and can never be renamed silently. Three separate name spaces
# feed the list below and they are kept apart deliberately.
#
# 1. **The zmanim keys are hdate's own**, taken from the dict
#    `hdate.zmanim.Zmanim.zmanim` publishes, because that dict is what the
#    lookup is done against. Inventing our own spelling would put a translation
#    table between a stored schedule and the library, which is one more place a
#    version bump can break quietly; `test_hdate_resolver.py` asserts instead
#    that every key here is present in the installed library, so a rename
#    upstream fails a test rather than a schedule.
# 2. **`candle_lighting` and `havdalah` are core's**, matching
#    `jewish_calendar/const.py::YearlyCalendarEventType`. They are *not* members
#    of hdate's zmanim dict — they are computed properties that fold in the
#    candle-lighting and havdalah offsets and the yom-tov chain — so agreeing
#    with core's spelling costs nothing and makes the two integrations legible
#    side by side. D8 is the reason we match the enum *value* and never the
#    `summary`, which core translates at render time (A.1).
# 3. **`issur_melacha` and the five holiday sets are ours**, because neither
#    hdate nor core names the shapes the resolver contract needs: an *interval*
#    (§5.4's Shabbat case) and a *date-granular set* (D10's all-day span).

HDATE_ALOT_HASHACHAR: Final = "alot_hashachar"
HDATE_TALIT_AND_TEFILLIN: Final = "talit_and_tefillin"
HDATE_NETZ_HACHAMA: Final = "netz_hachama"
HDATE_SOF_ZMAN_SHEMA_MGA: Final = "sof_zman_shema_mga"
HDATE_SOF_ZMAN_SHEMA_GRA: Final = "sof_zman_shema_gra"
HDATE_SOF_ZMAN_TFILLA_MGA: Final = "sof_zman_tfilla_mga"
HDATE_SOF_ZMAN_TFILLA_GRA: Final = "sof_zman_tfilla_gra"
HDATE_CHATZOT_HAYOM: Final = "chatzot_hayom"
HDATE_MINCHA_GEDOLA: Final = "mincha_gedola"
HDATE_MINCHA_GEDOLA_30MIN: Final = "mincha_gedola_30min"
HDATE_MINCHA_KETANA: Final = "mincha_ketana"
HDATE_PLAG_HAMINCHA: Final = "plag_hamincha"
HDATE_SHKIA: Final = "shkia"
HDATE_TSET_HAKOHAVIM: Final = "tset_hakohavim"
HDATE_TSET_HAKOHAVIM_TSOM: Final = "tset_hakohavim_tsom"
HDATE_TSET_HAKOHAVIM_SHABBAT: Final = "tset_hakohavim_shabbat"
HDATE_TSET_HAKOHAVIM_RABEINU_TAM: Final = "tset_hakohavim_rabeinu_tam"
HDATE_CHATZOT_HALAYLA: Final = "chatzot_halayla"

# All eighteen, which is the same argument the `sun` resolver makes about dawn,
# dusk and noon (A.3): upstream's whitelist excluded anchors the source already
# computed, and the exclusion is the complaint rather than the design. Four
# spellings of nightfall look like clutter until somebody's community holds by
# Rabbeinu Tam, at which point a whitelist of two is the bug.
HDATE_ZMANIM: Final = (
    HDATE_ALOT_HASHACHAR,
    HDATE_TALIT_AND_TEFILLIN,
    HDATE_NETZ_HACHAMA,
    HDATE_SOF_ZMAN_SHEMA_MGA,
    HDATE_SOF_ZMAN_SHEMA_GRA,
    HDATE_SOF_ZMAN_TFILLA_MGA,
    HDATE_SOF_ZMAN_TFILLA_GRA,
    HDATE_CHATZOT_HAYOM,
    HDATE_MINCHA_GEDOLA,
    HDATE_MINCHA_GEDOLA_30MIN,
    HDATE_MINCHA_KETANA,
    HDATE_PLAG_HAMINCHA,
    HDATE_SHKIA,
    HDATE_TSET_HAKOHAVIM,
    HDATE_TSET_HAKOHAVIM_TSOM,
    HDATE_TSET_HAKOHAVIM_SHABBAT,
    HDATE_TSET_HAKOHAVIM_RABEINU_TAM,
    HDATE_CHATZOT_HALAYLA,
)

# The two transition instants, spelled as core spells them.
HDATE_CANDLE_LIGHTING: Final = "candle_lighting"
HDATE_HAVDALAH: Final = "havdalah"

# The interval §5.4 is written about: from a candle lighting to the havdalah
# that closes the same stretch, chag chains included. Named for what it *is*
# rather than "shabbat", because a Thursday-to-Saturday Rosh Hashanah stretch is
# one span of this offering and is not Shabbat for two of its three days.
HDATE_ISSUR_MELACHA: Final = "issur_melacha"

# The date-granular sets (D10's `date`-typed span). Keyed by hdate's own
# `HolidayTypes` member names, lowercased, except `festival` — a union of three
# of them, and therefore a name that has to be chosen here or nowhere.
HDATE_YOM_TOV: Final = "yom_tov"
HDATE_CHOL_HAMOED: Final = "chol_hamoed"
HDATE_FESTIVAL: Final = "festival"
HDATE_FAST_DAY: Final = "fast_day"
HDATE_ROSH_CHODESH: Final = "rosh_chodesh"

# D15's tuning. Core exposes both as config-flow options
# (`candle_lighting_minutes_before_sunset`, `havdalah_minutes_after_sunset`) and
# these are the same defaults its `const.py` carries, which are in turn hdate's
# own. **Provisional**: almanac has no options flow (D65 — single config entry,
# nothing per-instance), so they are constructor arguments on the resolver with
# these defaults and no user surface yet. Whether they become almanac options or
# are borrowed from a loaded `jewish_calendar` entry is an owner ruling; D43's
# invalidation vocabulary has no way to say "another integration's options
# changed", which is why borrowing was not done silently.
HDATE_DEFAULT_CANDLE_LIGHTING_OFFSET: Final = 18
HDATE_DEFAULT_HAVDALAH_OFFSET: Final = 0

# How far either side of a window the resolver has to look to find a span that
# *overlaps* it (§5.3's `Span.overlaps`: an interval already running at
# `window.start` is part of the answer). Three numbers, because the three shapes
# have genuinely different reaches and each is stated rather than guessed:
#
# - a zmanim span is zero-length but is computed *from a civil date*, and the
#   result can land on the next local day — `chatzot_halayla` for 6 October in
#   New York is 7 October 00:44 — so one day either side is both enough and
#   necessary;
# - an issur-melacha stretch reaches at most Wednesday evening to Saturday night
#   (a two-day chag running into Shabbat), which is four civil days; six is that
#   bound with slack;
# - a festival stretch reaches erev Sukkot to Simchat Torah, ten civil days in
#   the diaspora; fourteen is that bound with slack.
#
# Truncating instead would not lose a span, it would *shorten* one — and a
# shortened span is worse than a missing one, because `edge: end` would then
# resolve to the window's own edge and render as fact.
HDATE_ZMAN_PAD_DAYS: Final = 1
HDATE_MELACHA_PAD_DAYS: Final = 6
HDATE_HOLIDAY_PAD_DAYS: Final = 14

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
# D124 -- layer 2 of §6.1: the span the user builds, rather than one a
# resolver happened to publish.
SOURCE_ANCHOR_SPAN: Final = "anchor_span"
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

# --- observability (D48-D52) -----------------------------------------------

# D48 puts the whole audit trail in the recorder, which makes these names a
# compatibility surface in the strongest sense on this page: a history query, a
# logbook filter and somebody else's automation trigger all name the event type
# and its payload keys, and none of them can be migrated by us. Renaming one is
# the same class of change as renaming a stored schedule field above.
#
# Two event types for one occurrence, which is a deviation from D49's "one rich
# event per occurrence" and is argued — with the evidence from the installed
# 2026.9.4 logbook — in `events.py`. The short version: D50's causation chain
# requires the event to be fired *before* the side effects, and D49's per-action
# results do not exist until after them.
EVENT_OCCURRENCE: Final = f"{DOMAIN}_occurrence"
EVENT_EXECUTION: Final = f"{DOMAIN}_execution"

ATTR_ACTIONS: Final = "actions"
ATTR_AT: Final = "at"
ATTR_BLOCKING: Final = "blocking"
ATTR_CAUSE: Final = "cause"
ATTR_KIND: Final = "kind"
ATTR_LATENESS: Final = "lateness"
ATTR_RESULT: Final = "result"
ATTR_RULE_ID: Final = "rule_id"
ATTR_SCHEDULE_ID: Final = "schedule_id"

# D49's `result` — the execution event's one-word summary of everything the
# occurrence did. Three of the four values are D49's own; `nothing` is the case
# D49 does not name and `ExecutionReport.attempted` already distinguishes: a rule
# with no actions and no desired state, which is legal and is how a schedule that
# exists only to be seen on the timeline is written. D49's fourth value,
# `skipped`, is not here because a skipped occurrence executes nothing and so
# never reaches an execution event — it is the *occurrence* event's `kind`.
RESULT_FIRED: Final = "fired"
RESULT_DROPPED: Final = "dropped"
RESULT_FAILED: Final = "failed"
RESULT_NOTHING: Final = "nothing"

# D52 — how many executions the display cache keeps per schedule. Small on
# purpose: this is an entity attribute, so every entry is re-serialised to every
# connected frontend on every change, and the durable answer is the recorder's
# (D48). **Provisional** — D52 says "last-N" without fixing N.
EXECUTION_CACHE_SIZE: Final = 5

ATTR_RECENT_EXECUTIONS: Final = "recent_executions"

# --- entity naming ---------------------------------------------------------

# D54 — the switch is the entity a schedule *is*, which makes its domain the one
# name every other part of the integration uses for a schedule: `storage`
# reserves the entity_id, `entity.py` builds it, and D49's event carries it so
# that the logbook can attach the row to the schedule rather than to nothing.
SCHEDULE_PLATFORM: Final = "switch"
SENSOR_PLATFORM: Final = "sensor"

# D55's sensor needs an object_id of its own, and it is derived from the
# schedule's stored `object_id` rather than from its name, so D66 holds for it
# too: fixed once, at creation, and never recomputed.
SENSOR_OBJECT_ID_SUFFIX: Final = "_next_trigger"
