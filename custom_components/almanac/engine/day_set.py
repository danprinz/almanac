"""D11's two stages, over D18's first-class day sets and D19's one level of algebra.

A day set is a named object that answers two questions and no others:

```
candidate_dates(window) -> list[date]   # generous: any civil day the set touches
covers(instant)         -> bool         # exact: is the set in force at this instant
```

**The two stages are one mechanism, not a coarse pass plus a correction.** §5.4 is
worth restating because it is the whole reason this module is shaped this way:
Shabbat runs Friday ~18:45 to Saturday ~19:40, so a single date predicate marks
both Friday *and* Saturday as in-set and "on Shabbat, at 22:00" fires twice — the
second time two hours after havdalah. Stage one yields both days, stage two asks
`covers(Friday 22:00)` (true) and `covers(Saturday 22:00)` (false), and one
occurrence survives. For a genuinely date-granular set the second stage is a
no-op, because the whole civil day is in force. Correct in both cases with no
special casing, which is what makes D20 — the same object as recurrence and as
condition — affordable.

**Composition is one level (D19), and that is enforced here rather than assumed.**
A member that is itself a composition resolves to `Unresolved` with the decision
named, because the alternative is a set whose evaluation cost is unbounded and
whose one-line summary cannot be written. The storage collection refuses to
create such a thing (`day_sets.py`), so reaching the check here means a store
written by hand or by an older build — exactly the case D12 wants visible rather
than silently reinterpreted.

**Composition combines answers, not spans.** `covers` composes exactly: a union is
in force where any member is, an intersection where all are, and `minus` where the
first is and no other is. `candidate_dates` composes *generously*, which needs an
argument rather than an assumption:

> A covered instant on civil day D requires every member of an intersection to
> cover it, and a member that covers an instant on D has a span touching D, so D
> appears in that member's own `candidate_dates`. Intersecting the members'
> candidate sets therefore cannot drop a day the intersection could cover. For
> `minus`, the first member's candidates are returned unfiltered: a day the
> subtrahend covers *partially* is still a day the difference can cover, and
> narrowing at date granularity would lose it.

**A source may also be a span the user wrote, which is D124 and §6.1's layer 2.**
Its two edges are anchors in the same shape a rule's are, so offsets, resolver
offerings and entity times work in it without anything new being explained, and
"from candle lighting to havdalah" stops depending on a resolver having thought of
it. For this source the two stages are derived from one computation: enumerate the
spans, then project them onto civil days for stage one and test containment for
stage two. That is the opposite direction from a date-granular source, where stage
two is the derived no-op, and it is the reason both stages live in one module.

> **Overlapping spans are allowed here and are not a collision.** A set is a union
> of instants, so two spans that overlap contribute the union of themselves and
> nothing has to be decided. This is the one place where the engine's usual
> treatment of overlap — D39's refusal, `_mark_overlaps` — would be wrong, and the
> case is not hypothetical: on a festival weekend `candle_lighting` occurs on the
> Friday *and* on the Saturday while the first span is still running, so a rule
> that forbade the second start would drop a legitimate edge.

Nothing here reads a clock (D64): both entry points take the instant or the window
they are asked about.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import date, datetime, time, timedelta
from typing import Any, Final, Protocol

from ..const import (
    COMPOSE_INTERSECT,
    COMPOSE_MINUS,
    COMPOSE_UNION,
    CONF_DOMAIN,
    CONF_END_ANCHOR,
    CONF_KEY,
    CONF_KIND,
    CONF_MEMBERS,
    CONF_OPERATOR,
    CONF_SOURCE,
    CONF_START_ANCHOR,
    SOURCE_ANCHOR_SPAN,
    SOURCE_COMPOSITION,
    SOURCE_OFFERING,
)
from ..resolver import (
    ResolverRegistry,
    Span,
    Unresolved,
    UnresolvedReason,
    Window,
    async_resolve_anchor_on_date,
)
from .occurrence import absolute
from .recurrence import start_dates

_LOGGER = logging.getLogger(__name__)

# How far D124's span enumeration reaches, in civil days, in each direction.
#
# Forwards it bounds the search for the end edge, and backwards it is how far
# before the window a span may have begun and still be running inside it —
# `Window.days`' padding, used for the reason that method documents.
#
# Eight because the longest stretch a pair of anchors plausibly describes is a
# festival week: Sukkot runs eight days in the diaspora, and the second edge of a
# span the user is actually reasoning about arrives inside that. Beyond it the
# thing being described is a season rather than a span, and a season is a resolver
# offering (§6.1's layer 3) rather than two anchors. The bound is on the *search*,
# not on correctness: a longer span fails to pair and says so (`NO_PAIRING`)
# rather than being silently truncated or silently dropped.
_SPAN_REACH_DAYS: Final = 8

# What the span enumeration reports as it goes: the slot it resolved and the
# instant it got. `plan.py` passes a recorder so D44's `known_through` sees a day
# set's own anchors, and every other caller passes nothing.
#
# The slot is the *same string* `day_set_anchors` gives that edge, which is the
# only part of this worth insisting on: a declaration and the instants it covers
# have to arrive under one key or `known_through` combines one anchor's horizon
# with another anchor's data. The shape is `day_set:<member index>:start|end`, and
# the index is 0 for a set that is not a composition.
SpanObserver = Callable[[str, datetime], None]


def _slot(index: int, edge: str) -> str:
    """The horizon key for one edge of one member's span. See `SpanObserver`."""
    return f"day_set:{index}:{edge}"


