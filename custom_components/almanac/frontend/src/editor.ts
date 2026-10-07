// The editor: one schedule, open for writing.
//
// This is the first file in the frontend that *changes* anything, and almost
// everything in it was decided elsewhere. `draft.ts` holds the edit algebra and
// the two write shapes, `api.ts` the three commands, `rails.ts::draftTrack` the
// drawing of an unsaved schedule and `track.ts` the `full` size that makes its
// stages clickable. What is left here is the arrangement: which control sits
// where, what each one writes, and which of them is not offered at all.
//
// **D149 is lifted, and the reason it existed is what shapes what replaced
// it.** The limit said this editor touched anchors, arming, ends, rules and
// identity and nothing else, because a partial action editor renders the
// `service` and `target` it understands, silently drops the `data` keys it does
// not, and then D140's diff sends the truncation as a change — opening an
// imported schedule and pressing Save would *destroy* it. What made the
// builders safe was answering the payload question first: `mapping.ts` edits
// `data`, `fields` and `attributes` as JSON text, so there is no key the editor
// knows about and no key it does not. D152 has that argument in full.
//
// So the four builders are here now — `<almanac-actions>`, `<almanac-conditions>`,
// `<almanac-desired>` and the completion section below — and each one owns its
// own value whole. This file's remaining job is the arrangement: which builder
// sits in which rule kind, what it is called, and where its value lands in the
// draft.
//
// **D141 — a non-admin is told before typing, not after saving.** Core wraps
// create, update and delete in `require_admin` with no opt-out while `/list`
// stays open, so a read-only session can reach this editor with every field
// populated. The banner and the disabled inputs come from `hass.user.is_admin`;
// `isUnauthorized` still guards the write, for the account demoted mid-session.
//
// **The track is a `preview`.** A draft has no plan — D64 forbids this layer
// from resolving an anchor and nothing has been saved for the backend to resolve
// — so every rail's instant is null. `preview` is what stops that from drawing
// the whole track amber, which would make amber mean nothing (finding 17).
//
// One thing is deliberately not here: there is no control that changes a
// recurrence from `dates`, `nth_weekday` or `every_n` to anything else. The two
// kinds the editor can build from nothing it can also switch between; the other
// three it can only show, because a switch away from a shape it cannot rebuild
// is a one-way door disguised as a dropdown.

import { LitElement, css, html, nothing } from "lit";
import { customElement, property, state } from "lit/decorators.js";

import "./actions";
import { describeDateWindow, isSectionOpen } from "./advanced";
import { ICON_ADD, ICON_REMOVE, haButton } from "./buttons";
import { ACTIONS_CHANGED } from "./actions";
import type { ActionsChangedDetail } from "./actions";
import {
  createSchedule,
  deleteSchedule,
  fetchDaySets,
  fetchResolvers,
  isNotLoaded,
  isUnauthorized,
  updateSchedule,
} from "./api";
import type { DaySetRow } from "./api";
import "./conditions";
import { CONDITIONS_CHANGED } from "./conditions";
import type { ConditionsChangedDetail } from "./conditions";
import "./desired";
import { DESIRED_CHANGED } from "./desired";
import type { DesiredChangedDetail } from "./desired";
import {
  DEFAULT_CLOCK_TIME,
  addRule,
  clockAtRule,
  draftOf,
  footprintOf,
  isDirty,
  newDesiredState,
  newDraft,
  problems,
  removeRuleAt,
  replaceRuleAt,
  rulesOf,
  toCreate,
  toUpdate,
  withBody,
  withName,
  withObjectId,
} from "./draft";
import type { Draft, WritableDuringRule, WritableRule } from "./draft";
import {
  countFrom,
  inputChecked,
  inputValue,
  secondsFrom,
} from "./form";
import "./entity-field";
import { duration, offsetLabel } from "./format";
import type { HaElementsState } from "./ha-elements";
import type { HomeAssistant } from "./ha";
import { openMoreInfo } from "./moreinfo";
import { draftTrack } from "./rails";
import type {
  StoredAction,
  StoredAnchor,
  StoredCompletion,
  StoredCondition,
  StoredConditionPolicy,
  StoredCountOn,
  StoredEnd,
  StoredFinishedWhen,
  StoredOnExit,
  StoredRecurrence,
  StoredSchedule,
  StoredThen,
} from "./stored";
import {
  almanacForm,
  almanacHitTarget,
  almanacText,
  almanacTokens,
} from "./styles";
import "./segmented";
import "./time-picker";
import "./track";
import { STAGE_SELECTED } from "./track";
import type { StageSelectedDetail } from "./track";
import type { WireOffering, WireResolverCatalogue } from "./wire";

/** What a closed editor says. `scheduleId` is null only on a cancelled create. */
export interface EditorClosedDetail {
  saved: boolean;
  scheduleId: string | null;
}

export const EDITOR_CLOSED = "almanac-editor-closed";

/**
 * `const.py::WEEKDAYS`, in that order.
 *
 * A restatement, and one of the few that is safe: these are the seven day names
 * the storage schema accepts, they are not a default anyone can change, and
 * `tests/test_frontend_assets.py` would have nothing to compare against anyway
 * because the list is a `Final` tuple rather than a schema default. The labels
 * are separate because the stored value is a slug and the screen is not.
 */
const WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;
const WEEKDAY_LABELS: Record<string, string> = {
  mon: "Mon",
  tue: "Tue",
  wed: "Wed",
  thu: "Thu",
  fri: "Fri",
  sat: "Sat",
  sun: "Sun",
};

/** The two recurrence kinds the editor can build from nothing. */
const RECUR_WEEKDAYS = "weekdays";
const RECUR_DAY_SET = "day_set";

/**
 * A new `during` rule's end: one hour.
 *
 * Not a restated default — `end` is required by `_DURING_RULE_SCHEMA`, so there
 * is no backend value to drift from and the editor has to choose something for
 * the rule to be drawable at all. An hour is the shortest span that reads as a
 * span rather than as a mistake, and it is the one number here the user is
 * expected to change immediately.
 */
const DEFAULT_DURATION = 3600;

/**
 * A never-saved schedule's completion, restated.
 *
 * The one restatement in this file that carries a real risk, and it is written
 * down rather than hidden: `_COMPLETION_SCHEMA` gives each of the three axes a
 * `vol.Optional` default, and D140's update is a shallow top-level merge — so a
 * *partial* `completion` would silently re-default the two axes left out. The
 * editor therefore has to send the object whole, and to send it whole it has to
 * have something to show for a draft that has never been saved.
 *
 * These three values are `schema.py`'s, and nothing compares them: there is no
 * defaults-comparison machinery in `tests/test_wire_contract.py` to hang a
 * check on. What limits the damage is that this value is only ever *shown* —
 * `toCreate` sends `completion` only once the user has touched it, so a drift
 * here would mislabel the three dropdowns rather than write a wrong schedule.
 * If the schema's defaults ever change, these are the strings to change too.
 */
