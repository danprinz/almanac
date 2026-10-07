// A list of conditions, open for writing. D23's model and nothing beyond it.
//
// **There are no templates here, and there never will be.** D23 says conditions
// are structured only, and the reason is this component: a template field is one
// the editor cannot render, cannot explain and cannot preview, so a schedule
// holding one would be a schedule the UI can only show as text. §9.1's parity
// rule — everything the engine does, the editor can say — is violated by
// construction the moment a template is allowed in. The escape hatch is a named
// template `binary_sensor` in the user's own configuration, compared here like
// any other entity.
//
// **The top-level list is an AND, and it is written in words rather than drawn
// (D155).**
// `CONDITIONS_SCHEMA` has no operator field at the top level, deliberately, so
// there is nothing to offer — and a control that only ever showed "and" would
// read as a choice. A group is how an OR is spelled, which makes the nesting one
// level deep: §7.1's groups contain leaves, never groups. A disjunction of
// conjunctions is not expressible, and that is D23's trade, not an omission.
//
// **A comparison's value is typed, not inferred (D154).** `_scalar` keeps the type the
// user typed, because a threshold of `20` stored as `"20"` turns a numeric
// comparison into a string one in which `"9" > "20"`. So the type is a third
// control beside the value, and the three-way choice is deliberate: inferring
// "number" from the text `20` would make a text sensor whose state is genuinely
// `"20"` impossible to compare against.

import { LitElement, html, nothing } from "lit";
import { customElement, property } from "lit/decorators.js";

import {
  asScalarType,
  newComparison,
  newDaySetCondition,
  newGroup,
  scalarType,
  takesAList,
  withOperator,
} from "./draft";
import type { ScalarType } from "./draft";
import type { DaySetRow } from "./api";
import "./entity-field";
import type { HomeAssistant } from "./ha";
import { countFrom, idList, idText, inputChecked, inputValue } from "./form";
import type {
  StoredComparisonCondition,
  StoredComparisonOperator,
  StoredCondition,
  StoredDaySetCondition,
  StoredGroupCondition,
  StoredLeafCondition,
  StoredOperand,
  StoredScalar,
} from "./stored";
import { almanacForm, almanacHitTarget, almanacText, almanacTokens } from "./styles";

export const CONDITIONS_CHANGED = "almanac-conditions-changed";

export interface ConditionsChangedDetail {
  /** Whatever the host set `name` to. See the property's own note. */
  name: string;
  conditions: StoredCondition[];
}

/**
 * `const.py::COMPARISON_OPERATORS`, in its order, with what each one says.
 *
 * The words are the point. `gte` is a storage key and "at least" is what the
 * user means; a dropdown of eight abbreviations would be a dropdown of the
 * schema's internals. The eight keys themselves are checked against `const.py`
 * by `tests/test_wire_contract.py` through `StoredComparisonOperator`, so this
 * list is a labelling of a type, not a second copy of a list.
 */
const OPERATORS: [StoredComparisonOperator, string][] = [
  ["eq", "is"],
  ["ne", "is not"],
  ["gt", "is above"],
  ["gte", "is at least"],
  ["lt", "is below"],
  ["lte", "is at most"],
  ["in", "is one of"],
  ["not_in", "is none of"],
];

const SCALAR_TYPES: [ScalarType, string][] = [
  ["text", "text"],
  ["number", "a number"],
  ["boolean", "true / false"],
];

@customElement("almanac-conditions")
export class AlmanacConditions extends LitElement {
  @property({ attribute: false }) public conditions: StoredCondition[] = [];

  @property() public label = "Only when";

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

  /** D18's day sets, for the second leaf kind. Empty hides that button. */
  @property({ attribute: false }) public daySets: DaySetRow[] = [];

  @property() public entityList = "";

  @property({ attribute: false }) public hass?: HomeAssistant | undefined;

  /** D167: render HA's own pickers rather than the plain inputs. */
  @property({ type: Boolean }) public haReady = false;

