// The stored form of a schedule, as `almanac/schedule/list` returns it.
//
// This is a different boundary from `wire.ts` and is here for a reason D73
// forced. The track's axis is the *anchor sequence*, and an occurrence carries
// no anchor: `Occurrence.as_dict()` emits the rule it came from and the instant
// it resolved to, which is everything a list of chips needs and nothing a rail
// needs. The anchor lives in the stored rule. So the track is drawn from two
// reads joined on `rule_id` — the plan for *when*, the stored schedule for
// *what it is relative to* — rather than from a widened timeline payload. D64 is
// why that is the right way round: the engine already resolved the instants with
// a threaded `now`, and asking it to also repeat the anchor would put the same
// fact on the wire twice.
//
// These shapes are `schema.py` and not an abbreviation of it.
// `tests/test_wire_contract.py` compares the key names here against the
// voluptuous schemas that produce them, interface by interface, for the same
// reason it does the `as_dict()` sweep: a renamed storage key is silent in both
// directions. One shape is deliberately left opaque and the test declares it so
// — see `StoredRecurrence` below.

import type { Day } from "./wire";

export type AnchorKind = "clock" | "entity_time" | "resolver";

/** D40 — a wall clock, stored naive as `HH:MM:SS`, never as an instant. */
export interface StoredClockAnchor {
  kind: "clock";
  at: string;
}

export interface StoredEntityTimeAnchor {
  kind: "entity_time";
  entity_id: string;
  /** Seconds, signed. D7 — the offset is arithmetic on the anchor's event. */
  offset: number;
}

export interface StoredResolverAnchor {
  kind: "resolver";
  domain: string;
  key: string;
  offset: number;
  edge: "start" | "end";
}

export type StoredAnchor =
  | StoredClockAnchor
  | StoredEntityTimeAnchor
  | StoredResolverAnchor;

/** An interval that ends a fixed time after it started. Not an anchor (D73). */
export interface StoredDurationEnd {
  kind: "duration";
  duration: number;
}

export interface StoredAnchorEnd {
  kind: "anchor";
  anchor: StoredAnchor;
}

export type StoredEnd = StoredDurationEnd | StoredAnchorEnd;

export interface StoredServiceAction {
  kind: "service";
  service: string;
  target: StoredTarget | null;
  data: Record<string, unknown>;
}

export interface StoredScriptAction {
  kind: "script";
  /** The script *entity*, so D31's pre-flight and D57's index both have it. */
  script: string;
  fields: Record<string, unknown>;
  wait: boolean;
  timeout: number | null;
}

export type StoredAction = StoredServiceAction | StoredScriptAction;

/**
 * A service call's target: the five selectors, each of which may be absent.
 *
 * The `?:` is the exception to the no-optionals convention `wire.ts` sets out,
 * and it is `_TARGET_SCHEMA` that makes it one. Every selector there is a
 * `vol.Optional` with **no default**, so a stored target holds exactly the
 * selectors that were written to it: `{"entity_id": ["light.hall"]}` has no
 * `device_id` key at all. That is unlike the rest of this file, where the
 * schemas fill an absent field with `None` and the key is always present.
 *
 * Step 9e is what made the difference matter. The track never looked inside a
 * target, so five required keys were a harmless over-claim; the action builder
 * reads each selector to draw a field, and under `strictNullChecks` the
 * over-claim is what stops it from being asked whether the key is there.
 *
 * `_target` refuses a target whose every selector is empty — `{}` beside a
 * `data` without `entity_id` is a call at every entity the service accepts —
 * while `target: null` stays legal, for the services that genuinely have none.
 * `withTargetIds` is where those two facts are kept straight.
 */
export interface StoredTarget {
  entity_id?: string[];
  device_id?: string[];
  area_id?: string[];
  floor_id?: string[];
  label_id?: string[];
}

export interface StoredDesiredEntity {
  entity_id: string;
  state: string | null;
  attributes: Record<string, unknown>;
}

export interface StoredDesiredState {
  entities: StoredDesiredEntity[];
  override: StoredAction[];
}

/**
 * D3's third exit. An interface rather than an inline arm because it carries a
 * key: `state` could be renamed in `schema.py` and nothing would notice, which
 * is the whole reason `tests/test_wire_contract.py` exists. The other two arms
 * stay inline — they are `kind` and nothing else, so an interface apiece would
 * be two interfaces carrying one key.
 */
export interface StoredApplyOnExit {
  kind: "apply";
  state: StoredDesiredState;
}

export type StoredOnExit =
  | { kind: "leave" }
  | { kind: "restore" }
  | StoredApplyOnExit;

// --- conditions, the policy and completion (D23, D26, D46) -----------------
//
// Written out at step 9e, where the condition and completion builders needed
// them. Until then they were open records, which was the honest shape for a
// track that never looked inside one; an editor that does look has to agree with
// `schema.py` key by key, so they joined the sweep at the same time.

/**
 * One JSON scalar, with the type left alone.
 *
 * `number | string | boolean` and deliberately not `string`: `schema.py`'s
 * `_scalar` keeps the type the user typed, because coercing a threshold of `20`
 * to `"20"` turns a numeric comparison into a string one in which `"9" > "20"`.
 * The editor's value field therefore has to decide a type too.
 */