class DaySetLookup(Protocol):
    """How the engine reaches a day set, without depending on the collection.

    A `Protocol` rather than an import of `DaySetCollection` because the engine
    must stay callable against a *hypothetical* set of day sets: D22's impact
    preview enumerates the same schedules twice, once with the stored definitions
    and once with a proposed one, and it does that by handing this one method a
    different answer. Depending on the collection would make the preview either
    mutate the store or reimplement the engine, and both of those are how a
    preview ends up disagreeing with what actually happens.
    """

    def async_get_day_set(self, day_set_id: str) -> dict[str, Any] | None:
        """The stored day set with this id, or `None` if there is none."""


async def async_candidate_dates(
    registry: ResolverRegistry,
    lookup: DaySetLookup | None,
    day_set_id: str,
    window: Window,
    observe: SpanObserver | None = None,
) -> list[date] | Unresolved:
    """D11's coarse stage: every civil day this set might be in force on.

    Generous by construction, and deliberately so — the pass that narrows it is
    `async_covers`, and one spare date costs an arithmetic call while a missing
    one costs an occurrence the user asked for.

    `observe` is how a D124 span's own anchors reach D44's horizon reporting: the
    enumeration says which instants it got, and the caller decides whether it
    cares. Optional because only `plan.py` does — a day-set condition and D21's
    binary_sensor are answering about one instant and have no window to be
    partially sure of.
    """
    if isinstance(item := _lookup(lookup, day_set_id), Unresolved):
        return item

    source = item[CONF_SOURCE]
    if source[CONF_KIND] != SOURCE_COMPOSITION:
        return await _async_member_dates(registry, source, window, observe, 0)

    members = _members(lookup, source)
    if isinstance(members, Unresolved):
        return members

    per_member: list[list[date]] = []
    for index, member in enumerate(members):
        dates = await _async_member_dates(
            registry, member[CONF_SOURCE], window, observe, index
        )
        if isinstance(dates, Unresolved):
            # Propagated rather than treated as an empty set. An unresolvable
            # member makes the *composition* unresolvable, and substituting "no
            # days" would turn a broken reference into a schedule that silently
            # never runs — the failure D12 exists to make visible.
            return dates
        per_member.append(dates)

    return _compose_dates(source[CONF_OPERATOR], per_member)


async def async_covers(
    registry: ResolverRegistry,
    lookup: DaySetLookup | None,
    day_set_id: str,
    instant: datetime,
) -> bool | Unresolved:
    """D11's precise stage: is this set in force at this instant.

    This is also the whole of a day-set *condition* (D20, §7.1's table), which is
    why the two callers share one function rather than one convention.
    """
    if isinstance(item := _lookup(lookup, day_set_id), Unresolved):
        return item

    source = item[CONF_SOURCE]
    if source[CONF_KIND] != SOURCE_COMPOSITION:
        return await _async_member_covers(registry, source, instant)

    members = _members(lookup, source)
    if isinstance(members, Unresolved):
        return members

    answers: list[bool] = []
    for member in members:
        covered = await _async_member_covers(registry, member[CONF_SOURCE], instant)
        if isinstance(covered, Unresolved):
            return covered
        answers.append(covered)

    return _compose_covers(source[CONF_OPERATOR], answers)


