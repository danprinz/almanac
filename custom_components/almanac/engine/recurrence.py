"""D1 — recurrence selects start dates, and only start dates.

The single most consequential line in §2: the recurrence picks the civil dates on
which a schedule's rules **begin**. It does not pick occurrences, it does not
bound them, and it has no opinion about when they end. That is what makes
midnight structurally uninteresting here — a 23:00 → 02:00 interval is one
occurrence beginning on one date rather than two halves needing reconciliation —
and it is the difference from upstream, whose resolver clamps an offset to the day
boundary instead of rolling over (A.3).

The module also owns the **period**, which is a smaller idea doing more work than
it looks. D39 rejects an interval longer than its recurrence period at save, and
that same number bounds the forward search D38 needs for an end anchor: if no end
has occurred within one period of the start, then any end this search could still
find would describe an interval D39 forbids. So the bound on the search is a
consequence of a decision already taken rather than a constant somebody chose —
which matters, because a constant somebody chose is how upstream ended up with
`MAX_OFFFSET_HOURS = 4` and five years of no stated reason (A.3).

Nothing here reads a clock (D64): a date range is a parameter.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Any

from ..const import (
    CONF_FROM,
    CONF_INTERVAL,
    CONF_KIND,
    CONF_NTH,
    CONF_WEEKDAY,
    RECUR_DATES,
    RECUR_DAY_SET,
    RECUR_EVERY_N,
    RECUR_NTH_WEEKDAY,
    RECUR_WEEKDAYS,
    WEEKDAYS,
)
from ..resolver import Unresolved, UnresolvedReason

# `WEEKDAYS` is ordered Monday-first, which is `date.weekday()`'s own numbering,
# so the index *is* the weekday number. Stated rather than relied on silently:
# the constant is a storage compatibility surface and reordering it would move
# every stored schedule by a day.
_WEEKDAY_NUMBERS: dict[str, int] = {name: i for i, name in enumerate(WEEKDAYS)}

# The shortest possible gap between the same nth weekday of consecutive months.
# Four weeks: the 1st Thursday of March is 28 days after the 1st Thursday of
# February in a non-leap year, and no pair can be closer. Used as a conservative
# period, because D39's check has to hold for the *worst* case rather than the
# typical one.
_NTH_WEEKDAY_MIN_PERIOD = timedelta(days=28)


def start_dates(
    recurrence: dict[str, Any], first: date, last: date
) -> list[date] | Unresolved:
    """Every date in `[first, last]` on which this recurrence starts a rule.

    Inclusive at both ends, unlike a `Window`. A date range typed by a person is
    inclusive — "until the 31st" means through the 31st — and the half-open
    convention D88 fixes governs resolver spans, where exclusivity buys adjacency
    as an equality. Mixing the two conventions in one codebase is survivable;
    mixing them without saying which applies where is not.
    """
    kind = recurrence.get(CONF_KIND)

    if kind == RECUR_WEEKDAYS:
        wanted = {_WEEKDAY_NUMBERS[name] for name in recurrence[RECUR_WEEKDAYS]}
        return [day for day in _every_day(first, last) if day.weekday() in wanted]

    if kind == RECUR_DATES:
        listed = sorted({date.fromisoformat(value) for value in recurrence[RECUR_DATES]})
        return [day for day in listed if first <= day <= last]

    if kind == RECUR_NTH_WEEKDAY:
        weekday = _WEEKDAY_NUMBERS[recurrence[CONF_WEEKDAY]]
        nth = recurrence[CONF_NTH]
        found = (
            _nth_weekday(year, month, weekday, nth)
            for year, month in _every_month(first, last)
        )
        return [day for day in found if day is not None and first <= day <= last]

    if kind == RECUR_EVERY_N:
        interval = recurrence[CONF_INTERVAL]
        origin = date.fromisoformat(recurrence[CONF_FROM])
        # Dates before the origin are excluded rather than counted backwards.
        # "Every third day from the 4th" names a start; extending the sequence
        # into the past would make the same stored schedule mean something
        # different depending on which window it is rendered in.
        begin = max(first, origin)
        offset = (begin - origin).days % interval
        cursor = begin + timedelta(days=(interval - offset) % interval)
        dates: list[date] = []
        while cursor <= last:
            dates.append(cursor)
            cursor += timedelta(days=interval)
        return dates

    if kind == RECUR_DAY_SET:
        # A guard, not a gap. D20 makes a day set usable as a recurrence and D11's
        # two stages are what keep it honest -- `candidate_dates` generates and
        # `covers` filters, so "at 22:00 on Shabbat" fires once on Friday rather
        # than twice -- but both stages need the day-set collection and the
        # resolver registry, which makes evaluating one `async`. And
        # `engine/day_set.py` calls back into *this* function for the four date
        # generators D20 shares with it, so putting the day-set branch here would
        # be a genuine import cycle rather than a stylistic worry.
        #
        # So the branch lives in `engine/day_set.py` and `plan.async_enumerate`
        # routes to it directly. Reaching this line means a caller bypassed that
        # routing, which is a programming error reported as a value rather than
        # raised, because this function's contract is that it always returns one.
        return Unresolved(
            UnresolvedReason.ERROR,
            "a day-set recurrence is evaluated by engine/day_set.py, which needs "
            "the day-set collection -- see plan.async_enumerate",
        )

    return Unresolved(
        UnresolvedReason.ERROR, f"{kind!r} is not a recurrence kind (D1, D18)"
    )


def recurrence_period(recurrence: dict[str, Any]) -> timedelta | None:
    """The shortest gap this recurrence can put between two start dates.

    `None` means "no bound can be stated", which is honest for a single listed
    date and for a day set whose membership is not ours to compute yet. A caller
    must treat that as *unknown* and not as *unlimited*: D39's check has nothing
    to reject, and D38's search falls back to D44's compute budget.

    Shortest rather than typical, because both callers need the conservative
    figure. D39 must reject an interval that could overlap the next occurrence in
    *any* week of the year, not in the average one — a Friday-and-Saturday
    recurrence has a one-day gap and a six-day gap, and a 25-hour interval is a
    D39 violation on the strength of the first alone.
    """
    kind = recurrence.get(CONF_KIND)

    if kind == RECUR_WEEKDAYS:
        selected = sorted(
            {_WEEKDAY_NUMBERS[name] for name in recurrence[RECUR_WEEKDAYS]}
        )
        if len(selected) == 1:
            return timedelta(days=7)
        # Circular: the gap from the last selected weekday round to the first is
        # a real gap, and for {sat, sun} it is the *short* one.
        gaps = [b - a for a, b in zip(selected, selected[1:], strict=False)]
        gaps.append(selected[0] + 7 - selected[-1])
        return timedelta(days=min(gaps))

    if kind == RECUR_DATES:
        listed = sorted({date.fromisoformat(value) for value in recurrence[RECUR_DATES]})
        if len(listed) < 2:
            return None
        return min(b - a for a, b in zip(listed, listed[1:], strict=False))

    if kind == RECUR_NTH_WEEKDAY:
        return _NTH_WEEKDAY_MIN_PERIOD

    if kind == RECUR_EVERY_N:
        return timedelta(days=recurrence[CONF_INTERVAL])

    return None


def _every_day(first: date, last: date) -> list[date]:
    """Every date in an inclusive range."""
    return [first + timedelta(days=n) for n in range((last - first).days + 1)]


def _every_month(first: date, last: date) -> list[tuple[int, int]]:
    """Every (year, month) the inclusive range touches."""
    months: list[tuple[int, int]] = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        months.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def _nth_weekday(year: int, month: int, weekday: int, nth: int) -> date | None:
    """The nth such weekday of a month, with `-1` meaning the last one.

    `None` when the month has no fifth Tuesday. Returning nothing rather than
    falling back to the fourth is the point: the user asked for a date that does
    not exist that month, and quietly substituting a different one would make the
    timeline disagree with what they wrote.
    """
    if nth == -1:
        last = date(year, month, calendar.monthrange(year, month)[1])
        return last - timedelta(days=(last.weekday() - weekday) % 7)
    first = date(year, month, 1)
    day = first + timedelta(
        days=(weekday - first.weekday()) % 7 + 7 * (nth - 1)
    )
    return day if day.month == month else None


__all__ = ["recurrence_period", "start_dates"]
