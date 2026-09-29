"""The contract's own edge cases, and the isolation D17 puts around it.

None of this exercises a real time source. The distinctions being checked —
known-empty against unresolved, a declared horizon against an inferred one, a
role asked for against a role offered — are the ones §5 says the engine may rely
on, and they have to hold for every resolver including the ones nobody has
written yet. So they are tested against stubs, where a failure is unambiguous.

Every instant here is written out. D64's point is that the pipeline is a pure
function of `now`, and a test that read the clock would be testing something
other than the thing that ships.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from homeassistant.const import EVENT_CORE_CONFIG_UPDATE
from homeassistant.core import HomeAssistant

from custom_components.almanac.resolver import (
    BaseResolver,
    Horizon,
    HorizonKind,
    InvalidationKind,
    InvalidationSignal,
    Offering,
    OfferingUnavailable,
    ResolverRegistry,
    Role,
    Span,
    Unresolved,
    UnresolvedReason,
    Window,
)
from custom_components.almanac.resolver.contract import known_through

NY = ZoneInfo("America/New_York")

# A Friday in October, chosen only because it is nowhere near a DST transition.
DAY = date(2026, 10, 2)
NOON = datetime(2026, 10, 2, 12, 0, tzinfo=NY)
WINDOW = Window(
    datetime(2026, 10, 2, 0, 0, tzinfo=NY), datetime(2026, 10, 5, 0, 0, tzinfo=NY)
)


# --- Horizon (D13, §5.5) ---------------------------------------------------


def test_an_until_horizon_has_to_say_until_when() -> None:
    """A horizon that declares a bound and omits it declares nothing."""
    with pytest.raises(ValueError, match="until when"):
        Horizon(HorizonKind.UNTIL)


@pytest.mark.parametrize("kind", [HorizonKind.UNBOUNDED, HorizonKind.NEXT_ONLY])
def test_only_an_until_horizon_carries_a_bound(kind: HorizonKind) -> None:
    """The other two kinds say everything they have to say in the kind."""
    with pytest.raises(ValueError, match="no bound"):
        Horizon(kind, NOON)


@pytest.mark.parametrize(
    ("horizon", "spans", "expected"),
    [
        # Unbounded sources answer for the whole window, always.
        (Horizon.unbounded(), (), WINDOW.end),
        # A bounded source is believed up to its own bound, and no further than
        # the window that was asked for.
        (Horizon.until(datetime(2026, 10, 3, 0, 0, tzinfo=NY)), (), datetime(2026, 10, 3, 0, 0, tzinfo=NY)),
        (Horizon.until(datetime(2027, 1, 1, 0, 0, tzinfo=NY)), (), WINDOW.end),
        # NEXT_ONLY holding nothing in range knows nothing about the window at
        # all — which is the case §5.5 refuses to let look like "nothing
        # scheduled".
        (Horizon.next_only(), (), WINDOW.start),
        # NEXT_ONLY holding one value is authoritative exactly that far.
        (Horizon.next_only(), (Span(NOON, NOON),), NOON),
    ],
)
def test_known_through_comes_from_the_declaration(
    horizon: Horizon, spans: tuple[Span, ...], expected: datetime
) -> None:
    """§5.5 — horizon is declared, not inferred from the shape of the answer."""
    assert known_through(WINDOW, spans, horizon, NY) == expected


# --- Window ----------------------------------------------------------------


def test_a_window_is_made_of_aware_instants() -> None:
    """A naive window is not comparable with anything a resolver returns."""
    with pytest.raises(ValueError, match="aware instants"):
        Window(datetime(2026, 10, 2), datetime(2026, 10, 3, tzinfo=NY))


@pytest.mark.parametrize("end", [WINDOW.start, WINDOW.start - timedelta(hours=1)])
def test_a_window_moves_forward(end: datetime) -> None:
    """An empty or reversed window has no occurrences to be wrong about."""
    with pytest.raises(ValueError, match="does not move forward"):
        Window(WINDOW.start, end)


def test_days_is_generous_at_both_ends() -> None:
    """Resolvers compute per civil day and filter; a spare day is the cheap error."""
    assert list(WINDOW.days(NY)) == [
        date(2026, 10, 2),
        date(2026, 10, 3),
        date(2026, 10, 4),
        date(2026, 10, 5),
    ]


def test_shifting_a_window_is_absolute() -> None:
    """The padding an offset needs is instant arithmetic, not wall-clock."""
    shifted = WINDOW.shifted(timedelta(hours=-1))
    assert shifted.start == WINDOW.start - timedelta(hours=1)
    assert shifted.end == WINDOW.end - timedelta(hours=1)


# --- Span (D10) ------------------------------------------------------------


def test_a_span_is_all_day_or_precise_not_one_of_each() -> None:
    """The union is per span, not per edge; a mixed span has no reading."""
    with pytest.raises(ValueError, match="not one edge of each"):
        Span(DAY, NOON)


def test_a_span_does_not_run_backwards() -> None:
    """D38 makes inversion structurally impossible upstream; refuse it here too."""
    with pytest.raises(ValueError, match="runs backwards"):
        Span(NOON, NOON - timedelta(hours=1))


def test_an_all_day_span_is_half_open_over_civil_days() -> None:
    """D10 — and pointedly *not* core's zero-extent `start == end` encoding (A.1).

    Mirroring core would make `bounds` collapse and `covers` uniformly false,
    which is the bug D11's second stage exists to catch rather than to cause.
    """
    span = Span(DAY, date(2026, 10, 3))
    assert span.is_all_day
    assert span.bounds(NY) == (
        datetime(2026, 10, 2, 0, 0, tzinfo=NY),
        datetime(2026, 10, 3, 0, 0, tzinfo=NY),
    )
    assert span.covers(datetime(2026, 10, 2, 23, 59, 59, tzinfo=NY), NY)
    assert not span.covers(datetime(2026, 10, 3, 0, 0, tzinfo=NY), NY)


def test_a_zero_length_span_covers_nothing() -> None:
    """An instant has no interior, so a zman is an anchor and not a day set.

    This is why §5.4's Shabbat case needs the *interval* between candle lighting
    and havdalah rather than the two instants, and why `sun` declares no day-set
    role.
    """
    assert not Span(NOON, NOON).covers(NOON, NY)


def test_a_span_already_running_overlaps_the_window() -> None:
    """A `During` rule that is active *now* is the answer to "what is happening".

    Dropping it because it began before the window would make restart recovery
    (D41) blind to the case it exists for.
    """
    span = Span(WINDOW.start - timedelta(hours=6), WINDOW.start + timedelta(hours=6))
    assert span.overlaps(WINDOW, NY)


def test_a_zero_length_span_on_the_exclusive_edge_does_not_overlap() -> None:
    """Half-open, so two adjacent windows cannot both claim the boundary."""
    assert not Span(WINDOW.end, WINDOW.end).overlaps(WINDOW, NY)
    assert Span(WINDOW.start, WINDOW.start).overlaps(WINDOW, NY)


# --- InvalidationSignal (D43) ----------------------------------------------


def test_an_observational_signal_has_to_name_what_to_watch() -> None:
    """D43 — "must expose a subscribable signal". Naming nothing is not one."""
    with pytest.raises(ValueError, match="what to watch"):
        InvalidationSignal(InvalidationKind.OBSERVATIONAL)


def test_only_deterministic_answers_are_cacheable() -> None:
    """D43's whole purpose: which forecasts may be held and which may not."""
    assert InvalidationSignal(InvalidationKind.DETERMINISTIC).cacheable
    assert not InvalidationSignal(
        InvalidationKind.OBSERVATIONAL, frozenset({"sensor.x"})
    ).cacheable


