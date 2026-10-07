// Which hidden settings are set (D170: policy, on-exit, completion and the like
// live in a collapsed Advanced section).
//
// A collapsed section is a promise that nothing in it matters until it is opened.
// The promise holds only if the section opens itself whenever a field in it
// differs from what a new schedule starts with -- otherwise an edited schedule
// carries a grace period or a completion mode the screen says nothing about.
// The defaults here are `schema.py`'s `vol.Optional(..., default=...)` values;
// a field missing from a schedule means its default, which is what the schema
// would fill in. `tests/test_frontend_assets.py` checks the names against the
// schema so the two cannot drift apart.
//
// No imports of any kind (D132: modules run by `node --test` have none).

export const DEFAULT_ADVANCED = {
  completion: {
    finished_when: { kind: "never" },
    then: { kind: "keep" },
    count_on: "scheduled",
  } as Record<string, unknown>,
  at: {
    condition_policy: { kind: "skip" } as unknown,
    grace: null as unknown,
  },
  during: {
    on_exit: { kind: "leave" } as unknown,
    latch: false as unknown,
  },
} as const;

/** The date window's default: both edges open. */
const DEFAULT_DATE_WINDOW = { from: null, until: null };

/** JSON with sorted keys, so two objects with the same content compare equal. */
export const stableStringify = (value: unknown): string => {
  if (value === null || value === undefined || typeof value !== "object") {
    return JSON.stringify(value ?? null) ?? "null";
  }
  if (Array.isArray(value)) {
    return `[${value.map(stableStringify).join(",")}]`;
  }
  const entries = Object.entries(value as Record<string, unknown>)
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([key, inner]) => `${JSON.stringify(key)}:${stableStringify(inner)}`);
  return `{${entries.join(",")}}`;
};

interface AdvancedSchedule {
  completion?: unknown;
  date_window?: unknown;
  // `object`, so a typed stored rule is accepted without an index signature.
  rules?: ReadonlyArray<object>;
}

/** A value that is absent counts as the default, as the schema would fill it. */
const differs = (actual: unknown, fallback: unknown): boolean =>
  stableStringify(actual ?? fallback) !== stableStringify(fallback);

/** The dotted names of every Advanced field that is not at its default. */
export const advancedNonDefault = (schedule: AdvancedSchedule): string[] => {
  const out: string[] = [];
  if (differs(schedule.completion, DEFAULT_ADVANCED.completion)) {
    out.push("completion");
  }
  if (differs(schedule.date_window, DEFAULT_DATE_WINDOW)) {
    out.push("date_window");
  }
  (schedule.rules ?? []).forEach((stored, index) => {
    const rule = stored as Record<string, unknown>;
    const defaults: Record<string, unknown> | undefined =
      rule["kind"] === "at"
        ? DEFAULT_ADVANCED.at
        : rule["kind"] === "during"
          ? DEFAULT_ADVANCED.during
          : undefined;
    if (!defaults) {
      return;
    }
    for (const [field, value] of Object.entries(defaults)) {
      if (differs(rule[field], value)) {
        out.push(`rules.${index}.${field}`);
      }
    }
  });
  return out;
};

export const isAdvancedOpen = (schedule: AdvancedSchedule): boolean =>
  advancedNonDefault(schedule).length > 0;

/**
 * Which Advanced section a dotted field name belongs to: the schedule's own
 * (completion, date window) or one rule's, named by position.
 */
export const sectionOf = (field: string): string => {
  const match = /^rules\.(\d+)\./.exec(field);
  return match ? `rule:${match[1]}` : "schedule";
};

/**
 * Whether one section is open. A section holding a set field is always open, so
 * collapsing it cannot hide the setting; any other follows the user's own toggle
 * for that section alone, so one collapse never reaches another section.
 */
export const isSectionOpen = (
  schedule: AdvancedSchedule,
  section: string,
  toggles: Readonly<Record<string, boolean>>,
): boolean =>
  advancedNonDefault(schedule).some((field) => sectionOf(field) === section) ||
  toggles[section] === true;

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const plainDay = (iso: unknown): string => {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(typeof iso === "string" ? iso : "");
  const month = match ? MONTHS[Number(match[2]) - 1] : undefined;
  return match && month ? `${Number(match[3])} ${month} ${match[1]}` : "";
};

/** The date window as one plain sentence, or "" when there is no window. */
export const describeDateWindow = (window: unknown): string => {
  const edges = (window ?? {}) as { from?: unknown; until?: unknown };
  const from = plainDay(edges.from);
  const until = plainDay(edges.until);
  if (from && until) {
    return `Active only from ${from} until ${until}`;
  }
  if (from) {
    return `Active only from ${from}`;
  }
  return until ? `Active only until ${until}` : "";
};
