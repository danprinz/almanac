// D73's axis, computed. The one file in the frontend with arithmetic in it.
//
// The track's axis is the anchor sequence, not the clock, so building it is a
// join and a sort rather than a layout: the stored rules say what each stage is
// relative to, the plan says where those anchors actually landed, and the rails
// come out ordered by the latter and positioned by the former. `track.ts` draws
// what this returns and makes no decisions of its own.
//
// Two builders come out of that. `buildTrack` joins the stored rules to an
// enumerated plan and is what the panel and the card draw. `draftTrack` is the
// same geometry with the plan left out — every instant null, no order check —
// which is the only honest drawing of a schedule the user has not saved, since
// nothing has resolved its anchors and D64 forbids this file from asking.
//
// **This module imports nothing at run time.** Every import is `import type`,
// which `verbatimModuleSyntax` forces to be written as such and which both
// TypeScript and Node erase outright. That is not an accident of this file being
// small — it is the condition that lets `node --test` execute it directly, with
// no bundler, no test framework and no new dependency (D132), and
// `tests/test_frontend_assets.py` fails if it stops holding. The same technique
// keeps `card.ts` off the always-loaded path for D70; here it buys a test suite
// instead of a byte count.
//
// Two instants never meet a wall clock in here. Everything is an epoch
// millisecond difference, which is A.13's lesson from the engine translated: a
// rail's position is the gap between two instants, and reading either of them as
// a local time first would make the arithmetic wrong across a DST boundary by
// exactly the hour the user is most likely to be scheduling around.

import type { WritableRule } from "./draft";
import type {
  StoredAction,
  StoredAnchor,
  StoredDesiredState,
  StoredEnd,
  StoredOnExit,
  StoredRule,
  StoredSchedule,
} from "./stored";
import type { WireOccurrence, WirePlan } from "./wire";

/** Which end of which rule a dot on a rail is. */
export type StageRole = "fire" | "enter" | "exit";

export interface Stage {
  ruleId: string;
  role: StageRole;
  /** Seconds from the rail's anchor event. Negative is before it. */
  offset: number;
  /** D77 — the rule's own `enabled`. A disabled stage is drawn, hollow. */
  enabled: boolean;
  /** What the stage does, in a few words. Null when the rule declares nothing. */
  does: string | null;
  /** `rail.instant` shifted by `offset`, or null when the rail did not resolve. */
  instant: string | null;
}

export interface Rail {
  /** The anchor's identity with its offset removed — D122's "the event". */
  key: string;
  /** What the rail is called on screen: `candle lighting`, `18:18`. */
  label: string;
  /** The anchor spelled out, for a tooltip: `hdate.candle_lighting`. */
  detail: string;
  /** Sorted by offset, earliest first. */
  stages: Stage[];
  /**
   * D73's local scale: the half-width this rail's offsets are laid out against,
   * in seconds, rounded up to a nice unit. Zero when every stage sits on the
   * anchor itself, which is the one case a rail needs no scale at all.
   */
  scale: number;
  /** Where this rail's anchor event landed on `day`, or null if it did not. */
  instant: string | null;
}

/** D73's compressed break, between rail *i* and rail *i+1*. */
export interface Gap {
  /** Real elapsed seconds between the two anchor events. Null if either is unknown. */
  seconds: number | null;
  /** D76 — these two anchors swap order somewhere in the window. */
  unstable: boolean;
}

export interface Track {
  scheduleId: string;
  rails: Rail[];
  /** `rails.length - 1` entries, or empty. */
  gaps: Gap[];
  /** The civil day the rails were resolved on, or null when none resolved. */
  day: string | null;
  /** D76 — true when *any* pair of rails changes order across the samples. */
  unstable: boolean;
}

/** D79's two ends of the generated summary. */
export interface Endpoint {
  rail: string;
  offset: number;
}

/**
 * How many future dates D76's order check samples.
 *
 * Four, because the pairs that actually swap — sunset against a fixed clock
 * time — swap on a seasonal scale, and a one-week window holds at most a few
 * distinct dates anyway. The check is free in D64's sense (the plan is already
 * enumerated; this is a sort over data in hand) so the number is about how much
 * of the window the user is being told about, not about cost.
 */
