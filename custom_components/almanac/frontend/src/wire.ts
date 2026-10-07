// The wire form of `almanac/timeline`, `almanac/dry_run` and `almanac/resolvers`.
//
// Every interface here is the exact output of an `as_dict()` on the Python side,
// and `tests/test_wire_contract.py` asserts that the two sets of key names have
// not drifted apart. That test is the reason these are written out by hand
// instead of being loosely typed: a renamed key in `engine/plan.py` should break
// something, and the only thing that can break is a file that names the keys.
//
// Two shape rules hold throughout, and they are the backend's doing rather than
// a convention adopted here:
//
// - **Nothing is optional; absent means `null`.** D12 says an occurrence the
//   precise stage dropped is reported with `status = "outside_set"` and not
//   omitted, and the same discipline runs through every dict: a value that has
//   no answer is `null`, never a missing key. So these are `T | null`, never
//   `T?`. The one exception is `PastOccurrence`, which is a merge of two
//   independently optional event payloads — see its own note.
// - **Every instant is an ISO-8601 string with an offset.** The backend refuses
//   a naive one (`ERR_INVALID_FORMAT`), because D64 forbids it a clock of its
//   own and the browser's clock is the one the user reads the screen by.

/** An ISO-8601 datetime carrying an offset. Produced by `.isoformat()`. */
export type Instant = string;

/** An ISO-8601 calendar date, no time part. Produced by `date.isoformat()`. */
export type Day = string;

export interface WireWindow {
  start: Instant;
  end: Instant;
}

/** `resolver/contract.py::UnresolvedReason`, plus its human detail. */
export interface WireProblem {
  reason: UnresolvedReason;
  detail: string;
}

export type UnresolvedReason =
  | "unknown_domain"
  | "unknown_key"
  | "not_implemented"
  | "not_selectable"
  | "no_pairing"
  | "role_not_offered"
  | "unavailable"
  | "timeout"
  | "error";

/** `engine/occurrence.py::OccurrenceStatus`. */
export type OccurrenceStatus =
  | "scheduled"
  | "unresolved"
  | "overlaps_previous"
  | "outside_set";

/** `engine/transition.py::TransitionKind`. */
export type TransitionKind =
  | "fire"
  | "missed"
  | "skipped"
  | "enter"
  | "resume"
  | "exit";

/** `engine/transition.py::ExitCause`. */
export type ExitCause = "window_end" | "disarmed" | "gone" | "conditions";

/** `timeline.py::Coverage`. §5.5's two states; *estimated* has no producer (D119). */
export type Coverage = "known" | "not_computed" | "unknown";

/** `actions.py::ActionStatus`. */
export type ActionStatus = "succeeded" | "dropped" | "failed";

/** `const.py`'s `RESULT_*`, the outcome of one execution. */
export type ExecutionResult = "fired" | "dropped" | "failed" | "nothing";

export type RuleKind = "at" | "during";

// --- the engine's own dicts ------------------------------------------------

/** `engine/occurrence.py::Occurrence.as_dict`. */
export interface WireOccurrence {
  schedule_id: string;
  rule_id: string;
  kind: RuleKind;
  /** The civil day the occurrence belongs to — D11 stage one's answer. */
  start_date: Day;
  status: OccurrenceStatus;
  armed: boolean;
  will_run: boolean;
  start: Instant | null;
  /** Always `null` for an `at` rule; an interval's resolved end otherwise. */
  end: Instant | null;
  problem: WireProblem | null;
}

/**
 * `engine/plan.py::Plan.as_dict`.
 *
 * §12.2's correction is visible here as two fields rather than one.
 * `computed_through` is how far we were willing to enumerate (D44's ninety-day
 * budget); `known_through` is how far the *sources* would commit (D13). They are
 * recombined by the reader, never by the producer — `solid_through` is their
 * `min` and is deliberately not on the wire, because sending it would invite a
 * consumer to read it instead of the two facts it came from.
 */
export interface WirePlan {
  schedule_id: string;
  window: WireWindow;
  occurrences: WireOccurrence[];
  computed_through: Instant;
  known_through: Instant;
  fully_computed: boolean;
  fully_known: boolean;
  problem: WireProblem | null;
}

export interface WireHeldInterval {
  schedule_id: string;
  rule_id: string;
  start_date: Day;
  start: Instant;
  end: Instant;
  entered_at: Instant;
}

export interface WireAtRecord {
  rule_id: string;
  start_date: Day;
  at: Instant;
}

export interface WirePendingAt {
  rule_id: string;
  start_date: Day;
  at: Instant;
  deadline: Instant;
}

export interface WireEngineState {
  held: WireHeldInterval[];
  decided: WireAtRecord[];
  waiting: WirePendingAt[];
  evaluated_through: Instant | null;
}

/** The condition verdict carried by a transition. `blocking` holds D24's labels. */
export interface WireConditionVerdict {
  passed: boolean;
  determined: boolean;
  blocking: string[];
  problem: WireProblem | null;
}

