// The four reads, the three writes, and the one rule the frontend has to keep.
//
// Three of the reads are almanac's own commands and the fourth is not, and
// neither is any of the writes: the stored schedules come from the collection
// websocket Home Assistant generates for us, `almanac/schedule/{list,create,
// update,delete}`, which `storage.py` registers by handing `WS_PREFIX_SCHEDULE`
// to `DictStorageCollectionWebsocket`. They are listed here because D73's track
// needs the list — an occurrence says *when* and the stored rule says *what it
// is relative to* — and because a reader looking for where the anchors come from,
// or for where a save goes, should not have to discover that half of this
// boundary is not in this file.
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

import type { ScheduleCreate, ScheduleUpdate } from "./draft";
import type { HomeAssistant } from "./ha";
import type { StoredSchedule } from "./stored";
import type { WireDryRun, WireResolverCatalogue, WireTimeline } from "./wire";

export const WS_TIMELINE = "almanac/timeline";
export const WS_DRY_RUN = "almanac/dry_run";
export const WS_RESOLVERS = "almanac/resolvers";
export const WS_SCHEDULE_LIST = "almanac/schedule/list";
export const WS_SCHEDULE_CREATE = "almanac/schedule/create";
export const WS_SCHEDULE_UPDATE = "almanac/schedule/update";
export const WS_SCHEDULE_DELETE = "almanac/schedule/delete";

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

/**
 * A write refused because the session is not an admin.
 *
 * The collection's mutating commands are unconditionally `require_admin` and
 * `/list` is not, so this is reachable from a panel that rendered perfectly.
 * D141 says the editor should know before the user starts typing; this is for
 * the case where it did not — an account demoted mid-session, say.
 */
export const isUnauthorized = (error: unknown): boolean =>
  isWsError(error) && error.code === "unauthorized";

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

/**
 * D16's pick-list, as the editor's anchor picker renders it (D145).
 *
 * No `at`, for the same reason `fetchSchedules` has none: which offerings exist
 * is not a function of when you ask, and each one's horizon is *declared* rather
 * than computed. Worth fetching once per editor session and keeping — a resolver
 * appearing or disappearing means a restart, which ends the session anyway.
 */
export const fetchResolvers = (
  hass: HomeAssistant,
): Promise<WireResolverCatalogue> =>
  hass.connection.sendMessagePromise<WireResolverCatalogue>({
    type: WS_RESOLVERS,
  });

// --- the three writes (D139-D141) ------------------------------------------
//
// All three are the collection's, not almanac's: `storage.py` hands
// `WS_PREFIX_SCHEDULE` to `DictStorageCollectionWebsocket` and core generates
// them. Two properties of that generated code reach this file.
//
// **They are unconditionally admin-only.** `helpers/collection.py` wraps each of
// create, update and delete in `require_admin` with no opt-out — the class
// docstring says so in as many words — while `/list` and `/subscribe` are open.
// So a non-admin can open the panel, read every schedule and render every track,
// and will be refused the moment they save. D141 is that the editor finds this
// out from `hass.user.is_admin` and says so up front rather than discovering it
// from an `unauthorized` error after the user has done the work.
//
// **Update is a shallow merge.** `_update_data` in `storage.py` is `item |
// update`, and `UPDATE_FIELDS` carries no defaults, so a field the message omits
// keeps its stored value. That is what makes D140 — send only what changed —
// correct rather than merely economical: a bundle that predates a schema
// addition cannot clobber the field it does not know about.

// The two write shapes themselves are in `draft.ts`, with the edit algebra that
// produces them: `toCreate` and `toUpdate` are the only callers that should be
// building one by hand, and a type defined here would have the dependency the
// wrong way round — `draft.ts` imports nothing at run time (D132) and this file
// imports `./ha`.

/** Create one schedule. Resolves with the stored item, `id` and all. */
export const createSchedule = (
  hass: HomeAssistant,
  schedule: ScheduleCreate,
): Promise<StoredSchedule> =>
  hass.connection.sendMessagePromise<StoredSchedule>({
    type: WS_SCHEDULE_CREATE,
    ...schedule,
  });

/**
 * Apply the fields in `changes` to one schedule. Resolves with the merged item.
 *
 * The id travels as `schedule_id` because that is what core's collection calls
 * it — `item_id_key` is `f"{model_name}_id"` and `storage.py` passes
 * `"schedule"` — while the item's own key is `id`. Two names for one value is
 * core's shape, not ours, and this is the one place the frontend has to know it.
 */
export const updateSchedule = (
  hass: HomeAssistant,
  scheduleId: string,
  changes: ScheduleUpdate,
): Promise<StoredSchedule> =>
  hass.connection.sendMessagePromise<StoredSchedule>({
    type: WS_SCHEDULE_UPDATE,
    schedule_id: scheduleId,
    ...changes,
  });

/** Delete one schedule. Resolves with nothing; `not_found` means it was gone. */
export const deleteSchedule = (
  hass: HomeAssistant,
  scheduleId: string,
): Promise<void> =>
  hass.connection.sendMessagePromise<void>({
    type: WS_SCHEDULE_DELETE,
    schedule_id: scheduleId,
  });

/** Midnight local, `days` away from `from`. Window edges, not instants of interest. */
export const dayOffset = (from: Date, days: number): Date => {
  const shifted = new Date(from);
  shifted.setDate(shifted.getDate() + days);
  shifted.setHours(0, 0, 0, 0);
  return shifted;
};
