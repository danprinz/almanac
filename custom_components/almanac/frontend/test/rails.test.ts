// The one piece of the frontend with arithmetic in it, under test.
//
// D132 is why this runs at all: `rails.ts` imports nothing at run time, so
// Node's own loader can execute it after stripping the types, and `node --test`
// is the whole runner. No vitest, no jsdom, no bundler step — and nothing here
// touches the DOM, because the decisions D73 and D76 describe are decisions
// about numbers and order, and a test that had to render them would be a test of
// Lit.
//
// The relative imports name `./rails.ts` with its extension, which is what
// Node's loader requires and what `tsconfig.test.json` exists to permit. That
// asymmetry with `src/` is confined to this directory.
//
// `tests/test_frontend_assets.py` asserts that `rails.ts` has no run-time
// import, because the day one appears is the day this file stops running and the
// failure is a module-resolution error that looks like a toolchain problem.

import { test } from "node:test";
import assert from "node:assert/strict";

import { armedRules, buildTrack, summaryEndpoints } from "../src/rails.ts";
import type {
  StoredAnchor,
  StoredAtRule,
  StoredDuringRule,
  StoredEnd,
  StoredRule,
  StoredSchedule,
} from "../src/stored.ts";
import type { RuleKind, WireOccurrence, WirePlan } from "../src/wire.ts";

const SCHEDULE_ID = "01JABCDEF";

const schedule = (rules: StoredRule[]): StoredSchedule => ({
  id: SCHEDULE_ID,
  name: "Shabbat",
  object_id: "shabbat",
  description: "",
  enabled: true,
  recurrence: { kind: "weekdays", weekdays: ["fri"] },
  date_window: { from: null, until: null },
  rules,
  completion: {},
});

const resolver = (key: string, offset: number): StoredAnchor => ({
  kind: "resolver",
  domain: "hdate",
  key,
  offset,
  edge: "start",
});

const sun = (key: string, offset: number): StoredAnchor => ({
  kind: "resolver",
  domain: "sun",
  key,
  offset,
  edge: "start",
});

const clock = (at: string): StoredAnchor => ({ kind: "clock", at });

const at = (
  id: string,
  anchor: StoredAnchor,
  enabled = true,
): StoredAtRule => ({
  kind: "at",
  id,
  enabled,
  anchor,
  actions: [],
  conditions: [],
  condition_policy: { kind: "skip" },
  grace: null,
});

const during = (
  id: string,
  start_anchor: StoredAnchor,
  end: StoredEnd,
): StoredDuringRule => ({
  kind: "during",
  id,
  enabled: true,
  start_anchor,
  end,
  state: null,
  on_exit: { kind: "leave" },
  latch: false,
  enter_actions: [],
  exit_actions: [],
  conditions: [],
});

const occurrence = (
  ruleId: string,
  kind: RuleKind,
  startDate: string,
  start: string | null,
  end: string | null = null,
): WireOccurrence => ({
  schedule_id: SCHEDULE_ID,
  rule_id: ruleId,
  kind,
  start_date: startDate,
  status: start === null ? "unresolved" : "scheduled",
  armed: true,
  will_run: start !== null,
  start,
  end,
  problem: null,
});

const plan = (occurrences: WireOccurrence[]): WirePlan => ({
  schedule_id: SCHEDULE_ID,
  window: { start: "2026-10-01T00:00:00Z", end: "2026-10-31T00:00:00Z" },
  occurrences,
  computed_through: "2026-10-31T00:00:00Z",
  known_through: "2026-10-31T00:00:00Z",
  fully_computed: true,
  fully_known: true,
  problem: null,
});

/** Two instants are the same instant; neither side's spelling is asserted. */
const sameInstant = (actual: string | null, expected: string): void => {
  assert.notEqual(actual, null);
  assert.equal(Date.parse(actual!), Date.parse(expected));
};

// --- the flagship evening ---------------------------------------------------
//
// Candle lighting at 15:50Z on the Friday, havdalah at 17:20Z on the Saturday:
// 25h 30m apart, of which 55 minutes carry every stage but one. This is the
// shape §16.1's diagram draws and the reason D73 does not use a time axis.