  protected override render() {
    return html`
      <section>
        <h3>${this.label}</h3>
        ${this.conditions.length === 0
          ? html`<p class="muted">No conditions — this always applies.</p>`
          : html`<p class="muted">
              All of these have to hold. For "either, or", add a group.
            </p>`}
        ${this.conditions.map((condition, index) =>
          condition.kind === "group"
            ? this._group(condition, index)
            : html`<div class="panel">
                ${this._leafHeader(index, null)}
                ${this._leaf(condition, (next) => this._write(index, next))}
              </div>`,
        )}
        <div class="row">
          <button
            class="tappable"
            ?disabled=${this.disabled}
            @click=${() => this._add(newComparison())}
          >
            Add a check
          </button>
          ${this.daySets.length === 0
            ? nothing
            : html`<button
                class="tappable"
                ?disabled=${this.disabled}
                @click=${() =>
                  this._add(newDaySetCondition(this.daySets[0]!.id))}
              >
                Add a day set
              </button>`}
          <button
            class="tappable"
            ?disabled=${this.disabled}
            @click=${() => this._add(newGroup())}
          >
            Add an either / or
          </button>
        </div>
      </section>
    `;
  }

  /**
   * A group: one level of nesting, and the OR almanac has no other spelling for.
   *
   * `and` is offered inside it even though the list above is already an AND,
   * because a group the user built and then changed their mind about should not
   * have to be torn down and rebuilt as three separate checks. `const.py` says
   * the same thing where `GROUP_AND` is defined.
   */
  private _group(group: StoredGroupCondition, index: number) {
    const members = group.conditions;
    return html`
      <div class="panel">
        ${this._leafHeader(index, "Either / or")}
        <label class="field">
          <span>Holds when</span>
          <select
            ?disabled=${this.disabled}
            @change=${(event: Event) =>
              this._write(index, {
                ...group,
                operator: inputValue(event) === "and" ? "and" : "or",
              })}
          >
            <option value="or" ?selected=${group.operator === "or"}>
              any one of these does
            </option>
            <option value="and" ?selected=${group.operator === "and"}>
              all of these do
            </option>
          </select>
        </label>
        ${members.map(
          (member, inner) => html`
            <div class="panel">
              <div class="row">
                <h4>${inner + 1}.</h4>
                <span class="spacer"></span>
                <button
                  class="tappable danger"
                  title=${members.length <= 2
                    ? "A group needs at least two parts."
                    : "Remove this part"}
                  ?disabled=${this.disabled || members.length <= 2}
                  @click=${() =>
                    this._write(index, {
                      ...group,
                      conditions: members.filter((_, at) => at !== inner),
                    })}
                >
                  Remove
                </button>
              </div>
              ${this._leaf(member, (next) =>
                this._write(index, {
                  ...group,
                  conditions: members.map((held, at) =>
                    at === inner ? next : held,
                  ),
                }),
              )}
            </div>
          `,
        )}
        <div class="row">
          <button
            class="tappable"
            ?disabled=${this.disabled}
            @click=${() =>
              this._write(index, {
                ...group,
                conditions: [...members, newComparison()],
              })}
          >
            Add a part
          </button>
        </div>
        <p class="muted">
          A group holds leaves, not groups (§7.1). "All of A, or all of B" is not
          something almanac stores; a template sensor of your own, compared here
          like any other entity, is the way to express it.
        </p>
      </div>
    `;
  }

  private _leafHeader(index: number, kind: string | null) {
    return html`
      <div class="row">
        <h4>${index + 1}. ${kind ?? ""}</h4>
        <span class="spacer"></span>
        <button
          class="tappable danger"
          ?disabled=${this.disabled}
          @click=${() => this._remove(index)}
        >
          Remove
        </button>
      </div>
    `;
  }

  /** Either leaf kind, writing through whatever put it on screen. */
  private _leaf(
    condition: StoredLeafCondition,
    write: (next: StoredLeafCondition) => void,
  ) {
    return condition.kind === "day_set"
      ? this._daySet(condition, write)
      : this._comparison(condition, write);
  }

