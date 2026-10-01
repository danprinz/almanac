// The edit algebra under test — which is to say, the editor's behaviour, minus
// the editor.
//
// Everything D139 bought is checked here: what the Save button sends after a
// rename, after a disable, after a rule is added, and after the user opened a
// field and changed nothing. That last one is the case worth having a test for,
// because D140 makes "nothing changed" mean "send nothing", and the obvious
// implementation — `JSON.stringify(a) === JSON.stringify(b)` — gets it wrong the
// first time a dict literal on the Python side is reordered.
//
// Runs under `node --test` for the same reason `rails.test.ts` does, and
// `tests/test_frontend_assets.py` holds the condition that makes it possible:
// `draft.ts` has no run-time import, so Node's loader executes it after
// stripping the types and there is no bundler and no framework in the way.

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_CLOCK_TIME,
  DEFAULT_SCRIPT_TIMEOUT,
  addRule,
  asScalarType,
  clockAtRule,
  draftOf,
  footprintOf,
  formatMapping,
  isDirty,
  looksLikeATemplate,
  newComparison,
  newDesiredState,
  newDraft,
  newGroup,
  newScriptAction,
  newServiceAction,
  parseMapping,
  problems,
  removeRuleAt,
  replaceRuleAt,
  rulesOf,
  scalarType,
  takesAList,
  toCreate,
  toUpdate,
  withBody,
  withName,
  withObjectId,
  withOperator,
  withWait,
} from "../src/draft.ts";
import type { StoredRule, StoredSchedule } from "../src/stored.ts";

const SCHEDULE_ID = "01JABCDEF";

const lamp: StoredRule = {
  kind: "at",
  id: "01JRULEONE",
  enabled: true,
  anchor: { kind: "clock", at: "07:00:00" },
  actions: [],
  conditions: [],
  condition_policy: { kind: "skip" },
  grace: null,
};

const stored: StoredSchedule = {
  id: SCHEDULE_ID,
  name: "Shabbat",
  object_id: "shabbat",
  description: "",
  enabled: true,
  recurrence: { kind: "weekdays", weekdays: ["fri"] },
  date_window: { from: null, until: null },
  rules: [lamp],
  completion: {
    finished_when: { kind: "never" },
    then: { kind: "keep" },
    count_on: "scheduled",
  },
};

// --- a schedule that does not exist yet -------------------------------------

test("a new draft is one rule at noon and no opinion about anything else", () => {
  const draft = newDraft("Bedtime");

  assert.equal(draft.id, null);
  assert.equal(draft.object_id, null);
  assert.equal(draft.name, "Bedtime");
  // D143: the backend owns `enabled`, `recurrence`, `date_window` and the rest.
  // Restating any of them here would be a second authority on the default.
  assert.deepEqual(Object.keys(draft.body), ["rules"]);
  assert.deepEqual(draft.body.rules, [
    { kind: "at", anchor: { kind: "clock", at: DEFAULT_CLOCK_TIME } },
  ]);
});

test("a rule the user has just added carries no id at all", () => {
  // D142. Not `id: null` — absent, which is what `_rule_id` needs in order to
  // mint one, and the reason the frontend owns no id policy.
  const rules = rulesOf(newDraft());
  assert.deepEqual(Object.keys(rules[0] ?? {}).sort(), ["anchor", "kind"]);
});

test("a create sends the name, the body, and object_id only when it is set", () => {
  const plain = toCreate(newDraft("Bedtime"));
  assert.deepEqual(Object.keys(plain).sort(), ["name", "rules"]);

  const slugged = toCreate(withObjectId(newDraft("Bedtime"), "bedtime"));
  assert.equal(slugged.object_id, "bedtime");
});

test("a draft of a stored schedule cannot be created again", () => {
  assert.throws(() => toCreate(draftOf(stored)), /already exists/);
});

// --- editing one that does ---------------------------------------------------

test("opening a schedule and changing nothing sends nothing", () => {
  const draft = draftOf(stored);

  assert.deepEqual(toUpdate(draft, stored), {});
  assert.equal(isDirty(draft, stored), false);
});

test("a rename sends the name and nothing else", () => {
  const changes = toUpdate(withName(draftOf(stored), "Shabbat lights"), stored);
  assert.deepEqual(changes, { name: "Shabbat lights" });
});

