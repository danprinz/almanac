"""The brief's Scenario A, enumerated end to end against the real `hdate` resolver.

Scenario A is *lights on 45 minutes before candle lighting, off 30 minutes after
havdalah, on Shabbat* — the rule `PRODUCT_BRIEF.md` says must be effortless or the
product has failed. This file is its regression test, and it exists because for a
while it did not work.

**What was wrong, and what the measurement said.** UX finding 1 claimed the rule
never ran. It was written before step 7, so it was an argument about the design;
steps 7 and 8 made it checkable, and it was right. D11's stage two was asked about
the rule's *start* — 17:33, forty-five minutes before candle lighting — and Shabbat
begins at 18:18, so the membership test failed and the occurrence was dropped as
`outside_set`. The boundary turned out to be exactly zero and one second wide: a
zero offset scheduled all five Fridays in October 2026, an offset of one second
scheduled none. `hdate` puts the first instant of `issur_melacha` on the
candle-lighting instant, so the unshifted anchor cleared stage two by nothing at
all and every negative offset fell off. No shorter setup window helped.

**The ruling, D122.** A day set is the set of days the anchor's *event* belongs
to, so stage two is asked about the anchor's own instant and D7's offset is
arithmetic applied afterwards. §5.4's motivating example survives untouched — "on
Shabbat, at 22:00" is a clock anchor with no offset, so its two instants coincide
and the filter sees what it always saw, Friday inside and Saturday after havdalah.
`test_the_motivating_example_still_holds` is here so that cannot quietly stop being
true.

The fixture instants are New York, 2026, and they are written down rather than
computed, because the point of the file is the arithmetic and a test that recomputed
the thing under test would assert nothing.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from homeassistant.core import HomeAssistant

from custom_components.almanac.const import (
    ANCHOR_CLOCK,
    ANCHOR_RESOLVER,
    CONF_DOMAIN,
    CONF_KEY,
    CONF_KIND,
    CONF_OFFSET,
    END_ANCHOR,
    HDATE_CANDLE_LIGHTING,
    HDATE_HAVDALAH,
    HDATE_ISSUR_MELACHA,
    RECUR_DAY_SET,
    RESOLVER_HDATE,
    RULE_AT,
    RULE_DURING,
    SOURCE_OFFERING,
)
from custom_components.almanac.engine import async_enumerate
from custom_components.almanac.engine.occurrence import Occurrence, OccurrenceStatus
from custom_components.almanac.resolver import Window
from custom_components.almanac.storage import AlmanacData

NY = ZoneInfo("America/New_York")

LATITUDE = 40.7128
LONGITUDE = -74.0060

# October 2026 — long enough to show the pattern, short enough to read, and it
# contains a festival, which is where the interesting row is.
OCTOBER = Window(
    datetime(2026, 10, 1, tzinfo=NY),
    datetime(2026, 11, 1, tzinfo=NY),
)

# Every Friday in that window. These are the occurrences the rule exists for.
FRIDAYS = ("2026-10-02", "2026-10-09", "2026-10-16", "2026-10-23", "2026-10-30")

# What `hdate` publishes for the second of October, measured rather than assumed.
# `issur_melacha` is one continuous span from Friday evening to Sunday evening —
# Shabbat running straight into Shemini Atzeret and Simchat Torah — and candle
# lighting falls on its first instant exactly. That exactness is why the old
# behaviour's cliff was one second wide.
SHABBAT_BEGINS = datetime(2026, 10, 2, 18, 18, tzinfo=NY)
CANDLE_LIGHTING = datetime(2026, 10, 2, 18, 18, tzinfo=NY)
SETUP = timedelta(minutes=45)


@pytest.fixture(autouse=True)
async def _located(hass: HomeAssistant) -> None:
    """New York, because candle lighting is a fact about a place."""
    await hass.config.async_set_time_zone("America/New_York")
    await hass.config.async_update(latitude=LATITUDE, longitude=LONGITUDE, elevation=0)


async def _shabbat_set(data: AlmanacData, name: str) -> str:
    """The day set, from the only instant-precise Shabbat source `hdate` has.

    `issur_melacha` it is: the five date-typed sets `hdate` also publishes (D112)
    are holidays, and none of them is Shabbat.
    """
    item = await data.day_sets.async_create_item(
        {
            "name": name,
            "source": {
                "kind": SOURCE_OFFERING,
                "domain": RESOLVER_HDATE,
                "key": HDATE_ISSUR_MELACHA,
            },
        }
    )
    return str(item["id"])


async def _enumerate(
    hass: HomeAssistant, data: AlmanacData, rule: dict[str, Any], name: str
) -> list[Occurrence]:
    """One rule, on Shabbat, over October 2026."""
    day_set_id = await _shabbat_set(data, f"Shabbat for {name}")
    item = await data.schedules.async_create_item(
        {
            "name": name,
            "date_window": {"from": "2026-10-01", "until": "2026-11-30"},
            "recurrence": {"kind": RECUR_DAY_SET, "day_set_id": day_set_id},
            "rules": [rule],
        }
    )
    await hass.async_block_till_done()
    schedule: dict[str, Any] = dict(data.schedules.data[str(item["id"])])

    plan = await async_enumerate(
        data.resolvers, schedule, OCTOBER, day_sets=data.day_sets
    )
    assert plan.problem is None, f"the plan did not even resolve: {plan.problem}"
    return list(plan.occurrences)


def _scenario_a(offset: int = -45 * 60) -> dict[str, Any]:
    """Scenario A, with the setup window as a parameter.

    The offset is a parameter only so that the boundary can be probed from both
    sides — see `test_the_setup_window_no_longer_decides_membership`. The brief's
    rule is the default.
    """
    return {
        "kind": RULE_DURING,
        "id": "r1",
        "start_anchor": {
            CONF_KIND: ANCHOR_RESOLVER,
            CONF_DOMAIN: RESOLVER_HDATE,
            CONF_KEY: HDATE_CANDLE_LIGHTING,
            CONF_OFFSET: offset,
        },
        "end": {
            "kind": END_ANCHOR,
            "anchor": {
                CONF_KIND: ANCHOR_RESOLVER,
                CONF_DOMAIN: RESOLVER_HDATE,
                CONF_KEY: HDATE_HAVDALAH,
                CONF_OFFSET: 30 * 60,
            },
        },
    }


def _on(occurrences: list[Occurrence], date: str) -> Occurrence:
    """The one occurrence starting on a given civil date."""
    return next(occ for occ in occurrences if str(occ.start_date) == date)


async def test_scenario_a_runs(hass: HomeAssistant, almanac_data: AlmanacData) -> None:
    """The flagship rule, scheduled on every Friday in the month.

    This is the test the whole of §5.8 was about. Each Friday starts forty-five
    minutes before that evening's candle lighting and ends thirty minutes after the
    following night's havdalah — a span of more than twenty-four hours, so the
    start date and the end date differ and the rule crosses midnight twice. That it
    crosses midnight at all is A.3: upstream clamps an offset to the day boundary
    instead of rolling over, which is why schemes there cannot do this.
    """
    occurrences = await _enumerate(hass, almanac_data, _scenario_a(), "Shabbat lights")

    for friday in FRIDAYS:
        occ = _on(occurrences, friday)
        assert occ.status is OccurrenceStatus.SCHEDULED, friday
        assert occ.will_run, friday
        assert occ.start is not None and occ.end is not None, friday
        # Over a day long, so the interval does not live inside its start date.
        assert occ.end > occ.start + timedelta(days=1), friday

    # The numbers for the second of October, so the arithmetic is on the record.
    first = _on(occurrences, "2026-10-02")
    assert first.start == CANDLE_LIGHTING - SETUP
    assert first.start == datetime(2026, 10, 2, 17, 33, tzinfo=NY)
    assert first.end == datetime(2026, 10, 4, 19, 44, tzinfo=NY)
    # And the fact that used to break it: the start is before Shabbat begins, and
    # that is now allowed, because what was asked about was the anchor's
    # membership and not the start's.
    assert first.start < SHABBAT_BEGINS


async def test_the_setup_window_no_longer_decides_membership(
    hass: HomeAssistant, almanac_data: AlmanacData
) -> None:
    """D122, as the measurement that produced it.

    Before the ruling this was a cliff: offset 0 scheduled all five Fridays and
    offset −1 second scheduled none, because stage two was asked about the shifted
    instant and `hdate` puts the start of `issur_melacha` on the candle-lighting
    instant exactly — so the unshifted anchor cleared the test by nothing, and any
    negative offset fell off.

    Now the offset cannot affect membership at all, which is the property worth
    asserting rather than the five Fridays on their own: the same Fridays come back
    for a head start of nothing, of one second, of forty-five minutes and of three
    hours, and for a *positive* offset too. A test that only checked forty-five
    minutes would pass again if the cliff merely moved.
    """
    for offset in (0, -1, -45 * 60, -3 * 3600, 600):
        occurrences = await _enumerate(
            hass, almanac_data, _scenario_a(offset), f"Lights {offset}"
        )
        ran = [str(occ.start_date) for occ in occurrences if occ.will_run]
        assert ran == list(FRIDAYS), offset


async def test_the_motivating_example_still_holds(
    hass: HomeAssistant, almanac_data: AlmanacData
) -> None:
    """§5.4's example, which D122 must not break and is the reason D11 exists.

    "On Shabbat, at 22:00" has to fire once, on the Friday. A one-stage date
    predicate marks Friday *and* Saturday as in-set, so it would fire twice, and
    the Saturday firing would be nearly three hours after havdalah — in front of a
    room, the exact failure this project exists to eliminate.

    D122 leaves it alone by construction: a clock anchor at 22:00 carries no
    offset, so its source instant and its resolved instant are the same value and
    stage two sees precisely what it saw before. This test is here because "by
    construction" is a claim, and the cost of it being wrong is the original bug
    coming back silently.

    The week is 9–10 October, deliberately **not** the one every other test in this
    file uses. The first weekend of the month runs into Shemini Atzeret, so there
    `issur_melacha` is continuous through to Sunday evening and 22:00 on the
    Saturday is genuinely still inside it — the right answer, and useless as a test
    of the filter, because the row it would check is one that *should* fire. An
    ordinary Shabbat is the only week where the Saturday has to be rejected.
    """
    occurrences = await _enumerate(
        hass,
        almanac_data,
        {"kind": RULE_AT, "id": "r1", "anchor": {"kind": ANCHOR_CLOCK, "at": "22:00"}},
        "Fan",
    )

    # That week: `issur_melacha` is Friday 18:07 to Saturday 19:34. Both days are
    # candidate dates — stage one is generous on purpose — and only the Friday
    # survives stage two.
    assert _on(occurrences, "2026-10-09").will_run
    assert _on(occurrences, "2026-10-09").start == datetime(2026, 10, 9, 22, tzinfo=NY)

    saturday = _on(occurrences, "2026-10-10")
    assert saturday.status is OccurrenceStatus.OUTSIDE_SET
    assert not saturday.will_run
    # D12: present, with the instant that failed the test, so the timeline can say
    # why. And this row is outside the span for the *right* reason — 22:00 is two
    # and a half hours after that week's havdalah at 19:34.
    assert saturday.start == datetime(2026, 10, 10, 22, tzinfo=NY)


async def test_a_festival_second_candle_lighting_overlaps_rather_than_repeats(
    hass: HomeAssistant, almanac_data: AlmanacData
) -> None:
    """The sixth row, and why it is not a sixth occurrence.

    October 2026 crosses Shemini Atzeret, so `issur_melacha` runs unbroken from
    Friday the second at 18:18 to Sunday the fourth at 19:14, and that span holds a
    *second* candle lighting at 19:15 on the Saturday, for the festival's second
    day. It is a real anchor occurrence on a real candidate date, so it is
    enumerated.

    It does not run, because the Friday occurrence is still going — that interval
    ends Sunday at 19:44 — and the overlap rule marks the later of two overlapping
    intervals rather than firing it. Worth pinning for two reasons: it is the one
    row in the month that is neither a plain Friday nor a rejection, and under the
    *old* behaviour it was the only row that ran at all, which is how a completely
    broken flagship scenario still looked alive.
    """
    occurrences = await _enumerate(hass, almanac_data, _scenario_a(), "Shabbat lights")

    saturday = _on(occurrences, "2026-10-03")
    assert saturday.status is OccurrenceStatus.OVERLAPS_PREVIOUS
    assert not saturday.will_run
    assert saturday.start == datetime(2026, 10, 3, 18, 30, tzinfo=NY)

    # Six rows enumerated, five of them running. D12 again: the sixth is shown.
    assert len(occurrences) == 6
    assert len([occ for occ in occurrences if occ.will_run]) == 5
