// Everything the surfaces read off a plan, derived once and in one place.
//
// These are the questions D63, D74, D77 and D119 ask of a `WirePlan`, and the
// reason they live together is that each of them is easy to answer *almost*
// right. "The next occurrence" is not the first in the list if the first is
// `outside_set`. "Is this schedule on" is not one boolean, because D77 says
// half-armed must never render as plain on. "Do we know what happens on Friday"
// is two facts, not one, because §12.2 says so.

import type {
  Coverage,
  WireOccurrence,
  WirePlan,
  WireScheduleTimeline,
} from "./wire";

/**
 * Occurrences that will actually happen, soonest first.
 *
 * `will_run` is the backend's own conjunction of `armed` and
 * `status === "scheduled"` (D77 keeps the two facts separate on the wire and
 * recombines them here), and an unresolved occurrence has no `start` to sort by.
 */
export const willRun = (plan: WirePlan): WireOccurrence[] =>
  plan.occurrences
    .filter((occurrence) => occurrence.will_run && occurrence.start !== null)
    .sort((a, b) => (a.start! < b.start! ? -1 : a.start! > b.start! ? 1 : 0));

/** The soonest occurrence at or after `at` that will run, or null. */
export const nextOccurrence = (
  plan: WirePlan,
  at: Date,
): WireOccurrence | null => {
  const cutoff = at.toISOString();
  for (const occurrence of willRun(plan)) {
    // Both sides are ISO-8601 with an offset, and `toISOString()` normalises to
    // UTC, so this compares two instants rather than two wall clocks. A plain
    // string compare would be wrong the moment the two carried different
    // offsets, which is exactly what happens across a DST boundary.
    if (new Date(occurrence.start!).getTime() >= new Date(cutoff).getTime()) {
      return occurrence;
    }
  }
  return null;
};

/**
 * D77's fraction: how many of the schedule's stages are armed.
 *
 * Counted over distinct rules rather than over occurrences, because "2 of 3
 * stages armed" is a statement about the schedule and a weekly schedule has five
 * copies of each stage inside a seven-day window. A rule counts as armed if any
 * of its occurrences is — `armed` mirrors the rule's own flag, so they agree, and
 * `some` rather than `every` means a rule that appears once as armed is not
 * reported as off by an unresolved sibling.
 */
export interface ArmedCount {
  armed: number;
  total: number;
  /** True when at least one stage is armed and at least one is not. */
  partial: boolean;
}

export const armedStages = (plan: WirePlan): ArmedCount => {
  const byRule = new Map<string, boolean>();
  for (const occurrence of plan.occurrences) {
    byRule.set(
      occurrence.rule_id,
      (byRule.get(occurrence.rule_id) ?? false) || occurrence.armed,
    );
  }
  const total = byRule.size;
  let armed = 0;
  for (const value of byRule.values()) {
    if (value) {
      armed += 1;
    }
  }
  return { armed, total, partial: armed > 0 && armed < total };
};

/** Occurrences D11's precise stage dropped — rendered as dropped, per D12. */
export const outsideSet = (plan: WirePlan): WireOccurrence[] =>
  plan.occurrences.filter(
    (occurrence) => occurrence.status === "outside_set",
  );

/** Occurrences with no resolved instant, which is a broken anchor and not a gap. */
export const unresolved = (plan: WirePlan): WireOccurrence[] =>
  plan.occurrences.filter((occurrence) => occurrence.status === "unresolved");

/**
 * How far the plan can be read as fact, and why it stops there.
 *
 * §12.2's correction in one function. `computed_through` is how far we were
 * willing to enumerate — D44's ninety-day budget. `known_through` is how far the
 * sources would commit — a NEXT_ONLY resolver (D13) or, since D127, a day set's
 * own anchor. `solid_through` is the earlier of the two and is deliberately not
 * on the wire, because the reason matters to the reader: "we stopped counting" is
 * a setting, "nobody knows yet" is the world.
 */
export type HorizonLimit = "none" | "budget" | "sources" | "both";

export interface Horizon {
  solidThrough: string;
  limit: HorizonLimit;
}

export const horizon = (plan: WirePlan): Horizon => {
  const computed = new Date(plan.computed_through).getTime();
  const known = new Date(plan.known_through).getTime();
  const solidThrough =
    computed <= known ? plan.computed_through : plan.known_through;
  let limit: HorizonLimit = "none";
  if (!plan.fully_computed && !plan.fully_known) {
    limit = "both";
  } else if (!plan.fully_computed) {
    limit = "budget";
  } else if (!plan.fully_known) {
    limit = "sources";
  }
  return { solidThrough, limit };
};

/**
 * One sentence for a lane's coverage, naming the cause.
 *
 * D119 is the reason there are three states and not four: §5.5 describes an
 * *estimated* coverage that no producer emits and that is deliberately not
 * synthesised here, because inventing it in the frontend would make the
 * backend's `Coverage` enum a lie about what it can report.
 */
export const coverageReason = (
  coverage: Coverage,
  plan: WirePlan,
): string | null => {
  if (coverage === "known") {
    return null;
  }
  if (coverage === "not_computed") {
    return "Beyond the window almanac enumerates. Ask for a narrower range.";
  }
  if (plan.problem) {
    return plan.problem.detail;
  }
  return "A source would not say what happens this far out.";
};

/** The lane's own ordering: a schedule with a problem is worth seeing first. */
export const laneOrder = (
  a: WireScheduleTimeline,
  b: WireScheduleTimeline,
): number => {
  const rank = (lane: WireScheduleTimeline): number =>
    lane.plan.problem || unresolved(lane.plan).length > 0 ? 0 : 1;
  const byRank = rank(a) - rank(b);
  if (byRank !== 0) {
    return byRank;
  }
  return (a.name ?? a.schedule_id).localeCompare(b.name ?? b.schedule_id);
};
