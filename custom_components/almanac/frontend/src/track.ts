// D73's device, drawn: the anchor-relative track.
//
// The axis is the anchor sequence, not the clock. Each distinct anchor gets a
// rail of fixed width, offsets lay out against a *local* scale sized to the
// widest offset on that rail, and what sits between two rails is a fixed-width
// break carrying the real elapsed time as text. The flagship evening is why:
// 25½ hours, of which 55 minutes carry every stage but one. On a time axis the
// whole of the interesting part is a single pixel, and no amount of zooming
// fixes it, because zooming in loses havdalah and zooming out loses everything
// else.
//
// All geometry arrives computed, from `rails.ts`. This file positions marks and
// chooses colours and does no arithmetic on instants, which is what let the
// decisions be tested without a DOM (D132).
//
// Two sizes are implemented, the two D74 needs now: `lane` for the timeline and
// `micro` for a list row. The editor's full-width size arrives with the editor,
// because what distinguishes it is not a larger rail but hit targets, drag and
// the inline reference creation of D78 — a bigger `lane` would look like the
// editor without being one.
//
// Shapes carry meaning, not only colour (`ux/FINDINGS.md` finding 17):
//
//   ● a moment — an `at` rule fires here
//   ■ an edge  — a `during` interval enters or leaves here
//   ○ ▫ hollow — the rule is disabled (D77); it is still drawn
//
// and amber is reserved for exactly one thing, "almanac does not know", which
// here means a rail whose anchor would not resolve on the day being drawn.

import { LitElement, html, nothing, css } from "lit";
import { customElement, property } from "lit/decorators.js";

import { duration, offsetLabel } from "./format";
import { summaryEndpoints } from "./rails";
import type { Endpoint, Gap, Rail, Stage, Track } from "./rails";
import { almanacText, almanacTokens } from "./styles";

export type TrackSize = "lane" | "micro";

/** An endpoint, spelled. A stage on the anchor itself is just the anchor. */
const endpointLabel = (endpoint: Endpoint): string =>
  endpoint.offset === 0
    ? endpoint.rail
    : `${endpoint.rail} ${offsetLabel(endpoint.offset)}`;

/**
 * D79's generated summary: `candle lighting −45m → havdalah +30m`.
 *
 * It lives here rather than in `rails.ts` because it is a label and `rails.ts`
 * has no run-time imports (D132), and `offsetLabel` is a run-time import. The
 * two ends arrive as data from `summaryEndpoints` and only the joining is done
 * here, so the decision about *which* stages are the ends stays under test.
 *
 * Empty string when there is nothing to say, so a caller can fall back without
 * having to recognise a placeholder sentence as one.
 */
export const summaryLine = (track: Track): string =>
  summaryEndpoints(track).map(endpointLabel).join(" → ");

/**
 * Where a stage sits along its rail, as a percentage of the rail's width.
 *
 * `scale` is the half-width in seconds, so `offset / scale` is in [-1, 1] and
 * the anchor itself is the centre. A rail with no scale has every stage on the
 * anchor, and they all land on the centre — which is correct and is also why
 * such a rail is drawn narrow.
 */
const position = (rail: Rail, stage: Stage): number =>
  rail.scale === 0 ? 50 : 50 + (stage.offset / rail.scale) * 50;

/** `fire` is a moment; `enter` and `exit` are the two edges of a span. */
const shapeOf = (stage: Stage): string =>
  stage.role === "fire" ? "moment" : "edge";

/**
 * The track as one sentence, for a screen reader and for the micro size's
 * tooltip. The visible micro-track has no text at all, so this is not a
 * convenience — without it that size conveys nothing to anyone not looking at
 * it.
 */
const spoken = (track: Track): string => {
  if (track.rails.length === 0) {
    return "No stages.";
  }
  const parts = track.rails.map((rail) => {
    const stages = rail.stages
      .map(
        (stage) =>
          `${offsetLabel(stage.offset)}${stage.enabled ? "" : " (off)"}`,
      )
      .join(", ");
    const unknown = rail.instant === null ? ", not resolved" : "";
    return `${rail.label}: ${stages}${unknown}`;
  });
  const gaps = track.gaps
    .map((gap, index) =>
      gap.seconds === null
        ? null
        : `${duration(gap.seconds)} between ${track.rails[index]!.label} and ${
            track.rails[index + 1]!.label
          }`,
    )
    .filter((part): part is string => part !== null);
  const warning = track.unstable ? " Their order varies." : "";
  return `${parts.join(". ")}.${gaps.length ? ` ${gaps.join(", ")}.` : ""}${warning}`;
};

