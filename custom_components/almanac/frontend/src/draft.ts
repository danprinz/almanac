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
  StoredComparisonCondition,
  StoredComparisonOperator,
  StoredCondition,
  StoredDaySetCondition,
  StoredDesiredEntity,
  StoredDesiredState,
  StoredDuringRule,
  StoredGroupCondition,
  StoredLeafCondition,
  StoredScalar,
  StoredSchedule,
  StoredScriptAction,
  StoredServiceAction,
  StoredTarget,
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
 * The pieces a rule is made of, and the conversions the schema's cross-field
 * relations force on them.
 *
 * Here rather than in the components for this file's own reason: "what does
 * switching the operator to `in` do to the value the user already typed" is one
 * function call here and a browser anywhere else. Every one of these is also a
 * place where the schema refuses a combination rather than reinterpreting it, so
 * the editor has to move two fields at once or offer a shape that cannot be
 * saved.
 *
 * None of them has a writable half, and the asymmetry with `WritableRule` is
 * worth saying out loud. A draft *rule* is genuinely incomplete: `_rule_id`
 * mints the `id` the frontend does not have, so the rule the editor holds is
 * missing a field the stored one always has. An action is not like that —
 * `service` is required and every other field has a default, so an action is
 * fully populated from the moment it exists and the only thing wrong with a new
 * one is that its service name is the empty string. `problems()` says so; the
 * type does not pretend otherwise.
 */

/** D27's two action kinds, each with nothing to call yet. */
export const newServiceAction = (): StoredServiceAction => ({
  kind: "service",
  service: "",
  target: null,
  data: {},
});

export const newScriptAction = (): StoredScriptAction => ({
  kind: "script",
  script: "",
  fields: {},
  wait: false,
  timeout: null,
});

/**
 * D30's relation, as the only two states the editor can put a script in.
 *
 * `wait: true` with no timeout is the one setting that turns a slow script into
 * a stuck scheduler and `_script_action` refuses it; `wait: false` with a
 * timeout is refused in the other direction, as a stored number that does
 * nothing. So "wait for it" cannot be a checkbox over one field — it has to move
 * both, and a default timeout has to come from somewhere.
 *
 * A minute is that default: long enough that no reasonable "prepare, then act"
 * script reaches it, short enough that reaching it is not mistaken for the
 * scheduler having stopped. The previous timeout is preferred over it, so a user
 * who unticks the box and ticks it again gets their own number back rather than
 * ours.
 */
export const DEFAULT_SCRIPT_TIMEOUT = 60;

export const withWait = (
  action: StoredScriptAction,
  wait: boolean,
): StoredScriptAction => ({
  ...action,
  wait,
  timeout: wait ? (action.timeout ?? DEFAULT_SCRIPT_TIMEOUT) : null,
});

/**
 * One selector of a service call's target, written or cleared.
 *
 * Two things are kept straight here, and both are `_target`'s. An empty target
 * is refused — so clearing the last selector has to produce `target: null`, not
 * `{}` — and a selector with nothing in it is not stored at all, which keeps the
 * stored shape the same one that arrived and so keeps D140's diff quiet about a
 * field the user never touched.
 *
 * The five names are not listed anywhere in this file on purpose. They are
 * `StoredTarget`'s keys, `tests/test_wire_contract.py` already pairs those
 * against `const.py::TARGET_SELECTORS`, and a list here would be a sixth
 * spelling of the same five strings that no test compares.
 */
export const withTargetIds = (
  action: StoredServiceAction,
  selector: keyof StoredTarget,
  ids: string[],
): StoredServiceAction => {
  const merged: StoredTarget = { ...(action.target ?? {}), [selector]: ids };
  const kept = Object.fromEntries(
    Object.entries(merged).filter(([, held]) => (held ?? []).length > 0),
  ) as StoredTarget;
  return {
    ...action,
    target: Object.keys(kept).length === 0 ? null : kept,
  };
};

/** `const.py::LIST_OPERATORS`. The relation `_comparison` enforces. */
export const LIST_OPERATORS = ["in", "not_in"] as const;

export const takesAList = (operator: StoredComparisonOperator): boolean =>
  (LIST_OPERATORS as readonly string[]).includes(operator);

