"""The occurrence — the thing this product exists to be able to enumerate.

An occurrence is one rule, on one recurrence date, resolved: an instant for an
`At` rule (D2) and an interval for a `During` one. It is the unit the timeline
draws, the dry run lists and the tick acts on, and it is deliberately a *value*
with no methods that reach for the world.

Two properties are worth the reading:

- **An occurrence that will not happen is still an occurrence.** D12 requires the
  timeline to render what it dropped *as dropped*, because an occurrence that
  silently vanishes between authoring and rendering is indistinguishable from a
  bug. So `status` and `problem` are fields rather than a reason to return
  nothing, and `armed` is separate from `status` — D77 needs both facts at once,
  since "this would fire but the rule is off" and "this rule is on but its anchor
  is broken" are different rows in a list and must not collapse into one.
- **`start_date` is not `start.date()`.** It is the recurrence date that selected
  the rule (D1), which is the date of the anchor's *source* event. An offset can
  and should carry the resolved instant onto the following day — that is the
  whole of D7 and the reason upstream's day-boundary clamp (A.3) is not copied
  here — so the two fields disagree in exactly the cases that matter, and the
  one an audit line should name is the date the user picked.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, tzinfo
from enum import StrEnum

from ..const import RULE_AT, RULE_DURING
from ..resolver import Unresolved, Window

# `absolute` comes from the resolver contract rather than being defined here. That
# layer needs it for the same reason the engine does — `Window.contains` and
# `Span.covers` compare two instants, and Python compares two instants sharing a
# `tzinfo` object by wall clock — and two definitions of what "compare two instants"
# means is one too many. Re-exported below so the engine's call sites read locally.
from ..resolver.contract import absolute


class OccurrenceStatus(StrEnum):
    """Whether an occurrence can happen, and if not, in which way.

    Kept small on purpose. Every value here is a sentence the timeline has to be
    able to render (D12) and the log has to be able to print (D49); a status that
    no surface distinguishes would be a distinction only the code believes in.
    """

    SCHEDULED = "scheduled"
    UNRESOLVED = "unresolved"
    OVERLAPS_PREVIOUS = "overlaps_previous"
    # D11's second stage said no, and D12 says so out loud. The generous date pass
    # offered this day, the resolved anchor landed outside the set, and the row the
    # timeline draws has to say *that* rather than go missing -- "on Shabbat, at
    # 22:00" produces a Saturday occurrence whose absence is otherwise the only
    # evidence the two-stage filter ran at all.
    OUTSIDE_SET = "outside_set"


@dataclass(frozen=True, slots=True)
class Occurrence:
    """One rule, on one recurrence date, resolved as far as it can be."""

    schedule_id: str
    rule_id: str
    # `RULE_AT` or `RULE_DURING` — D2's two shapes, carried as the same strings
    # storage uses so that a log line and a stored rule spell it the same way.
    kind: str
    start_date: date
    status: OccurrenceStatus
    armed: bool
    start: datetime | None = None
    end: datetime | None = None
    problem: Unresolved | None = None

    def __post_init__(self) -> None:
        """Refuse the shapes that would make a consumer guess.

        These are assertions about this module's own output, not validation of
        user input: every one of them is a thing only a bug in the enumerator
        could produce, and finding it here is cheaper than finding it in a
        timeline that renders an interval with no end as a point.
        """
        if self.kind not in (RULE_AT, RULE_DURING):
            raise ValueError(f"{self.kind!r} is not a rule shape (D2)")
        if self.status is OccurrenceStatus.UNRESOLVED and self.problem is None:
            raise ValueError("an unresolved occurrence has to say what went wrong")
        if self.status is OccurrenceStatus.SCHEDULED:
            if self.start is None:
                raise ValueError("a scheduled occurrence has a resolved start")
            if self.kind == RULE_DURING and self.end is None:
                raise ValueError("a scheduled interval has a resolved end (D38)")
            if self.kind == RULE_AT and self.end is not None:
                raise ValueError("an `At` occurrence is an instant, not an interval")
        if (
            self.start is not None
            and self.end is not None
            and absolute(self.end) < absolute(self.start)
        ):
            raise ValueError("D38 makes an inverted interval impossible; this one is")

    @property
    def key(self) -> tuple[str, date]:
        """What identifies this occurrence across evaluations.

        The rule and the *recurrence* date, never the resolved instant. An
        occurrence has to be recognisable as the same one after the anchor moves —
        a sensor republishes candle lighting a minute later, a resolver's config
        changes (D43) — or the engine would exit an interval and re-enter it for
        every jitter in its own start time. `start_date` is stable across all of
        that because D1 fixed it before anything resolved.
        """
        return (self.rule_id, self.start_date)

    @property
    def is_interval(self) -> bool:
        """Whether this is a `During` occurrence."""
        return self.kind == RULE_DURING

    @property
    def will_run(self) -> bool:
        """Whether anything is expected to happen at this occurrence.

        Both halves are required, and keeping them separate is D77: a disarmed
        rule and a broken anchor are both reasons nothing will happen, and a
        surface that could only see this boolean would render them identically.
        """
        return self.armed and self.status is OccurrenceStatus.SCHEDULED

    def holds_at(self, instant: datetime) -> bool:
        """Whether this interval is in force at `instant`, half-open.

        Half-open because two consecutive intervals must not both hold at the
        hand-over, and because it makes a zero-length interval hold nowhere —
        the same answer `Span.covers` gives for a zero-length span, and for the
        same reason: an instant has no interior. D38 permits an end *at* the
        start, so this is the line that decides such an interval never activates
        rather than flapping open and shut at one instant.
        """
        if not self.will_run or self.start is None or self.end is None:
            return False
        return absolute(self.start) <= absolute(instant) < absolute(self.end)

    def overlaps(self, window: Window, zone: tzinfo) -> bool:
        """Whether this occurrence has anything to do with `window`.

        Generous in both unresolved cases, which is D12 again: an occurrence we
        cannot place precisely is still an occurrence the user asked for, and
        dropping it here would make the timeline's silence the only evidence of a
        broken anchor. The generosity is bounded, because the dates offered to
        this test are already limited to the enumeration's own search range.
        """
        if self.start is None:
            return self.start_date in set(window.days(zone))
        start, opens, closes = (
            absolute(self.start),
            absolute(window.start),
            absolute(window.end),
        )
        if not self.is_interval:
            return opens <= start < closes
        if self.end is None:
            return start < closes
        return start < closes and absolute(self.end) > opens


__all__ = ["Occurrence", "OccurrenceStatus", "absolute"]