@customElement("almanac-track")
export class AlmanacTrack extends LitElement {
  @property({ attribute: false }) public track?: Track | undefined;
  @property({ reflect: true }) public size: TrackSize = "lane";

  protected override render() {
    const track = this.track;
    if (!track) {
      return nothing;
    }
    if (track.rails.length === 0) {
      return this.size === "micro"
        ? nothing
        : html`<span class="muted">No stages.</span>`;
    }
    return html`
      <div
        class="rails"
        role="img"
        aria-label=${spoken(track)}
        title=${this.size === "micro" ? spoken(track) : ""}
      >
        ${track.rails.map(
          (rail, index) => html`
            ${index === 0 ? nothing : this._gap(track.gaps[index - 1]!)}
            ${this._rail(rail)}
          `,
        )}
      </div>
    `;
  }

  /**
   * One anchor. The cap is the diagram's `┌── candle lighting ──┐`: it is drawn
   * as a bracket rather than as a plain label because the span it covers is the
   * claim being made — these offsets are measured from *this* event, and the
   * next rail's are measured from a different one.
   */
  private _rail(rail: Rail) {
    const point = rail.scale === 0;
    const unknown = rail.instant === null;
    const classes = [
      "rail",
      point ? "point" : "",
      unknown ? "unknown" : "",
    ]
      .filter(Boolean)
      .join(" ");
    return html`
      <div class=${classes}>
        ${this.size === "micro"
          ? nothing
          : html`<div class="cap">
              <span class="name" title=${rail.detail}>${rail.label}</span>
            </div>`}
        <div class="axis">
          <span class="line"></span>
          <span class="anchor" style="left: 50%"></span>
          ${rail.stages.map(
            (stage) => html`
              <span
                class="dot ${shapeOf(stage)} ${stage.enabled ? "" : "off"}"
                style="left: ${position(rail, stage)}%"
              ></span>
            `,
          )}
        </div>
        ${this.size === "micro"
          ? nothing
          : html`<div class="marks">
              ${rail.stages.map(
                (stage) => html`
                  <span
                    class="mark mono ${stage.enabled ? "" : "off"}"
                    style="left: ${position(rail, stage)}%"
                    title=${stage.does ?? ""}
                    >${offsetLabel(stage.offset)}</span
                  >
                `,
              )}
            </div>`}
      </div>
    `;
  }

  /**
   * The compressed break. The duration is text and never a width, which is the
   * whole of D73: a break of twenty-five hours and a break of twenty minutes
   * are the same number of pixels and say so in words.
   *
   * D76's mark goes here too, on the pair it was computed for. When the order
   * varies without any adjacent pair inverting, `rails.ts` sets `unstable` on
   * the track and on no gap — so the sentence in `aria-label` still says it and
   * nothing is marked in the wrong place.
   */
  private _gap(gap: Gap) {
    const unknown = gap.seconds === null;
    return html`
      <div class="gap ${unknown ? "unknown" : ""} ${gap.unstable ? "varies" : ""}">
        <span class="hair"></span>
        ${this.size === "micro"
          ? nothing
          : html`<span class="elapsed mono"
              >${unknown ? "?" : duration(gap.seconds!)}</span
            >`}
        ${this.size !== "micro" && gap.unstable
          ? html`<span
              class="varies-note"
              title="These two anchors swap order on another date in this window."
              >order varies</span
            >`
          : nothing}
      </div>
    `;
  }

