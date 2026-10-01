"""The `hdate` resolver — build step 7, and the contract's second opinion.

These tests are not primarily about the Jewish calendar. §15 put this step after
the engine because a contract is only proven by an implementation it was *not*
designed around, so what is under test here is §5: D10's date/datetime split,
D11's two stages, D88's exclusive end, and the padding `Window.days` grew when
the first three resolvers turned out to have been hiding a hole in it.

Every date below is written out. D64 again, for the usual reason: a suite that
sampled the clock could not tell whether the dry run and the live engine are one
code path, which is the property the whole design is arranged around.

**Why the dates are the dates.** Sukkot 5787 falls in the diaspora as erev Sukkot
on 2026-09-25 through Simchat Torah on 2026-10-04 — a ten-day contiguous
festival stretch, which is the longest merge any offering here produces and the
only realistic test of D88. Inside it, 2026-10-02 (erev Shmini Atzeret, a Friday)
opens an issur-melacha stretch that does not close until Saturday night on
2026-10-04: three civil days, one span. And 2026-10-06's `chatzot_halayla` lands
at 2026-10-07 00:44 local, which is the padding case.

The cross-check at the bottom is the point of the whole file. `hdate` ships
`Zmanim.issur_melacha_in_effect` — an independent second implementation of D11's
stage two — and this resolver deliberately does *not* call it. Comparing the two
across a fortnight is worth more than any assertion written from the design.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from hdate import Zmanim
from homeassistant.core import HomeAssistant

from custom_components.almanac.const import (
    ANCHOR_RESOLVER,
    CONF_DOMAIN,
    CONF_EDGE,
    CONF_KEY,
    CONF_KIND,
    CONF_OFFSET,
    EDGE_END,
    HDATE_CANDLE_LIGHTING,
    HDATE_CHATZOT_HALAYLA,
    HDATE_FESTIVAL,
    HDATE_HAVDALAH,
    HDATE_ISSUR_MELACHA,
    HDATE_ROSH_CHODESH,
    HDATE_SHKIA,
    HDATE_YOM_TOV,
    HDATE_ZMANIM,
    RESOLVER_HDATE,
)
from custom_components.almanac.resolver import (
    HDateResolver,
    HorizonKind,
    InvalidationKind,
    ResolverRegistry,
    Role,
    Unresolved,
    UnresolvedReason,
    Window,
    async_create_registry,
    async_forecast_anchor,
    ResolvedAnchor,
    async_resolve_anchor_on_date,
)

NY = ZoneInfo("America/New_York")

# New York, like `test_resolvers.py`, so the two files are talking about one
# place. Latitude matters more here than there: every zman is a solar depression
# angle, and a fixed site is what makes the times assertable at all.
LATITUDE = 40.7128
LONGITUDE = -74.0060

# Sukkot 5787 in the diaspora. See the module docstring.
SUKKOT_START = date(2026, 9, 25)
SUKKOT_END_EXCLUSIVE = date(2026, 10, 5)


def days(start: date, count: int) -> Window:
    """A window of whole local civil days, written out rather than derived."""
    return Window(
        datetime.combine(start, time(), tzinfo=NY),
        datetime.combine(start + timedelta(days=count), time(), tzinfo=NY),
    )


def at(moment: str) -> datetime:
    """A local instant, from an ISO string, so the test reads as a wall clock."""
    return datetime.fromisoformat(moment).replace(tzinfo=NY)


@pytest.fixture(autouse=True)
async def _located(hass: HomeAssistant) -> None:
    """Put `hass` in New York. Location is the whole of this resolver's input."""
    await hass.config.async_set_time_zone("America/New_York")
    await hass.config.async_update(
        latitude=LATITUDE, longitude=LONGITUDE, elevation=0
    )


@pytest.fixture
def hdate(hass: HomeAssistant) -> HDateResolver:
    """The resolver alone, for the assertions that are about it and not the wiring.

    `diaspora=True` is pinned rather than inferred: the inference is tested
    separately, and every date in this file is a diaspora date — Simchat Torah on
    the 4th rather than the 3rd is exactly the difference the flag makes.
    """
    return HDateResolver(hass, diaspora=True)