/**
 * A comparison on nothing yet: equality against the empty string.
 *
 * `eq` rather than a cleverer guess because the entity is not chosen yet, and
 * every operator but equality implies something about the entity's type.
 */
export const newComparison = (): StoredComparisonCondition => ({
  kind: "comparison",
  entity_id: "",
  attribute: null,
  operator: "eq",
  value: { kind: "constant", value: "" },
  for: null,
  label: null,
});

export const newDaySetCondition = (
  daySetId: string,
): StoredDaySetCondition => ({
  kind: "day_set",
  day_set_id: daySetId,
  negate: false,
  label: null,
});

/**
 * A group of two, because `_GROUP_SCHEMA` has `Length(min=2)`.
 *
 * `or` rather than `and`: the rule's own `conditions` list is already the AND
 * (§7.1), so a group is what an OR is spelled with and an `and` group is the
 * thing somebody built by mistake. It is still offered, for the reason
 * `const.py` gives — a group the user changed their mind about should not have
 * to be rebuilt — but it is not what a new one starts as.
 */
export const newGroup = (): StoredGroupCondition => ({
  kind: "group",
  operator: "or",
  conditions: [newComparison(), newComparison()],
  label: null,
});

/**
 * Change a comparison's operator, carrying the value across the list boundary.
 *
 * The conversion is the whole point. `_comparison` refuses `gt` against a list
 * and `in` against a scalar, so a bare operator swap would produce a condition
 * that cannot be saved — and the editor would be offering a dropdown whose
 * every other entry breaks the thing below it.
 *
 * Three cases, each chosen so that a mis-click costs as little as possible:
 *
 * - *scalar to list*: the scalar becomes the list's one element. Lossless, and
 *   "above 20" to "one of 20" is the reading the user will expect.
 * - *list to scalar*: the first element is kept. It is the only element every
 *   non-empty list has, and dropping all of them would make an accidental click
 *   destructive in a way no undo covers.
 * - *an entity operand to a list operator*: the entity is discarded for an empty
 *   list, because `_comparison` refuses the combination outright and no value
 *   the editor could invent out of an entity id would mean what the entity
 *   meant. The editor does not offer this direction — it hides the entity choice
 *   while a list operator is set — so this arm is the defence, not the path.
 */
export const withOperator = (
  condition: StoredComparisonCondition,
  operator: StoredComparisonOperator,
): StoredComparisonCondition => {
  const wantsList = takesAList(operator);
  const operand = condition.value;
  if (operand.kind === "entity") {
    return wantsList
      ? { ...condition, operator, value: { kind: "constant", value: [""] } }
      : { ...condition, operator };
  }
  const held = operand.value;
  if (Array.isArray(held) === wantsList) {
    return { ...condition, operator };
  }
  const value: StoredScalar | StoredScalar[] = Array.isArray(held)
    ? (held[0] ?? "")
    : [held];
  return { ...condition, operator, value: { kind: "constant", value } };
};

/**
 * Which of the three types a stored scalar is, and how to move it to another.
 *
 * The editor offers this as a choice rather than inferring it from the text, and
 * that is a decision with a cost on both sides. `_scalar` keeps the type the
 * user typed — deliberately, because coercing a threshold of `20` to `"20"`
 * turns a numeric comparison into a string one in which `"9" > "20"` — so the
 * type is a stored fact the editor has to render. Inference would render it by
 * guessing: a text sensor whose state is genuinely `"20"` could then never be
 * compared, because every spelling of the value would be read as a number.
 *
 * An unparseable conversion lands on `0`. The alternative — keep the text and
 * leave the selector saying "text" — makes the dropdown look broken; `0` is
 * wrong where the user can see it.
 */
export type ScalarType = "text" | "number" | "boolean";

export const scalarType = (value: StoredScalar): ScalarType => {
  if (typeof value === "number") {
    return "number";
  }
  return typeof value === "boolean" ? "boolean" : "text";
};

export const asScalarType = (
  value: StoredScalar,
  type: ScalarType,
): StoredScalar => {
  if (type === "number") {
    const parsed = Number(String(value).trim());
    return Number.isFinite(parsed) ? parsed : 0;
  }
  if (type === "boolean") {
    return value === true || String(value).trim().toLowerCase() === "true";
  }
  return String(value);
};

