// The one place in this repository that spells core's more-info event.
//
// **Why a module for one constant.** The event name is a verbatim third-party
// identifier, which is the single class of fact `CLAUDE.md` says this
// environment cannot certify — and this one fails *silently* when it is wrong.
// `dispatchEvent` with a name nobody listens for returns `true`, logs nothing
// and opens nothing. There is no error to find, no stack to read, and no check
// in the repository that would notice. D159 is the decision to keep it in one
// file for that reason: one spelling, verified once, reachable from every
// caller, and `tests/test_frontend_assets.py` asserts that no other file under
// `src/` contains the literal.
//
// **How it was verified, which matters more than the value.** Not from memory
// and not from a quote. `home-assistant-frontend` 20260826.7 is installed in
// this repository's `.venv`, and its built bundle was searched for the
// candidate strings:
//
//   - `hass-more-info` is present in dozens of `frontend_latest/*.js` chunks,
//     and the dispatch sites read `"hass-more-info",{entityId:` — which is the
//     detail key, confirmed by the same search.
//   - the listener is registered as `this.addEventListener("hass-more-info"`
//     inside a `firstUpdated`, so it sits on an *ancestor* element rather than
//     on the document. The event therefore has to both bubble and cross a
//     shadow boundary.
//   - core's own `fireEvent` helper builds the event with
//     `bubbles: void 0 === opts.bubbles || opts.bubbles` and the same shape for
//     `composed`, so both default to `true` inside core and both are what a
//     caller outside it has to set explicitly.
//
// A presence search is the strongest check available here, and it is the right
// one: `CLAUDE.md` lists "presence or absence of a symbol" among the facts to
// trust in practice, because a one-token rewrite of rendered content cannot
// make a string appear in a file that does not contain it.
//
// No imports, type-only included — the same rule `form.ts` is held to, and for
// the same reason it was written down there: a module whose whole job is to be
// the single site of one fact should not be able to grow a graph.

/**
 * The event that opens Home Assistant's more-info dialog.
 *
 * Verified against `home-assistant-frontend` 20260826.7 as above. If this is
 * ever wrong, the symptom is that every almanac link into another entity does
 * nothing at all — so re-run the presence search before suspecting anything
 * else.
 */
export const MORE_INFO_EVENT = "hass-more-info";

/** What `MORE_INFO_EVENT` carries. One key, spelled as core spells it. */
export interface MoreInfoDetail {
  entityId: string;
}

/**
 * Ask Home Assistant to open `entityId`'s dialog.
 *
 * `target` is the element the event is dispatched from, and it has to be in the
 * document: the listener is on an ancestor, so an element that has not been
 * attached yet dispatches into nothing. In practice this is always `this` from
 * inside a `@click`, which is attached by definition.
 *
 * `bubbles` and `composed` are both required and neither is a default. Every
 * almanac surface renders into a shadow root, and an event that is not
 * `composed` stops at that boundary — which looks exactly like a misspelled
 * event name, and is the mistake this function exists to make unrepeatable.
 */
export const openMoreInfo = (target: EventTarget, entityId: string): void => {
  target.dispatchEvent(
    new CustomEvent<MoreInfoDetail>(MORE_INFO_EVENT, {
      detail: { entityId },
      bubbles: true,
      composed: true,
    }),
  );
};
