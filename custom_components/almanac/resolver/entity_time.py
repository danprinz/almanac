"""The `entity_time` resolver — the feature upstream refused (A.8).

Parametric, like `clock`: the key is an entity_id. What makes it worth its own
module is that it is the only in-tree resolver that is *observational*, so it is
where three decisions meet and can be tested against each other.

- **D13 / §5.5 — NEXT_ONLY.** A `device_class: timestamp` sensor holds exactly
  one value, the next occurrence (A.2). Beyond it the source knows nothing, and
  the timeline has to say *unknown* rather than projecting the value forward.
  This is the asymmetry that stops `entity_time` being folded into the `sun`
  resolver: `sensor.sun_next_setting` and `sun`/`sunset` describe the same
  physical event with different horizons.
- **D42 — unavailable is skipped and logged, not failed.** An anchor entity that
  is `unknown` for ninety seconds after a restart is the normal case, so
  `forecast` raises `OfferingUnavailable` (which the registry turns into
  `Unresolved`) rather than returning an empty list. An empty list would be a
  positive claim that nothing is scheduled, and §10.4's late-arrival handling
  depends on telling those apart.
- **D43 — the subscription is the invalidation signal.** The entity is named
  whether or not it currently has a value, which is what "the subscription is
  kept live" means in practice.
"""

from __future__ import annotations

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import valid_entity_id
from homeassistant.util import dt as dt_util

from ..const import RESOLVER_ENTITY_TIME
from .contract import (
    BaseResolver,
    Horizon,
    InvalidationKind,
    InvalidationSignal,
    Offering,
    OfferingUnavailable,
    Role,
    Span,
    Window,
)


class EntityTimeResolver(BaseResolver):
    """An entity whose state is a timestamp, used as a time anchor."""

    domain = RESOLVER_ENTITY_TIME

    def offering(self, key: str) -> Offering | None:
        """Describe an entity-shaped key.

        Only the shape is checked. Whether the entity exists, and whether it
        holds a timestamp, is a question about *now* and therefore an answer
        that changes — which makes it `forecast`'s business (and D42's), not a
        validity rule that could quietly delete a stored anchor.
        """
        if not valid_entity_id(key):
            return None
        return Offering(
            key=key,
            display_name=key,
            roles=frozenset({Role.ANCHOR}),
            horizon=Horizon.next_only(),
        )

    async def forecast(self, key: str, window: Window) -> list[Span]:
        """The single instant the entity holds, if it falls in the window."""
        state = self.hass.states.get(key)
        if state is None:
            raise OfferingUnavailable(f"{key} does not exist")
        if state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            raise OfferingUnavailable(f"{key} is {state.state}")

        instant = dt_util.parse_datetime(state.state)
        if instant is None:
            raise OfferingUnavailable(
                f"{key} holds {state.state!r}, which is not a timestamp"
            )
        if instant.tzinfo is None:
            # `input_datetime` stores a naive civil datetime. Reading it as
            # local wall time is the only reading that matches what the user
            # typed into it; D40 says the same about clock anchors.
            instant = instant.replace(tzinfo=self.zone)

        # Outside the window is a real answer — the source is working and has
        # nothing in range — and it is deliberately *not* an
        # `OfferingUnavailable`. NEXT_ONLY then puts `known_through` at the
        # window start, so the timeline says *unknown* rather than *nothing*.
        if not window.contains(instant):
            return []
        return [Span(instant, instant)]

    def invalidation(self, key: str) -> InvalidationSignal:
        """Observational: recompute whenever the entity's state changes (D43)."""
        return InvalidationSignal(
            InvalidationKind.OBSERVATIONAL, entity_ids=frozenset({key})
        )