const ORDER_SAMPLES = 4;

/**
 * The ladder a rail's local scale rounds up to.
 *
 * D73 says "rounded to a nice unit" and this is the list. It stops at twelve
 * hours because an offset wider than that is not an offset any more — it is a
 * second anchor the user has not named yet, and a rail half a day wide draws
 * every other stage on the same pixel, which is the exact failure D73 exists to
 * avoid.
 */
const NICE_SCALES = [
  60, 300, 900, 1800, 3600, 2 * 3600, 3 * 3600, 6 * 3600, 12 * 3600,
];

const niceScale = (widest: number): number => {
  if (widest <= 0) {
    return 0;
  }
  for (const unit of NICE_SCALES) {
    if (unit >= widest) {
      return unit;
    }
  }
  return Math.ceil(widest / 3600) * 3600;
};

const shift = (instant: string, seconds: number): string =>
  new Date(Date.parse(instant) + seconds * 1000).toISOString();

/** `18:18:00` reads as `18:18`; a stored second is kept, because it was meant. */
const clockLabel = (at: string): string =>
  at.endsWith(":00") ? at.slice(0, 5) : at;

const humanise = (slug: string): string => slug.replace(/_/g, " ");

interface AnchorRef {
  key: string;
  label: string;
  detail: string;
  offset: number;
}

/**
 * An anchor's rail identity, label and offset.
 *
 * The key deliberately excludes the offset: D122 settled that the question asked
 * of a day set is about the anchor's *event* and that the offset is arithmetic
 * on top, and the same split is what makes a rail a rail. Two stages at −60m and
 * −5m from candle lighting are two dots on one rail, not two rails.
 *
 * `edge` is part of the key because a resolver's start and end are two different
 * events on the same day — an interval's beginning and its ending — and drawing
 * them as one rail would put a stage at the wrong end of the evening.
 */
const anchorRef = (
  anchor: StoredAnchor,
  entityName?: (entityId: string) => string | undefined,
): AnchorRef => {
  if (anchor.kind === "clock") {
    return {
      key: `clock:${anchor.at}`,
      label: clockLabel(anchor.at),
      detail: `clock ${anchor.at}`,
      offset: 0,
    };
  }
  if (anchor.kind === "entity_time") {
    return {
      key: `entity_time:${anchor.entity_id}`,
      label: entityName?.(anchor.entity_id) ?? anchor.entity_id,
      detail: anchor.entity_id,
      offset: anchor.offset,
    };
  }
  const edge = anchor.edge === "end" ? " end" : "";
  return {
    key: `resolver:${anchor.domain}:${anchor.key}:${anchor.edge}`,
    label: `${humanise(anchor.key)}${edge}`,
    detail: `${anchor.domain}.${anchor.key}${edge}`,
    offset: anchor.offset,
  };
};

const actionLabel = (action: StoredAction): string =>
  action.kind === "script" ? action.script : action.service;

const stateLabel = (state: StoredDesiredState | null): string | null => {
  if (!state || state.entities.length === 0) {
    return null;
  }
  const first = state.entities[0]!;
  return state.entities.length === 1
    ? `set ${first.entity_id}`
    : `set ${state.entities.length} entities`;
};

/** D3's three exits. `leave` says nothing, because leaving things alone is the default. */
const onExitLabel = (on_exit: StoredOnExit): string | null => {
  if (on_exit.kind === "restore") {
    return "restore";
  }
  return on_exit.kind === "apply" ? "apply a state" : null;
};

/**
 * One phrase for a stage, naming the first thing it does and counting the rest.
 *
 * Not a full list: D74 shrinks this to nothing at the two smaller sizes, and at
 * the editor's size the stage is expandable. Naming one and counting the others
 * is the shape that survives all four.
 */
const joined = (parts: readonly (string | null)[]): string | null => {
  const named = parts.filter((part): part is string => part !== null);
  const first = named[0];
  if (first === undefined) {
    return null;
  }
  return named.length === 1 ? first : `${first} +${named.length - 1} more`;
};

