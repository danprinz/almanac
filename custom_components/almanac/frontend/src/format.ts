// Rendering instants, in the instance's zone rather than the browser's.
//
// A.13 is the engine's version of the problem this file solves at the other end:
// an instant and a wall clock are different things, and code that forgets which
// one it is holding gets the right answer almost always. Here the rule is that
// every instant on the wire is absolute, and every instant on the screen is read
// in `hass.config.time_zone` — the instance's zone, not the viewer's. A schedule
// that fires at candle lighting fires at the building's dusk, and a maintainer
// checking it from another country must see the building's clock or the timeline
// is lying to them.
//
// `Intl.DateTimeFormat` does the whole job, so there is no date library here.

import type { HomeAssistant } from "./ha";

const cache = new Map<string, Intl.DateTimeFormat>();

const formatter = (
  hass: HomeAssistant,
  options: Intl.DateTimeFormatOptions,
): Intl.DateTimeFormat => {
  const zone = hass.config.time_zone;
  const key = `${hass.language}|${zone}|${JSON.stringify(options)}`;
  let found = cache.get(key);
  if (!found) {
    found = new Intl.DateTimeFormat(hass.language, {
      ...options,
      timeZone: zone,
    });
    cache.set(key, found);
  }
  return found;
};

/** `18:18` — the instance's wall clock, to the minute. */
export const clockTime = (hass: HomeAssistant, instant: string): string =>
  formatter(hass, { hour: "2-digit", minute: "2-digit" }).format(
    new Date(instant),
  );

/** `Fri 2 Oct` — enough to tell two occurrences of a weekly rule apart. */
export const dayLabel = (hass: HomeAssistant, instant: string): string =>
  formatter(hass, {
    weekday: "short",
    day: "numeric",
    month: "short",
  }).format(new Date(instant));

/** `Fri 2 Oct, 18:18`. */
export const dayAndTime = (hass: HomeAssistant, instant: string): string =>
  `${dayLabel(hass, instant)}, ${clockTime(hass, instant)}`;

/**
 * The civil day a `start_date` names, read as a date and never as midnight.
 *
 * `start_date` is `date.isoformat()`, and `new Date("2026-10-02")` parses that as
 * **UTC** midnight — which in any zone west of Greenwich renders as the previous
 * day. Appending a midday time and letting it be local is what keeps a day
 * labelled as itself; the value is never compared against an instant, only
 * displayed, so the invented time cannot leak anywhere.
 */
export const dateLabel = (hass: HomeAssistant, day: string): string =>
  formatter(hass, {
    weekday: "short",
    day: "numeric",
    month: "short",
  }).format(new Date(`${day}T12:00:00`));

/** `in 2h 14m`, `4m ago`, `now`. Relative to the instant the caller is about. */
export const relative = (instant: string, at: Date): string => {
  const seconds = Math.round((new Date(instant).getTime() - at.getTime()) / 1000);
  const magnitude = Math.abs(seconds);
  if (magnitude < 45) {
    return "now";
  }
  const spelled = duration(magnitude);
  return seconds > 0 ? `in ${spelled}` : `${spelled} ago`;
};

/**
 * `25h 30m` — D73's compressed gap label, and the only place a duration is
 * spelled. Hours do not roll into days: the flagship gap is 25½ hours and
 * "1d 1h 30m" reads as longer than it is to someone deciding whether a schedule
 * covers one evening or two.
 */
export const duration = (seconds: number): string => {
  const whole = Math.round(Math.abs(seconds));
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  if (hours === 0 && minutes === 0) {
    return `${whole}s`;
  }
  if (hours === 0) {
    return `${minutes}m`;
  }
  if (minutes === 0) {
    return `${hours}h`;
  }
  return `${hours}h ${minutes}m`;
};

/** `−45m`, `+30m`, `0` — an anchor offset, with a true minus sign (D73's labels). */
export const offsetLabel = (seconds: number): string => {
  if (seconds === 0) {
    return "0";
  }
  return `${seconds < 0 ? "−" : "+"}${duration(seconds)}`;
};
