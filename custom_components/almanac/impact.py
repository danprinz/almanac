"""D22 — showing the blast radius of a day-set edit before it is committed.

D18 makes a day set shared, and sharing is the whole point: *Shabbat* is defined
once and every schedule that needs it refers to the same definition. D18 also
records an `owner` and deliberately does not enforce it, so nothing stops a user
editing a set that four schedules and an integration depend on. The mitigation D22
chose is not a lock. It is a preview: say what will change, then let them decide.

**The preview is the engine, run twice.** It enumerates each affected schedule
over the same window against the stored day sets and against an overlay carrying
the proposed definition, and diffs the two plans. That is the only implementation
that cannot drift from the truth — a second, cheaper approximation of "what will
change" would be a second scheduler, and the first time it disagreed with the real
one the preview would be worse than nothing. D64 is what makes this affordable:
the engine takes its window as a parameter, so evaluating a hypothesis is the same
call as evaluating the present.

**One level out, because D19 only allows one.** A schedule is affected if it uses
the edited set directly, or if it uses a set that composes it. There is no
transitive closure to compute — D19's single level of set algebra means the
traversal terminates after one hop by construction, which is one of the things
that decision buys.

**Deleting is previewed the same way.** `proposed=None` means "show me what
happens if this disappears". That is deliberately not prevented: `day_sets.py`
permits deleting a referenced set, because a dangling reference is visible and
repairable whereas a refusal leaves a user unable to clean up. This is what makes
that choice defensible — the consequence is shown first.

A schedule whose *recurrence* is the deleted set does not acquire `UNRESOLVED`
occurrences, because an unevaluable recurrence is a fault of the schedule rather
than of any rule and `plan.py` reports it once on the plan. Its occurrences
therefore leave the diff as removals, and `broken` carries the distinction —
without it the preview would render "4 occurrences removed" identically for a set
that narrowed and a set that ceased to exist, which are not the same warning.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from .engine import Occurrence, Plan, async_enumerate
from .index import ReverseIndex, build_index
from .resolver import ResolverRegistry, Window


@dataclass(frozen=True, slots=True)
class OccurrenceChange:
    """One occurrence that differs between the current and proposed definitions.

    Both sides are carried rather than a description of the difference, because
    the frontend renders the two rows side by side and D12's statuses are the
    interesting part of the answer: "this Saturday 22:00 becomes *outside the
    set*" is the sentence the preview exists to produce, and it cannot be written
    from a diff of instants alone.

    `before` is `None` for an occurrence the edit adds and `after` is `None` for
    one it removes. Both present means it changed.
    """

    schedule_id: str
    rule_id: str
    start_date: date
    before: Occurrence | None = None
    after: Occurrence | None = None

    @property
    def added(self) -> bool:
        """Whether the proposed definition creates this occurrence."""
        return self.before is None

    @property
    def removed(self) -> bool:
        """Whether the proposed definition destroys this occurrence."""
        return self.after is None


@dataclass(frozen=True, slots=True)
class DaySetImpact:
    """What a proposed day-set definition would change.

    `references` counts uses rather than schedules: a schedule that mentions the
    set in three rules is one entry in `schedules` and three references, and both
    numbers are worth showing because they measure different risks — how many
    things break, and how tangled the set is.

    `unavailable` names the schedules whose plans could not be compared because
    something in them would not resolve either way (a resolver timed out, D17).
    Reported rather than silently omitted, because a preview that quietly skipped
    a schedule would understate the blast radius, which is the one failure mode
    that makes the feature harmful rather than merely incomplete.

    `broken` names the schedules that enumerate today and would not afterwards —
    the deletion case above, and an edit that makes a set unevaluable. Distinct
    from `unavailable`, which is about the preview failing rather than the
    schedule.
    """

    day_set_id: str
    references: int = 0
    schedules: tuple[str, ...] = ()
    day_sets: tuple[str, ...] = ()
    changes: tuple[OccurrenceChange, ...] = ()
    unavailable: tuple[str, ...] = ()
    broken: tuple[str, ...] = ()

    @property
    def added(self) -> tuple[OccurrenceChange, ...]:
        """Occurrences the edit would create."""
        return tuple(change for change in self.changes if change.added)

    @property
    def removed(self) -> tuple[OccurrenceChange, ...]:
        """Occurrences the edit would destroy."""
        return tuple(change for change in self.changes if change.removed)

    @property
    def altered(self) -> tuple[OccurrenceChange, ...]:
        """Occurrences that survive the edit with a different shape."""
        return tuple(
            change
            for change in self.changes
            if not change.added and not change.removed
        )


class _Overlay:
    """A `DaySetLookup` that answers for a hypothetical collection.

    The proposed item replaces the stored one for the duration of the preview, and
    `None` removes it. Nothing is written: the overlay exists precisely so that the
    engine can be asked about a definition that does not exist yet, which is what
    separates a preview from a save followed by an undo.

    Composition members are resolved through the overlay too, so previewing an
    edit to a set that another set contains shows the effect on schedules using
    the *container* — the case D19's single level makes possible and which a
    direct-references-only preview would miss.
    """

    def __init__(
        self,
        day_sets: Mapping[str, dict[str, Any]],
        *,
        day_set_id: str,
        proposed: dict[str, Any] | None,
    ) -> None:
        """Wrap a stored collection with one substitution."""
        self._day_sets = day_sets
        self._day_set_id = day_set_id
        self._proposed = proposed

    def async_get_day_set(self, day_set_id: str) -> dict[str, Any] | None:
        """The stored definition, or the proposed one for the edited set."""
        if day_set_id == self._day_set_id:
            return self._proposed
        return self._day_sets.get(day_set_id)


class _Stored:
    """A `DaySetLookup` over a plain mapping.

    A class rather than the collection itself, so that the preview can be run over
    any mapping — including a fixture in a test and a snapshot taken before an
    edit. `DaySetCollection` satisfies the same protocol, which is why the
    protocol is structural.
    """

    def __init__(self, day_sets: Mapping[str, dict[str, Any]]) -> None:
        """Wrap a mapping."""
        self._day_sets = day_sets

    def async_get_day_set(self, day_set_id: str) -> dict[str, Any] | None:
        """The stored definition."""
        return self._day_sets.get(day_set_id)


def affected_schedules(
    index: ReverseIndex, day_set_id: str
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Which schedules and which day sets an edit to this set reaches.

    Two hops and no more: the schedules that use the set, plus the schedules that
    use a set that composes it. D19 guarantees a composition's members are leaves,
    so there is no third hop to look for and no cycle to guard against — the
    collection refuses a self-referential composition at write time
    (`DaySetCollection._check_composition`), and even without that guarantee this
    traversal is bounded because it does not recurse.
    """
    containers = index.day_sets_using_day_set(day_set_id)
    schedules = set(index.schedules_using_day_set(day_set_id))
    for container in containers:
        schedules.update(index.schedules_using_day_set(container))
    return tuple(sorted(schedules)), containers


