// The time picker's decisions, with nothing of a DOM in the way.
//
// The component is a layout; the places it can be silently wrong are here. An
// offset stored with the wrong sign saves, validates, and moves a schedule to the
// wrong side of the event -- "45 minutes before candle lighting" becomes "after".

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  OFFSET_STEP_MINUTES,
  chooseDirection,
  clockChips,
  clockParts,
  clockText,
  describeOffset,
  latest,
  offsetParts,
  offsetSeconds,
  searchOfferings,
  setClockUnit,
  splitOfferings,
  stepClock,
  stepOffset,
  summarise,
  tileList,
  tileOf,
} from "../src/timepick.ts";

const offering = (domain: string, key: string, name = key, roles = ["anchor"]) => ({
  domain,
  key,
  display_name: name,
  roles,
});

// --- the offset ---------------------------------------------------------------

test("before is negative seconds, after is positive, at is exactly zero (D7)", () => {
  assert.equal(offsetSeconds("before", 45), -2700);
  assert.equal(offsetSeconds("after", 45), 2700);
  assert.equal(offsetSeconds("at", 45), 0);
  assert.equal(offsetSeconds("before", 0), 0);
});

test("a stored offset reads back as the same direction and magnitude", () => {
  assert.deepEqual(offsetParts(-2700), { direction: "before", minutes: 45 });
  assert.deepEqual(offsetParts(2700), { direction: "after", minutes: 45 });
  assert.deepEqual(offsetParts(0), { direction: "at", minutes: 0 });
});

test("the round trip is the identity across a range of offsets", () => {
  for (const seconds of [-86400, -5400, -300, -30, 0, 30, 300, 5400, 86400]) {
    const { direction, minutes } = offsetParts(seconds);
    assert.equal(offsetSeconds(direction, minutes), seconds);
  }
});

test("choosing at clears the offset; choosing a side from at starts at one step", () => {
  assert.equal(chooseDirection(-2700, "at"), 0);
  assert.equal(chooseDirection(0, "before"), -OFFSET_STEP_MINUTES * 60);
  assert.equal(chooseDirection(0, "after"), OFFSET_STEP_MINUTES * 60);
});

test("switching side keeps the magnitude", () => {
  assert.equal(chooseDirection(-2700, "after"), 2700);
  assert.equal(chooseDirection(2700, "before"), -2700);
});

test("stepping moves the magnitude and keeps the side", () => {
  assert.equal(stepOffset(-2700, 1), -2700 - OFFSET_STEP_MINUTES * 60);
  assert.equal(stepOffset(-2700, -1), -2700 + OFFSET_STEP_MINUTES * 60);
  assert.equal(stepOffset(900, 1), 900 + OFFSET_STEP_MINUTES * 60);
});

test("stepping down to nothing becomes at, and never crosses to the other side", () => {
  assert.equal(stepOffset(-300, -1), 0);
  assert.equal(stepOffset(300, -1), 0);
  assert.equal(stepOffset(-60, -1), 0);
  assert.equal(stepOffset(0, -1), 0);
});

test("an offset is described in words", () => {
  assert.equal(describeOffset(0), "");
  assert.equal(describeOffset(-2700), "45 min before");
  assert.equal(describeOffset(300), "5 min after");
  assert.equal(describeOffset(-5400), "1 h 30 min before");
  assert.equal(describeOffset(-7200), "2 h before");
});

test("a summary joins the offset and the subject", () => {
  assert.equal(summarise("Candle lighting", -2700), "45 min before candle lighting");
  assert.equal(summarise("Sunset", 0), "sunset");
  assert.equal(summarise("sun.next_rising", 600), "10 min after sun.next_rising");
  // A name that is not sentence-case is not lower-cased: it is a proper noun or an id.
  assert.equal(summarise("Shabbat and yom tov", 0), "Shabbat and yom tov");
});

// --- the clock ----------------------------------------------------------------

test("a clock value splits into digits and joins back with seconds", () => {
  assert.deepEqual(clockParts("18:30:00"), { hour: 18, minute: 30, second: 0 });
  assert.deepEqual(clockParts("07:05"), { hour: 7, minute: 5, second: 0 });
  assert.equal(clockText({ hour: 7, minute: 5, second: 0 }), "07:05:00");
});

test("seconds already stored survive an edit of hours or minutes", () => {
  // The picker does not show seconds, and must not erase them: D40's wall time
  // is stored as given.
  assert.equal(stepClock("18:30:15", "hour", 1), "19:30:15");
  assert.equal(setClockUnit("18:30:15", "minute", "45"), "18:45:15");
});

test("stepping wraps within its own unit and does not carry", () => {
  assert.equal(stepClock("23:00:00", "hour", 1), "00:00:00");
  assert.equal(stepClock("00:00:00", "hour", -1), "23:00:00");
  assert.equal(stepClock("10:58:00", "minute", 1), "10:03:00");
  assert.equal(stepClock("10:02:00", "minute", -1), "10:57:00");
});

