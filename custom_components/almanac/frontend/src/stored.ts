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
// directions. Three shapes are deliberately left opaque and the test declares
// them so — see `StoredRecurrence` below.

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

export interface StoredTarget {
  entity_id: string[];
  device_id: string[];
  area_id: string[];
  floor_id: string[];
  label_id: string[];
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

export type StoredOnExit =
  | { kind: "leave" }
  | { kind: "restore" }
  | { kind: "apply"; state: StoredDesiredState };

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
 * The three shapes the track does not read, left as open records.
 *
 * Writing them out would be writing the editor's types before the editor, and
 * the first one of them to change would change for a reason the track does not
 * participate in. They are named here so that `stored.ts` still describes a
 * whole schedule — a type that silently omitted `recurrence` would let a reader
 * believe a schedule has no recurrence — and `tests/test_wire_contract.py`
 * carries them in its `NOT_CHECKED` set rather than inferring the omission.
 */
export type StoredRecurrence = { kind: string } & Record<string, unknown>;
export type StoredCondition = { kind: string } & Record<string, unknown>;
export type StoredConditionPolicy = { kind: string } & Record<string, unknown>;
export type StoredCompletion = Record<string, unknown>;

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
