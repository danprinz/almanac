"""The resolver contract — §5, and the grammar boundary D58 draws with it.

A time source is admissible iff it can implement `forecast`: enumerate a window
into concrete spans as a pure function of (offering, window, currently-known
state). That is the whole of D58, and everything in this module exists to make
the sentence checkable rather than aspirational.

Three properties are structural here rather than left to each implementation,
because each of them is a thing a resolver could otherwise get wrong silently:

- **D64 — no clock.** Nothing in this package reads the wall clock. A resolver
  is asked about a `Window` it is handed; it has no opinion about *now*. This is
  what lets the timeline, the dry run and the live engine be one code path at
  three different instants, and it is why `Window` is a parameter of every call
  rather than something a resolver could work out for itself.
- **D13 / §5.5 — the horizon is declared, not inferred.** A `Forecast` carries
  both the spans and the instant through which those spans are authoritative,
  so "the source has nothing more to say" is a different answer from "nothing is
  scheduled". A renderer that had to tell those apart by counting spans would
  project an entity's single held value across a week and call it fact.
- **D17 — failure is a value.** A resolver that cannot answer produces
  `Unresolved`, never an exception reaching the engine and never an empty list.
  The isolation and the timeout live in the registry (see `registry.py`), which
  is what "in the contract, not in each implementation" means.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from enum import StrEnum
from typing import Protocol, runtime_checkable

from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util


class Role(StrEnum):
    """The two roles of §5.1, either or both per offering.

    They are distinct because they are asked different questions. An anchor
    takes one *edge* of one span and turns it into an instant; a day set asks
    whether the set is *in force*, which is a predicate over the interior. The
    same offering can serve both — Shabbat is an anchor pair and a day set — but
    plenty serve only one, and a caller that asks for the role an offering does
    not declare gets `Unresolved` rather than a plausible-looking wrong answer.
    """

    ANCHOR = "anchor"
    DAY_SET = "day_set"


class HorizonKind(StrEnum):
    """How far ahead an offering can see (D13).

    The timeline renders known / estimated / unknown from this field alone, so
    it never has to know what a resolver is.
    """

    UNBOUNDED = "unbounded"
    UNTIL = "until"
    NEXT_ONLY = "next_only"


@dataclass(frozen=True, slots=True)
class Horizon:
    """A declared horizon. `bound` is meaningful only for `UNTIL`."""

    kind: HorizonKind
    bound: datetime | None = None

    def __post_init__(self) -> None:
        """Refuse the two shapes that would make the declaration meaningless."""
        if self.kind is HorizonKind.UNTIL and self.bound is None:
            raise ValueError("an UNTIL horizon has to say until when")
        if self.kind is not HorizonKind.UNTIL and self.bound is not None:
            raise ValueError(f"a {self.kind} horizon has no bound")

    @classmethod
    def unbounded(cls) -> Horizon:
        """Computable for any date — sun and hdate, which never fetch."""
        return cls(HorizonKind.UNBOUNDED)

    @classmethod
    def until(cls, bound: datetime) -> Horizon:
        """Known up to an instant the source itself states. (v2: calendars.)"""
        return cls(HorizonKind.UNTIL, bound)

    @classmethod
    def next_only(cls) -> Horizon:
        """One value: the next occurrence. An `entity_time` sensor holds one."""
        return cls(HorizonKind.NEXT_ONLY)


def absolute(instant: datetime) -> datetime:
    """The same instant in UTC, so that comparing two of them cannot go wall-clock.

    Not a formality, and not defensive. Python compares — and subtracts — two aware
    datetimes that share a `tzinfo` *object* by their naive values, ignoring the
    offset entirely. This is deliberate in CPython: PEP 495 specifies intra-zone
    comparison as wall-clock so that a zone's own ordering stays total. And
    `ZoneInfo` interns its instances, so every instant produced for one schedule
    shares one `tzinfo` and takes that path.

    On the November night the clocks go back, 01:15 EST is genuinely forty-five
    minutes *after* 01:30 EDT, and compares as being fifteen minutes before it;
    subtracting them yields −23:15. So an interval ending inside the repeated hour
    appears to end before it began, and `Window.contains` answers for the wrong one
    of the two 01:15s.

    D40 already settles what the *semantics* are — clock anchors are wall time,
    resolver anchors are absolute instants. This is what stops the implementation
    reaching the opposite answer by accident, one attribute lookup at a time. Every
    comparison and every subtraction of two instants in this integration goes
    through here; `engine/occurrence.py` re-exports it for the engine's call sites.
    """
    return instant.astimezone(dt_util.UTC)


@dataclass(frozen=True, slots=True)
class Window:
    """A half-open interval of instants, `[start, end)`.

    Half-open for the usual reason — two adjacent windows must not both contain
    the boundary — but also because it is the only encoding under which "the
    whole of Friday" and "the whole of Saturday" tile without the 00:00:00 in
    between firing twice.
    """

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        """A window has to be orientable and absolute to be comparable."""
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("a window is made of aware instants")
        if absolute(self.end) <= absolute(self.start):
            raise ValueError(f"window {self.start} .. {self.end} does not move forward")

    def contains(self, instant: datetime) -> bool:
        """Whether an instant falls in the window, compared absolutely."""
        return absolute(self.start) <= absolute(instant) < absolute(self.end)

    def shifted(self, delta: timedelta) -> Window:
        """The same window moved by `delta`, as absolute instants.

        Used to pad a forecast by an anchor's offset: a resolved instant
        `s + offset` lands in this window exactly when the source instant `s`
        lands in `self.shifted(-offset)`. Doing it the other way round — forecast
        the unpadded window, then shift — loses the occurrence whose source sits
        just outside the edge, which is the commonest anchor there is (sunset
        minus twenty minutes, on the last evening of the window).
        """
        return Window(
            dt_util.as_utc(self.start) + delta, dt_util.as_utc(self.end) + delta
        )

    def days(self, zone: tzinfo) -> Iterator[date]:
        """Every local civil date the window touches, inclusive at both ends.

        Deliberately generous: a resolver computes per civil day and then filters
        by `contains`, so one spare date at each edge costs an arithmetic call
        and a missing one costs an occurrence.
        """
        day = self.start.astimezone(zone).date()
        last = self.end.astimezone(zone).date()
        while day <= last:
            yield day
            day += timedelta(days=1)


@dataclass(frozen=True, slots=True)
class Span:
    """A stretch of time a source knows about, typed `date | datetime` (D10).

    A **datetime** span is precise, and may be zero-length: a zman is an instant,
    and `start == end` is how the source itself describes it.

    A **date** span is genuinely all-day and **half-open over local civil days**:
    the whole of 2 October is `Span(date(2026, 10, 2), date(2026, 10, 3))`.

    That last sentence is a correction of core, not a restatement of it. Core's
    `_all_day_event` builds `CalendarEvent(start=target_date, end=target_date)` —
    a civil date with *zero extent* (A.1), which fails every overlap test it is
    put to. Mirroring the encoding would have made `covers()` uniformly false for
    all-day sets, which is the exact bug D11 exists to prevent.
    """

    start: date | datetime
    end: date | datetime

    def __post_init__(self) -> None:
        """Both edges share a type, and time runs forwards."""
        if isinstance(self.start, datetime) != isinstance(self.end, datetime):
            raise ValueError(
                "a span is all-day or precise, not one edge of each: "
                f"{self.start!r} .. {self.end!r}"
            )
        if isinstance(self.start, datetime):
            if self.start.tzinfo is None or self.end.tzinfo is None:
                raise ValueError("a precise span is made of aware instants")
        if isinstance(self.start, datetime):
            backwards = absolute(self.end) < absolute(self.start)  # type: ignore[arg-type]
        else:
            backwards = self.end < self.start
        if backwards:
            raise ValueError(f"span {self.start} .. {self.end} runs backwards")

    @property
    def is_all_day(self) -> bool:
        """Whether this span is date-typed. `datetime` subclasses `date`."""
        return not isinstance(self.start, datetime)

    def bounds(self, zone: tzinfo) -> tuple[datetime, datetime]:
        """The span as half-open instants, resolving civil days in `zone`.

        A date-typed span only becomes instants once somebody says where on
        Earth the day is, which is why the timezone is a parameter here and not
        a property of the span.
        """
        if not self.is_all_day:
            return (self.start, self.end)  # type: ignore[return-value]
        return (
            datetime.combine(self.start, time(), tzinfo=zone),
            datetime.combine(self.end, time(), tzinfo=zone),
        )

    def covers(self, instant: datetime, zone: tzinfo) -> bool:
        """Whether the span contains `instant`, half-open.

        A zero-length span covers nothing, including its own instant. That is
        the honest answer — an instant has no interior — and it is why a day set
        built on zmanim has to publish the *interval* between two of them rather
        than the two instants (§5.4's Shabbat case, step 7's problem).
        """
        low, high = self.bounds(zone)
        return absolute(low) <= absolute(instant) < absolute(high)

    def overlaps(self, window: Window, zone: tzinfo) -> bool:
        """Whether the span touches the window at all.

        Note what this does *not* require: that the span begins inside the
        window. An interval already running at `window.start` is returned by
        `forecast`, because a `During` rule that is currently active is the
        answer to "what is happening now" and dropping it would make restart
        recovery (D41) blind to exactly the case it exists for.
        """
        low, high = self.bounds(zone)
        if low == high:
            return window.contains(low)
        return low < window.end and high > window.start


@dataclass(frozen=True, slots=True)
class Offering:
    """One named thing a resolver can be asked about (D8, D16).

    `key` is addressed by machine name and never by display text: the display
    text of a zman is translated at render time, so a stored schedule that
    matched on it would break on a language change (A.1). D9 makes `key` and
    the resolver's `domain` a permanent compatibility surface — a rename needs
    an alias table, never a silent substitution.
    """

    key: str
    display_name: str
    roles: frozenset[Role]
    horizon: Horizon


class InvalidationKind(StrEnum):
    """When a resolver's answers can change (D43)."""

    DETERMINISTIC = "deterministic"
    OBSERVATIONAL = "observational"


