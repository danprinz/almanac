// D62's entry point, behind D160's stub.
//
// **What this element replaces, which is the thing to understand before
// changing it.** Core's `more-info-content` checks
// `stateObj.attributes.custom_ui_more_info` *before* it dispatches on domain,
// and when the attribute is there it renders that tag and never consults the
// per-domain map. So publishing the attribute on a `switch` does not add a
// region to the dialog — it takes the switch's own control away and puts this
// in its place. That is why the first thing below is an arm control: without
// one, D62's entry point would be a regression for the one gesture a schedule
// switch exists for. §16.6 records it as a judgement call for that reason.
//
// What is *not* ours: the dialog's header, its cog, the attribute expander and
// the history and logbook tabs all live in `more-info-dialog`, outside the
// element this file defines. `ux/FINDINGS.md` finding 11 drew that boundary and
// it still holds — including the part that cannot be fixed, which is that the
// Related tab cannot list almanac's own entities, because the related-items
// graph's `ItemType` is a closed enum with no registration hook.
//
// **What it shows, and why these four things.** The dialog is about one
// schedule, so it answers the four questions a schedule switch cannot: whether
// it is armed and with how many stages (D77), what it is (D79's generated
// summary), when it next runs, and what it touches (D78's footprint, here with
// the links D78 asked for and step 9d could not spell). The way out is an
// anchor into the panel, because D150 puts the editor there.

import { LitElement, html, nothing, css } from "lit";
import { customElement, property, state } from "lit/decorators.js";

import { dayOffset, fetchSchedules, fetchTimeline, isNotLoaded } from "./api";
import { nextOccurrence } from "./derive";
import { draftOf, footprintOf } from "./draft";
import { dayAndTime, relative } from "./format";
import type { HassEntity, HomeAssistant } from "./ha";
import { openMoreInfo } from "./moreinfo";
import { armedRules, buildTrack } from "./rails";
import type { StoredSchedule } from "./stored";
import { almanacHitTarget, almanacText, almanacTokens } from "./styles";
import { summaryLine } from "./track";
import type { WireScheduleTimeline } from "./wire";

/** Same week the card looks ahead, for the same reason: it answers "what next". */
const LOOKAHEAD_DAYS = 8;

/**
 * Where the attribute the switch publishes is read.
 *
 * Core hands this element a `stateObj` and nothing else that identifies the
 * schedule. The id is the entity's registry `unique_id`, which is not on the
 * state object and not reachable from inside the dialog, so `switch.py`
 * publishes it as an attribute instead — see the docstring there for why that
 * is cheaper than making D66's slug a two-way mapping.
 */
const ATTR_SCHEDULE_ID = "schedule_id";

/** The panel, and the parameter that asks it to open one schedule (D161). */
const PANEL_PATH = "/almanac";
const EDIT_PARAM = "edit";

@customElement("almanac-more-info-body")
export class AlmanacMoreInfoBody extends LitElement {
  @property({ attribute: false }) public hass?: HomeAssistant | undefined;
  @property({ attribute: false }) public stateObj?: HassEntity | undefined;

  @state() private _lane?: WireScheduleTimeline | undefined;
  @state() private _stored?: StoredSchedule | undefined;
  @state() private _error?: string | undefined;
  @state() private _notLoaded = false;
  @state() private _at = new Date();

  private _loadedFor?: string | undefined;
  private _inFlight = false;
  private _timer?: number | undefined;

  public override connectedCallback(): void {
    super.connectedCallback();
    // One minute, for the one relative label this element draws. The dialog is
    // usually open for less than that, so this exists for the case where it is
    // not — a dialog left open across the occurrence it was predicting.
    this._timer = window.setInterval(() => {
      this._at = new Date();
    }, 60_000);
  }

  public override disconnectedCallback(): void {
    super.disconnectedCallback();
    if (this._timer !== undefined) {
      window.clearInterval(this._timer);
      this._timer = undefined;
    }
  }

  /**
   * `hass` arrives on every state change in the instance, so the read is keyed
   * to the schedule rather than to the property.
   *
   * `_loadedFor` is the guard: core sets `hass` continuously for as long as the
   * dialog is open, and a refresh per set would be a websocket round trip per
   * light anybody switched on. What *should* re-read is a different schedule,
   * which only happens if the dialog is reused — so that is what is compared.
   */
  protected override willUpdate(changed: Map<string, unknown>): void {
    const id = this._scheduleId;
    if (!this.hass || id === undefined) {
      return;
    }
    if (changed.has("stateObj") || changed.has("hass")) {
      if (this._loadedFor !== id) {
        this._loadedFor = id;
        void this._refresh(id);
      }
    }
  }

