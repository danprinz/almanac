"""The two reads step 9's panel is built on: D63's timeline and D64's dry run.

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

from .const import ATTR_AT, ATTR_SCHEDULE_ID, DOMAIN, WS_DRY_RUN, WS_TIMELINE
from .resolver.contract import Window, absolute
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


__all__ = ["async_register_websocket", "websocket_dry_run", "websocket_timeline"]