/**
 * What the geometry needs of a rule, which is less than a stored rule is.
 *
 * A `StoredRule` satisfies this; a draft rule does not, because D142 leaves `id`
 * absent until the backend mints one and D139 lets every other field be
 * half-typed. `drawable()` is the single place that difference is resolved, so
 * a saved schedule and one the user is still writing are laid out by the same
 * code — which is the only reason the editor can draw a track at all, and the
 * same argument D64 makes for the dry run sharing the engine's code path.
 */
interface DrawableAt {
  kind: "at";
  id: string;
  enabled: boolean;
  anchor: StoredAnchor;
  actions: readonly StoredAction[];
}

interface DrawableDuring {
  kind: "during";
  id: string;
  enabled: boolean;
  start_anchor: StoredAnchor;
  end: StoredEnd;
  state: StoredDesiredState | null;
  enter_actions: readonly StoredAction[];
  exit_actions: readonly StoredAction[];
  on_exit: StoredOnExit;
}

type DrawableRule = DrawableAt | DrawableDuring;

interface RuleStages {
  ref: AnchorRef;
  stage: Omit<Stage, "instant">;
}

/**
 * The stages one rule puts on the track, and which rail each belongs to.
 *
 * A `during` rule whose end is a duration puts *both* its stages on the start
 * anchor's rail, the exit one at `start offset + duration`. That is not a
 * shortcut: an interval that ends two hours after it began has one anchor, and
 * inventing a second rail for the end would draw a break labelled with a
 * duration the user already typed as the end.
 */
const stagesOf = (
  rule: DrawableRule,
  entityName?: (entityId: string) => string | undefined,
): RuleStages[] => {
  if (rule.kind === "at") {
    const ref = anchorRef(rule.anchor, entityName);
    return [
      {
        ref,
        stage: {
          ruleId: rule.id,
          role: "fire",
          offset: ref.offset,
          enabled: rule.enabled,
          does: joined(rule.actions.map(actionLabel)),
        },
      },
    ];
  }

  const enter = anchorRef(rule.start_anchor, entityName);
  const entered: RuleStages = {
    ref: enter,
    stage: {
      ruleId: rule.id,
      role: "enter",
      offset: enter.offset,
      enabled: rule.enabled,
      does: joined([stateLabel(rule.state), ...rule.enter_actions.map(actionLabel)]),
    },
  };

  const exitRef =
    rule.end.kind === "anchor" ? anchorRef(rule.end.anchor, entityName) : enter;
  const exitOffset =
    rule.end.kind === "anchor" ? exitRef.offset : enter.offset + rule.end.duration;

  return [
    entered,
    {
      ref: exitRef,
      stage: {
        ruleId: rule.id,
        role: "exit",
        offset: exitOffset,
        enabled: rule.enabled,
        does: joined([
          ...rule.exit_actions.map(actionLabel),
          onExitLabel(rule.on_exit),
        ]),
      },
    },
  ];
};

/** Where each rule's two ends sit, kept so the plan can be joined back onto them. */
interface RuleGeometry {
  enterKey: string;
  enterOffset: number;
  /** Null when the rule's end is a duration and therefore not its own anchor. */
  exitKey: string | null;
  exitOffset: number;
}

const geometryOf = (
  rules: readonly DrawableRule[],
  entityName?: (entityId: string) => string | undefined,
): Map<string, RuleGeometry> => {
  const found = new Map<string, RuleGeometry>();
  for (const rule of rules) {
    const enter = anchorRef(
      rule.kind === "at" ? rule.anchor : rule.start_anchor,
      entityName,
    );
    if (rule.kind === "during" && rule.end.kind === "anchor") {
      const exit = anchorRef(rule.end.anchor, entityName);
      found.set(rule.id, {
        enterKey: enter.key,
        enterOffset: enter.offset,
        exitKey: exit.key,
        exitOffset: exit.offset,
      });
      continue;
    }
    found.set(rule.id, {
      enterKey: enter.key,
      enterOffset: enter.offset,
      exitKey: null,
      exitOffset: 0,
    });
  }
  return found;
};

/**
 * Each rail's anchor event, read back out of the occurrences on one date.
 *
 * The engine resolved `start` as *anchor event + offset*, so subtracting the
 * offset recovers the event — which is why this does not need the resolver and
 * does not need a clock. D64's threaded `now` is what makes that inversion safe:
 * the instant in the payload was computed from the same offset this subtracts,
 * not from a second evaluation that might have moved.
 */