const FLAGSHIP = schedule([
  at("acs", resolver("candle_lighting", -3600)),
  at("lights", resolver("candle_lighting", -300)),
  during("evening", resolver("candle_lighting", -2700), {
    kind: "anchor",
    anchor: resolver("havdalah", 1800),
  }),
]);

const FLAGSHIP_PLAN = plan([
  occurrence("acs", "at", "2026-10-02", "2026-10-02T14:50:00Z"),
  occurrence("lights", "at", "2026-10-02", "2026-10-02T15:45:00Z"),
  occurrence(
    "evening",
    "during",
    "2026-10-02",
    "2026-10-02T15:05:00Z",
    "2026-10-03T17:50:00Z",
  ),
]);

const FRIDAY = new Date("2026-10-02T00:00:00Z");

test("stages at different offsets from one anchor share one rail", () => {
  const track = buildTrack(FLAGSHIP, FLAGSHIP_PLAN, FRIDAY);
  assert.equal(track.rails.length, 2);
  const [candle, havdalah] = track.rails;
  assert.equal(candle!.label, "candle lighting");
  assert.equal(havdalah!.label, "havdalah");
  assert.deepEqual(
    candle!.stages.map((stage) => stage.offset),
    [-3600, -2700, -300],
  );
  assert.deepEqual(
    havdalah!.stages.map((stage) => stage.offset),
    [1800],
  );
});

test("a rail's anchor event is recovered by undoing the offset", () => {
  const track = buildTrack(FLAGSHIP, FLAGSHIP_PLAN, FRIDAY);
  sameInstant(track.rails[0]!.instant, "2026-10-02T15:50:00Z");
  sameInstant(track.rails[1]!.instant, "2026-10-03T17:20:00Z");
  assert.equal(track.day, "2026-10-02");
});

test("every stage instant lands back on the engine's own answer", () => {
  const track = buildTrack(FLAGSHIP, FLAGSHIP_PLAN, FRIDAY);
  const byRule = new Map(
    track.rails
      .flatMap((rail) => rail.stages)
      .map((stage) => [`${stage.ruleId}:${stage.role}`, stage.instant]),
  );
  sameInstant(byRule.get("acs:fire")!, "2026-10-02T14:50:00Z");
  sameInstant(byRule.get("lights:fire")!, "2026-10-02T15:45:00Z");
  sameInstant(byRule.get("evening:enter")!, "2026-10-02T15:05:00Z");
  sameInstant(byRule.get("evening:exit")!, "2026-10-03T17:50:00Z");
});

test("the break between rails carries the real elapsed duration", () => {
  const track = buildTrack(FLAGSHIP, FLAGSHIP_PLAN, FRIDAY);
  assert.equal(track.gaps.length, 1);
  // 25h 30m. The thing D73 compresses and labels rather than drawing to scale.
  assert.equal(track.gaps[0]!.seconds, 25 * 3600 + 30 * 60);
  assert.equal(track.gaps[0]!.unstable, false);
});

test("each rail's scale is the widest offset on it, rounded up", () => {
  const track = buildTrack(FLAGSHIP, FLAGSHIP_PLAN, FRIDAY);
  assert.equal(track.rails[0]!.scale, 3600);
  assert.equal(track.rails[1]!.scale, 1800);
});

test("a rail whose every stage sits on the anchor needs no scale", () => {
  const only = schedule([at("ring", clock("18:18:00"))]);
  const track = buildTrack(
    only,
    plan([occurrence("ring", "at", "2026-10-02", "2026-10-02T16:18:00Z")]),
    FRIDAY,
  );
  assert.equal(track.rails.length, 1);
  assert.equal(track.rails[0]!.label, "18:18");
  assert.equal(track.rails[0]!.scale, 0);
  assert.equal(track.gaps.length, 0);
});

test("D79's summary is the first stage and the last", () => {
  const track = buildTrack(FLAGSHIP, FLAGSHIP_PLAN, FRIDAY);
  assert.deepEqual(summaryEndpoints(track), [
    { rail: "candle lighting", offset: -3600 },
    { rail: "havdalah", offset: 1800 },
  ]);
});