# --- stubs used by the registry tests --------------------------------------


class _StubResolver(BaseResolver):
    """A resolver that answers with whatever the test handed it."""

    domain = "stub"

    def __init__(self, hass: HomeAssistant, **kwargs: object) -> None:
        super().__init__(hass)
        self.calls = 0

    def offerings(self) -> list[Offering]:
        return [
            Offering("anchor_only", "Anchor only", frozenset({Role.ANCHOR}), Horizon.unbounded()),
            Offering(
                "both",
                "Both roles",
                frozenset({Role.ANCHOR, Role.DAY_SET}),
                Horizon.unbounded(),
            ),
        ]

    def offering(self, key: str) -> Offering | None:
        return next((o for o in self.offerings() if o.key == key), None)

    async def forecast(self, key: str, window: Window) -> list[Span]:
        self.calls += 1
        # Out of order on purpose: the registry sorts, so no resolver has to.
        return [
            Span(datetime(2026, 10, 3, 9, 0, tzinfo=NY), datetime(2026, 10, 3, 17, 0, tzinfo=NY)),
            Span(datetime(2026, 10, 2, 9, 0, tzinfo=NY), datetime(2026, 10, 2, 17, 0, tzinfo=NY)),
        ]

    def invalidation(self, key: str) -> InvalidationSignal:
        return InvalidationSignal(InvalidationKind.DETERMINISTIC)


