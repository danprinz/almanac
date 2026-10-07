// The panel — D63's one screen, and the only entry point that is allowed to be
// expensive.
//
// `panel_custom` loads this bundle when the user opens the panel and not before,
// which is the opposite of the card's situation and the reason D70's stub
// discipline does not apply here. Lit is imported at the top level deliberately.
//
// What the panel draws is two different things about each schedule, and D135 is
// the decision that they are two:
//
//   * the **window** — now as the divider, recorded on its left and predicted on
//     its right, with every occurrence D11's precise stage dropped shown as
//     *outside the set* rather than omitted (D12);
//   * the **track** — D73's anchor-relative shape of one occurrence, above it.
//
// An earlier note in this file said the track would replace the lane's chips. It
// does not, because it cannot: a track is the anatomy of a single occurrence and
// a window is a list of many, and dropping the list would drop exactly the
// occurrences D12 exists to keep visible. The track answers "what does this
// schedule do"; the chips answer "and what will it do between Tuesday and
// Friday".

import { LitElement, html, nothing, css } from "lit";
import { customElement, property, state } from "lit/decorators.js";

import { haButton } from "./buttons";
import { dayOffset, fetchSchedules, fetchTimeline, isNotLoaded } from "./api";
import {
  armedStages,
  coverageReason,
  horizon,
  laneOrder,
  nextOccurrence,
} from "./derive";
import { EDITOR_CLOSED } from "./editor";
import type { EditorClosedDetail } from "./editor";
import "./editor";
import { clockTime, dateLabel, dayAndTime, relative } from "./format";
import { browserEnv, loadHaElements } from "./ha-elements";
import type { HaElementsState } from "./ha-elements";
import type { HomeAssistant } from "./ha";
import { armedRules, buildTrack } from "./rails";
import type { StoredSchedule } from "./stored";
import { almanacHitTarget, almanacText, almanacTokens } from "./styles";
import "./segmented";
import "./track";
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

/**
 * D161 — the parameter a small surface uses to ask for one schedule's editor.
 *
 * A query parameter and not a path segment, because `panel_custom` registers
 * one url path and nothing routes below it: `/almanac/<id>` is a 404 on a cold
 * load, and a cold load is exactly what an anchor from a dashboard produces.
 * `const.py` holds the same name on the Python side; nothing compares them at
 * run time, so `tests/test_frontend_assets.py` does.
 */
const EDIT_PARAM = "edit";

/**
 * What the URL is asking for, read once.
 *
 * `window.location` and `URLSearchParams` are platform APIs rather than Home
 * Assistant ones, which is the whole reason D161 is shaped this way: the
 * alternative was core's navigation event, a verbatim third-party identifier of
 * the same silent-failure class as the more-info event, and this needs none.
 *
 * It is read at construction rather than on every update because it is a
 * request, not a state. Acting on it twice would reopen the editor the user had
 * just closed.
 */
const requestedEdit = (): string | undefined => {
  const value = new URLSearchParams(window.location.search).get(EDIT_PARAM);
  return value !== null && value !== "" ? value : undefined;
};

@customElement("almanac-panel")
export class AlmanacPanel extends LitElement {
  @property({ attribute: false }) public hass?: HomeAssistant | undefined;
  @property({ type: Boolean, reflect: true }) public narrow = false;

  @state() private _range: Range = RANGES[1];
  @state() private _timeline?: WireTimeline | undefined;
  @state() private _stored?: Map<string, StoredSchedule> | undefined;
  @state() private _error?: string | undefined;
  @state() private _notLoaded = false;
  @state() private _at = new Date();

  /**
   * The editor, when one is open. `schedule: null` is a create.
   *
   * Wrapped in an object rather than held as `StoredSchedule | null` because
   * those two states are three: closed, creating, and editing a schedule.
   * `undefined` for closed and a one-field box for the other two is the
   * encoding that cannot collapse the first two into each other.
   */
  @state() private _editing?: { schedule: StoredSchedule | null } | undefined;

  /**
   * A schedule the URL named but the collection does not have.
   *
   * Separate from `_error` because it must not replace the screen: the user
   * followed a link to a schedule that has since been deleted, and the useful
   * answer is the list plus a sentence, not an empty panel. `_error` is for a
   * read that failed, where there is no list to show.
   */
  @state() private _notFound?: string | undefined;

  /**
   * D167. `loading` until `loadHaElements` settles; the editor treats it like
   * `failed` for rendering (plain inputs) but does not show the notice, so a
   * cold load does not flash an apology before the pickers arrive.
   */
  @state() private _haElements: HaElementsState = "loading";

  private _timer?: number | undefined;
  private _inFlight = false;

  /** D161's request, consumed once by `_openRequested`. */
  private _requested = requestedEdit();

  public override connectedCallback(): void {
    super.connectedCallback();
    this._timer = window.setInterval(() => {
      this._at = new Date();
    }, 60_000);
    // On the host, by the constant, for the reason `editor.ts` gives about
    // `STAGE_SELECTED`: Lit cannot bind a listener to a name held in a variable,
    // so a literal in the template would be a second spelling nothing compares.
    this.addEventListener(EDITOR_CLOSED, this._onEditorClosed as EventListener);
    void this._refresh();
  }

  public override disconnectedCallback(): void {
    super.disconnectedCallback();
    this.removeEventListener(EDITOR_CLOSED, this._onEditorClosed as EventListener);
    if (this._timer !== undefined) {
      window.clearInterval(this._timer);
      this._timer = undefined;
    }
  }