def day_set_references(source: dict[str, Any]) -> tuple[str, ...]:
    """The day sets this source references, for D57's reverse index.

    One level deep, because D19 says there is only one level. A caller that
    wanted a transitive closure would be describing a structure this design does
    not have.
    """
    if source.get(CONF_KIND) != SOURCE_COMPOSITION:
        return ()
    return tuple(source.get(CONF_MEMBERS, ()))


def day_set_anchors(
    lookup: DaySetLookup | None, day_set_id: str
) -> tuple[tuple[str, dict[str, Any]], ...]:
    """Every anchor this set is built from, as `(slot, anchor)` pairs.

    For D44, which is why it is a plain function and not part of evaluation: a
    plan has to be able to say how far ahead it is *sure*, and a set built on
    `sensor.candle_lighting` sees exactly as far as that sensor declares (D13).
    Before D124 no day set had an anchor at all, so nothing asked.

    One level deep, like `day_set_references`, because D19 says there is only one
    level. A reference that will not resolve yields nothing rather than raising:
    an unresolvable set limits nothing here, because it *is* unresolved and the
    occurrences carry that as a problem of their own.
    """
    if isinstance(item := _lookup(lookup, day_set_id), Unresolved):
        return ()

    sources = [item[CONF_SOURCE]]
    if sources[0][CONF_KIND] == SOURCE_COMPOSITION:
        members = _members(lookup, sources[0])
        if isinstance(members, Unresolved):
            return ()
        sources = [member[CONF_SOURCE] for member in members]

    found: list[tuple[str, dict[str, Any]]] = []
    for index, source in enumerate(sources):
        if source[CONF_KIND] != SOURCE_ANCHOR_SPAN:
            continue
        found.append((_slot(index, "start"), source[CONF_START_ANCHOR]))
        found.append((_slot(index, "end"), source[CONF_END_ANCHOR]))
    return tuple(found)


def _lookup(
    lookup: DaySetLookup | None, day_set_id: str
) -> dict[str, Any] | Unresolved:
    """Fetch a day set, turning both ways of not finding one into a value (D17)."""
    if lookup is None:
        # A caller asked the engine to evaluate a day-set reference without
        # handing it the day sets. That is a programming error rather than a
        # user's mistake, and it still comes back as a value: D12 requires the
        # occurrence to render as *not computed* rather than vanish, and a
        # raising engine would take the whole plan down with it.
        return Unresolved(
            UnresolvedReason.ERROR,
            f"day set {day_set_id!r} cannot be evaluated: no day-set collection "
            "was supplied to the enumerator",
        )
    if (item := lookup.async_get_day_set(day_set_id)) is None:
        # A dangling reference, which is a state the store is allowed to be in:
        # the schema validates a day-set id as a string and never against the
        # collection, so that deleting a day set degrades the schedules using it
        # instead of refusing to load them (the same reasoning as a missing
        # resolver domain, D17).
        return Unresolved(
            UnresolvedReason.UNKNOWN_KEY, f"no day set with id {day_set_id!r}"
        )
    return item


def _members(
    lookup: DaySetLookup | None, source: dict[str, Any]
) -> list[dict[str, Any]] | Unresolved:
    """Resolve a composition's members, refusing the second level (D19)."""
    members: list[dict[str, Any]] = []
    for member_id in source[CONF_MEMBERS]:
        if isinstance(member := _lookup(lookup, member_id), Unresolved):
            return member
        if member[CONF_SOURCE][CONF_KIND] == SOURCE_COMPOSITION:
            return Unresolved(
                UnresolvedReason.ERROR,
                f"day set {member_id!r} is itself a composition, and D19 allows "
                "one level of set algebra",
            )
        members.append(member)
    return members