test("a one-stage schedule summarises as one endpoint, not two equal ones", () => {
  const only = schedule([at("ring", clock("18:18:00"))]);
  const track = buildTrack(
    only,
    plan([occurrence("ring", "at", "2026-10-02", "2026-10-02T16:18:00Z")]),
    FRIDAY,
  );
  assert.deepEqual(summaryEndpoints(track), [{ rail: "18:18", offset: 0 }]);
});

// --- an interval that ends on a duration ------------------------------------

test("a duration end draws on the start anchor's rail, not a second one", () => {
  const twoHours = schedule([
    during("warmup", resolver("candle_lighting", -2700), {
      kind: "duration",
      duration: 7200,
    }),
  ]);
  const track = buildTrack(
    twoHours,
    plan([
      occurrence(
        "warmup",
        "during",
        "2026-10-02",
        "2026-10-02T15:05:00Z",
        "2026-10-02T17:05:00Z",
      ),
    ]),
    FRIDAY,
  );
  assert.equal(track.rails.length, 1);
  assert.deepEqual(
    track.rails[0]!.stages.map((stage) => stage.offset),
    [-2700, 4500],
  );
  sameInstant(track.rails[0]!.stages[1]!.instant, "2026-10-02T17:05:00Z");
  // 75 minutes is the widest offset, so the scale is the next nice unit above it.
  assert.equal(track.rails[0]!.scale, 7200);
});

// --- what is not known ------------------------------------------------------

test("an unresolved anchor sorts last and leaves its gap unknown", () => {
  const track = buildTrack(FLAGSHIP, plan([
    occurrence("acs", "at", "2026-10-02", "2026-10-02T14:50:00Z"),
    occurrence("lights", "at", "2026-10-02", "2026-10-02T15:45:00Z"),
    occurrence("evening", "during", "2026-10-02", null, null),
  ]), FRIDAY);
  assert.equal(track.rails.length, 2);
  assert.equal(track.rails[0]!.label, "candle lighting");
  assert.equal(track.rails[1]!.label, "havdalah");
  assert.equal(track.rails[1]!.instant, null);
  assert.equal(track.rails[1]!.stages[0]!.instant, null);
  assert.equal(track.gaps[0]!.seconds, null);
});

test("a schedule with no rules is a track with no rails, not a failure", () => {
  const track = buildTrack(schedule([]), plan([]), FRIDAY);
  assert.deepEqual(track.rails, []);
  assert.deepEqual(track.gaps, []);
  assert.equal(track.day, null);
  assert.deepEqual(summaryEndpoints(track), []);
});

test("a window that has entirely gone by still draws, on its last date", () => {
  const track = buildTrack(FLAGSHIP, FLAGSHIP_PLAN, new Date("2026-10-09T00:00:00Z"));
  assert.equal(track.day, "2026-10-02");
  sameInstant(track.rails[0]!.instant, "2026-10-02T15:50:00Z");
});

// --- D76, order instability -------------------------------------------------

const SWAPPING = schedule([
  at("blinds", sun("sunset", 0)),
  at("lamps", clock("22:00:00")),
]);

test("two anchors that swap order across the window are reported unstable", () => {
  const track = buildTrack(
    SWAPPING,
    plan([
      occurrence("blinds", "at", "2026-10-02", "2026-10-02T19:00:00Z"),
      occurrence("lamps", "at", "2026-10-02", "2026-10-02T22:00:00Z"),
      occurrence("blinds", "at", "2026-10-03", "2026-10-03T23:00:00Z"),
      occurrence("lamps", "at", "2026-10-03", "2026-10-03T22:00:00Z"),
    ]),
    FRIDAY,
  );
  assert.deepEqual(
    track.rails.map((rail) => rail.label),
    ["sunset", "22:00"],
  );
  assert.equal(track.unstable, true);
  assert.equal(track.gaps[0]!.unstable, true);
});