const DEFAULT_COMPLETION: StoredCompletion = {
  finished_when: { kind: "never" },
  then: { kind: "keep" },
  count_on: "scheduled",
};

/** D46's three axes, labelled. The values are `const.py`'s. */
const FINISHED_KINDS: [string, string][] = [
  ["never", "Never — it just keeps running"],
  ["one_rule_fired", "Once any one rule has fired"],
  ["cycle", "Once every armed rule has fired"],
  ["occurrences", "After a number of occurrences"],
  ["date", "On a day"],
  ["condition", "When something becomes true"],
];

const THEN_KINDS: [string, string][] = [
  ["keep", "Keep it, finished"],
  ["disable", "Switch it off"],
  ["delete", "Delete it"],
  ["action", "Run something"],
];

const COUNT_ON_KINDS: [StoredCountOn, string][] = [
  ["scheduled", "every occurrence that came due"],
  ["conditions_passed", "only the ones whose conditions passed"],
  ["actions_succeeded", "only the ones that actually did something"],
];

/** The datalist id the entity anchor's input reads from. */
const ENTITY_LIST = "almanac-time-entities";

/**
 * Module constants, because the picker's properties are compared by identity: a
 * fresh object on every render would look like a change on every render.
 */
const CLOCK_ANCHOR: StoredAnchor = { kind: "clock", at: DEFAULT_CLOCK_TIME };
const NO_OFFERINGS: WireOffering[] = [];

/**
 * The other two datalists: every entity, and every script.
 *
 * Every entity, uncapped, which is the one place this component renders a node
 * per entity in the instance. It is affordable because a `<datalist>` is not
 * laid out — the browser builds the popup and does the matching — so the cost
 * is DOM nodes and not reflow. A cap would be worse than the cost: a suggestion
 * list that silently stops at five hundred entities is a list that lies about
 * what the field accepts, and the field accepts anything (D17's rule again).
 */
const ANY_ENTITY_LIST = "almanac-entities";
const SCRIPT_LIST = "almanac-scripts";

/**
 * Today, for the one field that needs a day to start from.
 *
 * D64 forbids the *engine* from sampling a clock; it says nothing about a form
 * default, and this value is never evaluated — it is what the date input shows
 * the moment the user picks "On a day", so that the field is not blank and the
 * save is not refused by `_day`.
 */
const today = (): string => {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
};

@customElement("almanac-editor")
export class AlmanacEditor extends LitElement {
  @property({ attribute: false }) public hass?: HomeAssistant | undefined;

  /** D167. Set by the panel; see `panel.ts`. */
  @property({ attribute: false }) public haElements: HaElementsState = "loading";

  /** Whether a component may render HA's own elements. */
  protected get haReady(): boolean {
    return this.haElements === "ready";
  }

  /**
   * The schedule being edited, or null/absent to create one.
   *
   * Held as the original as well as the source of the draft, because D140's
   * update is a diff and the thing it diffs against has to survive every
   * keystroke. Re-setting this property restarts the edit.
   */
  @property({ attribute: false }) public schedule?: StoredSchedule | null;

  @state() private _draft: Draft = newDraft();
  @state() private _original: StoredSchedule | null = null;
  /** The selected rule's index, as `draftTrack` addresses it: `"0"`, `"1"`, … */
  @state() private _selected?: string | undefined;
  @state() private _catalogue?: WireResolverCatalogue | undefined;
  @state() private _daySets: DaySetRow[] = [];
  /**
   * The instant the anchor previews are asked about, handed to the time picker.
   *
   * This is one of the two places the frontend reads the clock, and it is the top
   * of the call tree -- the same position as the tick on the Python side, which is
   * what D64 (nothing below the top reads a clock) allows. It is read once, in
   * `connectedCallback`, so "next:" means "next since the editor was opened" and
   * does not move under the user while they edit.
   */
  @state() private _previewAt = "";
  @state() private _saving = false;
  /**
   * The user's own open or close of each Advanced section, keyed by section
   * ("schedule", "rule:<index>"), and cleared when another schedule is loaded.
   * Whether a section is open is this OR whether it holds a set field.
   */
  @state() private _advancedToggles: Record<string, boolean> = {};
  @state() private _error?: string | undefined;

  public override connectedCallback(): void {
    super.connectedCallback();
    // On the host, not in the template. `track.ts` exports the event's name as a
    // constant and Lit's `@name=` binding cannot take one, so a literal in the
    // template would be a second spelling of it that no test compares. The
    // event is `composed`, which is what lets it cross the track's shadow
    // boundary and reach this listener.
    this.addEventListener(STAGE_SELECTED, this._onStage as EventListener);
    // The same arrangement, three more times. Each builder dispatches one event
    // for its whole value and tags it with the `name` this component gave it,
    // because a rule panel holds up to four action lists and two condition
    // lists and Lit cannot bind a listener to a constant per element.
    this.addEventListener(ACTIONS_CHANGED, this._onActions as EventListener);
    this.addEventListener(
      CONDITIONS_CHANGED,
      this._onConditions as EventListener,
    );
    this.addEventListener(DESIRED_CHANGED, this._onDesired as EventListener);
    this._previewAt = new Date().toISOString();
    this._load();
    void this._catalogues();
  }

  public override disconnectedCallback(): void {
    super.disconnectedCallback();
    this.removeEventListener(STAGE_SELECTED, this._onStage as EventListener);
    this.removeEventListener(ACTIONS_CHANGED, this._onActions as EventListener);
    this.removeEventListener(
      CONDITIONS_CHANGED,
      this._onConditions as EventListener,
    );
    this.removeEventListener(DESIRED_CHANGED, this._onDesired as EventListener);
  }

  protected override willUpdate(changed: Map<string, unknown>): void {
    if (changed.has("schedule")) {
      this._load();
    }
    if (changed.has("hass") && this._catalogue === undefined) {
      void this._catalogues();
    }
  }

  /** Start, or restart, the edit from whatever `schedule` now holds. */
  private _load(): void {
    const schedule = this.schedule ?? null;
    this._original = schedule;
    this._draft = schedule ? draftOf(schedule) : newDraft();
    this._selected = rulesOf(this._draft).length > 0 ? "0" : undefined;
    this._error = undefined;
    this._advancedToggles = {};
  }

  /** The draft in the shape `advanced.ts` reads: what is set *now*. */
  private get _advancedView() {
    return {
      completion: this._draft.body.completion,
      date_window: this._draft.body.date_window,
      rules: rulesOf(this._draft),
    };
  }

  private _onAdvancedToggle = (event: Event): void => {
    const details = event.target as HTMLDetailsElement;
    const section = details.dataset["section"] ?? "schedule";
    if (!details.open && isSectionOpen(this._advancedView, section, {})) {
      // A section holding a set field cannot be collapsed into hiding it.
      details.open = true;
      return;
    }
    this._advancedToggles = { ...this._advancedToggles, [section]: details.open };
  };

