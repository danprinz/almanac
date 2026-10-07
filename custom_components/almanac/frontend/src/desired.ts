// A `during` rule's desired state: what is true while the interval lasts.
//
// **This is not a list of actions, and the difference is the whole of D28.** An
// action fires once; a desired state is a claim the engine keeps true, by
// reconciling whatever has drifted at every tick it owns the entity. So the
// controls here describe a *state* — this entity, that state, these attributes —
// and there is no "and then" ordering, because nothing here happens in sequence.
//
// `override` is the escape hatch for the case the claim cannot express: a device
// whose own integration needs one call to say several things at once, which is
// `../smartir-atomicity/`'s whole subject. It is a list of actions, run instead
// of the per-entity reconciliation, and it is presented last and explained
// rather than offered first.
//
// **An entity row has to name a state or some attributes.** `_desired_entity`
// refuses a row that names neither, and that refusal is why `newDesiredEntity`
// leaves `state` as null rather than `""`: the empty string *is* a state, so it
// would pass the schema and then ask `async_reproduce_state` for a state no
// entity has. The field here is therefore blank-means-null, and `problems()`
// says so before a save rather than after one.

import { LitElement, html, nothing } from "lit";
import { customElement, property } from "lit/decorators.js";

import "./actions";
import { ACTIONS_CHANGED } from "./actions";
import type { ActionsChangedDetail } from "./actions";
import { newDesiredEntity, newDesiredState } from "./draft";
import "./entity-field";
import { inputValue } from "./form";
import type { HomeAssistant } from "./ha";
import { MAPPING_CHANGED } from "./mapping";
import type { MappingChangedDetail } from "./mapping";
import "./mapping";
import type { StoredDesiredEntity, StoredDesiredState } from "./stored";
import { almanacForm, almanacHitTarget, almanacText, almanacTokens } from "./styles";

export const DESIRED_CHANGED = "almanac-desired-changed";

export interface DesiredChangedDetail {
  /** Whatever the host set `name` to. See the property's own note. */
  name: string;
  state: StoredDesiredState | null;
}

@customElement("almanac-desired")
export class AlmanacDesired extends LitElement {
  /** Null is a real value: a rule that only runs actions has no desired state. */
  @property({ attribute: false }) public state: StoredDesiredState | null = null;

  @property({ type: Boolean }) public disabled = false;

  /** The heading. A rule's held state and its exit state are both one of these. */
  @property() public label = "While it lasts";

  /**
   * No null, because the host's own shape has already ruled it out.
   *
   * D3's `on_exit: apply` arm carries a state as a required key, so the control
   * that would clear it has nothing to write. The checkbox is hidden rather
   * than disabled: a disabled checkbox invites the user to look for what
   * enables it, and nothing does.
   */
  @property({ type: Boolean }) public required = false;

  /**
   * Echoed back in the event, so one host can hold several of these.
   *
   * The same arrangement as `mapping.ts`'s `name`, and for the same reason: a
   * rule panel holds up to four action lists and two condition lists, and Lit
   * cannot bind a listener to a constant event name per element. The host
   * registers one listener and routes on this.
   */
  @property() public name = "";

  @property() public entityList = "";

  @property({ attribute: false }) public hass?: HomeAssistant | undefined;

  /** D167: render HA's own pickers rather than the plain inputs. */
  @property({ type: Boolean }) public haReady = false;

  @property() public scriptList = "";

  public override connectedCallback(): void {
    super.connectedCallback();
    this.addEventListener(MAPPING_CHANGED, this._onMapping as EventListener);
    this.addEventListener(ACTIONS_CHANGED, this._onOverride as EventListener);
  }

  public override disconnectedCallback(): void {
    super.disconnectedCallback();
    this.removeEventListener(MAPPING_CHANGED, this._onMapping as EventListener);
    this.removeEventListener(ACTIONS_CHANGED, this._onOverride as EventListener);
  }

  protected override render() {
    const state = this.state;
    return html`
      <section>
        <h3>${this.label}</h3>
        ${this.required
          ? nothing
          : html`<label class="check">
              <input
                type="checkbox"
                .checked=${state !== null}
                ?disabled=${this.disabled}
                @change=${this._toggle}
              />
              <span>Hold a state while the interval runs</span>
            </label>`}
        ${state === null
          ? html`<p class="muted">
              Nothing is held. The rule's entry and exit actions still run; it
              just does not keep anything true in between.
            </p>`
          : this._state(state)}
      </section>
    `;
  }

