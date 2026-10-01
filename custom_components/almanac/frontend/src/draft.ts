// The editor's edit algebra — a draft, the pure functions that change it, and
// the two shapes it becomes on the way to storage.
//
// **D139 — the editor edits a draft and writes once.** Nothing in this file
// touches a websocket, holds a component, or knows that Lit exists. Every
// mutation is a function from a draft to a new draft, which is what makes the
// editor's behaviour testable at all: "what does the Save button send after the
// user changes the anchor and disables the second rule" is three function calls
// here, and is a browser and a running instance anywhere else.
//
// The alternative — write on each keystroke, the way an `input_*` helper's
// dialog does — was rejected for a reason that is almanac's rather than a
// matter of taste. A write is a `vol.Invalid` away from being refused, and a
// half-edited schedule is *valid*: a `during` rule whose end anchor has been
// cleared but whose start has not is a rule the engine would enumerate, fire and
// record. D116 means a fired rule is on the timeline's past half permanently.
// So the draft is the place where a schedule is allowed to be incomplete.
//
// **D132 again, for the same reason as `rails.ts`.** Every import below is
// `import type`, so after type-stripping this file has no imports at all, which
// is the condition under which `node --test` executes it with no bundler and no
// test framework. `tests/test_frontend_assets.py` fails if a plain `import`
// appears here.

import type {
  StoredAction,
  StoredAnchor,
  StoredAtRule,
  StoredDesiredState,
  StoredDuringRule,
  StoredSchedule,
} from "./stored";

/**
 * A rule as a *writer* may send it, which is not the shape a reader gets back.
 *
 * `schema.py` marks every field of a rule `vol.Optional` with a default except
 * `kind` and the anchor, and `_rule_id` mints a ULID when `id` is absent. So a
 * rule the user has just added is sent without an `id` and the backend assigns
 * one (D142) — the frontend has no id policy, mints nothing, and therefore needs
 * neither a clock nor a source of randomness, both of which would end D132.
 *
 * The cost is that a draft cannot address an unsaved rule by id, so the
 * functions below address rules by *position*. That is the honest encoding: the
 * id genuinely does not exist yet, and a provisional one invented here would be
 * a value `stored.ts` declares as `string` and the backend would replace.
 */
export type WritableAtRule = { kind: "at" } & Partial<Omit<StoredAtRule, "kind">>;
export type WritableDuringRule = { kind: "during" } & Partial<
  Omit<StoredDuringRule, "kind">
>;
export type WritableRule = WritableAtRule | WritableDuringRule;

/**
 * The schedule's own fields, as `_body_fields()` optionalises them for both
 * write commands. `rules` is widened to the writable shape; everything else is
 * `StoredSchedule`'s, so the key names are the ones
 * `tests/test_wire_contract.py` already holds against `schema.py`.
 */
export type ScheduleBody = Omit<
  StoredSchedule,
  "id" | "object_id" | "name" | "rules"
> & { rules: WritableRule[] };

/**
 * `schema.py::CREATE_FIELDS`. `name` is required; the body defaults.
 *
 * `object_id` is offered here and in no other write shape (D144, D66): it is the
 * entity_id's slug, so accepting it on an update would re-slug on rename and
 * break every automation, template and card naming the old one — later, and
 * without a warning. `tests/test_design_constraints.py` asserts its absence from
 * `UPDATE_FIELDS` on the Python side; this type is the same statement in the
 * language that would otherwise offer the field.
 */
export type ScheduleCreate = {
  name: string;
  object_id?: string;
} & Partial<ScheduleBody>;

/** `schema.py::UPDATE_FIELDS`. Every field optional, and no `object_id`. */
export type ScheduleUpdate = { name?: string } & Partial<ScheduleBody>;

/**
 * One schedule, mid-edit.
 *
 * `body` is `Partial` and the omission is the point: it holds the fields this
 * draft has an *opinion* about, which is exactly what both write commands
 * accept. `draftOf` fills all of them from a stored schedule; `newDraft` fills
 * one. Nothing here restates a backend default (D143) — `_BODY_DEFAULTS` is the
 * authority on what `enabled` or `recurrence` start as, and a copy of that list
 * in TypeScript would be a second authority that drifts without failing
 * anything.
 */
