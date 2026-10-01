// The conversions between what a native input holds and what the schema stores.
//
// Four components now read the same `<input>` elements, so these moved out of
// `editor.ts` at step 9e rather than being copied into each one. They are the
// whole of the editor's "no Home Assistant form component" cost: `ha-textfield`
// and friends are all real and none of them is typed in `ha.ts`, so every field
// is a native element and every native element hands back a string.
//
// No imports, deliberately — including no `import type`. That keeps the file
// readable by `node --test` after type-stripping, which is what lets
// `test/form.test.ts` exist; it is not in
// `tests/test_frontend_assets.py`'s pure-modules list because that test asserts
// each named file *has* imports and all of them are type-only, and a file with
// none would fail it for being purer than the rule anticipated.

/** The typed value of whatever fired an `input` or `change`. */
export const inputValue = (event: Event): string =>
  (event.target as HTMLInputElement | HTMLSelectElement).value;

/** Whether whatever fired a `change` is a ticked checkbox. */
export const inputChecked = (event: Event): boolean =>
  (event.target as HTMLInputElement).checked;

/**
 * `<input type="time">` gives `HH:MM` with no seconds unless the user types
 * them, and D40's stored form is `HH:MM:SS`. The schema's `_clock_time` accepts
 * both; normalising here means the draft's value and the stored value are the
 * same string, so D140's diff does not see a change that is only a spelling.
 */
export const clockValue = (typed: string): string =>
  typed.length === 5 ? `${typed}:00` : typed;

/**
 * Minutes, as typed, to stored seconds.
 *
 * Minutes because that is the unit every offset in this domain is spoken in —
 * "forty-five minutes before candle lighting" — and the fields that use it are
 * `step="any"`, because the alternative, `step="1"`, silently rounds a stored
 * thirty-second offset away the first time the field is touched. `Math.round`
 * on the way back keeps the stored value an integer number of seconds, which is
 * what the schema takes.
 */
export const secondsFrom = (typed: string): number => {
  const minutes = Number(typed);
  return Number.isFinite(minutes) ? Math.round(minutes * 60) : 0;
};

/** Stored seconds back to the minutes the field shows. */
export const minutesOf = (seconds: number): number => seconds / 60;

/**
 * A whole number as typed, falling back to a given value.
 *
 * The fallback rather than `NaN` or zero because every caller is a field the
 * schema gives a `Range(min=1)`: an occurrence count, a deadline, a timeout. A
 * field cleared mid-edit would otherwise store a number the save refuses, and
 * the user would be told about a field they were in the middle of typing.
 */
export const countFrom = (typed: string, fallback: number): number => {
  const parsed = Number(typed);
  return Number.isFinite(parsed) && parsed >= 1 ? Math.round(parsed) : fallback;
};

/**
 * A list of ids, as one comma-separated field and back.
 *
 * `_TARGET_SCHEMA` stores each of the five selectors as a list of strings, and
 * a list needs either a repeating control or a separator. The separator is the
 * cheaper honest choice here: ids contain no commas — `cv.string` would allow
 * one, but an entity, device, area, floor or label id in Home Assistant is a
 * slug or a hex handle, and neither admits a comma — so the round trip is
 * lossless and the field shows exactly what is stored.
 *
 * Empty entries are dropped rather than stored, because `_target` refuses a
 * target whose every selector is empty and `""` is not an id of anything.
 */
export const idList = (typed: string): string[] =>
  typed
    .split(",")
    .map((id) => id.trim())
    .filter((id) => id !== "");

export const idText = (ids: readonly string[]): string => ids.join(", ");