/**
 * A service call's payload, as text and back.
 *
 * **The payload is edited as JSON, and that is the one place in the editor where
 * the user types a syntax.** It is not a shortcut. `_service_data` validates
 * only that the mapping has string keys, and says why: the set of valid keys
 * belongs to the target service, not to almanac, and a whitelist here would go
 * stale every time an integration adds a field. A widget cannot be built for a
 * schema that is explicitly not ours — so a field-by-field editor would be a
 * field editor for the keys it happened to know, and D140's diff would send the
 * truncated payload as a change. That is D149's data-loss bug, and the reason
 * the whole step exists; JSON text is the one representation that cannot have it.
 *
 * A parse failure keeps the previous value and is reported. The editor does not
 * write a half-typed payload, and it does not silently discard one either.
 */
export const formatMapping = (value: Record<string, unknown>): string =>
  Object.keys(value).length === 0 ? "" : JSON.stringify(value, null, 2);

export type ParsedMapping =
  | { ok: true; value: Record<string, unknown> }
  | { ok: false; error: string };

export const parseMapping = (text: string): ParsedMapping => {
  if (text.trim() === "") {
    return { ok: true, value: {} };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(text) as unknown;
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : String(error),
    };
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    return {
      ok: false,
      error: "A payload is a set of named fields, not a list or a single value.",
    };
  }
  return { ok: true, value: parsed as Record<string, unknown> };
};

/**
 * D58, on screen. A payload containing `{{ ... }}` is stored and sent verbatim
 * and will reach the service as literal text, which is the honest outcome of
 * having no template layer. `_service_data`'s own docstring says the editor
 * warns and the schema does not refuse; this is that warning's test.
 */
export const looksLikeATemplate = (value: Record<string, unknown>): boolean =>
  JSON.stringify(value).includes("{{");

/**
 * D28's desired state, with the row the schema would refuse.
 *
 * `state: null` rather than `""`, and the difference matters: `_desired_entity`
 * refuses a row naming neither a state nor any attributes, and `""` is a state
 * — the empty one — so it would pass the schema and then ask
 * `async_reproduce_state` for a state no entity has. `null` is the shape
 * `problems()` can name.
 */
export const newDesiredEntity = (): StoredDesiredEntity => ({
  entity_id: "",
  state: null,
  attributes: {},
});

export const newDesiredState = (): StoredDesiredState => ({
  entities: [newDesiredEntity()],
  override: [],
});

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
 *
 * Step 9e added the action, condition and desired-state checks, and the choice of
 * *which* is the same choice as before. A new service action has no service name
 * and a new comparison has no entity, so the first thing the builders do is make
 * incomplete items easy to create; those are stated here. The schema's
 * cross-field relations — D30's wait and timeout, `in` against a scalar, a group
 * of one — are **not**, because the builders cannot produce them: `withWait`,
 * `withOperator` and `newGroup` move both fields together, so a check for them
 * would be a check for a shape no gesture reaches.
 */
