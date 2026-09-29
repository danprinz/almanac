"""Anchors, resolved: D6's union of three storage shapes behind one code path.

D6 keeps `clock`, `entity_time` and `resolver` distinct in storage and in the
editor, because the config shapes genuinely differ — a literal, an entity
reference, and a key drawn from a fixed list. This module is the other half of
that decision: below here the difference is gone, and the engine asks one
question of all three.

What the module owns, and why each part is here rather than in step 3:

- **The offset (D7).** Applied as *absolute* arithmetic, never wall-clock. Adding
  a `timedelta` to a zone-aware datetime in Python moves the wall time and keeps
  the offset, so "sunset minus twenty minutes" across a DST boundary would come
  out an hour wrong exactly twice a year — the class of bug D40 exists to name.
- **The edge (D6).** An anchor takes one edge of one span (§5.1). For the
  zero-length spans every in-tree resolver produces this is a no-op; it starts
  mattering with `hdate`, where `edge: end` is havdalah.
- **The window padding.** An anchor's offset shifts the instant, so the *source*
  window has to be shifted the other way before forecasting or the occurrence at
  the edge is lost.

What it does not own: pairing a `During` rule's end to its start (D38), the
grace window (D41), and anything that decides whether an occurrence fires. Those
are step 3, and they are built on top of `AnchorForecast`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from typing import Any

from homeassistant.util import dt as dt_util

from ..const import (
    ANCHOR_CLOCK,
    ANCHOR_ENTITY_TIME,
    ANCHOR_RESOLVER,
    CONF_AT,
    CONF_DOMAIN,
    CONF_EDGE,
    CONF_KEY,
    CONF_KIND,
    CONF_OFFSET,
    EDGE_END,
    RESOLVER_CLOCK,
    RESOLVER_ENTITY_TIME,
)
from .contract import Horizon, Span, Unresolved, UnresolvedReason, Window
from .registry import ResolverRegistry


@dataclass(frozen=True, slots=True)
class AnchorForecast:
    """Every instant an anchor resolves to inside a window, plus the horizon.

    `known_through` is carried up from the underlying forecast and shifted by
    the same offset as the instants, so it stays an answer about *this anchor*
    rather than about the source behind it. Without that, an anchor of
    "candle lighting minus 45 minutes" would report knowledge up to candle
    lighting and the timeline would render a 45-minute sliver of false unknown.
    """

    window: Window
    instants: tuple[datetime, ...]
    horizon: Horizon
    known_through: datetime

    @property
    def fully_known(self) -> bool:
        """Whether the declared horizon covers the whole window asked for.

        The three states §5.5 requires the timeline to render fall out of this
        plus `known_through`: everything up to it is **known**, everything past
        it is **unknown**, and *estimated* is what a v2 source with an `UNTIL`
        horizon gets to claim in between.
        """
        return self.known_through >= self.window.end


async def async_forecast_anchor(
    registry: ResolverRegistry, anchor: dict[str, Any], window: Window
) -> AnchorForecast | Unresolved:
    """Resolve a stored anchor over a window.

    Returns every occurrence in ascending order — an anchor is not a single
    instant, and pretending otherwise is what forces today's scheduler to treat
    "today" as a special case. `Unresolved` is a value here for D17's reason:
    one broken anchor is one occurrence rendered as unresolved (D12), not a
    stalled engine.
    """
    kind = anchor.get(CONF_KIND)
    offset = timedelta(seconds=int(anchor.get(CONF_OFFSET, 0)))

    if kind == ANCHOR_CLOCK:
        # A clock anchor carries no offset field: the user types a different
        # time instead, so there is nothing for an offset to express (schema
        # `_CLOCK_ANCHOR_SCHEMA`).
        domain, key, offset = RESOLVER_CLOCK, anchor[CONF_AT], timedelta()
    elif kind == ANCHOR_ENTITY_TIME:
        domain, key = RESOLVER_ENTITY_TIME, anchor["entity_id"]
    elif kind == ANCHOR_RESOLVER:
        domain, key = anchor[CONF_DOMAIN], anchor[CONF_KEY]
        if isinstance(
            selectable := registry.async_resolve_selectable(domain, key), Unresolved
        ):
            return selectable
    else:
        return Unresolved(
            UnresolvedReason.ERROR, f"{kind!r} is not an anchor kind (D6)"
        )

    # An instant `s + offset` lands in `window` exactly when `s` lands in the
    # window shifted back by the offset. Forecasting the unpadded window and
    # then shifting would drop the occurrence whose source sits just past the
    # edge — which, for a negative offset, is every occurrence on the window's
    # last day.
    source_window = window.shifted(-offset)
    forecast = await registry.async_forecast(domain, key, source_window)
    if isinstance(forecast, Unresolved):
        return forecast

    zone = registry.zone
    instants = tuple(
        shifted
        for span in forecast.spans
        if window.contains(shifted := _shift(_edge(span, anchor, zone), offset))
    )
    return AnchorForecast(
        window=window,
        instants=instants,
        horizon=forecast.horizon,
        known_through=min(_shift(forecast.known_through, offset), window.end),
    )


def _edge(span: Span, anchor: dict[str, Any], zone: tzinfo) -> datetime:
    """The edge of the span this anchor takes (§5.1, D6).

    Only a `kind: resolver` anchor has an `edge` field, and only because a
    resolver offering can be an interval: `hdate`'s Shabbat has candle lighting
    at one end and havdalah at the other, and both are anchors of the same
    offering. Every span this build can produce is zero-length, so both edges
    agree — the branch is here so step 7 is an implementation rather than a
    change to the contract.
    """
    low, high = span.bounds(zone)
    return high if anchor.get(CONF_EDGE) == EDGE_END else low


def _shift(instant: datetime, offset: timedelta) -> datetime:
    """Move an instant by an offset, absolutely.

    Via UTC deliberately. `aware + timedelta` is wall-clock arithmetic in
    Python: it adds to the naive part and keeps the zone, so across a spring
    forward "sunset minus 20 minutes" would silently become "sunset minus 80".
    D40 says resolver and `entity_time` anchors are absolute instants, and this
    is the one line where that is either true or not.
    """
    return (dt_util.as_utc(instant) + offset).astimezone(instant.tzinfo)


__all__ = ["AnchorForecast", "async_forecast_anchor"]
