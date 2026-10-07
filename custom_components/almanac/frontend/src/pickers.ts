// What the Home Assistant pickers are asked for, and how their answers are read.
//
// Pure for the same reason `payload.ts` is: a picker "looks right" whether it
// returns a string or a list, and the stored target wants lists. The conversion
// is where that goes wrong silently, so it is the part with tests.
//
// No imports of any kind (D132).

export type PickerKind = "entity" | "timestamp" | "script" | "target";

/**
 * The selector each picker kind passes to `<ha-selector>`.
 *
 * `timestamp` is the entity-time anchor's list (D6): only entities whose
 * `device_class` is `timestamp` can be a time. `script` is the script action's.
 * `target` is HA's own target selector, which replaces the five comma-separated
 * boxes -- its value is `{entity_id?, device_id?, area_id?, floor_id?, label_id?}`.
 */
export const selectorFor = (kind: PickerKind): Record<string, unknown> => {
  switch (kind) {
    case "entity":
      return { entity: {} };
    case "timestamp":
      return { entity: { filter: { device_class: "timestamp" } } };
    case "script":
      return { entity: { domain: "script" } };
    case "target":
      return { target: {} };
  }
};

/** `const.py::TARGET_SELECTORS`, in the order the target selector shows them. */
export const TARGET_KEYS = [
  "entity_id",
  "device_id",
  "area_id",
  "floor_id",
  "label_id",
] as const;
export type TargetKey = (typeof TARGET_KEYS)[number];

type TargetLike = Partial<Record<TargetKey, string | string[] | null>>;

/** A picker's value for one target key, always as a clean list. */
export const idsOf = (
  value: TargetLike | undefined,
  key: TargetKey,
): string[] => {
  const held = value?.[key];
  const list = Array.isArray(held) ? held : held ? [held] : [];
  return list.filter((id) => typeof id === "string" && id !== "");
};

/** A stored target as the target selector's value: only the keys that hold ids. */
export const targetValue = (
  target: TargetLike | null | undefined,
): Record<string, string[]> => {
  const out: Record<string, string[]> = {};
  for (const key of TARGET_KEYS) {
    const ids = idsOf(target ?? undefined, key);
    if (ids.length > 0) {
      out[key] = ids;
    }
  }
  return out;
};

export interface ServiceName {
  id: string;
  domain: string;
  service: string;
}

/**
 * Every loaded service as `domain.service`, sorted. This is a *suggestion* list:
 * D17 means a schedule may name a service that is not in it, so nothing here ever
 * decides what is valid.
 */
export const serviceNames = (
  services: Record<string, Record<string, unknown>> | undefined,
): ServiceName[] =>
  Object.entries(services ?? {})
    .flatMap(([domain, byName]) =>
      Object.keys(byName).map((service) => ({
        id: `${domain}.${service}`,
        domain,
        service,
      })),
    )
    .sort((a, b) => a.id.localeCompare(b.id));

export const filterServices = (
  names: readonly ServiceName[],
  query: string,
  limit = 8,
): ServiceName[] => {
  const needle = query.trim().toLowerCase();
  return names
    .filter((entry) => needle === "" || entry.id.toLowerCase().includes(needle))
    .slice(0, limit);
};