export const problems = (draft: Draft): string[] => {
  const found: string[] = [];

  const checkActions = (
    actions: StoredAction[] | undefined,
    where: string,
  ): void => {
    (actions ?? []).forEach((action, at) => {
      const nth = at + 1;
      if (action.kind === "service") {
        if (action.service.trim() === "") {
          found.push(`${where} ${nth} has no service to call.`);
        }
        return;
      }
      if (action.script.trim() === "") {
        found.push(`${where} ${nth} has no script to run.`);
      }
    });
  };

  const checkLeaf = (condition: StoredLeafCondition, where: string): void => {
    if (condition.kind !== "comparison") {
      return;
    }
    if (condition.entity_id.trim() === "") {
      found.push(`${where} names no entity.`);
    }
    if (
      condition.value.kind === "entity" &&
      condition.value.entity_id.trim() === ""
    ) {
      found.push(`${where} compares against no entity.`);
    }
  };

  const checkConditions = (
    conditions: StoredCondition[] | undefined,
    where: string,
  ): void => {
    (conditions ?? []).forEach((condition, at) => {
      const nth = at + 1;
      if (condition.kind === "group") {
        condition.conditions.forEach((leaf, inner) => {
          checkLeaf(leaf, `${where} ${nth}, part ${inner + 1},`);
        });
        return;
      }
      checkLeaf(condition, `${where} ${nth}`);
    });
  };

  const checkState = (
    state: StoredDesiredState | null | undefined,
    where: string,
  ): void => {
    if (!state) {
      return;
    }
    if (state.entities.length === 0) {
      found.push(`${where} names no entities.`);
    }
    state.entities.forEach((entity, at) => {
      const nth = at + 1;
      if (entity.entity_id.trim() === "") {
        found.push(`${where}, entity ${nth}: nothing chosen.`);
      } else if (
        entity.state === null &&
        Object.keys(entity.attributes).length === 0
      ) {
        found.push(
          `${where}, ${entity.entity_id}: neither a state nor any ` +
            `attributes, so it describes nothing.`,
        );
      }
    });
    checkActions(state.override, `${where}'s override action`);
  };
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
    checkConditions(rule.conditions, `Rule ${nth}, condition`);
    if (rule.kind === "at") {
      if (!rule.anchor) {
        found.push(`Rule ${nth} has no time.`);
      }
      checkActions(rule.actions, `Rule ${nth}, action`);
      return;
    }
    if (!rule.start_anchor) {
      found.push(`Rule ${nth} has no start.`);
    }
    if (!rule.end) {
      found.push(`Rule ${nth} has no end.`);
    }
    checkState(rule.state, `Rule ${nth}'s desired state`);
    checkActions(rule.enter_actions, `Rule ${nth}, entry action`);
    checkActions(rule.exit_actions, `Rule ${nth}, exit action`);
    if (rule.on_exit?.kind === "apply") {
      checkState(rule.on_exit.state, `Rule ${nth}'s state on exit`);
    }
  });

  const completion = draft.body.completion;
  if (completion?.finished_when.kind === "condition") {
    checkConditions(completion.finished_when.conditions, "Finished-when check");
  }
  if (completion?.then.kind === "action") {
    checkActions(completion.then.actions, "Completion action");
  }
  return found;
};

/**
 * D78's footprint: what the schedule touches, separated by *how* it touches it.
 *
 * Four lists rather than one, because "touches" covers three different
 * relationships and a single list would conflate them. An entity a desired
 * state writes to is a thing this schedule changes; an entity an `entity_time`
 * anchor or a condition names is a thing it *reads*, and when that one goes
 * unavailable the schedule does not misfire — it fails to fire at all, or D97
 * says it changes nothing, which is a different question to ask of a different
 * entity. Scripts and services are
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
  /**
   * Entities the schedule reads rather than writes: an `entity_time` anchor's,
   * and a condition's — both sides of a comparison, including an `entity`
   * operand's. The two arrived at different steps and belong in one list
   * because the question they raise is the same one.
   */
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
    // Every selector `?? []`, because `_TARGET_SCHEMA` makes each of the five a
    // `vol.Optional` with no default: a target written with entities alone has
    // no `device_id` key at all. This read was `target.device_id.length` until
    // step 9e typed the absence, and that is a `TypeError` on any schedule
    // whose target names one selector -- which is most of them.
    for (const id of target.entity_id ?? []) {
      entities.add(id);
    }
    unexpanded +=
      (target.device_id ?? []).length +
      (target.area_id ?? []).length +
      (target.floor_id ?? []).length +
      (target.label_id ?? []).length;
  };

  const fromCondition = (value: StoredCondition): void => {
    if (value.kind === "group") {
      value.conditions.forEach(fromCondition);
      return;
    }
    if (value.kind !== "comparison") {
      return;
    }
    if (value.entity_id.trim() !== "") {
      reads.add(value.entity_id);
    }
    if (value.value.kind === "entity" && value.value.entity_id.trim() !== "") {
      reads.add(value.value.entity_id);
    }
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
    (rule.conditions ?? []).forEach(fromCondition);
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

  // Completion is a schedule-level field, so it is outside the rule loop — but
  // its two populated arms reach the same services and entities any rule does,
  // and a footprint that left them out would under-report the one action most
  // likely to be a surprise: the thing that runs when the schedule is finished.
  const completion = draft.body.completion;
  if (completion?.then.kind === "action") {
    completion.then.actions.forEach(fromAction);
  }
  if (completion?.finished_when.kind === "condition") {
    completion.finished_when.conditions.forEach(fromCondition);
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
