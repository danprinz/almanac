// The loader depends on private Home Assistant API (`_updateRoutes`,
// `routerOptions.routes.X.load()`), which is exactly the kind of thing that
// disappears in a release. The contract here is not that the trick works -- only a
// browser on a real instance can say that -- but that when it does not, the editor
// learns so within the budget and falls back instead of hanging or throwing.

import { test } from "node:test";
import assert from "node:assert/strict";

import { REQUIRED_ELEMENTS, loadHaElements } from "../src/ha-elements.ts";
import type { LoaderEnv } from "../src/ha-elements.ts";

/** An environment where nothing is defined yet and every step succeeds. */
const happy = (log: string[] = []): LoaderEnv => {
  const defined = new Set<string>();
  const route = (name: string) => ({
    load: async () => {
      log.push(`load:${name}`);
      for (const tag of REQUIRED_ELEMENTS) {
        defined.add(tag);
      }
      defined.add("ha-panel-config");
    },
  });
  return {
    isDefined: (tag) => defined.has(tag),
    whenDefined: async (tag) => {
      if (!defined.has(tag)) {
        throw new Error(`${tag} never defined`);
      }
    },
    create: (tag) => {
      log.push(`create:${tag}`);
      return {
        hass: undefined,
        _updateRoutes: () => log.push("updateRoutes"),
        routerOptions: { routes: { tmp: route("tmp"), automation: route("automation") } },
      };
    },
    delay: () => new Promise(() => {}),
  };
};

test("the three elements almanac uses are the three that are waited for", () => {
  assert.deepEqual([...REQUIRED_ELEMENTS], ["ha-form", "ha-selector", "ha-entity-picker"]);
});

test("the happy path follows the recipe, in order, and reports success", async () => {
  const log: string[] = [];
  assert.equal(await loadHaElements(happy(log)), true);
  assert.deepEqual(log, [
    "create:partial-panel-resolver",
    "updateRoutes",
    "load:tmp",
    "create:ha-panel-config",
    "load:automation",
  ]);
});

test("nothing is created when the elements are already defined", async () => {
  const log: string[] = [];
  const env = happy(log);
  const already: LoaderEnv = { ...env, isDefined: () => true };
  assert.equal(await loadHaElements(already), true);
  assert.deepEqual(log, []);
});

test("a load that never settles is reported as failure at the budget, not awaited forever", async () => {
  const env = happy();
  const stuck: LoaderEnv = {
    ...env,
    create: () => ({
      _updateRoutes: () => {},
      routerOptions: { routes: { tmp: { load: () => new Promise(() => {}) } } },
    }),
    delay: async () => "timeout",
  };
  assert.equal(await loadHaElements(stuck, 5), false);
});

test("a private API that has gone away is a failure, not an exception", async () => {
  const env = happy();
  const gone: LoaderEnv = { ...env, create: () => ({}) };
  assert.equal(await loadHaElements(gone), false);
});

test("a load that rejects is a failure", async () => {
  const env = happy();
  const rejects: LoaderEnv = {
    ...env,
    create: () => ({
      _updateRoutes: () => {},
      routerOptions: {
        routes: { tmp: { load: async () => Promise.reject(new Error("chunk 404")) } },
      },
    }),
  };
  assert.equal(await loadHaElements(rejects), false);
});

test("finishing without defining the elements is a failure", async () => {
  const env = happy();
  const incomplete: LoaderEnv = {
    ...env,
    whenDefined: async () => {
      throw new Error("not defined");
    },
  };
  assert.equal(await loadHaElements(incomplete), false);
});