const railInstants = (
  occurrences: readonly WireOccurrence[],
  geometry: Map<string, RuleGeometry>,
): Map<string, string> => {
  const found = new Map<string, string>();
  const keep = (key: string, instant: string): void => {
    if (!found.has(key)) {
      found.set(key, instant);
    }
  };
  for (const occurrence of occurrences) {
    const shape = geometry.get(occurrence.rule_id);
    if (!shape) {
      continue;
    }
    if (occurrence.start !== null) {
      keep(shape.enterKey, shift(occurrence.start, -shape.enterOffset));
    }
    if (occurrence.end !== null && shape.exitKey !== null) {
      keep(shape.exitKey, shift(occurrence.end, -shape.exitOffset));
    }
  }
  return found;
};

/**
 * Rail keys in the order their anchors fire.
 *
 * A rail with no resolved instant sorts last rather than keeping its declared
 * place, because its place is the thing that is unknown. `Array.sort` is stable,
 * so two unresolved rails stay in declaration order relative to each other.
 */
const orderOf = (
  keys: readonly string[],
  instants: Map<string, string>,
): string[] =>
  [...keys].sort((a, b) => {
    const left = instants.get(a);
    const right = instants.get(b);
    if (left === undefined && right === undefined) {
      return 0;
    }
    if (left === undefined) {
      return 1;
    }
    if (right === undefined) {
      return -1;
    }
    return Date.parse(left) - Date.parse(right);
  });

const byDate = (
  plan: WirePlan,
): Map<string, WireOccurrence[]> => {
  const found = new Map<string, WireOccurrence[]>();
  for (const occurrence of plan.occurrences) {
    const existing = found.get(occurrence.start_date);
    if (existing) {
      existing.push(occurrence);
    } else {
      found.set(occurrence.start_date, [occurrence]);
    }
  }
  return found;
};

/**
 * The date the track is drawn for: the next one that resolves, else the last.
 *
 * Falling back to the most recent past date rather than to nothing is what keeps
 * a track on screen for a schedule whose window has already gone by — a rail
 * with real labels and a day in the past is readable, and an empty frame saying
 * "nothing ahead" has thrown away the shape of the thing the user came to look
 * at. The day is reported on the `Track`, so no surface has to guess which it got.
 */
const referenceDate = (plan: WirePlan, at: Date): string | null => {
  const resolved = plan.occurrences
    .filter((occurrence) => occurrence.start !== null)
    .sort((a, b) => Date.parse(a.start!) - Date.parse(b.start!));
  const cutoff = at.getTime();
  for (const occurrence of resolved) {
    if (Date.parse(occurrence.start!) >= cutoff) {
      return occurrence.start_date;
    }
  }
  return resolved[resolved.length - 1]?.start_date ?? null;
};

/**
 * D76's check: does this rail order hold on the next few dates?
 *
 * Reported two ways on purpose. `Track.unstable` is true when *any* pair of
 * rails changes order, which is the honest statement; a gap is marked when the
 * two rails it sits between swap, which is where the mark can actually be drawn.
 * The two can disagree — three rails can rotate without any adjacent pair
 * inverting — and in that case the track says it is unstable without claiming to
 * know where, which D76 prefers to a mark in the wrong place.
 */
const instability = (
  reference: readonly string[],
  samples: readonly string[][],
): { unstable: boolean; gaps: Set<number> } => {
  const gaps = new Set<number>();
  let unstable = false;
  const rank = (order: readonly string[], key: string): number =>
    order.indexOf(key);
  for (let i = 0; i < reference.length; i += 1) {
    for (let j = i + 1; j < reference.length; j += 1) {
      const a = reference[i]!;
      const b = reference[j]!;
      for (const order of samples) {
        const left = rank(order, a);
        const right = rank(order, b);
        if (left === -1 || right === -1 || left <= right) {
          continue;
        }
        unstable = true;
        if (j === i + 1) {
          gaps.add(i);
        }
      }
    }
  }
  return { unstable, gaps };
};

/** Rails in declaration order with their stages, before any plan is joined on. */
interface Collected {
  declared: string[];
  meta: Map<string, AnchorRef>;
  stages: Map<string, Omit<Stage, "instant">[]>;
}