  private _state(state: StoredDesiredState) {
    const entities = state.entities;
    return html`
      ${entities.map(
        (entity, index) => html`
          <div class="panel">
            <div class="row">
              <h4>${entity.entity_id === "" ? index + 1 : entity.entity_id}</h4>
              <span class="spacer"></span>
              <button
                class="tappable danger"
                title=${entities.length <= 1
                  ? "A held state needs at least one entity."
                  : "Stop holding this one"}
                ?disabled=${this.disabled || entities.length <= 1}
                @click=${() =>
                  this._emit({
                    ...state,
                    entities: entities.filter((_, at) => at !== index),
                  })}
              >
                Remove
              </button>
            </div>
            <almanac-entity-field
              label="Entity"
              kind="entity"
              .hass=${this.hass}
              .haReady=${this.haReady}
              .value=${entity.entity_id}
              fallbackList=${this.entityList}
              ?disabled=${this.disabled}
              .required=${true}
              .onPick=${(value: unknown) =>
                this._patchEntity(index, { entity_id: String(value).trim() })}
            ></almanac-entity-field>
            <label class="field">
              <span>State</span>
              <input
                type="text"
                class="mono"
                placeholder="leave blank to set attributes only"
                .value=${entity.state ?? ""}
                ?disabled=${this.disabled}
                @change=${(event: Event) => {
                  const typed = inputValue(event).trim();
                  this._patchEntity(index, {
                    state: typed === "" ? null : typed,
                  });
                }}
              />
            </label>
            <almanac-mapping
              name=${`attributes:${index}`}
              label="Attributes"
              .value=${entity.attributes}
              ?disabled=${this.disabled}
              hint="Whatever the entity's own integration accepts, as JSON — a
                    brightness, a temperature, a fan mode."
            ></almanac-mapping>
          </div>
        `,
      )}
      <div class="row">
        <button
          class="tappable"
          ?disabled=${this.disabled}
          @click=${() =>
            this._emit({
              ...state,
              entities: [...entities, newDesiredEntity()],
            })}
        >
          Hold another entity
        </button>
      </div>
      <almanac-actions
        name="override"
        label="Instead of reconciling, call this"
        .actions=${state.override}
        ?disabled=${this.disabled}
        entityList=${this.entityList}
        scriptList=${this.scriptList}
        .hass=${this.hass}
        .haReady=${this.haReady}
      ></almanac-actions>
      <p class="muted">
        An override replaces the per-entity work above (D28). It is for the
        device whose integration needs one call to say several things at once —
        almanac still knows what state it wants, it just stops being the one to
        set it entity by entity.
      </p>
    `;
  }

  /**
   * Present or absent, with a whole state as the cost of a tick.
   *
   * Ticking it builds `newDesiredState()` — one blank row — rather than an empty
   * list, for `newDraft`'s reason: a block with no rows shows nothing about what
   * goes in one. Unticking it discards the rows, which is the one destructive
   * control in this component and is why it is a checkbox the user has to aim
   * at rather than a consequence of clearing the last row.
   */
  private _toggle = (event: Event): void => {
    const on = (event.target as HTMLInputElement).checked;
    this._emit(on ? newDesiredState() : null);
  };

  private _patchEntity(
    index: number,
    patch: Partial<StoredDesiredEntity>,
  ): void {
    const state = this.state;
    const entity = state?.entities[index];
    if (!state || !entity) {
      return;
    }
    this._emit({
      ...state,
      entities: state.entities.map((held, at) =>
        at === index ? { ...entity, ...patch } : held,
      ),
    });
  }

  private _onMapping = (event: CustomEvent<MappingChangedDetail>): void => {
    event.stopPropagation();
    const [key, position] = event.detail.name.split(":");
    const index = Number(position);
    // A `data:0` from the override's own actions is not ours. It is already
    // stopped one level down, in `actions.ts`; the key check is what makes that
    // independent of the order the two listeners happen to run in.
    if (key !== "attributes" || !Number.isInteger(index)) {
      return;
    }
    this._patchEntity(index, { attributes: event.detail.value });
  };

  private _onOverride = (event: CustomEvent<ActionsChangedDetail>): void => {
    event.stopPropagation();
    const state = this.state;
    if (event.detail.name !== "override" || !state) {
      return;
    }
    this._emit({ ...state, override: event.detail.actions });
  };

  private _emit(state: StoredDesiredState | null): void {
    this.dispatchEvent(
      new CustomEvent<DesiredChangedDetail>(DESIRED_CHANGED, {
        detail: { name: this.name, state },
        bubbles: true,
        composed: true,
      }),
    );
  }

  static override styles = [
    almanacTokens,
    almanacText,
    almanacForm,
    almanacHitTarget,
  ];
}

declare global {
  interface HTMLElementTagNameMap {
    "almanac-desired": AlmanacDesired;
  }
}
