// What the HA pickers are asked for, and what comes back from them.
//
// `ha-selector` speaks `{entity_id?: string | string[], ...}` for a target and a
// bare value for everything else; the stored target is five optional arrays.
// The conversion is where a picker that "looks right" can quietly store a string
// where the schema wants a list.

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  TARGET_KEYS,
  filterServices,
  idsOf,
  pickIsChange,
  stableTargetValue,
  selectorFor,
  serviceNames,
  targetValue,
} from "../src/pickers.ts";

test("each picker kind maps to the selector the spec names", () => {
  assert.deepEqual(selectorFor("entity"), { entity: {} });
  assert.deepEqual(selectorFor("timestamp"), {
    entity: { filter: { device_class: "timestamp" } },
  });
  assert.deepEqual(selectorFor("script"), { entity: { domain: "script" } });
  assert.deepEqual(selectorFor("target"), { target: {} });
});

test("the five target keys are the stored target's five", () => {
  assert.deepEqual(
    [...TARGET_KEYS],
    ["entity_id", "device_id", "area_id", "floor_id", "label_id"],
  );
});

test("ids come out as a list whether the picker gave a string, a list or nothing", () => {
  assert.deepEqual(idsOf({ entity_id: "light.hall" }, "entity_id"), ["light.hall"]);
  assert.deepEqual(idsOf({ entity_id: ["a", "b"] }, "entity_id"), ["a", "b"]);
  assert.deepEqual(idsOf({}, "entity_id"), []);
  assert.deepEqual(idsOf(undefined, "area_id"), []);
  assert.deepEqual(idsOf({ area_id: ["", "kitchen"] }, "area_id"), ["kitchen"]);
});

test("a stored target becomes a selector value with only the keys it has", () => {
  assert.deepEqual(targetValue({ entity_id: ["light.hall"], area_id: [] }), {
    entity_id: ["light.hall"],
  });
  assert.deepEqual(targetValue(null), {});
  assert.deepEqual(targetValue(undefined), {});
});

test("services are listed as domain.service, sorted, from whatever is loaded", () => {
  const names = serviceNames({
    light: { turn_on: {}, turn_off: {} },
    climate: { set_temperature: {} },
  });
  assert.deepEqual(
    names.map((entry) => entry.id),
    ["climate.set_temperature", "light.turn_off", "light.turn_on"],
  );
});

test("filtering matches any part of the id, case-insensitively, and caps the list", () => {
  const names = serviceNames({
    light: { turn_on: {}, turn_off: {}, toggle: {} },
    switch: { turn_on: {} },
  });
  assert.deepEqual(
    filterServices(names, "TURN_ON").map((entry) => entry.id),
    ["light.turn_on", "switch.turn_on"],
  );
  assert.equal(filterServices(names, "", 2).length, 2);
  assert.deepEqual(filterServices(names, "nothing like this"), []);
});

test("typing a service that is not loaded is not filtered away -- the caller keeps the text", () => {
  // The picker never rewrites what was typed; it only suggests. An empty
  // suggestion list is the correct answer for an unloaded service (D17).
  const names = serviceNames({ light: { turn_on: {} } });
  assert.deepEqual(filterServices(names, "zwave_js.refresh_value"), []);
});

test("a picker echo of the value it was given is not a change", () => {
  assert.equal(pickIsChange("light.a", "light.a"), false);
  assert.equal(pickIsChange("", undefined), false);
  assert.equal(pickIsChange(undefined, ""), false);
  assert.equal(pickIsChange("light.a", "light.b"), true);
  assert.equal(pickIsChange("light.a", ""), true);
  assert.equal(pickIsChange("", "light.a"), true);
});

test("a target echo is compared by its ids, not its key order or empties", () => {
  const current = { entity_id: ["light.a"], area_id: ["den"] };
  assert.equal(
    pickIsChange(current, { area_id: ["den"], entity_id: ["light.a"] }),
    false,
  );
  assert.equal(pickIsChange(current, { ...current, device_id: [] }), false);
  assert.equal(pickIsChange({}, undefined), false);
  assert.equal(
    pickIsChange(current, { entity_id: ["light.b"], area_id: ["den"] }),
    true,
  );
});

test("the target selector's value keeps its identity across renders", () => {
  const stored = { entity_id: "light.a" };
  assert.equal(stableTargetValue(stored), stableTargetValue(stored));
  assert.deepEqual(stableTargetValue(stored), { entity_id: ["light.a"] });
  assert.equal(stableTargetValue(undefined), stableTargetValue(null));
  assert.notEqual(
    stableTargetValue(stored),
    stableTargetValue({ entity_id: "light.a" }),
  );
});