test("disarming sends the one field, which is the engine's own write", () => {
  // `{"enabled": false}` is exactly what the switch entity sends, so the editor
  // and the engine reach storage through the same shallow merge (D140).
  const changes = toUpdate(withBody(draftOf(stored), { enabled: false }), stored);
  assert.deepEqual(changes, { enabled: false });
});

test("a value that is structurally the same is not a change", () => {
  // Same content, different key order and a different object identity — which is
  // what a round trip through a form control produces.
  const draft = withBody(draftOf(stored), {
    recurrence: { weekdays: ["fri"], kind: "weekdays" },
  });
  assert.deepEqual(toUpdate(draft, stored), {});
});

test("a changed rule sends the whole list, because that is what the field is", () => {
  const draft = replaceRuleAt(draftOf(stored), 0, clockAtRule("22:30:00"));
  const changes = toUpdate(draft, stored);

  assert.deepEqual(Object.keys(changes), ["rules"]);
  assert.equal(changes.rules?.length, 1);
  assert.deepEqual(changes.rules?.[0], {
    kind: "at",
    anchor: { kind: "clock", at: "22:30:00" },
  });
});

test("an edit of one schedule cannot be saved over another", () => {
  const draft = draftOf(stored);
  const other = { ...stored, id: "01JOTHER" };
  assert.throws(() => toUpdate(draft, other), /is not an edit of/);
});

test("a draft with no original is always worth saving", () => {
  assert.equal(isDirty(newDraft("Bedtime"), null), true);
});

// --- the rule list ----------------------------------------------------------

test("every mutation returns a new draft and leaves the old one alone", () => {
  const before = draftOf(stored);
  const after = addRule(before, clockAtRule());

  assert.equal(rulesOf(before).length, 1);
  assert.equal(rulesOf(after).length, 2);
  assert.equal(rulesOf(before)[0], lamp);
});

test("rules are addressed by position, and removal keeps the order", () => {
  const draft = addRule(
    addRule(draftOf(stored), clockAtRule("13:00:00")),
    clockAtRule("14:00:00"),
  );
  const rules = rulesOf(removeRuleAt(draft, 1));

  assert.equal(rules.length, 2);
  assert.equal(rules[0], lamp);
  assert.deepEqual(rules[1], {
    kind: "at",
    anchor: { kind: "clock", at: "14:00:00" },
  });
});

test("a position that is not a rule throws rather than editing nothing", () => {
  const draft = draftOf(stored);
  for (const index of [-1, 1, 1.5]) {
    assert.throws(() => removeRuleAt(draft, index), RangeError);
    assert.throws(() => replaceRuleAt(draft, index, clockAtRule()), RangeError);
  }
});

test("a draft with no rules is still editable, and adding is how you start", () => {
  // `withBody` can clear the list, and `rulesOf` is what keeps the mutators from
  // needing to know whether the draft has an opinion yet.
  const bare = withBody(newDraft(), { rules: [] });
  assert.deepEqual(rulesOf(bare), []);
  assert.equal(rulesOf(addRule(bare, clockAtRule())).length, 1);
});

// --- what the editor can say before it writes -------------------------------

test("an unnamed schedule is a problem, and so is a name of spaces", () => {
  assert.equal(problems(newDraft("Bedtime")).length, 0);
  assert.equal(problems(newDraft("")).length, 1);
  assert.equal(problems(newDraft("   ")).length, 1);
});

test("an entity id is only reported when it certainly cannot be one", () => {
  // D146 with D80: these name a refusal that is certain. The absence of a problem
  // is not a promise that the save will succeed — `schema.py` is still the
  // authority, and nothing here re-implements it.
  const draft = newDraft("Bedtime");
  assert.equal(problems(withObjectId(draft, "shabbat_lights")).length, 0);
  assert.equal(problems(withObjectId(draft, "")).length, 1);
  assert.equal(problems(withObjectId(draft, "Shabbat Lights")).length, 1);
  assert.equal(problems(withObjectId(draft, "lights.kitchen")).length, 1);
});

