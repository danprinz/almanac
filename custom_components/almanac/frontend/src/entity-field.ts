// One entity-ish field: HA's picker when it is loaded, today's text input when not.
//
// Three call sites used to spell "an entity id" with a plain `<input list>` --
// conditions, desired state, anchors -- and two more spelled "a script". They are
// one control with a different selector, so they are one component (D166).
//
// The fallback is not a degraded mode to be embarrassed by: it is the control that
// was shipping until this task, kept because the picker is lazy, private-API
// loaded (D167) and can fail. A stored value the picker cannot list (an entity
// that no longer exists) is still shown, because `ha-selector`'s entity picker
// renders an unknown id as typed text rather than blanking it.

import { LitElement, html, nothing } from "lit";
import { customElement, property } from "lit/decorators.js";

import { inputValue } from "./form";
import type { HomeAssistant } from "./ha";
import { pickIsChange, selectorFor } from "./pickers";
import type { PickerKind } from "./pickers";
import { almanacForm, almanacText, almanacTokens } from "./styles";

@customElement("almanac-entity-field")
export class AlmanacEntityField extends LitElement {
  @property({ attribute: false }) public hass?: HomeAssistant | undefined;

  /** D167. False renders the plain input. */
  @property({ type: Boolean }) public haReady = false;

  @property() public kind: PickerKind = "entity";

  @property() public label = "Entity";

  /** An entity id (or a script's), or the target selector's value for `target`. */
  @property({ attribute: false }) public value: unknown = "";

  /** The datalist `id` the fallback input points at; the host owns the `<datalist>`. */
  @property() public fallbackList = "";

  @property({ type: Boolean }) public disabled = false;

  /** Whether a value is mandatory. Always passed on, because the picker defaults to true. */
  @property({ type: Boolean }) public required = false;

  /**
   * Called with the picked value: a string for an entity or script, an object for
   * a target. A callback and not an event, for the reason in `segmented.ts`: the
   * host holds a closure per field, and an event would need a name spelled twice.
   */
  @property({ attribute: false }) public onPick: (value: unknown) => void =
    () => {};

  protected override render() {
    if (this.haReady && this.hass) {
      return html`
        <ha-selector
          .hass=${this.hass}
          .selector=${selectorFor(this.kind)}
          .value=${this.value === "" ? undefined : this.value}
          .label=${this.label}
          .required=${this.required}
          .disabled=${this.disabled}
          @value-changed=${(event: CustomEvent<{ value: unknown }>) => {
            event.stopPropagation();
            if (pickIsChange(this.value, event.detail.value)) {
              this.onPick(event.detail.value ?? "");
            }
          }}
        ></ha-selector>
      `;
    }
    return html`
      <label class="field">
        <span>${this.label}</span>
        <input
          type="text"
          class="mono"
          list=${this.fallbackList === "" ? nothing : this.fallbackList}
          .value=${typeof this.value === "string" ? this.value : ""}
          ?disabled=${this.disabled}
          @change=${(event: Event) => this.onPick(inputValue(event).trim())}
        />
      </label>
    `;
  }

  static override styles = [almanacTokens, almanacText, almanacForm];
}

declare global {
  interface HTMLElementTagNameMap {
    "almanac-entity-field": AlmanacEntityField;
  }
}