@dataclass(frozen=True, slots=True)
class InvalidationSignal:
    """What to watch in order to know that a forecast has gone stale.

    D43 puts this in the contract rather than in the engine because a pure
    `forecast(window)` is not enough to run a live system: without it the engine
    either polls or serves a stale answer, and a stale answer on the timeline is
    the failure this design exists to eliminate.

    It is also the other end of D42. An `entity_time` anchor whose entity is
    unavailable resolves to `Unresolved`, and the subscription named here is
    what stays live so that the recovery arrives at all.
    """

    kind: InvalidationKind
    entity_ids: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        """An observational signal that names nothing is not subscribable."""
        if self.kind is InvalidationKind.OBSERVATIONAL and not self.entity_ids:
            raise ValueError("an observational signal has to name what to watch")

    @property
    def cacheable(self) -> bool:
        """Whether the answer may be cached until a config change (D43)."""
        return self.kind is InvalidationKind.DETERMINISTIC


class UnresolvedReason(StrEnum):
    """Why an answer could not be produced.

    These are surfaced, not swallowed: D12 requires the timeline to render an
    occurrence it dropped *as dropped*, and D24 makes the same argument for
    conditions. "It didn't fire" with no reason attached is the complaint this
    product is a response to.
    """

    UNKNOWN_DOMAIN = "unknown_domain"
    UNKNOWN_KEY = "unknown_key"
    # Not a resolver failure at all, and deliberately in the same vocabulary as
    # one: §15's build order means a stored schedule can legitimately reference
    # something a later step implements — a day-set recurrence, before step 4.
    # D12 requires that occurrence to render as *not computed* rather than to
    # vanish, and a second enum for "we have not built it yet" would give the
    # timeline two unrelated ways to say the same sentence.
    NOT_IMPLEMENTED = "not_implemented"
    NOT_SELECTABLE = "not_selectable"
    # Also the engine's rather than a resolver's, and here for the same reason as
    # `NOT_IMPLEMENTED`. D38 pairs an interval's end to the first occurrence of its
    # end anchor at or after the resolved start, and D39 bounds how far that search
    # may run. When nothing is found inside the bound there is no interval at all,
    # and D12 requires the timeline to say so. The resolver answered perfectly
    # well — it is the *pairing* that failed, which is a different sentence from
    # "unavailable" and needs to read as one in a tooltip.
    NO_PAIRING = "no_pairing"
    ROLE_NOT_OFFERED = "role_not_offered"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Unresolved:
    """D17's degraded answer: a value the engine can carry, not an exception.

    Every registry call returns this or a result, so a caller that forgets to
    handle it fails a type check rather than a schedule.
    """

    reason: UnresolvedReason
    detail: str

    def __str__(self) -> str:
        """Render for a log line or a timeline tooltip."""
        return f"{self.reason}: {self.detail}"


