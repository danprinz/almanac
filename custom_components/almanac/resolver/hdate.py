"""The `hdate` resolver — build step 7, and the contract's second opinion.

§15 puts this step after the engine on purpose: *a contract is not proven by the
implementation it was designed around*. `sun`, `clock` and `entity_time` all
produce zero-length spans on the civil day they were computed for, with no
day-set role between them, so three quarters of §5 went untested. This module is
the part of the design that was written from the Jewish calendar and then had to
be implemented against it.

**What it exercises, and what that turned up.**

- **D10's date/datetime split.** Three shapes come out of one `forecast`: a
  zero-length datetime span (a zman), a datetime span with real extent (issur
  melacha, Friday evening to Saturday night), and a *date*-typed span (a festival
  stretch). `sun` returns only the first. The split held; see `_holiday_spans`
  for why the third is not a cop-out.
- **D11's two stages.** `issur_melacha` is §5.4's worked example made real —
  `candidate_dates` yields Friday *and* Saturday, and `covers` keeps only the
  Friday 22:00. The stages are inherited from `BaseResolver` unchanged, which is
  the strongest thing that can be said for them: hdate ships
  `Zmanim.issur_melacha_in_effect(instant)`, an exact second implementation of
  stage two, and it is used in the tests as a cross-check rather than here as a
  shortcut. Two implementations that agree today is how they drift.
- **D88 / D89's exclusive end.** A ten-day Sukkot stretch in the diaspora runs
  25 September to 4 October and this resolver returns
  `Span(date(2026, 9, 25), date(2026, 10, 5))`. §5.6 asked step 7 to confirm the
  convention; it does, and the `edge: end` anchor on that span resolves to
  5 October 00:00 local exactly as D89 says.
- **Where the contract was short.** `Window.days()` yielded only the days a
  window touches, which is correct for a zero-length span computed from its own
  day and wrong for everything here — an interval still running at
  `window.start`, and a zman like `chatzot_halayla` that is computed from
  6 October and lands on 7 October. The padding now lives on `Window` rather
  than in this file, because it is a property of the contract and not of this
  calendar.

**D8, and why it is the strongest argument in §4.** Nothing below reads a display
string out of the library. `Holiday.name` is a machine key (`shmini_atzeret`);
`str(holiday)` is translated, and translated *globally* — `hdate.translator`
holds one process-wide language, which core's `jewish_calendar` coordinator sets
from its own config entry. A resolver that matched on rendered text would
therefore break when an unrelated integration was configured in Hebrew. Matching
on keys makes that unthinkable rather than merely unlikely.

**D64.** No clock. Every entry point takes the window or the instant it is asked
about. Worth stating twice here because `hdate.Zmanim`'s `date` field defaults to
`date.today()` and `HDateInfo`'s to the same, so every construction below passes
a date explicitly — an omission would not fail, it would silently answer about
today.

**Verified against the installed packages** (`hdate[astral]==1.2.1`, the same pin
core's `jewish_calendar/manifest.json` carries) rather than assumed:
`Zmanim.zmanim` is a `dict[str, Zman]` whose eighteen keys are the tuple in
`const.py`; `Zman.local` is an aware datetime in `Location.timezone`;
`Zmanim.candle_lighting` and `Zmanim.havdalah` are `datetime | None` properties
that already fold in the yom-tov chain; `HDateInfo.holidays` is a list of
`Holiday` with `.name` and `.type: HolidayTypes`. The structural claims are the
ones this environment can make (Appendix B) — a key set, a signature, a `None`.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Final

from hdate import HDateInfo, Location, Zmanim
from hdate.hebrew_date import is_shabbat
from hdate.holidays import HolidayTypes, is_yom_tov
from homeassistant.core import HomeAssistant

from ..const import (
    HDATE_CANDLE_LIGHTING,
    HDATE_CHOL_HAMOED,
    HDATE_DEFAULT_CANDLE_LIGHTING_OFFSET,
    HDATE_DEFAULT_HAVDALAH_OFFSET,
    HDATE_FAST_DAY,
    HDATE_FESTIVAL,
    HDATE_HAVDALAH,
    HDATE_HOLIDAY_PAD_DAYS,
    HDATE_ISSUR_MELACHA,
    HDATE_MELACHA_PAD_DAYS,
    HDATE_ROSH_CHODESH,
    HDATE_YOM_TOV,
    HDATE_ZMAN_PAD_DAYS,
    HDATE_ZMANIM,
    RESOLVER_HDATE,
)
from .contract import (
    BaseResolver,
    Horizon,
    InvalidationKind,
    InvalidationSignal,
    Offering,
    Role,
    Span,
    Window,
)

# D8 — machine key on the left, display text on the right, and the two are
# separate strings so that translating the second can never reach a stored
# schedule. Written out in English here rather than pulled from the library
# precisely because the library's rendering is language-global (see the module
# docstring); these become `strings.json` entries when the UI lands at step 9.
_ZMAN_NAMES: Final[dict[str, str]] = {
    "alot_hashachar": "Alot hashachar (dawn)",
    "talit_and_tefillin": "Talit and tefillin",
    "netz_hachama": "Netz hachama (sunrise)",
    "sof_zman_shema_mga": 'Sof zman shema (Mg"A)',
    "sof_zman_shema_gra": 'Sof zman shema (Gr"A)',
    "sof_zman_tfilla_mga": 'Sof zman tefilla (Mg"A)',
    "sof_zman_tfilla_gra": 'Sof zman tefilla (Gr"A)',
    "chatzot_hayom": "Chatzot hayom (midday)",
    "mincha_gedola": "Mincha gedola",
    "mincha_gedola_30min": "Mincha gedola (30 minutes)",
    "mincha_ketana": "Mincha ketana",
    "plag_hamincha": "Plag hamincha",
    "shkia": "Shkia (sunset)",
    "tset_hakohavim": "Tset hakohavim",
    "tset_hakohavim_tsom": "Tset hakohavim (fast)",
    "tset_hakohavim_shabbat": "Tset hakohavim (Shabbat)",
    "tset_hakohavim_rabeinu_tam": "Tset hakohavim (Rabbeinu Tam)",
    "chatzot_halayla": "Chatzot halayla (midnight)",
}

_TRANSITION_NAMES: Final[dict[str, str]] = {
    HDATE_CANDLE_LIGHTING: "Candle lighting",
    HDATE_HAVDALAH: "Havdalah",
}

# The date-granular sets, each a set of `HolidayTypes` a civil day must carry.
#
# `festival` is a union of three rather than a fourth type of its own, and that
# is the offering D88 is tested against: erev Sukkot through Simchat Torah is one
# contiguous ten-day stretch in the diaspora, and it is the stretch a user means
# by "over Sukkot". Yom tov alone would be four separate two-day spans with the
# intermediate days missing, which is a true answer to a question nobody asked.
_HOLIDAY_SETS: Final[dict[str, frozenset[HolidayTypes]]] = {
    HDATE_YOM_TOV: frozenset({HolidayTypes.YOM_TOV}),
    HDATE_CHOL_HAMOED: frozenset({HolidayTypes.HOL_HAMOED}),
    HDATE_FESTIVAL: frozenset(
        {
            HolidayTypes.EREV_YOM_TOV,
            HolidayTypes.YOM_TOV,
            HolidayTypes.HOL_HAMOED,
        }
    ),
    HDATE_FAST_DAY: frozenset({HolidayTypes.FAST_DAY}),
    HDATE_ROSH_CHODESH: frozenset({HolidayTypes.ROSH_CHODESH}),
}

_HOLIDAY_SET_NAMES: Final[dict[str, str]] = {
    HDATE_YOM_TOV: "Yom tov",
    HDATE_CHOL_HAMOED: "Chol hamoed",
    HDATE_FESTIVAL: "Festival (erev, yom tov and chol hamoed)",
    HDATE_FAST_DAY: "Fast day",
    HDATE_ROSH_CHODESH: "Rosh chodesh",
}

# How many civil days forward the walk from a candle lighting may look for the
# havdalah that closes it. The longest real stretch is Wednesday evening to
# Saturday night — a two-day chag running into Shabbat — which is four days
# inclusive of the day the stretch begins on. Bounded rather than unbounded for
# D17's reason: a library that stopped producing a havdalah would otherwise turn
# one offering into a loop to the end of time rather than into one unresolved
# anchor.
_MELACHA_CHAIN_DAYS: Final = 6


class HDateResolver(BaseResolver):
    """The Jewish calendar: zmanim, the issur-melacha interval, and holiday sets.

    Selectable in D86's sense — it publishes a pick-list — and D16's list is
    genuinely fixed: twenty-six offerings, none of them parsed from user input.
    That is the whole point of a resolver rather than a template. "Forty-five
    minutes before candle lighting" is `{domain: hdate, key: candle_lighting,
    offset: -45m}`, and the thing the user typed is a number of minutes.
    """

    domain = RESOLVER_HDATE

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        diaspora: bool | None = None,
        candle_lighting_offset: int = HDATE_DEFAULT_CANDLE_LIGHTING_OFFSET,
        havdalah_offset: int = HDATE_DEFAULT_HAVDALAH_OFFSET,
    ) -> None:
        """Hold the three settings hdate needs beyond latitude and longitude.

        **Provisional, and the one thing in step 7 that wants an owner ruling.**
        Core's `jewish_calendar` asks for all three in its config flow; almanac
        has no options flow at all (D65 — one config entry, nothing
        per-instance), so they arrive as constructor arguments with core's own
        defaults and no user surface. Two alternatives were considered and
        rejected for now:

        - *Read them off a loaded `jewish_calendar` config entry.* Best fidelity
          and no new surface, but D43's `InvalidationSignal` can say only
          "deterministic" or "watch these entities", and neither describes
          "another integration's options changed" — so the resolver would either
          declare a horizon it could not keep or serve a cached answer that had
          gone stale, and both are the failure §5.5 exists to prevent.
        - *Add almanac options now.* That is a UI surface, and §15 puts the UI at
          step 9; deciding its shape from inside a resolver is how a config key
          becomes permanent by accident (D9).

        `diaspora=None` means *infer*, and the inference is stated rather than
        defaulted: outside Israel a second day of yom tov is observed, which
        changes both the holiday sets and where the issur-melacha stretches end.
        Guessing wrong is visible — Simchat Torah on the wrong day — which is an
        argument for asking, not for picking the commoner answer silently.
        """
        super().__init__(hass)
        self._diaspora = diaspora
        self._candle_lighting_offset = candle_lighting_offset
        self._havdalah_offset = havdalah_offset

    # --- the pick-list (D16, D86) ------------------------------------------

    def offerings(self) -> list[Offering]:
        """D16's fixed, enumerable list — every key this resolver answers to."""
        return [
            offering
            for key in (
                *HDATE_ZMANIM,
                *_TRANSITION_NAMES,
                HDATE_ISSUR_MELACHA,
                *_HOLIDAY_SETS,
            )
            if (offering := self.offering(key)) is not None
        ]

    def offering(self, key: str) -> Offering | None:
        """Describe one key, or `None` for one this resolver does not know.

        The roles differ by shape and the difference is the substance of §5.1.

        A zman is an *instant*, so it is anchor-only: asking whether an instant
        covers another instant has an answer — no — and that answer is
        indistinguishable from a day set that is out of force, which is why the
        registry refuses the question rather than answering it (D11, and
        `registry._for_role`).

        `issur_melacha` and the holiday sets carry **both** roles, and that is
        not generosity. §5.4's authoring guidance says "the whole of Shabbat" is
        better written as a `During` interval between the two edges than as a day
        set, and an offering that could not serve as an anchor would make the
        better shape unavailable while leaving the worse one.
        """
        if key in _ZMAN_NAMES:
            return Offering(
                key=key,
                display_name=_ZMAN_NAMES[key],
                roles=frozenset({Role.ANCHOR}),
                horizon=Horizon.unbounded(),
            )
        if key in _TRANSITION_NAMES:
            return Offering(
                key=key,
                display_name=_TRANSITION_NAMES[key],
                roles=frozenset({Role.ANCHOR}),
                horizon=Horizon.unbounded(),
            )
        if key == HDATE_ISSUR_MELACHA:
            return Offering(
                key=key,
                display_name="Shabbat and yom tov (issur melacha)",
                roles=frozenset({Role.ANCHOR, Role.DAY_SET}),
                horizon=Horizon.unbounded(),
            )
        if key in _HOLIDAY_SETS:
            return Offering(
                key=key,
                display_name=_HOLIDAY_SET_NAMES[key],
                roles=frozenset({Role.ANCHOR, Role.DAY_SET}),
                horizon=Horizon.unbounded(),
            )
        return None

    # --- the contract (§5.2) -----------------------------------------------

    async def forecast(self, key: str, window: Window) -> list[Span]:
        """Enumerate the window, in whichever of D10's two shapes the key has.

        One dispatch rather than three resolvers because D9 makes a domain
        permanent and "the Jewish calendar" is one source however many shapes it
        publishes. The engine never sees this branch: it receives spans, and
        `Span.is_all_day` is the only distinction that reaches it.
        """
        if key in _ZMAN_NAMES:
            return self._zman_spans(key, window)
        if key in _TRANSITION_NAMES:
            return self._transition_spans(key, window)
        if key == HDATE_ISSUR_MELACHA:
            return self._melacha_spans(window)
        if key in _HOLIDAY_SETS:
            return self._holiday_spans(key, window)
        # Not reachable through the registry, which checks the offering first.
        # Here because `BaseResolver.forecast` is abstract and a resolver called
        # directly should say no rather than return a confident empty list.
        raise ValueError(f"{self.domain} does not offer {key!r}")

    def invalidation(self, key: str) -> InvalidationSignal:
        """Deterministic (D43): nothing but the core config moves these answers.

        Latitude, longitude, elevation and the civil timezone are the whole of
        the astronomical input, and all four arrive on `EVENT_CORE_CONFIG_UPDATE`
        — which the registry is already subscribing a deterministic signal to.
        The Hebrew calendar itself is arithmetic.

        This is honest only because of the constructor's decision above. Were the
        offsets read from another integration's config entry, this declaration
        would be a lie the cache would then act on, and D43 has no third kind to
        tell the truth with.
        """
        return InvalidationSignal(InvalidationKind.DETERMINISTIC)

    # --- the three span shapes ---------------------------------------------

    def _zman_spans(self, key: str, window: Window) -> list[Span]:
        """One zero-length span per civil day the zman is computed for (D10).

        `pad_before=1` is not defensive. `Zman` is built as *UTC midnight of the
        civil date plus a number of minutes*, so a zman late in the halachic day
        can land on the following local date: `chatzot_halayla` computed for
        6 October in New York is 7 October 00:44. Without the pad, a window
        covering only 7 October computes 7 October's chatzot halayla (8 October
        00:44, outside), finds nothing, and reports a *known* nothing — the worst
        available answer, because the horizon is UNBOUNDED and the caller is
        entitled to believe it.
        """
        spans: list[Span] = []
        for day in window.days(self.zone, HDATE_ZMAN_PAD_DAYS, HDATE_ZMAN_PAD_DAYS):
            instant = self._zmanim(day).zmanim[key].local
            if window.contains(instant):
                spans.append(Span(instant, instant))
        return spans

    def _transition_spans(self, key: str, window: Window) -> list[Span]:
        """Candle lighting or havdalah, on the days there is one.

        Most days there is not, and that is a *known* nothing rather than a gap
        in knowledge — the same shape as `sun` on a polar day. `hdate` returns
        `None` for both on an ordinary Tuesday, and returns `None` for havdalah
        in the middle of a chag chain deliberately, so that nothing reads as
        "melacha is permitted from here".

        Note what that means for `candle_lighting` as an *anchor*: on the second
        night of a two-day chag hdate reports the havdalah time under the
        candle-lighting name, because that is when the candles are lit. This
        resolver passes that through unchanged rather than correcting it. The
        alternative — suppressing mid-chain candle lighting — would make "twenty
        minutes before candle lighting" silently skip the night of the chag,
        which is precisely the night it is most often wanted.
        """
        spans: list[Span] = []
        for day in window.days(self.zone, HDATE_ZMAN_PAD_DAYS, HDATE_ZMAN_PAD_DAYS):
            zmanim = self._zmanim(day)
            instant = (
                zmanim.candle_lighting
                if key == HDATE_CANDLE_LIGHTING
                else zmanim.havdalah
            )
            if instant is not None and window.contains(instant):
                spans.append(Span(instant, instant))
        return spans

    def _melacha_spans(self, window: Window) -> list[Span]:
        """§5.4's interval: candle lighting to the havdalah that closes it.

        This is the offering the whole two-stage design was written for, and it
        is the first span in the tree with an interior. Three things about how it
        is built are load-bearing:

        **A stretch is found by its entry, not by its days.** A day is the start
        of a stretch only when it has a candle lighting *and is not itself*
        Shabbat or yom tov. hdate reports a candle lighting mid-chain too — the
        transition from a chag into Shabbat — and treating that as a second entry
        would return two overlapping spans for one continuous stretch, so "on
        Shabbat" would match twice.

        **The end is walked forward, not assumed.** Rosh Hashanah on a Thursday
        runs into Shabbat and does not end until Saturday night; the interval is
        three days long and a rule anchored to its end has to say so. The walk is
        bounded (`_MELACHA_CHAIN_DAYS`) because an unbounded search is how one
        resolver stalls an engine (D17).

        **The window is padded backwards.** `Span.overlaps` promises that a span
        already running at `window.start` is part of the answer, and
        `BaseResolver.covers` is built on that promise — it asks about the
        instant's own civil day. Saturday 10:00 is inside a stretch that began on
        Friday evening, so without the pad the day set would answer *false* for
        the middle of Shabbat. That is not a near-miss; it is the failure mode
        D11 exists to prevent, arrived at from the other direction.
        """
        zone = self.zone
        spans: list[Span] = []
        for day in window.days(zone, HDATE_MELACHA_PAD_DAYS, HDATE_MELACHA_PAD_DAYS):
            if self._in_melacha(day):
                continue
            start = self._zmanim(day).candle_lighting
            if start is None:
                continue
            end = self._chain_end(day)
            if end is None:
                # A stretch whose close the library will not state. Left out
                # rather than closed at a guess: D17's degraded answer is
                # "nothing here", and inventing an end would put a fabricated
                # boundary on the timeline as fact (§5.3).
                continue
            span = Span(start, end)
            if span.overlaps(window, zone):
                spans.append(span)
        return spans

    def _holiday_spans(self, key: str, window: Window) -> list[Span]:
        """A date-typed span per contiguous run of in-set civil days (D10, D88).

        **Why `date` and not `datetime`.** Every day in the Jewish calendar runs
        evening to evening, so a midnight-to-midnight span looks like exactly the
        lie §5.3 warns about — until you ask what a boundary would be made of.
        hdate assigns holidays *to civil dates*; there is no instant in its
        holiday table. Synthesising one from the zmanim would work for yom tov
        and would be unimplementable for the rest of this list: Purim, Chanukah
        and a fast day have no candle lighting and no havdalah, and Rosh Chodesh
        has neither by construction. §5.3's rule decides it — "forcing a datetime
        would invent a precision the source does not have" — and the precise
        reading is available under its own key, `issur_melacha`, where the
        boundaries are real. The two offerings answer two different questions and
        the editor should say which is which (§5.4's authoring guidance).

        **The end is exclusive (D88).** Sukkot 2026 in the diaspora runs 25
        September to 4 October, and comes back as
        `Span(date(2026, 9, 25), date(2026, 10, 5))`. §5.6 asked step 7 to
        confirm the convention against a real multi-day source; it holds, and the
        `edge: end` anchor on it resolves to 5 October 00:00 local, which is D89.

        **Both ends are padded.** A run is merged, so a window landing in the
        middle of Sukkot must reach backwards to find where the festival started
        *and* forwards to find where it ends. Truncation would not drop the span,
        it would shorten it — and a shortened span reports the window's own edge
        as a festival boundary, which is a fabricated fact rather than a missing
        one.
        """
        wanted = _HOLIDAY_SETS[key]
        zone = self.zone
        diaspora = self.diaspora

        days = list(window.days(zone, HDATE_HOLIDAY_PAD_DAYS, HDATE_HOLIDAY_PAD_DAYS))
        in_set = [
            day
            for day in days
            if any(
                holiday.type in wanted
                for holiday in HDateInfo(day, diaspora).holidays
            )
        ]

        spans: list[Span] = []
        run_start: date | None = None
        previous: date | None = None
        for day in in_set:
            if run_start is None or previous is None:
                run_start = previous = day
                continue
            if day == previous + timedelta(days=1):
                previous = day
                continue
            spans.append(Span(run_start, previous + timedelta(days=1)))
            run_start = previous = day
        if run_start is not None and previous is not None:
            spans.append(Span(run_start, previous + timedelta(days=1)))

        return [span for span in spans if span.overlaps(window, zone)]

    # --- hdate's own objects, built once per civil day ---------------------

    @property
    def diaspora(self) -> bool:
        """Whether a second day of yom tov is observed here.

        Inferred from the core config when the constructor was not told, and the
        inference deliberately takes two signals rather than one: `country` is
        the authoritative field but is often unset, while a timezone of
        `Asia/Jerusalem` is set by everyone in Israel because nothing else works.
        Either one saying Israel is enough; neither saying it means diaspora,
        which is the commoner case and — more to the point — the one whose extra
        day is a *superset*, so a wrong guess adds an occurrence the user can see
        and remove rather than removing one they never learn about.
        """
        if self._diaspora is not None:
            return self._diaspora
        return not (
            getattr(self.hass.config, "country", None) == "IL"
            or self.hass.config.time_zone == "Asia/Jerusalem"
        )

    @property
    def location(self) -> Location:
        """hdate's view of where this installation is.

        Built per call rather than cached: `hass.config` is mutable, D43 declares
        these answers deterministic *in the core config*, and a cached `Location`
        would be the one place that declaration stopped being true.
        """
        return Location(
            name=self.hass.config.location_name,
            latitude=self.hass.config.latitude,
            longitude=self.hass.config.longitude,
            timezone=self.zone,
            altitude=self.hass.config.elevation,
            diaspora=self.diaspora,
        )

    def _zmanim(self, day: date) -> Zmanim:
        """hdate's times for one civil date.

        `date=` is passed explicitly and always. The field defaults to
        `date.today()`, so an omission here would not raise — it would answer
        about today, on every call, and D64's whole point is that the dry run and
        the live engine must not be able to differ like that.
        """
        return Zmanim(
            date=day,
            location=self.location,
            candle_lighting_offset=self._candle_lighting_offset,
            havdalah_offset=self._havdalah_offset,
        )

    def _in_melacha(self, day: date) -> bool:
        """Whether this civil day is itself Shabbat or yom tov.

        Used only to reject a mid-chain candle lighting as the *start* of a
        stretch. hdate's own two predicates rather than a reimplementation:
        `is_shabbat` is a weekday test and `is_yom_tov` consults the holiday
        table, and the diaspora flag matters to the second.
        """
        return is_shabbat(day) or is_yom_tov(day, self.diaspora)

    def _chain_end(self, day: date) -> datetime | None:
        """The havdalah that closes the stretch beginning on `day`, or `None`.

        Walks forward because the length of a stretch is a fact about the
        calendar and not about the day it starts on: an ordinary Friday closes on
        Saturday night, a Thursday chag running into Shabbat closes two days
        later. hdate answers `havdalah` with `None` for every day of a chain
        except its last, which is exactly the loop condition — the library
        documents the `None` as deliberate, so that nothing can read a mid-chain
        day as permitted.
        """
        for offset in range(_MELACHA_CHAIN_DAYS):
            if (end := self._zmanim(day + timedelta(days=offset)).havdalah) is not None:
                return end
        return None


__all__ = ["HDateResolver"]
