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

**Do not mirror core's all-day encoding.** `_all_day_event` builds
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

---

## 6. Day sets

**D18 — a day set is a named, first-class object referenced by schedules.**

```
DaySet
  id, name, owner        (recorded, not enforced)
  source : Weekdays | Dates | NthWeekday | EveryN | ResolverOffering | Composition
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
| **Finished when** | one rule fired · all rules fired once (a cycle) · N occurrences · a date · a condition became true |
| **Then** | keep running · disable self · delete self · run an action |
| **Counter increments on** | every scheduled occurrence · only those whose conditions passed · only those whose actions succeeded |

The third axis is the one today's model cannot express at all, and it is what "delete after it
triggers" actually turns on: a one-shot schedule that was skipped by a condition has, under one
reading, not yet done its job.

**D47 — termination never takes effect mid-interval.** A schedule that self-disables finishes the
current interval, runs its exit path, then terminates.

*Why:* otherwise a schedule can disable itself while holding the lights on and leave them on
indefinitely. A schedule must not exit leaving the world in a state it created.

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
9. UI — D61, D62, and §17's toolchain
10. **Import from `scheduler-component`, last** — D60

The packaging decisions D67–D71 are settled ahead of step 1, not at step 9: the repository layout
and the panel/card URL constants they fix are part of the integration's own setup code.

Step 7 is placed deliberately: a contract is not proven by the implementation it was designed
around. Writing `hdate` before any UI depends on the resolver list is what surfaces a wrong
abstraction while it is still cheap to change — and it is the implementation that exercises
D10's date/datetime split and D11's two-stage day set, neither of which `sun` touches.

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

## Appendix A — verified facts used by this design

Verified 2026-09-22/23 against cloned source. **See Appendix B for how to verify.**

**A.1 — `jewish_calendar` ships a calendar platform.** PR
[#145140](https://github.com/home-assistant/core/pull/145140), merged 2026-05-19 (no milestone;
not pinned to a release here). Three entities: `daily_events`, `yearly_events`,
`learning_schedule` (disabled by default).

- `candle_lighting` and `havdalah` are **timed, zero-length** events —
  `_timed_event` builds `CalendarEvent(start=zman.utc, end=zman.utc)`.
- All-day events use the **civil date with zero extent** — `_all_day_event` builds
  `CalendarEvent(start=target_date, end=target_date)` where `target_date` is a `date`. Both the
  evening-to-evening boundary and the conventional exclusive end are lost. The facts behind D10
  and D11.
- `async_get_events` iterates day by day computing from `hdate`: no network, no cache, no
  horizon.
- Events carry no machine-readable type, and `summary` is a **translated** string (`set_language`
  is re-applied per request) — the fact behind D8. Stable machine keys do exist, in `const.py`:
  `DailyCalendarEventType`, `YearlyCalendarEventType`, `LearningScheduleEventType`.

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