  static override styles = [
    almanacTokens,
    almanacText,
    css`
      :host {
        display: block;
        /* The two sizes differ in these four numbers and in what is drawn at
           all; nothing in the layout below is written twice. */
        --rail-w: 168px;
        --point-w: 72px;
        --gap-w: 96px;
        --dot: 10px;
        --axis-h: 14px;
      }

      :host([size="micro"]) {
        --rail-w: 52px;
        --point-w: 20px;
        --gap-w: 14px;
        --dot: 6px;
        --axis-h: 8px;
      }

      .rails {
        display: flex;
        align-items: flex-end;
        overflow-x: auto;
        padding-bottom: 2px;
      }

      :host([size="micro"]) .rails {
        overflow-x: visible;
        align-items: center;
      }

      .rail {
        flex: 0 0 var(--rail-w);
        min-width: 0;
      }

      .rail.point {
        flex: 0 0 var(--point-w);
      }

      /* The diagram bracket: ┌── candle lighting ──┐. The label sits inside it, so
         anchor name widens nothing — it ellipsises, and the full spelling is in
         the title. */
      .cap {
        border: 1px solid var(--almanac-rail);
        border-bottom: none;
        border-radius: 6px 6px 0 0;
        text-align: center;
        padding: 2px var(--almanac-gap-xs) 1px;
        min-width: 0;
      }

      .name {
        display: block;
        font-size: 0.8125rem;
        line-height: 1.3;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }

      .rail.unknown .cap {
        border-color: var(--almanac-unknown);
        border-style: dashed;
        border-bottom: none;
      }

      .rail.unknown .name {
        color: var(--almanac-unknown);
      }

      .axis {
        position: relative;
        height: var(--axis-h);
      }

      .line {
        position: absolute;
        left: 0;
        right: 0;
        top: 50%;
        height: 2px;
        margin-top: -1px;
        background: var(--almanac-rail);
      }

      .rail.unknown .line {
        background: none;
        border-top: 2px dashed var(--almanac-unknown);
        height: 0;
      }

      /* The anchor's own instant: a vertical tick, so "zero" is a place on the
         rail and not a dot competing with the stages. */
      .anchor {
        position: absolute;
        top: 0;
        bottom: 0;
        width: 2px;
        margin-left: -1px;
        background: var(--almanac-rail);
      }

      .rail.unknown .anchor {
        background: var(--almanac-unknown);
      }

      .dot {
        position: absolute;
        top: 50%;
        width: var(--dot);
        height: var(--dot);
        margin: calc(var(--dot) / -2) 0 0 calc(var(--dot) / -2);
        background: var(--almanac-known);
      }

      .dot.moment {
        border-radius: 50%;
      }

      .dot.edge {
        border-radius: 1px;
      }

      /* D77 — hollow, and still there. A disabled stage that vanished would make
         a three-stage schedule look like a two-stage one. */
      .dot.off {
        background: var(--card-background-color, transparent);
        box-shadow: inset 0 0 0 2px var(--almanac-disarmed);
      }

      .rail.unknown .dot {
        background: var(--almanac-unknown);
      }

      .marks {
        position: relative;
        height: 1.1rem;
      }

      .mark {
        position: absolute;
        top: 0;
        transform: translateX(-50%);
        font-size: 0.75rem;
        line-height: 1.1rem;
        white-space: nowrap;
        color: var(--secondary-text-color);
      }

      .mark.off {
        text-decoration: line-through;
      }

      .gap {
        flex: 0 0 var(--gap-w);
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: flex-end;
        position: relative;
        padding-bottom: calc(1.1rem + var(--axis-h) / 2 - 1px);
      }

      :host([size="micro"]) .gap {
        padding-bottom: 0;
        justify-content: center;
      }

      .hair {
        position: absolute;
        left: 2px;
        right: 2px;
        bottom: calc(1.1rem + var(--axis-h) / 2 - 1px);
        border-top: 2px dotted var(--almanac-rail);
      }

      :host([size="micro"]) .hair {
        position: static;
        width: 100%;
        border-top-width: 1px;
      }

      .gap.unknown .hair {
        border-top-color: var(--almanac-unknown);
      }

      .elapsed {
        font-size: 0.75rem;
        line-height: 1.2;
        padding: 0 var(--almanac-gap-xs);
        background: var(--card-background-color, var(--primary-background-color));
        color: var(--secondary-text-color);
        position: relative;
        z-index: 1;
      }

      .gap.unknown .elapsed {
        color: var(--almanac-unknown);
      }

      .varies-note {
        font-size: 0.6875rem;
        line-height: 1.2;
        color: var(--almanac-unknown);
        text-align: center;
      }

      .gap.varies .hair {
        border-top-color: var(--almanac-unknown);
      }
    `,
  ];
}

declare global {
  interface HTMLElementTagNameMap {
    "almanac-track": AlmanacTrack;
  }
}