@pytest.fixture
def resolvers(hass: HomeAssistant) -> ResolverRegistry:
    """The registry as the config entry builds it, `hdate` now included."""
    return async_create_registry(hass)


# --- the offering list (D8, D9, D16, D86) ----------------------------------


async def test_registered_with_the_others(resolvers: ResolverRegistry) -> None:
    """Step 7's only change to the wiring: `hdate` is in the registry."""
    assert RESOLVER_HDATE in resolvers.async_domains()


async def test_selectable_with_a_fixed_list(hdate: HDateResolver) -> None:
    """D86: a non-empty `offerings()` is what makes a resolver pickable.

    Eighteen zmanim, two transitions, the melacha interval and five holiday sets.
    The count is asserted so that adding an offering is a deliberate act — D9
    makes every key here a permanent compatibility surface, and the cheapest
    place to notice one appearing by accident is before it ships.
    """
    offerings = hdate.offerings()
    assert len(offerings) == 26
    assert len({offering.key for offering in offerings}) == 26


async def test_every_declared_zman_exists_in_the_library(
    hdate: HDateResolver,
) -> None:
    """The guard on D9's promise, checked against the installed `hdate`.

    `HDATE_ZMANIM` is our copy of the library's key set, and a copy is a thing
    that drifts. If a future pin renames a zman, this fails here — loudly, in one
    place — rather than turning into a `KeyError` inside a forecast for one user
    whose schedule happened to use it.
    """
    published = set(Zmanim(date=date(2026, 10, 1), location=hdate.location).zmanim)
    assert set(HDATE_ZMANIM) <= published


async def test_keys_are_machine_names_not_display_text(
    hdate: HDateResolver,
) -> None:
    """D8, and the reason it is not merely tidy.

    `hdate.translator` holds one process-global language, which core's
    `jewish_calendar` coordinator sets from *its* config entry. A key that was a
    rendered string would therefore change meaning because an unrelated
    integration was configured in Hebrew. Keys are ASCII slugs; the human text
    lives in `display_name` and is free to be translated.
    """
    for offering in hdate.offerings():
        assert offering.key.replace("_", "").isalnum()
        assert offering.key.islower()
        assert offering.display_name != offering.key


async def test_roles_split_instants_from_intervals(hdate: HDateResolver) -> None:
    """§5.1: an instant is an anchor; only a thing with extent is a day set.

    A zman cannot be a day set because "does sunset contain 22:00" has an answer
    — no — that is indistinguishable from a day set correctly out of force, and
    D11 would then have two stages that agree on nothing.
    """
    zman = hdate.offering(HDATE_SHKIA)
    assert zman is not None
    assert zman.roles == frozenset({Role.ANCHOR})

    melacha = hdate.offering(HDATE_ISSUR_MELACHA)
    assert melacha is not None
    assert melacha.roles == {Role.ANCHOR, Role.DAY_SET}


async def test_day_set_role_refused_for_a_zman(
    resolvers: ResolverRegistry,
) -> None:
    """The registry enforces §5.1 rather than trusting the caller (D11)."""
    refusal = await resolvers.async_covers(
        RESOLVER_HDATE, HDATE_SHKIA, at("2026-10-01T22:00")
    )
    assert isinstance(refusal, Unresolved)
    assert refusal.reason is UnresolvedReason.ROLE_NOT_OFFERED


async def test_horizon_is_unbounded_and_invalidation_deterministic(
    resolvers: ResolverRegistry,
) -> None:
    """D13 and D43: arithmetic all the way down, so no watch and no bound.

    Worth asserting together, because the pair is only honest if the resolver's
    inputs really are the core config. The provisional constructor arguments are
    what keeps that true; see `HDateResolver.__init__`.
    """
    offering = resolvers.async_offering(RESOLVER_HDATE, HDATE_ISSUR_MELACHA)
    assert not isinstance(offering, Unresolved)
    assert offering.horizon.kind is HorizonKind.UNBOUNDED

    signal = resolvers.async_invalidation(RESOLVER_HDATE, HDATE_ISSUR_MELACHA)
    assert not isinstance(signal, Unresolved)
    assert signal.kind is InvalidationKind.DETERMINISTIC
    assert not signal.entity_ids


# --- zmanim: zero-length datetime spans, and the padding they needed -------


