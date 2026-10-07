// A searchable service picker that still accepts a service Home Assistant has not
// got (D17: a schedule may name a service whose integration is unloaded).
//
// Not an HA element: none searches `hass.services`, and none would let the user
// keep a name that is not in the list. So: one text input, and under it a list of
// the loaded services that contain what was typed. Picking one fills the input;
// typing something else and leaving commits it as typed. It is not gated on
// `haReady`, because `hass.services` is a plain property, there once `hass` is.

import { LitElement, css, html, nothing } from "lit";
import { customElement, property, state } from "lit/decorators.js";

import { inputValue } from "./form";
import type { HomeAssistant } from "./ha";
import { filterServices, serviceNames } from "./pickers";
import { almanacForm, almanacText, almanacTokens } from "./styles";

const SHOWN = 8;

@customElement("almanac-service-field")
export class AlmanacServiceField extends LitElement {
  @property({ attribute: false }) public hass?: HomeAssistant | undefined;

  @property() public value = "";

  @property({ type: Boolean }) public disabled = false;

  @property({ attribute: false }) public onPick: (service: string) => void = () => {};

  @state() private _query: string | undefined;

  @state() private _open = false;

  private _commit(text: string): void {
    this._open = false;
    this._query = undefined;
    const next = text.trim();
    // An event whose value has not changed must cause no write.
    if (next !== this.value) {
      this.onPick(next);
    }
  }

  protected override render() {
    const text = this._query ?? this.value;
    const matches = this._open
      ? filterServices(serviceNames(this.hass?.services), this._query ?? "", SHOWN)
      : [];
    return html`
      <label class="field">
        <span>Service</span>
        <input
          type="text"
          class="mono"
          autocomplete="off"
          placeholder="light.turn_on"
          .value=${text}
          ?disabled=${this.disabled}
          @focus=${() => (this._open = true)}
          @input=${(event: Event) => {
            this._query = inputValue(event);
            this._open = true;
          }}
          @change=${(event: Event) => this._commit(inputValue(event))}
          @blur=${() => {
            this._open = false;
            this._query = undefined;
          }}
          @keydown=${(event: KeyboardEvent) => {
            if (event.key === "Escape") {
              this._open = false;
            }
          }}
        />
      </label>
      ${matches.length > 0
        ? html`<ul class="matches" role="listbox">
            ${matches.map(
              (name) => html`<li
                role="option"
                @mousedown=${(event: Event) => {
                  // Keeps focus in the input so the blur does not commit the
                  // half-typed text and re-render this list away before the click.
                  event.preventDefault();
                  this._commit(name.id);
                }}
              >
                ${name.id}
              </li>`,
            )}
          </ul>`
        : nothing}
    `;
  }

  static override styles = [
    almanacTokens,
    almanacText,
    almanacForm,
    css`
      .matches {
        list-style: none;
        margin: 0;
        padding: 0;
        max-height: 12rem;
        overflow-y: auto;
        border: 1px solid var(--almanac-rail);
        border-radius: 8px;
      }
      .matches li {
        padding: var(--almanac-gap-sm) var(--almanac-gap-md);
        min-height: 2.5rem;
        display: flex;
        align-items: center;
        cursor: pointer;
        font-family: var(--ha-font-family-code, ui-monospace, monospace);
      }
      .matches li:hover {
        background: var(--secondary-background-color);
      }
    `,
  ];
}