  protected override firstUpdated(): void {
    // Once, at the top: the panel is the one component that is always mounted
    // before an editor can be, and the answer does not change for the session.
    void loadHaElements(browserEnv()).then((ready) => {
      this._haElements = ready ? "ready" : "failed";
    });
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
      // Both reads, concurrently and against the same `at`. The join is on
      // `rule_id` (see `stored.ts`), so they have to describe the same
      // configuration — and a schedule edited between two sequential reads would
      // give a track whose rails no occurrence belongs to.
      const [timeline, stored] = await Promise.all([
        fetchTimeline(hass, {
          start: dayOffset(at, -this._range.back),
          end: dayOffset(at, this._range.forward),
          at,
        }),
        fetchSchedules(hass),
      ]);
      this._timeline = timeline;
      this._stored = new Map(
        stored.map((schedule) => [schedule.id, schedule] as const),
      );
      this._at = at;
      this._error = undefined;
      this._notLoaded = false;
      // After the collection is in hand and not before: the request names an
      // id, and the editor takes the schedule.
      this._openRequested();
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

  /**
   * D150's mounting point, and a departure from D61's table, which assigns rule
   * editing to the card and gives the panel the timeline.
   *
   * The reason is that the editor needs width and the card does not have it: a
   * `full`-size track is 240px of rail per anchor, and a Lovelace card in a
   * three-column view is narrower than two of them. So the panel hosts it and
   * the card will open it — which is the same division D62 already makes for
   * more-info, where the small surface is the entry point and the large one is
   * the destination. §16.4 records this against D61 rather than editing it.
   *
   * It replaces the lane list rather than floating over it. A dialog would be
   * the conventional shape and needs `ha-dialog`, which nothing in `ha.ts` has
   * a verified typing for; replacing the content needs no component at all, and
   * keeps the focus order linear without a focus trap to get wrong.
   */
  private _edit(schedule: StoredSchedule | null): void {
    this._editing = { schedule };
  }

  /**
   * D161's arriving half.
   *
   * The parameter is left in the URL rather than cleared with
   * `history.replaceState`, which means reloading the page reopens the editor.
   * That is what a link should do — the alternative is a URL that stops meaning
   * what it said as soon as it is used — and closing the editor still returns to
   * the list, because the request has already been consumed in this session.
   */
  private _openRequested(): void {
    const id = this._requested;
    if (id === undefined || this._editing !== undefined) {
      return;
    }
    this._requested = undefined;
    const schedule = this._stored?.get(id);
    if (schedule) {
      this._edit(schedule);
      return;
    }
    this._notFound = id;
  }

  private _onEditorClosed = (event: CustomEvent<EditorClosedDetail>): void => {
    this._editing = undefined;
    if (event.detail.saved) {
      // A save changes the configuration, so the timeline that was enumerated
      // from the old one is stale in a way no amount of re-rendering fixes.
      this._timeline = undefined;
      void this._refresh();
    }
  };

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
          <almanac-segmented
            label="How far to look"
            .options=${RANGES.map((range) => ({
              id: range.label,
              label: range.label,
            }))}
            .selected=${this._range.label}
            .onSelect=${(id: string) =>
              this._pickRange(RANGES.find((range) => range.label === id)!)}
          ></almanac-segmented>
          ${haButton(
            {
              kind: "primary",
              disabled: this._editing !== undefined,
              onClick: () => this._edit(null),
            },
            "New schedule",
          )}
          <span class="now mono"
            >now ${clockTime(this.hass, this._at.toISOString())}</span
          >
        </header>
        ${this._notFound === undefined
          ? nothing
          : html`<p class="problem">
              almanac was asked to open a schedule that is not there any more.
            </p>`}
        ${this._content()}
      </div>
    `;
  }

  private _content() {
    const editing = this._editing;
    if (editing) {
      return html`<almanac-editor
        class="editor"
        .hass=${this.hass}
        .schedule=${editing.schedule}
        .haElements=${this._haElements}
      ></almanac-editor>`;
    }
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

  /** A friendly name for an entity anchor's rail, when the entity exists. */
  private _entityName = (entityId: string): string | undefined => {
    const attributes = this.hass?.states[entityId]?.attributes;
    const name = attributes?.["friendly_name"];
    return typeof name === "string" ? name : undefined;
  };

  private _lane(lane: WireScheduleTimeline, timeline: WireTimeline) {
    const hass = this.hass!;
    const next = nextOccurrence(lane.plan, this._at);
    const stored = this._stored?.get(lane.schedule_id);
    // D77's fraction over the stored rules when they are to hand, because a rule
    // with no occurrence in this window is still a stage of this schedule and a
    // count taken from the plan alone would not know it exists.
    const stages = stored ? armedRules(stored) : armedStages(lane.plan);
    const track = stored
      ? buildTrack(stored, lane.plan, this._at, this._entityName)
      : null;
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
          ${stored
            ? haButton({ onClick: () => this._edit(stored) }, "Edit")
            : nothing}
        </div>

        ${track && track.rails.length > 0
          ? html`<almanac-track
              class="shape"
              size="lane"
              .track=${track}
            ></almanac-track>`
          : nothing}

        <div class="window" role="group" aria-label=${lane.name ?? lane.schedule_id}>
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

      /* D135's upper half: the shape, drawn once, above the window. */
      .shape {
        margin-top: var(--almanac-gap-sm);
      }

      /* D63 — one view, *now* as the divider. The two halves share a row and
         scroll independently, so a long recorded past cannot push the predicted
         future off the screen; the divider itself is the only fixed thing. */
      .window {
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

      .editor {
        margin-top: var(--almanac-gap-md);
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
