// The panel — D63's one screen, and the only entry point that is allowed to be
// expensive.
//
// `panel_custom` loads this bundle when the user opens the panel and not before,
// which is the opposite of the card's situation and the reason D70's stub
// discipline does not apply here. Lit is imported at the top level deliberately.
//
// What this sub-step draws is the timeline's data layer: the window, *now* as the
// divider, one lane per schedule, past on the left of the divider and predicted
// on the right, and every occurrence D11's precise stage dropped shown as
// *outside the set* rather than omitted (D12). The anchor-relative track that
// replaces each lane's list of chips is D72–D74 and is the next sub-step; the
// queries, the ordering and the honest statement of what is not known are the
// parts that have to be right first, because a track drawn over the wrong
// horizon is worse than a list drawn over the right one.

import { LitElement, html, nothing, css } from "lit";
import { customElement, property, state } from "lit/decorators.js";

import { dayOffset, fetchTimeline, isNotLoaded } from "./api";
import {
  armedStages,
  coverageReason,
  horizon,
  laneOrder,
  nextOccurrence,
} from "./derive";
import { clockTime, dateLabel, dayAndTime, relative } from "./format";
import type { HomeAssistant } from "./ha";
import { almanacHitTarget, almanacText, almanacTokens } from "./styles";
import type {
  WireOccurrence,
  WirePastOccurrence,
  WireScheduleTimeline,
  WireTimeline,
} from "./wire";

/** The ranges the header offers, as (days back, days forward). */
const RANGES = [
  { label: "2 days", back: 1, forward: 2 },
  { label: "Week", back: 2, forward: 7 },
  { label: "Month", back: 7, forward: 31 },
] as const;

type Range = (typeof RANGES)[number];

@customElement("almanac-panel")
export class AlmanacPanel extends LitElement {
  @property({ attribute: false }) public hass?: HomeAssistant | undefined;
  @property({ type: Boolean, reflect: true }) public narrow = false;

  @state() private _range: Range = RANGES[1];
  @state() private _timeline?: WireTimeline | undefined;
  @state() private _error?: string | undefined;
  @state() private _notLoaded = false;
  @state() private _at = new Date();

  private _timer?: number | undefined;
  private _inFlight = false;

  public override connectedCallback(): void {
    super.connectedCallback();
    this._timer = window.setInterval(() => {
      this._at = new Date();
    }, 60_000);
    void this._refresh();
  }

  public override disconnectedCallback(): void {
    super.disconnectedCallback();
    if (this._timer !== undefined) {
      window.clearInterval(this._timer);
      this._timer = undefined;
    }
  }

  protected override willUpdate(changed: Map<string, unknown>): void {
    if (changed.has("hass") && this._timeline === undefined) {
      void this._refresh();
    }
  }

  private async _refresh(): Promise<void> {
    const hass = this.hass;
    if (!hass || this._inFlight) {
      return;
    }
    this._inFlight = true;
    // One clock read, threaded into both the window and the divider. This is
    // D64's shape at the top of the frontend: the backend has no clock of its
    // own, so `at` is not a detail the request may omit, and taking it twice
    // would let the window and the divider disagree by a few milliseconds.
    const at = new Date();
    try {
      const timeline = await fetchTimeline(hass, {
        start: dayOffset(at, -this._range.back),
        end: dayOffset(at, this._range.forward),
        at,
      });
      this._timeline = timeline;
      this._at = at;
      this._error = undefined;
      this._notLoaded = false;
    } catch (error) {
      if (isNotLoaded(error)) {
        this._notLoaded = true;
        this._error = undefined;
      } else {
        this._error = error instanceof Error ? error.message : String(error);
      }
    } finally {
      this._inFlight = false;
    }
  }

  private _pickRange(range: Range): void {
    this._range = range;
    this._timeline = undefined;
    void this._refresh();
  }

