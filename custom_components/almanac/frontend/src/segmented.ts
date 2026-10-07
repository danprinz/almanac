// A choice among a few, drawn so it reads as a choice (D169).
//
// `Days of the week | Day set` and `before | at | after` used to be chip buttons,
// which look like actions. A segmented control is one group, one selection. It is
// a callback component rather than an event emitter because each host holds one
// closure per control, and an event would need its name spelled in two files with
// nothing to say when the spellings drift apart.
//
// Built from `ha-button` (appearance changes with the selection), so it is the
// same control as every other button on the page.

import { LitElement, css, html } from "lit";
import { customElement, property } from "lit/decorators.js";

import { almanacTokens } from "./styles";

export interface SegmentOption {
  id: string;
  label: string;
}

@customElement("almanac-segmented")
export class AlmanacSegmented extends LitElement {
  @property({ attribute: false }) public options: SegmentOption[] = [];

  @property() public selected = "";

  /** The group's accessible name. */
  @property() public label = "";

  @property({ type: Boolean }) public disabled = false;

  /** Called with the id of the option picked. Never called for the one already selected. */
  @property({ attribute: false }) public onSelect: (id: string) => void =
    () => {};

  protected override render() {
    return html`<div role="group" aria-label=${this.label}>
      ${this.options.map(
        (option) => html`<ha-button
          size="s"
          appearance=${option.id === this.selected ? "filled" : "outlined"}
          aria-pressed=${option.id === this.selected ? "true" : "false"}
          ?disabled=${this.disabled}
          @click=${() => {
            if (option.id !== this.selected) {
              this.onSelect(option.id);
            }
          }}
          >${option.label}</ha-button
        >`,
      )}
    </div>`;
  }

  static override styles = [
    almanacTokens,
    css`
      ha-button {
        --ha-button-height: 44px;
      }
      div {
        display: flex;
        flex-wrap: wrap;
        gap: 0.25rem;
      }
    `,
  ];
}

declare global {
  interface HTMLElementTagNameMap {
    "almanac-segmented": AlmanacSegmented;
  }
}