@dataclass(frozen=True, slots=True)
class Forecast:
    """Spans, plus how far into the window they are authoritative (§5.5).

    `known_through` is the whole point. Without it an empty tail is ambiguous
    between *nothing is scheduled* and *the source cannot see that far*, and
    D17's unresolved state plus §10.4's late-arrival handling both depend on
    telling those apart. It is computed once, by the registry, from the declared
    horizon — never inferred by a renderer from the shape of `spans`.
    """

    window: Window
    spans: tuple[Span, ...]
    horizon: Horizon
    known_through: datetime

    @property
    def fully_known(self) -> bool:
        """Whether the declared horizon covers the whole requested window."""
        return self.known_through >= self.window.end


def known_through(
    window: Window, spans: tuple[Span, ...], horizon: Horizon, zone: tzinfo
) -> datetime:
    """The instant past which the source is not claiming anything (D13).

    `NEXT_ONLY` is the interesting case and the reason this is a function rather
    than a constant: a sensor holding one timestamp is authoritative up to that
    timestamp and mute after it, so knowledge ends where its single span ends —
    or at the start of the window when it holds nothing in range at all.
    """
    if horizon.kind is HorizonKind.UNBOUNDED:
        return window.end
    if horizon.kind is HorizonKind.UNTIL:
        assert horizon.bound is not None
        return min(horizon.bound, window.end)
    if not spans:
        return window.start
    return min(max(span.bounds(zone)[1] for span in spans), window.end)