  /**
   * One collapsible section (D170). Open when a field in *this* section is set,
   * or when the user opened *this* section; never because of another one.
   */
  private _advanced(section: string, body: unknown) {
    return html`
      <details
        class="advanced"
        data-section=${section}
        ?open=${isSectionOpen(this._advancedView, section, this._advancedToggles)}
        @toggle=${this._onAdvancedToggle}
      >
        <summary>Advanced</summary>
        ${body}
      </details>
    `;
  }

  /**
   * The two pick-lists, read once.
   *
   * Neither takes an `at` and neither is a function of the draft, so one read
   * per open is right: an offering appearing or disappearing means a restart,
   * and a day set created in another tab is worth a reload rather than a poll.
   * A failure is not fatal — the clock and entity anchors need no catalogue —
   * so it leaves `_error` alone and the picker falls back to what it can offer.
   */
  private async _catalogues(): Promise<void> {
    const hass = this.hass;
    if (!hass) {
      return;
    }
    try {
      const [catalogue, daySets] = await Promise.all([
        fetchResolvers(hass),
        fetchDaySets(hass),
      ]);
      this._catalogue = catalogue;
      this._daySets = daySets;
    } catch (error) {
      if (!isNotLoaded(error)) {
        this._catalogue = { offerings: [], parametric: [] };
      }
    }
  }

  /** D141's answer, and "unknown" counts as writable — the write will say so. */
  private get _canWrite(): boolean {
    const user = this.hass?.user;
    return user ? user.is_admin : true;
  }

  private get _rules(): WritableRule[] {
    return rulesOf(this._draft);
  }

  private get _selectedIndex(): number | null {
    if (this._selected === undefined) {
      return null;
    }
    const index = Number(this._selected);
    return Number.isInteger(index) && index >= 0 && index < this._rules.length
      ? index
      : null;
  }

  private _onStage = (event: CustomEvent<StageSelectedDetail>): void => {
    this._selected = event.detail.ruleId;
  };

  private _entityName = (entityId: string): string | undefined => {
    const name = this.hass?.states[entityId]?.attributes["friendly_name"];
    return typeof name === "string" ? name : undefined;
  };

  /**
   * The entities an `entity_time` anchor can name.
   *
   * `device_class: timestamp` is the filter because that is what makes a state
   * string an instant rather than text that looks like one — D6's anchor reads
   * the state and parses it, and an entity whose state merely happens to be
   * parseable today is the kind of anchor that breaks on a firmware update. The
   * list is a suggestion, not a constraint: the input accepts anything, because
   * a template sensor the user knows is a timestamp should not be unreachable.
   */
  private get _timeEntities(): string[] {
    const states = this.hass?.states ?? {};
    return Object.values(states)
      .filter((entity) => entity?.attributes["device_class"] === "timestamp")
      .map((entity) => entity!.entity_id)
      .sort();
  }

  /** Everything, sorted. See `ANY_ENTITY_LIST` for why it is not filtered. */
  private get _anyEntities(): string[] {
    return Object.keys(this.hass?.states ?? {}).sort();
  }

  /**
   * The script entities.
   *
   * By domain prefix rather than by looking for a `mode` attribute, because an
   * unavailable script still has `script.` and still has a name worth
   * suggesting — and D30's pre-flight reads `mode` at call time, not here.
   */
  private get _scripts(): string[] {
    return this._anyEntities.filter((id) => id.startsWith("script."));
  }

  // --- rendering ------------------------------------------------------------

  protected override render() {
    if (!this.hass) {
      return nothing;
    }
    const found = problems(this._draft);
    return html`
      <div class="shell">
        <header>
          <h2>${this._original ? "Edit schedule" : "New schedule"}</h2>
          ${haButton({ onClick: () => this._close(false) }, "Close")}
        </header>
        ${this._canWrite
          ? nothing
          : html`<p class="problem">
              This account cannot change schedules, so everything below is
              read-only.
            </p>`}
        ${this.haElements === "failed"
          ? html`<p class="muted" role="status">
              Home Assistant's pickers didn't load — basic inputs shown.
            </p>`
          : nothing}
        ${this._identity()} ${this._recurrence()} ${this._shape()}
        ${this._rulePanel()}
        ${found.length === 0
          ? nothing
          : html`<ul class="problems">
              ${found.map((note) => html`<li class="problem">${note}</li>`)}
            </ul>`}
        ${this._advanced(
          "schedule",
          html`
            ${describeDateWindow(this._draft.body.date_window) === ""
              ? nothing
              : html`<p class="muted">
                  ${describeDateWindow(this._draft.body.date_window)}
                </p>`}
            ${this._completion()}
          `,
        )}
        ${this._footprint()}
        ${this._error ? html`<p class="problem">${this._error}</p>` : nothing}
        ${this._footer(found)}
      </div>
      <datalist id=${ENTITY_LIST}>
        ${this._timeEntities.map(
          (entityId) =>
            html`<option value=${entityId}>
              ${this._entityName(entityId) ?? entityId}
            </option>`,
        )}
      </datalist>
      <datalist id=${ANY_ENTITY_LIST}>
        ${this._anyEntities.map(
          (entityId) =>
            html`<option value=${entityId}>
              ${this._entityName(entityId) ?? entityId}
            </option>`,
        )}
      </datalist>
      <datalist id=${SCRIPT_LIST}>
        ${this._scripts.map(
          (entityId) =>
            html`<option value=${entityId}>
              ${this._entityName(entityId) ?? entityId}
            </option>`,
        )}
      </datalist>
    `;
  }

  private _identity() {
    const draft = this._draft;
    const description = draft.body.description ?? "";
    return html`
      <section>
        <label class="field">
          <span>Name</span>
          <input
            type="text"
            .value=${draft.name}
            ?disabled=${!this._canWrite}
            @input=${(event: Event) =>
              (this._draft = withName(draft, inputValue(event)))}
          />
        </label>
        ${this._original
          ? nothing
          : html`
              <label class="field">
                <span>Entity id</span>
                <input
                  type="text"
                  placeholder="slugged from the name"
                  .value=${draft.object_id ?? ""}
                  ?disabled=${!this._canWrite}
                  @input=${(event: Event) => {
                    const typed = inputValue(event);
                    this._draft = withObjectId(draft, typed === "" ? null : typed);
                  }}
                />
              </label>
              <p class="muted">
                Offered once, on creation only. It becomes the entity id,
                so renaming the schedule later leaves it alone rather than
                breaking every automation and card that names it.
              </p>
            `}
        <label class="field">
          <span>Description</span>
          <input
            type="text"
            .value=${description}
            ?disabled=${!this._canWrite}
            @input=${(event: Event) =>
              (this._draft = withBody(draft, { description: inputValue(event) }))}
          />
        </label>
      </section>
    `;
  }

