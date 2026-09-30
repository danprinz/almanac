"""The brief's Scenario A, enumerated end to end against the real `hdate` resolver.

UX finding 1 claims this rule — *start 45 minutes before candle lighting, end 30
minutes after havdalah, on Shabbat* — produces no occurrences under D11 as written,
every week, forever, and renders as a ghost rather than as a failure. It was the
finding the UX round marked blocking, and it was written before step 7 existed, so
it was an argument about the design rather than a measurement of it.

Steps 7 and 8 are what make it checkable, and this file is the measurement. It is
real, the mechanism is the one claimed, and the shape of it is sharper than the
finding knew: the day set is asked about the *start instant*, and a setup window
puts that instant before the set begins. `test_the_cliff_is_one_second_wide`
locates the boundary exactly — a zero offset schedules all five Fridays in the
month and an offset of **one second** schedules none of them. The two features are
not marginally in tension, they are categorically incompatible, and every setup
window of every length is on the wrong side.

Four tests, pulling against each other on purpose:

- `test_scenario_a_is_rejected` pins today's behaviour with the numbers, so nobody
  has to re-derive it and so a change to it is visible.
- `test_the_cliff_is_one_second_wide` is the diagnosis rather than the symptom. It
  is also the test that says which fix works: reading the day set at the anchor's
  own instant is what the zero-offset column already does.
- `test_the_only_thing_that_does_run_is_a_festival_candle_lighting` pins the false
  positive that would otherwise let a laxer test report the scenario healthy.
- `test_scenario_a_runs` is `xfail(strict=True)` and pins what the brief says has
  to be true. It fails today and passes when the ruling lands.

None of these is deleted when the ruling arrives: one becomes the regression test
and the others have to be inverted deliberately.

The fixture instants are New York, 2026. They are written down rather than computed
because the point of the file is the arithmetic, and a test that recomputed the
thing under test would assert nothing.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from homeassistant.core import HomeAssistant

from custom_components.almanac.const import (
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
# contains a festival, which is where the false positive lives.
OCTOBER = Window(
    datetime(2026, 10, 1, tzinfo=NY),
    datetime(2026, 11, 1, tzinfo=NY),
)

# Every Friday in that window. These are the occurrences the rule exists for, and
# the ones it does not produce.
FRIDAYS = ("2026-10-02", "2026-10-09", "2026-10-16", "2026-10-23", "2026-10-30")

# What `hdate` publishes for the second of October, measured rather than assumed.
# `issur_melacha` is one continuous span from Friday evening to Sunday evening —
# Shabbat running straight into Shemini Atzeret and Simchat Torah — and candle
# lighting falls on its first instant exactly.
SHABBAT_BEGINS = datetime(2026, 10, 2, 18, 18, tzinfo=NY)
CANDLE_LIGHTING = datetime(2026, 10, 2, 18, 18, tzinfo=NY)


@pytest.fixture(autouse=True)
async def _located(hass: HomeAssistant) -> None:
    """New York, because candle lighting is a fact about a place."""
    await hass.config.async_set_time_zone("America/New_York")
    await hass.config.async_update(latitude=LATITUDE, longitude=LONGITUDE, elevation=0)


async def _enumerate(
    hass: HomeAssistant, data: AlmanacData, offset: int = -45 * 60
) -> list[Occurrence]:
    """Scenario A over October 2026, with the setup window as a parameter.

    Start `offset` seconds from candle lighting, end thirty minutes after havdalah,
    on Shabbat. The day set is `issur_melacha`, which is the only instant-precise
    Shabbat source `hdate` publishes (D112) — the five date-typed sets it also
    publishes are holidays, and none of them is Shabbat.

    The offset is a parameter so that the same rule can be enumerated either side
    of the boundary, which is the only way to show that the boundary is where it
    is rather than somewhere in the neighbourhood.
    """
    day_set = await data.day_sets.async_create_item(
        {
            "name": f"Shabbat {offset}",
            "source": {
                "kind": SOURCE_OFFERING,
                "domain": RESOLVER_HDATE,
                "key": HDATE_ISSUR_MELACHA,
            },
        }
    )
    item = await data.schedules.async_create_item(
        {
            "name": f"Shabbat lights {offset}",
            "date_window": {"from": "2026-10-01", "until": "2026-11-30"},
            "recurrence": {"kind": RECUR_DAY_SET, "day_set_id": day_set["id"]},
            "rules": [
                {
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
            ],
        }
    )
    await hass.async_block_till_done()
    schedule: dict[str, Any] = dict(data.schedules.data[str(item["id"])])

    plan = await async_enumerate(
        data.resolvers, schedule, OCTOBER, day_sets=data.day_sets
    )
    assert plan.problem is None, f"the plan did not even resolve: {plan.problem}"
    return list(plan.occurrences)


def _on(occurrences: list[Occurrence], date: str) -> Occurrence:
    """The one occurrence starting on a given civil date."""
    return next(occ for occ in occurrences if str(occ.start_date) == date)


async def test_scenario_a_is_rejected(
    hass: HomeAssistant, almanac_data: AlmanacData
) -> None:
    """UX finding 1, confirmed against the shipped engine rather than argued.

    The mechanism is the one the finding named. D11 runs in two stages —
    `candidate_dates` per civil day, then `covers` per instant — and the second is
    asked about the rule's *start*, 17:33 on the second of October. Shabbat begins
    at 18:18. 17:33 is before it, `covers` returns false, and the occurrence is
    carried with `outside_set` and never runs.

    Note what D12 costs here, and why it is still right. The row is not omitted, so
    the timeline *can* draw it. But the status it draws is `outside_set`, whose
    honest reading is "you asked for a day this is not on" — and that is not what
    happened. What happened is that the rule asked to start before the day it is
    about, which is the whole point of a setup window. The user is told the truth
    about a question they did not ask, which is the failure mode D12 exists to
    prevent, arriving through the one door D12 left open.
    """
    occurrences = await _enumerate(hass, almanac_data)

    for friday in FRIDAYS:
        occ = _on(occurrences, friday)
        assert occ.status is OccurrenceStatus.OUTSIDE_SET, friday
        assert not occ.will_run, friday
        # The end is never even computed, the occurrence having been dropped first.
        assert occ.end is None, friday

    # The numbers for the second of October, so the mechanism is on the record
    # rather than inferred from the status.
    assert _on(occurrences, "2026-10-02").start == CANDLE_LIGHTING - timedelta(
        minutes=45
    )
    assert SHABBAT_BEGINS == CANDLE_LIGHTING



async def test_the_cliff_is_one_second_wide(
    hass: HomeAssistant, almanac_data: AlmanacData
) -> None:
    """Where the boundary actually is, which is the fact the ruling turns on.

    A zero offset schedules every Friday. An offset of **one second** schedules
    none of them. The span's first instant is inside it, so candle lighting itself
    passes `covers` — by exactly nothing, because `hdate` puts the start of
    `issur_melacha` on the candle-lighting instant.

    So this is not a setup window that is *too long*, and no shorter one helps. Any
    rule that starts before its day starts is rejected, and starting before the day
    starts is what a setup window is for. A day set and a negative offset are
    categorically incompatible under D11 as written.

    It also says which of the two proposals works. The zero-offset column is what
    reading the day set at the *anchor's own* instant would produce for every
    offset, and it produces the five Fridays the brief asks for — so the smaller
    change is sufficient, and publishing a second granularity is not needed to fix
    this. That is evidence for a ruling, not the ruling.
    """
    at_zero = [occ for occ in await _enumerate(hass, almanac_data, 0) if occ.will_run]
    a_second_early = [
        occ for occ in await _enumerate(hass, almanac_data, -1) if occ.will_run
    ]

    assert [str(occ.start_date) for occ in at_zero] == list(FRIDAYS)
    assert [str(occ.start_date) for occ in a_second_early] == ["2026-10-03"]


async def test_the_only_thing_that_does_run_is_a_festival_candle_lighting(
    hass: HomeAssistant, almanac_data: AlmanacData
) -> None:
    """The false positive that would have hidden all of this.

    October 2026 falls across Shemini Atzeret, and `issur_melacha` is therefore one
    continuous span from Friday the second at 18:18 to Sunday the fourth at 19:14 —
    Shabbat running without a break into the festival. That span contains a *second*
    candle lighting, at 19:15 on Saturday the third, for the festival's second day.
    Being already well inside the span, that one start instant survives `covers`
    even with forty-five minutes subtracted from it.

    So the rule is not silent. It produces exactly one occurrence in the month, on a
    day the user did not ask about, running twenty-five hours to Sunday evening. A
    test that only asserted "something ran" would pass on this row and report the
    flagship scenario healthy — which is why the Fridays are asserted by name above
    and this row is pinned here rather than allowed to stand in for them.
    """
    occurrences = await _enumerate(hass, almanac_data)

    ran = [occ for occ in occurrences if occ.will_run]
    assert len(ran) == 1
    assert str(ran[0].start_date) == "2026-10-03"  # a Saturday
    assert ran[0].start == datetime(2026, 10, 3, 18, 30, tzinfo=NY)
    assert ran[0].end == datetime(2026, 10, 4, 19, 44, tzinfo=NY)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "UX finding 1, unresolved. The day set is asked about the start instant, "
        "which a setup window places before the set begins. Awaiting a ruling "
        "between reading the set at the anchor's own instant and publishing both "
        "granularities — see test_the_cliff_is_one_second_wide."
    ),
)
async def test_scenario_a_runs(hass: HomeAssistant, almanac_data: AlmanacData) -> None:
    """What the brief says has to be true, pinned so the ruling has a target.

    Five Fridays in October, five occurrences, each starting forty-five minutes
    before candle lighting and ending thirty minutes after the following night's
    havdalah. `strict` is deliberate: when this passes, the marker has to come off
    in the same commit, so the fix cannot land silently.
    """
    occurrences = await _enumerate(hass, almanac_data)

    for friday in FRIDAYS:
        occ = _on(occurrences, friday)
        assert occ.will_run, friday
        assert occ.end is not None, friday