export type StoredScalar = string | number | boolean;

export interface StoredConstantOperand {
  kind: "constant";
  /** A list only for `in` / `not_in`, which `_comparison` enforces. */
  value: StoredScalar | StoredScalar[];
}

export interface StoredEntityOperand {
  kind: "entity";
  entity_id: string;
  /** `null` is the entity's state, which is not an attribute called `state`. */
  attribute: string | null;
  /** Signed. Its unit is decided at evaluation, not here — see `schema.py`. */
  offset: number;
}

export type StoredOperand = StoredConstantOperand | StoredEntityOperand;

/** `const.py::COMPARISON_OPERATORS`, in its order. */
export type StoredComparisonOperator =
  | "eq"
  | "ne"
  | "gt"
  | "gte"
  | "lt"
  | "lte"
  | "in"
  | "not_in";

export interface StoredComparisonCondition {
  kind: "comparison";
  entity_id: string;
  attribute: string | null;
  operator: StoredComparisonOperator;
  value: StoredOperand;
  /** D23's "state held for a duration", in whole seconds. */
  for: number | null;
  label: string | null;
}

export interface StoredDaySetCondition {
  kind: "day_set";
  day_set_id: string;
  /** "…unless it is a holiday", without a second level of nesting. */
  negate: boolean;
  label: string | null;
}

/** §7.1's one level of nesting: a group's members are leaves, never groups. */
export type StoredLeafCondition =
  | StoredComparisonCondition
  | StoredDaySetCondition;

export interface StoredGroupCondition {
  kind: "group";
  operator: "and" | "or";
  conditions: StoredLeafCondition[];
  label: string | null;
}

export type StoredCondition = StoredLeafCondition | StoredGroupCondition;

/** D26 — the deadline is required, so the arm is an interface. */
export interface StoredWaitUntilPolicy {
  kind: "wait_until";
  deadline: number;
}

export type StoredConditionPolicy = { kind: "skip" } | StoredWaitUntilPolicy;

export interface StoredFinishedOccurrences {
  kind: "occurrences";
  count: number;
}

export interface StoredFinishedDate {
  kind: "date";
  date: Day;
}

export interface StoredFinishedCondition {
  kind: "condition";
  conditions: StoredCondition[];
}

/** D46's first axis, with D83's `never` as the default and commonest value. */
export type StoredFinishedWhen =
  | { kind: "never" }
  | { kind: "one_rule_fired" }
  | { kind: "cycle" }
  | StoredFinishedOccurrences
  | StoredFinishedDate
  | StoredFinishedCondition;

export interface StoredThenAction {
  kind: "action";
  actions: StoredAction[];
}

export type StoredThen =
  | { kind: "keep" }
  | { kind: "disable" }
  | { kind: "delete" }
  | StoredThenAction;

/** D46's third axis — what a completion *counts*. */
export type StoredCountOn =
  | "scheduled"
  | "conditions_passed"
  | "actions_succeeded";

export interface StoredCompletion {
  finished_when: StoredFinishedWhen;
  then: StoredThen;
  count_on: StoredCountOn;
}

export interface StoredAtRule {
  kind: "at";
  id: string;
  /** D77 — the rule's own armed state. A disabled stage is still drawn. */
  enabled: boolean;
  anchor: StoredAnchor;
  actions: StoredAction[];
  conditions: StoredCondition[];
  condition_policy: StoredConditionPolicy;
  grace: number | null;
}

export interface StoredDuringRule {
  kind: "during";
  id: string;
  enabled: boolean;
  start_anchor: StoredAnchor;
  end: StoredEnd;
  state: StoredDesiredState | null;
  on_exit: StoredOnExit;
  latch: boolean;
  enter_actions: StoredAction[];
  exit_actions: StoredAction[];
  conditions: StoredCondition[];
}

export type StoredRule = StoredAtRule | StoredDuringRule;

export interface StoredSchedule {
  id: string;
  name: string;
  object_id: string;
  description: string;
  enabled: boolean;
  recurrence: StoredRecurrence;
  date_window: StoredDateWindow;
  rules: StoredRule[];
  completion: StoredCompletion;
}

/**
 * The one shape still left as an open record, and why it stays one.
 *
 * Three of the original four were written out at step 9e, when the condition and
 * completion builders had to agree with `schema.py` key by key. Recurrence did
 * not join them, because D151 means the editor never looks inside four of its
 * five arms: `dates`, `nth_weekday` and `every_n` are read for their `kind`
 * alone and rendered read-only, and the two it does build it writes whole. A
 * type spelling out keys nothing reads would be a claim the editor does not
 * depend on, which is the kind of restatement D143 warns about.
 * `tests/test_wire_contract.py` carries it in `NOT_CHECKED_OPAQUE` rather than
 * inferring the omission.
 */
export type StoredRecurrence = { kind: string } & Record<string, unknown>;

/**
 * D19's date window. Both keys are always present: `date_window` defaults to
 * `{}` and the window schema then fills each edge with `null`, so "no window"
 * arrives as two nulls rather than as an empty object. Nothing here is `?:`,
 * for the reason `wire.ts`'s header gives.
 */
export interface StoredDateWindow {
  from: Day | null;
  until: Day | null;
}
