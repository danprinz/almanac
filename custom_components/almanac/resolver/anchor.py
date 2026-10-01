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
- **The two questions an anchor answers.** `async_forecast_anchor` asks *every
  instant in this window*, which is what a timeline wants;
  `async_resolve_anchor_on_date` asks *the instant for this civil date*, which is
  what D1's recurrence wants, because recurrence selects the date a rule begins
  on and that date belongs to the anchor's source event rather than to the
  instant the offset produces. They are separate functions because the second
  deliberately does **not** filter its answer by the window it computed from.
- **The two instants an offset anchor *is* (D122).** `async_resolve_anchor_on_date`
  returns a `ResolvedAnchor` carrying both the source event and the offset
  instant, because D11's stage two has to be asked about the first and the rule
  fires at the second. Collapsing them is what made the flagship scenario
  unschedulable (§5.8), and the two are kept apart here for the same reason step
  8 pulled `known_through` back off `computed_through`: one value cannot answer
  two questions, and the place to notice that is the type.

What it does not own: pairing a `During` rule's end to its start (D38), the
grace window (D41), and anything that decides whether an occurrence fires. Those
are step 3, and they are built on top of `AnchorForecast`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
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
from .contract import (
    Horizon,
    Span,
    Unresolved,
    UnresolvedReason,
    Window,
    absolute,
)
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
        return absolute(self.known_through) >= absolute(self.window.end)


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
    if isinstance(address := _address(registry, anchor), Unresolved):
        return address
    domain, key, offset = address.domain, address.key, address.offset

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
        # `key=absolute` for A.13's reason: picking the earlier of two instants
        # is the operation that reverses inside the repeated hour.
        known_through=min(
            _shift(forecast.known_through, offset), window.end, key=absolute
        ),
    )


def anchor_horizon(
    registry: ResolverRegistry, anchor: dict[str, Any]
) -> Horizon | Unresolved:
    """The horizon an anchor's source *declared* (D13), without forecasting it.

    §5.5's claim is that the horizon is declared and never inferred, and this is
    where that becomes literal: it is read off the `Offering`, so an anchor's
    honesty about how far it can see costs no computation and cannot be changed
    by what a forecast happened to return. The engine needs it separately from
    the instants because a plan has to report *known through* even for a window
    in which nothing is scheduled.
    """
    if isinstance(address := _address(registry, anchor), Unresolved):
        return address
    offering = registry.async_offering(address.domain, address.key)
    return offering if isinstance(offering, Unresolved) else offering.horizon


async def async_resolve_anchor_on_date(
    registry: ResolverRegistry, anchor: dict[str, Any], day: date
) -> ResolvedAnchor | Unresolved | None:
    """The instants this anchor resolves to *for one civil date*, or `None`.

    This is the shape D1 needs and `async_forecast_anchor` deliberately does not
    provide. Recurrence picks the dates on which a rule **begins**, and the date
    it picks belongs to the anchor's *source event*, not to the instant the
    anchor finally resolves to. So the window here is the civil day itself and
    there is **no containment filter on the result**: an offset is allowed, and
    expected, to carry the answer out of the day it was computed for.

    Filtering on the resolved instant's own date instead would reintroduce the
    bug this project exists to fix. Upstream's `timer.py` clamps an offset to the
    day boundary rather than rolling over (A.3), which is why schemes there
    cannot cross midnight; dropping "twenty minutes after sunset" on a northern
    June evening because it lands at 00:10 tomorrow is the same failure wearing
    a filter instead of a clamp.

    `None` is a *known* nothing — the source works and this date has no such
    event, as at a polar midsummer — and is distinct from `Unresolved`, which is
    D17's degraded answer and D42's late arrival.
    """
    if isinstance(address := _address(registry, anchor), Unresolved):
        return address

    zone = registry.zone
    forecast = await registry.async_forecast(
        address.domain, address.key, _civil_day(day, zone)
    )
    if isinstance(forecast, Unresolved):
        return forecast
    if not forecast.spans:
        return None

    # The registry returns spans sorted, so the first is the earliest. A resolver
    # offering more than one event of the same kind on one civil day is not a
    # shape any offering in this design has — a date names at most one sunset and
    # at most one candle lighting — so taking the earliest is a statement about
    # the data model rather than a tie-break.
    source = _edge(forecast.spans[0], anchor, zone)
    return ResolvedAnchor(at=_shift(source, address.offset), source=source)


@dataclass(frozen=True, slots=True)
class ResolvedAnchor:
    """One anchor, on one civil date, before and after its offset (D122).

    `source` is the instant the anchor's own event happens — candle lighting at
    18:18. `at` is where the rule starts once D7's offset has been applied —
    17:33, forty-five minutes earlier. For a zero offset they are equal, which is
    every anchor in the first six build steps and is why this was one value for
    as long as it was.

    They are separate because D11 asks two different questions of them. "Is this
    on Shabbat" is a question about `source`, since the day set is what the
    anchor's *event* belongs to; "when do the lights come on" is `at`. Asking the
    set about `at` is what §5.8 measured: a setup window moves `at` to before the
    day begins, the membership test fails, and the occurrence is dropped as
    outside a set it was never outside.
    """

    at: datetime
    source: datetime


@dataclass(frozen=True, slots=True)
class _Address:
    """Where an anchor points, once D6's three storage shapes are behind it."""

    domain: str
    key: str
    offset: timedelta


def _address(
    registry: ResolverRegistry, anchor: dict[str, Any]
) -> _Address | Unresolved:
    """Turn a stored anchor into a resolver address. The whole of D6's collapse.

    One function rather than one per caller: this is the only place where the
    union in storage becomes the single interface below it, and a second copy of
    the dispatch would be the first place the timeline and the engine could
    disagree about what an anchor means.
    """
    kind = anchor.get(CONF_KIND)
    offset = timedelta(seconds=int(anchor.get(CONF_OFFSET, 0)))

    if kind == ANCHOR_CLOCK:
        # A clock anchor carries no offset field: the user types a different
        # time instead, so there is nothing for an offset to express (schema
        # `_CLOCK_ANCHOR_SCHEMA`).
        return _Address(RESOLVER_CLOCK, anchor[CONF_AT], timedelta())
    if kind == ANCHOR_ENTITY_TIME:
        return _Address(RESOLVER_ENTITY_TIME, anchor["entity_id"], offset)
    if kind == ANCHOR_RESOLVER:
        domain, key = anchor[CONF_DOMAIN], anchor[CONF_KEY]
        if isinstance(
            selectable := registry.async_resolve_selectable(domain, key), Unresolved
        ):
            return selectable
        return _Address(domain, key, offset)
    return Unresolved(UnresolvedReason.ERROR, f"{kind!r} is not an anchor kind (D6)")


def _civil_day(day: date, zone: tzinfo) -> Window:
    """One local civil day as a half-open window of instants.

    Built the same way `Span.bounds` builds an all-day span's edges, so the two
    cannot disagree about where a day starts. Both ends are civil midnights
    rather than `start + 24h`, which is what makes a 23- or 25-hour DST day one
    whole day here instead of one day and an hour of the next.
    """
    return Window(
        datetime.combine(day, time(), tzinfo=zone),
        datetime.combine(day + timedelta(days=1), time(), tzinfo=zone),
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


__all__ = [
    "ResolvedAnchor",
    "AnchorForecast",
    "anchor_horizon",
    "async_forecast_anchor",
    "async_resolve_anchor_on_date",
]
