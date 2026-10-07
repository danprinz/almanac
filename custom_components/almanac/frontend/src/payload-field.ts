// A payload edited as a form where the service describes itself, and as JSON text
// for everything the form cannot name (D166, superseding D152).
//
// D152 (a payload is JSON text) rejected a form built from `hass.services`
// because such a form cannot show a key it has not heard of, or a service whose
// integration is not loaded (D17), and would silently drop both on save. This
// component keeps D152's guarantee and adds the convenience on top of it: the
// JSON box is **always** present for the keys the schema does not cover, and the
// saved mapping is the form's values merged with the box's. A form is the
// convenience; the box is the guarantee.
//
// Without Home Assistant's elements (D167) there are no form fields and the whole
// payload is "other", which is the single JSON box that shipped before.

import { LitElement, html, nothing } from "lit";
import { customElement, property } from "lit/decorators.js";

import type { HomeAssistant } from "./ha";
import "./mapping";
import { MAPPING_CHANGED } from "./mapping";
import type { MappingChangedDetail } from "./mapping";
import {
  fieldIsChange,
  flattenFields,
  lookupService,
  mergePayload,
  partitionPayload,
  withFieldValue,
} from "./payload";
import type { FieldDef } from "./payload";
import { almanacForm, almanacText, almanacTokens } from "./styles";

type Mapping = Record<string, unknown>;

@customElement("almanac-payload")
export class AlmanacPayload extends LitElement {
  @property({ attribute: false }) public hass?: HomeAssistant | undefined;

  /** D167. False renders only the JSON box. */
  @property({ type: Boolean }) public haReady = false;

  /** The host's tag (D157): `data:3`, `fields:0`, `attributes:1`. Echoed back untouched. */
  @property() public name = "";

  @property() public label = "Data";

  /** A one-line note under the JSON box, passed straight to it. */
  @property() public hint = "";

  /** The stored mapping. */
  @property({ attribute: false }) public value: Mapping = {};

  /** `light.turn_on`, or `script.<object_id>` for a script's fields. Empty for none. */
  @property() public serviceName = "";

  @property({ type: Boolean }) public disabled = false;

  public override connectedCallback(): void {
    super.connectedCallback();
    // On the shadow root, not on `this`: the inner `almanac-mapping`'s event is
    // composed, and listening on the host would also hear this element's own
    // re-emission. A `@` binding would spell the event name a second time in a
    // template, where nothing compares it to `MAPPING_CHANGED`.
    this.renderRoot.addEventListener(MAPPING_CHANGED, this._onOther as EventListener);
  }

  public override disconnectedCallback(): void {
    super.disconnectedCallback();
    this.renderRoot.removeEventListener(MAPPING_CHANGED, this._onOther as EventListener);
  }

  /** The service's renderable fields, or `undefined` when there is no schema to show. */
  private _schema(): FieldDef[] | undefined {
    if (!this.haReady || !this.hass || this.serviceName === "") {
      return undefined;
    }
    const service = lookupService(this.hass.services, this.serviceName);
    return service ? flattenFields(service.fields) : undefined;
  }

  private _emit(next: Mapping): void {
    this.dispatchEvent(
      new CustomEvent<MappingChangedDetail>(MAPPING_CHANGED, {
        detail: { name: this.name, value: next },
        bubbles: true,
        composed: true,
      }),
    );
  }

  private _keys(): string[] {
    return (this._schema() ?? []).map((field) => field.key);
  }

  /**
   * The inner box reports only the "other" keys. Re-report the whole mapping
   * under the host's name, and stop the inner event so the host never sees a
   * half-payload tagged `other`.
   */
  private _onOther = (event: CustomEvent<MappingChangedDetail>): void => {
    event.stopPropagation();
    const { known } = partitionPayload(this.value, this._keys());
    this._emit(mergePayload(known, event.detail.value));
  };

  private _onField(field: FieldDef, known: Mapping, other: Mapping, value: unknown): void {
    // `ha-selector` may echo the value it was handed, on mount or after an inner
    // picker normalises it. That is not an edit and must not touch the draft.
    if (!fieldIsChange(known, field.key, value)) {
      return;
    }
    this._emit(mergePayload(withFieldValue(known, field.key, value), other));
  }

  protected override render() {
    const schema = this._schema();
    const fields = schema ?? [];
    const { known, other } = partitionPayload(this.value, this._keys());
    const unloaded =
      this.haReady && this.hass !== undefined && this.serviceName !== "" && schema === undefined;
    const box = html`<almanac-mapping
      name="other"
      .label=${fields.length > 0 ? "Other data (JSON)" : this.label}
      .hint=${this.hint}
      .value=${other}
      ?disabled=${this.disabled}
    ></almanac-mapping>`;
    return html`
      ${fields.map(
        (field) => html`
          <ha-selector
            .hass=${this.hass}
            .selector=${field.selector}
            .value=${known[field.key]}
            .label=${field.name}
            .helper=${field.description}
            .required=${field.required}
            .disabled=${this.disabled}
            @value-changed=${(event: CustomEvent<{ value: unknown }>) => {
              event.stopPropagation();
              this._onField(field, known, other, event.detail.value);
            }}
          ></ha-selector>
        `,
      )}
      ${unloaded
        ? html`<p class="muted">
            This service isn't loaded right now, so its fields can't be shown.
          </p>`
        : nothing}
      ${fields.length === 0
        ? box
        : html`<details ?open=${Object.keys(other).length > 0}>
            <summary>Other data (JSON)</summary>
            ${box}
          </details>`}
    `;
  }

  static override styles = [almanacTokens, almanacText, almanacForm];
}

declare global {
  interface HTMLElementTagNameMap {
    "almanac-payload": AlmanacPayload;
  }
}
