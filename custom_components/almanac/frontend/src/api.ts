// The three reads, and the one rule the frontend has to keep.
//
// Two of them are almanac's own commands and the third is not: the stored
// schedules come from the collection websocket Home Assistant generates for us,
// `almanac/schedule/list`, which `storage.py` registers by handing
// `WS_PREFIX_SCHEDULE` to `DictStorageCollectionWebsocket`. It is listed here
// because D73's track needs it — an occurrence says *when* and the stored rule
// says *what it is relative to* — and because a reader looking for where the
// anchors come from should not have to discover that one of the three reads is
// not in this file.
//
// D64 gives the backend a single clock reader — the tick — and
// `tests/test_design_constraints.py` enforces it with an AST sweep. The
// consequence reaches as far as this file: `almanac/timeline` and
// `almanac/dry_run` both take the instant they are about as a required field,
// and there is nothing on the Python side that will fill it in. The browser's
// clock is the right one anyway, because it is the clock the user is reading the
// screen by.
//
// Every instant goes over the wire as `Date.toISOString()`, which is UTC with a
// `Z` offset. The backend refuses a naive string and converts an aware one into
// the instance's zone, so sending UTC is both accepted and unambiguous — and it
// means a browser whose own zone disagrees with the instance's cannot shift a
// window by a day.

import type { HomeAssistant } from "./ha";
import type { StoredSchedule } from "./stored";
import type { WireDryRun, WireTimeline } from "./wire";

export const WS_TIMELINE = "almanac/timeline";
export const WS_DRY_RUN = "almanac/dry_run";
export const WS_SCHEDULE_LIST = "almanac/schedule/list";

/** `send_error`'s shape, as `sendMessagePromise` rejects with it. */
export interface WsError {
  code: string;
  message: string;
}

export const isWsError = (error: unknown): error is WsError =>
  typeof error === "object" &&
  error !== null &&
  typeof (error as WsError).code === "string";

/**
 * `not_found` with "almanac is not loaded" is the answer while the config entry
 * is reloading, and is worth telling apart from a real failure: it is temporary
 * and the right response is to say so rather than to show an error.
 */
export const isNotLoaded = (error: unknown): boolean =>
  isWsError(error) && error.code === "not_found";

export interface TimelineRequest {
  start: Date;
  end: Date;
  /** D63's divider. Defaults to now, which is the only case the panel uses. */
  at?: Date;
  scheduleIds?: readonly string[];
}

export const fetchTimeline = (
  hass: HomeAssistant,
  request: TimelineRequest,
): Promise<WireTimeline> =>
  hass.connection.sendMessagePromise<WireTimeline>({
    type: WS_TIMELINE,
    start: request.start.toISOString(),
    end: request.end.toISOString(),
    at: (request.at ?? new Date()).toISOString(),
    ...(request.scheduleIds ? { schedule_ids: [...request.scheduleIds] } : {}),
  });

export interface DryRunRequest {
  at: Date;
  scheduleIds?: readonly string[];
  /**
   * D41's distinction, not a verbosity flag. `true` evaluates as a tick that has
   * been watching all along; `false` as the recovery pass that has not. The
   * backend defaults to `true` and so does this.
   */
  live?: boolean;
}

export const fetchDryRun = (
  hass: HomeAssistant,
  request: DryRunRequest,
): Promise<WireDryRun> =>
  hass.connection.sendMessagePromise<WireDryRun>({
    type: WS_DRY_RUN,
    at: request.at.toISOString(),
    live: request.live ?? true,
    ...(request.scheduleIds ? { schedule_ids: [...request.scheduleIds] } : {}),
  });

/**
 * Every stored schedule, as the collection websocket returns them.
 *
 * No request fields and no `at`: this read is the configuration, not an
 * evaluation of it, so D64 does not reach it. The panel and the card both pair
 * it with a timeline read and join the two on `rule_id` — see `stored.ts` for
 * why the anchor is fetched rather than carried on the occurrence.
 */
export const fetchSchedules = (hass: HomeAssistant): Promise<StoredSchedule[]> =>
  hass.connection.sendMessagePromise<StoredSchedule[]>({
    type: WS_SCHEDULE_LIST,
  });

/** Midnight local, `days` away from `from`. Window edges, not instants of interest. */
export const dayOffset = (from: Date, days: number): Date => {
  const shifted = new Date(from);
  shifted.setDate(shifted.getDate() + days);
  shifted.setHours(0, 0, 0, 0);
  return shifted;
};