  /**
   * D72 — the recurrence sits above the shape, because it says which days the
   * shape below is a shape *of*. A control that said "every Saturday" under the
   * rails would be answering a question the user had already stopped asking.
   */
  private _recurrence() {
    const recurrence = this._draft.body.recurrence;
    const kind = recurrence?.kind;
    const switchable = kind === undefined || kind === RECUR_WEEKDAYS || kind === RECUR_DAY_SET;
    return html`
      <section>
        <h3>Which days</h3>
        ${recurrence === undefined
          ? html`
              <p class="muted">
                Every day — almanac's own default, which nobody has overridden.
              </p>
            `
          : nothing}
        ${switchable
          ? html`
              <almanac-segmented
                label="Which days"
                .options=${[
                  { id: RECUR_WEEKDAYS, label: "Days of the week" },
                  { id: RECUR_DAY_SET, label: "Day set" },
                ]}
                .selected=${kind ?? ""}
                ?disabled=${!this._canWrite}
                .onSelect=${(id: string) => this._setRecurrence(id)}
              ></almanac-segmented>
            `
          : nothing}
        ${kind === RECUR_WEEKDAYS ? this._weekdays(recurrence!) : nothing}
        ${kind === RECUR_DAY_SET ? this._daySetPicker(recurrence!) : nothing}
        ${switchable
          ? nothing
          : html`
              <p class="muted mono">${kind}</p>
              <p class="muted">
                This recurrence is shown rather than offered: almanac can read a
                ${kind} recurrence but this editor cannot yet build one, and a
                control that switched away from it would discard a shape it could
                not put back.
              </p>
            `}
      </section>
    `;
  }

  private _weekdays(recurrence: StoredRecurrence) {
    const chosen = new Set(
      Array.isArray(recurrence["weekdays"])
        ? (recurrence["weekdays"] as string[])
        : [],
    );
    return html`
      <div class="row">
        ${WEEKDAYS.map((day) => {
          const on = chosen.has(day);
          // The last remaining day does not turn off: `Length(min=1)` would
          // refuse the save, and "no days" is not a state worth representing
          // on the way to a state the schema rejects.
          const last = on && chosen.size === 1;
          const name = WEEKDAY_LABELS[day] ?? day;
          return haButton(
            {
              pressed: on,
              disabled: !this._canWrite || last,
              label: last
                ? `${name}: a schedule needs at least one day`
                : name,
              onClick: () => this._toggleWeekday(day, chosen),
            },
            name,
          );
        })}
      </div>
    `;
  }

  private _daySetPicker(recurrence: StoredRecurrence) {
    const chosen = String(recurrence["day_set_id"] ?? "");
    const known = this._daySets.some((row) => row.id === chosen);
    return html`
      <label class="field">
        <span>Day set</span>
        <select
          ?disabled=${!this._canWrite}
          @change=${(event: Event) =>
            this._writeRecurrence({
              kind: RECUR_DAY_SET,
              day_set_id: inputValue(event),
            })}
        >
          ${chosen === "" || known
            ? nothing
            : html`<option value=${chosen} selected>${chosen}</option>`}
          ${this._daySets.map(
            (row) => html`
              <option value=${row.id} ?selected=${row.id === chosen}>
                ${row.name}
              </option>
            `,
          )}
        </select>
      </label>
      ${chosen !== "" && !known && this._daySets.length > 0
        ? html`<p class="problem">
            This schedule names a day set that no longer exists. It is kept as it
            is until you pick another one.
          </p>`
        : nothing}
    `;
  }

  private _setRecurrence(kind: string): void {
    if (kind === RECUR_WEEKDAYS) {
      this._writeRecurrence({ kind, weekdays: [...WEEKDAYS] });
      return;
    }
    const first = this._daySets[0];
    this._writeRecurrence({ kind, day_set_id: first?.id ?? "" });
  }

  private _toggleWeekday(day: string, chosen: Set<string>): void {
    const next = new Set(chosen);
    if (next.has(day)) {
      next.delete(day);
    } else {
      next.add(day);
    }
    // Written back in `WEEKDAYS` order, not click order: the stored list is a
    // set and D140's diff is structural, so a reordering that means nothing
    // would otherwise register as a change and be sent.
    this._writeRecurrence({
      kind: RECUR_WEEKDAYS,
      weekdays: WEEKDAYS.filter((name) => next.has(name)),
    });
  }

  private _writeRecurrence(recurrence: StoredRecurrence): void {
    this._draft = withBody(this._draft, { recurrence });
  }

  /** The preview track and the rule list, selection-synced. */
  private _shape() {
    const track = draftTrack(this._rules, this._draft.id, this._entityName);
    return html`
      <section>
        <h3>The shape of a day</h3>
        <almanac-track
          .track=${track}
          size="full"
          preview
          selected=${this._selected ?? ""}
        ></almanac-track>
        ${track.rails.length === 0
          ? html`<p class="muted">
              Nothing to draw yet — every rule still needs a time.
            </p>`
          : nothing}
        <ul class="rules">
          ${this._rules.map((rule, index) => this._ruleRow(rule, index))}
        </ul>
        <div class="row">
          ${haButton(
            {
              icon: ICON_ADD,
              disabled: !this._canWrite,
              onClick: () => this._addRule(clockAtRule()),
            },
            "Add a moment",
          )}
          ${haButton(
            {
              icon: ICON_ADD,
              disabled: !this._canWrite,
              onClick: () =>
                this._addRule({
                  kind: "during",
                  start_anchor: { kind: "clock", at: DEFAULT_CLOCK_TIME },
                  end: { kind: "duration", duration: DEFAULT_DURATION },
                }),
            },
            "Add an interval",
          )}
        </div>
      </section>
    `;
  }

  private _ruleRow(rule: WritableRule, index: number) {
    const id = String(index);
    const selected = id === this._selected;
    return html`
      <li class="rule ${selected ? "selected" : ""}">
        <button
          class="pick tappable"
          aria-pressed=${selected}
          @click=${() => (this._selected = id)}
        >
          <span class="kind muted">${rule.kind === "at" ? "at" : "during"}</span>
          <span>${this._ruleSummary(rule)}</span>
          ${rule.enabled === false
            ? html`<span class="pill disarmed">off</span>`
            : nothing}
        </button>
        ${haButton(
          {
            kind: "danger",
            icon: ICON_REMOVE,
            disabled: !this._canWrite,
            label: "Remove this rule",
            onClick: () => this._removeRule(index),
          },
          "Remove",
        )}
      </li>
    `;
  }

  /** A rule in a few words, from its anchors alone — the fields this editor owns. */
  private _ruleSummary(rule: WritableRule): string {
    if (rule.kind === "at") {
      return rule.anchor ? this._anchorSummary(rule.anchor) : "no time yet";
    }
    const start = rule.start_anchor
      ? this._anchorSummary(rule.start_anchor)
      : "no start yet";
    if (!rule.end) {
      return `${start} → no end yet`;
    }
    return rule.end.kind === "duration"
      ? `${start} for ${duration(rule.end.duration)}`
      : `${start} → ${this._anchorSummary(rule.end.anchor)}`;
  }