class _AngryResolver(_StubResolver):
    """A resolver that raises. D17 says this costs one anchor, not the engine."""

    domain = "angry"

    async def forecast(self, key: str, window: Window) -> list[Span]:
        raise RuntimeError("the sun has gone out")


class _SlowResolver(_StubResolver):
    """A resolver that never answers."""

    domain = "slow"

    async def forecast(self, key: str, window: Window) -> list[Span]:
        await asyncio.sleep(30)
        return []


class _MissingResolver(_StubResolver):
    """A resolver whose source is temporarily unavailable (D42)."""

    domain = "missing"

    async def forecast(self, key: str, window: Window) -> list[Span]:
        raise OfferingUnavailable("sensor.candle_lighting is unknown")


class _ParametricResolver(_StubResolver):
    """A resolver with no pick-list, like `clock` and `entity_time`."""

    domain = "parametric"

    def offerings(self) -> list[Offering]:
        return []

    def offering(self, key: str) -> Offering | None:
        return Offering(key, key, frozenset({Role.ANCHOR}), Horizon.next_only())


@pytest.fixture(autouse=True)
async def _new_york(hass: HomeAssistant) -> None:
    """Pin the civil timezone, because these tests assert about civil days.

    The harness's default would do, but a test whose expected dates depend on a
    zone it does not name is a test that changes meaning when the harness does.
    """
    await hass.config.async_set_time_zone("America/New_York")


@pytest.fixture
def registry(hass: HomeAssistant) -> ResolverRegistry:
    """A registry holding the stubs above and nothing real."""
    registry = ResolverRegistry(hass)
    for cls in (
        _StubResolver,
        _AngryResolver,
        _SlowResolver,
        _MissingResolver,
        _ParametricResolver,
    ):
        registry.async_register(cls(hass))
    return registry


# --- registration (§5.6, D14) ----------------------------------------------


def test_a_domain_cannot_be_claimed_twice(
    hass: HomeAssistant, registry: ResolverRegistry
) -> None:
    """D9 makes a domain permanent, so last-one-wins is not an option.

    Two resolvers answering to one name would make a stored schedule's meaning
    depend on import order.
    """
    with pytest.raises(ValueError, match="already claimed"):
        registry.async_register(_StubResolver(hass))


def test_the_pick_list_excludes_parametric_resolvers(
    registry: ResolverRegistry,
) -> None:
    """D16's list is what the editor renders; a parametric source has none.

    `clock` and `entity_time` have their own anchor kinds (D6), which is where
    a user types a time or picks an entity.
    """
    offerings = registry.async_offerings()
    assert "stub" in offerings
    assert "parametric" not in offerings
    assert "parametric" in registry.async_domains()


@pytest.mark.parametrize(
    ("domain", "key", "reason"),
    [
        ("hdate", "candle_lighting", UnresolvedReason.UNKNOWN_DOMAIN),
        ("stub", "tset_hakohavim", UnresolvedReason.UNKNOWN_KEY),
        ("parametric", "17:00:00", UnresolvedReason.NOT_SELECTABLE),
    ],
)
def test_a_resolver_anchor_is_checked_against_the_pick_list(
    registry: ResolverRegistry, domain: str, key: str, reason: UnresolvedReason
) -> None:
    """The schema validates these as slugs and defers the lookup to here.

    Each reason is a different sentence to a human — "that resolver isn't
    installed", "that zman no longer exists", "that source has its own kind of
    anchor" — which is why they are not one `Unresolved`.
    """
    result = registry.async_resolve_selectable(domain, key)
    assert isinstance(result, Unresolved)
    assert result.reason is reason


# --- D17: failure is a value -----------------------------------------------


async def test_a_resolver_that_raises_costs_one_anchor(
    registry: ResolverRegistry,
) -> None:
    """D17 — per-call isolation, in the contract rather than in each resolver."""
    angry = await registry.async_forecast("angry", "both", WINDOW)
    assert isinstance(angry, Unresolved)
    assert angry.reason is UnresolvedReason.ERROR
    assert "RuntimeError" in angry.detail

    # And the next resolver asked is unaffected, which is the half of D17 that
    # would be easy to claim and never check.
    healthy = await registry.async_forecast("stub", "both", WINDOW)
    assert not isinstance(healthy, Unresolved)
    assert len(healthy.spans) == 2


