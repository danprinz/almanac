"""The `sun` resolver — the first real implementation of §5, and only that.

§1 puts it plainly: sun is not a special case, it is the first implementation.
Nothing in this module is reachable except through the contract, which is what
makes step 7's `hdate` a test of the abstraction rather than a second one.

Two things it gets that today's scheduler does not:

- **Six offerings, not two.** `validate_time` upstream whitelists `sunrise` and
  `sunset` only, even though `sun.sun` publishes dawn, dusk and noon as well
  (A.3). The gap is wider than the brief recorded, and closing it costs one
  tuple.
- **An UNBOUNDED horizon.** `helpers/sun.py::get_astral_event_date(hass, event,
  date)` computes any date locally from latitude and longitude — nothing is
  fetched and nothing is cached from a sensor (A.2, re-verified against the
  installed 2026.9.4 package). That is what earns the declaration, and it is why
  a sun anchor and `sensor.sun_next_setting` are different kinds (D6) despite
  describing the same physical event.

*Verified in the installed package rather than assumed:* the helper takes
`(hass, event, date)` and dispatches with `getattr(astral.sun, event)`, so the
offering keys below have to be astral's own function names; it reads the clock
**only** when `date is None`, which is why every call here passes a date (D64).
"""

from __future__ import annotations

from typing import Final

from homeassistant.helpers import sun as sun_helper

from ..const import (
    RESOLVER_SUN,
    SUN_DAWN,
    SUN_DUSK,
    SUN_MIDNIGHT,
    SUN_NOON,
    SUN_SUNRISE,
    SUN_SUNSET,
)
from .contract import (
    BaseResolver,
    Horizon,
    InvalidationKind,
    InvalidationSignal,
    Offering,
    Role,
    Span,
    Window,
)

# D8 — addressed by machine key, displayed by name, and the two are separate
# strings so that translating the second cannot break a stored schedule.
_DISPLAY_NAMES: Final[dict[str, str]] = {
    SUN_DAWN: "Dawn",
    SUN_SUNRISE: "Sunrise",
    SUN_NOON: "Solar noon",
    SUN_SUNSET: "Sunset",
    SUN_DUSK: "Dusk",
    SUN_MIDNIGHT: "Solar midnight",
}


class SunResolver(BaseResolver):
    """Solar events, computed locally for any date."""

    domain = RESOLVER_SUN

    def offerings(self) -> list[Offering]:
        """D16's fixed, enumerable pick-list — the whole of what `sun` offers."""
        return [self._offering(key) for key in _DISPLAY_NAMES]

    def offering(self, key: str) -> Offering | None:
        """One offering, or `None` for a key this resolver does not know.

        The membership check is load-bearing beyond validation: the helper
        dispatches with `getattr(astral.sun, event)`, so an unchecked key would
        reach for an arbitrary attribute of a third-party module. D16's fixed
        set is a safety property here as well as a UI one.
        """
        if key not in _DISPLAY_NAMES:
            return None
        return self._offering(key)

    async def forecast(self, key: str, window: Window) -> list[Span]:
        """The event, once per civil day the window touches.

        A day on which the event does not occur — the sun never rises, or never
        sets — yields nothing, and that is a *known* nothing: the horizon is
        UNBOUNDED, so the caller can tell it apart from a source that ran out of
        answers. §5.5 exists for exactly this distinction.
        """
        zone = self.zone
        spans: list[Span] = []
        for day in window.days(zone):
            # The date is always passed. Omitting it makes the helper call
            # `dt_util.now()` internally, which would put a clock underneath the
            # dry run and the timeline (D64) — the one thing this layer must not
            # do, and the reason this line has a comment on it.
            event = sun_helper.get_astral_event_date(self.hass, key, day)
            if event is None:
                continue
            instant = event.astimezone(zone)
            if window.contains(instant):
                spans.append(Span(instant, instant))
        return spans

    def invalidation(self, key: str) -> InvalidationSignal:
        """Deterministic (D43): only a location or timezone change moves these.

        Which is why they may be cached aggressively, and why the timeline can
        render a year of sunsets without a single state subscription.
        """
        return InvalidationSignal(InvalidationKind.DETERMINISTIC)

    def _offering(self, key: str) -> Offering:
        """Build one offering. Anchor-only: a solar event is an instant.

        Deliberately no day-set role. "Between sunset and sunrise" is a `During`
        rule with two anchors (D2, D38), and D10's date/datetime split plus
        D11's two stages are first exercised by `hdate` in step 7 — which is the
        point of putting `hdate` there rather than here.
        """
        return Offering(
            key=key,
            display_name=_DISPLAY_NAMES[key],
            roles=frozenset({Role.ANCHOR}),
            horizon=Horizon.unbounded(),
        )