test("a rule the track cannot place is reported, and it says which rule", () => {
  // The two cases `rails.ts::draftTrack` drops, because there is no position to
  // draw them at. Without these the editor would list a rule that is simply
  // absent from its own track, which reads as a rendering bug.
  const noTime = withBody(newDraft("Bedtime"), { rules: [{ kind: "at" }] });
  assert.deepEqual(problems(noTime), ["Rule 1 has no time."]);

  const noEnd = withBody(newDraft("Bedtime"), {
    rules: [
      clockAtRule(),
      { kind: "during", start_anchor: { kind: "clock", at: "18:00:00" } },
    ],
  });
  assert.deepEqual(problems(noEnd), ["Rule 2 has no end."]);

  const neither = withBody(newDraft("Bedtime"), { rules: [{ kind: "during" }] });
  assert.deepEqual(problems(neither), [
    "Rule 1 has no start.",
    "Rule 1 has no end.",
  ]);
});

// --- D78's footprint --------------------------------------------------------

test("the footprint separates what is written from what is only read", () => {
  // The distinction the four lists exist for. `sensor.candle_lighting` is an
  // anchor: when it goes unavailable the schedule does not misfire, it fails to
  // fire, and asking the user to check it is a different sentence from asking
  // them to check the lamp.
  const draft = withBody(newDraft("Shabbat"), {
    rules: [
      {
        kind: "during",
        start_anchor: {
          kind: "entity_time",
          entity_id: "sensor.candle_lighting",
          offset: -2700,
        },
        end: {
          kind: "anchor",
          anchor: {
            kind: "entity_time",
            entity_id: "sensor.havdalah",
            offset: 0,
          },
        },
        state: {
          entities: [{ entity_id: "light.dining", state: "on", attributes: {} }],
          override: [],
        },
        exit_actions: [
          {
            kind: "script",
            script: "script.wind_down",
            fields: {},
            wait: false,
            timeout: null,
          },
        ],
      },
    ],
  });

  assert.deepEqual(footprintOf(draft), {
    entities: ["light.dining"],
    reads: ["sensor.candle_lighting", "sensor.havdalah"],
    scripts: ["script.wind_down"],
    services: [],
    unexpanded: 0,
  });
});

test("a target almanac cannot expand is counted, not dropped", () => {
  // D57's index does not resolve an area to its entities, so a footprint that
  // listed `entity_id` targets alone would be complete-looking and wrong. The
  // count is the editor's way of saying "and some more I cannot name".
  const draft = withBody(newDraft("Evening"), {
    rules: [
      {
        kind: "at",
        anchor: { kind: "clock", at: "18:00:00" },
        actions: [
          {
            kind: "service",
            service: "light.turn_on",
            target: {
              entity_id: ["light.hall"],
              device_id: [],
              area_id: ["living_room", "kitchen"],
              floor_id: [],
              label_id: ["downstairs"],
            },
            data: {},
          },
        ],
      },
    ],
  });

  const footprint = footprintOf(draft);
  assert.deepEqual(footprint.entities, ["light.hall"]);
  assert.deepEqual(footprint.services, ["light.turn_on"]);
  assert.equal(footprint.unexpanded, 3);
});

test("the footprint of a draft with nothing in it is four empty lists", () => {
  // The editor renders nothing at all for this, so the shape matters: a
  // footprint that returned `null` for "nothing" would make every caller ask
  // the question twice.
  assert.deepEqual(footprintOf(newDraft("Bedtime")), {
    entities: [],
    reads: [],
    scripts: [],
    services: [],
    unexpanded: 0,
  });
});

// --- the pieces, and the conversions the schema forces (9e) -----------------

test("switching to a list operator wraps the value the user already typed", () => {
  // Lossless, and the reading the user expects: "above 20" to "one of 20".
  const above = withOperator(
    { ...newComparison(), entity_id: "sensor.rooms", value: { kind: "constant", value: 20 } },
    "gt",
  );
  const oneOf = withOperator(above, "in");

  assert.deepEqual(oneOf.value, { kind: "constant", value: [20] });
  assert.equal(oneOf.operator, "in");
});

test("switching away from a list operator keeps the first element", () => {
  // The only element every non-empty list has. Dropping all of them would make
  // a mis-click destructive with no undo behind it.
  const oneOf = withOperator(
    { ...newComparison(), value: { kind: "constant", value: ["home", "away"] } },
    "in",
  );

  assert.deepEqual(withOperator(oneOf, "eq").value, {
    kind: "constant",
    value: "home",
  });
});

test("an entity operand survives a scalar operator and not a list one", () => {
  // `_comparison` refuses `in` against another entity outright, so the editor
  // hides that direction — this is the defence behind the hiding.
  const against = {
    ...newComparison(),
    entity_id: "sensor.inside",
    value: { kind: "entity" as const, entity_id: "sensor.outside", attribute: null, offset: 0 },
  };

  assert.deepEqual(withOperator(against, "gt").value, against.value);
  assert.deepEqual(withOperator(against, "in").value, {
    kind: "constant",
    value: [""],
  });
});

