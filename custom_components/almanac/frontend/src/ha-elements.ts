// Getting Home Assistant's own form elements defined on a page that has not
// visited an editor yet (D167).
//
// `ha-button` is in the frontend's entry bundle and needs nothing. `ha-form`,
// `ha-selector` and `ha-entity-picker` are lazy: Home Assistant only defines them
// when a route that uses them is first opened. A custom panel opened directly --
// a cold load, no dashboard visited first -- therefore finds them undefined. The
// technique below is the one scheduler-card (`src/lib/load_ha_form.js`, sha
// 83b6dec82d32) and Alarmo (`load-ha-elements.ts`, sha 47b31a68c61c) use: ask a
// `partial-panel-resolver` to load the `config` panel, then ask the config
// panel's own router to load its `automation` route, which imports all three.
//
// **It is private API**, and the risk is accepted in the spec: the trick has no
// reported breakage in either project. What did break was a specific component --
// `ha-combo-box` was removed in Home Assistant 2026.1 -- so almanac depends only
// on `ha-selector`, `ha-form` and `ha-entity-picker`, which both projects
// migrated *to*. And the failure mode is designed in: this returns a boolean, a
// `false` makes every component render today's plain inputs, and the editor says
// so in one line.
//
// The environment is injected so the control flow can be tested without a
// browser; `browserEnv()` is the real one. No imports of any kind (D132).

/** Whether the lazy elements are loaded. `loading` renders the fallback, silently. */
export type HaElementsState = "loading" | "ready" | "failed";

/** Only the elements almanac actually renders; waiting for more is a way to hang. */
export const REQUIRED_ELEMENTS = ["ha-form", "ha-selector", "ha-entity-picker"] as const;

export interface LoaderRoute {
  load: () => Promise<unknown>;
}

/** The bits of a resolver / panel element the recipe touches. All optional: absent is a failure. */
export interface LoaderElement {
  hass?: unknown;
  _updateRoutes?: () => void;
  routerOptions?: { routes: Record<string, LoaderRoute | undefined> };
}

export interface LoaderEnv {
  isDefined: (tag: string) => boolean;
  whenDefined: (tag: string) => Promise<unknown>;
  create: (tag: string) => LoaderElement;
  /** Resolves (with anything) after `ms`. */
  delay: (ms: number) => Promise<unknown>;
}

export const LOAD_BUDGET_MS = 4000;

const recipe = async (env: LoaderEnv): Promise<boolean> => {
  if (REQUIRED_ELEMENTS.every((tag) => env.isDefined(tag))) {
    return true;
  }
  const resolver = env.create("partial-panel-resolver");
  resolver.hass = { panels: [{ url_path: "tmp", component_name: "config" }] };
  (resolver._updateRoutes as () => void)();
  await (resolver.routerOptions?.routes["tmp"] as LoaderRoute).load();
  await env.whenDefined("ha-panel-config");

  const config = env.create("ha-panel-config");
  await (config.routerOptions?.routes["automation"] as LoaderRoute).load();
  await Promise.all(REQUIRED_ELEMENTS.map((tag) => env.whenDefined(tag)));
  return true;
};

/**
 * `true` when every required element is defined, `false` on a timeout or on any
 * throw -- including a `TypeError` from a private method that no longer exists,
 * which is why the casts above are allowed to fail loudly *inside* this `try`.
 */
export const loadHaElements = async (
  env: LoaderEnv,
  budgetMs = LOAD_BUDGET_MS,
): Promise<boolean> => {
  try {
    const outcome = await Promise.race([
      recipe(env),
      env.delay(budgetMs).then(() => false),
    ]);
    return outcome === true;
  } catch {
    return false;
  }
};

export const browserEnv = (): LoaderEnv => ({
  isDefined: (tag) => customElements.get(tag) !== undefined,
  whenDefined: (tag) => customElements.whenDefined(tag),
  create: (tag) => document.createElement(tag) as unknown as LoaderElement,
  delay: (ms) => new Promise((resolve) => window.setTimeout(resolve, ms)),
});
