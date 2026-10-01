// The one identifier in this repository that is wrong silently.
//
// Everything else the frontend gets wrong announces itself. A missing property
// is a type error, a bad websocket command is a rejected call with a message, a
// mis-shaped template throws. `openMoreInfo` has no failure mode at all:
// `dispatchEvent` returns `true` whether or not anything is listening, logs
// nothing either way, and the user sees a button that does not work. There is no
// run-time check to add, which is why D159 made it a single spelling in a single
// file and why that file has a test of its own.
//
// What the test can and cannot establish. It cannot establish that
// `hass-more-info` is the name Home Assistant listens for -- nothing inside this
// repository can, and the verification that it is lives in `src/moreinfo.ts`'s
// header as a presence search against the installed `home-assistant-frontend`
// bundle. What it establishes is the other half: that the value, the detail key
// and the two flags do not *drift* from what was verified. A refactor that
// renames the detail key to `entity_id` to match the Python side, or drops
// `composed` because "bubbles is surely enough", is exactly the change this
// catches, and each of those would otherwise ship as a button that does nothing.
//
// `composed` is the one worth stating plainly: every almanac surface renders
// into a shadow root, and the listener is on an ancestor outside it. A
// `bubbles`-only event stops at the boundary, which looks identical to a
// misspelled name.

import { test } from "node:test";
import assert from "node:assert/strict";

import { MORE_INFO_EVENT, openMoreInfo } from "../src/moreinfo.ts";
import type { MoreInfoDetail } from "../src/moreinfo.ts";

/**
 * The smallest thing `openMoreInfo` will accept: it dispatches and nothing else,
 * so an object with a `dispatchEvent` is a sufficient target. Which is itself
 * the point of taking an `EventTarget` rather than an `HTMLElement` -- the
 * function is testable without a DOM because it does nothing a DOM is needed
 * for.
 */
const recorder = (): { target: EventTarget; events: Event[] } => {
  const events: Event[] = [];
  return {
    target: {
      dispatchEvent(event: Event): boolean {
        events.push(event);
        return true;
      },
    } as EventTarget,
    events,
  };
};

test("the event name is the one that was verified, character for character", () => {
  // Spelled out rather than compared to the import, so that a change to
  // `moreinfo.ts` fails here instead of agreeing with itself.
  assert.equal(MORE_INFO_EVENT, "hass-more-info");
});

test("the detail carries the entity under core's key, not the schema's", () => {
  const { target, events } = recorder();

  openMoreInfo(target, "switch.almanac_shabbat");

  assert.equal(events.length, 1);
  const event = events[0] as CustomEvent<MoreInfoDetail>;
  assert.equal(event.type, "hass-more-info");
  // `entityId`, not `entity_id`. The Python half of almanac spells every key in
  // snake case and the temptation to "fix" this one is the whole reason it is
  // asserted.
  assert.deepEqual(event.detail, { entityId: "switch.almanac_shabbat" });
});

test("the event both bubbles and crosses the shadow boundary", () => {
  const { target, events } = recorder();

  openMoreInfo(target, "light.kitchen");

  const event = events[0]!;
  // Neither of these is a default for `CustomEvent`, and neither is optional:
  // the listener sits on an ancestor element, on the far side of a shadow root.
  assert.equal(event.bubbles, true);
  assert.equal(event.composed, true);
  // Not cancellable, because nothing in almanac inspects the return value and
  // an event a caller could suppress would be a second silent failure.
  assert.equal(event.cancelable, false);
});

test("a fresh event is built per call", () => {
  const { target, events } = recorder();

  openMoreInfo(target, "switch.a");
  openMoreInfo(target, "switch.b");

  assert.equal(events.length, 2);
  assert.notEqual(events[0], events[1]);
  assert.equal((events[1] as CustomEvent<MoreInfoDetail>).detail.entityId, "switch.b");
});