async def test_a_zman_is_a_zero_length_datetime_span(
    hdate: HDateResolver,
) -> None:
    """D10's datetime half. An instant is a span whose edges coincide."""
    spans = await hdate.forecast(HDATE_SHKIA, days(date(2026, 10, 1), 1))
    assert len(spans) == 1
    assert not spans[0].is_all_day
    assert spans[0].start == spans[0].end


async def test_a_zman_that_lands_on_the_following_civil_day(
    hdate: HDateResolver,
) -> None:
    """The hole in `Window.days` that step 7 found, stated as a fact.

    `chatzot_halayla` is halachic midnight, computed from the *preceding* civil
    date: 2026-10-06's lands at 2026-10-07 00:44 local. Without `pad_before` the
    window over 7 October computes only 7 October's own (8 October 00:44,
    outside the window), returns nothing, and — under an UNBOUNDED horizon —
    reports that nothing as *known*. A confident wrong answer, which is the one
    outcome §5.5 is built to prevent.
    """
    spans = await hdate.forecast(HDATE_CHATZOT_HALAYLA, days(date(2026, 10, 7), 1))
    assert [span.start for span in spans] == [at("2026-10-07T00:44")]


async def test_a_zman_forecast_is_one_per_day(hdate: HDateResolver) -> None:
    """The padding widens the computation, not the answer.

    The point of the previous test is a span the window would otherwise miss;
    the point of this one is that reaching a day either side does not smuggle in
    a second span per day. `Window.contains` is what keeps them apart, and the
    two tests together are what make the padding a fix rather than a trade.
    """
    spans = await hdate.forecast(HDATE_SHKIA, days(date(2026, 10, 5), 7))
    assert len(spans) == 7
    assert len({span.start.date() for span in spans}) == 7


async def test_candle_lighting_is_absent_on_an_ordinary_weekday(
    hdate: HDateResolver,
) -> None:
    """A *known* nothing, not a gap — the distinction D17 and §5.5 both turn on.

    Tuesday 2026-10-13 has no candle lighting, and the forecast says so by
    returning no span while still declaring an UNBOUNDED horizon. That is a
    different statement from `Unresolved`, and the timeline renders them
    differently: empty versus unknown.
    """
    assert not await hdate.forecast(HDATE_CANDLE_LIGHTING, days(date(2026, 10, 13), 1))


async def test_candle_lighting_and_havdalah_bracket_a_plain_shabbat(
    hdate: HDateResolver,
) -> None:
    """2026-10-09 is an ordinary Friday: lighting in, havdalah out the next night."""
    window = days(date(2026, 10, 9), 2)
    lighting = await hdate.forecast(HDATE_CANDLE_LIGHTING, window)
    havdalah = await hdate.forecast(HDATE_HAVDALAH, window)
    assert len(lighting) == 1
    assert len(havdalah) == 1
    assert lighting[0].start.date() == date(2026, 10, 9)
    assert havdalah[0].start.date() == date(2026, 10, 10)
    assert lighting[0].start < havdalah[0].start


# --- issur melacha: the first span with an interior (§5.4, D11) ------------


async def test_a_plain_shabbat_is_one_span_across_two_days(
    hdate: HDateResolver,
) -> None:
    """One stretch, not two. The Friday candle lighting opens it; Saturday does not.

    A day is the start of a stretch only if it is not itself Shabbat or yom tov,
    which is what stops the mid-chain lighting on the second night of a chag from
    opening a second, overlapping span.
    """
    spans = await hdate.forecast(HDATE_ISSUR_MELACHA, days(date(2026, 10, 9), 2))
    assert len(spans) == 1
    assert spans[0].start.date() == date(2026, 10, 9)
    assert spans[0].end.date() == date(2026, 10, 10)
    assert not spans[0].is_all_day


async def test_a_chag_running_into_shabbat_is_one_three_day_span(
    hdate: HDateResolver,
) -> None:
    """Shmini Atzeret into Shabbat: 2026-10-02 evening to 2026-10-04 night.

    This is what a resolver has to be able to say and a `weekdays` list cannot.
    Upstream's model has no shape for it at all — the stretch is not a weekday,
    not a date range, and not a fixed number of hours — and "the whole of yom
    tov" is the single most common thing the motivating user wants to schedule
    around.
    """
    spans = await hdate.forecast(HDATE_ISSUR_MELACHA, days(date(2026, 10, 2), 3))
    assert len(spans) == 1
    assert spans[0].start.date() == date(2026, 10, 2)
    assert spans[0].end.date() == date(2026, 10, 4)