export interface Draft {
  /** `null` for a schedule that does not exist yet. */
  readonly id: string | null;
  /** Only ever sent on a create. `null` means "let the backend slug the name". */
  readonly object_id: string | null;
  readonly name: string;
  readonly body: Partial<ScheduleBody>;
}

/**
 * The clock time a brand-new rule starts at (D143).
 *
 * Fixed rather than derived from the browser's clock, which is the obvious
 * alternative and is worse in two ways: the editor's first render would differ
 * on every open, so no test could state what it shows; and "now, rounded up"
 * reads as a decision the user made when it is one nobody made. Noon is the one
 * wall-clock time that is never in a DST gap and never ambiguous, which matters
 * because D40 makes a clock anchor wall time.
 */
export const DEFAULT_CLOCK_TIME = "12:00:00";

/** An `at` rule on a typed clock time, with every other field left to default. */
export const clockAtRule = (at: string = DEFAULT_CLOCK_TIME): WritableAtRule => ({
  kind: "at",
  anchor: { kind: "clock", at },
});

/**
 * A draft for a schedule that does not exist yet.
 *
 * One `at` rule, because the empty schedule is the one thing the editor must not
 * show: D75 says no surface renders a bare rule, so a schedule with no rules has
 * nothing to draw, and D73's track would be a blank strip with no way to learn
 * what to put on it. One rule at noon is a diagram the user can edit.
 */
export const newDraft = (name = ""): Draft => ({
  id: null,
  object_id: null,
  name,
  body: { rules: [clockAtRule()] },
});

/** A draft of an existing schedule, opinionated about every field it has. */
export const draftOf = (schedule: StoredSchedule): Draft => ({
  id: schedule.id,
  object_id: schedule.object_id,
  name: schedule.name,
  body: {
    description: schedule.description,
    enabled: schedule.enabled,
    recurrence: schedule.recurrence,
    date_window: schedule.date_window,
    rules: schedule.rules,
    completion: schedule.completion,
  },
});

export const withName = (draft: Draft, name: string): Draft => ({ ...draft, name });

/**
 * Set or clear the entity_id slug. Meaningful only before the first save, and
 * `toUpdate` drops it rather than this refusing it — see `ScheduleCreate`.
 */
export const withObjectId = (draft: Draft, objectId: string | null): Draft => ({
  ...draft,
  object_id: objectId,
});

/** Merge an opinion about one or more body fields. */
export const withBody = (draft: Draft, patch: Partial<ScheduleBody>): Draft => ({
  ...draft,
  body: { ...draft.body, ...patch },
});

/** The draft's rules, or the empty list when it has no opinion about them. */
export const rulesOf = (draft: Draft): WritableRule[] => draft.body.rules ?? [];

export const addRule = (draft: Draft, rule: WritableRule): Draft =>
  withBody(draft, { rules: [...rulesOf(draft), rule] });

export const replaceRuleAt = (
  draft: Draft,
  index: number,
  rule: WritableRule,
): Draft => {
  const rules = [...checkedRules(draft, index)];
  rules[index] = rule;
  return withBody(draft, { rules });
};

export const removeRuleAt = (draft: Draft, index: number): Draft =>
  withBody(draft, {
    rules: checkedRules(draft, index).filter((_, at) => at !== index),
  });

/**
 * A position that is not a rule is a bug in the caller, not an edit.
 *
 * So it throws rather than returning the draft unchanged: an editor that silently
 * dropped a keystroke because its index was stale would be a defect nobody could
 * see, and the index comes from the component's own render, never from a user.
 */
const checkedRules = (draft: Draft, index: number): WritableRule[] => {
  const rules = rulesOf(draft);
  if (!Number.isInteger(index) || index < 0 || index >= rules.length) {
    throw new RangeError(`no rule at position ${index} of ${rules.length}`);
  }
  return rules;
};

