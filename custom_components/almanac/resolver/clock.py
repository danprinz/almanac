"""The `clock` resolver — a wall-clock time, and D40's two DST rules.

Parametric: the key *is* the time (`"17:00:00"`), so there is no pick-list and
D16 does not apply. It is a resolver anyway, rather than a special case in the
engine, because D6's whole claim is that the three anchor kinds differ in
storage and in the editor and nowhere else. A clock anchor that took a different
code path to a sun anchor would be the first place the timeline and the engine
could disagree.

D40 is the substance:

- a **nonexistent** local time — inside a spring-forward gap — is skipped and
  logged;
- an **ambiguous** local time — inside a fall-back repeat — fires on its **first**
  occurrence.

Both are stated rather than inherited because the second one is the failure this
product is a response to. "The 02:30 schedule fired twice" is visible to a room,
and a rule nobody wrote down gets decided by accident in whichever branch ships
first.
"""

from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.util import dt as dt_util

from ..const import RESOLVER_CLOCK
from .contract import (
    BaseResolver,
    Horizon,
    InvalidationKind,
    InvalidationSignal,
    Offering,
    Role,
    Span,
    Window,
    datetime_ambiguous,
    datetime_exists,
)

_LOGGER = logging.getLogger(__name__)


class ClockResolver(BaseResolver):
    """Local wall-clock times, one per civil day in the window."""

    domain = RESOLVER_CLOCK

    def offering(self, key: str) -> Offering | None:
        """Describe a time-shaped key.

        The key is normalised by the schema on the way into storage (`_clock_time`
        returns `HH:MM:SS`), so this is a check on what arrives from an older
        store or from a caller that built an anchor by hand, not a second
        authoring surface.
        """
        if (parsed := dt_util.parse_time(key)) is None:
            return None
        return Offering(
            key=key,
            display_name=parsed.isoformat(timespec="minutes"),
            # Anchor only. "Every day between 09:00 and 17:00" is a `During`
            # rule with two clock anchors (D2), not a day set — routing it
            # through a day set would make the engine build the interval twice.
            roles=frozenset({Role.ANCHOR}),
            horizon=Horizon.unbounded(),
        )

    async def forecast(self, key: str, window: Window) -> list[Span]:
        """One zero-length span per civil day on which the wall time exists."""
        wall = dt_util.parse_time(key)
        if wall is None:
            return []
        zone = self.zone

        spans: list[Span] = []
        for day in window.days(zone):
            # `fold=0` is the default, and it is D40's "first occurrence" rule:
            # on a fall-back day the earlier of the two 01:30s is the one that
            # fires, and the later one is not a second occurrence.
            candidate = datetime.combine(day, wall, tzinfo=zone)
            if not datetime_exists(candidate):
                _LOGGER.warning(
                    "Skipping %s on %s: the clock jumps over it at the DST "
                    "transition, so there is no such local time (D40)",
                    key,
                    day,
                )
                continue
            if datetime_ambiguous(candidate):
                _LOGGER.debug(
                    "%s happens twice on %s; firing on the first (D40)", key, day
                )
            if window.contains(candidate):
                spans.append(Span(candidate, candidate))
        return spans

    def invalidation(self, key: str) -> InvalidationSignal:
        """Deterministic: a wall time moves only if the civil timezone does.

        Which is a core config change, and is exactly what the registry
        subscribes a deterministic signal to.
        """
        return InvalidationSignal(InvalidationKind.DETERMINISTIC)