  private _anchorSummary(anchor: StoredAnchor): string {
    if (anchor.kind === "clock") {
      return anchor.at;
    }
    const offset = anchor.offset === 0 ? "" : ` ${offsetLabel(anchor.offset)}`;
    if (anchor.kind === "entity_time") {
      const name = this._entityName(anchor.entity_id) ?? anchor.entity_id;
      return `${name}${offset}`;
    }
    const offering = this._offering(anchor.domain, anchor.key);
    const label = offering?.display_name ?? `${anchor.domain}.${anchor.key}`;
    const edge = anchor.edge === "end" ? " (end)" : "";
    return `${label}${edge}${offset}`;
  }

  private _offering(domain: string, key: string): WireOffering | undefined {
    return this._catalogue?.offerings.find(
      (offering) => offering.domain === domain && offering.key === key,
    );
  }

  // --- the selected rule ----------------------------------------------------

  private _rulePanel() {
    const index = this._selectedIndex;
    if (index === null) {
      return nothing;
    }
    const rule = this._rules[index];
    if (!rule) {
      return nothing;
    }
    return html`
      <section class="panel">
        <h3>Rule ${index + 1}</h3>
        <label class="check">
          <input
            type="checkbox"
            .checked=${rule.enabled ?? true}
            ?disabled=${!this._canWrite}
            @change=${(event: Event) =>
              this._patchRule(index, { enabled: inputChecked(event) })}
          />
          <span>Armed</span>
        </label>
        <p class="muted">
          A disarmed rule stays on the shape above, drawn hollow. It is
          still what the schedule says; it just does not fire.
        </p>
        ${rule.kind === "at"
          ? html`
              ${this._anchorFields("Time", rule.anchor, (anchor) =>
                this._patchRule(index, { anchor }),
              )}
              ${this._conditions("conditions", rule.conditions ?? [])}
              ${this._actions("actions", "What it does", rule.actions ?? [])}
              ${this._advanced(`rule:${index}`, html`
                ${this._policy(index, rule.condition_policy ?? { kind: "skip" })}
                ${this._grace(index, rule.grace ?? null)}
              `)}
            `
          : html`
              ${this._anchorFields("Starts", rule.start_anchor, (anchor) =>
                this._patchRule(index, { start_anchor: anchor }),
              )}
              ${this._endFields(index, rule)}
              ${this._conditions("conditions", rule.conditions ?? [])}
              <almanac-desired
                name="state"
                .state=${rule.state ?? null}
                ?disabled=${!this._canWrite}
                entityList=${ANY_ENTITY_LIST}
                scriptList=${SCRIPT_LIST}
                .hass=${this.hass}
                .haReady=${this.haReady}
              ></almanac-desired>
              ${this._actions("enter", "When it starts", rule.enter_actions ?? [])}
              ${this._actions("exit", "When it ends", rule.exit_actions ?? [])}
              ${this._advanced(`rule:${index}`, html`
                ${this._onExit(index, rule.on_exit ?? { kind: "leave" })}
                <label class="check">
                  <input
                    type="checkbox"
                    .checked=${rule.latch ?? false}
                    ?disabled=${!this._canWrite}
                    @change=${(event: Event) =>
                      this._patchRule(index, { latch: inputChecked(event) })}
                  />
                  <span>Keep going even if the conditions stop holding</span>
                </label>
                <p class="muted">
                  Unlatched, the conditions govern the exit and the interval ends
                  when they stop holding. Latched, it runs to the end above
                  regardless — "once the lights are on for the evening, leave
                  them on even if the motion sensor gives up".
                </p>
              `)}
            `}
      </section>
    `;
  }

  /**
   * One action list, named for the handler below.
   *
   * Three of these are reachable from one rule panel, a fourth from inside a
   * desired state and a fifth from the completion section, which is the whole
   * reason the builders carry a `name` (D157): the editor gets one event per
   * list and has to know which list it was.
   */
  private _actions(name: string, label: string, actions: StoredAction[]) {
    return html`
      <almanac-actions
        name=${name}
        label=${label}
        .actions=${actions}
        ?disabled=${!this._canWrite}
        entityList=${ANY_ENTITY_LIST}
        scriptList=${SCRIPT_LIST}
        .hass=${this.hass}
        .haReady=${this.haReady}
      ></almanac-actions>
    `;
  }

  private _conditions(
    name: string,
    conditions: StoredCondition[],
    label = "Only when",
  ) {
    return html`
      <almanac-conditions
        name=${name}
        label=${label}
        .conditions=${conditions}
        .daySets=${this._daySets}
        ?disabled=${!this._canWrite}
        entityList=${ANY_ENTITY_LIST}
        .hass=${this.hass}
        .haReady=${this.haReady}
      ></almanac-conditions>
    `;
  }

  /**
   * D26's two policies, and the deadline one of them requires.
   *
   * The wording is the point. Today's card offers "re-evaluate when conditions
   * change", which is an unbounded wait described as a refresh — the thing D26
   * exists to name. So the choice here is between skipping the occurrence and
   * waiting for it, and the wait says how long it is prepared to wait.
   */
  private _policy(index: number, policy: StoredConditionPolicy) {
    return html`
      <div class="anchor">
        <label class="field">
          <span>If they fail</span>
          <select
            ?disabled=${!this._canWrite}
            @change=${(event: Event) =>
              this._patchRule(index, {
                condition_policy:
                  inputValue(event) === "wait_until"
                    ? {
                        kind: "wait_until",
                        deadline:
                          policy.kind === "wait_until" ? policy.deadline : 3600,
                      }
                    : { kind: "skip" },
              })}
          >
            <option value="skip" ?selected=${policy.kind === "skip"}>
              Skip this one
            </option>
            <option
              value="wait_until"
              ?selected=${policy.kind === "wait_until"}
            >
              Wait for them, up to a point
            </option>
          </select>
        </label>
        ${policy.kind === "wait_until"
          ? html`
              <label class="field">
                <span>Waiting at most</span>
                <input
                  type="number"
                  step="any"
                  min="1"
                  .value=${String(policy.deadline / 60)}
                  ?disabled=${!this._canWrite}
                  @change=${(event: Event) =>
                    this._patchRule(index, {
                      condition_policy: {
                        kind: "wait_until",
                        deadline: Math.max(
                          1,
                          Math.abs(secondsFrom(inputValue(event))),
                        ),
                      },
                    })}
                />
                <span class="muted">minutes</span>
              </label>
              <p class="muted">
                A deadline is required when it waits. Without one the wait
                is unbounded, which is what today's "re-evaluate when conditions
                change" silently does.
              </p>
            `
          : nothing}
      </div>
    `;
  }

  /**
   * D41's catch-up window, blank meaning none.
   *
   * Blank is the default and the safe one: a missed occurrence is logged and not
   * fired unless the rule opts in, because replaying an announcement three hours
   * late is worse than not making it.
   */
  private _grace(index: number, grace: number | null) {
    return html`
      <label class="field">
        <span>If it was missed</span>
        <input
          type="number"
          step="any"
          min="0"
          placeholder="do not catch up"
          .value=${grace === null ? "" : String(grace / 60)}
          ?disabled=${!this._canWrite}
          @change=${(event: Event) => {
            const typed = inputValue(event).trim();
            this._patchRule(index, {
              grace:
                typed === ""
                  ? null
                  : Math.max(1, Math.abs(secondsFrom(typed))),
            });
          }}
        />
        <span class="muted">minutes late, still fire</span>
      </label>
    `;
  }

