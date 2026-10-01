// The two elements Home Assistant creates by name. D70, and nothing else.
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
//
// **D160 put a second element here, and it is here for a stronger reason than
// the card.** Core's `more-info-content` reads an entity's
// `custom_ui_more_info` attribute and renders that tag name, with no loader of
// any kind: the element is created, and if nothing has defined it the dialog
// body is empty. There is no hook to register a lazy module against and no
// event to answer. So the definition has to already exist by the time any user
// opens any dialog, which means it has to be in this file — the one almanac
// ships on every page. The stub shape is then the same trade as the card's:
// the definition is free, and Lit arrives only once a dialog actually opens.
//
// Neither tag is imported from a shared constants module, because every import
// here would have to be `import type` to satisfy the test that guards D70.
// They are literals, checked against `const.py` by
// `tests/test_frontend_assets.py` — the same arrangement D69 already uses for
// the bundle URLs, for the same reason: two sides of a contract that no build
// step and no run time can compare.

import type { HassEntity, HomeAssistant, LovelaceCard, LovelaceCardConfig } from "./ha";

const CARD_TAG = "almanac-card";
const IMPL_TAG = "almanac-card-body";

const MORE_INFO_TAG = "almanac-more-info";
const MORE_INFO_IMPL_TAG = "almanac-more-info-body";

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

/**
 * What the lazy half of the dialog has to accept.
 *
 * Declared here rather than in `ha.ts` because it is almanac's own element and
 * not a description of upstream — and declared at all because the stub must not
 * import the module that defines it.
 *
 * `hass` and `stateObj` are the two of core's five properties almanac uses. The
 * others (`entry`, `editMode`, `data`) are assigned to the stub by core's
 * directive and ignored; an unknown property on an `HTMLElement` is a plain
 * assignment, so ignoring them costs nothing and means a sixth appearing
 * upstream cannot break this.
 */
interface AlmanacMoreInfoBody extends HTMLElement {
  hass?: HomeAssistant | undefined;
  stateObj?: HassEntity | undefined;
}

/**
 * The dialog-body placeholder, and the whole of D160's cost on a cold page.
 *
 * `stateObj` is the trigger rather than `hass`, because it is the property that
 * means "there is an entity whose dialog is open". `hass` is set on every state
 * change in the instance and would make this element load its chunk the moment
 * anything anywhere changed, which is the opposite of what the stub is for.
 *
 * Both properties are forwarded after the body exists, since core keeps setting
 * them for as long as the dialog is open — `hass` on every tick, `stateObj`
 * whenever the schedule is armed or disarmed.
 */
class AlmanacMoreInfoStub extends HTMLElement {
  private _hass?: HomeAssistant;
  private _stateObj?: HassEntity;
  private _body?: AlmanacMoreInfoBody;
  private _loading?: Promise<void>;

  set hass(hass: HomeAssistant) {
    this._hass = hass;
    if (this._body) {
      this._body.hass = hass;
    }
  }

  get hass(): HomeAssistant | undefined {
    return this._hass;
  }

  set stateObj(stateObj: HassEntity) {
    this._stateObj = stateObj;
    if (this._body) {
      this._body.stateObj = stateObj;
      return;
    }
    this._loading ??= this._load();
  }

  get stateObj(): HassEntity | undefined {
    return this._stateObj;
  }

  private async _load(): Promise<void> {
    await import("./more-info-body");
    const body = document.createElement(
      MORE_INFO_IMPL_TAG,
    ) as AlmanacMoreInfoBody;
    if (this._hass) {
      body.hass = this._hass;
    }
    if (this._stateObj) {
      body.stateObj = this._stateObj;
    }
    this._body = body;
    this.replaceChildren(body);
  }
}

if (!customElements.get(CARD_TAG)) {
  customElements.define(CARD_TAG, AlmanacCardStub);
}

if (!customElements.get(MORE_INFO_TAG)) {
  customElements.define(MORE_INFO_TAG, AlmanacMoreInfoStub);
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
