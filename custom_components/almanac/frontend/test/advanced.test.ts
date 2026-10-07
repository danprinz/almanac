// Advanced hides settings; the hazard is hiding one that is set. Every field the
// section holds is checked against what a new schedule starts with, so a schedule
// loaded with a non-default value opens its own Advanced and nothing is hidden.

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_ADVANCED,
  advancedNonDefault,
  describeDateWindow,
  isAdvancedOpen,
  isSectionOpen,
  stickyToggles,
  sectionOf,
} from "../src/advanced.ts";

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

test("each field belongs to one section: the schedule's or one rule's", () => {
  assert.equal(sectionOf("completion"), "schedule");
  assert.equal(sectionOf("date_window"), "schedule");
  assert.equal(sectionOf("rules.1.grace"), "rule:1");
});

test("a section with a set field is open whatever the user toggled", () => {
  const s = fresh();
  s.rules[0]!["grace"] = 60;
  assert.equal(isSectionOpen(s, "rule:0", { "rule:0": false }), true);
  // another section's collapse does not touch it, and does not open a default one
  assert.equal(isSectionOpen(s, "rule:0", { "rule:1": false }), true);
  assert.equal(isSectionOpen(s, "rule:1", {}), false);
  assert.equal(isSectionOpen(s, "schedule", {}), false);
});

test("a default section follows the user's own toggle, per section", () => {
  const s = fresh();
  assert.equal(isSectionOpen(s, "rule:1", { "rule:1": true }), true);
  assert.equal(isSectionOpen(s, "rule:1", { "rule:0": true }), false);
});

test("a section that opened because a field was set stays open after it is reverted", () => {
  const s = fresh();
  s.rules[0]!["grace"] = 60;
  const toggles = stickyToggles(s, {});
  assert.deepEqual(toggles, { "rule:0": true });
  s.rules[0]!["grace"] = null; // reverted to the default
  assert.equal(isSectionOpen(s, "rule:0", toggles), true);
});

test("a rule added after load, then set, becomes sticky too", () => {
  const s = fresh();
  const loaded = stickyToggles(s, {});
  assert.deepEqual(loaded, {});
  s.rules.push({ kind: "at", grace: 30 });
  const after = stickyToggles(s, loaded);
  assert.deepEqual(after, { "rule:2": true });
  (s.rules[2] as { grace: unknown }).grace = null;
  assert.equal(isSectionOpen(s, "rule:2", after), true);
});

test("a re-load starts from empty toggles and seeds from the new schedule", () => {
  const s = fresh();
  s.completion = { ...DEFAULT_ADVANCED.completion, count_on: "conditions_passed" };
  assert.deepEqual(stickyToggles(s, {}), { schedule: true });
  assert.deepEqual(stickyToggles(fresh(), {}), {});
});

test("stickiness keeps the user's own choices and returns the same object when nothing changes", () => {
  const s = fresh();
  const toggles = { "rule:1": true };
  assert.equal(stickyToggles(s, toggles), toggles);
  s.rules[1]!["latch"] = true;
  assert.equal(stickyToggles(s, toggles), toggles);
  s.rules[0]!["grace"] = 5;
  assert.deepEqual(stickyToggles(s, { "rule:1": true }), { "rule:0": true, "rule:1": true });
});

test("the date window is described in plain words, or not at all", () => {
  assert.equal(describeDateWindow({ from: null, until: null }), "");
  assert.equal(describeDateWindow(null), "");
  assert.equal(
    describeDateWindow({ from: "2026-12-01", until: "2027-01-31" }),
    "Active only from 1 Dec 2026 until 31 Jan 2027",
  );
  assert.equal(describeDateWindow({ from: "2026-12-01", until: null }), "Active only from 1 Dec 2026");
  assert.equal(describeDateWindow({ from: null, until: "2027-01-31" }), "Active only until 31 Jan 2027");
});