  /** D3's three exits, and the state the third one carries. */
  private _onExit(index: number, onExit: StoredOnExit) {
    return html`
      <label class="field">
        <span>On the way out</span>
        <select
          ?disabled=${!this._canWrite}
          @change=${(event: Event) =>
            this._patchRule(index, {
              on_exit: this._onExitFor(inputValue(event), onExit),
            })}
        >
          <option value="leave" ?selected=${onExit.kind === "leave"}>
            Leave everything as it is
          </option>
          <option value="restore" ?selected=${onExit.kind === "restore"}>
            Put back what was there before
          </option>
          <option value="apply" ?selected=${onExit.kind === "apply"}>
            Set something else
          </option>
        </select>
      </label>
      ${onExit.kind === "restore"
        ? html`<p class="muted">
            "Before" is what almanac read when the interval started, saved then
            and not looked at again — so if something else changed those
            entities in the meantime, this puts back the older value, not
            theirs.
          </p>`
        : nothing}
      ${onExit.kind === "apply"
        ? html`<almanac-desired
            name="on_exit"
            label="Set this on the way out"
            required
            .state=${onExit.state}
            ?disabled=${!this._canWrite}
            entityList=${ANY_ENTITY_LIST}
            scriptList=${SCRIPT_LIST}
            .hass=${this.hass}
            .haReady=${this.haReady}
          ></almanac-desired>`
        : nothing}
      <p class="muted">
        Changing this does not change an interval that is already running: the
        exit is decided when the interval is entered and read back from that
        promise, so an edit mid-interval takes effect next time.
      </p>
    `;
  }

  /** What picking an exit produces, carrying the state across where it can. */
  private _onExitFor(value: string, current: StoredOnExit): StoredOnExit {
    if (value === "apply") {
      return {
        kind: "apply",
        state: current.kind === "apply" ? current.state : newDesiredState(),
      };
    }
    return value === "restore" ? { kind: "restore" } : { kind: "leave" };
  }


  /**
   * One anchor, drawn by `almanac-time-picker`: a tile for the kind of time, one
   * control for its value, a signed stepper for the offset, and a line that says
   * it back and asks the engine when it next happens.
   */
  private _anchorFields(
    label: string,
    anchor: StoredAnchor | undefined,
    write: (anchor: StoredAnchor) => void,
  ) {
    return html`
      <almanac-time-picker
        .hass=${this.hass}
        .haReady=${this.haReady}
        .anchor=${anchor ?? CLOCK_ANCHOR}
        .offerings=${this._catalogue?.offerings ?? NO_OFFERINGS}
        .usedTimes=${this._usedTimes()}
        .at=${this._previewAt}
        entityList=${ENTITY_LIST}
        ?disabled=${!this._canWrite}
        .onChange=${write}
        label=${label}
      ></almanac-time-picker>
    `;
  }

  /** The clock times already in the draft, in rule order, for the picker's chips. */
  private _usedTimes(): string[] {
    const times: string[] = [];
    for (const rule of this._rules) {
      const anchors =
        rule.kind === "at"
          ? [rule.anchor]
          : [rule.start_anchor, rule.end?.kind === "anchor" ? rule.end.anchor : undefined];
      for (const anchor of anchors) {
        if (anchor?.kind === "clock") {
          times.push(anchor.at);
        }
      }
    }
    return times;
  }

  private _endFields(index: number, rule: WritableDuringRule) {
    const end = rule.end;
    const kind = end?.kind ?? "duration";
    return html`
      <div class="anchor">
        <label class="field">
          <span>Ends</span>
          <select
            ?disabled=${!this._canWrite}
            @change=${(event: Event) =>
              this._patchRule(index, { end: this._endFor(inputValue(event), end) })}
          >
            <option value="duration" ?selected=${kind === "duration"}>
              After a while
            </option>
            <option value="anchor" ?selected=${kind === "anchor"}>
              At another time
            </option>
          </select>
        </label>
        ${end?.kind === "duration"
          ? html`
              <label class="field">
                <span>For</span>
                <input
                  type="number"
                  step="any"
                  min="0"
                  .value=${String(end.duration / 60)}
                  ?disabled=${!this._canWrite}
                  @change=${(event: Event) =>
                    this._patchRule(index, {
                      end: {
                        kind: "duration",
                        duration: Math.abs(secondsFrom(inputValue(event))),
                      },
                    })}
                />
                <span class="muted">minutes</span>
              </label>
              <p class="muted">
                A duration has no time of its own, so this end is drawn as a
                second stage on the start's line.
              </p>
            `
          : nothing}
        ${end?.kind === "anchor"
          ? this._anchorFields("Until", end.anchor, (anchor) =>
              this._patchRule(index, { end: { kind: "anchor", anchor } }),
            )
          : nothing}
      </div>
    `;
  }

  private _endFor(value: string, current: StoredEnd | undefined): StoredEnd {
    if (value === "duration") {
      return {
        kind: "duration",
        duration: current?.kind === "duration" ? current.duration : DEFAULT_DURATION,
      };
    }
    return {
      kind: "anchor",
      anchor:
        current?.kind === "anchor"
          ? current.anchor
          : { kind: "clock", at: DEFAULT_CLOCK_TIME },
    };
  }

  private _patchRule(index: number, patch: Partial<WritableRule>): void {
    const rule = this._rules[index];
    if (!rule) {
      return;
    }
    this._draft = replaceRuleAt(this._draft, index, {
      ...rule,
      ...patch,
    } as WritableRule);
  }

  private _addRule(rule: WritableRule): void {
    this._draft = addRule(this._draft, rule);
    this._selected = String(this._rules.length - 1);
  }

  private _removeRule(index: number): void {
    this._draft = removeRuleAt(this._draft, index);
    // The selection is an index, so removing a rule renumbers the ones after it.
    // Clamping rather than clearing keeps the panel open on the rule that took
    // the removed one's place, which is what the user is looking at.
    const remaining = this._rules.length;
    this._selected =
      remaining === 0 ? undefined : String(Math.min(index, remaining - 1));
  }

  // --- the schedule's own completion (D46) ----------------------------------

