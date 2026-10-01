// One payload, edited as JSON text (D152), and not blocking a save when it does
// not parse (D153).
//
// `data`, `fields` and `attributes` are the three places where almanac stores a
// mapping whose key set belongs to somebody else. `_service_data` says so in as
// many words — *"the set of valid keys belongs to the target service, not to us,
// and a whitelist here would go stale every time an integration adds a field"* —
// and that is exactly why there is no widget here. A field-by-field editor would
// be an editor for the keys it happened to know, which is D149's data-loss bug
// rebuilt at a smaller scale: render the three keys we recognise, drop the
// fourth, and D140's structural diff sends the truncation as a change.
//
// JSON text is the one representation that is lossless for a shape nobody owns.
// It is also, honestly, the worst control in this editor — so it is the only one
// that gets a monospace font, a resize handle and its own error line.
//
// **What happens to text that does not parse, and why Save is not blocked on
// it.** The text stays as typed, the border goes red and the line underneath
// says what is wrong and that the payload is therefore unchanged. Nothing is
// destroyed: the draft still holds the last payload that parsed, and that is
// what a save writes. Blocking Save would need the editor to track the validity
// of every payload on screen through rule additions, removals and selection
// changes, and a counter that drifts out of step with the screen either blocks a
// save for no visible reason or allows one the screen says is broken. A visible
// red field next to an unchanged payload is the smaller failure, and it is the
// one the user can see.

import { LitElement, html, nothing } from "lit";
import { customElement, property, state } from "lit/decorators.js";

import { formatMapping, looksLikeATemplate, parseMapping } from "./draft";
import { inputValue } from "./form";
import { almanacForm, almanacText, almanacTokens } from "./styles";

export const MAPPING_CHANGED = "almanac-mapping-changed";

export interface MappingChangedDetail {
  /** Whatever the host set `name` to. See the property's own note. */
  name: string;
  value: Record<string, unknown>;
}

@customElement("almanac-mapping")
export class AlmanacMapping extends LitElement {
  @property({ attribute: false }) public value: Record<string, unknown> = {};

  @property() public label = "Payload";

  @property({ type: Boolean }) public disabled = false;

  /** A one-line note under the field, for the shape that is legal but odd. */
  @property() public hint = "";

  /**
   * How the host tells its payloads apart, echoed back in the event.
   *
   * There are up to a dozen of these on screen at once — a `data` per service
   * call, a `fields` per script, an `attributes` per desired entity — and the
   * host listens for one event on itself rather than binding a handler to each
   * element. That is `editor.ts`'s own pattern for `STAGE_SELECTED`, and it is
   * there for the reason given beside it: Lit's `@name=` binding cannot take a
   * constant, so a per-element binding would spell the event's name a second
   * time in a template, where nothing compares it to `MAPPING_CHANGED`.
   *
   * The string's shape is the host's business. Both hosts use `key:index`.
   */
  @property() public name = "";

  /**
   * The text as typed, or null when the field is showing `value`.
   *
   * Two states rather than one because the text and the payload are not the same
   * thing: `{ }` and `{}` are the same payload, and re-serialising on every
   * keystroke would move the caret and delete the user's whitespace as they
   * typed it. Null means "nothing typed since the payload last arrived", which
   * is what lets an external change — a rule switched under the panel — reach
   * the field at all.
   */
  @state() private _typed: string | null = null;

  @state() private _error: string | null = null;

  protected override willUpdate(changed: Map<string, unknown>): void {
    // A different payload arriving from above discards the local buffer. That is
    // the right way round: the only thing that changes `value` is a write this
    // element asked for, or a switch to a different action, and in the second
    // case the buffer belongs to the action that is no longer on screen.
    if (changed.has("value")) {
      this._typed = null;
      this._error = null;
    }
  }

  protected override render() {
    const text = this._typed ?? formatMapping(this.value);
    return html`
      <label class="field">
        <span>${this.label}</span>
        <textarea
          class=${this._error === null ? "" : "bad"}
          spellcheck="false"
          placeholder="{}"
          .value=${text}
          ?disabled=${this.disabled}
          aria-invalid=${this._error === null ? "false" : "true"}
          @input=${this._onInput}
        ></textarea>
      </label>
      ${this._error === null
        ? nothing
        : html`<p class="problem">
            ${this._error} The payload is left as it was.
          </p>`}
      ${this._error === null && looksLikeATemplate(this.value)
        ? html`<p class="muted">
            This payload contains <span class="mono">{{ }}</span>, which almanac
            sends as literal text (D58). It is not rendered as a template.
          </p>`
        : nothing}
      ${this.hint === "" ? nothing : html`<p class="muted">${this.hint}</p>`}
    `;
  }

  private _onInput = (event: Event): void => {
    const typed = inputValue(event);
    this._typed = typed;
    const parsed = parseMapping(typed);
    if (!parsed.ok) {
      this._error = parsed.error;
      return;
    }
    this._error = null;
    this.dispatchEvent(
      new CustomEvent<MappingChangedDetail>(MAPPING_CHANGED, {
        detail: { name: this.name, value: parsed.value },
        bubbles: true,
        composed: true,
      }),
    );
  };

  static override styles = [almanacTokens, almanacText, almanacForm];
}

declare global {
  interface HTMLElementTagNameMap {
    "almanac-mapping": AlmanacMapping;
  }
}