/**
 * The rails a rule list declares, in the order it declares them.
 *
 * Declaration order first, always, so that a rail whose anchor did not resolve
 * has somewhere to be — `orderOf` sorts the resolved ones in front of it and
 * leaves it where the user put it. A draft never gets past this function,
 * because there is nothing to sort by.
 */
const collect = (
  rules: readonly DrawableRule[],
  entityName?: (entityId: string) => string | undefined,
): Collected => {
  const declared: string[] = [];
  const meta = new Map<string, AnchorRef>();
  const stages = new Map<string, Omit<Stage, "instant">[]>();
  for (const rule of rules) {
    for (const { ref, stage } of stagesOf(rule, entityName)) {
      if (!meta.has(ref.key)) {
        meta.set(ref.key, ref);
        declared.push(ref.key);
        stages.set(ref.key, []);
      }
      stages.get(ref.key)!.push(stage);
    }
  }
  return { declared, meta, stages };
};

/** Each rail's stages sorted and scaled, with whatever instants resolved joined on. */
const railsOf = (
  order: readonly string[],
  { meta, stages }: Omit<Collected, "declared">,
  instants: Map<string, string>,
): Rail[] =>
  order.map((key) => {
    const ref = meta.get(key)!;
    const instant = instants.get(key) ?? null;
    const own = [...(stages.get(key) ?? [])].sort((a, b) => a.offset - b.offset);
    const widest = own.reduce((most, stage) => Math.max(most, Math.abs(stage.offset)), 0);
    return {
      key,
      label: ref.label,
      detail: ref.detail,
      scale: niceScale(widest),
      instant,
      stages: own.map((stage) => ({
        ...stage,
        instant: instant === null ? null : shift(instant, stage.offset),
      })),
    };
  });

/** D73's breaks between adjacent rails. Null seconds wherever either end is unknown. */
const gapsOf = (rails: readonly Rail[], unstable: ReadonlySet<number>): Gap[] => {
  const gaps: Gap[] = [];
  for (let i = 0; i + 1 < rails.length; i += 1) {
    const here = rails[i]!.instant;
    const next = rails[i + 1]!.instant;
    gaps.push({
      seconds:
        here === null || next === null
          ? null
          : Math.round((Date.parse(next) - Date.parse(here)) / 1000),
      unstable: unstable.has(i),
    });
  }
  return gaps;
};

/**
 * D73's track for one schedule, from the stored rules and the enumerated plan.
 *
 * Returns a `Track` whose `rails` may be empty — a schedule with no rules is a
 * real thing to have just created — and callers render that as "no stages yet"
 * rather than treating it as a failure.
 */
export const buildTrack = (
  schedule: StoredSchedule,
  plan: WirePlan,
  at: Date,
  entityName?: (entityId: string) => string | undefined,
): Track => {
  const geometry = geometryOf(schedule.rules, entityName);
  const dates = byDate(plan);
  const day = referenceDate(plan, at);
  const instants = railInstants(dates.get(day ?? "") ?? [], geometry);

  const collected = collect(schedule.rules, entityName);
  const { declared } = collected;
  const order = orderOf(declared, instants);

  // Only dates on which *every* rail resolved can say anything about order. A
  // date where one anchor was unreadable sorts that rail last, which would read
  // as a swap and mark a stable pair as unstable — the one direction D76 must
  // not fail in, because the mark is a warning and a warning that fires on
  // ordinary unavailability is a warning the user learns to ignore.
  const complete = (found: Map<string, string>): boolean =>
    found.size === declared.length;
  const samples = complete(instants)
    ? [...dates.keys()]
        .filter((date) => day !== null && date > day)
        .sort()
        .slice(0, ORDER_SAMPLES)
        .map((date) => railInstants(dates.get(date) ?? [], geometry))
        .filter(complete)
        .map((found) => orderOf(declared, found))
    : [];
  const { unstable, gaps: unstableGaps } = instability(order, samples);

  const rails = railsOf(order, collected, instants);
  return {
    scheduleId: schedule.id,
    rails,
    gaps: gapsOf(rails, unstableGaps),
    day,
    unstable,
  };
};