# --- the contract itself ---------------------------------------------------


@runtime_checkable
class Resolver(Protocol):
    """§5.2, as a protocol.

    `offerings()` and `offering()` are two different questions and both are
    needed. `offerings()` is D16's pick-list — the fixed, enumerable set a user
    chooses from — and it is what the editor renders. `offering()` is the lookup
    that describes *one* address, and it is the only one a parametric source can
    answer: the `clock` resolver's key is a wall-clock time and the
    `entity_time` resolver's key is an entity_id, so neither has a list to
    publish, while both still have a horizon, a role and a validity rule.

    A resolver whose `offerings()` is empty is therefore **parametric** and is
    not addressable by a `kind: resolver` anchor — D6 gives `clock` and
    `entity_time` their own anchor kinds precisely so there is one spelling per
    concept, and D9 makes that spelling permanent.
    """

    domain: str

    def offerings(self) -> list[Offering]:
        """The pick-list. Empty for a parametric resolver."""
        ...

    def offering(self, key: str) -> Offering | None:
        """Describe one addressable key, or `None` if it is not one."""
        ...

    def horizon(self, key: str) -> Horizon:
        """How far ahead this key can be seen (D13)."""
        ...

    async def forecast(self, key: str, window: Window) -> list[Span]:
        """Enumerate the window. Pure in (key, window, known state) — D58."""
        ...

    async def candidate_dates(self, key: str, window: Window) -> list[date]:
        """Day-set role, coarse stage: any civil day a span touches (D11)."""
        ...

    async def covers(self, key: str, instant: datetime) -> bool:
        """Day-set role, precise stage: does a span contain this instant (D11)."""
        ...

    def invalidation(self, key: str) -> InvalidationSignal:
        """When this key's answers can change (D43)."""
        ...


class ResolverError(Exception):
    """What a resolver raises to say it cannot answer. Caught by the registry."""


class OfferingUnavailable(ResolverError):
    """The source exists but has nothing to say right now (D42).

    Distinct from an empty forecast, which is a positive statement that nothing
    is scheduled. An entity that is `unknown` for ninety seconds after a restart
    raises this; a sunset that genuinely does not occur above the Arctic circle
    does not.
    """


