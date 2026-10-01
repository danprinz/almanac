// The wire form of `almanac/timeline` and `almanac/dry_run`.
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

// --- the two command results -----------------------------------------------

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
