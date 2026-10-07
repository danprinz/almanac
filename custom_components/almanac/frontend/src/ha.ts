// The parts of Home Assistant's frontend that almanac actually touches.
//
// Hand-written rather than taken from `custom-card-helpers`, for the reason
// CLAUDE.md gives for everything else in this project: a third-party mirror of
// someone else's types is a claim about upstream that nobody is checking. These
// declarations are narrow enough to be read against the real thing, and each one
// that is not obvious says where it comes from.
//
// Nothing here is a runtime import. Keeping it types-only is what lets the card
// stub satisfy D70 — a stub that imported a helper module would already have an
// import graph to unpick.

/** `hass.states[entity_id]`. */
export interface HassEntity {
  entity_id: string;
  state: string;
  attributes: Record<string, unknown>;
  last_changed: string;
  last_updated: string;
}

/**
 * `hass.connection` — the websocket. `sendMessagePromise` resolves with the
 * handler's `result` and rejects with `{code, message}` on `send_error`, which
 * is how `websocket.py`'s `ERR_NOT_FOUND` and `ERR_INVALID_FORMAT` arrive.
 */
export interface HassConnection {
  sendMessagePromise<T>(message: Record<string, unknown>): Promise<T>;
  subscribeEvents<T>(
    callback: (event: T) => void,
    eventType: string,
  ): Promise<() => Promise<void>>;
}

export interface HassUser {
  id: string;
  name: string;
  is_admin: boolean;
}

/** One entry of a service's `fields`. A `fields` sub-object makes it a section. */
export interface HassServiceField {
  name?: string;
  description?: string;
  required?: boolean;
  selector?: Record<string, unknown>;
  example?: unknown;
  fields?: Record<string, HassServiceField>;
}

/**
 * `hass.services[domain][service]`. Absent means unknown *or unloaded* -- D17 says
 * a schedule may name a service whose integration is not loaded, so absence is
 * never an error. Script fields live under `services.script[<object_id>]`.
 */
export interface HassService {
  name?: string;
  description?: string;
  fields: Record<string, HassServiceField>;
  target?: unknown;
}

export interface HomeAssistant {
  states: Record<string, HassEntity | undefined>;
  services: Record<string, Record<string, HassService>>;
  connection: HassConnection;
  /** IANA name. `config.time_zone` is the instance's zone, not the browser's. */
  config: { time_zone: string };
  user?: HassUser;
  language: string;
  locale?: { language: string; time_zone?: string };
  localize(key: string, ...args: unknown[]): string;
  callService(
    domain: string,
    service: string,
    data?: Record<string, unknown>,
  ): Promise<unknown>;
}

/** What a Lovelace card element has to be. */
export interface LovelaceCardConfig {
  type: string;
  [key: string]: unknown;
}

export interface LovelaceCard extends HTMLElement {
  hass?: HomeAssistant | undefined;
  setConfig(config: LovelaceCardConfig): void;
  getCardSize?(): number | Promise<number>;
}

/**
 * The picker's registry. A plain array on `window`, created by whichever custom
 * card loads first — hence the `??=` at every registration site rather than an
 * assignment.
 */
export interface CustomCardEntry {
  type: string;
  name: string;
  description: string;
  preview?: boolean;
  documentationURL?: string;
}

declare global {
  interface Window {
    customCards?: CustomCardEntry[];
  }
}
