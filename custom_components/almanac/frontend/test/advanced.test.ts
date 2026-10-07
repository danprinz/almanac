// Advanced hides settings; the hazard is hiding one that is set. Every field the
// section holds is checked against what a new schedule starts with, so a schedule
// loaded with a non-default value opens its own Advanced and nothing is hidden.

import { test } from "node:test";
import assert from "node:assert/strict";

import { DEFAULT_ADVANCED, advancedNonDefault, isAdvancedOpen } from "../src/advanced.ts";

const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T;

const fresh = () => ({
  completion: clone(DEFAULT_ADVANCED.completion) as unknown,
  date_window: { from: null, until: null } as unknown,
  rules: [
    {
      kind: "at",
      condition_policy: clone(DEFAULT_ADVANCED.at.condition_policy) as unknown,
      grace: DEFAULT_ADVANCED.at.grace as unknown,
    },
    {
      kind: "during",
      on_exit: clone(DEFAULT_ADVANCED.during.on_exit) as unknown,
      latch: DEFAULT_ADVANCED.during.latch as unknown,
    },
  ] as Array<Record<string, unknown>>,
});

test("a new schedule has nothing in Advanced", () => {
  assert.deepEqual(advancedNonDefault(fresh()), []);
  assert.equal(isAdvancedOpen(fresh()), false);
});

test("the defaults are the schema's", () => {
  assert.deepEqual(DEFAULT_ADVANCED.completion, {
    finished_when: { kind: "never" },
    then: { kind: "keep" },
    count_on: "scheduled",
  });
  assert.deepEqual(DEFAULT_ADVANCED.at, { condition_policy: { kind: "skip" }, grace: null });
  assert.deepEqual(DEFAULT_ADVANCED.during, { on_exit: { kind: "leave" }, latch: false });
});

test("a field left out reads as its default", () => {
  assert.deepEqual(advancedNonDefault({ rules: [{ kind: "at" }, { kind: "during" }] }), []);
  assert.deepEqual(advancedNonDefault({}), []);
  assert.deepEqual(advancedNonDefault({ date_window: null, completion: null }), []);
});

test("each hidden field opens the section when it is changed", () => {
  const completion = fresh();
  completion.completion = { ...DEFAULT_ADVANCED.completion, count_on: "actions_succeeded" };
  assert.deepEqual(advancedNonDefault(completion), ["completion"]);

  const window = fresh();
  window.date_window = { from: "2026-10-01", until: null };
  assert.deepEqual(advancedNonDefault(window), ["date_window"]);

  const policy = fresh();
  policy.rules[0]!["condition_policy"] = { kind: "wait_until", deadline: 600 };
  assert.deepEqual(advancedNonDefault(policy), ["rules.0.condition_policy"]);

  const grace = fresh();
  grace.rules[0]!["grace"] = 999;
  assert.deepEqual(advancedNonDefault(grace), ["rules.0.grace"]);

  const exit = fresh();
  exit.rules[1]!["on_exit"] = { kind: "restore" };
  assert.deepEqual(advancedNonDefault(exit), ["rules.1.on_exit"]);

  const latch = fresh();
  latch.rules[1]!["latch"] = true;
  assert.deepEqual(advancedNonDefault(latch), ["rules.1.latch"]);
  assert.equal(isAdvancedOpen(latch), true);
});

test("the comparison is by value, not by key order", () => {
  const reordered = (value: Record<string, unknown>) =>
    Object.fromEntries(Object.entries(value).reverse());
  const s = fresh();
  s.completion = reordered(DEFAULT_ADVANCED.completion);
  s.date_window = { until: null, from: null };
  assert.deepEqual(advancedNonDefault(s), []);
  assert.equal(isAdvancedOpen(s), false);
});

test("a rule kind the section does not know is ignored, not flagged", () => {
  const s = fresh();
  s.rules.push({ kind: "future", grace: 5 });
  assert.deepEqual(advancedNonDefault(s), []);
});