test("typed digits are clamped into range and unreadable text changes nothing", () => {
  assert.equal(setClockUnit("10:00:00", "hour", "7"), "07:00:00");
  assert.equal(setClockUnit("10:00:00", "hour", "99"), "23:00:00");
  assert.equal(setClockUnit("10:00:00", "minute", "75"), "10:59:00");
  assert.equal(setClockUnit("10:00:00", "hour", ""), "10:00:00");
  assert.equal(setClockUnit("10:00:00", "hour", "ab"), "10:00:00");
});

test("quick chips are the times already in use first, then the fixed ones, once each", () => {
  assert.deepEqual(clockChips(["22:00:00", "08:30:00"]), [
    "22:00:00",
    "08:30:00",
    "06:00:00",
    "07:00:00",
    "18:00:00",
  ]);
  assert.deepEqual(clockChips([]), ["06:00:00", "07:00:00", "18:00:00", "22:00:00"]);
  assert.deepEqual(clockChips(["07:00:00", "07:00:00"]).slice(0, 2), [
    "07:00:00",
    "06:00:00",
  ]);
});

// --- tiles --------------------------------------------------------------------

test("tiles are a clock, one per resolver domain with an anchor, and an entity", () => {
  const tiles = tileList([
    offering("sun", "sunrise"),
    offering("sun", "sunset"),
    offering("hdate", "candle_lighting"),
    offering("hdate", "issur_melacha", "x", ["day_set"]),
  ]);
  assert.deepEqual(
    tiles.map((tile) => [tile.id, tile.label]),
    [
      ["clock", "Clock"],
      ["domain:sun", "Sun"],
      ["domain:hdate", "Jewish times"],
      ["entity_time", "From an entity"],
    ],
  );
});

test("a domain with only day-set offerings gets no tile, and a new domain gets one for free", () => {
  assert.deepEqual(
    tileList([offering("hdate", "x", "x", ["day_set"])]).map((tile) => tile.id),
    ["clock", "entity_time"],
  );
  const tiles = tileList([offering("zmanim_plus", "alos")]);
  assert.equal(tiles[1]?.id, "domain:zmanim_plus");
  assert.equal(tiles[1]?.label, "Zmanim plus");
});

test("a stored anchor selects the tile it belongs to", () => {
  assert.equal(tileOf(undefined), "clock");
  assert.equal(tileOf({ kind: "clock" }), "clock");
  assert.equal(tileOf({ kind: "entity_time" }), "entity_time");
  assert.equal(tileOf({ kind: "resolver", domain: "sun" }), "domain:sun");
});

// --- common offerings and search -----------------------------------------------

test("a known domain shows its curated common keys first, in curated order", () => {
  const all = [
    offering("sun", "midnight"),
    offering("sun", "sunset"),
    offering("sun", "sunrise"),
    offering("sun", "noon"),
    offering("sun", "dawn"),
    offering("sun", "dusk"),
    offering("sun", "solar_midnight_x"),
  ];
  const { common, more } = splitOfferings(all, "sun");
  assert.deepEqual(
    common.map((entry) => entry.key),
    ["sunrise", "sunset", "dawn", "dusk", "noon"],
  );
  assert.deepEqual(
    more.map((entry) => entry.key).sort(),
    ["midnight", "solar_midnight_x"],
  );
});

test("an unknown domain shows its first five in catalogue order", () => {
  const all = ["a", "b", "c", "d", "e", "f", "g"].map((key) => offering("new", key));
  const { common, more } = splitOfferings(all, "new");
  assert.deepEqual(common.map((entry) => entry.key), ["a", "b", "c", "d", "e"]);
  assert.deepEqual(more.map((entry) => entry.key), ["f", "g"]);
});

test("the selected offering is always visible, even when it would be under More", () => {
  const all = ["a", "b", "c", "d", "e", "f"].map((key) => offering("new", key));
  const { common, more } = splitOfferings(all, "new", "f");
  assert.ok(common.some((entry) => entry.key === "f"));
  assert.ok(!more.some((entry) => entry.key === "f"));
});

test("an offering that is not an anchor is never offered", () => {
  const all = [offering("hdate", "issur_melacha", "x", ["day_set"])];
  assert.deepEqual(splitOfferings(all, "hdate").common, []);
});

test("search matches the display name or the key, case-insensitively", () => {
  const all = [
    offering("hdate", "candle_lighting", "Candle lighting"),
    offering("hdate", "havdalah", "Havdalah"),
  ];
  assert.deepEqual(searchOfferings(all, "CANDLE").map((entry) => entry.key), [
    "candle_lighting",
  ]);
  assert.deepEqual(searchOfferings(all, "hav").map((entry) => entry.key), ["havdalah"]);
  assert.equal(searchOfferings(all, "").length, 2);
});

// --- the stale-answer guard -----------------------------------------------------

test("only the most recent request counts as current", () => {
  const guard = latest();
  const first = guard.next();
  const second = guard.next();
  assert.equal(guard.isCurrent(first), false);
  assert.equal(guard.isCurrent(second), true);
});