async def test_a_resolver_that_hangs_times_out(
    registry: ResolverRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D17 — the timeout lives in the contract, so a slow resolver cannot stall."""
    monkeypatch.setattr(
        "custom_components.almanac.resolver.registry.RESOLVER_TIMEOUT", 0.01
    )
    result = await registry.async_forecast("slow", "both", WINDOW)
    assert isinstance(result, Unresolved)
    assert result.reason is UnresolvedReason.TIMEOUT


async def test_unavailable_is_its_own_reason(registry: ResolverRegistry) -> None:
    """D42 / §10.4 — a source with nothing to say yet is not a broken source.

    The distinction is what lets recovery be honoured late by D41's rules
    instead of the occurrence being discarded on the spot.
    """
    result = await registry.async_forecast("missing", "both", WINDOW)
    assert isinstance(result, Unresolved)
    assert result.reason is UnresolvedReason.UNAVAILABLE


async def test_an_anchor_only_offering_refuses_the_day_set_role(
    registry: ResolverRegistry,
) -> None:
    """§5.1 — two roles, declared per offering, and asking wrongly is visible.

    `covers` on an anchor-only offering would answer `False` almost always, and
    a mis-authored day set would then look exactly like one that is out of
    force. Refusing is what keeps the mistake findable.
    """
    result = await registry.async_covers("stub", "anchor_only", NOON)
    assert isinstance(result, Unresolved)
    assert result.reason is UnresolvedReason.ROLE_NOT_OFFERED


# --- the derived defaults (§5.2, D11) --------------------------------------


async def test_the_registry_sorts_and_attaches_the_horizon(
    registry: ResolverRegistry,
) -> None:
    """A resolver states its spans; the contract states their order and reach."""
    forecast = await registry.async_forecast("stub", "both", WINDOW)
    assert not isinstance(forecast, Unresolved)
    assert [span.start.day for span in forecast.spans] == [2, 3]
    assert forecast.horizon == Horizon.unbounded()
    assert forecast.fully_known


async def test_candidate_dates_are_derived_from_forecast(
    registry: ResolverRegistry,
) -> None:
    """§5.2's default. D11's coarse stage: any civil day a span touches."""
    dates = await registry.async_candidate_dates("stub", "both", WINDOW)
    assert dates == [date(2026, 10, 2), date(2026, 10, 3)]


async def test_covers_is_derived_from_forecast(registry: ResolverRegistry) -> None:
    """D11's precise stage, over the instant's own civil day."""
    assert await registry.async_covers("stub", "both", NOON) is True
    assert (
        await registry.async_covers(
            "stub", "both", datetime(2026, 10, 2, 22, 0, tzinfo=NY)
        )
        is False
    )


async def test_an_exclusive_end_at_midnight_does_not_claim_the_next_day(
    hass: HomeAssistant,
) -> None:
    """The coarse stage is generous, not wrong.

    A span finishing exactly at midnight is half-open, so it does not touch the
    day it lands on — which is the one-day-too-many that would make "the whole
    of Shabbat" claim Sunday.
    """

    class _Midnight(_StubResolver):
        domain = "midnight"

        async def forecast(self, key: str, window: Window) -> list[Span]:
            return [
                Span(
                    datetime(2026, 10, 2, 18, 0, tzinfo=NY),
                    datetime(2026, 10, 3, 0, 0, tzinfo=NY),
                )
            ]

    registry = ResolverRegistry(hass)
    registry.async_register(_Midnight(hass))
    assert await registry.async_candidate_dates("midnight", "both", WINDOW) == [
        date(2026, 10, 2)
    ]


# --- invalidation and subscription (D43, D42) ------------------------------


async def test_a_deterministic_signal_subscribes_to_the_config(
    hass: HomeAssistant, registry: ResolverRegistry
) -> None:
    """D43 — sun and hdate move only when location or timezone does."""
    fired: list[int] = []
    unsub = registry.async_subscribe("stub", "both", lambda: fired.append(1))

    hass.bus.async_fire(EVENT_CORE_CONFIG_UPDATE, {})
    await hass.async_block_till_done()
    assert fired == [1]

    unsub()
    hass.bus.async_fire(EVENT_CORE_CONFIG_UPDATE, {})
    await hass.async_block_till_done()
    assert fired == [1]


async def test_subscribing_to_a_key_that_does_not_resolve_is_a_no_op(
    registry: ResolverRegistry,
) -> None:
    """A caller never has to branch on it, so nothing forgets to unsubscribe."""
    registry.async_subscribe("hdate", "candle_lighting", lambda: None)()