  /** The schedule this dialog is about, or nothing if the attribute is absent. */
  private get _scheduleId(): string | undefined {
    const value = this.stateObj?.attributes[ATTR_SCHEDULE_ID];
    return typeof value === "string" && value !== "" ? value : undefined;
  }

  private async _refresh(id: string): Promise<void> {
    const hass = this.hass;
    if (!hass || this._inFlight) {
      return;
    }
    this._inFlight = true;
    const from = new Date();
    try {
      const [timeline, stored] = await Promise.all([
        fetchTimeline(hass, {
          start: from,
          end: dayOffset(from, LOOKAHEAD_DAYS),
          at: from,
          scheduleIds: [id],
        }),
        fetchSchedules(hass),
      ]);
      this._lane = timeline.schedules.find((lane) => lane.schedule_id === id);
      this._stored = stored.find((schedule) => schedule.id === id);
      this._error = undefined;
      this._notLoaded = false;
    } catch (error) {
      if (isNotLoaded(error)) {
        this._notLoaded = true;
        this._error = undefined;
      } else {
        this._error = error instanceof Error ? error.message : String(error);
      }
      // So that the next `hass` is allowed to try again. A failed read that
      // marks itself done leaves the dialog empty until it is reopened.
      this._loadedFor = undefined;
    } finally {
      this._inFlight = false;
    }
  }

  private _entityName = (entityId: string): string | undefined => {
    const name = this.hass?.states[entityId]?.attributes["friendly_name"];
    return typeof name === "string" ? name : undefined;
  };

  protected override render() {
    const stateObj = this.stateObj;
    if (!this.hass || !stateObj) {
      return nothing;
    }
    return html`
      <div class="body">
        ${this._arm(stateObj)} ${this._what()} ${this._when()}
        ${this._touches()} ${this._out()}
      </div>
    `;
  }

  /**
   * The control the attribute took away, put back.
   *
   * `switch.turn_on` / `switch.turn_off` rather than `switch.toggle`, because
   * the button says what it will do and a toggle would make that a guess about
   * state the call does not read. The caveat under it is D47 and it is only
   * shown where it can happen: disarming a schedule that is holding an interval
   * does not end the interval, and a schedule with no `during` rule has no
   * interval to hold.
   */
  private _arm(stateObj: HassEntity) {
    const armed = stateObj.state === "on";
    const stages = this._stored ? armedRules(this._stored) : undefined;
    return html`
      <section class="arm">
        <button
          class="primary tappable"
          @click=${() => void this._setArmed(!armed)}
        >
          ${armed ? "Disarm" : "Arm"}
        </button>
        ${stages?.partial
          ? html`<span class="pill disarmed"
              >${stages.armed} of ${stages.total} stages armed</span
            >`
          : html`<span class="pill">${armed ? "armed" : "disarmed"}</span>`}
        ${armed && this._holdsAnInterval
          ? html`<p class="muted">
              If it is in the middle of an interval, disarming waits for that
              interval to end rather than cutting it short.
            </p>`
          : nothing}
      </section>
    `;
  }

  /** Whether any rule could be holding an interval right now. */
  private get _holdsAnInterval(): boolean {
    return (this._stored?.rules ?? []).some((rule) => rule.kind === "during");
  }

  private async _setArmed(armed: boolean): Promise<void> {
    const hass = this.hass;
    const entityId = this.stateObj?.entity_id;
    if (!hass || entityId === undefined) {
      return;
    }
    try {
      await hass.callService("switch", armed ? "turn_on" : "turn_off", {
        entity_id: entityId,
      });
    } catch (error) {
      this._error = error instanceof Error ? error.message : String(error);
    }
  }

  /** D79, in the same order of authority the card row uses. */
  private _what() {
    const stored = this._stored;
    const lane = this._lane;
    if (!stored || !lane) {
      return this._status();
    }
    const track = buildTrack(stored, lane.plan, this._at, this._entityName);
    const summary =
      stored.description !== ""
        ? stored.description
        : track.rails.length > 0
          ? summaryLine(track)
          : "";
    return html`
      ${summary === "" ? nothing : html`<p class="summary">${summary}</p>`}
      ${track.rails.length > 0
        ? html`<almanac-track size="lane" .track=${track}></almanac-track>`
        : nothing}
    `;
  }

  private _when() {
    const lane = this._lane;
    if (!lane || !this.hass) {
      return nothing;
    }
    const next = nextOccurrence(lane.plan, this._at);
    return html`
      <p class="when">
        ${next
          ? html`<span class="mono">${dayAndTime(this.hass, next.start!)}</span>
              <span class="muted">· ${relative(next.start!, this._at)}</span>`
          : html`<span class="muted">Nothing in the next week.</span>`}
      </p>
    `;
  }