  private _daySet(
    condition: StoredDaySetCondition,
    write: (next: StoredLeafCondition) => void,
  ) {
    const known = this.daySets.some((row) => row.id === condition.day_set_id);
    return html`
      <label class="field">
        <span>Day set</span>
        <select
          ?disabled=${this.disabled}
          @change=${(event: Event) =>
            write({ ...condition, day_set_id: inputValue(event) })}
        >
          ${known
            ? nothing
            : html`<option value=${condition.day_set_id} selected>
                ${condition.day_set_id}
              </option>`}
          ${this.daySets.map(
            (row) => html`
              <option
                value=${row.id}
                ?selected=${row.id === condition.day_set_id}
              >
                ${row.name}
              </option>
            `,
          )}
        </select>
      </label>
      <label class="check">
        <input
          type="checkbox"
          .checked=${condition.negate}
          ?disabled=${this.disabled}
          @change=${(event: Event) =>
            write({ ...condition, negate: inputChecked(event) })}
        />
        <span>Unless, rather than when</span>
      </label>
      ${known
        ? nothing
        : html`<p class="problem">
            This names a day set that no longer exists. It is kept as it is until
            you pick another one.
          </p>`}
    `;
  }

  private _comparison(
    condition: StoredComparisonCondition,
    write: (next: StoredLeafCondition) => void,
  ) {
    const wantsList = takesAList(condition.operator);
    const operand = condition.value;
    return html`
      <almanac-entity-field
        label="Entity"
        kind="entity"
        .hass=${this.hass}
        .haReady=${this.haReady}
        .value=${condition.entity_id}
        fallbackList=${this.entityList}
        ?disabled=${this.disabled}
        .required=${true}
        .onPick=${(value: unknown) =>
          write({ ...condition, entity_id: String(value).trim() })}
      ></almanac-entity-field>
      <label class="field">
        <span>Attribute</span>
        <input
          type="text"
          class="mono"
          placeholder="its state"
          .value=${condition.attribute ?? ""}
          ?disabled=${this.disabled}
          @change=${(event: Event) => {
            const typed = inputValue(event).trim();
            write({ ...condition, attribute: typed === "" ? null : typed });
          }}
        />
      </label>
      <label class="field">
        <span>Is</span>
        <select
          ?disabled=${this.disabled}
          @change=${(event: Event) =>
            write(withOperator(condition, inputValue(event) as StoredComparisonOperator))}
        >
          ${OPERATORS.map(
            ([operator, words]) => html`
              <option
                value=${operator}
                ?selected=${operator === condition.operator}
              >
                ${words}
              </option>
            `,
          )}
        </select>
      </label>
      ${this._operand(condition, operand, wantsList, write)}
      <label class="field">
        <span>Held for</span>
        <input
          type="number"
          min="1"
          step="1"
          placeholder="0"
          .value=${condition.for === null ? "" : String(condition.for)}
          ?disabled=${this.disabled}
          @change=${(event: Event) => {
            const typed = inputValue(event).trim();
            write({
              ...condition,
              for: typed === "" ? null : countFrom(typed, 1),
            });
          }}
        />
        <span class="muted">seconds</span>
      </label>
      <label class="field">
        <span>Called</span>
        <input
          type="text"
          placeholder="optional, for the timeline"
          .value=${condition.label ?? ""}
          ?disabled=${this.disabled}
          @change=${(event: Event) => {
            const typed = inputValue(event);
            write({ ...condition, label: typed === "" ? null : typed });
          }}
        />
      </label>
    `;
  }