/** `engine/transition.py::Transition.as_dict`. */
export interface WireTransition {
  kind: TransitionKind;
  schedule_id: string;
  rule_id: string;
  start_date: Day;
  at: Instant;
  /** Seconds, fractional. How far behind the instant the engine was. */
  lateness: number;
  occurrence: WireOccurrence | null;
  held: WireHeldInterval | null;
  cause: ExitCause | null;
  conditions: WireConditionVerdict | null;
}

/** `engine/transition.py::Reconciliation.as_dict`. */
export interface WireReconciliation {
  plan: WirePlan;
  transitions: WireTransition[];
  state: WireEngineState;
  next_at: Instant | null;
}

// --- the recorded past ------------------------------------------------------

/** One entry of `execution_payload().actions` / `.state`. */
export interface WireActionResult {
  index: number;
  kind: string;
  target: string;
  status: ActionStatus;
  detail: string | null;
}

/**
 * `timeline.py::PastOccurrence.as_dict` — the one dict on the wire whose keys
 * are genuinely conditional, because it spreads two event payloads that were
 * recorded independently.
 *
 * `announced` is true when the `almanac_occurrence` half is present, `executed`
 * when the `almanac_execution` half is. D116 made `run_now` fire an execution
 * event while D109 keeps it firing no occurrence event, so
 * `announced: false, executed: true` is a manual run and is the case that forces
 * every occurrence-only key below to be optional.
 */
export interface WirePastOccurrence {
  schedule_id: string;
  entity_id: string;
  rule_id: string;
  kind: TransitionKind;
  at: Instant;
  context_id: string;
  recorded_at: Instant;
  announced: boolean;
  executed: boolean;
  // Present iff `announced`.
  name?: string | null;
  lateness?: number;
  blocking?: string[];
  cause?: ExitCause | null;
  // Present iff `executed`.
  result?: ExecutionResult | null;
  actions?: WireActionResult[];
  state?: WireActionResult[];
}

// --- the timeline and the dry run -------------------------------------------

/** `timeline.py::ScheduleTimeline.as_dict` — one lane of D63's view. */
export interface WireScheduleTimeline {
  schedule_id: string;
  name: string | null;
  entity_id: string;
  plan: WirePlan;
  coverage: Coverage;
  /** Oldest first. Empty when the recorder is not loaded — see `recorded`. */
  past: WirePastOccurrence[];
}

/** The whole result of `almanac/timeline`. */
export interface WireTimeline {
  window: WireWindow;
  at: Instant;
  /** False when the recorder is absent, which makes every `past` empty. */
  recorded: boolean;
  truncated: boolean;
  schedules: WireScheduleTimeline[];
}

/** One entry of `almanac/dry_run`'s `schedules`. */
export type WireDryRunSchedule = WireReconciliation & { schedule_id: string };

/** The whole result of `almanac/dry_run`. */
export interface WireDryRun {
  at: Instant;
  live: boolean;
  schedules: WireDryRunSchedule[];
}

// --- the resolver catalogue (D145) -----------------------------------------

/** `resolver/contract.py::Role` — §5.1's two roles. */
export type ResolverRole = "anchor" | "day_set";

/** `resolver/contract.py::HorizonKind` — D13's declared reach. */
export type HorizonKind = "unbounded" | "until" | "next_only";

/**
 * One row of D16's pick-list.
 *
 * **`roles` is how the editor decides whether to offer D124's `edge` choice**,
 * and that is the whole reason the field is on the wire rather than a derived
 * boolean beside it. §5.1 defines the day-set role as a predicate over a span's
 * *interior*, so an offering with no interior — a zman, a sunset, a candle
 * lighting, every one of them an instant — cannot declare it, and an offering
 * that does declare it has a `start` edge and an `end` edge that are different
 * instants. `roles.includes("day_set")` is therefore the test, and it is the
 * declared fact rather than a second shape of it (D133).
 */
export interface WireOffering {
  domain: string;
  key: string;
  display_name: string;
  /** Sorted. Never empty — an offering with no role could not be asked anything. */
  roles: ResolverRole[];
  horizon: HorizonKind;
  /** Non-null only when `horizon === "until"`; nothing shipped declares one yet. */
  horizon_through: Instant | null;
}

/**
 * The whole result of `almanac/resolvers`.
 *
 * `parametric` is the domains with no pick-list — `clock`, whose anchor is typed,
 * and `entity_time`, whose anchor is picked off the entity list. D6 gives each
 * its own anchor kind in the editor, so the editor needs to know they are there
 * without having anything to list for them.
 */
export interface WireResolverCatalogue {
  offerings: WireOffering[];
  parametric: string[];
}

// --- anchor preview (D171) -------------------------------------------------

/**
 * The whole result of `almanac/anchor/preview`.
 *
 * `at` is the instant the request asked about, echoed so a caller that has moved
 * on can drop a slow answer. `unresolved` is non-null only when nothing resolved
 * (D17): the reason, as `Unresolved.__str__` renders it.
 */
export interface WireAnchorPreview {
  at: Instant;
  instants: Instant[];
  unresolved: string | null;
}