test("an operator the list check does not claim keeps its scalar untouched", () => {
  // The no-op arm. `eq` to `lte` changes one field and nothing else, which is
  // what makes the three conversions above readable as the exceptions.
  const held = { ...newComparison(), value: { kind: "constant" as const, value: "on" } };

  assert.deepEqual(withOperator(held, "lte"), { ...held, operator: "lte" });
  assert.equal(takesAList("lte"), false);
  assert.equal(takesAList("not_in"), true);
});

test("waiting for a script moves the timeout with it, both ways", () => {
  // D30 refuses both halves of the inconsistent pair, so the checkbox cannot be
  // a checkbox over one field.
  const script = newScriptAction();
  const waiting = withWait(script, true);
  assert.equal(waiting.timeout, DEFAULT_SCRIPT_TIMEOUT);

  const notWaiting = withWait({ ...waiting, timeout: 15 }, false);
  assert.equal(notWaiting.timeout, null);

  // And the user's own number comes back rather than ours, because unticking a
  // box and reticking it is not a request to be given a default.
  assert.equal(withWait({ ...waiting, timeout: 15 }, true).timeout, 15);
});

test("a scalar's type is a stored fact, and the editor moves it deliberately", () => {
  // `_scalar` keeps the type the user typed because `"9" > "20"` is true as
  // strings. So a text sensor whose state is genuinely "20" has to be
  // expressible, which is what rules out inferring the type from the text.
  assert.equal(scalarType("20"), "text");
  assert.equal(scalarType(20), "number");
  assert.equal(scalarType(true), "boolean");

  assert.equal(asScalarType("20", "number"), 20);
  assert.equal(asScalarType(20, "text"), "20");
  assert.equal(asScalarType("true", "boolean"), true);
  assert.equal(asScalarType("on", "boolean"), false);

  // Wrong where the user can see it, rather than a dropdown that looks broken.
  assert.equal(asScalarType("not a number", "number"), 0);
});

test("a payload round-trips as JSON, and an empty one is empty text", () => {
  assert.equal(formatMapping({}), "");
  assert.equal(formatMapping({ brightness: 120 }), '{\n  "brightness": 120\n}');

  assert.deepEqual(parseMapping(""), { ok: true, value: {} });
  assert.deepEqual(parseMapping('{"brightness": 120}'), {
    ok: true,
    value: { brightness: 120 },
  });
});

test("a payload that is not a set of named fields is refused, not coerced", () => {
  // The three shapes JSON admits and `_service_data` does not. A list or a bare
  // number would reach the schema as the wrong type and come back as a
  // voluptuous marker; this says it in the user's terms first.
  for (const text of ["[1, 2]", "42", "null"]) {
    const parsed = parseMapping(text);
    assert.equal(parsed.ok, false, `${text} should not parse as a payload`);
  }

  const broken = parseMapping("{oops");
  assert.equal(broken.ok, false);
});

test("a payload with a template in it is flagged, because D58 sends it as text", () => {
  // `_service_data`'s own docstring says the editor warns and the schema does
  // not refuse. This is that warning.
  assert.equal(looksLikeATemplate({ message: "{{ states('sensor.x') }}" }), true);
  assert.equal(looksLikeATemplate({ message: "literally fine" }), false);
  assert.equal(looksLikeATemplate({ nested: { deep: "{{ x }}" } }), true);
});

// --- what the pre-flight can now say (D146 again) ---------------------------

test("a half-built action is named, by rule and by position", () => {
  const draft = withBody(newDraft("Evening"), {
    rules: [
      { ...clockAtRule("18:00:00"), actions: [newServiceAction(), newScriptAction()] },
    ],
  });

  assert.deepEqual(problems(draft), [
    "Rule 1, action 1 has no service to call.",
    "Rule 1, action 2 has no script to run.",
  ]);
});

test("a comparison with no entity is named, inside a group as well as outside", () => {
  const draft = withBody(newDraft("Evening"), {
    rules: [
      {
        ...clockAtRule("18:00:00"),
        conditions: [newComparison(), newGroup()],
      },
    ],
  });

  assert.deepEqual(problems(draft), [
    "Rule 1, condition 1 names no entity.",
    "Rule 1, condition 2, part 1, names no entity.",
    "Rule 1, condition 2, part 2, names no entity.",
  ]);
});

