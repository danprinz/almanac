// The card, behind D70's dynamic import.
//
// Everything this module pulls in — Lit, the styles, the derivations — is in the
// lazy chunk, which is the whole point of `card.ts` being what it is. Nothing
// here may be imported from `card.ts`.
//
// What a row shows is D79: the generated summary is the payload — `candle
// lighting −45m → havdalah +30m`, built from the stored rules — and the
// free-text description overrides it only where someone wrote one. That way
// round, because a description is a note somebody left and a summary is a fact
// about the schedule, and the note is the thing more likely to have gone stale.
//
// D74's smallest two sizes both appear here: the micro-track beside the name,
// and no track at all when the stored schedule is not to hand.
//
// D75 holds throughout: a row is a schedule. Stages are a disclosure inside one,
// never a sibling row.

import { LitElement, html, nothing, css } from "lit";
import { customElement, property, state } from "lit/decorators.js";

import { dayOffset, fetchSchedules, fetchTimeline, isNotLoaded } from "./api";
import {
  armedStages,
  coverageReason,
  laneOrder,
  nextOccurrence,
} from "./derive";
import { dayAndTime, relative } from "./format";
import type { HomeAssistant, LovelaceCardConfig } from "./ha";
import { armedRules, buildTrack } from "./rails";
import type { StoredSchedule } from "./stored";
import { almanacText, almanacTokens } from "./styles";
import { summaryLine } from "./track";
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
  @state() private _stored?: Map<string, StoredSchedule> | undefined;
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
      // `fetchSchedules` has no filter, so a card configured for one schedule
      // still reads the collection. That is the same read the schedule list
      // itself does, it is served from memory, and filtering it on the server
      // would mean a second command that exists only for this caller.
      const [timeline, stored] = await Promise.all([
        fetchTimeline(hass, {
          start: from,
          end: dayOffset(from, LOOKAHEAD_DAYS),
          at: from,
          ...(config.schedule_ids ? { scheduleIds: config.schedule_ids } : {}),
        }),
        fetchSchedules(hass),
      ]);
      this._lanes = [...timeline.schedules].sort(laneOrder);
      this._stored = new Map(
        stored.map((schedule) => [schedule.id, schedule] as const),
      );
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

  /** A friendly name for an entity anchor's rail, when the entity exists. */
  private _entityName = (entityId: string): string | undefined => {
    const name = this.hass?.states[entityId]?.attributes["friendly_name"];
    return typeof name === "string" ? name : undefined;
  };

  private _row(lane: WireScheduleTimeline) {
    const hass = this.hass!;
    const next = nextOccurrence(lane.plan, this._at);
    const stored = this._stored?.get(lane.schedule_id);
    const stages = stored ? armedRules(stored) : armedStages(lane.plan);
    const track = stored
      ? buildTrack(stored, lane.plan, this._at, this._entityName)
      : null;
    const coverage = coverageReason(lane.coverage, lane.plan);
    const entity = hass.states[lane.entity_id];
    // D79, in order of authority: what someone wrote, else what the schedule
    // demonstrably is, else nothing — and never a placeholder, because an empty
    // payload is a row that has not loaded and a placeholder is a row that has.
    const summary =
      stored && stored.description !== ""
        ? stored.description
        : track
          ? summaryLine(track)
          : "";

    return html`
      <li class="lane">
        <div class="head">
          <span class="name">${lane.name ?? lane.schedule_id}</span>
          ${track && track.rails.length > 0
            ? html`<almanac-track
                class="micro"
                size="micro"
                .track=${track}
              ></almanac-track>`
            : nothing}
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
        ${summary === ""
          ? nothing
          : html`<div class="payload">${summary}</div>`}
        <div class="when">
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
        flex: 1 1 auto;
        min-width: 0;
      }

      /* D74's smallest size: about 120px for two rails, and it must not be what
         decides the row's height. */
      .micro {
        flex: 0 0 auto;
      }

      /* The row's payload slot. Finding 13 measured the prototype's summary
         truncating at 340px and wanting about 460; D79 then made the generated
         summary the payload, which is longer still. So it wraps rather than
         clipping, and the slot is allowed two lines. */
      .payload {
        margin-top: var(--almanac-gap-xs);
        font-size: 0.9375rem;
        overflow-wrap: anywhere;
      }

      .when {
        margin-top: var(--almanac-gap-xs);
        display: flex;
        flex-wrap: wrap;
        gap: var(--almanac-gap-xs);
        font-size: 0.875rem;
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
