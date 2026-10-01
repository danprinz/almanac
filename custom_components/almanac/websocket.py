"""The three reads the frontend is built on: D63's timeline, D64's dry run, D145's
catalogue.

Registered beside the CRUD commands `helpers/collection.py` gives us for free
(D33). Those are writes over a storage collection and core owns their shape; these
are queries over the engine, and there is nothing in core to inherit from.

**Every instant is a field of the message.** Not a convenience — a constraint.
D64 gives this package exactly one clock reader, `tick.py`, and
`tests/test_design_constraints.py` enforces that by an AST sweep whose allow-list
names five functions there and may not grow. A handler that sampled `dt_util.now()`
to fill in a default would break the sweep, and would break it for the right
reason: the moment the backend has a second opinion about what time it is, the
timeline and the dry run are no longer "the same code path at three instants",
which is the whole of what D64 buys.

That leaves the frontend to say what it means, and it is the component that
actually knows: the browser's clock is the one the user is reading the screen by,
and the window it wants is a property of the scroll position, not of the server.
Core's own read APIs agree — `components/history/websocket_api.py` and
`components/logbook/websocket_api.py` both take `start_time`/`end_time` from the
message and never sample a clock.

*The alternatives rejected, because this is the decision most likely to be
questioned:* (a) adding a sampler to the sweep's allow-list — that list exists to
be the definition of D64, and a read surface is the least defensible place to
widen it; (b) calling `tick.async_refresh()` and reading the instant back off the
engine state — opening a timeline would then *fire schedules*, which is a
spectacular thing for a read to do; (c) caching the last `now` the tick was handed
— no clock read, but it is stale by up to a whole idle wake (a day, D-`_IDLE_WAKE`),
so an occurrence from ten minutes ago would render in the future half.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv

from .const import (
    ATTR_AT,
    ATTR_SCHEDULE_ID,
    DOMAIN,
    WS_DRY_RUN,
    WS_RESOLVERS,
    WS_TIMELINE,
)
from .resolver.contract import Offering, Window, absolute
from .resolver.registry import ResolverRegistry
from .storage import AlmanacData
from .timeline import async_timeline

_FIELD_START: Final = "start"
_FIELD_END: Final = "end"
_FIELD_SCHEDULE_IDS: Final = "schedule_ids"
_FIELD_LIVE: Final = "live"


@callback
def async_register_websocket(hass: HomeAssistant) -> None:
    """Register the query commands. Called once, from `async_setup_entry`."""
    websocket_api.async_register_command(hass, websocket_timeline)
    websocket_api.async_register_command(hass, websocket_dry_run)
    websocket_api.async_register_command(hass, websocket_resolvers)


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_TIMELINE,
        vol.Required(_FIELD_START): cv.string,
        vol.Required(_FIELD_END): cv.string,
        vol.Required(ATTR_AT): cv.string,
        vol.Optional(_FIELD_SCHEDULE_IDS): [cv.string],
    }
)
@websocket_api.async_response
async def websocket_timeline(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """D63's view over `[start, end)`, divided at `at`.

    Not admin-only. It is a read of information the schedule list already exposes
    to any authenticated user, and core treats its own history and logbook queries
    the same way. `dry_run` below is the one that is restricted, and for a reason
    that does not apply here.
    """
    data = _async_data(hass, connection, msg)
    if data is None:
        return
    window = _async_window(connection, msg, data)
    if window is None:
        return
    at = _async_instant(connection, msg, data, ATTR_AT)
    if at is None:
        return

    timeline = await async_timeline(
        hass,
        data,
        window,
        at=at,
        schedule_ids=msg.get(_FIELD_SCHEDULE_IDS),
    )
    connection.send_result(msg["id"], timeline.as_dict())


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_DRY_RUN,
        vol.Required(ATTR_AT): cv.string,
        vol.Optional(_FIELD_SCHEDULE_IDS): [cv.string],
        vol.Optional(_FIELD_LIVE, default=True): cv.boolean,
    }
)
@websocket_api.async_response
async def websocket_dry_run(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Evaluate every schedule at a hypothetical instant, changing nothing.

    Admin-only, which the timeline is not, and the difference is deliberate. This
    takes the tick's own lock — it has to, or it would read a runtime record a tick
    was halfway through writing — so a caller can make the live scheduler wait.
    Contention is bounded and brief, but the ability to create it is not something
    to hand to every session on the instance, and a dry run is a maintainer's tool
    in any case.

    `live` is D41's distinction: `true` evaluates as a tick that has been watching,
    `false` as the recovery pass that has not. Defaulting to `true` matches
    `async_tick`.
    """
    data = _async_data(hass, connection, msg)
    if data is None:
        return
    at = _async_instant(connection, msg, data, ATTR_AT)
    if at is None:
        return
    if (tick := data.tick) is None:
        connection.send_error(
            msg["id"], websocket_api.ERR_NOT_FOUND, "the almanac tick is not running"
        )
        return

    results = await tick.async_dry_run(
        at=at,
        schedule_ids=msg.get(_FIELD_SCHEDULE_IDS),
        live=msg[_FIELD_LIVE],
    )
    connection.send_result(
        msg["id"],
        {
            ATTR_AT: at.isoformat(),
            "live": msg[_FIELD_LIVE],
            "schedules": [
                {ATTR_SCHEDULE_ID: schedule_id, **reconciliation.as_dict()}
                for schedule_id, reconciliation in results.items()
            ],
        },
    )