test("an order that holds is not marked", () => {
  const track = buildTrack(
    SWAPPING,
    plan([
      occurrence("blinds", "at", "2026-10-02", "2026-10-02T19:00:00Z"),
      occurrence("lamps", "at", "2026-10-02", "2026-10-02T22:00:00Z"),
      occurrence("blinds", "at", "2026-10-03", "2026-10-03T19:01:00Z"),
      occurrence("lamps", "at", "2026-10-03", "2026-10-03T22:00:00Z"),
    ]),
    FRIDAY,
  );
  assert.equal(track.unstable, false);
  assert.equal(track.gaps[0]!.unstable, false);
});

test("a date where one anchor did not resolve is not evidence of a swap", () => {
  const track = buildTrack(
    SWAPPING,
    plan([
      occurrence("blinds", "at", "2026-10-02", "2026-10-02T19:00:00Z"),
      occurrence("lamps", "at", "2026-10-02", "2026-10-02T22:00:00Z"),
      occurrence("blinds", "at", "2026-10-03", null),
      occurrence("lamps", "at", "2026-10-03", "2026-10-03T22:00:00Z"),
    ]),
    FRIDAY,
  );
  assert.equal(track.unstable, false);
});

// --- D77 --------------------------------------------------------------------

test("a disabled stage is counted, and still drawn", () => {
  const half = schedule([
    at("acs", resolver("candle_lighting", -3600), false),
    at("lights", resolver("candle_lighting", -300)),
  ]);
  assert.deepEqual(armedRules(half), { armed: 1, total: 2, partial: true });
  const track = buildTrack(
    half,
    plan([
      occurrence("acs", "at", "2026-10-02", "2026-10-02T14:50:00Z"),
      occurrence("lights", "at", "2026-10-02", "2026-10-02T15:45:00Z"),
    ]),
    FRIDAY,
  );
  assert.deepEqual(
    track.rails[0]!.stages.map((stage) => stage.enabled),
    [false, true],
  );
});

test("a fully armed schedule is not partial, and neither is a fully off one", () => {
  const anchor = resolver("candle_lighting", 0);
  assert.equal(armedRules(schedule([at("a", anchor)])).partial, false);
  assert.equal(armedRules(schedule([at("a", anchor, false)])).partial, false);
  assert.deepEqual(armedRules(schedule([])), {
    armed: 0,
    total: 0,
    partial: false,
  });
});

// --- labels -----------------------------------------------------------------

test("an entity anchor is named by the entity, or by a supplied friendly name", () => {
  const sensor = schedule([
    at("ring", { kind: "entity_time", entity_id: "sensor.candle", offset: -2700 }),
  ]);
  const bare = buildTrack(sensor, plan([]), FRIDAY);
  assert.equal(bare.rails[0]!.label, "sensor.candle");
  const named = buildTrack(sensor, plan([]), FRIDAY, () => "Candle lighting");
  assert.equal(named.rails[0]!.label, "Candle lighting");
  assert.equal(named.rails[0]!.detail, "sensor.candle");
});

test("a resolver's end edge is its own rail and says so", () => {
  const edges = schedule([
    at("open", { kind: "resolver", domain: "hdate", key: "shabbat", offset: 0, edge: "start" }),
    at("close", { kind: "resolver", domain: "hdate", key: "shabbat", offset: 0, edge: "end" }),
  ]);
  const track = buildTrack(edges, plan([]), FRIDAY);
  assert.deepEqual(
    track.rails.map((rail) => rail.label),
    ["shabbat", "shabbat end"],
  );
  assert.equal(track.rails[1]!.detail, "hdate.shabbat end");
});

test("a stage names one action and counts the rest", () => {
  const acting = schedule([
    {
      ...at("prep", resolver("candle_lighting", -3600)),
      actions: [
        { kind: "script", script: "script.shabbat_prep", fields: {}, wait: false, timeout: null },
        { kind: "service", service: "light.turn_on", target: null, data: {} },
      ],
    },
  ]);
  const track = buildTrack(acting, plan([]), FRIDAY);
  assert.equal(track.rails[0]!.stages[0]!.does, "script.shabbat_prep +1 more");
});

test("a stage that does nothing says nothing", () => {
  const track = buildTrack(FLAGSHIP, FLAGSHIP_PLAN, FRIDAY);
  assert.equal(track.rails[0]!.stages[0]!.does, null);
});