async def _async_member_dates(
    registry: ResolverRegistry,
    source: dict[str, Any],
    window: Window,
    observe: SpanObserver | None = None,
    index: int = 0,
) -> list[date] | Unresolved:
    """Stage one for a single, non-composed source.

    `index` is this source's position in its composition, and 0 for a source that
    is not in one. It exists only to name the horizon slot the observer reports
    under, which has to match what `day_set_anchors` declared.
    """
    kind = source[CONF_KIND]

    if kind == SOURCE_ANCHOR_SPAN:
        spans = await _async_anchor_spans(registry, source, window, observe, index)
        if isinstance(spans, Unresolved):
            return spans
        # Projected onto the window's own days rather than onto each span's. A
        # span that began last Thursday is in the answer — `Span.overlaps` keeps
        # it, because a set already in force at the window's start is part of
        # what the window is in force for — but *Thursday* is not a candidate
        # start date for a window that begins on Friday, and returning it would
        # hand the rule enumerator a date outside what it asked about.
        touched: set[date] = set()
        for span in spans:
            low, high = span.bounds(registry.zone)
            for day in Window(low, high).days(registry.zone):
                touched.add(day)
        return sorted(touched & set(window.days(registry.zone)))

    if kind == SOURCE_OFFERING:
        # Straight through to the resolver, which checks `Role.DAY_SET` and
        # refuses an anchor-only offering (`registry._for_role`). That refusal
        # matters here: asking a zman whether it *covers* an instant has an
        # answer — almost always false, because an instant has no interior — and
        # it is indistinguishable from a day set that is simply out of force.
        return await registry.async_candidate_dates(
            source[CONF_DOMAIN], source[CONF_KEY], window
        )

    days = list(window.days(registry.zone))
    if not days:  # pragma: no cover - a Window always touches at least one day
        return []
    # The same four generators D1 uses for a recurrence, called through the same
    # function. That is D20 made structural: "every Tuesday" cannot mean one thing
    # in a schedule and another in a day set, because there is one implementation.
    return start_dates(source, days[0], days[-1])


async def _async_member_covers(
    registry: ResolverRegistry, source: dict[str, Any], instant: datetime
) -> bool | Unresolved:
    """Stage two for a single, non-composed source."""
    kind = source[CONF_KIND]

    if kind == SOURCE_ANCHOR_SPAN:
        # The instant's own civil day, widened by `_async_anchor_spans`' own
        # reach, which is what finds a span that began days earlier and is still
        # running. Exactly the shape `BaseResolver.covers` has, and for the same
        # reason: a span with an interior is not found by looking only at the day
        # its end lands on.
        local = instant.astimezone(registry.zone).date()
        spans = await _async_anchor_spans(
            registry, source, day_window(local, local, registry.zone)
        )
        if isinstance(spans, Unresolved):
            return spans
        return any(span.covers(instant, registry.zone) for span in spans)

    if kind == SOURCE_OFFERING:
        return await registry.async_covers(
            source[CONF_DOMAIN], source[CONF_KEY], instant
        )

    # A date-granular set is in force for the whole of each civil day it names, so
    # stage two reduces to *is this instant's local date one of them*. This is
    # §5.4's "the second stage is a no-op" case, and writing it as a one-day query
    # against the same generator rather than as a separate date test is what keeps
    # it that way — there is no second implementation to disagree.
    local = instant.astimezone(registry.zone).date()
    dates = start_dates(source, local, local)
    if isinstance(dates, Unresolved):
        return dates
    return bool(dates)


async def _async_anchor_spans(
    registry: ResolverRegistry,
    source: dict[str, Any],
    window: Window,
    observe: SpanObserver | None = None,
    index: int = 0,
) -> list[Span] | Unresolved:
    """D124's spans, for every one that touches `window`.

    One span per occurrence of the start edge, paired with the first occurrence of
    the end edge at or after it — the same pairing rule as D38, reached by the same
    forward walk over civil days, because "across midnight" is the case both exist
    for and a second implementation of it would be a second place to get it wrong.

    **Both edges use the anchor's offset instant, and D122 does not apply here.**
    D122 is about which instant a *rule's* anchor is tested against when a day set
    filters it. Inside the set the offset is the edge: "from forty-five minutes
    before candle lighting" means the span starts at 17:33, which is the whole
    point of letting the user write the edges.
    """
    start_anchor = source[CONF_START_ANCHOR]
    end_anchor = source[CONF_END_ANCHOR]
    zone = registry.zone

    spans: list[Span] = []
    for day in window.days(zone, pad_before=_SPAN_REACH_DAYS):
        anchored = await async_resolve_anchor_on_date(registry, start_anchor, day)
        if isinstance(anchored, Unresolved):
            # Propagated, like a composition member that will not resolve. An
            # unresolvable edge makes the set unresolvable, and substituting "no
            # span" would turn a broken anchor into a set that is silently never
            # in force — the failure D12 exists to make visible.
            return anchored
        if anchored is None:
            continue
        start = anchored.at
        if observe is not None:
            observe(_slot(index, "start"), start)

        end = await _async_span_end(
            registry, end_anchor, start, day, zone, observe, index
        )
        if isinstance(end, Unresolved):
            return end
        span = Span(start, end)
        if span.overlaps(window, zone):
            spans.append(span)

    return spans