  /**
   * D46's three axes, one section.
   *
   * Three because they are three separate questions and today's scheduler
   * answers them with one word: "Stop" means pause and "Delete" means delete,
   * and for a multi-slot scheme "after it triggers" is undocumented. So *when*
   * it is finished, *what* happens then, and *which* occurrences count towards
   * it are each asked, and each has a visible value rather than an absence.
   *
   * Written whole, every time, and `DEFAULT_COMPLETION` is why: D140's update is
   * a shallow top-level merge, so sending `{finished_when: ...}` alone would
   * re-default `then` and `count_on` to whatever the schema says. One axis
   * changing has to carry the other two with it.
   */
  private _completion() {
    const completion = this._draft.body.completion ?? DEFAULT_COMPLETION;
    const finished = completion.finished_when;
    const then = completion.then;
    return html`
      <section>
        <h3>When it is finished</h3>
        <label class="field">
          <span>Finished</span>
          <select
            ?disabled=${!this._canWrite}
            @change=${(event: Event) =>
              this._writeCompletion({
                finished_when: this._finishedFor(inputValue(event), finished),
              })}
          >
            ${FINISHED_KINDS.map(
              ([kind, label]) => html`
                <option value=${kind} ?selected=${finished.kind === kind}>
                  ${label}
                </option>
              `,
            )}
          </select>
        </label>
        ${finished.kind === "occurrences"
          ? html`
              <label class="field">
                <span>After</span>
                <input
                  type="number"
                  min="1"
                  step="1"
                  .value=${String(finished.count)}
                  ?disabled=${!this._canWrite}
                  @change=${(event: Event) =>
                    this._writeCompletion({
                      finished_when: {
                        kind: "occurrences",
                        count: countFrom(inputValue(event), finished.count),
                      },
                    })}
                />
                <span class="muted">of them</span>
              </label>
            `
          : nothing}
        ${finished.kind === "date"
          ? html`
              <label class="field">
                <span>On</span>
                <input
                  type="date"
                  .value=${finished.date}
                  ?disabled=${!this._canWrite}
                  @change=${(event: Event) => {
                    // A cleared date keeps the old one. `_day` refuses an empty
                    // string, and a field the user can empty into a save that
                    // fails at the backend is a field that validates nowhere.
                    const typed = inputValue(event);
                    this._writeCompletion({
                      finished_when: {
                        kind: "date",
                        date: typed === "" ? finished.date : typed,
                      },
                    });
                  }}
                />
              </label>
              <p class="muted">
                The day named is included: "finish on 31 December" means
                through the 31st, not up to it.
              </p>
            `
          : nothing}
        ${finished.kind === "condition"
          ? this._conditions(
              "finished_when",
              finished.conditions,
              "Finished when",
            )
          : nothing}
        <label class="field">
          <span>Then</span>
          <select
            ?disabled=${!this._canWrite}
            @change=${(event: Event) =>
              this._writeCompletion({
                then: this._thenFor(inputValue(event), then),
              })}
          >
            ${THEN_KINDS.map(
              ([kind, label]) => html`
                <option value=${kind} ?selected=${then.kind === kind}>
                  ${label}
                </option>
              `,
            )}
          </select>
        </label>
        ${then.kind === "action"
          ? this._actions("then", "Run when finished", then.actions)
          : nothing}
        ${then.kind === "disable" || then.kind === "delete"
          ? html`<p class="muted">
              It waits for any interval still being held. A schedule that
              switched itself off mid-interval would leave the world in a state
              it created and nothing left to undo it.
            </p>`
          : nothing}
        <label class="field">
          <span>Counting</span>
          <select
            ?disabled=${!this._canWrite}
            @change=${(event: Event) =>
              this._writeCompletion({
                count_on: inputValue(event) as StoredCountOn,
              })}
          >
            ${COUNT_ON_KINDS.map(
              ([kind, label]) => html`
                <option value=${kind} ?selected=${completion.count_on === kind}>
                  ${label}
                </option>
              `,
            )}
          </select>
        </label>
        <p class="muted">
          "Actually did something" is stricter than it looks: a rule with nothing
          to do has not succeeded at anything, so a schedule counting that never
          advances on an occurrence that performed no actions. That is the point
          — it is what makes "delete after it triggers" mean after it triggered.
        </p>
      </section>
    `;
  }

  /** What picking a *finished when* produces, carrying across what it can. */
  private _finishedFor(
    value: string,
    current: StoredFinishedWhen,
  ): StoredFinishedWhen {
    if (value === "occurrences") {
      return {
        kind: "occurrences",
        count: current.kind === "occurrences" ? current.count : 1,
      };
    }
    if (value === "date") {
      return {
        kind: "date",
        date: current.kind === "date" ? current.date : today(),
      };
    }
    if (value === "condition") {
      return {
        kind: "condition",
        conditions: current.kind === "condition" ? current.conditions : [],
      };
    }
    if (value === "one_rule_fired" || value === "cycle") {
      return { kind: value };
    }
    return { kind: "never" };
  }

  private _thenFor(value: string, current: StoredThen): StoredThen {
    if (value === "action") {
      return {
        kind: "action",
        actions: current.kind === "action" ? current.actions : [],
      };
    }
    if (value === "disable" || value === "delete") {
      return { kind: value };
    }
    return { kind: "keep" };
  }

  private _writeCompletion(patch: Partial<StoredCompletion>): void {
    this._draft = withBody(this._draft, {
      completion: {
        ...(this._draft.body.completion ?? DEFAULT_COMPLETION),
        ...patch,
      },
    });
  }

  // --- what the builders send back ------------------------------------------
  //
  // One handler per builder, routing on the `name` the template gave it. Each
  // one writes a whole value: a rule's `actions` is one field of the draft as
  // far as `replaceRuleAt` is concerned, and the alternative — an event per
  // action field, carrying an index and a key — would make this file reassemble
  // a list it does not otherwise touch.

  private _onActions = (event: CustomEvent<ActionsChangedDetail>): void => {
    const index = this._selectedIndex;
    const { name, actions } = event.detail;
    if (name === "then") {
      this._writeCompletion({ then: { kind: "action", actions } });
      return;
    }
    if (index === null) {
      return;
    }
    if (name === "actions") {
      this._patchRule(index, { actions });
    } else if (name === "enter") {
      this._patchRule(index, { enter_actions: actions });
    } else if (name === "exit") {
      this._patchRule(index, { exit_actions: actions });
    }
    // Anything else is a list belonging to a builder that should have stopped
    // the event itself -- a desired state's `override`. Ignored rather than
    // guessed at, so a missing `stopPropagation` shows up as a control that
    // does nothing rather than as a write to the wrong field.
  };

  private _onConditions = (
    event: CustomEvent<ConditionsChangedDetail>,
  ): void => {
    const { name, conditions } = event.detail;
    if (name === "finished_when") {
      this._writeCompletion({ finished_when: { kind: "condition", conditions } });
      return;
    }
    const index = this._selectedIndex;
    if (name === "conditions" && index !== null) {
      this._patchRule(index, { conditions });
    }
  };

  private _onDesired = (event: CustomEvent<DesiredChangedDetail>): void => {
    const index = this._selectedIndex;
    const { name, state } = event.detail;
    if (index === null) {
      return;
    }
    if (name === "state") {
      this._patchRule(index, { state });
    } else if (name === "on_exit" && state !== null) {
      // `required` on that element means null never arrives, and the check is
      // here as well because `on_exit: apply` has nowhere to put one: the arm
      // carries `state` as a required key, so a null would have to become a
      // different arm, which is a choice this handler must not make silently.
      this._patchRule(index, { on_exit: { kind: "apply", state } });
    }
  };

