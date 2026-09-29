"""Registration (§5.6) and the isolation D17 puts in the contract.

D14 — the registry is internal for v1, and there is deliberately **no**
discovery hook here. HA's integration-platform mechanism (the one `logbook`
uses, A.4) is the right long-term shape and would eventually let core's
`jewish_calendar` ship its own resolver, but the moment a third party ships one
the interface becomes a compatibility commitment. At 0.x, untested against any
external implementation, that is premature. Step 7's `hdate` is the second
implementation that has to prove the contract first.

D17 — every call out to a resolver goes through `_call` below, so a resolver
that raises, hangs or returns rubbish costs its own occurrence and nothing else.
Putting the timeout here rather than in each implementation is what makes that a
property of the contract rather than of whoever wrote the resolver.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from datetime import date, datetime, tzinfo
from typing import Any

from homeassistant.const import EVENT_CORE_CONFIG_UPDATE
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from ..const import RESOLVER_TIMEOUT
from .contract import (
    Forecast,
    InvalidationKind,
    InvalidationSignal,
    Offering,
    OfferingUnavailable,
    Resolver,
    Role,
    Unresolved,
    UnresolvedReason,
    Window,
    known_through,
)

_LOGGER = logging.getLogger(__name__)


class ResolverRegistry:
    """The one place a domain name is turned into a resolver.

    Every public method returns either a result or `Unresolved`. Nothing here
    raises at a caller, because D17's promise is that a broken resolver degrades
    one anchor rather than stalling the engine — and a promise kept by asking
    every call site to remember a `try` is not kept.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Set up an empty registry."""
        self.hass = hass
        self._resolvers: dict[str, Resolver] = {}

    @property
    def zone(self) -> tzinfo:
        """The civil timezone spans are interpreted in. See `BaseResolver.zone`."""
        return dt_util.get_time_zone(self.hass.config.time_zone) or dt_util.UTC

    @callback
    def async_register(self, resolver: Resolver) -> None:
        """Add a resolver, refusing to shadow a domain that is already claimed.

        D9 makes a domain permanent, so two resolvers answering to one name is
        not a last-one-wins situation — it is a stored schedule whose meaning
        depends on import order.
        """
        if resolver.domain in self._resolvers:
            raise ValueError(f"resolver domain {resolver.domain!r} is already claimed")
        self._resolvers[resolver.domain] = resolver

    @callback
    def async_get(self, domain: str) -> Resolver | None:
        """The resolver claiming `domain`, if any."""
        return self._resolvers.get(domain)

    @callback
    def async_domains(self) -> list[str]:
        """Every registered domain, parametric ones included."""
        return sorted(self._resolvers)

    @callback
    def async_offerings(self) -> dict[str, list[Offering]]:
        """D16's pick-list, by domain — what the anchor editor renders.

        Parametric resolvers are absent because they have nothing to list: a
        clock time is typed and an entity is picked from the entity list, and
        both already have their own anchor kind in the editor (D6).
        """
        return {
            domain: offerings
            for domain, resolver in sorted(self._resolvers.items())
            if (offerings := resolver.offerings())
        }

    @callback
    def async_resolve_selectable(
        self, domain: str, key: str
    ) -> Offering | Unresolved:
        """Check that a stored `kind: resolver` anchor still addresses something.

        The schema validates `domain` and `key` as slugs and deliberately does
        not consult this registry, so that a schedule whose resolver is
        temporarily absent loads and renders as *unresolved* instead of failing
        the whole store. This is where that check actually happens, and it is
        the reason `Unresolved` distinguishes a missing domain from a missing
        key: "the hdate resolver isn't installed" and "this zman no longer
        exists" need different answers from a human.
        """
        resolver = self._resolvers.get(domain)
        if resolver is None:
            return Unresolved(
                UnresolvedReason.UNKNOWN_DOMAIN, f"no resolver for domain {domain!r}"
            )
        if not resolver.offerings():
            return Unresolved(
                UnresolvedReason.NOT_SELECTABLE,
                f"{domain!r} is parametric and has its own anchor kind (D6)",
            )
        if (offering := resolver.offering(key)) is None:
            return Unresolved(
                UnresolvedReason.UNKNOWN_KEY, f"{domain} does not offer {key!r}"
            )
        return offering

    async def async_forecast(
        self, domain: str, key: str, window: Window
    ) -> Forecast | Unresolved:
        """Enumerate a window, with the horizon attached (§5.5).

        The horizon is read from the offering and `known_through` computed here,
        once, rather than by each resolver — so a resolver cannot claim to see
        further than it declared, and a renderer never has to guess.
        """
        resolver = self._resolvers.get(domain)
        if resolver is None:
            return Unresolved(
                UnresolvedReason.UNKNOWN_DOMAIN, f"no resolver for domain {domain!r}"
            )
        if (offering := resolver.offering(key)) is None:
            return Unresolved(
                UnresolvedReason.UNKNOWN_KEY, f"{domain} does not offer {key!r}"
            )

        result = await self._call(domain, key, resolver.forecast(key, window))
        if isinstance(result, Unresolved):
            return result

        zone = self.zone
        spans = tuple(sorted(result, key=lambda span: span.bounds(zone)))
        return Forecast(
            window=window,
            spans=spans,
            horizon=offering.horizon,
            known_through=known_through(window, spans, offering.horizon, zone),
        )

    async def async_candidate_dates(
        self, domain: str, key: str, window: Window
    ) -> list[date] | Unresolved:
        """D11's coarse stage. Refused for an offering with no day-set role."""
        if isinstance(
            checked := self._for_role(domain, key, Role.DAY_SET), Unresolved
        ):
            return checked
        resolver, _ = checked
        return await self._call(
            domain, key, resolver.candidate_dates(key, window)
        )

    async def async_covers(
        self, domain: str, key: str, instant: datetime
    ) -> bool | Unresolved:
        """D11's precise stage. Refused for an offering with no day-set role."""
        if isinstance(
            checked := self._for_role(domain, key, Role.DAY_SET), Unresolved
        ):
            return checked
        resolver, _ = checked
        return await self._call(domain, key, resolver.covers(key, instant))

    @callback
    def async_invalidation(
        self, domain: str, key: str
    ) -> InvalidationSignal | Unresolved:
        """What has to change for this key's answers to change (D43)."""
        resolver = self._resolvers.get(domain)
        if resolver is None:
            return Unresolved(
                UnresolvedReason.UNKNOWN_DOMAIN, f"no resolver for domain {domain!r}"
            )
        if resolver.offering(key) is None:
            return Unresolved(
                UnresolvedReason.UNKNOWN_KEY, f"{domain} does not offer {key!r}"
            )
        return resolver.invalidation(key)

    @callback
    def async_subscribe(
        self, domain: str, key: str, on_invalidated: Callable[[], None]
    ) -> CALLBACK_TYPE:
        """Subscribe to D43's signal, and hand back the unsubscribe.

        The subscription is deliberately independent of whether the key
        currently resolves. D42 keeps it live through an unavailable entity
        precisely because an anchor sensor that is `unknown` for ninety seconds
        after a restart is the normal case, and dropping the subscription with
        the occurrence would make a restart quietly destructive.

        A key that does not resolve at all subscribes to nothing and returns a
        no-op, so a caller never has to branch on it.
        """
        signal = self.async_invalidation(domain, key)
        if isinstance(signal, Unresolved):
            _LOGGER.debug(
                "Not subscribing to %s/%s: %s", domain, key, signal
            )
            return lambda: None

        if signal.kind is InvalidationKind.OBSERVATIONAL:
            return async_track_state_change_event(
                self.hass, sorted(signal.entity_ids), lambda _event: on_invalidated()
            )

        # Deterministic sources recompute only when the inputs to the
        # computation move — for `sun` that is latitude, longitude, elevation
        # and the civil timezone, all of which arrive on this one event.
        return self.hass.bus.async_listen(
            EVENT_CORE_CONFIG_UPDATE, lambda _event: on_invalidated()
        )

    @callback
    def _for_role(
        self, domain: str, key: str, role: Role
    ) -> tuple[Resolver, Offering] | Unresolved:
        """Look a key up and check it declares the role being asked for.

        Asking an anchor-only offering whether it *covers* an instant has an
        answer — almost always `False`, because a zman is zero-length — and that
        answer is indistinguishable from a day set that is simply out of force.
        Refusing outright is what keeps a mis-authored day set visible.
        """
        resolver = self._resolvers.get(domain)
        if resolver is None:
            return Unresolved(
                UnresolvedReason.UNKNOWN_DOMAIN, f"no resolver for domain {domain!r}"
            )
        if (offering := resolver.offering(key)) is None:
            return Unresolved(
                UnresolvedReason.UNKNOWN_KEY, f"{domain} does not offer {key!r}"
            )
        if role not in offering.roles:
            return Unresolved(
                UnresolvedReason.ROLE_NOT_OFFERED,
                f"{domain}/{key} is not usable as a {role}",
            )
        return (resolver, offering)

    async def _call[T](
        self, domain: str, key: str, awaitable: Coroutine[Any, Any, T]
    ) -> T | Unresolved:
        """Run one resolver call under D17's timeout and isolation.

        The timeout is honest about what it can do: it interrupts a resolver
        that awaits, not one that burns the event loop synchronously. Nothing
        in-tree can do the latter — `sun` is arithmetic — and a resolver that
        did would be a bug to fix rather than a case to contain.
        """
        try:
            async with asyncio.timeout(RESOLVER_TIMEOUT):
                return await awaitable
        except OfferingUnavailable as err:
            # D42's normal case, not an error: the source exists and has nothing
            # to say yet. Logged at debug because a restart produces one of
            # these per anchor entity and they are self-healing.
            _LOGGER.debug("Resolver %s/%s is unavailable: %s", domain, key, err)
            return Unresolved(UnresolvedReason.UNAVAILABLE, str(err))
        except TimeoutError:
            _LOGGER.warning(
                "Resolver %s/%s did not answer within %ss", domain, key, RESOLVER_TIMEOUT
            )
            return Unresolved(
                UnresolvedReason.TIMEOUT, f"no answer within {RESOLVER_TIMEOUT}s"
            )
        except Exception as err:  # noqa: BLE001 - D17: one anchor, not the engine
            _LOGGER.exception("Resolver %s/%s failed", domain, key)
            return Unresolved(UnresolvedReason.ERROR, f"{type(err).__name__}: {err}")


@callback
def async_create_registry(hass: HomeAssistant) -> ResolverRegistry:
    """Build the registry with the resolvers that ship in-tree (D14).

    The import is local to keep `contract` importable without dragging the
    implementations in — step 7 adds `hdate` to this function and to nowhere
    else, which is the test of whether D14's internal registry was worth having.
    """
    from .clock import ClockResolver  # noqa: PLC0415
    from .entity_time import EntityTimeResolver  # noqa: PLC0415
    from .sun import SunResolver  # noqa: PLC0415

    registry = ResolverRegistry(hass)
    registry.async_register(ClockResolver(hass))
    registry.async_register(EntityTimeResolver(hass))
    registry.async_register(SunResolver(hass))
    return registry


__all__ = ["ResolverRegistry", "async_create_registry"]