class BaseResolver(ABC):
    """The shared half of the contract: the parts §5.2 says are derived.

    `horizon`, `candidate_dates` and `covers` all have one correct definition in
    terms of `forecast`, and writing them out per resolver would be four
    opportunities to disagree about D11's two stages. A source that can do
    better — `hdate` can answer `covers` without enumerating — overrides them.
    """

    domain: str

    def __init__(self, hass: HomeAssistant) -> None:
        """Hold `hass` for location, timezone and state reads. Not for the clock."""
        self.hass = hass

    @property
    def zone(self) -> tzinfo:
        """The civil timezone spans are interpreted in.

        `hass.config.time_zone` rather than `dt_util.DEFAULT_TIME_ZONE`: the
        latter is process-global state set during core startup, and depending on
        it would make a resolver's output depend on something other than its
        arguments — which is the property D58 and D64 are both about.
        """
        return dt_util.get_time_zone(self.hass.config.time_zone) or dt_util.UTC

    def offerings(self) -> list[Offering]:
        """Parametric by default: no pick-list. `sun` overrides."""
        return []

    @abstractmethod
    def offering(self, key: str) -> Offering | None:
        """Describe one addressable key, or `None` if it is not one."""

    def horizon(self, key: str) -> Horizon:
        """The declared horizon, from the offering that carries it."""
        if (offering := self.offering(key)) is None:
            raise ResolverError(f"{self.domain} does not offer {key!r}")
        return offering.horizon

    @abstractmethod
    async def forecast(self, key: str, window: Window) -> list[Span]:
        """Enumerate the window into spans."""

    async def candidate_dates(self, key: str, window: Window) -> list[date]:
        """Coarse stage: every civil day any span touches (D11).

        Generous on purpose. The pass that narrows it is `covers`, and the two
        together are what stop "at 22:00 on Shabbat" from firing twice — once on
        Friday and once two hours after havdalah on Saturday.
        """
        zone = self.zone
        days: list[date] = []
        for span in await self.forecast(key, window):
            low, high = span.bounds(zone)
            first_day = low.astimezone(zone).date()
            last_day = first_day
            if high > low:
                end_local = high.astimezone(zone)
                # The end is exclusive, so a span finishing exactly at midnight
                # does not touch the day it lands on. Getting this wrong is the
                # one-day-too-many that makes "the whole of Shabbat" claim
                # Sunday as well.
                last_day = end_local.date() - (
                    timedelta(days=1) if end_local.time() == time() else timedelta()
                )
                last_day = max(last_day, first_day)
            day = first_day
            while day <= last_day:
                if day not in days:
                    days.append(day)
                day += timedelta(days=1)
        # Sorted here rather than left to the resolver: `forecast` is allowed to
        # return spans in any order (the registry sorts those), and a caller
        # generating recurrence start dates from an unordered list would produce
        # a timeline that is right but reads as broken.
        return sorted(days)

    async def covers(self, key: str, instant: datetime) -> bool:
        """Precise stage: does a span contain this instant (D11).

        The window is the instant's own civil day rather than the instant alone,
        because a span that began yesterday evening still covers this morning —
        which is the whole reason the second stage exists.
        """
        zone = self.zone
        local = instant.astimezone(zone)
        day_start = datetime.combine(local.date(), time(), tzinfo=zone)
        window = Window(day_start, day_start + timedelta(days=1))
        return any(
            span.covers(instant, zone) for span in await self.forecast(key, window)
        )

    @abstractmethod
    def invalidation(self, key: str) -> InvalidationSignal:
        """When this key's answers can change (D43)."""


@callback
def datetime_exists(instant: datetime) -> bool:
    """Whether a local wall time exists — i.e. is not inside a spring-forward gap.

    The round trip works because comparing two aware datetimes that share a
    `tzinfo` compares their wall times and ignores `fold` (PEP 495), so an
    imaginary 02:30 comes back as a real 03:30 and fails the equality. Core's
    own `dt_util._datetime_exists` is the same three lines; it is private, and
    copying three lines is cheaper than depending on a private symbol that D81
    says we track at one exact version.
    """
    return instant == instant.astimezone(dt_util.UTC).astimezone(instant.tzinfo)


@callback
def datetime_ambiguous(instant: datetime) -> bool:
    """Whether a local wall time happens twice — i.e. inside a fall-back repeat."""
    opposite = instant.replace(fold=not instant.fold)
    return datetime_exists(instant) and instant.utcoffset() != opposite.utcoffset()