  protected override render() {
    if (!this.hass) {
      return nothing;
    }
    return html`
      <div class="shell">
        <header>
          <h1>almanac</h1>
          <div class="ranges">
            ${RANGES.map(
              (range) => html`
                <button
                  class="range tappable ${range === this._range
                    ? "selected"
                    : ""}"
                  @click=${() => this._pickRange(range)}
                  aria-pressed=${range === this._range}
                >
                  ${range.label}
                </button>
              `,
            )}
          </div>
          <span class="now mono"
            >now ${clockTime(this.hass, this._at.toISOString())}</span
          >
        </header>
        ${this._content()}
      </div>
    `;
  }

  private _content() {
    if (this._notLoaded) {
      return html`<p class="muted">almanac is reloading.</p>`;
    }
    if (this._error) {
      return html`<p class="problem">${this._error}</p>`;
    }
    const timeline = this._timeline;
    if (!timeline) {
      return html`<p class="muted">Enumerating…</p>`;
    }
    const lanes = [...timeline.schedules].sort(laneOrder);
    return html`
      ${this._banner(timeline)}
      ${lanes.length === 0
        ? html`<p class="muted">No schedules yet.</p>`
        : html`<ul class="lanes">
            ${lanes.map((lane) => this._lane(lane, timeline))}
          </ul>`}
    `;
  }

  /**
   * What the view as a whole could not answer. Two separate sentences, because
   * §12.2 says they are two facts: `truncated` is the view's own cap, and
   * `recorded: false` means the recorder is not loaded and therefore that *every*
   * lane's past half is empty for a reason that is not "nothing happened".
   */
  private _banner(timeline: WireTimeline) {
    const notes: string[] = [];
    if (!timeline.recorded) {
      notes.push(
        "The recorder is not loaded, so nothing to the left of now can be shown.",
      );
    }
    if (timeline.truncated) {
      notes.push("More happened in this window than is shown.");
    }
    return notes.length === 0
      ? nothing
      : html`<div class="banner">
          ${notes.map((note) => html`<p class="problem">${note}</p>`)}
        </div>`;
  }

  private _lane(lane: WireScheduleTimeline, timeline: WireTimeline) {
    const hass = this.hass!;
    const next = nextOccurrence(lane.plan, this._at);
    const stages = armedStages(lane.plan);
    const coverage = coverageReason(lane.coverage, lane.plan);
    const limit = horizon(lane.plan);
    const future = [...lane.plan.occurrences]
      .filter((occurrence) => occurrence.start !== null)
      .sort((a, b) => (a.start! < b.start! ? -1 : 1));

    return html`
      <li class="lane">
        <div class="head">
          <span class="name">${lane.name ?? lane.schedule_id}</span>
          ${stages.partial
            ? html`<span class="pill disarmed"
                >${stages.armed} of ${stages.total} stages armed</span
              >`
            : nothing}
          ${next
            ? html`<span class="muted"
                >next ${dayAndTime(hass, next.start!)} ·
                ${relative(next.start!, this._at)}</span
              >`
            : html`<span class="muted">nothing ahead in this window</span>`}
        </div>

        <div class="track" role="group" aria-label=${lane.name ?? lane.schedule_id}>
          <div class="half past">
            ${timeline.recorded && lane.past.length === 0
              ? html`<span class="muted">nothing recorded</span>`
              : lane.past.map((entry) => this._pastChip(entry))}
          </div>
          <div class="divider" aria-hidden="true"></div>
          <div class="half future">
            ${future.map((occurrence) => this._futureChip(occurrence))}
          </div>
        </div>

        ${coverage ? html`<p class="problem">${coverage}</p>` : nothing}
        ${limit.limit === "budget"
          ? html`<p class="muted">
              Enumerated to ${dayAndTime(hass, limit.solidThrough)}; almanac
              stopped counting there.
            </p>`
          : nothing}
        ${limit.limit === "sources" || limit.limit === "both"
          ? html`<p class="muted">
              Known to ${dayAndTime(hass, limit.solidThrough)}; past that a
              source would not commit.
            </p>`
          : nothing}
      </li>
    `;
  }

  /**
   * The recorded past. D116 is why `announced` and `executed` are both drawn:
   * `run_now` fires an execution event and, under D109, no occurrence event, so
   * an execution with nothing announced is a manual run and says so rather than
   * looking like a scheduled stage that lost its announcement.
   */
  private _pastChip(entry: WirePastOccurrence) {
    const hass = this.hass!;
    const failed =
      entry.result === "failed" ||
      (entry.actions ?? []).some((action) => action.status === "failed");
    const manual = entry.executed && !entry.announced;
    return html`
      <span
        class="chip pill ${failed ? "failed" : ""}"
        title=${`${entry.kind} · ${dayAndTime(hass, entry.at)}`}
      >
        <span class="mono">${clockTime(hass, entry.at)}</span>
        <span>${entry.kind}</span>
        ${manual ? html`<span class="muted">manual</span>` : nothing}
        ${entry.result && entry.result !== "fired"
          ? html`<span class="muted">${entry.result}</span>`
          : nothing}
      </span>
    `;
  }

  /**
   * The predicted future. D12's requirement is the whole of this method: an
   * occurrence the precise stage dropped is drawn as *outside the set*, and one
   * whose anchor would not resolve is drawn as unknown — neither is omitted,
   * because a timeline that silently loses an occurrence cannot be told apart
   * from a timeline that is wrong.
   */
  private _futureChip(occurrence: WireOccurrence) {
    const hass = this.hass!;
    const status = occurrence.status;
    const classes = [
      "chip",
      "pill",
      status === "unresolved" ? "unknown" : "",
      !occurrence.armed ? "disarmed" : "",
    ]
      .filter(Boolean)
      .join(" ");
    const note =
      status === "outside_set"
        ? "outside the set"
        : status === "overlaps_previous"
          ? "overlaps the one before"
          : status === "unresolved"
            ? (occurrence.problem?.reason ?? "unknown")
            : null;
    return html`
      <span
        class=${classes}
        title=${`${dateLabel(hass, occurrence.start_date)} · ${occurrence.rule_id}`}
      >
        <span class="mono">${clockTime(hass, occurrence.start!)}</span>
        ${occurrence.end
          ? html`<span class="mono muted"
              >–${clockTime(hass, occurrence.end)}</span
            >`
          : nothing}
        ${note ? html`<span class="muted">${note}</span>` : nothing}
        ${!occurrence.armed ? html`<span class="muted">off</span>` : nothing}
      </span>
    `;
  }

  static override styles = [
    almanacTokens,
    almanacText,
    almanacHitTarget,
    css`
      :host {
        display: block;
        background: var(--primary-background-color);
        min-height: 100%;
      }

      .shell {
        padding: var(--almanac-gap-md);
        max-width: 1100px;
        margin: 0 auto;
        box-sizing: border-box;
      }

      header {
        display: flex;
        flex-wrap: wrap;
        align-items: baseline;
        gap: var(--almanac-gap-md);
      }

      h1 {
        font-size: 1.25rem;
        margin: 0;
        flex: 1 1 auto;
      }

      .ranges {
        display: flex;
        gap: var(--almanac-gap-xs);
      }

      .range {
        padding: var(--almanac-gap-xs) var(--almanac-gap-sm);
        border-radius: 12px;
        border: 1px solid var(--almanac-rail);
        min-height: 36px;
      }

      .range.selected {
        border-color: var(--almanac-known);
        color: var(--almanac-known);
      }

      .lanes {
        list-style: none;
        margin: var(--almanac-gap-md) 0 0;
        padding: 0;
      }

      .lane {
        background: var(--card-background-color);
        border-radius: var(--ha-card-border-radius, 12px);
        padding: var(--almanac-gap-md);
        margin-bottom: var(--almanac-gap-sm);
      }

      .head {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: var(--almanac-gap-sm);
      }

      .name {
        font-weight: 500;
        flex: 1 1 auto;
      }

      /* D63 — one view, *now* as the divider. The two halves share a row and
         scroll independently, so a long recorded past cannot push the predicted
         future off the screen; the divider itself is the only fixed thing. */
      .track {
        display: flex;
        align-items: stretch;
        gap: var(--almanac-gap-sm);
        margin-top: var(--almanac-gap-sm);
      }

      .half {
        display: flex;
        gap: var(--almanac-gap-xs);
        overflow-x: auto;
        flex-wrap: nowrap;
        padding-bottom: var(--almanac-gap-xs);
        min-width: 0;
        align-items: center;
      }

      .past {
        flex: 1 1 40%;
        justify-content: flex-end;
      }

      .future {
        flex: 1 1 60%;
      }

      .divider {
        flex: 0 0 2px;
        background: var(--almanac-now);
        border-radius: 1px;
      }

      .chip {
        gap: var(--almanac-gap-xs);
      }

      .banner {
        margin-top: var(--almanac-gap-sm);
      }

      p {
        margin: var(--almanac-gap-sm) 0 0;
        font-size: 0.875rem;
      }

      @media (max-width: 600px) {
        .shell {
          padding: var(--almanac-gap-sm);
        }
      }
    `,
  ];
}

declare global {
  interface HTMLElementTagNameMap {
    "almanac-panel": AlmanacPanel;
  }
}