async def test_the_two_stages_disagree_on_purpose(
    resolvers: ResolverRegistry,
) -> None:
    """§5.4's worked example, and the reason D11 has two stages at all.

    "At 22:00, on Shabbat" over a plain weekend: the coarse stage offers Friday
    *and* Saturday, because the span touches both civil days, and the precise
    stage keeps only Friday — 22:00 on Saturday is after havdalah. A one-stage
    day set fires twice, which is the bug §5.4 names.
    """
    candidates = await resolvers.async_candidate_dates(
        RESOLVER_HDATE, HDATE_ISSUR_MELACHA, days(date(2026, 10, 9), 2)
    )
    assert candidates == [date(2026, 10, 9), date(2026, 10, 10)]

    assert await resolvers.async_covers(
        RESOLVER_HDATE, HDATE_ISSUR_MELACHA, at("2026-10-09T22:00")
    )
    assert not await resolvers.async_covers(
        RESOLVER_HDATE, HDATE_ISSUR_MELACHA, at("2026-10-10T22:00")
    )


async def test_covers_holds_in_the_middle_of_shabbat(
    resolvers: ResolverRegistry,
) -> None:
    """The other half of the padding fix, and the sharper half.

    `BaseResolver.covers` forecasts the instant's *own civil day*, trusting
    `Span.overlaps`' promise that a span already running at `window.start` comes
    back. Nothing kept that promise before step 7, because every span the first
    three resolvers produce is zero-length. Saturday 10:00 is squarely inside a
    stretch that began on Friday evening, and without `pad_before` this answered
    `False` — the middle of Shabbat, reported as not Shabbat.
    """
    assert await resolvers.async_covers(
        RESOLVER_HDATE, HDATE_ISSUR_MELACHA, at("2026-10-10T10:00")
    )


async def test_covers_agrees_with_the_library_across_a_fortnight(
    hdate: HDateResolver,
) -> None:
    """The independent second opinion — the most valuable test in the file.

    `Zmanim.issur_melacha_in_effect` is hdate's own implementation of exactly the
    question D11's stage two asks, and this resolver deliberately does not call
    it: `BaseResolver.covers` derives the answer from `forecast`, so the two
    stages cannot drift apart. That leaves the library free to be used here as a
    check on the derivation.

    A fortnight spanning Sukkot, every three hours: 112 instants, four distinct
    melacha stretches including the three-day one. An agreement this wide is the
    closest thing to proof that the span construction is right, because the two
    sides share no code below `Zmanim`.
    """
    for offset in range(14):
        day = date(2026, 9, 25) + timedelta(days=offset)
        zmanim = Zmanim(date=day, location=hdate.location)
        for hour in range(0, 24, 3):
            instant = datetime.combine(day, time(hour), tzinfo=NY)
            assert await hdate.covers(HDATE_ISSUR_MELACHA, instant) is (
                zmanim.issur_melacha_in_effect(instant)
            )


# --- holiday sets: D10's date half, and D88's exclusive end ----------------


async def test_a_festival_stretch_is_one_date_typed_span(
    hdate: HDateResolver,
) -> None:
    """D10's date half, D88's exclusive end, and §5.6's open question, closed.

    Sukkot 5787 in the diaspora runs 25 September to 4 October inclusive — erev
    Sukkot, two days of yom tov, chol hamoed, Shmini Atzeret, Simchat Torah. Ten
    contiguous days, merged into one span whose `end` is the 5th: the first day
    *not* in the set. §5.6 asked step 7 to confirm the convention against a real
    multi-day source, and this is it.
    """
    spans = await hdate.forecast(HDATE_FESTIVAL, days(date(2026, 9, 28), 3))
    assert len(spans) == 1
    assert spans[0].is_all_day
    assert spans[0].start == SUKKOT_START
    assert spans[0].end == SUKKOT_END_EXCLUSIVE


