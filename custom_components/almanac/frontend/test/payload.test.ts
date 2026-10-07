// The partition that lets a schema-driven form lose nothing.
//
// D152 refused a form built from `hass.services` because such a form cannot
// represent a key the schema does not list, or a service that is not loaded --
// and D17 says both are legal. The form is the convenience; the JSON box is the
// guarantee; these tests are the proof that the two halves put the whole payload
// back together.

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  fieldIsChange,
  flattenFields,
  lookupService,
  mergePayload,
  partitionPayload,
  samePartitionInput,
  withFieldValue,
} from "../src/payload.ts";

test("a field section is flattened into its children, in order", () => {
  const fields = flattenFields({
    transition: { name: "Transition", selector: { number: {} } },
    advanced_fields: {
      fields: {
        kelvin: { name: "Kelvin", selector: { color_temp: {} }, required: true },
        profile: { selector: { text: {} } },
      },
    },
  });
  assert.deepEqual(
    fields.map((field) => field.key),
    ["transition", "kelvin", "profile"],
  );
  assert.equal(fields[1]?.required, true);
  assert.equal(fields[1]?.name, "Kelvin");
});

test("a field with no selector is dropped, because there is nothing to render it with", () => {
  // The key still survives: it falls into the JSON box, which is the point.
  assert.deepEqual(flattenFields({ odd: { name: "Odd" } }), []);
});

test("a field with no name is labelled by its key", () => {
  assert.equal(flattenFields({ rgb_color: { selector: { color_rgb: {} } } })[0]?.name, "rgb_color");
});

test("a service is looked up by its dotted name, and a script by its entity-style name", () => {
  const services = {
    light: { turn_on: { fields: { brightness: { selector: { number: {} } } } } },
    script: { evening: { fields: { who: { selector: { text: {} } } } } },
  };
  assert.ok(lookupService(services, "light.turn_on"));
  assert.ok(lookupService(services, "script.evening"));
});

test("an unloaded or misspelt service has no entry rather than an empty one (D17)", () => {
  const services = { light: { turn_on: { fields: {} } } };
  assert.equal(lookupService(services, "zwave_js.refresh_value"), undefined);
  assert.equal(lookupService(services, "light.turn_onn"), undefined);
  assert.equal(lookupService(services, ""), undefined);
  assert.equal(lookupService(services, "nodot"), undefined);
});

test("partitioning splits stored data into schema keys and everything else", () => {
  const { known, other } = partitionPayload(
    { brightness: 200, transition: 2, vendor_flag: true },
    ["brightness", "transition"],
  );
  assert.deepEqual(known, { brightness: 200, transition: 2 });
  assert.deepEqual(other, { vendor_flag: true });
});

test("with no schema at all, everything is 'other' (the unloaded-service case)", () => {
  const data = { anything: 1, at: "all" };
  const { known, other } = partitionPayload(data, []);
  assert.deepEqual(known, {});
  assert.deepEqual(other, data);
});

test("merge(partition(x)) is the identity, for every split of every key", () => {
  const data = { a: 1, b: { nested: [1, 2] }, c: null, d: "x" };
  for (const keys of [[], ["a"], ["a", "c"], ["a", "b", "c", "d"], ["zzz"]]) {
    const { known, other } = partitionPayload(data, keys);
    assert.deepEqual(mergePayload(known, other), data);
  }
});

test("a form key beats the same key typed into the JSON box", () => {
  // Deterministic rather than first-come: the box is for keys the form cannot
  // show, so a collision means the user typed a key the form already owns.
  assert.deepEqual(mergePayload({ brightness: 10 }, { brightness: 99, x: 1 }), {
    brightness: 10,
    x: 1,
  });
});

test("clearing a field removes that key and nothing else", () => {
  const known = { brightness: 200, transition: 2 };
  assert.deepEqual(withFieldValue(known, "brightness", undefined), { transition: 2 });
  assert.deepEqual(withFieldValue(known, "transition", ""), { brightness: 200 });
});

test("setting a field leaves the others alone and does not mutate its input", () => {
  const known = { brightness: 200 };
  const next = withFieldValue(known, "transition", 4);
  assert.deepEqual(next, { brightness: 200, transition: 4 });
  assert.deepEqual(known, { brightness: 200 });
});

test("false and zero are values, not absences", () => {
  assert.deepEqual(withFieldValue({}, "flag", false), { flag: false });
  assert.deepEqual(withFieldValue({}, "level", 0), { level: 0 });
});

test("a selector echoing the value it was given is not a change", () => {
  const known = { brightness: 200, flash: false };
  assert.equal(fieldIsChange(known, "brightness", 200), false);
  assert.equal(fieldIsChange(known, "flash", false), false);
  assert.equal(fieldIsChange(known, "color", undefined), false);
  assert.equal(fieldIsChange(known, "color", ""), false);
  assert.equal(fieldIsChange({ rgb: [1, 2, 3] }, "rgb", [1, 2, 3]), false);
});

test("a selector reporting a different value, or a cleared one, is a change", () => {
  const known = { brightness: 200 };
  assert.equal(fieldIsChange(known, "brightness", 100), true);
  assert.equal(fieldIsChange(known, "brightness", undefined), true);
  assert.equal(fieldIsChange(known, "color", "red"), true);
  assert.equal(fieldIsChange({ flash: false }, "flash", true), true);
});

test("a partition is reused while the mapping reference and key list are unchanged", () => {
  const data = { a: 1 };
  assert.equal(samePartitionInput(data, ["a", "b"], data, ["a", "b"]), true);
  assert.equal(samePartitionInput(data, ["a", "b"], { a: 1 }, ["a", "b"]), false);
  assert.equal(samePartitionInput(data, ["a"], data, ["a", "b"]), false);
  assert.equal(samePartitionInput(data, ["a", "b"], data, ["b", "a"]), false);
  assert.equal(samePartitionInput(undefined, undefined, data, []), false);
});