/**
 * A draft rule as the geometry needs it, or null when it cannot be drawn yet.
 *
 * Three fields are filled in here and nowhere else. `id` becomes the rule's
 * index, which is what D142 makes it — a draft rule has no id until the backend
 * mints one, and the index is the handle the editor already addresses it by; a
 * stored id is a ULID, so a decimal index cannot be mistaken for one. `enabled`
 * becomes true, which is the one backend default this file restates that the
 * drawing depends on; `tests/test_schema.py` pins it, and names this file, so
 * that the restatement fails loudly rather than drifting. `on_exit` becomes
 * `leave`, whose label is null — which is also what absence would draw, so
 * nothing is claimed by filling it in.
 *
 * Null is for a rule with no anchor, and for a `during` rule with no end: there
 * is no position to draw them at, and inventing one would put a dot where the
 * engine will not fire. `problems()` names both cases, which is what keeps a
 * rule missing from its own track from reading as a rendering bug.
 */
const drawable = (rule: WritableRule, index: number): DrawableRule | null => {
  const id = String(index);
  const enabled = rule.enabled ?? true;
  if (rule.kind === "at") {
    return rule.anchor
      ? { kind: "at", id, enabled, anchor: rule.anchor, actions: rule.actions ?? [] }
      : null;
  }
  if (!rule.start_anchor || !rule.end) {
    return null;
  }
  return {
    kind: "during",
    id,
    enabled,
    start_anchor: rule.start_anchor,
    end: rule.end,
    state: rule.state ?? null,
    enter_actions: rule.enter_actions ?? [],
    exit_actions: rule.exit_actions ?? [],
    on_exit: rule.on_exit ?? { kind: "leave" },
  };
};

/**
 * D73's track for a schedule that has not been saved: the shape, not a prediction.
 *
 * Every instant is null and so is `day`, because nothing has enumerated these
 * rules — the plan comes from the engine over stored data, and D64 is what stops
 * this file resolving an anchor itself. So the rails come out in declaration
 * order rather than in firing order, and `unstable` is false rather than
 * unknown: D76's check needs resolved instants on several dates, and false is
 * the claim that no swap was *found*, which is true of a check that could not
 * run.
 *
 * A null instant is what `track.ts` draws amber for, meaning "almanac does not
 * know". That reading is right for a saved schedule and wrong here, where it
 * would make every draft amber and teach the user to ignore the colour — so the
 * component takes a `preview` flag and the editor sets it. That flag is the
 * whole of the difference between the two builders at the screen.
 */
export const draftTrack = (
  rules: readonly WritableRule[],
  scheduleId: string | null,
  entityName?: (entityId: string) => string | undefined,
): Track => {
  const drawables = rules
    .map(drawable)
    .filter((rule): rule is DrawableRule => rule !== null);
  const collected = collect(drawables, entityName);
  const rails = railsOf(collected.declared, collected, new Map());
  return {
    scheduleId: scheduleId ?? "",
    rails,
    gaps: gapsOf(rails, new Set()),
    day: null,
    unstable: false,
  };
};

/**
 * D79's summary, as its two ends rather than as a string.
 *
 * The string is `candle lighting −45m → havdalah +30m`, and the minus sign in it
 * is a real one — which is `format.ts`'s job, not this file's, because this file
 * has no run-time imports and `offsetLabel` lives there. Returning the parts
 * keeps that split and lets the micro-track reuse the same two endpoints as
 * labels without re-deriving them from a string.
 */
export const summaryEndpoints = (track: Track): Endpoint[] => {
  const first = track.rails[0];
  const last = track.rails[track.rails.length - 1];
  if (!first || !last) {
    return [];
  }
  const head = first.stages[0];
  const tail = last.stages[last.stages.length - 1];
  if (!head || !tail) {
    return [];
  }
  const start = { rail: first.label, offset: head.offset };
  if (head === tail) {
    return [start];
  }
  return [start, { rail: last.label, offset: tail.offset }];
};

/** D77's fraction, counted over the stored rules rather than over occurrences. */
export const armedRules = (
  schedule: StoredSchedule,
): { armed: number; total: number; partial: boolean } => {
  const total = schedule.rules.length;
  const armed = schedule.rules.filter((rule) => rule.enabled).length;
  return { armed, total, partial: armed > 0 && armed < total };
};
