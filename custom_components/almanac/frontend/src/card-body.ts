// The card, behind D70's dynamic import.
//
// Everything this module pulls in — Lit, the styles, the derivations — is in the
// lazy chunk, which is the whole point of `card.ts` being what it is. Nothing
// here may be imported from `card.ts`.
//
// What a row shows is D79: the generated summary is the payload, and a
// description overrides it only where someone wrote one. The summary this
// sub-step can generate is the one the plan supports — when the schedule next
// runs — because the anchor-relative phrasing (`candle lighting −45m → havdalah
// +30m`) needs the stored rule bodies and those arrive with the editor. The row
// is laid out for the longer string now so that it does not have to be relaid
// out later.
//
// D75 holds throughout: a row is a schedule. Stages are a disclosure inside one,
// never a sibling row.

import { LitElement, html, nothing, css } from "lit";
import { customElement, property, state } from "lit/decorators.js";

import { dayOffset, fetchTimeline, isNotLoaded } from "./api";
import {
  armedStages,
  coverageReason,
  laneOrder,
  nextOccurrence,
} from "./derive";
import { dayAndTime, relative } from "./format";
import type { HomeAssistant, LovelaceCardConfig } from "./ha";
import { almanacText, almanacTokens } from "./styles";
import type { WireScheduleTimeline } from "./wire";

/** How far ahead a card looks. One week answers "what is next" for anything weekly. */
const LOOKAHEAD_DAYS = 8;

export interface AlmanacCardConfig extends LovelaceCardConfig {
  title?: string;
  /** Omitted means every schedule, which is what the picker's preview shows. */
  schedule_ids?: string[];
}

@customElement("almanac-card-body")
export class AlmanacCardBody extends LitElement {
  @property({ attribute: false }) public hass?: HomeAssistant | undefined;

  @state() private _config?: AlmanacCardConfig | undefined;
  @state() private _lanes?: WireScheduleTimeline[] | undefined;
  @state() private _error?: string | undefined;
  @state() private _notLoaded = false;

  /** The instant the card's relative labels are read against. */
  @state() private _at = new Date();

  private _timer?: number | undefined;
  private _inFlight = false;

  public setConfig(config: AlmanacCardConfig): void {
    if (config.schedule_ids && !Array.isArray(config.schedule_ids)) {
      throw new Error("almanac: schedule_ids must be a list");
    }
    this._config = config;
    this._lanes = undefined;
    if (this.hass) {
      void this._refresh();
    }
  }

  public getCardSize(): number {
    return 1 + Math.max(1, this._lanes?.length ?? 1) * 2;
  }

  public override connectedCallback(): void {
    super.connectedCallback();
    // One minute, because every label this card draws is to the minute. D64's
    // reasoning applies in miniature: the clock is read in exactly one place.
    this._timer = window.setInterval(() => {
      this._at = new Date();
    }, 60_000);
    if (this.hass && this._config) {
      void this._refresh();
    }
  }

  public override disconnectedCallback(): void {
    super.disconnectedCallback();
    if (this._timer !== undefined) {
      window.clearInterval(this._timer);
      this._timer = undefined;
    }
  }

  protected override willUpdate(changed: Map<string, unknown>): void {
    if (changed.has("hass") && this._config && this._lanes === undefined) {
      void this._refresh();
    }
  }

  private async _refresh(): Promise<void> {
    const hass = this.hass;
    const config = this._config;
    if (!hass || !config || this._inFlight) {
      return;
    }
    this._inFlight = true;
    const from = new Date();
    try {
      const timeline = await fetchTimeline(hass, {
        start: from,
        end: dayOffset(from, LOOKAHEAD_DAYS),
        at: from,
        ...(config.schedule_ids ? { scheduleIds: config.schedule_ids } : {}),
      });
      this._lanes = [...timeline.schedules].sort(laneOrder);
      this._error = undefined;
      this._notLoaded = false;
    } catch (error) {
      if (isNotLoaded(error)) {
        // Reloading the config entry is not a failure, and a card that shouts
        // about it during every reload teaches the user to ignore it.
        this._notLoaded = true;
        this._error = undefined;
      } else {
        this._error = error instanceof Error ? error.message : String(error);
      }
    } finally {
      this._inFlight = false;
    }
  }

  protected override render() {
    if (!this.hass || !this._config) {
      return nothing;
    }
    return html`
      <ha-card .header=${this._config.title ?? "almanac"}>
        <div class="body">${this._content()}</div>
      </ha-card>
    `;
  }

  private _content() {
    if (this._notLoaded) {
      return html`<p class="muted">almanac is reloading.</p>`;
    }
    if (this._error) {
      return html`<p class="problem">${this._error}</p>`;
    }
    if (this._lanes === undefined) {
      return html`<p class="muted">Reading the schedule…</p>`;
    }
    if (this._lanes.length === 0) {
      return html`<p class="muted">No schedules yet.</p>`;
    }
    return html`<ul class="lanes">
      ${this._lanes.map((lane) => this._row(lane))}
    </ul>`;
  }

  private _row(lane: WireScheduleTimeline) {
    const hass = this.hass!;
    const next = nextOccurrence(lane.plan, this._at);
    const stages = armedStages(lane.plan);
    const coverage = coverageReason(lane.coverage, lane.plan);
    const entity = hass.states[lane.entity_id];

    return html`
      <li class="lane">
        <div class="head">
          <span class="name">${lane.name ?? lane.schedule_id}</span>
          ${
            // D77 — a schedule with any stage disabled never renders as plain
            // *on*. The fraction replaces the state word rather than sitting
            // next to it, because two badges invite the reader to believe the
            // reassuring one.
            stages.partial
              ? html`<span class="pill disarmed"
                  >${stages.armed} of ${stages.total} stages armed</span
                >`
              : html`<span class="pill">${entity?.state ?? "unknown"}</span>`
          }
        </div>
        <div class="payload">
          ${next
            ? html`<span class="mono">${dayAndTime(hass, next.start!)}</span>
                <span class="muted"
                  >· ${relative(next.start!, this._at)}</span
                >`
            : html`<span class="muted">Nothing in the next week.</span>`}
        </div>
        ${coverage
          ? html`<div class="problem">${coverage}</div>`
          : nothing}
      </li>
    `;
  }

  static override styles = [
    almanacTokens,
    almanacText,
    css`
      .body {
        padding: 0 var(--almanac-gap-md) var(--almanac-gap-md);
      }

      .lanes {
        list-style: none;
        margin: 0;
        padding: 0;
      }

      .lane + .lane {
        border-top: 1px solid var(--almanac-rail);
        margin-top: var(--almanac-gap-sm);
        padding-top: var(--almanac-gap-sm);
      }

      .head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: var(--almanac-gap-sm);
      }

      .name {
        font-weight: 500;
      }

      /* The row's payload slot. Finding 13 measured the prototype's summary
         truncating at 340px and wanting about 460; D79 then made the generated
         summary the payload, which is longer still. So it wraps rather than
         clipping, and the slot is allowed two lines. */
      .payload {
        margin-top: var(--almanac-gap-xs);
        display: flex;
        flex-wrap: wrap;
        gap: var(--almanac-gap-xs);
        font-size: 0.9375rem;
      }

      .problem {
        margin-top: var(--almanac-gap-xs);
        font-size: 0.8125rem;
      }

      p {
        margin: var(--almanac-gap-sm) 0 0;
      }
    `,
  ];
}

declare global {
  interface HTMLElementTagNameMap {
    "almanac-card-body": AlmanacCardBody;
  }
}
