"""D57 — the reverse index: what uses this entity, and what uses this day set.

**Built in-tree because core's version is closed.** Home Assistant already draws
a "related items" graph, and this is exactly the question it answers for
automations and scripts. It cannot answer it for almanac: `ItemType` in
`homeassistant/components/search/__init__.py` is a closed enum and the five core
components it understands are imported by name, with no registration hook
anywhere (A.7). So either the question goes unanswered or the integration answers
it itself, and the question is not optional — D22's impact preview is built on
this index, and "which schedules will change if I edit *Shabbat*" has no other
implementation.

**It is a pure function of the two collections.** `build_index` takes the stored
schedules and day sets and returns a value; nothing here reads a clock (D64),
touches a store or resolves anything. That is what lets the preview in
`impact.py` build an index over a *proposed* edit as cheaply as over the real one,
which is the whole trick of showing a blast radius before committing to it.

**Every edge records where it came from.** A `Reference` names the schedule, the
rule inside it and which field mentioned the thing, because "this schedule uses
`sensor.candle_lighting`" is not actionable and "rule 3's start anchor uses
`sensor.candle_lighting`" is. `Usage` is the small closed vocabulary for that
"where", and it is deliberately coarser than the schema: a user repairing a
renamed entity wants to know it is an anchor, not which of the two anchor fields
of a `During` rule it is.

**Actions are indexed as far as step 4 can see them.** §15 step 5 owns the
actions model, and an action's target entities are the largest source of edges
this index will eventually carry. `_action_references` has one hook for them and
nothing behind it yet: the shape is here so that adding it is one function rather
than a second index.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from homeassistant.const import CONF_ID

from .const import (
    ANCHOR_ENTITY_TIME,
    CONDITION_COMPARISON,
    CONDITION_DAY_SET,
    CONDITION_GROUP,
    CONF_ANCHOR,
    CONF_COMPLETION,
    CONF_CONDITIONS,
    CONF_DAY_SET_ID,
    CONF_END,
    CONF_ENTITY_ID,
    CONF_FINISHED_WHEN,
    CONF_KIND,
    CONF_MEMBERS,
    CONF_RECURRENCE,
    CONF_RULES,
    CONF_SOURCE,
    CONF_START_ANCHOR,
    CONF_VALUE,
    END_ANCHOR,
    FINISHED_CONDITION,
    OPERAND_ENTITY,
    RECUR_DAY_SET,
    SOURCE_COMPOSITION,
)


class Usage(StrEnum):
    """Where in a schedule a reference was found.

    Coarser than the schema on purpose — see the module docstring. The values are
    stable strings because the frontend renders them as headings and D57's whole
    value is that the answer is legible.
    """

    RECURRENCE = "recurrence"
    ANCHOR = "anchor"
    CONDITION = "condition"
    COMPLETION = "completion"
    ACTION = "action"
    # Only ever on a day-set edge: D19's one level of set algebra means a day set
    # can reference another, and the referrer is a day set rather than a schedule.
    COMPOSITION = "composition"


@dataclass(frozen=True, slots=True)
class Reference:
    """One use of one entity or day set, and where it was found.

    `rule_id` is empty for a reference that belongs to the schedule rather than to
    any rule — a day-set recurrence, or a completion condition. Empty rather than
    `None` so that the tuple is sortable without a key function, which matters
    because the index's public answers are sorted for reproducibility.

    `schedule_id` carries a *day set's* id on a composition edge. That reuse is
    deliberate: one referrer field means the frontend renders one list, and
    `usage is Usage.COMPOSITION` is how it knows which collection to look the id
    up in.
    """

    schedule_id: str
    rule_id: str
    usage: Usage


@dataclass(frozen=True, slots=True)
class ReverseIndex:
    """Both directions of the graph, precomputed.

    Both directions, because both are asked. The forward direction answers "what
    does this schedule depend on", which is what an editor needs to warn that a
    schedule references something missing; the reverse answers "what depends on
    this", which is D22's question and the reason the index exists.

    Stored as plain mappings of sorted tuples rather than as sets, so that two
    indexes built from equal input compare equal — which is what makes the impact
    preview's before/after diff a comparison rather than a traversal.
    """

    entity_users: Mapping[str, tuple[Reference, ...]] = field(default_factory=dict)
    day_set_users: Mapping[str, tuple[Reference, ...]] = field(default_factory=dict)
    entities_used: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    day_sets_used: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def schedules_using_entity(self, entity_id: str) -> tuple[str, ...]:
        """Which schedules mention this entity, once each, in id order."""
        return _referrers(self.entity_users.get(entity_id, ()))

    def schedules_using_day_set(self, day_set_id: str) -> tuple[str, ...]:
        """Which schedules mention this day set, once each, in id order.

        Day-set referrers are excluded: a composition is not a schedule, and a
        caller asking this question is about to list schedules. `day_sets_using_day_set`
        is the other half, and `affected_schedules` in `impact.py` is what joins
        them — because D19 means editing a set changes every schedule using a set
        that contains it, one level deep and no further.
        """
        return _referrers(
            reference
            for reference in self.day_set_users.get(day_set_id, ())
            if reference.usage is not Usage.COMPOSITION
        )

    def day_sets_using_day_set(self, day_set_id: str) -> tuple[str, ...]:
        """Which day sets compose this one (D19's single level)."""
        return _referrers(
            reference
            for reference in self.day_set_users.get(day_set_id, ())
            if reference.usage is Usage.COMPOSITION
        )

    def references_to_entity(self, entity_id: str) -> tuple[Reference, ...]:
        """Every use of this entity, with its rule and field."""
        return self.entity_users.get(entity_id, ())

    def references_to_day_set(self, day_set_id: str) -> tuple[Reference, ...]:
        """Every use of this day set, with its rule and field."""
        return self.day_set_users.get(day_set_id, ())


def build_index(
    schedules: Mapping[str, dict[str, Any]],
    day_sets: Mapping[str, dict[str, Any]] | None = None,
) -> ReverseIndex:
    """Walk both collections once and record every reference.

    One pass over storage, which is the right cost model: the index is rebuilt
    when a collection changes rather than maintained incrementally, because an
    incremental index of a structure this small is a cache-invalidation bug
    waiting for its first day-set rename. `DictStorageCollection.data` is exactly
    the mapping this takes, so a caller passes it straight in.

    `day_sets` is optional so the entity half of the index is usable before day
    sets are loaded; without it, composition edges simply do not exist.
    """
    entity_users: defaultdict[str, list[Reference]] = defaultdict(list)
    day_set_users: defaultdict[str, list[Reference]] = defaultdict(list)
    entities_used: defaultdict[str, set[str]] = defaultdict(set)
    day_sets_used: defaultdict[str, set[str]] = defaultdict(set)

    for schedule_id, schedule in schedules.items():
        for target, reference in _schedule_references(schedule_id, schedule):
            kind, value = target
            if kind == "entity":
                entity_users[value].append(reference)
                entities_used[schedule_id].add(value)
            else:
                day_set_users[value].append(reference)
                day_sets_used[schedule_id].add(value)

    for day_set_id, day_set in (day_sets or {}).items():
        source = day_set.get(CONF_SOURCE, {})
        if source.get(CONF_KIND) != SOURCE_COMPOSITION:
            continue
        for member in source.get(CONF_MEMBERS, ()):
            day_set_users[member].append(
                Reference(day_set_id, "", Usage.COMPOSITION)
            )
            day_sets_used[day_set_id].add(member)

    return ReverseIndex(
        entity_users={key: tuple(value) for key, value in entity_users.items()},
        day_set_users={key: tuple(value) for key, value in day_set_users.items()},
        entities_used={
            key: tuple(sorted(value)) for key, value in entities_used.items()
        },
        day_sets_used={
            key: tuple(sorted(value)) for key, value in day_sets_used.items()
        },
    )


def _referrers(references: Iterable[Reference]) -> tuple[str, ...]:
    """The distinct referring ids, in id order.

    Deduplicated because a schedule that mentions the same day set in three rules
    is still one schedule to warn about, and sorted because "the same input gives
    the same answer" is what makes the impact preview's diff meaningful.
    """
    return tuple(sorted({reference.schedule_id for reference in references}))


type _Target = tuple[str, str]


def _schedule_references(
    schedule_id: str, schedule: dict[str, Any]
) -> Iterator[tuple[_Target, Reference]]:
    """Every entity and day set one schedule mentions, with its provenance."""
    recurrence = schedule.get(CONF_RECURRENCE, {})
    if recurrence.get(CONF_KIND) == RECUR_DAY_SET:
        # D20's other face. A day set used as a recurrence belongs to the
        # schedule, not to any rule, which is why `Reference.rule_id` is empty:
        # there is no rule to point the user at.
        yield ("day_set", recurrence[CONF_DAY_SET_ID]), Reference(
            schedule_id, "", Usage.RECURRENCE
        )

    # D46's `finished_when: condition` is the one place outside a rule where a
    # condition list is stored, and it is indexed for a practical reason: a
    # schedule that stops itself when an entity says so depends on that entity as
    # hard as any anchor does, and a rename that broke it would break termination
    # rather than a trigger.
    finished = schedule.get(CONF_COMPLETION, {}).get(CONF_FINISHED_WHEN, {})
    if finished.get(CONF_KIND) == FINISHED_CONDITION:
        for target in _condition_references(finished.get(CONF_CONDITIONS, ())):
            yield target, Reference(schedule_id, "", Usage.COMPLETION)

    for rule in schedule.get(CONF_RULES, ()):
        rule_id = rule.get(CONF_ID) or ""
        for anchor in _anchors(rule):
            for target in _anchor_references(anchor):
                yield target, Reference(schedule_id, rule_id, Usage.ANCHOR)
        for target in _condition_references(rule.get(CONF_CONDITIONS, ())):
            yield target, Reference(schedule_id, rule_id, Usage.CONDITION)
        for target in _action_references(rule):
            yield target, Reference(schedule_id, rule_id, Usage.ACTION)


def _anchors(rule: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Every anchor a rule carries, whichever shape the rule is.

    Written as a walk over the three possible fields rather than a branch on the
    rule kind, because a rule kind added later would silently drop its anchors
    from the index and nothing would fail — an index that is quietly incomplete
    is worse than one that is absent, since D22's preview would under-report the
    blast radius rather than error.
    """
    for key in (CONF_ANCHOR, CONF_START_ANCHOR):
        if isinstance(anchor := rule.get(key), dict):
            yield anchor
    end = rule.get(CONF_END, {})
    if end.get(CONF_KIND) == END_ANCHOR:
        yield end[CONF_ANCHOR]


def _anchor_references(anchor: dict[str, Any]) -> Iterator[_Target]:
    """The entity an anchor reads, if it reads one.

    A `kind: resolver` anchor names a domain and a key, not an entity, so it
    contributes nothing here even when the resolver happens to be backed by one.
    That is the correct answer rather than a gap: the schedule's dependency is on
    the *offering* (D9's compatibility surface), and an index that claimed a
    dependency on whatever entity a resolver reads today would be wrong the moment
    step 7's `hdate` resolver computes its answer with no entity at all.
    """
    if anchor.get(CONF_KIND) == ANCHOR_ENTITY_TIME:
        yield ("entity", anchor[CONF_ENTITY_ID])


def _condition_references(
    conditions: Sequence[dict[str, Any]],
) -> Iterator[_Target]:
    """Every entity and day set a condition list mentions, groups included.

    Both operands of a comparison are indexed. The right-hand side is the one it
    would be easy to miss — D23's entity-with-offset form is how "before candle
    lighting" is written as a condition — and missing it would make the reverse
    index disagree with the evaluator about what a rule depends on.
    """
    for condition in conditions:
        kind = condition.get(CONF_KIND)
        if kind == CONDITION_GROUP:
            # One level, so this recursion terminates by construction (§7.1).
            yield from _condition_references(condition.get(CONF_CONDITIONS, ()))
        elif kind == CONDITION_DAY_SET:
            yield ("day_set", condition[CONF_DAY_SET_ID])
        elif kind == CONDITION_COMPARISON:
            yield ("entity", condition[CONF_ENTITY_ID])
            operand = condition.get(CONF_VALUE, {})
            if operand.get(CONF_KIND) == OPERAND_ENTITY:
                yield ("entity", operand[CONF_ENTITY_ID])


def _action_references(rule: dict[str, Any]) -> Iterator[_Target]:
    """The entities a rule's actions target — step 5's half of the index.

    Empty, and present. §15 step 5 owns the actions model and nothing is stored in
    those fields yet (the schema refuses non-empty contents until then), so there
    is nothing to walk. The function exists because the alternative is discovering
    in step 5 that the index needs restructuring: the call site, the `Usage` value
    and the docstring that says where the edges come from are the parts that are
    expensive to add later.
    """
    return iter(())


__all__ = ["Reference", "ReverseIndex", "Usage", "build_index"]
