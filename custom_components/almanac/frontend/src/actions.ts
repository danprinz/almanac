// A list of actions, open for writing. D27's two kinds, and nothing else.
//
// This is the first half of what D149 said step 9e would lift. The editor could
// not offer this until the payload question had an answer, because an action
// editor that renders some of an action is worse than none: it would send the
// part it understood as the whole thing. D152 is that answer, and it is why
// `data` and `fields` are the only two controls here that are not a labelled
// field.
//
// **Order is meaningful.** D27 runs an action list in order and stops at the
// first failure, so these move up and down rather than sorting themselves. A
// list that reordered itself — alphabetically by service, say — would change
// what the schedule does without the user having asked.
//
// **The service name is a text field, not a picker.** `hass.services` is real
// and would give a two-level dropdown of every service in the instance, and it
// is deliberately not used: almanac resolves the service at *call* time, so a
// schedule may legitimately name a service belonging to an integration that is
// currently unloaded, and a picker built from what is loaded now would have no
// row to show for it. D17's rule for a missing resolver is the same one — show
// what is stored, do not silently edit it. The entity ids under `target` get a
// datalist for the same reason in reverse: the list is a suggestion and the
// field accepts anything.

import { LitElement, html, nothing } from "lit";
import { customElement, property } from "lit/decorators.js";

import {
  DEFAULT_SCRIPT_TIMEOUT,
  newScriptAction,
  newServiceAction,
  withTargetIds,
  withWait,
} from "./draft";
import { countFrom, idList, idText, inputChecked, inputValue } from "./form";
import { MAPPING_CHANGED } from "./mapping";
import type { MappingChangedDetail } from "./mapping";
import "./mapping";
import type {
  StoredAction,
  StoredScriptAction,
  StoredServiceAction,
  StoredTarget,
} from "./stored";
import { almanacForm, almanacHitTarget, almanacText, almanacTokens } from "./styles";

export const ACTIONS_CHANGED = "almanac-actions-changed";

export interface ActionsChangedDetail {
  /** Whatever the host set `name` to. See the property's own note. */
  name: string;
  actions: StoredAction[];
}

/**
 * The five selectors, in the order a user reaches for them.
 *
 * `entity_id` first because it is the one that names a thing the user can see in
 * the footprint; the other four are the ones D78 cannot expand, and the note
 * under them says so rather than leaving the gap to be discovered.
 *
 * Spelled as literals rather than read from a list, because they are
 * `StoredTarget`'s keys and `tests/test_wire_contract.py` already pairs those
 * against `const.py::TARGET_SELECTORS`. A list here would be a sixth copy of
 * five strings that no test compares.
 */
const SELECTORS: [keyof StoredTarget, string][] = [
  ["entity_id", "Entities"],
  ["device_id", "Devices"],
  ["area_id", "Areas"],
  ["floor_id", "Floors"],
  ["label_id", "Labels"],
];

@customElement("almanac-actions")
export class AlmanacActions extends LitElement {
  @property({ attribute: false }) public actions: StoredAction[] = [];

  /** The heading. Four lists of actions are reachable from one rule panel. */
  @property() public label = "Actions";

  /**
   * Echoed back in the event, so one host can hold several of these.
   *
   * The same arrangement as `mapping.ts`'s `name`, and for the same reason: a
   * rule panel holds up to four action lists and two condition lists, and Lit
   * cannot bind a listener to a constant event name per element. The host
   * registers one listener and routes on this.
   */
  @property() public name = "";

  @property({ type: Boolean }) public disabled = false;

  /** For the entity datalist, if the host has one to offer. */
  @property() public entityList = "";

  /** For the script datalist. */
  @property() public scriptList = "";

  public override connectedCallback(): void {
    super.connectedCallback();
    this.addEventListener(MAPPING_CHANGED, this._onMapping as EventListener);
  }

  public override disconnectedCallback(): void {
    super.disconnectedCallback();
    this.removeEventListener(MAPPING_CHANGED, this._onMapping as EventListener);
  }

  protected override render() {
    return html`
      <section>
        <h3>${this.label}</h3>
        ${this.actions.length === 0
          ? html`<p class="muted">Nothing happens yet.</p>`
          : nothing}
        ${this.actions.map((action, index) => this._action(action, index))}
        <div class="row">
          <button
            class="tappable"
            ?disabled=${this.disabled}
            @click=${() => this._add(newServiceAction())}
          >
            Add a service call
          </button>
          <button
            class="tappable"
            ?disabled=${this.disabled}
            @click=${() => this._add(newScriptAction())}
          >
            Add a script
          </button>
        </div>
      </section>
    `;
  }

  private _action(action: StoredAction, index: number) {
    const last = this.actions.length - 1;
    return html`
      <div class="panel">
        <div class="row">
          <h4>
            ${index + 1}.
            ${action.kind === "service" ? "Call a service" : "Run a script"}
          </h4>
          <span class="spacer"></span>
          <button
            class="tappable"
            title="Run this one earlier"
            aria-label="Move up"
            ?disabled=${this.disabled || index === 0}
            @click=${() => this._move(index, -1)}
          >
            ↑
          </button>
          <button
            class="tappable"
            title="Run this one later"
            aria-label="Move down"
            ?disabled=${this.disabled || index === last}
            @click=${() => this._move(index, 1)}
          >
            ↓
          </button>
          <button
            class="tappable danger"
            ?disabled=${this.disabled}
            @click=${() => this._remove(index)}
          >
            Remove
          </button>
        </div>
        ${action.kind === "service"
          ? this._service(action, index)
          : this._script(action, index)}
      </div>
    `;
  }

