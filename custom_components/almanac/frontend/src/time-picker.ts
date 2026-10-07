// The one control that says when something happens (D168).
//
// An anchor used to be a `<select>` of every event there is, a time input, a
// number of minutes and two paragraphs of reasoning. What a person is deciding is
// one thing -- "when" -- in three layers: which kind of time (a tile), which
// event or which digits (one value control), and how far from it (a signed
// stepper). The summary line under them says it back in words and, for an event,
// asks the engine when it next happens, so a wrong choice shows before Save.
//
// Storage is unchanged. The arithmetic -- offset signs, digit wrapping, chip
// order, the stale-answer guard -- is in `timepick.ts`; this file is markup and
// the one network call.
//
// D64: the preview is a function of (anchor, `at`), and `at` is handed in. This
// file never samples a clock, and a source test says so.

import { LitElement, css, html, nothing } from "lit";
import { customElement, property, state } from "lit/decorators.js";

import { fetchAnchorPreview } from "./api";
import "./entity-field";
import type { HomeAssistant } from "./ha";
import "./segmented";
import type { StoredAnchor, StoredResolverAnchor } from "./stored";
import {
  almanacForm,
  almanacHitTarget,
  almanacText,
  almanacTokens,
} from "./styles";
import {
  chooseDirection,
  clockChips,
  clockParts,
  latest,
  offsetParts,
  searchOfferings,
  setClockUnit,
  splitOfferings,
  stepClock,
  stepOffset,
  summarise,
  tileList,
  tileOf,
} from "./timepick";
import type { ClockUnit, Direction } from "./timepick";
import type { WireOffering } from "./wire";

/** How long a press must last before the stepper starts repeating. */
const LONG_PRESS_MS = 400;
const REPEAT_MS = 120;

/** Quiet time after the last change before the engine is asked. */
const PREVIEW_DEBOUNCE_MS = 300;

const DEFAULT_CLOCK = "18:00:00";

const DIRECTIONS = [
  { id: "before", label: "Before" },
  { id: "at", label: "At" },
  { id: "after", label: "After" },
];

const EDGES = [
  { id: "start", label: "Begins" },
  { id: "end", label: "Ends" },
];

const two = (value: number): string => String(value).padStart(2, "0");

const offsetOf = (anchor: StoredAnchor): number =>
  anchor.kind === "clock" ? 0 : anchor.offset;

@customElement("almanac-time-picker")
export class AlmanacTimePicker extends LitElement {
  @property({ attribute: false }) public hass?: HomeAssistant | undefined;

  /** D167. Passed on to the entity field. */
  @property({ type: Boolean }) public haReady = false;

  /** The visible caption: "Time", "Starts", "Until". */
  @property() public label = "";

  @property({ attribute: false }) public anchor!: StoredAnchor;

  @property({ attribute: false }) public offerings: WireOffering[] = [];

  /** Clock times already in the schedule, offered first as chips. */
  @property({ attribute: false }) public usedTimes: string[] = [];

  /** ISO instant for the preview. Handed in by the host: D64 and D171. */
  @property() public at = "";

  /** The datalist id the entity field's fallback input points at. */
  @property() public entityList = "";

  @property({ type: Boolean }) public disabled = false;

  /** Called with a whole new anchor. Never called with the value already held. */
  @property({ attribute: false }) public onChange: (anchor: StoredAnchor) => void =
    () => {};

  @state() private _more = false;
  @state() private _query = "";
  @state() private _next: string | undefined;
  @state() private _unresolved: string | undefined;

  private readonly _guard = latest();
  private _repeat: number | undefined;
  private _previewTimer: number | undefined;

  // --- writing -----------------------------------------------------------

  /** One door for every write, so an unchanged value is never one. */
  private _write(next: StoredAnchor): void {
    if (JSON.stringify(next) !== JSON.stringify(this.anchor)) {
      this.onChange(next);
    }
  }

  // --- tiles ---------------------------------------------------------------

  /**
   * A fresh anchor of the kind chosen, never a mutated one: a `key` carried from
   * `sun` into `hdate` would name an offering that does not exist. A clock has no
   * offset (a clock time is the time; there is no event to be early for).
   */
  private _chooseTile(id: string): void {
    if (id === tileOf(this.anchor)) {
      return;
    }
    this._more = false;
    this._query = "";
    if (id === "clock") {
      this._write({ kind: "clock", at: DEFAULT_CLOCK });
    } else if (id === "entity_time") {
      this._write({ kind: "entity_time", entity_id: "", offset: 0 });
    } else {
      const domain = id.slice("domain:".length);
      const first = splitOfferings(this.offerings, domain).common[0];
      this._write({
        kind: "resolver",
        domain,
        key: first?.key ?? "",
        offset: 0,
        edge: "start",
      });
    }
  }

