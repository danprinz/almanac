"""The resolver layer — build step 2 of DESIGN.md §15.

§5's contract, the registry §5.6 keeps internal for now, the anchor layer that
puts D6's three storage shapes behind it, and the three resolvers that ship in
this step: `clock`, `entity_time` and `sun`.

`hdate` is step 7 and is not here. §15 places it there deliberately — a contract
is not proven by the implementation it was designed around, and `hdate` is the
one that exercises D10's date/datetime split and D11's two-stage day set, which
none of the three below touch. If the abstraction is wrong, that is where it
shows, and it is cheaper to find out before any UI depends on the offering list.

Everything exported here obeys D64: no clock is read, `now` never appears as an
ambient value, and a `Window` is a parameter of every question.
"""

from __future__ import annotations

from .anchor import AnchorForecast, async_forecast_anchor
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
)
from .entity_time import EntityTimeResolver
from .registry import ResolverRegistry, async_create_registry
from .sun import SunResolver

__all__ = [
    "AnchorForecast",
    "BaseResolver",
    "ClockResolver",
    "EntityTimeResolver",
    "Forecast",
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
    "async_create_registry",
    "async_forecast_anchor",
]
