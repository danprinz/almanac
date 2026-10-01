// The editor: one schedule, open for writing.
//
// This is the first file in the frontend that *changes* anything, and almost
// everything in it was decided elsewhere. `draft.ts` holds the edit algebra and
// the two write shapes, `api.ts` the three commands, `rails.ts::draftTrack` the
// drawing of an unsaved schedule and `track.ts` the `full` size that makes its
// stages clickable. What is left here is the arrangement: which control sits
// where, what each one writes, and which of them is not offered at all.
//
// **D149 — the scope is anchors, arming, ends, rules and the schedule's own
// identity, and nothing else.** Actions, conditions, completion and desired
// state are read from the draft, carried through a save untouched, and shown as
// counts. That is not a staging convenience; it is the only safe way to ship an
// editor before the action builder exists. A partial action editor renders the
// `service` and `target` it understands and silently drops the `data` keys it
// does not, and D140's diff would then send the truncated action as a change —
// so opening an imported or YAML-authored schedule and pressing Save would
// *destroy* it. Showing a count destroys nothing. The builders are step 9e.
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
import {
  DEFAULT_CLOCK_TIME,
  addRule,
  clockAtRule,
  draftOf,
  footprintOf,
  isDirty,
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
import { duration, offsetLabel } from "./format";
import type { HomeAssistant } from "./ha";
import { draftTrack } from "./rails";
import type {
  StoredAnchor,
  StoredEnd,
  StoredRecurrence,
  StoredSchedule,
} from "./stored";
import { almanacHitTarget, almanacText, almanacTokens } from "./styles";
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
 * How the anchor `<select>` encodes a resolver offering.
 *
 * Takes the pair rather than a whole offering, because the stored anchor is the
 * other caller: matching a saved anchor to its option means spelling its value
 * the same way, and an anchor whose offering is absent (D17) still has to match
 * nothing rather than fail to be spelled.
 */
const offeringValue = (offering: { domain: string; key: string }): string =>
  `resolver:${offering.domain}:${offering.key}`;

/** The datalist id the entity anchor's input reads from. */
const ENTITY_LIST = "almanac-time-entities";

@customElement("almanac-editor")
export class AlmanacEditor extends LitElement {
  @property({ attribute: false }) public hass?: HomeAssistant | undefined;

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
  @state() private _saving = false;
  @state() private _error?: string | undefined;

  public override connectedCallback(): void {
    super.connectedCallback();
    // On the host, not in the template. `track.ts` exports the event's name as a
    // constant and Lit's `@name=` binding cannot take one, so a literal in the
    // template would be a second spelling of it that no test compares. The
    // event is `composed`, which is what lets it cross the track's shadow
    // boundary and reach this listener.
    this.addEventListener(STAGE_SELECTED, this._onStage as EventListener);
    this._load();
    void this._catalogues();
  }

  public override disconnectedCallback(): void {
    super.disconnectedCallback();
    this.removeEventListener(STAGE_SELECTED, this._onStage as EventListener);
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
          <button class="ghost tappable" @click=${() => this._close(false)}>
            Close
          </button>
        </header>
        ${this._canWrite
          ? nothing
          : html`<p class="problem">
              This account cannot change schedules, so everything below is
              read-only.
            </p>`}
        ${this._identity()} ${this._recurrence()} ${this._shape()}
        ${this._rulePanel()}
        ${found.length === 0
          ? nothing
          : html`<ul class="problems">
              ${found.map((note) => html`<li class="problem">${note}</li>`)}
            </ul>`}
        ${this._untouched()} ${this._footprint()}
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
                Offered once, on creation only (D144). It becomes the entity id,
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
              <div class="row">
                <button
                  class="chip tappable ${kind === RECUR_WEEKDAYS ? "selected" : ""}"
                  aria-pressed=${kind === RECUR_WEEKDAYS}
                  ?disabled=${!this._canWrite}
                  @click=${() => this._setRecurrence(RECUR_WEEKDAYS)}
                >
                  Days of the week
                </button>
                <button
                  class="chip tappable ${kind === RECUR_DAY_SET ? "selected" : ""}"
                  aria-pressed=${kind === RECUR_DAY_SET}
                  ?disabled=${!this._canWrite}
                  @click=${() => this._setRecurrence(RECUR_DAY_SET)}
                >
                  Day set
                </button>
              </div>
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
          return html`
            <button
              class="chip tappable ${on ? "selected" : ""}"
              aria-pressed=${on}
              ?disabled=${!this._canWrite || last}
              title=${last ? "A schedule needs at least one day." : ""}
              @click=${() => this._toggleWeekday(day, chosen)}
            >
              ${WEEKDAY_LABELS[day]}
            </button>
          `;
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
          <button
            class="ghost tappable"
            ?disabled=${!this._canWrite}
            @click=${() => this._addRule(clockAtRule())}
          >
            Add a moment
          </button>
          <button
            class="ghost tappable"
            ?disabled=${!this._canWrite}
            @click=${() =>
              this._addRule({
                kind: "during",
                start_anchor: { kind: "clock", at: DEFAULT_CLOCK_TIME },
                end: { kind: "duration", duration: DEFAULT_DURATION },
              })}
          >
            Add an interval
          </button>
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
        <button
          class="ghost tappable"
          ?disabled=${!this._canWrite}
          title="Remove this rule"
          @click=${() => this._removeRule(index)}
        >
          Remove
        </button>
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
              this._patchRule(index, {
                enabled: (event.target as HTMLInputElement).checked,
              })}
          />
          <span>Armed</span>
        </label>
        <p class="muted">
          A disarmed rule stays on the shape above, drawn hollow (D77). It is
          still what the schedule says; it just does not fire.
        </p>
        ${rule.kind === "at"
          ? this._anchorFields("Time", rule.anchor, (anchor) =>
              this._patchRule(index, { anchor }),
            )
          : html`
              ${this._anchorFields("Starts", rule.start_anchor, (anchor) =>
                this._patchRule(index, { start_anchor: anchor }),
              )}
              ${this._endFields(index, rule)}
            `}
      </section>
    `;
  }

  /**
   * One anchor: its kind, and whatever that kind needs.
   *
   * The three kinds are D6's, and the editor offers them as one list rather than
   * as a kind radio plus a value control, because what the user is choosing is
   * the event — `18:00`, `candle lighting`, `that sensor` — and the fact that
   * two of those are the same storage kind is not something they are deciding.
   */
  private _anchorFields(
    label: string,
    anchor: StoredAnchor | undefined,
    write: (anchor: StoredAnchor) => void,
  ) {
    const kinds = new Map<string, WireOffering[]>();
    for (const offering of this._catalogue?.offerings ?? []) {
      if (!offering.roles.includes("anchor")) {
        continue;
      }
      const group = kinds.get(offering.domain) ?? [];
      group.push(offering);
      kinds.set(offering.domain, group);
    }
    const value = anchor
      ? anchor.kind === "resolver"
        ? offeringValue(anchor)
        : anchor.kind
      : "clock";
    return html`
      <div class="anchor">
        <label class="field">
          <span>${label}</span>
          <select
            ?disabled=${!this._canWrite}
            @change=${(event: Event) =>
              write(this._anchorFor(inputValue(event), anchor))}
          >
            <option value="clock" ?selected=${value === "clock"}>
              A clock time
            </option>
            <option value="entity_time" ?selected=${value === "entity_time"}>
              An entity's time
            </option>
            ${[...kinds.entries()].map(
              ([domain, offerings]) => html`
                <optgroup label=${domain}>
                  ${offerings.map(
                    (offering) => html`
                      <option
                        value=${offeringValue(offering)}
                        ?selected=${value === offeringValue(offering)}
                      >
                        ${offering.display_name}
                      </option>
                    `,
                  )}
                </optgroup>
              `,
            )}
          </select>
        </label>
        ${anchor?.kind === "clock"
          ? html`
              <label class="field">
                <span>At</span>
                <input
                  type="time"
                  step="1"
                  .value=${anchor.at}
                  ?disabled=${!this._canWrite}
                  @change=${(event: Event) =>
                    write({ kind: "clock", at: clockValue(inputValue(event)) })}
                />
              </label>
              <p class="muted">
                Wall time, not an instant (D40). It means 18:00 on whatever day
                the recurrence picks, including the day the clocks change.
              </p>
            `
          : nothing}
        ${anchor?.kind === "entity_time"
          ? html`
              <label class="field">
                <span>Entity</span>
                <input
                  type="text"
                  list=${ENTITY_LIST}
                  .value=${anchor.entity_id}
                  ?disabled=${!this._canWrite}
                  @change=${(event: Event) =>
                    write({ ...anchor, entity_id: inputValue(event).trim() })}
                />
              </label>
            `
          : nothing}
        ${anchor && anchor.kind !== "clock"
          ? html`
              <label class="field">
                <span>Offset</span>
                <input
                  type="number"
                  step="any"
                  .value=${String(anchor.offset / 60)}
                  ?disabled=${!this._canWrite}
                  @change=${(event: Event) =>
                    write({ ...anchor, offset: secondsFrom(inputValue(event)) })}
                />
                <span class="muted">minutes</span>
              </label>
              <p class="muted">
                Signed, and arithmetic on the event itself (D7): −45 is
                forty-five minutes before it. The day the rule belongs to is the
                day of the event, not of the offset result (D122).
              </p>
            `
          : nothing}
        ${anchor?.kind === "resolver" && this._hasEdges(anchor)
          ? html`
              <label class="field">
                <span>Which edge</span>
                <select
                  ?disabled=${!this._canWrite}
                  @change=${(event: Event) =>
                    write({
                      ...anchor,
                      edge: inputValue(event) === "end" ? "end" : "start",
                    })}
                >
                  <option value="start" ?selected=${anchor.edge === "start"}>
                    When it begins
                  </option>
                  <option value="end" ?selected=${anchor.edge === "end"}>
                    When it ends
                  </option>
                </select>
              </label>
            `
          : nothing}
      </div>
    `;
  }

  /**
   * D124's edge choice, offered exactly when the offering declares it.
   *
   * `roles.includes("day_set")` is the test and not a derived flag beside it
   * (D133): §5.1 defines the day-set role as a predicate over a span's
   * *interior*, so an offering that has one has two distinct edges and an
   * offering that does not — a sunset, a zman, a candle lighting — is a single
   * instant whose "end" would be the same moment under another name.
   */
  private _hasEdges(anchor: StoredAnchor & { kind: "resolver" }): boolean {
    const offering = this._offering(anchor.domain, anchor.key);
    // An unknown offering keeps whatever edge is stored and offers the choice,
    // because the alternative is hiding a stored value: D17 says an anchor whose
    // resolver is absent renders as unresolved rather than as edited.
    return offering ? offering.roles.includes("day_set") : anchor.edge === "end";
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
                A duration is not an anchor (D73), so this end gets no rail of its
                own — it is drawn as a second stage on the start's rail.
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

  /** What picking a kind in the anchor list produces, carrying across what it can. */
  private _anchorFor(
    value: string,
    current: StoredAnchor | undefined,
  ): StoredAnchor {
    if (value === "clock") {
      return {
        kind: "clock",
        at: current?.kind === "clock" ? current.at : DEFAULT_CLOCK_TIME,
      };
    }
    // The offset survives a kind change and is dropped by a clock time, which is
    // not an inconsistency: the offset is arithmetic on an event, and a clock
    // time has no event to be early or late for — the time *is* the time.
    const offset = current && current.kind !== "clock" ? current.offset : 0;
    if (value === "entity_time") {
      return {
        kind: "entity_time",
        entity_id:
          current?.kind === "entity_time"
            ? current.entity_id
            : (this._timeEntities[0] ?? ""),
        offset,
      };
    }
    const parts = value.split(":");
    return {
      kind: "resolver",
      domain: parts[1] ?? "",
      key: parts[2] ?? "",
      offset,
      edge: current?.kind === "resolver" ? current.edge : "start",
    };
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

  // --- what the editor does not edit ---------------------------------------

  /**
   * D149's limit, stated on screen rather than implied by an absence.
   *
   * Counts, not controls. The point is that the user can see these exist and are
   * being preserved — a schedule with eleven actions that showed nothing about
   * them would read as a schedule with none, and the Save button would look like
   * it was about to write that.
   */
  private _untouched() {
    const rules = this._rules;
    const count = (pick: (rule: WritableRule) => number): number =>
      rules.reduce((total, rule) => total + pick(rule), 0);
    const actions = count((rule) =>
      rule.kind === "at"
        ? (rule.actions?.length ?? 0)
        : (rule.enter_actions?.length ?? 0) + (rule.exit_actions?.length ?? 0),
    );
    const conditions = count((rule) => rule.conditions?.length ?? 0);
    const states = count((rule) =>
      rule.kind === "during" && rule.state ? 1 : 0,
    );
    if (actions + conditions + states === 0) {
      return nothing;
    }
    const parts = [
      actions === 1 ? "1 action" : actions > 0 ? `${actions} actions` : null,
      conditions === 1
        ? "1 condition"
        : conditions > 0
          ? `${conditions} conditions`
          : null,
      states === 1
        ? "1 desired state"
        : states > 0
          ? `${states} desired states`
          : null,
    ].filter((part): part is string => part !== null);
    return html`
      <section>
        <h3>Carried through unchanged</h3>
        <p class="muted">
          ${parts.join(", ")}. This editor does not change what a rule
          <em>does</em> yet, only when it happens. Saving leaves all of it exactly
          as it is.
        </p>
      </section>
    `;
  }

  /** D78's footprint, as text. */
  private _footprint() {
    const footprint = footprintOf(this._draft);
    const groups: [string, string[]][] = [
      ["Writes to", footprint.entities],
      ["Reads a time from", footprint.reads],
      ["Runs", footprint.scripts],
      ["Calls", footprint.services],
    ];
    const shown = groups.filter(([, items]) => items.length > 0);
    if (shown.length === 0 && footprint.unexpanded === 0) {
      return nothing;
    }
    return html`
      <section>
        <h3>What this touches</h3>
        ${shown.map(
          ([label, items]) => html`
            <p>
              <span class="muted">${label}</span>
              ${items.map(
                (item) => html`<span class="pill mono">${item}</span>`,
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
        <p class="muted">
          Names, not links. Opening one needs core's more-info event, whose exact
          spelling this environment cannot certify (Appendix B) and whose
          misspelling fails silently — nothing opens and nothing is logged. It
          arrives with step 9f, verified.
        </p>
      </section>
    `;
  }

  private _footer(found: string[]) {
    const dirty = isDirty(this._draft, this._original);
    const blocked = !this._canWrite || this._saving || found.length > 0 || !dirty;
    return html`
      <footer>
        <button
          class="primary tappable"
          ?disabled=${blocked}
          @click=${() => void this._save()}
        >
          ${this._saving ? "Saving…" : "Save"}
        </button>
        <button class="ghost tappable" @click=${() => this._close(false)}>
          Cancel
        </button>
        ${this._original
          ? html`<button
              class="danger tappable"
              ?disabled=${!this._canWrite || this._saving}
              @click=${() => void this._delete()}
            >
              Delete
            </button>`
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

      h3 {
        font-size: 0.8125rem;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        margin: 0 0 var(--almanac-gap-sm);
        color: var(--secondary-text-color);
      }

      section {
        display: flex;
        flex-direction: column;
        gap: var(--almanac-gap-sm);
      }

      /* The selected rule's panel is the one surface that is not the page, so it
         gets an edge. Everything else sits flat on the card. */
      .panel {
        border: 1px solid var(--almanac-rail);
        border-radius: 12px;
        padding: var(--almanac-gap-md);
      }

      .row {
        display: flex;
        flex-wrap: wrap;
        gap: var(--almanac-gap-xs);
      }

      .field {
        display: flex;
        align-items: center;
        flex-wrap: wrap;
        gap: var(--almanac-gap-sm);
      }

      /* Visible, always, and never a placeholder standing in for one: a label
         that vanishes once the field has content takes the field's meaning with
         it, and these fields hold slugs and signed numbers. */
      .field > span:first-child {
        flex: 0 0 7rem;
        font-size: 0.875rem;
        color: var(--secondary-text-color);
      }

      .field input,
      .field select {
        flex: 1 1 12rem;
        min-width: 0;
        min-height: 40px;
        box-sizing: border-box;
        padding: 0 var(--almanac-gap-sm);
        font: inherit;
        color: var(--primary-text-color);
        background: var(--secondary-background-color);
        border: 1px solid var(--almanac-rail);
        border-radius: 8px;
      }

      .field input:disabled,
      .field select:disabled {
        opacity: 0.6;
      }

      .field input[type="number"] {
        flex: 0 0 6rem;
      }

      .check {
        display: flex;
        align-items: center;
        gap: var(--almanac-gap-sm);
        font-size: 0.875rem;
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

      button {
        font: inherit;
        color: inherit;
        border-radius: 10px;
        border: 1px solid var(--almanac-rail);
        background: none;
        min-height: 40px;
        padding: 0 var(--almanac-gap-sm);
      }

      button:disabled {
        opacity: 0.5;
      }

      .chip {
        padding: 0 var(--almanac-gap-sm);
        min-height: 36px;
      }

      .chip.selected {
        border-color: var(--almanac-known);
        color: var(--almanac-known);
      }

      .primary {
        border-color: var(--almanac-known);
        color: var(--almanac-known);
      }

      .danger {
        border-color: var(--almanac-failed);
        color: var(--almanac-failed);
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

      p {
        margin: 0;
        font-size: 0.875rem;
      }

      almanac-track {
        overflow-x: auto;
      }

      @media (max-width: 600px) {
        .field > span:first-child {
          flex: 1 1 100%;
        }
      }
    `,
  ];
}

/** The typed value of whatever fired an `input` or `change`. */
const inputValue = (event: Event): string =>
  (event.target as HTMLInputElement | HTMLSelectElement).value;

/**
 * `<input type="time">` gives `HH:MM` with no seconds unless the user types
 * them, and D40's stored form is `HH:MM:SS`. The schema's `_clock_time` accepts
 * both; normalising here means the draft's value and the stored value are the
 * same string, so D140's diff does not see a change that is only a spelling.
 */
const clockValue = (typed: string): string =>
  typed.length === 5 ? `${typed}:00` : typed;

/**
 * Minutes, as typed, to stored seconds.
 *
 * Minutes because that is the unit every offset in this domain is spoken in —
 * "forty-five minutes before candle lighting" — and `step="any"` because the
 * alternative, `step="1"`, silently rounds a stored thirty-second offset away
 * the first time the field is touched. `Math.round` on the way back keeps the
 * stored value an integer number of seconds, which is what the schema takes.
 */
const secondsFrom = (typed: string): number => {
  const minutes = Number(typed);
  return Number.isFinite(minutes) ? Math.round(minutes * 60) : 0;
};

declare global {
  interface HTMLElementTagNameMap {
    "almanac-editor": AlmanacEditor;
  }
}
