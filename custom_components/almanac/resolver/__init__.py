"""The resolver layer — build step 2 of DESIGN.md §15.

§5's contract, the registry §5.6 keeps internal for now, the anchor layer that
puts D6's three storage shapes behind it, and the three resolvers that ship in
this step: `clock`, `entity_time` and `sun`.

`hdate` joined them at step 7. §15 placed it there deliberately — a contract is
not proven by the implementation it was designed around, and `hdate` is the one
that exercises D10's date/datetime split and D11's two-stage day set, which none
of the other three touch. It is also the only resolver here with a third-party
requirement (see `manifest.json`), and the only one that returns a span with an
interior. The abstraction held; `Window.days` gained padding, and several
comparisons that had quietly gone wall-clock were fixed where they stood (A.13).

Everything exported here obeys D64: no clock is read, `now` never appears as an
ambient value, and a `Window` is a parameter of every question.
"""

from __future__ import annotations

from .anchor import (
    AnchorForecast,
    anchor_horizon,
    async_forecast_anchor,
    ResolvedAnchor,
    async_resolve_anchor_on_date,
)
from .clock import ClockResolver
from .contract import (
    BaseResolver,
    Forecast,
    Horizon,
    HorizonKind,
    InvalidationKind,
    InvalidationSignal,
    Offering,
    OfferingUnavailable,
    Resolver,
    ResolverError,
    Role,
    Span,
    Unresolved,
    UnresolvedReason,
    Window,
    known_through,
)
from .entity_time import EntityTimeResolver
from .hdate import HDateResolver
from .registry import ResolverRegistry, async_create_registry
from .sun import SunResolver

__all__ = [
    "AnchorForecast",
    "BaseResolver",
    "ClockResolver",
    "EntityTimeResolver",
    "Forecast",
    "HDateResolver",
    "Horizon",
    "HorizonKind",
    "InvalidationKind",
    "InvalidationSignal",
    "Offering",
    "OfferingUnavailable",
    "Resolver",
    "ResolverError",
    "ResolverRegistry",
    "Role",
    "Span",
    "SunResolver",
    "Unresolved",
    "UnresolvedReason",
    "Window",
    "anchor_horizon",
    "async_create_registry",
    "async_forecast_anchor",
    "ResolvedAnchor",
    "async_resolve_anchor_on_date",
    "known_through",
]