  private _service(action: StoredServiceAction, index: number) {
    const target = action.target;
    return html`
      <label class="field">
        <span>Service</span>
        <input
          type="text"
          class="mono"
          placeholder="light.turn_on"
          .value=${action.service}
          ?disabled=${this.disabled}
          @change=${(event: Event) =>
            this._patch(index, { service: inputValue(event).trim() })}
        />
      </label>
      ${SELECTORS.map(
        ([selector, label]) => html`
          <label class="field">
            <span>${label}</span>
            <input
              type="text"
              class="mono"
              list=${selector === "entity_id" && this.entityList !== ""
                ? this.entityList
                : nothing}
              placeholder="comma separated"
              .value=${idText(target?.[selector] ?? [])}
              ?disabled=${this.disabled}
              @change=${(event: Event) =>
                this._write(
                  index,
                  withTargetIds(action, selector, idList(inputValue(event))),
                )}
            />
          </label>
        `,
      )}
      ${target && !target.entity_id?.length
        ? html`<p class="muted">
            almanac does not expand an area, floor, device or label to entities,
            so this action's reach is not listed under "what this touches".
          </p>`
        : nothing}
      <almanac-mapping
        name=${`data:${index}`}
        label="Data"
        .value=${action.data}
        ?disabled=${this.disabled}
        hint="Whatever the service takes, as JSON. almanac does not check these
              keys — they belong to the service, not to the schedule."
      ></almanac-mapping>
    `;
  }

  private _script(action: StoredScriptAction, index: number) {
    return html`
      <label class="field">
        <span>Script</span>
        <input
          type="text"
          class="mono"
          list=${this.scriptList === "" ? nothing : this.scriptList}
          placeholder="script.evening"
          .value=${action.script}
          ?disabled=${this.disabled}
          @change=${(event: Event) =>
            this._patch(index, { script: inputValue(event).trim() })}
        />
      </label>
      <almanac-mapping
        name=${`fields:${index}`}
        label="Fields"
        .value=${action.fields}
        ?disabled=${this.disabled}
        hint="The script's own fields, as JSON. Calling a script with fields
              waits for it by design, which is why the choice below exists."
      ></almanac-mapping>
      <label class="check">
        <input
          type="checkbox"
          .checked=${action.wait}
          ?disabled=${this.disabled}
          @change=${(event: Event) =>
            this._write(index, withWait(action, inputChecked(event)))}
        />
        <span>Wait for it to finish</span>
      </label>
      ${action.wait
        ? html`
            <label class="field">
              <span>Give up after</span>
              <input
                type="number"
                min="1"
                step="1"
                .value=${String(action.timeout ?? DEFAULT_SCRIPT_TIMEOUT)}
                ?disabled=${this.disabled}
                @change=${(event: Event) =>
                  this._patch(index, {
                    timeout: countFrom(
                      inputValue(event),
                      action.timeout ?? DEFAULT_SCRIPT_TIMEOUT,
                    ),
                  })}
              />
              <span class="muted">seconds</span>
            </label>
            <p class="muted">
              A timeout is required when almanac waits (D30): an unbounded wait
              holds the engine inside this one rule for as long as the script
              takes. The timeout is what it gives up after, not what it kills.
            </p>
          `
        : html`<p class="muted">
            The script is started and almanac carries on. Anything after this
            action runs immediately, not when the script is done.
          </p>`}
    `;
  }

  // --- writes ---------------------------------------------------------------
  //
  // Every one of them rebuilds the list and dispatches it whole. The alternative
  // — an event per field, with an index and a key — would make the editor above
  // reassemble a list it does not otherwise touch, and the actions in a rule are
  // one value as far as the draft is concerned.

  private _patch(
    index: number,
    patch: Partial<StoredServiceAction> | Partial<StoredScriptAction>,
  ): void {
    const action = this.actions[index];
    if (!action) {
      return;
    }
    this._write(index, { ...action, ...patch } as StoredAction);
  }

  /**
   * A payload, back from whichever `<almanac-mapping>` holds it.
   *
   * One listener on the host, keyed by the `name` this component set — see that
   * property's note for why it is not a binding per element. The event is
   * `composed`, so it is stopped here: an editor two levels up that happened to
   * listen for it would otherwise see a payload with no idea whose it was.
   */
  private _onMapping = (event: CustomEvent<MappingChangedDetail>): void => {
    event.stopPropagation();
    const [key, position] = event.detail.name.split(":");
    const index = Number(position);
    if ((key !== "data" && key !== "fields") || !Number.isInteger(index)) {
      return;
    }
    this._patch(index, { [key]: event.detail.value });
  };

  private _write(index: number, action: StoredAction): void {
    const next = [...this.actions];
    next[index] = action;
    this._emit(next);
  }

  private _add(action: StoredAction): void {
    this._emit([...this.actions, action]);
  }

  private _remove(index: number): void {
    this._emit(this.actions.filter((_, at) => at !== index));
  }

  private _move(index: number, by: number): void {
    const next = [...this.actions];
    const moved = next[index];
    const target = next[index + by];
    if (!moved || !target) {
      return;
    }
    next[index] = target;
    next[index + by] = moved;
    this._emit(next);
  }

  private _emit(actions: StoredAction[]): void {
    this.dispatchEvent(
      new CustomEvent<ActionsChangedDetail>(ACTIONS_CHANGED, {
        detail: { name: this.name, actions },
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
    "almanac-actions": AlmanacActions;
  }
}