/**
 * What a create would send. Throws for a draft that has already been saved.
 *
 * The body is spread whole, defaults and all, because a create has no stored
 * value to preserve — the asymmetry with `toUpdate` is the whole of D140.
 */
export const toCreate = (draft: Draft): ScheduleCreate => {
  if (draft.id !== null) {
    throw new Error(`${draft.id} already exists; this is an update`);
  }
  return {
    name: draft.name,
    ...(draft.object_id === null ? {} : { object_id: draft.object_id }),
    ...draft.body,
  };
};

/**
 * **D140 — an update sends only the fields that changed.**
 *
 * `storage.py::_update_data` is `item | update` and `UPDATE_FIELDS` carries no
 * defaults, so a field the message omits keeps its stored value. That makes the
 * diff correct rather than merely economical: a bundle that predates a schema
 * addition cannot clobber the field it does not know about, and the switch's own
 * arm/disarm write — `{"enabled": false}` — is already this shape, so the engine
 * and the editor reach storage the same way.
 *
 * The comparison is structural, over values that came from JSON, so key order
 * does not count and nothing is compared by reference. A field whose stored
 * value is an object the user opened and closed again is therefore not sent.
 */
export const toUpdate = (draft: Draft, original: StoredSchedule): ScheduleUpdate => {
  if (draft.id !== original.id) {
    throw new Error(`draft ${draft.id} is not an edit of ${original.id}`);
  }
  const changes: Record<string, unknown> = {};
  if (draft.name !== original.name) {
    changes.name = draft.name;
  }
  const stored = original as unknown as Record<string, unknown>;
  for (const [field, value] of Object.entries(draft.body)) {
    if (value !== undefined && !same(value, stored[field])) {
      changes[field] = value;
    }
  }
  return changes as ScheduleUpdate;
};

/**
 * Whether there is anything to send. `null` original means a create, which there
 * always is.
 */
export const isDirty = (draft: Draft, original: StoredSchedule | null): boolean =>
  original === null || Object.keys(toUpdate(draft, original)).length > 0;

/**
 * The reasons a save will certainly be refused — and **not** a claim that its
 * absence means the save will succeed (D146, and D80's rule about pre-flight
 * checks).
 *
 * `schema.py` is the authority and this file deliberately does not mirror it:
 * re-implementing `_clock_time`, `cv.entity_id` or D39's interval check in
 * TypeScript would produce a second validator that disagrees with the first
 * somewhere nobody is looking. What is here is the short list of things the
 * schema rejects that the *editor* can state in the user's own terms before a
 * round trip, so the checks below are the ones whose error messages would
 * otherwise come back as `humanize_error` output about a voluptuous marker.
 *
 * The rule checks were added with the editor, because they are the two cases
 * `rails.ts::draftTrack` cannot draw: a rule with no anchor has no position, so
 * without this the editor would list a rule that is simply missing from its own
 * track, which reads as a rendering bug rather than as an unfinished rule.
 */
export const problems = (draft: Draft): string[] => {
  const found: string[] = [];
  if (draft.name.trim() === "") {
    found.push("A schedule needs a name.");
  }
  if (draft.object_id !== null) {
    if (draft.object_id === "") {
      found.push("An entity id cannot be empty. Leave it unset instead.");
    } else if (/[^a-z0-9_]/.test(draft.object_id)) {
      found.push(
        "An entity id uses lower-case letters, digits and underscores only.",
      );
    }
  }
  rulesOf(draft).forEach((rule, index) => {
    const nth = index + 1;
    if (rule.kind === "at") {
      if (!rule.anchor) {
        found.push(`Rule ${nth} has no time.`);
      }
      return;
    }
    if (!rule.start_anchor) {
      found.push(`Rule ${nth} has no start.`);
    }
    if (!rule.end) {
      found.push(`Rule ${nth} has no end.`);
    }
  });
  return found;
};