async def test_a_window_inside_a_run_still_gets_the_whole_run(
    hdate: HDateResolver,
) -> None:
    """Why both ends are padded, and why truncation would be worse than omission.

    A window landing in the middle of chol hamoed has to reach backwards to find
    where the festival began and forwards to find where it ends. Without the
    padding the span would not go missing — it would be *shortened*, and a
    shortened span reports the window's own edge as a festival boundary. A
    fabricated fact reads as true; a missing one reads as missing.
    """
    narrow = await hdate.forecast(HDATE_FESTIVAL, days(date(2026, 9, 30), 1))
    assert len(narrow) == 1
    assert narrow[0].start == SUKKOT_START
    assert narrow[0].end == SUKKOT_END_EXCLUSIVE


async def test_yom_tov_alone_is_several_spans(hdate: HDateResolver) -> None:
    """Why `festival` is a union and not a synonym.

    Yom tov inside Sukkot is two days at the start and two at the end, with chol
    hamoed between — so the same fortnight yields separate spans rather than one.
    Both answers are true; they are answers to different questions, which is
    precisely the argument for offering both keys instead of picking one.
    """
    spans = await hdate.forecast(HDATE_YOM_TOV, days(date(2026, 9, 25), 10))
    assert len(spans) > 1
    assert all(span.is_all_day for span in spans)
    assert all(span.end > span.start for span in spans)


async def test_a_single_day_set_spans_one_day(hdate: HDateResolver) -> None:
    """The degenerate merge: a one-day run is `[day, day + 1)`, never empty.

    Rosh Chodesh Cheshvan 5787 falls on 2026-10-11 and 2026-10-12 — two days —
    but the shape under test is the arithmetic, and a half-open span of one civil
    day is where an off-by-one in the merge would show first. A zero-extent span
    is exactly the encoding A.1 says core's own calendar gets wrong and DESIGN
    says not to mirror.
    """
    spans = await hdate.forecast(HDATE_ROSH_CHODESH, days(date(2026, 10, 1), 31))
    assert spans
    for span in spans:
        assert span.end > span.start
        assert (span.end - span.start).days >= 1


async def test_candidate_dates_of_a_date_typed_span_exclude_the_end(
    resolvers: ResolverRegistry,
) -> None:
    """D88 as the engine sees it: the exclusive end is not one of the days.

    This is where getting the convention wrong would actually bite — a day set
    claiming the day after Simchat Torah. `candidate_dates` derives the list from
    the span, so the contract's own subtraction is under test here and not a
    reimplementation of it.
    """
    candidates = await resolvers.async_candidate_dates(
        RESOLVER_HDATE, HDATE_FESTIVAL, days(date(2026, 9, 28), 3)
    )
    assert candidates is not None
    assert not isinstance(candidates, Unresolved)
    assert candidates[0] == SUKKOT_START
    assert candidates[-1] == SUKKOT_END_EXCLUSIVE - timedelta(days=1)
    assert len(candidates) == 10


# --- anchors over hdate (D6, D7, D89) --------------------------------------


async def test_end_edge_of_a_date_typed_span_is_the_exclusive_midnight(
    resolvers: ResolverRegistry,
) -> None:
    """D89, checked on the first offering that can actually exercise it.

    `anchor._edge` has had an `edge: end` branch since step 2 and every span in
    the tree was zero-length, so both edges agreed and the branch was untestable.
    Here they differ by ten days: the end edge of the Sukkot span is 5 October
    00:00 local — the exclusive boundary as an instant, which is what D89 says a
    date-typed end resolves to.
    """
    forecast = await async_forecast_anchor(
        resolvers,
        {
            CONF_KIND: ANCHOR_RESOLVER,
            CONF_DOMAIN: RESOLVER_HDATE,
            CONF_KEY: HDATE_FESTIVAL,
            CONF_EDGE: EDGE_END,
        },
        days(date(2026, 9, 28), 10),
    )
    assert not isinstance(forecast, Unresolved)
    assert forecast.instants == (
        datetime.combine(SUKKOT_END_EXCLUSIVE, time(), tzinfo=NY),
    )