  // --- the clock -----------------------------------------------------------

  private _clockAt(): string {
    return this.anchor.kind === "clock" ? this.anchor.at : DEFAULT_CLOCK;
  }

  private _setClock(at: string): void {
    this._write({ kind: "clock", at });
  }

  private _digitKey(unit: ClockUnit, event: KeyboardEvent): void {
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") {
      return;
    }
    event.preventDefault();
    this._setClock(stepClock(this._clockAt(), unit, event.key === "ArrowUp" ? 1 : -1));
  }

  private _digitWheel(unit: ClockUnit, event: WheelEvent): void {
    event.preventDefault();
    if (event.deltaY === 0) {
      return;
    }
    this._setClock(stepClock(this._clockAt(), unit, event.deltaY < 0 ? 1 : -1));
  }

  private _digitChange(unit: ClockUnit, event: Event): void {
    const input = event.target as HTMLInputElement;
    const at = this._clockAt();
    const next = setClockUnit(at, unit, input.value);
    // Lit will not re-write a bound value that did not change, so text the
    // user typed that was refused (or clamped) is put back by hand.
    input.value = two(clockParts(next)[unit]);
    this._setClock(next);
  }

  private _clock() {
    const at = this._clockAt();
    const parts = clockParts(at);
    const digit = (unit: ClockUnit, label: string) => html`<input
      class="digit mono"
      inputmode="numeric"
      maxlength="2"
      aria-label=${label}
      .value=${two(parts[unit])}
      ?disabled=${this.disabled}
      @keydown=${(event: KeyboardEvent) => this._digitKey(unit, event)}
      @wheel=${(event: WheelEvent) => this._digitWheel(unit, event)}
      @change=${(event: Event) => this._digitChange(unit, event)}
    />`;
    return html`
      <div class="row digits">
        ${digit("hour", "Hour")}<span class="colon mono">:</span>${digit(
          "minute",
          "Minute",
        )}
      </div>
      <div class="row">
        ${clockChips(this.usedTimes).map(
          (chip) => html`<ha-button
            size="small"
            appearance=${chip === at ? "filled" : "outlined"}
            ?disabled=${this.disabled}
            @click=${() => this._setClock(chip)}
            >${chip.slice(0, 5)}</ha-button
          >`,
        )}
      </div>
    `;
  }

  // --- resolver events -------------------------------------------------------

  private _offeringOf(anchor: StoredResolverAnchor): WireOffering | undefined {
    return this.offerings.find(
      (offering) =>
        offering.domain === anchor.domain && offering.key === anchor.key,
    );
  }

  /** An unknown offering keeps its stored edge and offers the choice (D17). */
  private _hasEdges(anchor: StoredResolverAnchor): boolean {
    const offering = this._offeringOf(anchor);
    return offering ? offering.roles.includes("day_set") : anchor.edge === "end";
  }

  private _pickOffering(anchor: StoredResolverAnchor, offering: WireOffering): void {
    this._more = false;
    this._query = "";
    this._write({
      ...anchor,
      key: offering.key,
      // A single instant has no "end"; carrying one across would be a wrong
      // edge on an event that has only one.
      edge: offering.roles.includes("day_set") ? anchor.edge : "start",
    });
  }

  private _events(anchor: StoredResolverAnchor) {
    const { common, more } = splitOfferings(
      this.offerings,
      anchor.domain,
      anchor.key,
    );
    const found = searchOfferings(more, this._query);
    return html`
      <div class="row">
        ${common.map(
          (offering) => html`<ha-button
            size="small"
            appearance=${offering.key === anchor.key ? "filled" : "outlined"}
            ?disabled=${this.disabled}
            @click=${() => this._pickOffering(anchor, offering)}
            >${offering.display_name}</ha-button
          >`,
        )}
        ${more.length > 0
          ? html`<ha-button
              size="small"
              appearance="plain"
              aria-expanded=${this._more ? "true" : "false"}
              ?disabled=${this.disabled}
              @click=${() => {
                this._more = !this._more;
              }}
              >More…</ha-button
            >`
          : nothing}
      </div>
      ${this._more
        ? html`
            <input
              type="search"
              class="search"
              placeholder="Search events"
              aria-label="Search events"
              .value=${this._query}
              @input=${(event: Event) => {
                this._query = (event.target as HTMLInputElement).value;
              }}
            />
            <div class="more">
              ${found.length === 0
                ? html`<span class="muted">Nothing matches.</span>`
                : found.map(
                    (offering) => html`<ha-button
                      size="small"
                      appearance="plain"
                      ?disabled=${this.disabled}
                      @mousedown=${(event: Event) => event.preventDefault()}
                      @click=${() => this._pickOffering(anchor, offering)}
                      >${offering.display_name}</ha-button
                    >`,
                  )}
            </div>
          `
        : nothing}
      ${this._hasEdges(anchor)
        ? html`<almanac-segmented
            label="Which edge"
            .options=${EDGES}
            selected=${anchor.edge}
            ?disabled=${this.disabled}
            .onSelect=${(id: string) =>
              this._write({ ...anchor, edge: id === "end" ? "end" : "start" })}
          ></almanac-segmented>`
        : nothing}
    `;
  }

  // --- the entity ---------------------------------------------------------------

  private _entity(anchor: StoredAnchor & { kind: "entity_time" }) {
    return html`<almanac-entity-field
      kind="timestamp"
      label="Entity"
      fallbackList=${this.entityList}
      .hass=${this.hass}
      .haReady=${this.haReady}
      .required=${true}
      .value=${anchor.entity_id}
      ?disabled=${this.disabled}
      .onPick=${(value: unknown) =>
        this._write({
          ...anchor,
          entity_id: typeof value === "string" ? value : "",
        })}
    ></almanac-entity-field>`;
  }

  // --- the offset ----------------------------------------------------------------

  private _startStepping(steps: number): void {
    this._stopStepping();
    this._step(steps);
    this._repeat = window.setTimeout(() => {
      this._repeat = window.setInterval(() => this._step(steps), REPEAT_MS);
    }, LONG_PRESS_MS);
  }

  private _stopStepping = (): void => {
    window.clearTimeout(this._repeat);
    window.clearInterval(this._repeat);
    this._repeat = undefined;
  };

  private _step(steps: number): void {
    if (this.anchor.kind === "clock") {
      return;
    }
    const next = stepOffset(this.anchor.offset, steps);
    if (next === this.anchor.offset) {
      this._stopStepping(); // at the stop: nothing left to repeat
      return;
    }
    this._write({ ...this.anchor, offset: next });
  }

  /** A keyboard activation has `detail` 0; a pointer one was handled on press. */
  private _stepButton(steps: number, event: MouseEvent): void {
    if (event.detail === 0) {
      this._step(steps);
    }
  }

  private _offset(anchor: Exclude<StoredAnchor, { kind: "clock" }>) {
    const { direction, minutes } = offsetParts(anchor.offset);
    return html`
      <div class="row offset">
        <almanac-segmented
          label="Before or after"
          .options=${DIRECTIONS}
          selected=${direction}
          ?disabled=${this.disabled}
          .onSelect=${(id: string) =>
            this._write({
              ...anchor,
              offset: chooseDirection(anchor.offset, id as Direction),
            })}
        ></almanac-segmented>
        ${direction === "at"
          ? nothing
          : html`
              <ha-button
                class="step"
                size="small"
                appearance="outlined"
                aria-label="Closer to the event"
                ?disabled=${this.disabled}
                @pointerdown=${() => this._startStepping(-1)}
                @pointerup=${this._stopStepping}
                @pointerleave=${this._stopStepping}
                @pointercancel=${this._stopStepping}
                @click=${(event: MouseEvent) => this._stepButton(-1, event)}
                >−</ha-button
              >
              <span class="mono amount">${minutes} min</span>
              <ha-button
                class="step"
                size="small"
                appearance="outlined"
                aria-label="Further from the event"
                ?disabled=${this.disabled}
                @pointerdown=${() => this._startStepping(1)}
                @pointerup=${this._stopStepping}
                @pointerleave=${this._stopStepping}
                @pointercancel=${this._stopStepping}
                @click=${(event: MouseEvent) => this._stepButton(1, event)}
                >＋</ha-button
              >
            `}
      </div>
    `;
  }

  // --- the summary and the preview -------------------------------------------------

  private _complete(): boolean {
    switch (this.anchor.kind) {
      case "resolver":
        return this.anchor.key !== "";
      case "entity_time":
        return this.anchor.entity_id !== "";
      default:
        return false; // a clock time needs no question put to the engine
    }
  }

  protected override updated(changed: Map<string, unknown>): void {
    if (
      changed.has("anchor") ||
      changed.has("at") ||
      (changed.has("hass") && changed.get("hass") === undefined)
    ) {
      this._schedulePreview();
    }
  }

  /**
   * The old answer belongs to the old anchor, so it goes at once; the question
   * waits for a quiet moment, so a held stepper asks once and not every repeat.
   */
  private _schedulePreview(): void {
    window.clearTimeout(this._previewTimer);
    this._guard.next(); // retires any answer still in flight
    this._next = undefined;
    this._unresolved = undefined;
    if (!this.hass || this.at === "" || !this._complete()) {
      return;
    }
    this._previewTimer = window.setTimeout(
      () => void this._refresh(),
      PREVIEW_DEBOUNCE_MS,
    );
  }

  private async _refresh(): Promise<void> {
    const token = this._guard.next();
    if (!this.hass || this.at === "" || !this._complete()) {
      return;
    }
    try {
      const result = await fetchAnchorPreview(this.hass, this.anchor, this.at, 1);
      if (!this._guard.isCurrent(token)) {
        return; // a slower answer for an earlier anchor must not replace this one
      }
      this._next = result.instants[0];
      this._unresolved =
        result.instants.length === 0 ? (result.unresolved ?? undefined) : undefined;
    } catch {
      if (this._guard.isCurrent(token)) {
        this._next = undefined;
        this._unresolved = undefined;
      }
    }
  }

  private _subject(): string {
    const anchor = this.anchor;
    if (anchor.kind === "entity_time") {
      return anchor.entity_id === "" ? "an entity's time" : anchor.entity_id;
    }
    if (anchor.kind === "resolver") {
      const offering = this._offeringOf(anchor);
      const name = offering?.display_name ?? `${anchor.domain}.${anchor.key}`;
      return this._hasEdges(anchor)
        ? `${name} ${anchor.edge === "end" ? "ends" : "begins"}`
        : name;
    }
    return "";
  }

  private _summary() {
    const anchor = this.anchor;
    if (anchor.kind === "clock") {
      return html`<p class="summary">At ${anchor.at.slice(0, 5)}</p>`;
    }
    if (!this._complete()) {
      return nothing;
    }
    const said = summarise(this._subject(), offsetOf(anchor));
    const text = said.charAt(0).toUpperCase() + said.slice(1);
    const next =
      this._next === undefined
        ? nothing
        : html` — next:
            ${new Date(this._next).toLocaleString(undefined, {
              weekday: "short",
              day: "numeric",
              month: "short",
              hour: "2-digit",
              minute: "2-digit",
            })}`;
    return html`<p class="summary">
      ${text}${next}${this._unresolved === undefined
        ? nothing
        : html` — <span class="muted">${this._unresolved}</span>`}
    </p>`;
  }

  // --- the whole -----------------------------------------------------------------

  override disconnectedCallback(): void {
    this._stopStepping();
    window.clearTimeout(this._previewTimer);
    super.disconnectedCallback();
  }

  protected override render() {
    const anchor = this.anchor;
    if (!anchor) {
      return nothing;
    }
    return html`
      ${this.label === ""
        ? nothing
        : html`<span class="caption">${this.label}</span>`}
      <almanac-segmented
        label="Kind of time"
        .options=${tileList(this.offerings)}
        selected=${tileOf(anchor)}
        ?disabled=${this.disabled}
        .onSelect=${(id: string) => this._chooseTile(id)}
      ></almanac-segmented>
      ${anchor.kind === "clock" ? this._clock() : nothing}
      ${anchor.kind === "resolver" ? this._events(anchor) : nothing}
      ${anchor.kind === "entity_time" ? this._entity(anchor) : nothing}
      ${anchor.kind === "clock" ? nothing : this._offset(anchor)}
      ${this._summary()}
    `;
  }

  static override styles = [
    almanacTokens,
    almanacText,
    almanacForm,
    almanacHitTarget,
    css`
      :host {
        display: flex;
        flex-direction: column;
        gap: var(--almanac-gap-sm);
      }
      .digits {
        align-items: center;
      }
      .digit {
        font-size: 2.5rem;
        width: 4ch;
        min-height: 44px;
        text-align: center;
      }
      .colon {
        font-size: 2.5rem;
      }
      ha-button {
        min-height: 44px;
        min-width: 44px;
      }
      .offset {
        align-items: center;
      }
      .amount {
        min-width: 5ch;
        text-align: center;
      }
      .search {
        min-height: 44px;
      }
      .more {
        display: flex;
        flex-wrap: wrap;
        gap: var(--almanac-gap-xs);
        max-height: 12rem;
        overflow-y: auto;
      }
      .caption {
        font-weight: 500;
      }
      .summary {
        margin: 0;
      }
    `,
  ];
}

declare global {
  interface HTMLElementTagNameMap {
    "almanac-time-picker": AlmanacTimePicker;
  }
}