async def _async_span_end(
    registry: ResolverRegistry,
    anchor: dict[str, Any],
    start: datetime,
    day: date,
    zone: Any,
    observe: SpanObserver | None,
    index: int,
) -> datetime | Unresolved:
    """The first occurrence of the end edge at or after `start`, or why not."""
    last_day = day + timedelta(days=_SPAN_REACH_DAYS)
    while day <= last_day:
        anchored = await async_resolve_anchor_on_date(registry, anchor, day)
        if isinstance(anchored, Unresolved):
            # Stop rather than skip, for D38's reason: a day whose answer is
            # unknown might hold an *earlier* end than any later day, so taking a
            # later one would silently lengthen the span.
            return anchored
        if anchored is not None:
            if observe is not None:
                observe(_slot(index, "end"), anchored.at)
            # At or after, like D38, so a zero-length span is permitted. It
            # covers nothing — an instant has no interior (`Span.covers`) — which
            # is the honest answer and is why a set cannot be built from one zman.
            if absolute(anchored.at) >= absolute(start):
                return anchored.at
        day += timedelta(days=1)

    return Unresolved(
        UnresolvedReason.NO_PAIRING,
        f"the span beginning {start.isoformat()} has no end edge within "
        f"{_SPAN_REACH_DAYS} days (D124)",
    )


def _compose_dates(
    operator: str, per_member: Sequence[Sequence[date]]
) -> list[date] | Unresolved:
    """D19's algebra over stage-one answers. See the module docstring's argument."""
    if not per_member:  # pragma: no cover - the schema requires two members
        return []

    if operator == COMPOSE_UNION:
        found: set[date] = set()
        for dates in per_member:
            found.update(dates)
        return sorted(found)

    if operator == COMPOSE_INTERSECT:
        common = set(per_member[0])
        for dates in per_member[1:]:
            common &= set(dates)
        return sorted(common)

    if operator == COMPOSE_MINUS:
        # Unfiltered on purpose. A day the subtrahend covers only part of — the
        # evening of a festival that ends at havdalah — is still a day the
        # difference can cover, and subtracting at date granularity would drop it
        # before stage two ever gets to ask. The narrowing happens in `covers`,
        # where it can be exact.
        return sorted(set(per_member[0]))

    return Unresolved(
        UnresolvedReason.ERROR, f"{operator!r} is not a composition operator (D19)"
    )


def _compose_covers(operator: str, answers: Sequence[bool]) -> bool | Unresolved:
    """D19's algebra over stage-two answers, where it is exact."""
    if operator == COMPOSE_UNION:
        return any(answers)
    if operator == COMPOSE_INTERSECT:
        return all(answers)
    if operator == COMPOSE_MINUS:
        return bool(answers) and answers[0] and not any(answers[1:])
    return Unresolved(
        UnresolvedReason.ERROR, f"{operator!r} is not a composition operator (D19)"
    )


def day_window(first: date, last: date, zone: Any) -> Window:
    """The window of instants covering an inclusive range of civil days.

    Here rather than at the call site because the conversion has one correct
    form and two tempting wrong ones: the end is the midnight *after* `last`
    (D88's exclusivity), and it is built with `datetime.combine` rather than
    `dt_util.start_of_local_day`, which reads the clock when its argument is
    omitted and is therefore barred outright by D64's sweep.
    """
    return Window(
        datetime.combine(first, time(), tzinfo=zone),
        datetime.combine(last + timedelta(days=1), time(), tzinfo=zone),
    )


__all__ = [
    "DaySetLookup",
    "SpanObserver",
    "async_candidate_dates",
    "async_covers",
    "day_set_anchors",
    "day_set_references",
    "day_window",
]