@websocket_api.websocket_command({vol.Required("type"): WS_RESOLVERS})
@callback
def websocket_resolvers(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """D16's pick-list, as the anchor editor needs it (D145).

    Takes no instant, which is the one way this read differs from the other two:
    an offering's existence is not a function of when you ask. Its *horizon* is
    declared rather than computed for the same reason, so nothing here resolves
    anything and nothing here needs a clock.

    Not admin-only, by the same argument as the timeline, only shorter: this says
    which resolvers are installed, which is less than the schedule list already
    tells any authenticated session.
    """
    data = _async_data(hass, connection, msg)
    if data is None:
        return
    connection.send_result(msg["id"], catalogue_payload(data.resolvers))


def offering_payload(domain: str, offering: Offering) -> dict[str, Any]:
    """One row of the pick-list.

    `roles` is sent verbatim and the editor reads it, rather than the backend
    sending a derived `has_end_edge` boolean. The editor needs to know whether to
    offer D124's `edge` choice at all, and `Role.DAY_SET in roles` is the answer:
    §5.1 defines the day-set role as a predicate over a span's *interior*, so an
    offering with no interior — a zman, a sunset, a candle lighting, all of them
    instants — has nothing to predicate and declares the anchor role alone. An
    offering that declares both has two distinct edges by that definition. Both
    shapes of the same fact on the wire is what D133 refused; this is the half of
    the pair that is declared, so it is the half that travels.

    `horizon_through` is `null` for every resolver shipped today — only an `UNTIL`
    horizon has a bound, and nothing registers one yet (v2's calendars will). It
    is on the wire because it is a real field of a real declaration, and a shape
    that omitted it would let a reader believe the horizon is a single word.
    """
    return {
        "domain": domain,
        "key": offering.key,
        "display_name": offering.display_name,
        "roles": sorted(role.value for role in offering.roles),
        "horizon": offering.horizon.kind.value,
        "horizon_through": (
            None
            if offering.horizon.bound is None
            else offering.horizon.bound.isoformat()
        ),
    }


def catalogue_payload(registry: ResolverRegistry) -> dict[str, Any]:
    """The whole pick-list, flat, plus the domains that do not have one.

    **Flat rather than grouped by domain.** `async_offerings()` returns a mapping
    because that is how the registry stores it, but the editor renders one
    searchable list — D16's whole point is that you find "havdalah" by typing it,
    not by knowing which integration computes it — and every row carries its own
    `domain`, so grouping stays available to the frontend without the wire form
    having to be un-nested first.

    **`parametric` is the complement, and it is a positive statement.** A `clock`
    anchor is typed and an `entity_time` anchor is picked off the entity list, so
    neither has anything to list; the editor still has to know those domains are
    *there*, and D6 gives each its own anchor kind. Computing it as
    `async_domains()` minus the offering keys means a resolver that is registered
    and lists nothing can never be invisible in both halves.
    """
    offerings = registry.async_offerings()
    return {
        "offerings": [
            offering_payload(domain, offering)
            for domain, rows in offerings.items()
            for offering in rows
        ],
        "parametric": [
            domain for domain in registry.async_domains() if domain not in offerings
        ],
    }


@callback
def _async_data(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> AlmanacData | None:
    """The one config entry's runtime data, or an error on the connection.

    Looked up per call rather than captured at registration. D65 means there is
    exactly one entry, but it can be reloaded, and a handler holding the data of an
    entry that has since been unloaded would answer from a collection nothing is
    maintaining any more.
    """
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry.runtime_data
    connection.send_error(
        msg["id"], websocket_api.ERR_NOT_FOUND, "almanac is not loaded"
    )
    return None


@callback
def _async_instant(
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
    data: AlmanacData,
    field: str,
) -> datetime | None:
    """Read one ISO instant out of the message, in the engine's own zone.

    Two conversions, both load-bearing.

    **Naive is refused, not assumed.** A string with no offset does not name an
    instant, and the only way to turn it into one is to pick a zone on the caller's
    behalf — which is a guess about what time it is, made in the layer D64 spent a
    whole decision keeping clocks out of. The frontend has the offset; `Date`
    serialises it by default.

    **Aware is converted to the resolver registry's zone.** `fromisoformat` on
    `…+01:00` yields a fixed-offset `timezone`, and a fixed offset is not a time
    zone: `plan.py` adds D44's budget in *civil* days on purpose, so that a
    timeline stops at a date boundary rather than an hour short of one, and civil
    arithmetic on a fixed offset silently ignores the DST transition in between.
    `astimezone` is a pure conversion and reads no clock.
    """
    raw = msg[field]
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        connection.send_error(
            msg["id"],
            websocket_api.ERR_INVALID_FORMAT,
            f"{field!r} is not an ISO 8601 instant",
        )
        return None
    if parsed.tzinfo is None:
        connection.send_error(
            msg["id"],
            websocket_api.ERR_INVALID_FORMAT,
            f"{field!r} needs a UTC offset; almanac will not guess one",
        )
        return None
    return parsed.astimezone(data.resolvers.zone)


@callback
def _async_window(
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
    data: AlmanacData,
) -> Window | None:
    """Build the requested window, or send the reason it cannot be built.

    `Window.__post_init__` already refuses one that does not move forward; the
    check is repeated as a message the frontend can show rather than a traceback in
    the log, because an empty or inverted range is an ordinary thing for a
    date-picker to emit while a user is halfway through choosing.
    """
    start = _async_instant(connection, msg, data, _FIELD_START)
    if start is None:
        return None
    end = _async_instant(connection, msg, data, _FIELD_END)
    if end is None:
        return None
    if absolute(end) <= absolute(start):
        connection.send_error(
            msg["id"],
            websocket_api.ERR_INVALID_FORMAT,
            "the timeline window has to move forward",
        )
        return None
    return Window(start, end)


__all__ = [
    "async_register_websocket",
    "catalogue_payload",
    "offering_payload",
    "websocket_dry_run",
    "websocket_resolvers",
    "websocket_timeline",
]
