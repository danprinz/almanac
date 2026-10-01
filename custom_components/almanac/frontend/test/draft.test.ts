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
  addRule,
  clockAtRule,
  draftOf,
  footprintOf,
  isDirty,
  newDraft,
  problems,
  removeRuleAt,
  replaceRuleAt,
  rulesOf,
  toCreate,
  toUpdate,
  withBody,
  withName,
  withObjectId,
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
  condition_policy: { kind: "all" },
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
  completion: {},
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
