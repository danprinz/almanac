// The conversions between what a native input gives and what the schema takes.
//
// Small functions, and the reason they are tested separately from `draft.ts` is
// that they are the layer where a wrong answer is *invisible*. An editor that
// writes `"18:00"` where the schema wants `"18:00:00"` still saves; D140's diff
// just reports a change that is only a spelling, and the next open shows the
// same field. An editor that writes `0` where the user typed nothing silently
// turns a thirty-minute timeout into no timeout at all.
//
// `form.ts` has no imports whatsoever — not even type-only ones — which is why
// it is the one pure module `tests/test_frontend_assets.py` checks by a
// different rule than `rails.ts` and `draft.ts`.

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  clockValue,
  countFrom,
  idList,
  idText,
  inputChecked,
  inputValue,
  minutesOf,
  secondsFrom,
} from "../src/form.ts";

/** What an input's `change` looks like, with nothing of a DOM in the way. */
const typedEvent = (value: string): Event =>
  ({ target: { value } }) as unknown as Event;

const tickedEvent = (checked: boolean): Event =>
  ({ target: { checked } }) as unknown as Event;

test("the value of whatever fired the event is read off the target", () => {
  assert.equal(inputValue(typedEvent("light.hall")), "light.hall");
  assert.equal(inputChecked(tickedEvent(true)), true);
  assert.equal(inputChecked(tickedEvent(false)), false);
});

test("a clock time with no seconds gets them, and one with them is left alone", () => {
  assert.equal(clockValue("18:00"), "18:00:00");
  assert.equal(clockValue("18:00:30"), "18:00:30");
});

test("minutes become whole seconds, in both directions", () => {
  assert.equal(secondsFrom("45"), 2700);
  assert.equal(secondsFrom("-45"), -2700);
  assert.equal(secondsFrom("0.5"), 30);
  assert.equal(minutesOf(2700), 45);
});

test("a cleared or unreadable duration field is zero, not NaN", () => {
  // Zero rather than `NaN` because `NaN` reaches the schema as `null` through
  // `JSON.stringify` and the field it feeds is required. A visible zero is a
  // value the user can see is wrong.
  assert.equal(secondsFrom(""), 0);
  assert.equal(secondsFrom("soon"), 0);
});

test("a count keeps the value it had rather than becoming zero", () => {
  // Every caller is a field the schema gives `Range(min=1)`, so there is no
  // reading of an empty box that the backend would accept. Falling back to what
  // was there is the only answer that saves.
  assert.equal(countFrom("3", 60), 3);
  assert.equal(countFrom("3.6", 60), 4);
  assert.equal(countFrom("", 60), 60);
  assert.equal(countFrom("0", 60), 60);
  assert.equal(countFrom("-2", 60), 60);
  assert.equal(countFrom("later", 60), 60);
});

test("a comma-separated list of ids drops the whitespace and the gaps", () => {
  // The empty entries matter: `_target` refuses a target that names nothing,
  // and `""` is not an id — a trailing comma must not produce one.
  assert.deepEqual(idList(" light.hall , light.porch "), [
    "light.hall",
    "light.porch",
  ]);
  assert.deepEqual(idList("light.hall,,"), ["light.hall"]);
  assert.deepEqual(idList("  "), []);
  assert.deepEqual(idList(""), []);
});

test("the list round-trips through the text the field shows", () => {
  const ids = ["light.hall", "light.porch"];
  assert.deepEqual(idList(idText(ids)), ids);
  assert.equal(idText([]), "");
});