  /**
   * D78's footprint, now with the links D78 asked for.
   *
   * Three of the four lists link and one does not, and the split is not
   * cosmetic: entities, time sources and scripts are entities, and a service is
   * a name in the service registry with no dialog to open. The unexpanded count
   * is not a link either, for a stronger reason — it is the number of targets
   * almanac did *not* resolve, so there is nothing it could link to.
   */
  private _touches() {
    const stored = this._stored;
    if (!stored) {
      return nothing;
    }
    const footprint = footprintOf(draftOf(stored));
    const linked: [string, string[]][] = [
      ["Writes to", footprint.entities],
      ["Reads a time from", footprint.reads],
      ["Runs", footprint.scripts],
    ];
    const shown = linked.filter(([, items]) => items.length > 0);
    if (
      shown.length === 0 &&
      footprint.services.length === 0 &&
      footprint.unexpanded === 0
    ) {
      return nothing;
    }
    return html`
      <section class="touches">
        <h3>What this touches</h3>
        ${shown.map(
          ([label, items]) => html`
            <p>
              <span class="muted">${label}</span>
              ${items.map(
                (item) => html`<button
                  class="pill mono tappable"
                  @click=${() => openMoreInfo(this, item)}
                >
                  ${this._entityName(item) ?? item}
                </button>`,
              )}
            </p>
          `,
        )}
        ${footprint.services.length > 0
          ? html`<p>
              <span class="muted">Calls</span>
              ${footprint.services.map(
                (item) => html`<span class="pill mono">${item}</span>`,
              )}
            </p>`
          : nothing}
        ${footprint.unexpanded > 0
          ? html`<p class="muted">
              ${footprint.unexpanded}
              target${footprint.unexpanded === 1 ? "" : "s"} name an area,
              floor, device or label. almanac does not expand those to entities,
              so what they reach is not listed here.
            </p>`
          : nothing}
      </section>
    `;
  }

  /**
   * The way out, and D161's whole surface.
   *
   * A plain anchor, not core's navigation event. The event would be a second
   * verbatim third-party identifier of the same silent-failure class as
   * `MORE_INFO_EVENT`, and an anchor cannot fail that way: if nothing
   * intercepts the click the browser loads `/almanac` itself, which is slower
   * and correct. The query parameter is read by the panel on first update, so
   * either route arrives at the same screen.
   */
  private _out() {
    const id = this._scheduleId;
    if (id === undefined) {
      return nothing;
    }
    const href = `${PANEL_PATH}?${EDIT_PARAM}=${encodeURIComponent(id)}`;
    return html`<p class="out">
      <a class="tappable" href=${href}>Edit this schedule in almanac</a>
    </p>`;
  }

  /** What there is to say while there is nothing else to say. */
  private _status() {
    if (this._scheduleId === undefined) {
      // Not a failure the user caused, and not one they can fix — it means the
      // attribute arrived without an id, which is a version skew between this
      // bundle and the integration that published it.
      return html`<p class="muted">
        almanac did not recognise this entity as one of its schedules.
      </p>`;
    }
    if (this._notLoaded) {
      return html`<p class="muted">almanac is reloading.</p>`;
    }
    if (this._error) {
      return html`<p class="problem">${this._error}</p>`;
    }
    return html`<p class="muted">Reading the schedule…</p>`;
  }

  static override styles = [
    almanacTokens,
    almanacText,
    almanacHitTarget,
    css`
      .body {
        display: flex;
        flex-direction: column;
        gap: var(--almanac-gap-md);
      }

      h3 {
        font-size: 0.8125rem;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        color: var(--secondary-text-color);
        margin: 0 0 var(--almanac-gap-sm);
      }

      p {
        margin: 0;
      }

      .arm {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: var(--almanac-gap-sm);
      }

      /* The arm control is the one thing in this dialog that was already there
         before almanac replaced the domain control, so it is the one thing that
         must not look like an afterthought. */
      .primary {
        min-height: 40px;
        padding: 0 var(--almanac-gap-md);
        border: none;
        border-radius: 4px;
        background: var(--primary-color);
        color: var(--text-primary-color, #fff);
        font: inherit;
        cursor: pointer;
      }

      .arm p {
        flex: 1 0 100%;
      }

      .summary {
        font-weight: 500;
      }

      .touches p {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: var(--almanac-gap-xs);
      }

      .touches p + p {
        margin-top: var(--almanac-gap-sm);
      }

      .out a {
        color: var(--primary-color);
        min-height: 40px;
        display: inline-flex;
        align-items: center;
      }
    `,
  ];
}

declare global {
  interface HTMLElementTagNameMap {
    "almanac-more-info-body": AlmanacMoreInfoBody;
  }
}