  /**
   * What a comparison compares against: a value the user types, or an entity.
   *
   * The entity choice is hidden while a list operator is set, because
   * `_comparison` refuses `in` against another entity outright — the operand
   * would have to be a list and an entity has one state. `withOperator` has an
   * arm for the combination anyway, as the defence behind this hiding.
   */
  private _operand(
    condition: StoredComparisonCondition,
    operand: StoredOperand,
    wantsList: boolean,
    write: (next: StoredLeafCondition) => void,
  ) {
    const held = operand.kind === "constant" ? operand.value : "";
    const list = Array.isArray(held) ? held : [];
    const scalar: StoredScalar = Array.isArray(held) ? (held[0] ?? "") : held;
    const type = scalarType(scalar);
    return html`
      ${wantsList
        ? nothing
        : html`
            <label class="field">
              <span>Compared to</span>
              <select
                ?disabled=${this.disabled}
                @change=${(event: Event) =>
                  write({
                    ...condition,
                    value:
                      inputValue(event) === "entity"
                        ? {
                            kind: "entity",
                            entity_id: "",
                            attribute: null,
                            offset: 0,
                          }
                        : { kind: "constant", value: "" },
                  })}
              >
                <option value="constant" ?selected=${operand.kind === "constant"}>
                  a value
                </option>
                <option value="entity" ?selected=${operand.kind === "entity"}>
                  another entity
                </option>
              </select>
            </label>
          `}
      ${operand.kind === "entity"
        ? html`
            <almanac-entity-field
              label="That entity"
              kind="entity"
              .hass=${this.hass}
              .haReady=${this.haReady}
              .value=${operand.entity_id}
              fallbackList=${this.entityList}
              ?disabled=${this.disabled}
              .required=${true}
              .onPick=${(value: unknown) =>
                write({
                  ...condition,
                  value: { ...operand, entity_id: String(value).trim() },
                })}
            ></almanac-entity-field>
            <label class="field">
              <span>Its attribute</span>
              <input
                type="text"
                class="mono"
                placeholder="its state"
                .value=${operand.attribute ?? ""}
                ?disabled=${this.disabled}
                @change=${(event: Event) => {
                  const typed = inputValue(event).trim();
                  write({
                    ...condition,
                    value: {
                      ...operand,
                      attribute: typed === "" ? null : typed,
                    },
                  });
                }}
              />
            </label>
            <label class="field">
              <span>Plus</span>
              <input
                type="number"
                step="any"
                .value=${String(operand.offset)}
                ?disabled=${this.disabled}
                @change=${(event: Event) => {
                  const typed = Number(inputValue(event));
                  write({
                    ...condition,
                    value: {
                      ...operand,
                      offset: Number.isFinite(typed) ? typed : 0,
                    },
                  });
                }}
              />
            </label>
            <p class="muted">
              The offset's unit is whatever the two sides are: seconds when they
              are times, degrees when they are temperatures. almanac does not
              convert between them.
            </p>
          `
        : wantsList
          ? html`
              <label class="field">
                <span>One of</span>
                <input
                  type="text"
                  .value=${idText(list.map(String))}
                  ?disabled=${this.disabled}
                  @change=${(event: Event) =>
                    write({
                      ...condition,
                      value: {
                        kind: "constant",
                        value: idList(inputValue(event)),
                      },
                    })}
                />
              </label>
              <p class="muted">
                Comma separated, and each one is text. A list of numbers is not
                something this control can build — a single value with "is at
                least" is almost always the check that was meant.
              </p>
            `
          : html`
              <label class="field">
                <span>That value</span>
                <input
                  type="text"
                  .value=${String(scalar)}
                  ?disabled=${this.disabled}
                  @change=${(event: Event) =>
                    write({
                      ...condition,
                      value: {
                        kind: "constant",
                        value: asScalarType(inputValue(event), type),
                      },
                    })}
                />
                <select
                  ?disabled=${this.disabled}
                  @change=${(event: Event) =>
                    write({
                      ...condition,
                      value: {
                        kind: "constant",
                        value: asScalarType(
                          scalar,
                          inputValue(event) as ScalarType,
                        ),
                      },
                    })}
                >
                  ${SCALAR_TYPES.map(
                    ([name, words]) => html`
                      <option value=${name} ?selected=${name === type}>
                        ${words}
                      </option>
                    `,
                  )}
                </select>
              </label>
              ${type === "text"
                ? html`<p class="muted">
                    Compared as text, so "9" is above "20". Pick "a number" for a
                    threshold.
                  </p>`
                : nothing}
            `}
    `;
  }

  // --- writes ---------------------------------------------------------------

  private _write(index: number, condition: StoredCondition): void {
    this._emit(
      this.conditions.map((held, at) => (at === index ? condition : held)),
    );
  }

  private _add(condition: StoredCondition): void {
    this._emit([...this.conditions, condition]);
  }

  private _remove(index: number): void {
    this._emit(this.conditions.filter((_, at) => at !== index));
  }

  private _emit(conditions: StoredCondition[]): void {
    this.dispatchEvent(
      new CustomEvent<ConditionsChangedDetail>(CONDITIONS_CHANGED, {
        detail: { name: this.name, conditions },
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
    "almanac-conditions": AlmanacConditions;
  }
}
