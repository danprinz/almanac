# almanac — technical design

**Status: design. v1 scope frozen; no code yet.**

This document records decisions and the reasoning behind them. Requirements and any remaining
open questions stay in [`PRODUCT_BRIEF.md`](PRODUCT_BRIEF.md); verified facts about upstream
behaviour stay in [`CLAUDE.md`](CLAUDE.md). Decisions are numbered `D1…` so they can be cited
and revisited individually.

> **Numbering convention.** Decision numbers are **identifiers, not an ordering.** D1–D63 were
> assigned in reading order at the first commit; every decision added after that takes the next
> free number and lives in the section it belongs to. So a later section may contain a lower
> number than the one above it. Renumbering to restore reading order would invalidate every
> citation from code, commits and reviews, which costs more than the tidiness is worth.

Where a decision rests on a verified fact, the fact is cited in
[Appendix A](#appendix-a--verified-facts-used-by-this-design). **Read the source-integrity
note in [Appendix B](#appendix-b--source-integrity-note) before verifying anything in this
repo.**

---

## 1. The shape of the thing

Three structural moves define v1. Everything else follows from them.

1. **The authoring primitive is a rule with two shapes** — a *moment* or an *interval* — and
   the 24-hour bar is a rendering, not a model. (§3)
2. **Every non-clock time source implements one resolver contract.** Sun is not a special
   case; it is the first implementation. (§5)
3. **Enumerability is enforced by a type signature, not a policy.** A trigger source that
   cannot forecast a window cannot implement the contract, so it cannot exist. (§14)

### 1.1 What v1 is not

Deferred deliberately, with the shape reserved so each lands additively:

| Deferred | Reserved by |
| --- | --- |
| Calendar entities as anchors / day-set sources | resolver contract + span shape (§5.3) |
| Day sets *publishing* a calendar entity | `forecast` already returns spans (§6) |
| Continuous state reconciliation | per-rule flag on `During` (§8.2) |
| Third-party resolver registration | internal registry behind a stable contract (§5.6) |
| Migration from `scheduler-component` | nothing — built last, deliberately (§15) |

---

## 2. Schedule

```
Schedule
  id, name, enabled, labels/categories/area   (HA registry, not ours)
  recurrence  : DaySetRef | InlineRecurrence
  date_window : { from?, until? }
  rules       : Rule[]
  completion  : Completion
```

**D1 — recurrence selects start dates, not occurrences.** The recurrence picks the dates on
which the schedule's rules *begin*. A rule's start anchor resolves on that date; its end may
land on a later one.

*Why:* this is what makes midnight structurally uninteresting. The current component's
resolver clamps offsets to the day boundary (`00:00` / `23:59`) rather than rolling over
(A.3), which is the root cause of the "schemes are stuck in one day" problem. By anchoring
recurrence to start dates only, a 23:00 → 02:00 interval is one occurrence beginning on one
date, not two half-occurrences needing reconciliation.

---

## 3. Rules

**D2 — a rule is one of exactly two shapes.**

```
At:      { anchor, actions[], condition_policy }

During:  { start_anchor, end, state, on_exit, latch,
           enter_actions[], exit_actions[] }
         end = { duration } | { anchor }
```

*Why:* today's model conflates "a moment at which something happens" with "an interval during
which a state should hold", and ships only the first. Turning a light on at 17:00 and off at
18:00 therefore needs two slots plus a null-stop ghost slot — the confusion the brief records.

The deeper reason to carry both: **intervals are idempotent and reconcilable; moments are
not.** "Should this be on right now?" has an answer for an interval and no answer for a
moment. That single property is what makes restart recovery (§10.3), mid-interval enable, and
desired-state actions well-defined rather than heuristic. A model with only moments cannot
answer any of them.

**D3 — `on_exit` is explicit and three-valued.** `apply(state)` | `restore` | `leave`.
`restore` captures entity state at interval entry and re-applies it at exit. This governs
*state*; the action hooks in D5 are separate.

**D4 — `latch` governs predicate exit, separately from window exit.** For a `During` rule
whose conditions form part of its activation predicate (§7.2), `latch: false` runs the exit
path when the predicate goes false; `latch: true` runs it only at the end of the time window.

*Why:* "AC on 17:00–23:00 while someone is home" is ambiguous about what happens when they
leave, and both readings are legitimate. This is the honest replacement for today's
`track_conditions` toggle (§7.3).

**D5 — `During` rules carry `enter_actions` and `exit_actions`** — `At`-style action lists
fired at the interval's edges, independent of the state it reconciles.

*Why:* without them the flagship case fragments. "From candle lighting to havdalah: set the
lights **and** announce over the PA, then restore **and** announce" would need one `During`
plus two `At` rules carrying duplicate anchors — change the offset and you change it in three
places, and nothing keeps them in step. Hooks keep the distinction that matters (state is
*reconciled*, actions are *fired*) without duplicating the anchor that defines the interval.

---

## 4. Anchors

**D6 — anchors are a tagged union of three kinds.**

```yaml
{ kind: clock,       at: "17:00" }
{ kind: entity_time, entity_id: sensor.x, offset: -45m }
{ kind: resolver,    domain: sun,   key: sunset,          offset: -20m }
{ kind: resolver,    domain: hdate, key: candle_lighting, offset: -45m, edge: start }
```

*Why three kinds and not one:* the config shapes genuinely differ — a literal, an entity
reference, and a key drawn from a fixed list. Collapsing them forces a
lowest-common-denominator shape onto all three. Internally they all run behind one interface
(§5), so the engine keeps a single code path; the union exists in **storage and UI**, where
the difference is real and must be visible. §5.5 shows why the difference is not cosmetic:
`sensor.sun_next_setting` and the `sun` resolver describe the same physical event with
different horizons.

**D7 — offsets are signed durations with no magnitude clamp.** A warning is shown past 24h;
nothing is rejected.

*Why:* the ±4h limit users hit today exists only in the card, as `MAX_OFFFSET_HOURS = 4`, and
its value has been 3 → 2 → 6 → 4 across five years of UI rewrites with no stated technical
reason (A.3). The component enforces no magnitude limit at all. There is nothing behind the
constraint to preserve.

**D8 — resolver offerings are addressed by stable machine key, never by display text.**

*Why:* the alternative — matching a calendar event's `summary` — breaks under language change,
because that string is translated at render time (A.1). Keys like `candle_lighting` are
language-independent, greppable and diffable. This is the strongest argument for the resolver
architecture over the calendar route it replaces: it yields a better identity model, not
merely tidier code.

**D9 — offering keys and resolver domains are a compatibility surface.** Both are stable for
the life of a resolver; a rename requires an alias table, never a silent change.

*Why:* a stored `key: tset_hakohavim_tsom` that quietly stops resolving breaks a schedule
without telling anyone — the worst failure mode this product has. The same rule is why the
Jewish-calendar resolver is named `hdate` from the outset (D15) rather than being renamed
after schedules already reference it.

---

## 5. The resolver contract

### 5.1 Two roles

A resolver serves two roles, either or both per offering:

- **anchor** — a window resolves to concrete spans, and an anchor takes one edge of one span.
- **day set** — a predicate answering whether the set is in force.

### 5.2 Contract

```python
class Resolver(Protocol):
    domain: str                                    # "sun", "hdate"

    def offerings(self) -> list[Offering]: ...
        # Offering: key, display_name, roles: {anchor, day_set}, horizon

    def horizon(self, key: str) -> Horizon: ...
        # UNBOUNDED | UNTIL(datetime) | NEXT_ONLY

    async def forecast(self, key: str, window: Window) -> list[Span]: ...
        # Span.start, Span.end : date | datetime

    async def candidate_dates(self, key: str, window: Window) -> list[date]: ...
        # day-set role, coarse stage — default: derived from forecast()

    async def covers(self, key: str, instant: datetime) -> bool: ...
        # day-set role, precise stage — default: derived from forecast()

    def invalidation(self, key: str) -> InvalidationSignal: ...
```

### 5.3 Spans, not instants

**D10 — `forecast` returns spans whose `start` and `end` are typed `date | datetime`**,
mirroring `CalendarEvent`. A date-typed span is genuinely all-day. A datetime-typed span is
precise, and a zman is a zero-length one (`start == end`) — which is literally what core's
`jewish_calendar` produces for timed events (A.1).

*Why one shape:* it serves both roles, and — the payoff — `CalendarEvent` **already is this
shape**. The deferred calendar work (§1.1) therefore reduces to one more implementation of an
unchanged interface, and a day set can *publish* a calendar entity from the same data. The
span shape is what makes both additive rather than a future schema break.

*Why the type union rather than always-datetime:* forcing a datetime on "every second Tuesday"
would invent a precision the source does not have, and the timeline would then render a
fabricated boundary as fact.

**Do not mirror core's all-day encoding.** Core builds
`CalendarEvent(start=target_date, end=target_date)` — a civil `date`, with **start equal to
end**, so the span has zero extent and fails any overlap test (A.1). Our own date-typed spans
are half-open over the local civil day. This is a second reason to compute from `hdate`
ourselves (D15) rather than consume core's calendar.

### 5.4 The day-set role is two-stage

**D11 — a day set is evaluated coarsely to generate, then precisely to filter.**

```
candidate_dates(window) -> list[date]      # generous: any span touching the civil day
covers(instant)         -> bool            # exact: does a span contain this instant
```

Recurrence uses `candidate_dates` to pick start dates, then **re-checks each rule's resolved
anchor with `covers(instant)` and drops occurrences that fall outside.** Conditions (§7) use
`covers` directly.

*Why this is not over-engineering:* a day set looks like a predicate over dates, but the
underlying reality is often a predicate over instants, and the gap silently produces
wrong-time firing. Shabbat runs Friday ~18:45 to Saturday ~19:40. A one-stage date predicate
marks **both** Friday and Saturday as in-set, so "on Shabbat, at 22:00" fires twice — and the
Saturday firing is two hours *after* havdalah. In front of a room, that is exactly the failure
this project exists to eliminate.

The two-stage rule fixes it with one mechanism: `candidate_dates` yields Friday and Saturday,
then `covers(Friday 22:00)` is true and `covers(Saturday 22:00)` is false, so one occurrence
survives. For a genuinely date-granular set the spans are date-typed, `covers` is true across
the whole civil day, and the second stage is a no-op. Correct in both cases, with no special
casing.

This is not specific to the Jewish calendar. Any day that does not align to midnight has it —
night shifts, a business day ending at 02:00.

**D12 — the timeline shows filtered-out occurrences as *outside the set*, not as nothing.**

*Why:* an occurrence that silently vanishes between authoring and rendering is indistinguishable
from a bug, and the user cannot tell which of their two readings the engine took.

> **Authoring guidance, enforced in the UI rather than the schema.** For an evening-to-evening
> period the better construct is not a day set at all — it is a `During` interval whose start
> and end are the span's two edges, which D6's `edge` and D38's pairing already support. "The
> whole of Shabbat" should be offered as an interval; "at 22:00 on Shabbat" remains expressible
> and now behaves correctly, but it is the second-best shape and the editor should say so.

### 5.5 Horizon is declared, not inferred

**D13 — every offering declares its horizon, and the timeline renders it.**

| Source | Horizon | Because |
| --- | --- | --- |
| `clock` | UNBOUNDED | arithmetic |
| `resolver: sun` | UNBOUNDED | astral computes from lat/long, never fetched |
| `resolver: hdate` | UNBOUNDED | `hdate` computes offline |
| `entity_time` | NEXT_ONLY | a sensor holds one value: the next occurrence |
| *(v2)* calendar | UNTIL(…) | whatever the calendar publishes |

The timeline renders three states — **known**, **estimated**, **unknown** — driven by this
field alone. It never guesses, and it never needs to know what a resolver is.

*Why this is load-bearing:* the product's headline claim is that one screen truthfully shows
what will fire. A timeline that silently projects an entity's current value forward is lying
in exactly the situation where being wrong is most visible. Declaring the horizon makes
truthfulness a property each resolver asserts about itself, checkable at the boundary rather
than inferred by the renderer.

Note the asymmetry the table captures: `sensor.sun_next_setting` and the `sun` resolver
describe the same physical event, but the sensor holds only the next value while
`helpers/sun.py::get_astral_event_date(hass, event, date)` computes any date locally (A.2).
Same event, different horizons — which is why D6 keeps them as distinct kinds instead of
routing everything through `entity_time`.

### 5.6 Registration

**D14 — the registry is internal for v1. Sun and hdate ship in-tree.**

HA's integration-platform discovery — the mechanism `logbook` uses (A.4) — is the right
long-term shape, and would eventually let `jewish_calendar` ship its own resolver. But the
moment a third party ships one, the interface becomes a compatibility commitment; at 0.x,
untested against any real external implementation, that is premature. Open it once the
contract has stopped moving.

**D15 — the Jewish-calendar resolver is `hdate`, backed by the `hdate` library, not the
hebcal.com API.** Display name: *Jewish calendar*.

*Why the library:* core's `jewish_calendar` uses `hdate` and computes entirely offline, which
is what earns it an UNBOUNDED horizon. A network-backed resolver would carry rate limits,
failure modes and a bounded horizon, all three of which would leak into the timeline.

*Why that domain name:* `hebcal` names a different project (hebcal.com), so using it for an
`hdate`-backed resolver misdescribes the implementation in a field that D9 makes permanent.
If HA's own `jewish_calendar` later ships a resolver under D14, D9's alias table covers the
handover.

**D16 — resolvers offer a fixed, enumerable set of named offerings. Users pick from a list.**

*Why:* a resolver accepting free-form user configuration would have reinvented templating
behind a plugin boundary, defeating the very non-goal the contract exists to enforce.

**D17 — resolver failures degrade to *unresolved*, never to an engine stall.** Timeouts and
per-call isolation live in the contract, not in each implementation. An unresolved anchor
surfaces on the timeline and in the log; it does not take down unrelated schedules.

### 5.6 Gaps closed at step 2

Four questions the contract could not be written without, none of them settled by D6–D17.
Recorded 2026-09-30, all four as implemented in step 2.

**D86 — a resolver is *selectable* or *parametric*, and the protocol says which.** D16 gives
users a fixed list to pick from; that cannot describe `clock`, whose key is a time of day, or
`entity_time`, whose key is an entity_id. A parametric resolver returns an empty `offerings()`
and answers `offering(key)` for any key it can parse. A selectable resolver — `sun`, and `hdate`
at step 7 — enumerates.

*Why:* D16's purpose is to stop a resolver becoming a templating surface, and that purpose is
served by the *set of resolvers* being closed, not by every key being enumerable. A time of day
and an entity_id are both already constrained elsewhere — by the schema, and by the entity
registry — so a parametric key smuggles in no expressiveness. The split is in the protocol rather
than a convention because a UI listing offerings has to know which resolvers have none to list.

**D87 — `forecast` returns a `Forecast`, not a bare list of spans.** It carries the declared
horizon (D13) and a computed `known_through`.

*Why:* an empty list is two different answers — *nothing is scheduled in this window* and *my
knowledge stops before this window* — and the timeline has to draw them differently. D13 already
requires the horizon to reach the timeline; putting it on the return value means it arrives with
the data it qualifies, rather than being fetched separately and possibly going stale against it.

**D88 — a date-typed span's `end` is exclusive.** The whole of 2 October is
`Span(date(2026, 10, 2), date(2026, 10, 3))`. An eight-day festival starting 2 October ends
`date(2026, 10, 10)`.

*Why:* either convention is workable and the cost of the choice is entirely in its being
*unstated*. A resolver author guessing wrong makes every span one day short or one day long, and
the error is invisible in the common case of a single-day span, where inclusive and exclusive
encodings differ by nothing a test would notice unless it tests adjacency. Exclusive is the half
chosen because it makes `bounds()` a pair of midnights and adjacency an equality — the day after
2 October begins exactly where 2 October ends — and because inclusive would have put the
zero-extent encoding of A.1, which D10 exists to reject, back within one off-by-one of being
correct-looking.

**D89 — the `end` edge of a date-typed span is the exclusive midnight.** "The end of
2 October" is `2026-10-03 00:00` local, not the last instant of 2 October.

*Why:* this is not an independent choice; it is D88 applied to anchors. An anchor names a span
and one of its edges, and if `end` means one thing to `bounds()` and another to an anchor then
"during the festival" and "at the end of the festival" disagree about where the festival stops.
The alternative — forbidding anchors on all-day edges — was rejected because "the evening before"
is a real case and refusing it would push the arithmetic into the user's configuration.

*Step 7 confirms this against `hdate`*, which is the first resolver to return date-typed spans at
all; `sun` returns none.

### 5.7 Gaps closed at step 7

Recorded 2026-09-30, as implemented in step 7. §15 places this step after the engine because *a
contract is not proven by the implementation it was designed around*, and that is what it was:
one hole in a promise the contract makes about itself, six places A.13 had not been applied, and
five things that needed deciding.

**D111 — `Window.days()` takes `pad_before` / `pad_after`, and the padding is how a resolver
declares its own reach backwards.** `hdate` passes one day for a zman, six for an issur-melacha
stretch, fourteen for a festival run, each stated in `const.py` beside the bound it comes from.

*Why:* `Span.overlaps` promises that a span already running at `window.start` is part of the
answer, and `BaseResolver.covers` is **built** on that promise — it forecasts the instant's own
civil day and asks whether any span contains the instant. Nothing kept the promise. Every span
`clock`, `sun` and `entity_time` produce is zero-length *and* is computed from the civil day it
lands on, so "the days the window touches" and "the days worth computing" coincided and the two
halves cancelled. `hdate` breaks both: its melacha interval is still running at 10:00 on Saturday
having begun on Friday evening, and its `chatzot_halayla` for 6 October in New York lands at
7 October 00:44. Before the fix, `covers("issur_melacha", Saturday 10:00)` returned **false** —
the middle of Shabbat reported as not Shabbat. Verified by mutation: reverting the two lines
fails eight tests.

*Alternatives rejected:* (a) each resolver widening its own window before looping — a copied loop
is the first place the engine and the timeline diverge about which days were considered, and the
whole of D11 rests on both asking the same question. (b) A fixed generous pad inside `days()` —
that charges every resolver the worst resolver's reach, so `sun` would compute two spare days on
every call forever. (c) Declaring the reach on the `Offering` instead of at the call — the better
shape if a caller ever needs the reach *without* calling `forecast`, and nothing does; deferred
rather than refused.

*The cost, stated:* a resolver that under-declares its pad gets a silently short answer rather
than an error. What holds it down is that each number is written with the bound it derives from,
and that `covers` is cross-checked against hdate's own predicate (below).

**D112 — the holiday sets are date-typed, and the instant-precise reading is a separate
offering.** `yom_tov`, `chol_hamoed`, `festival`, `fast_day` and `rosh_chodesh` return date-typed
spans; `issur_melacha` returns a datetime span with real boundaries.

*Why:* §5.3 says not to invent a precision the source lacks, and `hdate` assigns holidays to
civil dates — there is no instant anywhere in its holiday table. A boundary would have to be
synthesised from the zmanim, which works for yom tov and is **unimplementable** for most of this
list: Purim, Chanukah and a fast day have no candle lighting and no havdalah, and Rosh Chodesh
has neither by construction. *Rejected:* a datetime encoding for the subset that can carry one.
That would make the type of a span depend on which holiday happened to fall in the window, so a
rule would change shape between one festival and the next. The two offerings answer two different
questions and §5.4's authoring guidance is where the editor says which is which.

**D113 — a mid-chain candle lighting is passed through as an anchor occurrence, not suppressed.**
On the second night of a two-day chag `hdate` reports the havdalah time under the
candle-lighting name, because that is when the candles are lit.

*Why:* *rejected* was suppressing it, so that `candle_lighting` would mean only "the start of a
stretch". That reading is already available, exactly, as the `start` edge of `issur_melacha` —
and suppression would make "twenty minutes before candle lighting" silently skip the night of the
chag, which is the night it is most often wanted. Passing the library's answer through keeps the
offering meaning what its name says.

**D114 — `diaspora`, `candle_lighting_offset` and `havdalah_offset` are constructor arguments
with core's defaults and no user surface. Provisional; this one needs a ruling.** `diaspora=None`
infers from `hass.config.country == "IL"` or a timezone of `Asia/Jerusalem`, defaulting to
diaspora.

*Why provisional:* core's `jewish_calendar` asks for all three in its config flow and almanac has
no options flow at all (D65). *Rejected:* (a) reading them off a loaded `jewish_calendar` config
entry — best fidelity and no new surface, but D43's `InvalidationSignal` can say only
*deterministic* or *watch these entities*, and neither describes "another integration's options
changed", so the resolver would declare a horizon it could not keep and the cache would act on
it. (b) Adding almanac options now — that is a UI surface, §15 puts the UI at step 9, and
deciding its shape from inside a resolver is how a config key becomes permanent by accident (D9).

*Not cosmetic:* diaspora moves Simchat Torah by a day. The inference defaults to diaspora because
the extra day is a **superset** — a wrong guess adds an occurrence the user can see and remove,
rather than removing one they never learn about — but a default chosen for its failure mode is
still a default, and the ruling wanted is whether this becomes an options flow at step 9 or stays
inferred.

**D115 — a melacha stretch whose closing havdalah the library will not state is omitted, not
closed at a guess.** The forward walk is bounded at six civil days (D17 — an unbounded search is
how one resolver stalls an engine); if it finds no havdalah, the stretch is left out.

*Why:* §5.3 again. A fabricated end renders on the timeline as fact and resolves as an `edge:
end` anchor, so it is worse than a missing span, which at least reads as missing. *Rejected:*
closing the stretch at the end of the walk.

#### What the step found rather than decided

Six places where A.13 — Python compares *and subtracts* two aware datetimes sharing a `tzinfo`
**object** by wall clock — had not been applied. All were invisible while every span in the tree
was zero-length. Each is fixed in place with a regression test, and each test was verified by
mutation to fail without its fix.

- **`Span.overlaps`** was the one method on `Span` still comparing wall clock. Both halves
  mattered: `low == high` is a wall-clock equality, so a span running 01:30 EDT → 01:30 EST — a
  real hour — read as an *instant* and took the zero-length branch.
- **`ResolverRegistry.async_forecast`** sorted spans on a raw `bounds()` tuple. This one is
  load-bearing rather than untidy: `async_resolve_anchor_on_date` takes `spans[0]` as "the
  earliest", so inside the fold it handed D1's recurrence the wrong instant.
- **`contract.known_through`**, **`Forecast.fully_known`**, **`AnchorForecast.fully_known`**,
  **`anchor.py`'s `min(..., window.end)`** and **`plan.py`'s `fully_computed` / `fully_known`** —
  bare `min`/`max` and bare comparisons. The last is the worst shape of the bug: the plan would
  report a horizon and then contradict its own summary of it.

#### What held

D10's date/datetime split needed no change — three shapes come out of one `forecast` and the
engine sees only `Span.is_all_day`. D11's two stages were inherited from `BaseResolver`
**verbatim**; `engine/day_set.py` needed no change at all. D88 and D89 are confirmed against a
real multi-day source: Sukkot 5787 comes back as `Span(date(2026, 9, 25), date(2026, 10, 5))` and
`edge: end` on it resolves to 5 October 00:00 local. D14's internal registry cost step 7 exactly
one changed line, which is what it was for. §5.1's role split did real work for the first time:
a zman is anchor-only, and `issur_melacha` carries both roles.

`hdate` ships `Zmanim.issur_melacha_in_effect`, an exact second implementation of D11's stage
two. It is deliberately **not** used as an override of `covers` — a second implementation is a
second thing to drift — and is instead a test: the derived `covers` agrees with it on 112
instants across a fortnight spanning Sukkot, with no mismatches. That evidence exists only
because the shortcut was refused.

### 5.8 D122 — a day set is the set of days the anchor's *event* belongs to

*Measured 2026-09-30 at the start of step 9, and ruled on by the owner the same day.
`tests/test_flagship.py` is the measurement, and it is in the suite.*

**D122 — D11's stage two is asked about the anchor's source instant. D7's offset is
arithmetic applied afterwards.**

The UX round's first finding was marked blocking and claimed that Scenario A — *start 45 minutes
before candle lighting, end 30 minutes after havdalah, on Shabbat*, the rule `PRODUCT_BRIEF.md`
says must be effortless or the product has failed — never runs. It was written before step 7, so
it was an argument about the design. Steps 7 and 8 made it checkable. It was right.

**The measurement.** October 2026, New York, against the real `hdate` resolver, `issur_melacha`
as the day set:

| setup window | occurrences that ran |
| --- | --- |
| none (offset 0) | all five Fridays |
| one second | none of them |
| forty-five minutes | none of them |

The boundary was not near zero, it **was** zero, and the cliff was one second wide. `hdate` puts
the first instant of `issur_melacha` on the candle-lighting instant exactly, so the unshifted
anchor cleared stage two by nothing at all and *any* negative offset fell off. It was not a setup
window that was too long — no shorter one helped. A day-set recurrence and a negative offset were
categorically incompatible, and starting before the day starts is what a setup window is for.

**There was also a false positive, which is why this survived to step 9.** Across a festival,
`issur_melacha` is one continuous span — 2 Oct 18:18 to 4 Oct 19:14, Shabbat running without a
break into Shemini Atzeret — and it contains a *second* candle lighting, well inside it, which
survives the offset. So Scenario A was not silent. It produced exactly one occurrence a month, on
a day nobody asked about, running twenty-five hours. Any check that asked "did anything run"
would have reported it healthy. That is the reason the measurement was taken before the first
line of frontend code rather than after it.

**The ruling.** The owner's words: *"it must work the anchor and backwards (first know when is
the candle lighting and then calc 45 minutes offest (+ or -)"*. Candle lighting is the event the
day belongs to, and the offset is what you do to it once you know it. So the question stage two
asks is about the event, and the rule's start is the event plus the offset — in that order, not
the other way round. The wider frame the same ruling sets out is §6.1.

**Why this does not break §5.4's motivating example**, which is the thing it could have broken.
"On Shabbat, at 22:00" must fire on the Friday and not on the Saturday, and only an
instant-granular test gets that. Its anchor is a clock at 22:00 carrying **no offset**, so its
source instant and its resolved instant are the same value and the filter sees exactly what it
always saw: Friday 22:00 inside the span, Saturday 22:00 after havdalah. That is why D122 needs
no per-rule flag and can be the silent default — the two instants diverge only when there *is* an
offset, and an offset is precisely the case the old behaviour got wrong.
`test_the_motivating_example_still_holds` exists so that "by construction" cannot quietly stop
being true. It uses an ordinary Shabbat, 9–10 October, deliberately not the festival weekend the
rest of the file uses: on the festival weekend the Saturday 22:00 row *should* fire, so it cannot
test a filter.

**What it is in the code.** `async_resolve_anchor_on_date` returns a `ResolvedAnchor` carrying
both instants — `source`, the event, and `at`, the event plus the offset — rather than one
`datetime`. Stage two reads `source`, everything else reads `at`. This is the same move step 8
made when it pulled `known_through` back off `computed_through` (§12.2): one value was answering
two questions, and the place to make that impossible is the type, not a comment at each call
site. Four call sites, all in `engine/plan.py` and `tests/`.

**D12 is unchanged and now means what it says.** A row dropped by stage two is still carried with
`status = outside_set` and its resolved start retained, so the timeline can draw it. Before D122
that label was a lie about the Fridays — "you asked for a day this is not on", when the day was
exactly the day asked for. Now `outside_set` appears only when the anchor's own event really is
outside the set. For an offset anchor the instant *tested* is `source` and the instant *shown* is
`at`. Showing the start is right, because the start is what the user wrote down.

**Rejected.** Two alternatives were on the table, and both are strictly more to carry:

- **`hdate` publishes a date-granular Shabbat alongside the instant-granular one, and the user
  picks.** More expressive, and more to explain: two sets called Shabbat differing in a way that
  is invisible until a rule is offset. It also does not generalise — every future resolver has to
  make the same choice again, whereas D122 is a property of the rule model.
- **A sixth `RECUR_*` kind: a recurrence derived from an anchor rather than from a set of days.**
  The largest change of the three, and the only one that makes the day set unnecessary rather
  than correct. D122 leaves the day set doing the job it was designed for, which is the smaller
  claim and the one the measurement supports.

---

## 6. Day sets

**D18 — a day set is a named, first-class object referenced by schedules.**

```
DaySet
  id, name, owner        (recorded, not enforced)
  source : Weekdays | Dates | NthWeekday | EveryN | ResolverOffering | AnchorSpan | Composition
```

Editing a day set changes every schedule referencing it — that is the point. "Cleaning days"
is maintained once, by the person who knows them, and affects lighting, HVAC and announcements
owned by other people.

**D19 — composition is one level of union / intersect / minus. No nesting.**

*Why:* deeply composed sets are not enumerable in bounded time and cannot be explained in a
one-line summary, which breaks trigger honesty — the thing the whole design is for.

**D20 — day sets are usable as recurrence and as condition**, with the two-stage evaluation of
D11 making those the same object rather than two objects that drift apart.

**D21 — every day set exposes a `binary_sensor`. State: does the set cover *now*.**

The storage collection stays the source of truth and the entity is a projection over it,
paired by `sync_entity_lifecycle` — the pattern core's `schedule` integration already uses
(A.6). It is not either/or.

*Why:* it makes a day set referenceable by everything else in HA — other people's automations,
templates, dashboard conditionals — rather than only by us. That is the "be part of the HA
family" argument applied to the object most likely to be shared. It also inherits labels,
categories and areas for free (D56), plus `unique_id`, restart survival and a more-info
dialog. Defining the state as *covers now* rather than *covers today* keeps it consistent with
D11, so the entity cannot disagree with the engine.

> **What this does not buy: attribution.** The entity registry stores `created_at` and
> `modified_at` and **nothing about who** — there is no `created_by`, `modified_by` or
> `user_id` field anywhere in it (A.9). HA records when something changed, never who changed
> it. Who-edited-what comes solely from `Context.user_id` on the events we fire (D50), and
> that works whether or not day sets are entities. Being an entity is worth it for reference-
> ability; it is not an audit mechanism, and designing as though it were would leave a gap
> nobody notices until someone asks.

**D22 — editing a day set requires an impact preview.** The editor states how many schedules
reference it and shows the resulting timeline delta before saving.

*Why:* a shared object with a large blast radius, edited by someone who does not own the
affected schedules, is the most likely source of a surprise outage in this design. The preview
is not a nicety; it is the mitigation for the feature.

> **On ownership.** `owner` is recorded and displayed, not enforced. HA has no per-object ACL
> model. Recording it is worth doing for the audit trail (§12); calling it enforcement would
> be false.

> **Deferred, cheap, not v1.** A day set could also publish a `calendar` entity, since
> `forecast` already returns spans — free visualisation in HA's calendar panel. It is
> display-only, so it waits until the engine is proven.

### 6.1 The three layers a day set comes from

*Ruled on by the owner 2026-09-30, in the same answer as D122.*

The ruling opened with a correction worth writing down before the decisions it produced:
**"Shabbat (and Holiday) is not the same as Friday or Saturday. It's a 'logical' time span."**
Every bug in §5.8 is downstream of treating it as a pair of weekdays, and the fix is not a better
weekday — it is admitting that the span has its own edges and that those edges are anchors.

**D123 — day sets come from three layers, and the middle one is the one that makes the other two
unnecessary to extend.**

| layer | built from | who writes it |
| --- | --- | --- |
| 1 — basic | clock times and weekdays, nothing resolved | the user, in the simple editor |
| 2 — sets | a span between two anchors, which may be resolver offerings or entities | the user, when layer 1 cannot say it |
| 3 — predefined | Shabbat, holidays, national days, shipped ready to use | us |

Layer 3 is a convenience over layer 2, not a separate mechanism: a shipped set is one a user
could have written, and saying so is what stops the catalogue becoming the product. The owner's
sentence for layer 2 is *"with that I can define any span I want"*, and that is the test it has
to pass — if a span the user wants is not expressible, the answer is a gap in layer 2, not a
request for a new layer 3 entry.

**D124 — a day set may be sourced from a span between two anchors.** A seventh `source.kind`
beside D18's six, holding a start anchor and an end anchor in the same shape rules already use
(§5, D6 and D7), so offsets, resolver offerings and entity times all work in it for free and
nothing new has to be explained about them.

*Why it is needed and was not there.* Before this, a day set could only name a prebuilt resolver
offering (`SOURCE_OFFERING`) or compose sets that already existed (`SOURCE_COMPOSITION`, D19).
There was no way to say *"the span from candle lighting to havdalah"* — only to hope a resolver
had already published it, which for Shabbat means `issur_melacha` and for anything the user
invents means nothing at all. "Between the kids' bedtime sensor and sunrise" was unsayable, and
it is layer 1's `During` interval written as a set rather than as a rule.

*Why it composes rather than complicates.* D19's one level of union / intersect / minus applies
to it unchanged, which is how layer 3 gets built: a shipped Shabbat set is a span source on
`hdate`'s two edges, and a shipped "weekend" is a composition over spans. D11's two stages work
on it without change too — `candidate_dates` is every civil day the span touches, deliberately
generous, and `covers` is the instant test against the resolved edges. And D122 is what makes it
usable with an offset, because the anchor the set is built from and the anchor a rule starts from
are now asked different questions.

*The one constraint it inherits.* An anchor-sourced set is only known as far ahead as its
anchors declare (D13), and D44's horizon reporting already carries that per rule, so a set built
on an entity time is `fully_known` only while the entity is. A set built on two resolver
offerings is unbounded, like the offering it replaces.

### 6.2 What step 8a found

*Four gaps, closed here rather than by editing D124, per the convention at the top of this file.
Three of them are consequences of one fact: before D124 no day set had an anchor, so every piece
of machinery that exists because anchors are uncertain had never been pointed at a day set.*

**D125 — a span's end edge is searched for eight days and no further, and past that the set is
unresolved.** *Owner's judgement call.* D38's forward walk for a rule's end anchor is bounded by
D39 — an interval may not outlast its recurrence period — and a day set has no recurrence period
to be bounded by, so the bound here had to be chosen. Eight days, because the longest continuous
stretch the motivating calendar produces is a diaspora Sukkot week, and because a bound that
large cannot be hit by anything a user would call a span.

Two readings were rejected. *Unbounded* means one typo — "from sunrise to candle lighting", on a
Tuesday — makes the engine walk forward for as long as the compute budget allows, per candidate
day. *Stop at the next occurrence of the start edge* is the rule that looks obviously right and
is wrong on the exact case §5.8 had just fixed: `hdate` reports candle lighting on both the
Friday and the Saturday of a two-day chag while the havdalah that closes it is on the Sunday, so
the second lighting would truncate the first span and the flagship scenario would break again.

Past the bound the answer is `NO_PAIRING` and not a truncated span, for D12's reason — a set that
was silently shorter than the user wrote would be indistinguishable from a correct one.

**D126 — two spans in one set overlap freely, and that is a union rather than a collision.** D39
reads "an interval may not outlast its recurrence period" and `_mark_overlaps` is its backstop,
and neither applies here. A rule's overlapping intervals are a contradiction about what to do: a
day set's are two reasons for the same day to be in force, which is what a set is. The festival
weekend is the worked example — candle lighting on the Friday *and* the Saturday, one havdalah on
the Sunday, so the two spans nest — and the honest answer is that the day is in the set.

**D127 — a day set's own anchors limit the plan's `known_through`.** §12.2 split one fact into
two: `computed_through` is how far we were willing to enumerate, `known_through` is how far a
source would commit. A day-set *recurrence* chooses the dates, so a set built on
`sensor.candle_lighting` (NEXT_ONLY, D13) makes a plan that cannot honestly claim to see past
that sensor's single value — however unbounded every rule's own anchor is. Before D124 this
could not arise, and `_Horizons` was never given a day set's anchors, so it did not see them.

The declarations are filed under the empty rule id, which is the same thing `index.Reference`
does for a day-set recurrence and for the same reason: the recurrence belongs to the schedule
rather than to any one rule, and filing it under a rule would make the plan's horizon depend on
which rule happened to be enumerated first. The slot name carries the member's position in its
composition (`day_set:<index>:start`), because `_Horizons` keys on `(rule_id, slot)` and two
members' edges under one key would combine one anchor's declaration with another anchor's data.

**D128 — an entity read for a span edge has a referrer that is not a schedule.** D57's reverse
index had one shape for an entity: whatever mentions it is a schedule, and `schedules_using_entity`
returns ids a frontend renders as schedules. A span edge breaks that, so `Usage.DAY_SET_ANCHOR`
names the case and `day_sets_using_entity` is the other half — two functions rather than one list
with a flag, mirroring exactly how `Usage.COMPOSITION` already splits the day-set half. A caller
asking "what breaks if this sensor goes away" wants both and joins them itself, which is what
`impact.affected_schedules` already does one level deep.

*And one thing that is not a gap.* **D122 does not apply inside a set.** D122 says a *rule's*
anchor is tested for membership at its own instant with the offset applied afterwards. A set's
edge is the opposite: the offset *is* the edge, because "from forty-five minutes before candle
lighting" is a statement about where the span starts. The two are not in tension — one is about
being filtered, the other about doing the filtering — and `tests/test_anchor_span_day_sets.py`
asserts both boundaries to the second so neither can drift into the other.

---

## 7. Conditions

**D23 — conditions are structured only. No templates, ever.**

*Why:* a template field is a field the UI cannot render or explain, so it violates UI/YAML
parity (§9.1) by construction and defeats trigger honesty. The escape hatch already exists one
layer down and is strictly better: the user defines a template `binary_sensor` helper once, in
HA's own UI, and references it here by entity. That version is named, reusable across
schedules, visible in the entity list, appears in the reverse index (§13), and renders on the
timeline as *"unless more than 2 rooms occupied"* rather than as a wall of Jinja.

The condition editor earns this by making the common cases structural rather than pushing them
toward templates:

| Case | Provided as |
| --- | --- |
| entity vs. constant | comparison |
| entity vs. entity, ± offset | comparison with offset operand |
| attribute comparison | attribute picker |
| state held for a duration | `for:` duration on a comparison |
| day-set membership | day-set reference, evaluated by `covers(instant)` (D11, D20) |
| anything else | the user's own template helper entity |

**D24 — every condition carries an optional label, and the UI pushes for one.**

*Why:* the execution log's entire value is answering *why didn't it fire.* "Skipped by
condition 2" is noise; "Skipped: *cleaning crew present*" is the feature.

### 7.1 Grouping

and/or groups, one level of nesting, evaluated as a whole.

### 7.2 Evaluation differs by rule shape

**D25 — for `During` rules, conditions are part of the activation predicate and are always
live.** The rule is active when *(time in window) AND (conditions)*. Entering and leaving that
predicate is the trigger; `latch` (D4) governs what leaving does.

**D26 — for `At` rules, condition policy is explicit per rule:** `skip` (evaluate once, at the
instant) or `wait_until(deadline)` (fire late if the predicate becomes true before the
deadline; otherwise skip and log).

### 7.3 `track_conditions` is deleted, not renamed

Today's toggle exists because without it, conditions are evaluated only at the trigger instant:
nobody home at 17:00 means the AC never comes on, even if they arrive at 17:30. The need is
real. The toggle is the wrong shape, and its known flaw (card#878) is that it reacts to *any
individual condition* flipping rather than to the overall and/or result changing.

D25 and D26 replace it. Under D25 the predicate *is* the whole expression, so the granularity
bug cannot be expressed. Under D26 the user states what they actually meant — skip, or wait
with a deadline — instead of toggling an implementation detail whose behaviour they must
reverse-engineer.

### 7.4 Gaps closed at step 4

Recorded 2026-09-30, as implemented in step 4. D99 and D100 are about the day-set impact preview
(D22) rather than conditions, and sit here because they were settled in the same pass.

**D97 — an undetermined predicate changes nothing.** A condition the engine cannot read leaves a
held interval held and a pending entry un-entered. It is not treated as false.

*Why:* the alternative is fail-closed, which is effectively what core's `condition.state` and
upstream's `track_conditions` do. It is rejected because a motion sensor that restarts for four
seconds would turn the lights off, and the state that follows is one no rule asked for. A user who
wants an interval to survive its predicate going away has a way to say so — D4's `latch` — and a
user who wants unreadable to mean stop has the condition they already wrote. Neither is served by
the engine guessing. The undetermined outcome is still reported (§12), so the case is visible
rather than silently absorbed.

*The asymmetry worth naming:* a definite `False` anywhere settles the whole list even when a
sibling is unreadable. Without that, one broken sensor would make a rule with an obviously-false
condition read as *unreadable* rather than *blocked*, which is the less useful of the two
sentences.

**D98 — a `for:` period is met at exactly its boundary.** Sixty seconds is met at sixty seconds.
Core's `_state_valid_since` uses a strict `>`.

*Why:* under D64 the engine evaluates at instants it chose itself, so a tick landing exactly on
the boundary is a case that actually occurs rather than a measure-zero hypothetical — the next
transition instant is computed, and the boundary is one of the things it is computed from.
Matching core exactly would make a sixty-second condition unmet at sixty seconds, which is a
sentence no user would predict. The divergence is deliberate and small; it is recorded because
it is the kind of thing a later reader would otherwise "fix" back.

**D99 — the impact preview names schedules it would *break*, separately from occurrences it would
change.** D22 requires the timeline delta before saving; a deletion or a narrowing both remove
occurrences, and the diff alone renders them identically.

*Why:* a day set that a schedule's *recurrence* depends on is not optional to that schedule.
Deleting it does not narrow the schedule, it stops the schedule enumerating at all — and in a
diff of occurrences that is indistinguishable from a set the user meant to narrow to nothing. The
preview has to be able to say "these two schedules will stop working", which is a different
warning from "these occurrences go away", and D22's purpose is defeated if the most severe edit
is the one that looks mildest.

**D100 — the preview's occurrence diff does not cover schedules that reference a day set only in
a condition, and says so.** Such a schedule is still listed among the affected; its timeline delta
is empty.

*Why:* conditions are evaluated when a transition is decided, not when occurrences are enumerated
— that is D25, and it is what makes the predicate always-live. So there is no occurrence to
diff, and producing one would mean evaluating conditions at enumeration time against a
hypothetical future state, which is both wrong and a second evaluation path of the kind D64 exists
to prevent. Stated as a limit rather than worked around, because the honest version of the warning
("this set is used by these schedules; here is what changes on the timeline, which is not
everything") is still the mitigation D22 asks for, and a fabricated delta would not be.

---

## 8. Actions

**D27 — action kind is bound to rule shape.**

| Position | Actions |
| --- | --- |
| `At` rule | service calls and scripts — heterogeneous, multiple per rule |
| `During` rule, `state` | desired state — entity → attributes, reconciled |
| `During` rule, `enter_actions` / `exit_actions` | service calls and scripts (D5) |

*Why:* this dissolves the v1/v2 framing of brief Q1. Service calls and desired state are not
competing candidates for one slot; each is bound to a position. Scripts are not stateful, so
they belong to moments and to interval *edges*. Interval *bodies* need state, because state is
what makes them reconcilable.

This also settles Fix #4 — the one-service-call-per-timeslot limit — by construction: an
action list holds heterogeneous calls across domains, which removes the reason the
script-per-permutation workaround exists.

**D28 — desired state is applied via `async_reproduce_state`, with a per-rule explicit
service-call override.**

Core's helper covers every domain that matters here — climate, light, switch, fan, cover,
humidifier, media_player, lock, vacuum, water_heater, number, select, all five `input_*`,
alarm_control_panel (A.5). A missing domain logs a warning rather than failing.

The override exists because `reproduce_state` is known to be chatty and non-atomic for climate:
it issues up to seven sequential blocking calls and does not skip attributes already matching
current state. Users with such devices need a way out that does not require us to build a
device-control layer.

**D29 — no device-control layer. Per-integration atomicity is out of scope.** The actions model
stops at desired state. Making one burst of calls produce one IR frame belongs in the device
integration — which is exactly what the sibling `smartir-atomicity` project demonstrates, and
why it must stay a sibling.

### 8.2 Reconciliation is v2, as a flag

v1 applies desired state at interval **edges** — entry and exit. Continuous re-assertion (if
something else changes the entity mid-interval, put it back) is a per-rule boolean added later.
No storage change is needed: the state description is already the payload.

### 8.3 Scripts

Scripts are first-class actions, and the point of D27/D28 is that they stop being a *workaround*
for a schema limit. Their runtime behaviour has edges that the engine must handle explicitly.

**D30 — scripts are called non-blocking by default, with opt-in `wait` that requires a
timeout.**

`script.turn_on` starts a script and returns immediately (`wait=False`). Calling `script.<name>`
as a service waits for the script to finish (`wait=True`) and can return a response (A.10).

*Why non-blocking by default:* a script containing a `delay` would otherwise hold the engine
inside a single rule's execution. Waiting is occasionally what the user wants — a script that
prepares something the next action depends on — so it stays available, but an unbounded wait
is not offered.

**D31 — before calling a script, check whether the call will be dropped, and log the truth.**

A script with `mode: single` that is already running does **not** raise. It writes
`"Already running"` to the Python log and returns `None` (A.10). A naive engine records
*fired* when nothing ran. `single` is also the default mode, so this is the common case, not
an exotic one — and two schedules sharing one PA announcement script is the first user's
actual configuration.

Script entities expose `mode`, `current` and `max` as attributes (A.10), so the engine
pre-flights the call and records *dropped: script already running* instead. This is the
execution log earning its keep: the failure is invisible in HA today.

**D32 — a failed or dropped action never blocks state application, and each action's result is
logged separately.**

*Why:* a failed announcement must not stop the lights. Coupling them makes the least reliable
action the reliability ceiling for the whole rule.

Two further properties, recorded because they are easy to assume and expensive to discover
otherwise:

- **Parameters work.** A direct `script.<name>` call passes `variables=service.data`, so script
  fields are supported (A.10). The schema carries `{ script, fields }`.
- **Context survives the indirection.** Both call paths propagate `context=service.context` into
  the script, and the script's own service calls inherit it (A.10). So D50's causation chain
  reads correctly *through* a script — "turned on by Shabbat Lights" — rather than stopping at
  the script boundary.

Scripts are actions, not triggers, so they never touch the grammar boundary (D58). The reverse
index (D57) indexes every script a schedule references, so "which schedules call
`script.pa_announce`" is answerable.

---

## 9. Storage

### 9.1 UI/YAML parity

**D33 — one storage collection, `.storage`-backed, with a code view in the UI. No
`YamlCollection`.**

HA's `helpers/collection.py` gives `.storage` persistence, websocket list/create/update/delete
and entity lifecycle sync for free; core's own `schedule` integration uses exactly this (A.6).
The code view is the automation editor's "Edit in YAML" pattern — the same object, rendered as
text.

*Why no YAML collection:* the websocket CRUD commands operate **exclusively** on the storage
collection and never see a paired `YamlCollection` — no update or delete path exists for
YAML-defined items (A.6). Such items would be structurally un-editable from the UI, which
violates parity by construction. This makes D33 load-bearing rather than a preference.

**D34 — the parity rule, enforced at review time:**

> **No field may exist in the schema that the visual editor cannot render and edit.**

*Why it needs enforcing:* the failure mode is always the same — someone adds a field, ships it
code-view-only "for now", and parity quietly dies. The schema is therefore the constraining
artifact: every schema addition is a UI commitment, and is reviewed as one.

**D35 — definition and runtime state live in separate stores.** Counters, `last_fired`,
`last_result` never live inside the object being edited.

*Why:* otherwise every edit races the engine, and saving an unrelated change can silently reset
a completion counter.

**D36 — a schedule exports and imports as a pasteable blob.** Clipboard, not file storage. It is
how blueprints actually get shared, and it matters once this is public (Q5).

### 9.2 Versioning

**D37 — schema version field and migration hook from the first commit**, alongside config flow,
`strings.json`, and a `unique_id` on every entity. An explicit *0.x, schema may change* period
is declared, so no compatibility obligation is inherited before the model has settled.

*Why now rather than at 1.0:* these four are cheap on day one and expensive to retrofit — a
`unique_id` added later cannot rename entities that already exist, and a missing version field
makes the first migration guesswork.

---

## 10. Engine semantics

### 10.0 The engine does not read the clock

**D64 — `now` is a parameter, not an ambient value. Nothing below the top-level scheduler tick
calls `dt_util.now()`, `utcnow()` or any other clock.** One component — the tick — reads the
real clock and passes the instant down. Everything beneath it (anchor resolution, interval
pairing, condition evaluation, completion counting, recovery) is a pure function of
`(now, config, known entity state)`.

*Why this is load-bearing rather than hygiene:* it is the precondition for a feature the brief
already commits to. **Dry run** (Add #5 — *"show me this Friday without waiting for Friday"*) is
exactly "evaluate the whole pipeline at a hypothetical `now`". If any layer reads the wall clock
internally, dry run cannot be built on top of the engine — it has to be a second,
parallel implementation of the same logic, which then disagrees with the real one. A timeline
that disagrees with what fires is worse than no timeline.

The second reason is testability, and it applies to precisely the decisions most likely to be
wrong: the DST rules (D40), at-or-after pairing across midnight (D38), and the shape-dependent
recovery asymmetry (D41). None of them can be tested without controlling the clock, and all
three fail visibly in front of people when wrong.

*Why it has to be decided before any code:* this constrains every signature in the engine.
Threading an injected instant through afterwards is a rewrite, not a refactor — which is why it
sits at the head of §10 rather than in a testing appendix.

Two corollaries:

- **The timeline, the dry run and the live engine are one code path** evaluated at three
  different instants. They cannot drift, because there is nothing to drift.
- **Resolvers already obey this** — D58 requires `forecast` to be a pure function of
  `(offering, window, known state)`. D64 extends the same rule inward to the engine, so the
  property holds end to end rather than stopping at the contract boundary.

### 10.1 Interval pairing

**D38 — an interval's end anchor resolves to its first occurrence at or after the resolved
start.** Not "the same calendar day".

*Why:* this one rule does a surprising amount of work.

- Candle lighting Friday pairs with havdalah Saturday automatically, across midnight, with the
  seasonal drift between them handled because both resolve independently. This is the flagship
  case, and it is the reason interval ends could not be restricted to relative offsets.
- 23:00 → 02:00 works with no special case.
- **Inversion becomes structurally impossible.** If the end is always after the start by
  construction, there is nothing to detect and nothing to skip. The only failure left is a
  genuinely missing anchor, already handled as *unresolved* (D17).

**D39 — an interval longer than its recurrence period is rejected at save. Not coalesced.**

*Why:* silent coalescing produces a timeline the user cannot predict, and predictability is the
product.

### 10.2 Time zones and DST

**D40 — clock anchors are local wall-clock.** At a DST transition, a **nonexistent** local time
is skipped and logged; an **ambiguous** local time fires on its first occurrence. Resolver and
`entity_time` anchors are absolute instants and are unaffected.

*Why state it explicitly:* this is silently wrong in most schedulers, and "the 02:30 schedule
fired twice" is exactly the visible-to-a-room failure this product exists to avoid. A rule
nobody wrote down is a rule that gets decided by accident in whichever branch ships first.

### 10.3 Restart recovery

**D41 — recovery differs by rule shape, because the shapes differ in idempotence.**

- **`During`**: on start, evaluate *should this be active now?* and reconcile. Always safe —
  this is the property that justifies the interval primitive in the first place.
- **`At`**: a missed occurrence is **logged as missed and not fired**, unless the rule opts
  into a grace window. The grace window is **off by default**.

*Why the asymmetry:* replaying a missed announcement three hours late is worse than skipping it,
while failing to restore a light that should be on is straightforwardly wrong. Defaulting both
to "fire" or both to "skip" gets one of them wrong. The brief's Keep #13 is satisfied by
reconciling what can be reconciled and *logging* the rest, which is not the same as dropping it.

### 10.4 Unresolved anchors

**D42 — an anchor entity that is unavailable is skipped and logged, the subscription is kept
live, and recovery is honoured late by the same rules as D41.**

- `During`: if the entity recovers after the would-be start but before the end, the rule
  **enters late** and reconciles. Intervals are idempotent, so this is simply correct.
- `At`: recovery after the instant reuses D41's grace window — off by default, so the default
  behaviour is to log the miss.

*Why keep the subscription rather than fail the occurrence outright:* an anchor entity that is
`unknown` for ninety seconds after a restart is the normal case, not an exception, and
discarding the whole occurrence for it would make restarts quietly destructive. Reusing D41's
machinery rather than inventing a second late-arrival policy means there is one answer to
"something showed up late", not two that can disagree.

### 10.5 Invalidation

**D43 — resolvers declare when their answers change.**

- **Deterministic** (sun, hdate): only on config change — location, diaspora, havdalah opinion.
  Cache aggressively.
- **Observational** (`entity_time`, future calendar): on entity state change; must expose a
  subscribable signal.

*Why it belongs in the contract:* a pure `forecast(window)` interface is insufficient for a live
system — the engine must know when to recompute, or it polls, or it serves a stale forecast. A
stale forecast on the timeline is precisely the failure this design exists to eliminate, so
invalidation is part of the interface rather than an implementation concern. It is also what
D42 subscribes to; the two are the same mechanism seen from different ends.

### 10.6 Enumeration horizon

**D44 — a declared window limit, default 90 days.** Beyond it the timeline says *not computed*
rather than guessing. This is distinct from a resolver's own horizon (D13): one is our compute
budget, the other is the source's honesty. The figure is a starting point to be revisited
against real enumeration cost, not a measured one.

### 10.6a Gaps closed at step 3

Seven questions the engine could not be written without. Recorded 2026-09-30, all as implemented
in step 3. Where the alternative was defensible it is named, because these are the ones most
likely to be revisited.

**D90 — restart re-entry is its own transition, distinct from entry.** D41 says a `During` rule
"reconciles" on restart; the engine emits `RESUME`, which re-applies desired state without
re-running D5's enter actions.

*Why:* a consumer that only saw `ENTER` would re-run the enter actions on every Home Assistant
restart. For the flagship case that means re-sending an IR burst and re-dimming lights someone
has since adjusted by hand, once per restart, for no scheduled reason. The distinction has to be
in the transition kind rather than in a flag on it, because the audit log (§12) has to be able to
say which of the two happened.

**D91 — disarming a rule mid-interval exits immediately and runs its exit behaviour.** D47
settles that *completion* never takes effect mid-interval; turning a rule off is not completion,
and takes effect at once.

*Why:* the principle under D47 is that a schedule must not exit leaving the world in a state it
created. Disarming a rule and leaving its interval latched until its natural end would hold the
world in that state with nothing on any surface still claiming responsibility for it — the switch
reads off, and the lights are still on because of it. Immediate exit runs `on_exit` (D3), so the
three-valued choice the user already made is what decides whether state is restored, replaced or
left.

*The alternative, and why not:* letting the interval finish is more predictable, and is what a
user who disarms a rule at 23:00 "for tomorrow" probably meant. It is rejected because it makes
*off* mean two different things depending on when it was pressed, and because D47's mid-interval
protection already exists for the case where finishing matters.

**D92 — an `At` occurrence whose anchor is unresolved produces no transition at all.** It is
logged at DEBUG and left undecided, rather than emitting a missed-fire.

*Why:* D42 says an unavailable anchor is "skipped and logged", and the engine does not know *when*
the occurrence should have happened — that is precisely what failed to resolve. Emitting a missed
transition would mean inventing an instant to hang it on, and a fabricated timestamp in the §12
audit trail is worse than no row, because the plan already shows the occurrence as unresolved
(D12). Leaving it undecided is also what allows D41's grace window to pick it up if the anchor
recovers in time.

**D93 — a schedule with no recorded evaluation point gets no lookback, whatever its grace
window.** Either it was just created or its runtime state was lost.

*Why:* the grace window exists to survive a restart that straddled an occurrence, and it can only
mean that if there is a recorded point to have straddled. Firing on first evaluation because the
rule happens to declare twelve hours of grace would be the engine inventing a past, and the
observable effect — a schedule that fires the moment it is saved — is indistinguishable from a
bug.

**D94 — the forward search for an end anchor is bounded by the recurrence period.** D38 pairs an
end anchor to its first occurrence at or after the start; that search needs a limit. It is the
shortest possible gap between two starts of the same rule, falling back to D44's horizon when the
recurrence states no period.

*Why:* it introduces no new arbitrary constant, and it is the same quantity D39's save-time check
already bounds on — an interval that ran past the next start would be the overlap D39 rejects, so
searching beyond that point can only find an end the engine would refuse anyway.

**D95 — `end: duration` is absolute, not wall-clock.** "For four hours" is four hours, including
across a DST transition.

*Why:* D40 makes *clock anchors* wall time, because 22:00 means 22:00 on both sides of a
transition. A duration is the opposite kind of statement: the user said how long, not when. An
air conditioner asked to run for four hours on the March night would otherwise run for three.

**D96 — a date window's `from` and `until` are inclusive at both ends**, in deliberate contrast
to D88's half-open resolver spans.

*Why:* the two are different kinds of object. A resolver span is computed and tiles against its
neighbours, which is what half-open is for. A date window is two dates a person typed, and a
person who types an end date means the day they named. Making it exclusive would mean a schedule
that runs "until 31 December" stops on the 30th — an off-by-one the user would have to know about
the encoding to predict. The contrast is stated here rather than reconciled because reconciling it
would mean one of the two lying about its own purpose.

### 10.7 Firing externally

**D45 — a `run_now` service takes a schedule or rule reference and an explicit
`bypass_conditions` flag**, and its executions are logged with the invoking `Context` like any
other (§12). Keep #12.

*Why a flag rather than a separate service:* condition bypass is the whole reason the service
gets used in testing, and burying it in a second service name makes the audit log read as if
conditions passed.

---

## 11. Completion

**D46 — completion decomposes into three independent axes**, replacing today's three magic names
(`repeat` / `pause` / `single`, surfaced as Stop/Delete, with "after it triggers" left undefined
for multi-slot schemes).

| Axis | Values |
| --- | --- |
| **Finished when** | never (default, D83) · one rule fired · all rules fired once (a cycle) · N occurrences · a date · a condition became true |
| **Then** | keep running · disable self · delete self · run an action |
| **Counter increments on** | every scheduled occurrence · only those whose conditions passed · only those whose actions succeeded |

The third axis is the one today's model cannot express at all, and it is what "delete after it
triggers" actually turns on: a one-shot schedule that was skipped by a condition has, under one
reading, not yet done its job.

**D47 — termination never takes effect mid-interval.** A schedule that self-disables finishes the
current interval, runs its exit path, then terminates.

*Why:* otherwise a schedule can disable itself while holding the lights on and leave them on
indefinitely. A schedule must not exit leaving the world in a state it created.

**D83 — `never` is a value on the *Finished when* axis, and it is the default.** Added
2026-09-29, after step 1 found the gap: D46 listed five terminal conditions and no way to say
there is not one.

*Why:* most schedules are perpetual. A daily blinds schedule has no completion condition and
never acquires one, and under D46 as first written that had to be expressed by omission — which
leaves the commonest case as the one with no representation. It also makes the axis unreadable
in the editor, because every value on offer is a way to end something the user was not trying to
end. Stating it explicitly gives the *Then* axis a defined value in the ordinary case instead of
being inapplicable, and it means a schedule's completion can be read off the stored record rather
than inferred from a missing key.

### 11.1 Gaps closed at step 5

Recorded 2026-09-30, as implemented in step 5 — actions, desired state, scripts and completion,
plus the tick that drives them. D102 and D103 belong to §10.7's `run_now` and D104–D106 to the
tick. They sit here because step 5 is where all of them were settled.

**D101 — the exit promise decides how an interval exits, not the rule as it currently stands.**
When an interval is entered the engine stores a copy of that rule's `on_exit` and exit actions,
keyed to the held interval, and *every* exit reads the copy.

*Why:* `ExitCause.GONE` — a rule edited away while holding — forces the copy to exist regardless,
because at that point there is no rule left to read the exit behaviour from. Once it exists,
consulting it only there is the worse of the two rules available: it means editing a rule silently
changes how an interval **currently in force** will end, so a user adjusting tomorrow's behaviour
changes tonight's, with nothing in the UI to say so. The promise reading is also the only one that
can be explained in a sentence — "it will put back what it found, because that is what it recorded
when it started" — which is the same argument `HeldInterval.end` already makes for carrying its own
end. *Rejected:* read the live rule and fall back to the promise only for `GONE`.

**D102 — `run_now` on a `During` rule enters properly when an occurrence holds at `now`, and
otherwise runs the enter actions only, leaving desired state untouched.**

*Why:* the two alternatives are both worse. Applying desired state outside any occurrence would
create state with no recorded end — nothing would ever put it back, which is precisely the orphaned
state D47 exists to prevent. Refusing `run_now` on `During` rules removes the service from the
rules that need it most: an `At` rule can be checked by waiting a second, an interval rule cannot.
Enter actions are one-shot and self-contained, so running them is the honest subset — it does the
part that has an ending.

**D103 — `run_now` ignores the enabled flag. It does not ignore conditions.** With no `rule_id`
named it runs every rule in the schedule, disabled ones included. Conditions are still evaluated,
and a condition that blocks logs at INFO and returns rather than raising.

*Why the enabled flag:* the service exists to answer "why did this not fire", and the commonest
answer is "because it is disabled". A service that refuses to run a disabled rule cannot be used
on the case it is most often reached for.

*Why not conditions:* D45 already provides an explicit `bypass_conditions` flag, for the reason
recorded there — burying condition bypass makes the audit log read as if conditions passed. Having
`run_now` bypass them silently would make that flag meaningless. And it returns rather than raises
because a blocked condition is a **result**, not a failure: a service exception surfaces in the UI
as an error when the correct answer is "conditions did not pass".

**D104 — a wake is armed even when nothing falls inside the enumeration horizon: a daily
re-enumeration.** If D44's ninety days hold no transition for any schedule, the tick still wakes
once a day and enumerates again.

*Why:* D44's horizon is measured from `now`, so it moves. A schedule whose first occurrence is six
months out has nothing inside the horizon today and something inside it in three months, and the
only thing that can notice is a re-enumeration. Arming nothing is correct about the *plan* and
wrong about the *schedule* — it would leave that schedule waiting for an unrelated schedule's tick
to happen to run. This is not the polling interval the tick's docstring rejects: the wake has a
nameable reason ("the horizon moved"), the period is a day rather than seconds, and it does no work
when the horizon is still empty. **Flagged for ruling** — the period is a judgement, not a
derivation.

**D105 — the tick evaluates disabled schedules.** A schedule with its switch off is enumerated and
reconciled like any other. What changes is that its occurrences come back unarmed.

*Why:* D91 requires that disarming mid-interval produce an immediate exit that runs the exit path.
Skipping disabled schedules produces no transitions at all, so a schedule disabled while holding
would hold forever — the lights stay on and the switch reads off, which is the failure D47 and D91
both exist to prevent. `plan.py` already marks a disabled schedule's occurrences unarmed, so the
cost is one enumeration per disabled schedule per wake, and what it buys is that *off* means the
world was put back.

**D106 — shutdown runs no exit paths.** Home Assistant stopping, or the config entry unloading,
cancels the wake and stops evaluating. It does not exit held intervals and does not restore state.

*Why:* exiting on the way down would turn the lights off on every restart for an update, which is
not what a held interval means. D41's recovery exists for exactly this: the held interval and its
exit promise (D101) live in the runtime store, so the promise survives the restart and is honoured
by whichever run is holding it afterwards. The alternative also cannot be done reliably — shutdown
is time-boxed, and a restore that is half-applied leaves a worse state than one never started.

---

## 12. Observability

**D48 — everything goes through the recorder and logbook. No private audit store.**

This is settled by verification, not merely by preference: the recorder subscribes `MATCH_ALL`
and persists custom events by default, with no allow-list and no requirement that an event carry
a logbook description (A.4). Unseen event types get a row created on the fly. The cost of being
a good HA citizen here is zero, and the alternative — a private store — would be invisible to
every tool the user already has.

**D49 — fire one rich event per occurrence.** `schedule_id`, `rule_id`, result
(fired / skipped / dropped / failed), **the label of the blocking condition** (D24), and
per-action results (D32).

*Why rich:* event data is stored, so "why didn't it fire on the 12th" becomes a recorder query
rather than a feature we have to build. Add #6 is largely satisfied by choosing the payload well.

**D50 — propagate `Context` into every service call the engine makes**, so a state change reads
as *"turned on by Shabbat Lights"* rather than appearing orphaned — including through scripts,
which inherit it (D32, A.10). For our own websocket handlers, `connection.user` gives the acting
user directly and `connection.context(msg)` a ready-made `Context`, so *who edited this schedule*
needs no machinery of our own beyond firing the event (A.4). This is the **only** source of user
attribution available: the registries record timestamps and never identities (A.9).

**D51 — ship a `logbook.py` platform with `async_describe_events`.**

*Why it matters more than it looks:* the logbook's causation chain is **generic** — the processor
resolves "what caused this" purely from context id/parent columns, with no
`if domain == "automation"` anywhere. Automations get their "triggered by" line *only* because
they ship a `logbook.py` (A.4). Shipping one buys identical native treatment for schedules.

**D52 — the last-N execution display cache is declared in `_unrecorded_attributes`.**

*Why:* attributes are recorded on every state change, so a churning history attribute would
bloat the database badly. `_unrecorded_attributes` keeps it live in the UI and out of the DB —
the right tool for a display cache, and it makes the idea cost essentially nothing.

> **Accepted consequence.** Retention is global (`purge_keep_days`, default 10); there is no
> per-entity or per-event override, and long-term statistics hold only numeric aggregates, so
> they cannot serve as an audit trail (A.4). Audit history is therefore exactly as durable as
> the user's recorder configuration — which is the user's call to make, and the right place for
> it. This must be documented, not worked around.

### 12.1 Gaps closed at step 6

Recorded 2026-09-30, as implemented in step 6. D107 **refines D49** rather than replacing it, and
the refinement was forced by a verified fact about how the logbook attributes causes.

**D107 — two events per occurrence, not one: `almanac_occurrence` before execution and
`almanac_execution` after.** The first is described to the logbook (D51), carries the `Context`
that D50 propagates, and is the one row a person reads. The second is undescribed — recorded but
producing no logbook row — and carries the per-action results.

*Why D49's "one rich event" could not stand:* D50's causation chain requires the event to exist
**before** the service calls, because `logbook/processor.py::_humanify` memoises the *first* row
carrying a context id as the cause of every later row sharing it. Fire once, after execution, and
the light is attributed to the `light.turn_on` that changed it — the uninformative line this
project exists to replace. But D49's `result` does not exist until execution has finished. The two
requirements cannot be met by one event.

*Alternatives rejected:* (a) one post-execution event — verified to lose "triggered by *Shabbat
Lights*" on the target entity's own logbook page, which is the whole of D50. (b) One
pre-execution event with no results — a failed or dropped action would then be durably recorded
nowhere, and D48 says the recorder *is* the audit trail. (c) A parent/child context pair —
verified broken: a target entity's logbook page builds its context-id set from that entity's rows
plus events whose JSON `entity_id` matches, and a parent event carrying the *schedule's*
entity_id is not in it. (d) Backdating the single event with `time_fired` — needs a clock read,
which D64's sweep forbids and rightly.

*The cost, stated:* two event types to describe instead of one, and a reader of the recorder has
to know that the pair belongs together. They share a `Context`, which is what makes the join
possible. Core makes the same trade in `components/automation/__init__.py`, which passes one
`trigger_context` to both `EVENT_AUTOMATION_TRIGGERED` and the action script.

**D108 — D52's cache lives on the switch, and N is 5.** Not on the next-trigger sensor, whose
`device_class: timestamp` more-info dialog would then be mostly about something other than its own
value. Five because the whole list is re-serialised to every connected frontend on every
occurrence, and the durable copy is the recorder's — the cache is a display convenience, and D48
is explicit that it is not the audit trail.

**D109 — `run_now` (D45) fires no occurrence event.** The `almanac.run_now` service call is
already the logbook's cause for everything that follows it, and unlike an almanac-generated event
it names the invoking user (A.9 — `Context.user_id` is the only user attribution that exists).
An occurrence event fired alongside it would compete to be `_humanify`'s memoised first row and
would win, replacing "*Daniel* ran this by hand" with "almanac decided this", which is the less
true of the two sentences. *Rejected:* firing one for symmetry.

**D110 — a schedule deleted while holding an interval has that interval released, and its runtime
record discarded, by a sweep in the tick.** The exit runs from the stored promise (D101) with
`ExitCause.GONE`, and produces the same event, context and D52 cache entry as any other exit.

*Why:* D47 says a schedule must not exit leaving the world in a state it created, and
`completion.py` guarantees that only for a schedule that ends *itself* — it defers the termination
until nothing is held. A schedule a person deletes from the UI had no such protection: `async_tick`
iterates the surviving schedules, so the deleted one was never evaluated again. The lights stayed
on and the promise sat in the runtime store with nothing left to read it, which is also an
unbounded leak — one record per schedule ever deleted.

*Why a sweep rather than the collection listener:* a listener sees the deletion once. A restart
landing between the delete and the release, or a deletion performed while the integration is not
loaded, leaves nothing to notice it. Comparing the two stores on each pass covers the listener's
case as well, so the listener would be an optimisation on ground already held. `ExitCause.GONE`
rather than a new cause, because "the thing that scheduled this is gone" is what the reader needs
to know at either level.

*What it required:* the runtime record now carries the schedule's `object_id` and name, refreshed
every pass. D49's payload needs both, and after a deletion there is nowhere else to read them
from — D66 makes `object_id` a fact about the stored schedule rather than about the registry.

### 12.2 Gaps closed at step 8

Step 8 built the timeline query (D63) and the dry run (D64's promise cashed in) — `timeline.py`,
`websocket.py`, and `AlmanacTick.async_dry_run`. It is the first code in the package that *reads*
the engine's output rather than producing it, and reading is what exposed the places where two
facts had been quietly collapsed into one.

**D116 — a manual run (D45) fires `almanac_execution` and, per D109, still fires no
`almanac_occurrence`.** The asymmetry reaches the wire: `PastOccurrence.as_dict` emits
`announced: false` for a row with no occurrence half, so a renderer can draw a manual run as the
unpredicted thing it is rather than as a prediction that came true.

*Why:* `run_now` ran real actions against real devices and left no trace anywhere the timeline
reads, because the past half is a recorder query for D107's two events and a run that fired
neither drew nothing. D63's claim is that "divergence between prediction and reality is visible
rather than something the user has to go looking for", and a run nobody predicted is the purest
divergence there is — the one kind of occurrence the engine genuinely did not foresee was the one
kind the view built for unforeseen things could not show.

*Why it does not contradict D109:* D109 refuses the *occurrence* event, and every word of its
reasoning is about the logbook — an occurrence event would win `_humanify`'s memoised first row
and overwrite "*Daniel* ran this by hand" with "almanac decided this". The execution event has no
logbook description in `events.py`, so it is not a candidate for that row and none of the argument
reaches it. The event goes out *after* the actions, exactly as on the scheduled path, which is
what keeps the `call_service` row earliest under that context and D109's attribution intact.
*Rejected:* leaving the manual run invisible, which is what it was.

**D117 — D52's status cache takes a manual run too, gated differently from the event.** The cache
records every manual transition; the event is skipped when nothing was attempted, inheriting
`_async_execute`'s gate.

*Why:* "why did the light come on at 14:32" is the question the more-info dialog is opened to
answer and "someone ran it by hand" is an answer. Leaving it out would put two surfaces that both
show *actual* — the cache and the timeline — in disagreement. The event's gate survives for its
own reason: a rule with no actions did nothing, and an event saying nothing happened is a row per
manual run that says nothing.

**D118 — an execution event with no occurrence partner is rendered, not dropped.** It is placed
from its own payload, at its own `at`, with `announced: false`.

*Why:* after D116 an unpaired execution is the *normal* shape of a manual run rather than a
damaged pair. It also arrives a second way — a window whose start falls between D107's two events
clips the first of them — and a view that states it omits nothing (D12) may not lose that row. The
drop had been justified on the grounds that an orphan "has no `kind` and no `at` and cannot be
placed", which was simply wrong about the payload: `execution_payload` repeats `schedule_id`,
`entity_id`, `rule_id`, `kind` and `at` deliberately, "rather than expecting a reader to join on
the context id".

**D119 — the timeline's coverage states are *known*, *not computed* and *unknown*.** Section 5.5
names the third one *estimated*; nothing in the engine can produce it, and this is a gap step 8
found rather than one it invented.

*Why:* `HorizonKind` is `UNBOUNDED` / `UNTIL` / `NEXT_ONLY` and `Plan` reduces the lot to one
instant, `known_through`. One instant gives you *before it* and *after it*; there is no third
region, because no declaration means "we can see this far and then it gets vague". So the states
are **known**, **not computed** (D44 — our own budget ran out and we declined to look) and
**unknown** (D13 — we looked and the source would not commit). The first two are different
sentences with different remedies, which is precisely why §10.6 keeps the two limits as separate
facts: a renderer that collapsed them would tell a user to fix their sensor when the answer is to
widen the window. Ties go to *not computed*, because that is the one the user can change — D44's
ninety days is a setting, a resolver's horizon is a property of the world.

*Rejected:* mapping `NEXT_ONLY`'s tail to *estimated* so §5.5's three words are used verbatim. It
would make *estimated* mean "one real value and then guesswork" — a silent forward projection of a
sensor's current value, in the situation where being wrong is most visible, which is the lying
§5.5 exists to forbid. **Flagged for the owner:** the other reading available is a fourth
`HorizonKind` that a resolver may declare, which is a contract change rather than a rendering one
and is therefore not being made here.

**D120 — a dry run is one evaluation of every schedule at one hypothetical instant, not a replay
of the interval between now and then.** It takes the tick's lock, reads the stored engine state,
and returns `Reconciliation` per schedule, executing nothing and writing nothing.

*Why:* the value D64 promised is that the dry run and the live tick are the same code path, and
they are — `_async_reconcile` is one function with one call site, chosen by the `live` flag (D41),
so the two cannot drift. Stepping the engine forward over successive `next_at` values would be a
second copy of `async_tick`'s scheduling loop, and the moment there are two the answer the dry run
gives stops being evidence about the one that runs. **Flagged for the owner:** "what will the
state be on Friday night" is a reasonable question this does not answer, and the honest answer to
it is the timeline, which enumerates the whole window.

**D121 — every instant crossing the websocket is a required field of the message, and a naive one
is refused rather than defaulted.** `start`, `end` and `at` are all mandatory; an aware instant is
converted to the resolver registry's zone.

*Why:* D64 gives this package one clock reader, `tick.py`, and `tests/test_design_constraints.py`
enforces that by an AST sweep whose allow-list is the definition of the constraint. A handler
sampling `dt_util.now()` for a default would break the sweep for the right reason: the moment the
backend has a second opinion about what time it is, the timeline and the dry run stop being the
same code path at three instants. The frontend is also the component that knows — the browser's
clock is the one the user is reading the screen by. Core agrees: `history` and `logbook`'s
websocket APIs both take their bounds from the message. The conversion to a real zone rather than
a fixed offset is load-bearing separately: D44's budget is added in *civil* days, and civil
arithmetic on a `…+01:00` offset silently ignores the DST transition inside the window.

*Rejected:* (a) widening the sweep's allow-list, when a read surface is the least defensible place
to do it; (b) calling `async_refresh()` and reading the instant off the engine — opening a
timeline would then fire schedules; (c) reusing the last `now` the tick was handed, which is stale
by up to a whole idle wake, so an occurrence from ten minutes ago would render in the future half.

#### What the step found rather than decided

**`known_through` was pinned to the compute budget, and that is now fixed.** `_Horizons` was built
over the *clamped* window, so for any window wider than D44's ninety days `known_through` could
not exceed `computed_through` and `fully_known` was false however unbounded every source had
declared itself. D44 already settles the question in as many words — "one is our compute budget,
the other is the source's honesty" — so this was a defect against a decision already recorded, not
a new ruling. `Plan` now reports the two independently and `Plan.solid_through` is where they are
deliberately recombined. The `span is None` early return was carrying the same conflation and is
fixed the same way: nothing was asked, so nothing declared a limit, and `known_through` is
`window.end`.

**The timeline read is one recorder query for the whole set, not one per schedule**, filtered on
the indexed `Events.time_fired_ts` rather than on the payload's `at`. The two differ by `lateness`,
and after a restart D41's recovery pass can record an occurrence materially later than the instant
it is about. Filtering on the payload would need a JSON predicate against an unindexed column;
filtering on the row's own time keeps the index and costs a bounded skew the caller can *see*,
because both instants survive into the result. Rows are ordered by the payload's `at`, because on
a timeline a row belongs where the occurrence was due.

**`Timeline.recorded` is `False`, not an empty list, when there is no recorder.** `manifest.json`
declares no hard dependency on it — almanac schedules things whether or not anything writes
history down — so a user who turned the recorder off has no past, and a blank left-hand half would
read as "nothing happened". Same argument `Plan` makes for the future half, applied backwards.

**The dry run is admin-only and the timeline is not.** The dry run takes the tick's lock, so a
caller can make the live scheduler wait; the ability to create that contention is not something to
hand to every session. The timeline is a read of what the schedule list already exposes, and core
treats its own history and logbook queries the same way.

---

## 13. Identity and entity model

**D53 — the integration domain is `almanac`.**

*Why:* an almanac is a table of *predicted* times — sunrise, sunset, holidays, festivals. That
is precisely what the resolver layer computes, and "what will happen between now and Friday
night" is an almanac query. It reads well in the places a domain actually surfaces:
`almanac.run_now`, `.storage/almanac`, and the HACS listing.

*Why not the obvious names:* `scheduler` is taken by the incumbent, which migrating users will
still have installed while the importer (D60) runs — a collision there would be fatal rather
than untidy. `scheduler_v2` puts a version number in a permanent identifier and reads as a
fork of someone else's project.

*Accepted cost:* the word "scheduler" does not appear in it, so a HACS search for that term
will not surface the domain. HACS searches repository name and description, so the mitigation
belongs there rather than in the domain.

*Verified free* — core, the HACS default list, and a GitHub manifest-domain search, 2026-09-23.
Evidence and residual risk are under "Remaining checks".

**D65 — the integration is single-instance: `"single_config_entry": true` in the manifest.**

*Why:* there is nothing per-instance to configure. Schedules and day sets are collection items,
not config entries, so a second entry would create a second empty store and two sets of services
with no way to tell them apart. Core uses this key for exactly this shape of integration — `sun`
and `jewish_calendar` both declare it, among roughly seventy others (A.11).

**D54 — one `switch` per schedule, with a slugged entity_id derived from the name.**

*Why:* today's `switch.schedule_<6 hex>` is the core of the "entities you cannot find"
complaint. A slug is not cosmetic — it is what makes a schedule referenceable from an
automation someone else wrote.

**D66 — the slug is a *suggestion*, editable at creation, and never re-derived afterwards.**

- **At creation** the editor pre-fills the entity_id from the name and lets the user overwrite
  it. The only constraint is collision: it must not already be taken by another entity.
  Validation is live, in the form, before save.
- **After creation** renaming the schedule changes the friendly name only. The entity_id is
  never recomputed, and changing it is a deliberate, separate act through HA's own entity
  settings.

*Why a suggestion rather than a derivation:* the name a schedule wants to display and the
identifier it wants to be referenced by are different strings with different audiences.
*"Shabbat — sanctuary lights & PA"* is a good display name and a terrible entity_id;
`switch.shabbat_sanctuary` is a good entity_id and a poor display name. Deriving one from the
other forces the user to compromise one to get the other, and the slug generated from a name
with punctuation in it is rarely what anyone wanted.

*Why never re-derived:* this is the more important half. Re-slugging on rename would silently
break every automation, script, dashboard card and template referencing the old entity_id — the
exact discoverability failure D54 exists to fix, inverted and made worse, because it arrives
later and without warning. Renaming for clarity is a thing users do freely and expect to be
safe. It must stay safe.

**D55 — one `sensor` per schedule with `device_class: timestamp`** for next trigger, so
templates, dashboards and automations do not have to dig through attributes. Day sets get their
own `binary_sensor` under D21.

**D56 — HA labels, categories and areas replace bespoke tags.** They are free for any entity
carrying a `unique_id` (2024.4, set via `async_update_entity`) and are searchable from the main
entity list — which is exactly what tags were failing to provide. Keep #10 is met by deleting
the feature and using the platform's.

**D57 — we build the reverse index ourselves.** "Which schedules touch `light.sanctuary`?",
"which call `script.pa_announce`?", "which reference this day set?"

*Why:* core's related-items graph is **not extensible** — `ItemType` is a closed enum, and
`search/__init__.py` imports `automation`, `script`, `scene`, `group` and `person` by name to
call each one's own helper. There is no registration hook (A.7). Our schedules will not appear
in an entity's Related tab without an upstream core PR. That PR is small and well-shaped, and is
a good candidate contribution once this is public — but it cannot be a dependency, and the
reverse index is needed internally anyway for D22's impact preview.

---

**D84 — the next-trigger sensor is `sensor.<object_id>_next_trigger`, with friendly name
"<name> next trigger".** D55 settled that each schedule has one sensor carrying
`device_class: timestamp`; it did not name it. The switch keeps the bare slug (D54); the sensor
takes the suffix.

*Why:* the same `object_id` in two domains gives the two entities an identical friendly name in
every picker, search field and automation editor in Home Assistant — which is D54's
discoverability failure reintroduced one domain over. The suffix goes on the sensor rather than
the switch because the switch is the one a user reaches for, and the shorter name should be the
one they reach for.

**D85 — `integration_type` is `service`.** There is no `integration` value to choose: core's
`Manifest` admits exactly `entity`, `device`, `hardware`, `helper`, `hub`, `service`, `system`
and `virtual`, and the default when the key is absent is `hub`.

*Why:* for a custom integration the placement is binary. `async_get_config_flows` buckets
`helper` into the Helpers tab and **everything else** into the Add-integration dialog, so
`service` already lands almanac where D54's discoverability argument wants it. Within that bucket
the value is descriptive only, and `hub` — the default — would be wrong, because almanac brokers
no devices. `helper` is arguably the truer description of what almanac is, and is rejected
precisely because it moves the integration out of the dialog where people look for it.

*Verified 2026-09-29* against `homeassistant/loader.py` in the pinned 2026.9.4 install: the
`Literal` on `Manifest.integration_type`, the `("integration", "helper")` bucketing inside
`async_get_config_flows`, and the `"hub"` fallback in the `integration_type` property. A name
list and a branch — structural facts, which Appendix B treats as trustworthy.

---

## 14. The grammar boundary

**D58 — the boundary is the resolver contract.**

A time source is admissible **iff it can implement `forecast`** — enumerate a window into
concrete spans as a pure function of (offering, window, currently-known state), without
executing user code and without simulating the world.

Templates fail it. Event triggers fail it. State triggers fail it.

*Why this formulation:* it is checkable, and it is not arbitrary. The candidate boundaries
considered earlier were worse — "six recurrence kinds" is a fence with nothing behind it, and
"anything expressible as a calendar" quietly becomes unbounded once calendars can be
template-driven. Under D58, exotic recurrence is unsupported in v1 **because nobody has written
that resolver yet**, which is a statement about work rather than about policy. It is the answer
to brief Q4.

**D59 — conditions and actions are not triggers and need not be enumerable.** Conditions need
only to be displayed and evaluated now; the timeline renders a gated occurrence as *"will fire
unless X"* rather than resolving it. Actions, scripts included, are never enumerated at all.

*Why:* this separation is what lets the trigger grammar stay tiny without crippling
expressiveness, and it is the reason D23 (no templates in conditions) costs less than it appears
to — a template helper entity is evaluated, never enumerated, so it never touches the boundary.

---

## 15. Build order

1. Schema, storage collection, entity model, config flow — D33–D37, D53–D56, D65, D66
2. Resolver contract + `clock` / `entity_time` / `sun` — D6–D17, D42–D43
3. Rule engine: `At` and `During`, pairing, DST, restart — D2–D5, D38–D41
4. Conditions and day sets — D18–D26, D57
5. Actions, desired state, scripts, completion — D27–D32, D45–D47
6. Observability — D48–D52
7. `hdate` resolver — validates the contract against a second implementation
8. Timeline and dry run — D12, D44, D63
8a. Anchor-span day sets — D122, D123, D124, and §6.2's D125–D128
9. UI — D61, D62, and §17's toolchain
9a. The delivery path — the toolchain, the Python registration, and §17.1's D129–D131
10. **Import from `scheduler-component`, last** — D60

The packaging decisions D67–D71 are settled ahead of step 1, not at step 9: the repository layout
and the panel/card URL constants they fix are part of the integration's own setup code. **Step 9a
is where they were actually built**, and §17.1 records the three things building them found —
most of which follow from D70, the constraint §17 already said could not be retrofitted.

Step 7 is placed deliberately: a contract is not proven by the implementation it was designed
around. Writing `hdate` before any UI depends on the resolver list is what surfaces a wrong
abstraction while it is still cheap to change — and it is the implementation that exercises
D10's date/datetime split and D11's two-stage day set, neither of which `sun` touches.

**Step 8a was not in the plan, and that is the argument for step 8 being where it is.** The
timeline is what made the flagship scenario measurable, and measuring it showed the rule model
could not express it (§5.8). The fix is a change to how a day set is asked about an anchor (D122)
plus a day-set source that did not exist (D124), and both are things a card would have been
written against. Numbered 8a rather than renumbered to 9, for the same reason decisions are not
renumbered: the order a thing was found in is information.

*It paid, and §5.7 records what it bought.* The contract made a promise about spans already
running at a window's start that nothing in the tree kept, and the first three resolvers could
not expose it because all their spans are zero-length. Had the UI been built first, the same hole
would have been found through a screen showing the middle of Shabbat as not Shabbat — with a
`Window.days()` signature that the timeline, the engine and a card had all been written against.

**D60 — the importer is built last, deliberately.** Best-effort, flagging what it cannot map,
with no compatibility layer and no constraint on the model. Brief Q3.

*Why last:* built earlier, the existing storage format would silently shape decisions the brief
explicitly freed us from.

---

## 16. UI surfaces

**D61 — a card and a panel, one frontend bundle.** Brief Q2.

| Surface | Carries |
| --- | --- |
| Card | per-schedule and per-area view, quick enable/disable, rule editing |
| Panel | timeline, day sets, audit log |

*Why both:* the timeline does not fit in a card, and a schedule the user cannot drop on a
dashboard is a regression against the current stack.

**D62 — the schedule editor is reachable from an entity's more-info dialog.** Together with D57,
this is what actually closes the discoverability gap.

**D63 — the timeline shows past and future in one view, with *now* as the divider.** Predicted
to the right, actual (from the recorder) to the left, and occurrences dropped by D11's precise
stage shown as *outside the set* rather than omitted (D12).

*Why:* this is the one screen the brief asks for, and merging the execution log into it means
divergence between prediction and reality is visible rather than something the user has to go
looking for.

Detailed design went to the UX designer on that basis, and `ux/FINDINGS.md` plus the prototype
in `ux/prototype/` are what came back. D72–D80 are what that round settled. They *are* a layout,
so the sentence that used to close this section — deferring layout entirely — no longer holds.

### 16.1 The occurrence track

The prototype authored schedules through a four-step wizard, and its read surfaces collapsed a
multi-stage evening into a single line. Each is defensible alone; together they mean the occasion
— *get the building ready for Shabbat* — is never drawn as one object with parts. The container
itself was never missing: D1 puts `recurrence` on the Schedule, D54 and D55 give the schedule one
switch and one sensor, and both the list card and the timeline already render one row and one
lane per schedule, not per rule. What was missing is the middle.

**D72 — the schedule editor is an anchor-relative track, not a wizard.** One rail per anchor,
stages placed as markers at their offsets, the whole occurrence visible at once. Recurrence stays
a separate control above the track, because D1 puts it on the Schedule rather than the rule.

*Why:* the unit of thought is "the evening", and an evening is a set of moments defined relative
to one or two events. A wizard makes the reader hold that shape in their head; a track puts it on
the screen. It also renders D5's rationale instead of merely asserting it — with the stages drawn
hanging off a shared spine, moving the anchor visibly moves all of them, which is the argument
for `enter_actions`/`exit_actions` as a picture.

**D73 — the track's axis is the anchor sequence, not the clock; gaps between anchors are
compressed and labelled.** Each distinct anchor gets a rail at a fixed position. Offsets lay out
linearly against a *local* scale per rail, sized to the widest offset at that anchor and rounded
to a nice unit. Between rails sits a fixed-width break carrying the real elapsed duration as text.

```
     ┌── candle lighting ──┐                      ┌─ havdalah ─┐
 ────●──────────●──────────┤≈≈≈ 25h 30m ≈≈≈≈≈≈≈≈≈─┼────●────────
    −60m       −5m         0                      0   +30m
    ACs on     lights on,                             all off
               ACs to warm
```

*Why:* the flagship case spans 25½ hours, of which 55 minutes carry every stage but one. A linear
time axis puts the two pre-lighting stages on the same pixel. Anchors are what stages are defined
against, so anchors are what the axis should be; the elapsed gap is information, but not
information that has earned proportional space. D63 already puts anchors on their own rails in
the timeline, so this is one device at two scales. Adding a stage at a wider offset rescales that
rail's local scale — animate the rescale, so the user sees it happen rather than seeing their
work jump.

**D74 — the same track renders at four sizes.** Full width in the editor; roughly 200px as a
micro-track in a list row; as the lane in the timeline; as a strip in the next-runs list. Rails
become ticks, stages become dots, labels fall away, the break becomes a hairline.

*Why:* an editor is learnable when you author in the shape you will later read. One diagram at
four sizes costs one design and is recognised everywhere it appears.

**D75 — no surface renders a bare rule.** Every list, lane, log line and more-info panel renders
the *schedule*; stages are a disclosure inside it, never a sibling row.

*Why:* this is already true of everything drawn so far, and is written down so it survives the
sixth screen. A rule has no name, no entity and no recurrence of its own, so rendering one as a
peer of a schedule invents an object the model does not have.

**D76 — rail order is computed from the next occurrence, and order instability is shown, not
hidden.** Where two anchors do not hold a fixed order across the date window — sunset and 22:00
swap across the year; candle lighting and havdalah never do — the track marks the pair and the
next-runs list is the authority.

*Why:* the order is a real property of the schedule, and the enumerable model is exactly what
lets us detect it: compute the order at each of the next N occurrences and compare. That check is
nearly free given D64's threaded `now`. Suppressing it would make the track lie about precisely
the schedules that are hardest to reason about.

**D77 — a schedule with any stage disabled never renders as plain *on*.** The row reads
"2 of 3 stages armed", the micro-track draws the disabled stage as a hollow dot, and the
next-runs list strikes that stage through.

*Why:* half-armed is the state that produces a phone call asking why the lights came on and the
air conditioning did not. It is worse than off, because off is legible.

**D78 — the editor creates its references inline and states its footprint.** Day sets, anchors,
scripts and entities are all creatable without leaving the editor, and a footprint block lists
what the schedule touches, each entry a link.

*Why:* this answers the other half of the complaint behind D72 — one goal, defined in several
places. Nothing in the schema causes that; it is purely that finishing a thought meant leaving
the screen. D57 obliges us to build the reverse index for discoverability anyway, so the
footprint is a free consumer of it, and D22's impact preview is the same idea pointed the other
way.

**D79 — the list row's payload is the generated summary; the free-text description is an
override.** `candle lighting −45m → havdalah +30m` is what a row shows by default, and a
description replaces it only where someone wrote one. This reverses `ux/FINDINGS.md` finding 13,
which had the description as the payload.

*Why:* most schedules will carry no description, and a row whose primary slot is usually empty
has no primary slot. The generated summary is always present, always accurate and cannot drift
from the rule — which also shrinks the description-drift problem down to the rows where someone
deliberately wrote something.

**D80 — the pre-flight check is described as a check, never as a guarantee.** almanac reads a
script's `mode` / `current` attributes before calling and records a skip, but the read and the
call are not atomic. UI copy must not imply that they are.

*Why:* closing that race needs engine work that is deliberately deferred to a later phase.
Deferring the work is a decision; shipping copy that claims the work was done is not.
`ux/prototype/15-new-shabbat-2.html` currently claims it, and is the thing to fix.

---

## 17. Frontend toolchain and packaging

D61 says *one frontend bundle*; this section says how it is written, built, delivered and
shipped. It is settled before step 1 rather than at step 9 because the panel and card URLs are
string constants in the integration's own setup code, and because the repository layout it
implies is expensive to move later.

**D67 — one repository, one HACS install, category `integration`.** Frontend source and build
output live under `custom_components/almanac/frontend/`, inside the integration directory. No
second repository, and no `plugin`-category registration.

*Why this works:* HACS's integration download extracts **every** file under
`custom_components/<domain>/` with no extension filter. `download_repository_zip()` matches on
`content.path.remote`, which `repositories/integration.py` sets to `f"custom_components/{name}"`;
neither that path nor the per-file fallback tree-walk looks at extensions. The one
extension-aware branch in the entire download path is guarded by `if category == "plugin"` —
the *card-only* category, which is an option for card-only repos, not an obligation on repos
that ship a frontend. Verified A.12.

*Why it needs saying:* the existing scheduler's two-repo split is widely assumed to be something
HACS forces. It is not. `thomasloven/hass-browser_mod` ships real dashboard cards
(`browser-mod-tile-card`, `browser-mod-badge`, `popup-card`, each with an editor) from a
repository registered only as an integration.

**D68 — TypeScript and Lit, bundled with Rollup.**

*Lit:* HA's own frontend is Lit, so the `ha-*` elements, the design tokens and the `hass` object
idioms are usable directly rather than through an adapter. Both single-repo precedents use it.

*Rollup, not Vite:* the build must produce a small, fixed set of ESM files at **predictable
URLs**, because those URLs are baked into Python constants and handed to `module_url` and
`add_extra_js_url`. Vite's real value is its dev server, and that is worth close to nothing here
— the card and panel do nothing except inside HA's shell with a live `hass` object and an open
websocket, so the loop is "rebuild, reload HA", never "rebuild, refresh a standalone page".
Vite's production build is Rollup underneath, so picking Rollup directly gives up only the part
that does not apply. Two code searches for a Vite-based HA integration+frontend precedent
returned nothing against a control query that worked — weak evidence, pointing the same way.

**D69 — the panel goes through `panel_custom`; the card is delivered by `add_extra_js_url`;
Lovelace resources are not touched.**

| Surface | Mechanism |
| --- | --- |
| Panel | `StaticPathConfig` for the panel bundle, then `panel_custom.async_register_panel(module_url=…)` |
| Card | `StaticPathConfig` for the card bundle, then `frontend.add_extra_js_url(hass, url)` |

`add_extra_js_url` is the only mechanism that gets a card working with **zero manual steps** in
both storage and YAML dashboard modes, and that is what the single-installation requirement has
to mean to be worth anything: one install that still needs a hand-added Resource entry has moved
the second step, not removed it. Both URLs carry `?v={version}&m={mtime}` so a rebuilt bundle is
never served from cache.

*Given up deliberately:* the card does not appear in Settings → Resources, so a user auditing
their resources will not find it; and it is unavailable to Cast, which does not receive
`extra_js_url`. `browser_mod` solves the Cast case by reaching into
`hass.data["lovelace"].resources` — private internals, with a YAML-mode fallback its own source
comments as *"not the best solution, but what else can we do"*. For an admin surface that is not
a trade worth making.

**D70 — the card entry point is a thin stub, and everything else is behind a dynamic import.**
`add_extra_js_url` injects into the shared `index.html`, so the card bundle is fetched and
executed on **every** frontend page, including every page with no almanac card on it. The entry
therefore holds only the `customElements.define` of a placeholder and the `window.customCards`
registration for the picker; the rule editor, the timeline and everything they drag in load on
first `setConfig`.

*Also:* `add_extra_js_url` is frozenset-backed, so ordering across extra JS URLs is not
guaranteed. A self-contained stub makes that irrelevant rather than something to reason about.

This is D64's shape applied to the frontend — a constraint on every signature, imposed up front
because it cannot be retrofitted. A card whose entry point already imports the editor cannot be
split later without unpicking the import graph.

**D71 — the built bundle is not committed; the release zip is the only install path.**

```json
{
  "name": "almanac",
  "zip_release": true,
  "filename": "almanac.zip",
  "hide_default_branch": true,
  "homeassistant": "2024.7.0",
  "render_readme": true
}
```

`frontend/dist/` is gitignored. The release workflow runs the build and zips from **inside**
`custom_components/almanac/`, because the `zip_release` extraction calls `extractall()` with no
prefix stripping — the archive's internal paths must already be exactly what belongs directly
under `custom_components/almanac/`. `hide_default_branch` then makes the release asset the only
thing a user can install.

*Why this deviates from both precedents:* Alarmo commits `dist/` *and* ships a zip; browser_mod
commits `dist/` and needs a dedicated CI workflow (`prevent-build-artifacts-in-pr.yaml`) to stop
humans editing it. Both are defending the same failure — committed build output drifting from
its source, and HACS shipping stale frontend code because nothing in the install path rebuilds.
With `hide_default_branch` set, a committed `dist/` serves no user at all; it serves only the
contributor who would rather not run the build, who is precisely the person most likely to leave
it stale. Not committing it deletes the failure class instead of guarding it.

*The risk taken on:* no fallback install path if the release workflow breaks. Acceptable, because
that failure is loud — a failed build produces no release asset, so HACS offers nothing new,
rather than silently shipping last month's bundle.

*Dev loop:* `rollup -c --watch` writing into `custom_components/almanac/frontend/dist/`, that
directory symlinked into a dev HA instance, and a browser refresh. None of the three precedents
has HMR. Note that the `?m={mtime}` bust is computed at integration setup, so a rebuild inside a
running session needs an integration reload, not only a refresh.

*Minimum HA version:* `2024.7.0` is the floor set by `async_register_static_paths`, which
replaced the singular `register_static_path`. It must be re-checked against `single_config_entry`
(D65) and the entity-registry `created_at` / `modified_at` fields (A.9) before the first release;
both are newer, and whichever is newest sets the real floor.

**Manifest dependencies:** `["http", "frontend", "panel_custom", "websocket_api"]` — hard
`dependencies`, not `after_dependencies`, because a setup that cannot register its panel has not
succeeded. Both single-repo precedents do the same.

---

### 17.1 What step 9a found

§17 settles the toolchain and the two delivery mechanisms. Writing them found three things it
did not say, and all three are consequences of D70 rather than of D69's table.

**D129 — one static path, for the whole `dist/` directory, not one per bundle.** D69 reads as
two registrations because there are two entry points. D70 makes that impossible: the card entry
holds nothing but a `customElements.define` and a dynamic `import()`, so Rollup emits the card's
real body as a *third* file whose name it chooses (`chunks/card-body-<hash>.js`) and which the
browser resolves relative to the card's own URL. A `StaticPathConfig` per file cannot name a file
whose name the bundler invents at build time. So `FRONTEND_URL_BASE` serves the directory, and
the two bundle constants are paths under it.

*What that costs:* `cache_headers=True` on a directory means the chunk is served with a long
cache lifetime and no `?v=&m=` query to bust, because the query is built by the Python side for
the two URLs it knows. The answer is in the Rollup config, not the Python: `chunkFileNames`
carries a content hash, so a changed chunk is a changed URL. Unhashed entry names plus hashed
chunk names is the only combination where both halves are cache-correct — the entries are named
because Python has to construct their URLs, the chunks are hashed because it cannot.

**Two Rollup configs, not one with two inputs.** A single build hoists code shared between the
card and the panel into a common chunk, and the card entry would then `import` it — statically,
on every frontend page, which is the one thing D70 exists to prevent. Two configs duplicate the
shared modules across the two output trees. That is the correct trade: the duplicated code lives
in the panel bundle and in the card's *lazy* body, neither of which is on the always-loaded path.
`rollup.config.mjs` exports an array and says so in its header.

**D130 — the panel and the card URL are undone on unload; the static path is not, and is
therefore registered once per process.** A config entry reload re-runs `async_setup_entry`, and
none of the three registrations is idempotent:

- `frontend.async_register_built_in_panel` raises `ValueError` on a second registration of the
  same url path unless `update=True` — and `panel_custom.async_register_panel`, which is what
  D69 uses, does not expose `update`. So a reload would raise unless the panel is removed first.
- `frontend.add_extra_js_url` adds to a **set**. A second call with the same string is a no-op,
  but the string contains `?m={mtime}` — so after a rebuild the reload adds a *second* URL and
  the browser loads two copies of the card, the stale one of which may win the
  `customElements.define` race. `remove_extra_js_url` raises `KeyError` on a string that is not
  there, which is why the exact string handed to `add_extra_js_url` is kept in `hass.data`
  rather than recomputed at unload.
- `hass.http.async_register_static_paths` has **no unregister**: aiohttp's router is
  append-only. A second registration of one url path is a duplicate route that nothing can take
  back.

Hence the asymmetry in `frontend_setup.py`: `async_unregister_frontend` removes the panel and the
card URL, and the static path is guarded by a `hass.data` flag and never removed. It points at a
path derived from `__file__`, which does not change while the process lives, so registering it
once is not a stale-pointer risk.

**The dist directory is resolved from `__file__`, not from `hass.config.path(...)`.** The two
agree for every real installation, but only one of them is a fact — the bundles ship *inside* the
integration directory (D67), so the integration's own module path is what knows where they are.
Building the path out of the config directory is a second claim about the same thing, and it is
the claim that breaks first: under the test harness `config_dir` is a temporary directory with no
`custom_components` in it at all, so every setup test logged a missing-frontend error.

**D131 — a missing bundle logs an error and skips registration; it does not fail setup.** D71
ships `dist/` only inside the release zip, so a clone has nothing there until `npm run build`.
Failing setup would be defensible if the frontend were the integration, but it is not: the
engine, the services, the entities and both websocket commands work without it. An integration
that refuses to load because a UI is absent is strictly harder to diagnose than one that loads
and says, in the log, that the UI is absent and how to build it.

*Flagged for the owner:* this means a broken release workflow degrades quietly rather than
loudly, which is the opposite of the trade §17 made when it chose not to commit `dist/`. The two
are consistent — §17 is about a build that produced the wrong bytes, D131 about an install that
has no bytes at all — but a user who installs a bad release gets a working integration with no
panel and one line in the log, and that is worth knowing before the first release.

**The minimum-version re-check §17 asked for is closed, with no change.** §17 wrote `2024.7.0`
as the floor set by `async_register_static_paths`; D81 later set the supported version to the
current release and derives it from the harness pin, so `hacs.json` and `manifest.json` both say
`2026.9.4` and every API step 9a uses is older than that. The three D69/D70 calls add no new
floor. `async_panel_exists` and `remove_extra_js_url` were checked against the installed
`homeassistant==2026.9.4`, not against memory.

**One dependency was added to the test harness, for a reason worth recording.**
`manifest.json` declares `frontend` and `panel_custom` as hard `dependencies` (§17, and D130 is
why they cannot be `after_dependencies` — `add_extra_js_url` would otherwise be able to run
before `frontend`'s setup creates the store it writes to). Core's `frontend` component imports
`hass_frontend` during its own setup, and `pytest-homeassistant-custom-component` does not pull
that in, so until `home-assistant-frontend` was pinned in `requirements_test.txt` every test that
set almanac up failed at *dependency resolution* — 113 errors whose message named core's
components and not a line almanac wrote. The pin matches core's `frontend/manifest.json`, by the
same one-site-packages rule as `hdate`.

---

## 18. Python baseline and test harness

**D81 — the supported Home Assistant version is the current release. There is no compatibility
floor.** `manifest.json` declares `"homeassistant": "2026.9.4"`, `hacs.json` matches, and Python
is 3.14. Older versions are not supported and no shim is written for them.

*Why:* a floor is a promise to test against versions nobody runs, and every decision in §13 leans
on recent core — D56 on the label and category registries, D65 on `single_config_entry`, D55 on
a `device_class: timestamp` sensor, and Appendix A's attribution note on entity-registry
`created_at` / `modified_at` at storage v1.15. Picking a floor would mean verifying four separate
landing versions and then carrying the oldest of them forever, to serve users who are, by
construction, not yet running this integration at all. There are none, because it does not exist.
The cost of being wrong is one line in two files.

**D82 — the test harness is `pytest-homeassistant-custom-component`, pinned exactly, and it is
what defines the supported version.** `pytest-homeassistant-custom-component==0.13.367`, which
declares `homeassistant==2026.9.4` as a hard dependency and `requires_python >= 3.14`.

*Why:* the harness ships the whole of core's test fixture machinery — `hass`, the config-entry
helpers, the storage mocks — and rebuilds itself against one exact core release. Pinning it
loosely means the fixtures and the integration can drift apart silently. Because the pin is
exact, the harness *is* the statement of what we support, and D81's version is derived from it
rather than chosen beside it: a version bump is one line, and the test run is the evidence that
it worked.

*Verified 2026-09-29:* `home-assistant/core` latest non-prerelease is `2026.9.4` (GitHub releases
API); `pytest-homeassistant-custom-component` latest is `0.13.367` with
`requires_dist: homeassistant==2026.9.4` and `requires_python: >=3.14` (PyPI JSON API). Both are
API metadata, which Appendix B's source-integrity note treats as trustworthy.

---

## Appendix A — verified facts used by this design

Verified 2026-09-22/23 against cloned source. **See Appendix B for how to verify.**

**A.1 — `jewish_calendar` ships a calendar platform.** PR
[#145140](https://github.com/home-assistant/core/pull/145140), merged 2026-05-19 (no milestone;
not pinned to a release here). Three entities: `daily_events`, `yearly_events`,
`learning_schedule` (disabled by default).

- `candle_lighting` and `havdalah` are **timed, zero-length** events —
  the event is built as `CalendarEvent(start=zman.utc, end=zman.utc)`.
- All-day events use the **civil date with zero extent** — the event is built as
  `CalendarEvent(start=target_date, end=target_date)` where `target_date` is a `date`. Both the
  evening-to-evening boundary and the conventional exclusive end are lost. The facts behind D10
  and D11.
- `async_get_events` iterates day by day computing from `hdate`: no network, no cache, no
  horizon.
- Events carry no machine-readable type, and `summary` is a **translated** string (`set_language`
  is re-applied per request) — the fact behind D8. Stable machine keys do exist, in `const.py`:
  `DailyCalendarEventType`, `YearlyCalendarEventType`, `LearningScheduleEventType`.

*Re-checked at step 7 against the installed `homeassistant==2026.9.4`.* Every encoding claim
above holds. The two helper names this entry used to cite — `_timed_event` and `_all_day_event` —
**no longer exist**: `calendar.py` now builds events in `_create_daily_event`,
`_create_yearly_event` and `_create_learning_event`. The names are removed rather than updated,
because what the design depends on is the encoding and the encoding is what was re-verified.

D8 turns out to be stronger in practice than this entry states. `hdate.translator` holds one
**process-global** language, which core's `jewish_calendar` coordinator sets from *its own*
config entry — so a resolver matching on rendered text would break because an unrelated
integration was configured in Hebrew. Nothing in `resolver/hdate.py` reads `str(holiday)`;
`Holiday.name` only.

**A.2 — sun sensors.** Six `sensor.sun_next_*` entities — dawn, dusk, midnight, noon, rising,
setting — all `device_class: timestamp` and **enabled by default** (only solar elevation and
azimuth are disabled). Each holds one value: the *next* occurrence.
`helpers/sun.py::get_astral_event_date(hass, event, date)` computes any date locally. The
asymmetry behind D6 and D13.

**A.3 — the ±4h offset limit is card-only and arbitrary.** `MAX_OFFFSET_HOURS = 4` (sic — three
F's) in `scheduler-card`'s `src/components/scheduler-time-picker.ts`; the value has been 3
(2020-09-18) → 2 → 6 (2023-03-08) → 4 (2024-05-06, changed silently inside a work-in-progress
rewrite), with no technical rationale at any point. The component's `validate_time` checks format
and the sunrise/sunset whitelist only, with **no magnitude check**. `timer.py` clamps offsets to
the day boundary rather than rolling over — the fact behind D1. **`dawn`, `dusk` and `noon` are
excluded** by the whitelist despite `sun.sun` publishing all three: the anchor gap is wider than
the brief records.

**A.4 — recorder and logbook.** The recorder subscribes `MATCH_ALL`; custom events are persisted
by default, with no allow-list and no logbook-registration gate. The only filters are a
user-configured `exclude: event_types:` and an entity filter that applies solely to events
carrying an `entity_id`. `logbook.py` + `async_describe_events` is the display extension point;
the causation chain in `ContextAugmenter` is generic, not automation-specific. `purge_keep_days`
(default 10) is global only — no per-entity or per-event override; long-term statistics survive
purge but hold only numeric aggregates. `_unrecorded_attributes` is a class-level frozenset
opt-out. `connection.user` and `connection.context(msg)` are available in our own websocket
handlers.

**A.5 — `async_reproduce_state` coverage.** `helpers/state.py` dispatches to per-domain
`reproduce_state.py` platforms; the set includes climate, light, switch, fan, cover, humidifier,
media_player, lock, vacuum, water_heater, number, select, all five `input_*`, and
alarm_control_panel. A domain without a platform logs a warning rather than failing. Separately:
`climate/reproduce_state.py` issues up to **7 sequential blocking service calls** and does not
skip attributes already matching current state — the fact behind D28's override.

**A.6 — `helpers/collection.py`.** `StorageCollection` / `DictStorageCollection` plus
`StorageCollectionWebsocket` / `DictStorageCollectionWebsocket` give `.storage` persistence,
websocket CRUD and entity lifecycle sync (`sync_entity_lifecycle`). Core's `schedule` and
`input_*` integrations use the pattern — a storage collection as source of truth with entities
projected from it, which is what D21 follows. Websocket CRUD touches **only** the storage
collection — a paired `YamlCollection` has no update or delete path — the fact behind D33.

**A.7 — the search / related-items graph is not extensible.** `ItemType` is a closed `StrEnum`,
and `search/__init__.py` imports five core components by name. No registration hook exists.

**A.8 — upstream refusal, still the justification for building.** Entity-based time anchors were
explicitly refused in
[scheduler-component#154](https://github.com/nielsfaber/scheduler-component/issues/154) — *"I
don't consider this as a good addition… I don't want to go this route."* This is a rejection, not
a gap, and it is the fact that makes contributing upstream unavailable for the single feature the
first user most needs.

**A.9 — the registries record *when*, never *who*.** `helpers/entity_registry.py` carries
`created_at` and `modified_at` (added in registry storage version 1.15) on both live and deleted
entity entries. There is **no** `created_by`, `modified_by` or `user_id` field anywhere in it.
User attribution in HA exists only as `Context.user_id` on events, reaching the recorder. The
fact behind D21's caveat and D50.

**A.10 — script call semantics.** From `components/script/__init__.py` and `helpers/script.py`:

- The `script.turn_on` service calls `async_turn_on(..., context=service.context, wait=False)` —
  it starts the script and returns immediately.
- Calling `script.<name>` as a service goes through `_service_handler`, which calls
  `_async_start_run(variables=service.data, context=service.context, wait=True)` — it waits for
  completion, and the service is registered with `supports_response=SupportsResponse.OPTIONAL`,
  so it can return data. `variables=service.data` is what makes script fields work.
- Both paths pass `context=service.context` into the run, so context propagates through a script
  into the calls it makes.
- `DEFAULT_SCRIPT_MODE = SCRIPT_MODE_SINGLE`. When a `single`-mode script is already running,
  `_async_start_run` logs `"Already running"` at the `max_exceeded` level and **returns without
  raising** — the caller cannot tell the difference from a successful no-op run. The fact behind
  D31.
- Script entities expose `ATTR_MODE` (`mode`), `ATTR_CUR` (`current`), `ATTR_MAX` (`max`),
  `last_triggered` and `last_action` as state attributes, which is what makes D31's pre-flight
  check possible.

**A.11 — `single_config_entry` is a real manifest key, verified 2026-09-24.** Roughly seventy
core integrations declare `"single_config_entry": true` in `manifest.json`, including `sun`,
`jewish_calendar`, `moon`, `backup`, `analytics`, `mqtt` and `knx`. Confirmed by grep over
`homeassistant/components/**/manifest.json` in the `b2f1fad` clone. The precedent shape is a
hub-like integration with nothing per-instance to configure — which is D65.

**A.12 — HACS ships whatever sits under `custom_components/<domain>/`, frontend included.
Verified 2026-09-24** against clones of `hacs/integration`, `hacs/documentation`, `hacs/default`,
`nielsfaber/alarmo`, `thomasloven/hass-browser_mod` and `AlexxIT/WebRTC`. This is the whole
foundation of D67, so it was re-verified directly rather than taken from a researcher's report.

- **`hacs.json` schema** — `HACS_MANIFEST_JSON_SCHEMA` in `utils/validate.py:46-60` accepts
  exactly `content_in_root`, `country`, `filename`, `hacs`, `hide_default_branch`,
  `homeassistant`, `persistent_directory`, `render_readme`, `zip_release`, and requires `name`,
  with `extra=vol.PREVENT_EXTRA`. `validate/hacsjson.py:43-44` raises if `zip_release` is set
  without `filename`.
- **Default download path** — `download_content()` (`repositories/base.py:616-627`) calls
  `download_repository_zip()` whenever there is no `zip_release`; that method
  (`base.py:653-697`) pulls GitHub's own source tarball, keeps only entries under
  `content.path.remote`, strips the leading segment and `extractall()`s. For an integration,
  `repositories/integration.py:91,131` sets `content.path.remote = f"custom_components/{name}"`.
  **No extension filter anywhere in this path.**
- **The one extension-aware branch** is `if category == "plugin":` in
  `gather_files_to_download()` (`base.py:1205-1222`), which special-cases `dist/` and `.js` for
  card-only repos. Integration category falls through to the generic tree-walk
  (`base.py:1229-1234`), which again takes every non-directory file under `content.path.remote`.
- **`zip_release` path** — `should_try_releases` (`base.py:446-459`) is true only once
  `zip_release` is set, `filename` ends in `.zip`, *and* the ref is not the default branch. Then
  `async_download_zip_file` `extractall()`s the release **asset** straight into
  `content.path.local` with **no prefix stripping** (`base.py:594-598`), which is why Alarmo's
  release job zips from inside `custom_components/alarmo/`.
- **No two-repo requirement exists.** `docs/publish/integration.md` states only that there must
  be one integration per repository and that everything required to run it must live under
  `custom_components/INTEGRATION_NAME/`. Nothing about frontend assets, no file-type exclusion.
- **Worked precedent for D69/D70**, `browser_mod/mod_view.py`: `StaticPathConfig` +
  `add_extra_js_url(hass, FRONTEND_SCRIPT_URL + "?" + version)` for the card bundle,
  `StaticPathConfig` + `async_register_built_in_panel(component_name="custom",
  config={"_panel_custom": {...}})` for each panel, and `remove_extra_js_url` on unload. It
  additionally registers a Lovelace resource via `hass.data["lovelace"].resources` under the
  comment *"so it's accessible to Cast"*, with a YAML-mode fallback its own source calls *"not
  the best solution"* — the part D69 declines.
- **Alarmo's cache-bust**, `alarmo/panel.py`: `module_url=f"{PANEL_URL}?v={VERSION}&m={cache_bust}"`
  where `cache_bust = int(os.path.getmtime(view_url))`. Adopted in D69.
- **`async_register_static_paths` floor** — `WebRTC/custom_components/webrtc/utils.py:113-121`
  gates on `(MAJOR_VERSION, MINOR_VERSION) >= (2024, 7)`, falling back to the singular
  `register_static_path` below that. The singular form is gone from current `dev`.

One caveat, per Appendix B: the *structure* above — which branch is guarded by what, which
parameter names exist, which paths have no filter — is what these citations establish. Where a
precedent's string literals matter (`"almanac"`, a URL) they are this project's own, not quoted
from third-party source.

**A.13 — Python compares and subtracts two aware datetimes sharing a `tzinfo` *object* by wall
clock, ignoring the offset. Verified 2026-09-30 by execution** on the pinned CPython 3.14.2,
which is out-of-band evidence in Appendix B's sense: the interpreter's behaviour is not a content
read.

On 2026-11-01 in `America/New_York`, with `a = 01:30 fold=0` (EDT) and `b = 01:15 fold=1` (EST) —
genuinely forty-five minutes apart:

- `a.tzinfo is b.tzinfo` → `True`. `ZoneInfo` interns its instances, so *every* instant this
  integration produces for one location takes this path. It is not an exotic case.
- `b < a` → `True`, and `b - a` → `-15 minutes`. Converted to UTC first, `b > a` and the
  difference is `+45 minutes`.

This is specified, not a defect: PEP 495 defines intra-zone comparison as wall-clock so that a
zone's own ordering stays total, and `fold` is deliberately ignored. It therefore cannot be waited
out and has to be compared around.

*What it decided:* `absolute()` in `resolver/contract.py`, through which every comparison and
subtraction of two instants in the integration passes, and the reason it lives in the contract
rather than the engine. Found at step 3 by a test: a four-hour `During` across the spring-forward
read as five hours. The autumn case is worse than wrong — an interval ending inside the repeated
hour looks inverted, so a correctly built interval fails its own invariant. D40 settles what the
semantics are; this is what stops the implementation reaching the opposite answer by accident.

**A.14 — `ObservableCollection.notify_changes` *awaits* its listeners. Verified 2026-09-30**
against the pinned `homeassistant` 2026.9.4 in `.venv`, `helpers/collection.py` — the same file as
A.6. It is one `asyncio.gather` over every registered listener and change-set listener, awaited
before the write returns.

*What it decided:* D46's *Then* axis runs **outside** the tick's lock. `disable` and `delete` write
through the schedule collection, the tick registers its own re-evaluation handler as a listener, and
that handler takes the tick's non-reentrant lock — so a termination carried out inside the locked
pass deadlocks the tick on a lock the same tick is holding. Found by reading the helper before
writing the call rather than by observing the hang, which is the only reason it is a note here and
not a defect in the history. A second benefit fell out: every runtime record is already persisted
when a `then` fires, so the re-evaluation its write triggers sees the state this pass decided on
rather than the state it started from.

---

## Appendix B — source-integrity note

**Corrected 2026-09-24. The earlier version of this appendix was wrong**, and the correction
matters more than the original claim.

**What was recorded on 2026-09-22:** HTTP fetches of source were being altered —
`import voluptuous as vol` came back as `import probatio` from both `raw.githubusercontent.com`
and the GitHub REST contents API — while a `git clone` was *"unaffected, verified by grep across
four files."* The working rule drawn from that was "read the bytes from a clone."

**What is actually true.** Clone reads are altered too. In the clone at
`b2f1fad07dc60ee1592bb46d7a1b467a49283624`, `components/http/__init__.py` reads `import
probatio` with 18 further `probatio.*` call sites, and `requirements.txt` line 41 reads
`probatio==0.12.1`. The substitution is uniform across source and dependency metadata, so the
original "clones are clean" finding cannot be reproduced and should be treated as mistaken.

**But the stored bytes are genuine.** Three facts settle where the rewrite happens:

1. `git fsck --no-progress` on the clone passes silently — every object's SHA-1 matches its
   content, so nothing was mutated on disk after checkout.
2. `b2f1fad07dc60ee1592bb46d7a1b467a49283624` is a real upstream commit on
   `home-assistant/core` — *"Update uv to 0.12.15 (#182961)"*, 2026-09-23T09:32:07Z, confirmed
   through the GitHub API.
3. A commit hash cryptographically determines its entire tree. If upstream's tree at that hash
   contains `voluptuous`, and our objects hash consistently to that same commit, then the
   objects **contain `voluptuous`**.

So the rewrite is applied when file content is rendered into the agent's context — not in
transit, and not on disk. Transport is irrelevant, which is why choosing a clone over HTTP
bought nothing.

> **Corrected working rule.** *Content reads are unreliable, whatever the transport. Establish
> **which** bytes you have cryptographically, and treat structure as trustworthy but verbatim
> third-party identifiers as suspect.*
>
> - **Trustworthy:** git object hashes, `git fsck`, commit shas, and GitHub API *metadata*
>   (commits, PRs, releases, listings). These are what exposed the problem.
> - **Trustworthy in practice:** *structural* facts — function and class names, signatures,
>   parameter lists, file organisation, line numbers, and the presence or absence of a symbol.
>   A token-level rename of one unrelated library cannot change any of them.
> - **Suspect:** any verbatim quotation, especially of a third-party identifier. Pin the commit
>   sha next to the claim so a reader can check it themselves.

**What this does and does not invalidate.** Appendix A's facts are structural — key names,
signatures, presence/absence, counts — and stand. The `almanac` namespace check also stands: it
rested on the *absence* of a string, and a rename of `voluptuous` neither creates nor conceals
`almanac`. What would not be safe is quoting a dependency pin or a third-party import verbatim
and relying on the spelling.

**Unresolved, and worth knowing.** The environment cannot self-certify: every observation
channel passes through the same layer that does the rewriting. Only an out-of-band check — the
same file opened by a human, or a checksum computed elsewhere — can establish the true bytes.
The one substitution observed so far is `voluptuous` → `probatio`, a library this project does
not depend on semantically.

A blobless shallow clone is still the right way to get source, and is a single command:

```
git clone --depth 1 --filter=blob:none --no-tags \
  https://github.com/home-assistant/core.git core-full
```

Sparse checkout narrows it further, but `sparse-checkout set` needs a second command run inside
the clone, so the one-liner above is usually the faster route.

---

## Remaining checks

Every open question from the brief and from the first draft of this document is now settled.

1. ~~**Confirm `almanac` is free**~~ — **done 2026-09-23, clear on all three checks** (D53):
   - **Core:** no `homeassistant/components/almanac`, and `grep -ri almanac` over the whole
     `homeassistant/` tree returns nothing. Clone of `home-assistant/core` at
     `b2f1fad07dc60ee1592bb46d7a1b467a49283624` (`dev`), 1526 integrations present.
   - **HACS:** `almanac` appears in neither `integration` (3264 entries) nor `removed`, in a
     clone of [`hacs/default`](https://github.com/hacs/default).
   - **Declared domains:** GitHub code search for `almanac iot_class filename:manifest.json`
     returns nothing, where the same query with `scheduler` returns
     `nielsfaber/scheduler-component` and others — so the empty result is a real negative, not
     a broken query. This is the check that matters, because the HACS list holds repository
     names and a repository may declare any domain it likes.

   *Residual risk, accepted:* GitHub code search indexes only default branches of public repos
   and is not exhaustive, so this is strong evidence rather than proof. Nothing further is
   worth doing before a name is in use.

2. **Revisit the 90-day enumeration horizon** against measured cost once the resolvers and
   timeline exist (D44). Not blocking — this is future work, not a check.