/**
 * D78's footprint: what the schedule touches, separated by *how* it touches it.
 *
 * Four lists rather than one, because "touches" covers three different
 * relationships and a single list would conflate them. An entity a desired
 * state writes to is a thing this schedule changes; an entity an `entity_time`
 * anchor names is a thing it *reads a time from*, and when that one goes
 * unavailable the schedule does not misfire — it fails to fire at all, which is
 * a different question to ask of a different entity. Scripts and services are
 * named separately because a script is an entity with a `mode` and D31's
 * pre-flight reads it, while a service is not an entity at all.
 *
 * `unexpanded` is the known gap, counted rather than hidden: D57's index does
 * not resolve area, floor, device or label targets to entities, so a footprint
 * that listed only `entity_id` targets would under-report and look complete.
 * The editor says how many it could not expand.
 *
 * Pure, so it runs over a draft — the point being that the footprint of what
 * the user is about to save is the one worth showing, not the footprint of what
 * is stored.
 */
export interface Footprint {
  /** Entities a desired state or an action target writes to. */
  entities: string[];
  /** Entities an `entity_time` anchor reads a time from. */
  reads: string[];
  scripts: string[];
  services: string[];
  /** Area, floor, device and label targets, which almanac does not expand. */
  unexpanded: number;
}

export const footprintOf = (draft: Draft): Footprint => {
  const entities = new Set<string>();
  const reads = new Set<string>();
  const scripts = new Set<string>();
  const services = new Set<string>();
  let unexpanded = 0;

  const fromAnchor = (value: StoredAnchor | undefined): void => {
    if (value?.kind === "entity_time") {
      reads.add(value.entity_id);
    }
  };

  const fromAction = (value: StoredAction): void => {
    if (value.kind === "script") {
      scripts.add(value.script);
      return;
    }
    services.add(value.service);
    const target = value.target;
    if (!target) {
      return;
    }
    for (const id of target.entity_id) {
      entities.add(id);
    }
    unexpanded +=
      target.device_id.length +
      target.area_id.length +
      target.floor_id.length +
      target.label_id.length;
  };

  const fromState = (value: StoredDesiredState | null | undefined): void => {
    if (!value) {
      return;
    }
    for (const entity of value.entities) {
      entities.add(entity.entity_id);
    }
    value.override.forEach(fromAction);
  };

  for (const rule of rulesOf(draft)) {
    if (rule.kind === "at") {
      fromAnchor(rule.anchor);
      (rule.actions ?? []).forEach(fromAction);
      continue;
    }
    fromAnchor(rule.start_anchor);
    if (rule.end?.kind === "anchor") {
      fromAnchor(rule.end.anchor);
    }
    fromState(rule.state);
    (rule.enter_actions ?? []).forEach(fromAction);
    (rule.exit_actions ?? []).forEach(fromAction);
    if (rule.on_exit?.kind === "apply") {
      fromState(rule.on_exit.state);
    }
  }

  const sorted = (found: Set<string>): string[] => [...found].sort();
  return {
    entities: sorted(entities),
    reads: sorted(reads),
    scripts: sorted(scripts),
    services: sorted(services),
    unexpanded,
  };
};

/**
 * Structural equality over JSON values.
 *
 * Written out rather than `JSON.stringify(a) === JSON.stringify(b)`, which
 * compares key *order* and would report every field of a round-tripped schedule
 * as changed the first time a Python dict literal was reordered.
 */
const same = (a: unknown, b: unknown): boolean => {
  if (a === b) {
    return true;
  }
  if (Array.isArray(a) || Array.isArray(b)) {
    return (
      Array.isArray(a) &&
      Array.isArray(b) &&
      a.length === b.length &&
      a.every((value, index) => same(value, b[index]))
    );
  }
  if (typeof a !== "object" || typeof b !== "object" || a === null || b === null) {
    return false;
  }
  const left = a as Record<string, unknown>;
  const right = b as Record<string, unknown>;
  const keys = Object.keys(left);
  return (
    keys.length === Object.keys(right).length &&
    keys.every((key) => key in right && same(left[key], right[key]))
  );
};
