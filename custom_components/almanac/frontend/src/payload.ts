// Splitting a stored payload into what a service's schema can render and what it
// cannot -- and putting it back together without losing either half.
//
// D166 (which supersedes D152, the "payload is JSON text" decision) renders a
// service's own `fields` as Home Assistant selectors. D152 refused that because a
// form built from the schema cannot represent a key the schema does not list or a
// service that is not loaded, and D17 says a schedule may name both. The answer is
// not to drop the form but to give the remainder somewhere to live: the form shows
// the keys the schema knows, and the JSON box shows every other key verbatim. The
// saved mapping is the two halves merged, so the round trip stays the identity
// that made D152 safe.
//
// No imports of any kind (D132): `node --test` runs this file with the types
// stripped and nothing resolved, and `tests/test_frontend_assets.py` checks it.

/** One entry of `hass.services[d][s].fields`, as the frontend delivers it. */
export interface RawField {
  name?: string;
  description?: string;
  required?: boolean;
  selector?: Record<string, unknown>;
  fields?: Record<string, RawField>;
}

/** A field a form can actually render: it has a selector, and a label. */
export interface FieldDef {
  key: string;
  name: string;
  description: string;
  required: boolean;
  selector: Record<string, unknown>;
}

export interface ServiceLike {
  fields: Record<string, RawField>;
}

/**
 * The renderable fields, sections flattened.
 *
 * A `fields` sub-object marks a section (the light services group their advanced
 * keys that way); its children are real payload keys, so they are lifted out in
 * order. A field with no selector is dropped *from the form* only -- its key is
 * still in the stored data, and `partitionPayload` is told which keys the form
 * owns, so it falls into the JSON box rather than disappearing.
 */
export const flattenFields = (
  fields: Record<string, RawField> | undefined,
): FieldDef[] => {
  const out: FieldDef[] = [];
  for (const [key, field] of Object.entries(fields ?? {})) {
    if (field.fields) {
      out.push(...flattenFields(field.fields));
      continue;
    }
    if (!field.selector) {
      continue;
    }
    out.push({
      key,
      name: field.name ?? key,
      description: field.description ?? "",
      required: field.required === true,
      selector: field.selector,
    });
  }
  return out;
};

/**
 * The service a stored name refers to, or `undefined`.
 *
 * `undefined` and not an empty service, on purpose: an absent entry means the
 * integration is unloaded or the name is misspelt, and the editor says so in
 * words. An empty `fields` object means "this service takes nothing", which is a
 * different sentence. A script is `script.<object_id>`, the same lookup.
 */
export const lookupService = (
  services: Record<string, Record<string, ServiceLike>> | undefined,
  name: string,
): ServiceLike | undefined => {
  const dot = name.indexOf(".");
  if (dot <= 0 || dot === name.length - 1) {
    return undefined;
  }
  return services?.[name.slice(0, dot)]?.[name.slice(dot + 1)];
};

export interface Partition {
  /** Keys the form owns. */
  known: Record<string, unknown>;
  /** Everything else, for the JSON box. */
  other: Record<string, unknown>;
}

export const partitionPayload = (
  data: Record<string, unknown>,
  schemaKeys: readonly string[],
): Partition => {
  const owned = new Set(schemaKeys);
  const known: Record<string, unknown> = {};
  const other: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(data)) {
    (owned.has(key) ? known : other)[key] = value;
  }
  return { known, other };
};

/**
 * The two halves, back together. The form wins a collision: the box is for keys
 * the form cannot show, so a key present in both means the user typed one the
 * form already owns, and "the form is right" is the only answer that does not
 * depend on which half was edited last.
 */
export const mergePayload = (
  known: Record<string, unknown>,
  other: Record<string, unknown>,
): Record<string, unknown> => ({ ...other, ...known });

/**
 * One form field's new value. `undefined` and the empty string remove the key --
 * that is what clearing a field means -- while `false` and `0` are values.
 * Returns a new object; the argument is never mutated.
 */
export const withFieldValue = (
  known: Record<string, unknown>,
  key: string,
  value: unknown,
): Record<string, unknown> => {
  const next = { ...known };
  if (value === undefined || value === "") {
    delete next[key];
  } else {
    next[key] = value;
  }
  return next;
};