async def test_an_offset_anchor_on_candle_lighting(
    resolvers: ResolverRegistry, hdate: HDateResolver
) -> None:
    """The motivating case, end to end: forty-five minutes before candle lighting.

    Upstream refuses this shape outright (component#154) and it is the reason
    this project exists. Note what the user supplies: a key from a fixed list and
    a number of minutes — no template, no sensor, no script.
    """
    window = days(date(2026, 10, 9), 1)
    lighting = (await hdate.forecast(HDATE_CANDLE_LIGHTING, window))[0].start

    forecast = await async_forecast_anchor(
        resolvers,
        {
            CONF_KIND: ANCHOR_RESOLVER,
            CONF_DOMAIN: RESOLVER_HDATE,
            CONF_KEY: HDATE_CANDLE_LIGHTING,
            CONF_OFFSET: -45 * 60,
        },
        window,
    )
    assert not isinstance(forecast, Unresolved)
    assert forecast.instants == (lighting - timedelta(minutes=45),)


async def test_resolving_an_anchor_on_one_civil_date(
    resolvers: ResolverRegistry,
) -> None:
    """D1's shape: the instant for a date the recurrence picked, or a known `None`.

    The `None` is the half worth testing. Recurrence asks about a date; most
    dates have no havdalah; and "this date has none" has to be distinguishable
    from "the resolver is broken", because the first is a schedule that correctly
    does nothing and the second is one the user needs to be told about (D12).
    """
    anchor = {
        CONF_KIND: ANCHOR_RESOLVER,
        CONF_DOMAIN: RESOLVER_HDATE,
        CONF_KEY: HDATE_HAVDALAH,
    }
    saturday = await async_resolve_anchor_on_date(
        resolvers, anchor, date(2026, 10, 10)
    )
    assert isinstance(saturday, ResolvedAnchor)
    assert saturday.at.date() == date(2026, 10, 10)
    # No offset on this anchor, so D122's two instants coincide. That is the case
    # every anchor in build steps 1-6 was, and it is why one value sufficed for
    # as long as it did.
    assert saturday.source == saturday.at

    assert (
        await async_resolve_anchor_on_date(resolvers, anchor, date(2026, 10, 13))
        is None
    )


# --- configuration, and the one thing step 7 left provisional --------------


async def test_diaspora_changes_the_answer(hass: HomeAssistant) -> None:
    """Not a setting: Simchat Torah moves by a day, and so does the melacha span.

    Asserted because it is the argument for asking the user rather than guessing.
    In Israel the 2026-10-02 stretch closes on Saturday night too, but the
    festival run is nine days rather than ten — a difference a user notices the
    first time a schedule fires, which is one occurrence too late.
    """
    window = days(date(2026, 9, 28), 3)
    diaspora = await HDateResolver(hass, diaspora=True).forecast(
        HDATE_FESTIVAL, window
    )
    israel = await HDateResolver(hass, diaspora=False).forecast(HDATE_FESTIVAL, window)
    assert diaspora[0].end != israel[0].end


async def test_diaspora_is_inferred_from_the_core_config(
    hass: HomeAssistant,
) -> None:
    """The provisional inference, stated so a later options flow can replace it.

    Two signals, either sufficient: `country` is authoritative but often unset,
    while an installation in Israel invariably sets `Asia/Jerusalem` because
    nothing else works. Defaulting to diaspora is the safer error — the extra
    day is a superset, so a wrong guess adds an occurrence the user can see and
    remove rather than silently removing one they never learn about.
    """
    assert HDateResolver(hass).diaspora is True

    await hass.config.async_set_time_zone("Asia/Jerusalem")
    assert HDateResolver(hass).diaspora is False


async def test_offsets_reach_the_library(hass: HomeAssistant) -> None:
    """The other two provisional settings, proven to be wired rather than ignored.

    Both are minutes and both move a real boundary. They have no user surface
    yet — D65 gives almanac one config entry and no options flow — so the test is
    what stops them being decorative until step 9 decides where they live.
    """
    window = days(date(2026, 10, 9), 2)
    default = await HDateResolver(hass, diaspora=True).forecast(
        HDATE_ISSUR_MELACHA, window
    )
    adjusted = await HDateResolver(
        hass, diaspora=True, candle_lighting_offset=40, havdalah_offset=72
    ).forecast(HDATE_ISSUR_MELACHA, window)

    assert adjusted[0].start < default[0].start
    assert adjusted[0].end > default[0].end