async def async_preview_day_set_change(
    registry: ResolverRegistry,
    schedules: Mapping[str, dict[str, Any]],
    day_sets: Mapping[str, dict[str, Any]],
    *,
    day_set_id: str,
    proposed: dict[str, Any] | None,
    window: Window,
) -> DaySetImpact:
    """D22's preview: what changes if this day set becomes `proposed`.

    `window` is the caller's, which is the D64 property doing real work here: the
    frontend asks about the fortnight it is showing, a test asks about one week,
    and neither needs a different code path. `proposed=None` previews a deletion.

    Disabled schedules are still enumerated. A schedule that is off today is one a
    user may turn on tomorrow, and telling them the edit was harmless because
    nothing was running would be true and useless.
    """
    index = build_index(schedules, day_sets)
    reached, containers = affected_schedules(index, day_set_id)
    references = len(index.references_to_day_set(day_set_id))

    stored = _Stored(day_sets)
    overlay = _Overlay(day_sets, day_set_id=day_set_id, proposed=proposed)

    changes: list[OccurrenceChange] = []
    unavailable: list[str] = []
    broken: list[str] = []
    for schedule_id in reached:
        schedule = schedules[schedule_id]
        before = await async_enumerate(registry, schedule, window, day_sets=stored)
        after = await async_enumerate(registry, schedule, window, day_sets=overlay)
        if before.problem is not None and after.problem is not None:
            # Broken both ways, so the edit is not what is wrong with it. Named
            # rather than dropped: the user is entitled to know the preview could
            # not speak for this schedule.
            unavailable.append(schedule_id)
            continue
        if before.problem is None and after.problem is not None:
            # The edit is what breaks it. Recorded *and* diffed: the removals are
            # still the occurrences the user loses, and this is why they lose them.
            broken.append(schedule_id)
        changes.extend(_diff(schedule_id, before, after))

    return DaySetImpact(
        day_set_id=day_set_id,
        references=references,
        schedules=reached,
        day_sets=containers,
        changes=tuple(changes),
        unavailable=tuple(unavailable),
        broken=tuple(broken),
    )


def _diff(schedule_id: str, before: Plan, after: Plan) -> list[OccurrenceChange]:
    """The occurrences that differ between two plans of one schedule.

    Keyed on `(rule_id, start_date)` — `Occurrence.key` — and not on the resolved
    instant, for the reason that property's docstring gives: the recurrence date
    is what survives the anchor moving, so "this occurrence moved" is expressible
    as a change rather than as an unrelated removal and addition.

    Compared by value, which works because an `Occurrence` is a frozen dataclass
    all the way down and `Unresolved` is one too. Equality therefore covers the
    status, the instants and the reason a thing did not resolve, and no field can
    be added later that the diff silently ignores.
    """
    old = {occ.key: occ for occ in before.occurrences}
    new = {occ.key: occ for occ in after.occurrences}

    changes: list[OccurrenceChange] = []
    for key in sorted(old.keys() | new.keys(), key=lambda item: (item[1], item[0])):
        was, now = old.get(key), new.get(key)
        if was == now:
            continue
        rule_id, start_date = key
        changes.append(
            OccurrenceChange(
                schedule_id=schedule_id,
                rule_id=rule_id,
                start_date=start_date,
                before=was,
                after=now,
            )
        )
    return changes


__all__ = [
    "DaySetImpact",
    "OccurrenceChange",
    "affected_schedules",
    "async_preview_day_set_change",
]
