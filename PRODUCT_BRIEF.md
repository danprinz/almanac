# Product brief — almanac (scheduler v2)

**Status: requirements only.** Nothing here is a technical decision. No stack, no schema,
no architecture. The open questions at the end were settled on 2026-09-23 — each now carries a
one-line answer and a pointer, with the reasoning in [`DESIGN.md`](DESIGN.md).

## The problem

Home Assistant's scheduling story splits badly. Native automations are fully general and
therefore opaque — you cannot ask them what will happen on Friday. The `scheduler-component`
stack is legible but hemmed in: sun-only time anchors, one service call per timeslot,
conditions that hide inside attributes so a gated schedule still displays as "daily", and
completion modes whose names don't match what they do.

The result is that a schedule you can read is a schedule you can't express, and a schedule
you can express is one you can't read.

## Who it's for

Heavy HA users who schedule real-world routines with genuine conditionality — a building
whose heating, cooling, lighting and announcements follow a calendar that isn't just
"weekdays at 07:00". The first user runs a synagogue: times derive from zmanim sensors,
many actions are gated on occupancy or manual override toggles, and being wrong is visible
to a room full of people.

## What success looks like

One screen answers: *between now and Friday night, what will fire, when, and what could
stop it.* Everything else in this brief exists to make that screen possible and truthful.

---

## Keep

Table stakes. The current stack does these, and v2 is a regression without them.

1. **Schedules are entities** — enable/disable from automations, real state, survive restart.
2. **UI-first.** YAML is never required for the common case.
3. **Weekday sets**, including workday / weekend / daily.
4. **Timeslots** with a start and an optional stop.
5. **Schemes** — the multi-slot 24-hour bar.
6. **Conditions** with and/or grouping.
7. **Re-evaluate when conditions change** (today's `track_conditions`).
8. **Sun anchors** with ± offset.
9. **Date windows** — active from / until.
10. **Tags** for grouping schedules.
11. **Completion behaviour** — repeat, disable-self, delete-self.
12. **Fire externally** — an equivalent of `scheduler.run_action`, including a condition bypass.
13. **Restart recovery** — a slot missed while HA was down is reconciled, not silently dropped.
14. **Overview list** with a next-trigger countdown.

## Fix

Present today, but wrong often enough to hurt.

1. **Trigger honesty.** A schedule gated on a helper toggle displays as "daily". The summary
   must state the real firing rule. Conditions cannot live only in attributes.
2. **Completion-mode naming.** UI says Stop/Delete, storage says `pause`/`single`, and for a
   multi-slot scheme "after it triggers" is undefined. Name it for what it does and define
   it for schemes.
3. **Condition re-evaluation granularity.** Should react to the overall and/or result
   changing, not to any individual condition flipping.
4. **One service call per timeslot.** The single-`service_data` limit is the root cause of
   the script-per-permutation explosion users hit with multi-attribute devices.

## Add

The reasons to build rather than fork.

1. **Arbitrary time anchors.** Any datetime- or timestamp-valued entity, ± an offset —
   "45 minutes before candle lighting". Refused upstream, so it can only come from here.
2. **Heterogeneous actions per timeslot** — several different services, across domains,
   in one slot, composed in the UI.
3. **Desired-state actions.** A slot declares what the world should look like
   (entity → attributes) rather than which services to call, and that is reconciled against
   current state. See open question Q1 — this may be v2 rather than v1.
4. **Timeline.** Every schedule, resolved across a chosen window, showing what fires when
   and which conditions gate it.
5. **Dry run at a hypothetical time.** "Show me this Friday" without waiting for Friday.
6. **Execution log.** Per schedule: fired or skipped, and *which condition* skipped it.

## Non-goals

- **Not a general automation engine.** No arbitrary templates or event triggers in the
  trigger grammar. This is the constraint that buys the timeline and the dry run — every
  trigger must be resolvable to concrete times over a future window, or neither can exist.
  Anything needing real templating stays an HA automation that fires a schedule.
- **Not a replacement** for HA automations, scripts or scenes. It should compose with them.
- **No new device-control layer.** Reconciling desired state is in scope; reimplementing
  per-integration atomicity is not — that belongs in the device integration.
- **Not a fork.** Compatibility with the existing card is not a goal. Migration from
  existing schedules might be; see Q3.

---

## Open questions — all settled 2026-09-23

The questions are kept as written. Each now carries the answer in one line, with a pointer to
the decision in `DESIGN.md`. The reasoning lives there, not here.

**Q1 — Is desired-state (Add #3) in v1, or v2?**
It is the difference between a scheduler and a scheduler plus a reconciliation engine, and
it shapes how actions are stored. A middle path exists: ship service calls in v1 but shape
the storage so desired-state can land later without a migration.

> **Answered: v1, and the question dissolves.** Action kind is bound to rule shape — intervals
> take desired state, moments take service calls. What defers to v2 is *continuous
> reconciliation*, as a per-rule flag needing no storage change. `DESIGN.md` D27–D29, §8.2.

**Q2 — What is the UI, concretely?**
A Lovelace card, a custom panel, or a config-flow-driven settings UI. The timeline may not
fit comfortably in a card, and that constraint may decide it.

> **Answered: both, one frontend bundle.** Card for per-schedule and per-area views, panel for
> the timeline, day sets and audit log. Layout goes to the UX designer. `DESIGN.md` D61–D63.

**Q3 — Migration from `scheduler-component`?**
Import existing schedules, or start clean? Affects whether the storage model must stay
close to the existing one.

> **Answered: start clean.** A best-effort importer is built last, flagging what it cannot map,
> with no compatibility layer and no constraint on the model. `DESIGN.md` D60, §15.

**Q4 — How far does the trigger grammar go before it stops being enumerable?**
Recurrence beyond weekly (every-other-week, nth-weekday-of-month, offsets from calendar
events) is desirable but each addition must preserve the ability to enumerate a window.
Needs a boundary drawn deliberately, not discovered later.

> **Answered: the boundary is a type signature, not a list.** A time source is admissible iff
> it can implement the resolver contract's `forecast(window) -> spans` as a pure function,
> without executing user code. Exotic recurrence is then unsupported because nobody wrote that
> resolver yet — a statement about work, not policy. `DESIGN.md` §5, D58–D59.

**Q5 — Single-user tool or public integration?**
Whether this targets HACS distribution changes the weight on config flow, translations,
docs and backwards compatibility from day one.

> **Answered: public eventually, private until ready.** Config flow, `strings.json`,
> `unique_id` and a schema version ship from the first commit, because they are cheap now and
> cannot be retrofitted. An explicit *0.x, schema may change* period defers the compatibility
> obligation until the model settles. `DESIGN.md` D37.
