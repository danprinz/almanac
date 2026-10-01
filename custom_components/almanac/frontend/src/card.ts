// The card's entry point. D70, and nothing else.
//
// `frontend.add_extra_js_url` injects this file into the shared `index.html`, so
// it is fetched and executed on **every** Home Assistant page — the map, the
// logbook, a dashboard with no almanac card anywhere on it. What this file costs
// is therefore paid by every page load in the instance, which is why it holds
// only two things: the `customElements.define` of a placeholder, and the
// `window.customCards` entry the card picker reads.
//
// Note what is *not* imported. Lit is not imported. `./wire` is not imported,
// and it is types-only. `./api` is not imported. The stub extends `HTMLElement`
// directly so that the lazy chunk carries the whole framework, rather than the
// entry carrying it and the chunk carrying the card drawn with it.
//
// D70 also records the ordering reason: `add_extra_js_url` is backed by a set, so
// nothing guarantees when this file runs relative to another integration's extra
// JS. A stub with no dependencies makes that a non-question instead of an
// invariant to maintain.

import type { HomeAssistant, LovelaceCard, LovelaceCardConfig } from "./ha";

const CARD_TAG = "almanac-card";
const IMPL_TAG = "almanac-card-body";

/**
 * The placeholder. It owns the element lifecycle and forwards to the real card
 * once that has loaded; until then it is an empty element, which is what a card
 * that has not finished loading should look like.
 *
 * `setConfig` is the trigger because it is the first thing Lovelace calls and
 * the only call that means "this card is actually on a dashboard". It is also
 * the one method that is allowed to throw synchronously — Lovelace catches it
 * and renders a configuration error — so the shallow check below stays here and
 * everything that needs the schema happens in the body element.
 */
class AlmanacCardStub extends HTMLElement implements LovelaceCard {
  private _config?: LovelaceCardConfig;
  private _hass?: HomeAssistant;
  private _body?: LovelaceCard;
  private _loading?: Promise<void>;

  setConfig(config: LovelaceCardConfig): void {
    if (!config || typeof config !== "object") {
      throw new Error("almanac: card configuration is missing");
    }
    this._config = config;
    if (this._body) {
      this._body.setConfig(config);
      return;
    }
    this._loading ??= this._load();
  }

  set hass(hass: HomeAssistant) {
    this._hass = hass;
    if (this._body) {
      this._body.hass = hass;
    }
  }

  get hass(): HomeAssistant | undefined {
    return this._hass;
  }

  /**
   * Lovelace asks for a height before the body exists, so the stub answers for
   * it. Three is the single-schedule height the body settles at, and a wrong
   * first answer costs one masonry reflow. What it must not do is await the
   * chunk: a page that merely *has* a card would then block on the import this
   * file exists to defer.
   */
  getCardSize(): number | Promise<number> {
    return this._body?.getCardSize?.() ?? 3;
  }

  private async _load(): Promise<void> {
    await import("./card-body");
    const body = document.createElement(IMPL_TAG) as LovelaceCard;
    if (this._config) {
      body.setConfig(this._config);
    }
    if (this._hass) {
      body.hass = this._hass;
    }
    this._body = body;
    this.replaceChildren(body);
  }
}

if (!customElements.get(CARD_TAG)) {
  customElements.define(CARD_TAG, AlmanacCardStub);
}

// `??=` rather than `=`: the array belongs to whichever custom card loaded
// first, and overwriting it would unregister theirs.
window.customCards ??= [];
window.customCards.push({
  type: CARD_TAG,
  name: "almanac",
  // D79 — the row's payload is the generated summary, so the picker says what
  // the card shows rather than naming the fields it was built from.
  description: "Schedules, what they will do next, and when.",
  preview: true,
  documentationURL: "https://github.com/danprinz/almanac",
});
