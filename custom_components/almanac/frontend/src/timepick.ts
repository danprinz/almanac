// The time picker's arithmetic and its choices, separated from the layout.
//
// Everything here can be wrong without any error: an offset with the wrong sign
// validates and saves, a clock that carries an hour when its minutes wrap puts a
// schedule an hour off. So it is a pure module (D132) with tests, and the
// component under `time-picker.ts` is left holding only markup.
//
// Storage does not change. D7 stores an offset as signed seconds and D40 stores a
// clock time as wall time; this module is the translation between that and what
// a person says -- "45 minutes before", "at 18:30".
//
// No imports of any kind (D132).

// --- the offset (D7) ----------------------------------------------------------

export type Direction = "before" | "at" | "after";

export interface OffsetParts {
  direction: Direction;
  minutes: number;
}

/** The stepper's step. Long press repeats it; it is never made finer. */
export const OFFSET_STEP_MINUTES = 5;

export const offsetParts = (seconds: number): OffsetParts =>
  seconds === 0
    ? { direction: "at", minutes: 0 }
    : {
        direction: seconds < 0 ? "before" : "after",
        minutes: Math.abs(seconds) / 60,
      };

/** `at`, or a magnitude of zero, is exactly zero -- never negative zero. */
export const offsetSeconds = (direction: Direction, minutes: number): number => {
  if (direction === "at" || !(minutes > 0)) {
    return 0;
  }
  const seconds = Math.round(minutes * 60);
  return direction === "before" ? -seconds : seconds;
};

/**
 * What picking a side does to the stored offset. `at` clears it; a side keeps the
 * magnitude, and from `at` starts at one step rather than at zero (zero would be
 * `at` again, and the button would appear to do nothing).
 */
export const chooseDirection = (seconds: number, direction: Direction): number => {
  if (direction === "at") {
    return 0;
  }
  const minutes = seconds === 0 ? OFFSET_STEP_MINUTES : Math.abs(seconds) / 60;
  return offsetSeconds(direction, minutes);
};

/**
 * `steps` is a count of steps of the *magnitude*: positive is further from the
 * event, negative is closer. It keeps the side, and reaching zero is `at`; it can
 * never cross to the other side, because "5 before" minus a step meaning "0 after"
 * is the same thing as `at` and anything else would be a surprise.
 */
export const stepOffset = (seconds: number, steps: number): number => {
  const { direction, minutes } = offsetParts(seconds);
  if (direction === "at") {
    return 0;
  }
  return offsetSeconds(direction, Math.max(0, minutes + steps * OFFSET_STEP_MINUTES));
};

export const describeOffset = (seconds: number): string => {
  const { direction, minutes } = offsetParts(seconds);
  if (direction === "at") {
    return "";
  }
  const hours = Math.floor(minutes / 60);
  const rest = Math.round(minutes - hours * 60);
  const parts = [hours > 0 ? `${hours} h` : "", rest > 0 ? `${rest} min` : ""];
  return `${parts.filter((part) => part !== "").join(" ")} ${direction}`;
};

/**
 * "Candle lighting" -> "candle lighting" in the middle of a sentence. Only a
 * capitalised first word followed by lower-case words is touched, so a proper
 * noun ("Shabbat and yom tov") and an entity id ("sun.next_rising") keep their
 * spelling -- the latter has no capital to begin with.
 */
const SENTENCE_CASE = /^[A-Z][a-z]+( [a-z]+)*$/;
const PROPER = /^(Shabbat|Havdalah|Yom|Rosh|Pesach|Sukkot|Shavuot|Chanukah|Purim)/;
const lowerFirst = (text: string): string =>
  SENTENCE_CASE.test(text) && !PROPER.test(text)
    ? (text[0] as string).toLowerCase() + text.slice(1)
    : text;

/** "45 min before candle lighting". `subject` is an offering's display name or an entity id. */
export const summarise = (subject: string, seconds: number): string => {
  const offset = describeOffset(seconds);
  const named = lowerFirst(subject);
  return offset === "" ? named : `${offset} ${named}`;
};

// --- the clock (D40) ----------------------------------------------------------

export type ClockUnit = "hour" | "minute";

export interface ClockParts {
  hour: number;
  minute: number;
  second: number;
}

const two = (value: number): string => String(value).padStart(2, "0");

export const clockParts = (value: string): ClockParts => {
  const [hour = "0", minute = "0", second = "0"] = value.split(":");
  return {
    hour: Number(hour) || 0,
    minute: Number(minute) || 0,
    second: Number(second) || 0,
  };
};

export const clockText = (parts: ClockParts): string =>
  `${two(parts.hour)}:${two(parts.minute)}:${two(parts.second)}`;

const LIMIT: Record<ClockUnit, number> = { hour: 24, minute: 60 };