  /**
   * D78's footprint, and D62's links out of it.
   *
   * Three of the four groups hold entity ids, and an entity id is something
   * Home Assistant can open: those become buttons that fire core's more-info
   * event, which is spelled in exactly one file in this repository (D159,
   * `./moreinfo`). The fourth holds `domain.service` pairs, which name no entity
   * and have no dialog, so they stay as plain pills — a link that led nowhere
   * would be worse than a name, because the footprint's whole job is to be
   * believed.
   *
   * `script.*` ids are in the linkable set and not the service one: D78 collects
   * them from `script.turn_on` targets and from the `script.<name>` form alike,
   * and both are entities with a dialog showing the last run.
   */
  private _footprint() {
    const footprint = footprintOf(this._draft);
    const groups: [string, string[], boolean][] = [
      ["Writes to", footprint.entities, true],
      ["Reads a time from", footprint.reads, true],
      ["Runs", footprint.scripts, true],
      ["Calls", footprint.services, false],
    ];
    const shown = groups.filter(([, items]) => items.length > 0);
    if (shown.length === 0 && footprint.unexpanded === 0) {
      return nothing;
    }
    return html`
      <section>
        <h3>What this touches</h3>
        ${shown.map(
          ([label, items, linkable]) => html`
            <p>
              <span class="muted">${label}</span>
              ${items.map((item) =>
                linkable
                  ? html`<button
                      class="pill mono tappable"
                      title="Open ${item}"
                      @click=${() => openMoreInfo(this, item)}
                    >
                      ${item}
                    </button>`
                  : html`<span class="pill mono">${item}</span>`,
              )}
            </p>
          `,
        )}
        ${footprint.unexpanded > 0
          ? html`<p class="muted">
              ${footprint.unexpanded} target${footprint.unexpanded === 1 ? "" : "s"}
              name an area, floor, device or label. almanac does not expand those
              to entities, so what they reach is not listed here.
            </p>`
          : nothing}
      </section>
    `;
  }

  private _footer(found: string[]) {
    const dirty = isDirty(this._draft, this._original);
    const blocked = !this._canWrite || this._saving || found.length > 0 || !dirty;
    return html`
      <footer>
        ${haButton(
          {
            kind: "primary",
            disabled: blocked,
            onClick: () => void this._save(),
          },
          this._saving ? "Saving…" : "Save",
        )}
        ${haButton({ onClick: () => this._close(false) }, "Cancel")}
        ${this._original
          ? haButton(
              {
                kind: "danger",
                icon: ICON_REMOVE,
                disabled: !this._canWrite || this._saving,
                onClick: () => void this._delete(),
              },
              "Delete",
            )
          : nothing}
        ${dirty || this._original === null
          ? nothing
          : html`<span class="muted">Nothing has changed.</span>`}
      </footer>
    `;
  }

  // --- the three writes -----------------------------------------------------

  private async _save(): Promise<void> {
    const hass = this.hass;
    if (!hass || this._saving) {
      return;
    }
    this._saving = true;
    this._error = undefined;
    try {
      const original = this._original;
      const saved = original
        ? await updateSchedule(hass, original.id, toUpdate(this._draft, original))
        : await createSchedule(hass, toCreate(this._draft));
      // Re-seeded from what came back, not from what was sent: the backend
      // applies the defaults and mints the id, and a draft that kept the sent
      // shape would show the next diff against a stale original.
      this._original = saved;
      this._draft = draftOf(saved);
      this._close(true, saved.id);
    } catch (error) {
      this._error = this._writeError(error);
    } finally {
      this._saving = false;
    }
  }

  private async _delete(): Promise<void> {
    const hass = this.hass;
    const original = this._original;
    if (!hass || !original || this._saving) {
      return;
    }
    this._saving = true;
    this._error = undefined;
    try {
      await deleteSchedule(hass, original.id);
      this._close(true, original.id);
    } catch (error) {
      this._error = this._writeError(error);
    } finally {
      this._saving = false;
    }
  }

  private _writeError(error: unknown): string {
    if (isUnauthorized(error)) {
      return "This account is not allowed to change schedules.";
    }
    if (isNotLoaded(error)) {
      return "almanac is reloading. Nothing was saved; try again in a moment.";
    }
    return error instanceof Error ? error.message : String(error);
  }

  private _close(saved: boolean, scheduleId?: string): void {
    this.dispatchEvent(
      new CustomEvent<EditorClosedDetail>(EDITOR_CLOSED, {
        detail: { saved, scheduleId: scheduleId ?? this._original?.id ?? null },
        bubbles: true,
        composed: true,
      }),
    );
  }

  static override styles = [
    almanacTokens,
    almanacText,
    // `almanacForm` holds every control rule this file used to carry its own
    // copy of. It moved at step 9e, when four builders started rendering the
    // same `.field`, `.panel` and button classes inside their own shadow roots:
    // a copy apiece is four chances to disagree about what a disabled input
    // looks like.
    almanacForm,
    almanacHitTarget,
    css`
      :host {
        display: block;
        background: var(--card-background-color);
        border-radius: var(--ha-card-border-radius, 12px);
      }

      .shell {
        padding: var(--almanac-gap-md);
        display: flex;
        flex-direction: column;
        gap: var(--almanac-gap-md);
        box-sizing: border-box;
      }

      header {
        display: flex;
        align-items: baseline;
        gap: var(--almanac-gap-sm);
      }

      h2 {
        font-size: 1.1rem;
        margin: 0;
        flex: 1 1 auto;
      }



      .advanced {
        margin-top: var(--almanac-gap-sm);
      }

      .advanced > summary {
        cursor: pointer;
        min-height: 44px;
        display: flex;
        align-items: center;
        font-weight: 500;
      }

      .anchor {
        display: flex;
        flex-direction: column;
        gap: var(--almanac-gap-sm);
        margin-top: var(--almanac-gap-sm);
      }

      /* A nested anchor — a during rule's "until" — is indented, because
         otherwise "Ends / At another time / Until" reads as three peers. */
      .anchor .anchor {
        border-left: 2px solid var(--almanac-rail);
        padding-left: var(--almanac-gap-sm);
      }


      .rules {
        list-style: none;
        margin: 0;
        padding: 0;
        display: flex;
        flex-direction: column;
        gap: var(--almanac-gap-xs);
      }

      .rule {
        display: flex;
        align-items: center;
        gap: var(--almanac-gap-xs);
      }

      .pick {
        flex: 1 1 auto;
        display: flex;
        align-items: center;
        gap: var(--almanac-gap-sm);
        text-align: left;
        font-size: 0.875rem;
      }

      .rule.selected .pick {
        border-color: var(--almanac-now);
      }

      .kind {
        flex: 0 0 3.5rem;
        font-size: 0.75rem;
      }

      .problems {
        list-style: none;
        margin: 0;
        padding: 0;
      }

      footer {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: var(--almanac-gap-sm);
      }


      almanac-track {
        overflow-x: auto;
      }

    `,
  ];
}

declare global {
  interface HTMLElementTagNameMap {
    "almanac-editor": AlmanacEditor;
  }
}