test("a desired-state row describing nothing is the schema's own refusal, early", () => {
  // `_desired_entity` refuses a row with neither a state nor any attributes.
  // `newDesiredEntity` leaves `state` null rather than "" precisely so this can
  // be said — "" is a state, the empty one, and would pass.
  const draft = withBody(newDraft("Shabbat"), {
    rules: [
      {
        kind: "during",
        start_anchor: { kind: "clock", at: "18:00:00" },
        end: { kind: "duration", duration: 3600 },
        state: {
          entities: [
            { entity_id: "", state: null, attributes: {} },
            { entity_id: "light.dining", state: null, attributes: {} },
          ],
          override: [],
        },
      },
    ],
  });

  assert.deepEqual(problems(draft), [
    "Rule 1's desired state, entity 1: nothing chosen.",
    "Rule 1's desired state, light.dining: neither a state nor any " +
      "attributes, so it describes nothing.",
  ]);
});

test("completion's two populated arms are checked like any rule's", () => {
  const draft = withBody(newDraft("One-off"), {
    rules: [clockAtRule("18:00:00")],
    completion: {
      finished_when: { kind: "condition", conditions: [newComparison()] },
      then: { kind: "action", actions: [newServiceAction()] },
      count_on: "scheduled",
    },
  });

  assert.deepEqual(problems(draft), [
    "Finished-when check 1 names no entity.",
    "Completion action 1 has no service to call.",
  ]);
});

test("a draft with nothing missing is a draft with no problems", () => {
  // The honest half of D146: an empty list is not a promise, and this test is
  // not asserting one. It asserts that the 9e checks do not fire on a schedule
  // that has filled them in — which is the thing a check that over-fires breaks.
  const draft = withBody(newDraft("Evening"), {
    rules: [
      {
        ...clockAtRule("18:00:00"),
        actions: [{ ...newServiceAction(), service: "light.turn_on" }],
        conditions: [{ ...newComparison(), entity_id: "binary_sensor.home" }],
      },
    ],
  });

  assert.deepEqual(problems(draft), []);
});

// --- the footprint, widened --------------------------------------------------

test("a condition's entities are reads, on both sides of the comparison", () => {
  // D97 — an unreadable condition changes nothing, which is a different failure
  // from a lamp that will not turn on and belongs in the same list as an anchor.
  const draft = withBody(newDraft("Evening"), {
    rules: [
      {
        ...clockAtRule("18:00:00"),
        conditions: [
          {
            ...newComparison(),
            entity_id: "sensor.inside",
            operator: "gt",
            value: {
              kind: "entity",
              entity_id: "sensor.outside",
              attribute: null,
              offset: 0,
            },
          },
          {
            kind: "group",
            operator: "or",
            conditions: [
              { ...newComparison(), entity_id: "binary_sensor.home" },
              { kind: "day_set", day_set_id: "01JDAYSET", negate: true, label: null },
            ],
            label: null,
          },
        ],
      },
    ],
  });

  assert.deepEqual(footprintOf(draft).reads, [
    "binary_sensor.home",
    "sensor.inside",
    "sensor.outside",
  ]);
  // A day set is not an entity, so it is in none of the four lists.
  assert.deepEqual(footprintOf(draft).entities, []);
});

test("what runs when a schedule finishes is in the footprint too", () => {
  // Completion sits outside the rule loop, so leaving it out was the easy
  // mistake — and it is the action most likely to be a surprise.
  const draft = withBody(newDraft("One-off"), {
    rules: [clockAtRule("18:00:00")],
    completion: {
      finished_when: { kind: "never" },
      then: {
        kind: "action",
        actions: [
          {
            kind: "script",
            script: "script.all_done",
            fields: {},
            wait: false,
            timeout: null,
          },
        ],
      },
      count_on: "scheduled",
    },
  });

  assert.deepEqual(footprintOf(draft).scripts, ["script.all_done"]);
});

test("a new desired state is one row naming nothing, which is a thing to edit", () => {
  // The empty-list alternative draws a block with no rows and no way to learn
  // what goes in one, which is `newDraft`'s argument about rules again.
  assert.deepEqual(newDesiredState(), {
    entities: [{ entity_id: "", state: null, attributes: {} }],
    override: [],
  });
});