/** Minutes step by five from the keyboard and the wheel; typing is exact. */
const STEP: Record<ClockUnit, number> = { hour: 1, minute: 5 };

/** Wraps within its own unit and never carries: 10:58 + one step is 10:03. */
export const stepClock = (value: string, unit: ClockUnit, delta: number): string => {
  const parts = clockParts(value);
  const limit = LIMIT[unit];
  const next = (((parts[unit] + delta * STEP[unit]) % limit) + limit) % limit;
  return clockText({ ...parts, [unit]: next });
};

/** Typed digits, clamped into range. Unreadable text changes nothing. */
export const setClockUnit = (value: string, unit: ClockUnit, text: string): string => {
  if (!/^\d{1,2}$/.test(text.trim())) {
    return value;
  }
  const parts = clockParts(value);
  return clockText({
    ...parts,
    [unit]: Math.min(LIMIT[unit] - 1, Number(text)),
  });
};

export const QUICK_TIMES = ["06:00:00", "07:00:00", "18:00:00", "22:00:00"] as const;

/** Times already used in this schedule first, then the fixed ones; each once. */
export const clockChips = (used: readonly string[]): string[] => [
  ...new Set([...used, ...QUICK_TIMES]),
];

// --- tiles --------------------------------------------------------------------

export interface OfferingLike {
  domain: string;
  key: string;
  display_name: string;
  roles: readonly string[];
}

export interface Tile {
  id: string;
  label: string;
}

/** The two domains that ship. Anything else is its domain name, capitalised. */
export const DOMAIN_LABELS: Record<string, string> = {
  sun: "Sun",
  hdate: "Jewish times",
};

const labelOfDomain = (domain: string): string =>
  DOMAIN_LABELS[domain] ??
  (domain.charAt(0).toUpperCase() + domain.slice(1)).replace(/_/g, " ");

/**
 * One tile for a clock, one per resolver domain that has an anchor-role offering,
 * and one for an entity. Data-driven: a new resolver adds a tile with no editor
 * change.
 */
export const tileList = (offerings: readonly OfferingLike[]): Tile[] => {
  const domains: string[] = [];
  for (const offering of offerings) {
    if (offering.roles.includes("anchor") && !domains.includes(offering.domain)) {
      domains.push(offering.domain);
    }
  }
  return [
    { id: "clock", label: "Clock" },
    ...domains.map((domain) => ({ id: `domain:${domain}`, label: labelOfDomain(domain) })),
    { id: "entity_time", label: "From an entity" },
  ];
};

export const tileOf = (
  anchor: { kind: string; domain?: string } | undefined,
): string => {
  if (!anchor || anchor.kind === "clock") {
    return "clock";
  }
  return anchor.kind === "resolver" ? `domain:${anchor.domain ?? ""}` : "entity_time";
};

// --- common offerings and search -------------------------------------------------

/**
 * The offerings worth a chip of their own. A frontend constant rather than a
 * catalogue field: which sunset-like events a home user reaches for is a judgement
 * about people, not a property of a resolver, and putting it on the wire would
 * be a contract change for a layout decision. A domain with no entry shows its
 * first five in catalogue order.
 */
export const COMMON_KEYS: Record<string, readonly string[]> = {
  sun: ["sunrise", "sunset", "dawn", "dusk", "noon"],
  hdate: ["candle_lighting", "havdalah"],
};

export const splitOfferings = <T extends OfferingLike>(
  offerings: readonly T[],
  domain: string,
  selectedKey?: string,
  limit = 5,
): { common: T[]; more: T[] } => {
  const mine = offerings.filter(
    (offering) => offering.domain === domain && offering.roles.includes("anchor"),
  );
  const curated = COMMON_KEYS[domain];
  let common: T[] = curated
    ? curated.flatMap((key) => mine.filter((offering) => offering.key === key))
    : mine.slice(0, limit);
  const selected = mine.find((offering) => offering.key === selectedKey);
  if (selected && !common.includes(selected)) {
    common = [...common, selected];
  }
  return { common, more: mine.filter((offering) => !common.includes(offering)) };
};

export const searchOfferings = <T extends OfferingLike>(
  offerings: readonly T[],
  query: string,
): T[] => {
  const needle = query.trim().toLowerCase();
  return offerings.filter(
    (offering) =>
      needle === "" ||
      offering.display_name.toLowerCase().includes(needle) ||
      offering.key.toLowerCase().includes(needle),
  );
};

// --- a late answer must not replace a fresh one -----------------------------------

/**
 * A request counter. The preview is a network round trip fired on every change of
 * anchor, and a slow answer for the previous anchor arriving after a fast answer
 * for the current one would put the wrong "next:" line under the right control.
 */
export const latest = (): { next: () => number; isCurrent: (token: number) => boolean } => {
  let counter = 0;
  return {
    next: () => ++counter,
    isCurrent: (token) => token === counter,
  };
};
