# almanac — UX findings

What drawing the model taught us about the model. Twenty screens in
[`prototype/`](prototype/), forty-five PNGs in [`screenshots/`](screenshots/), and this
document, which is the deliverable.

Screens 13–17 were added after the first twelve, to five situations named directly: a
container card being configured, a cover schedule that skips holidays and Shabbat, a four-step
wizard for a two-stage candle-lighting schedule, a container card listing schedules with their
descriptions, and one schedule showing its next N runs. They are the source of findings 12–18.

Written against `DESIGN.md` decisions D1–D71 as of 2026-09-24. Nothing outside `ux/` was
modified. Every change this document wants is written here as a **proposal against a decision
number**, never applied.

**Read this first if you read nothing else:** [Scenario A, the flagship, produces zero
occurrences](#1-the-flagship-scenario-produces-zero-occurrences) under the model as decided.
That is finding one and it is not a rendering problem.

---

## Contents

1. [How to read the prototype](#how-to-read-the-prototype)
2. [What works](#what-works)
3. [What doesn't](#what-doesnt)
4. [What I had to invent](#what-i-had-to-invent)
5. [The nine questions](#the-nine-questions)
6. [Naming](#naming)
7. [Components used](#components-used)
8. [Icons](#icons)
9. [How the screenshots were captured](#how-the-screenshots-were-captured)

---

## How to read the prototype

Open [`prototype/index.html`](prototype/index.html). No build step, no framework, no npm. The
only JavaScript is a theme toggle and an inline SVG sprite.

Colour comes from Home Assistant's own tokens, copied from `home-assistant/frontend` at commit
`d8effd44b2bced4479112fef0ce5a60291d9cbd3` (2026-09-24). Every value in
[`prototype/assets/tokens.css`](prototype/assets/tokens.css) carries the `path:line` it came
from. The prototype never hard-codes an HA colour: it derives an `--al-*` semantic layer from
HA tokens with `color-mix`, so re-theming the host re-themes the prototype.

One thing to know about that file. **Dark mode in HA is not a `prefers-color-scheme` media
query.** The dark values live in a JS object (`darkColorVariables`,
`src/resources/theme/color/index.ts`) and are written onto the root with
`element.style.setProperty` by `src/common/dom/apply_themes_on_element.ts`. The prototype uses a
`:root[data-theme="dark"]` block, which is a faithful *result* and a dishonest *mechanism*. Real
almanac code must not add a media query; it inherits whatever the host has set. Same for the
`--rgb-<key>` tokens — HA derives those from the hex at theme-apply time, so an integration must
never ship its own.

Per `CLAUDE.md`'s source-integrity rule, treat the structural claims in this document (which
file defines what, which symbols exist, which do not, signatures, counts) as reliable, and treat
any verbatim third-party identifier as pinned to the sha quoted beside it.

---

## What works

These are not compliments. They are the parts of the model that survived being drawn, listed so
that the next section is read as narrow rather than general.

**The two-shape rule model (D2) renders.** `At` and `During` are genuinely different pictures — a
mark on a line and a span on a line — and once you accept that the picture is the primary
artefact, the model falls out of the picture rather than the other way round. Screens 01 and 02
never needed a third shape.

**Threading `now` as a parameter (D64) is the single highest-value decision in the document.**
It is invisible in the UI and it is what makes screen 08 credible. The dry run is not a
simulator that can disagree with the engine; it is the engine with a different clock and its
writes discarded. That sentence is on the screen because it is the entire reason to trust the
number the screen shows. No other decision in `DESIGN.md` buys as much UX for as little.

**Anchors as a tagged union with signed, unclamped offsets (D6, D7).** The anchor picker
(screen 04) is the screen that could most easily have been a mess, and it is not, because there
is exactly one concept: *a source publishes times, you shift by an amount*. "45 minutes before
candle lighting" and "three days after" use the same control. The card's arbitrary
`MAX_OFFFSET_HOURS = 4` would have needed its own explanation and its own error state; having no
magnitude check at all needs neither.

**Addressing offerings by machine key, never display text (D8).** This never shows up on a
screen, which is the point. It is why screen 04 can show translated labels without the
selection meaning anything different in Hebrew.

**Horizons as three named classes (D13).** UNBOUNDED / UNTIL / NEXT_ONLY maps onto three words
a user already has — *always* / *until a date* / *the next one only* — and onto three visual
weights. The legend in screen 04's left rail is four lines long and explains a genuinely hard
idea (this source cannot see past Tuesday) without using the word "horizon".

**Rendering excluded occurrences rather than omitting them (D12).** See Q4 — it works, but only
in a specific rendering. Counting it as a win with that caveat.

**Structured conditions with no templates, ever (D23).** Every screen that shows a condition
shows it as a sentence, and it is a sentence *because* there is no template to fall back to. The
moment one rule can hold a Jinja string, every list view has a row it cannot summarise and every
editor has a field that has to be validated by trying it. D23 is why screen 05 can put 40
schedules on one page.

**One rich event per occurrence (D48, D49) plus a `logbook.py` (D51).** Screen 07 is entirely
built from a payload that already exists in the design, and screen 09's logbook lines read as
sentences only because D51 is shipped. The observability design is ahead of most integrations
and it shows.

**Day sets as first-class shared objects (D18) with one level of algebra (D19).** The flat
three-step list in screen 06 ("Start with… / Except…") is only possible because nesting is
banned. Allow one level of nesting and the editor becomes a tree widget, and the impact preview
stops being computable in a sentence.

**`binary_sensor` per day set (D21).** Free, and it is what lets screen 06 end with a concrete
"Elsewhere" row rather than a promise.

---

## What doesn't

Each item names the decision it pressures, what breaks, and a proposal.

### 1. The flagship scenario produces zero occurrences

**Pressures:** D1, D11, and the brief's own §5 Scenario A.
**Screens:** 02 (rule editor), 04 (anchor picker), 08 (dry run).

Scenario A as the brief specifies it is: a `During` rule, start `hdate:candle_lighting` **−45m**,
end `hdate:havdalah` **+30m**, day set **Shabbat**.

D11 evaluates in two stages: `candidate_dates` generates, then `covers(instant)` filters. So:

1. `candidate_dates(shabbat)` yields the Friday.
2. The start anchor resolves to Friday **17:47** — candle lighting at 18:32, minus 45 minutes.
3. `covers(Fri 17:47)` is asked of the Shabbat set. Shabbat begins at 18:32. **17:47 is before
   it.** `covers` returns false.
4. The occurrence is discarded as *outside the set*.

Every week. Forever. The flagship rule — the one the brief says must be effortless or the product
has failed — never runs, and by D12 it does not even fail loudly: it renders as a ghost.

Worse, this is not a bug you can fix by redefining the day set, because **the day set cannot be
both things at once:**

- If `shabbat` is **instant-granular** (a span from candle lighting to havdalah), it is what
  D11's own motivating example needs — "at 22:00 on Shabbat fires twice, Friday night and
  Saturday night, and not at 22:00 Saturday morning" — and it excludes the entire pre-Shabbat
  setup window, which is the only reason anyone writes this rule.
- If `shabbat` is **date-granular** (the Friday and the Saturday, whole), the −45m start is
  covered and the flagship works — and D11's motivating example breaks, because 22:00 Saturday
  morning is now inside the set.

The design has one name for two different objects. Screen 08 renders this as the product would
have to: a large amber **0**, *"This rule never runs. Not once in 30 days. It came due 4 times
and was thrown away every time"*, each discarded instant labelled with the candle-lighting time
that excluded it, and the diagnosis in one line — *"the day set decides whether, the rule decides
when, and here they disagree by 45 minutes — every week, by exactly the amount of the shift."*

**Proposal, against D11 — two changes, either of which fixes it, both of which are probably
wanted:**

- **(a) `recurrence: anchor`.** Let a rule's recurrence be *"whenever the start anchor occurs"*,
  with the day set applied to the **anchor's own date** rather than to the shifted instant. The
  offset then shifts the action without moving the membership test, which is what a user means
  by "45 minutes before Shabbat". This is the smaller change and the one I would take.
- **(b) `hdate` publishes both granularities** — `shabbat` (instant-granular, candle lighting →
  havdalah) and `shabbat_days` (date-granular, the civil days it touches) — as two offerings with
  distinct keys, so the user picks the one they mean and the picker can explain the difference
  once.

Whichever is taken, the editor needs the guard rail screen 02 shows: **resolve the anchor and
test membership at edit time, not at first fire.** A rule that will never run must say so while
you are writing it.

**Note for D10.** This is the concrete case D10's `date | datetime` span typing was for, and it
is not sufficient on its own — knowing a span is instant-typed does not tell the engine whether
to test membership before or after the offset. That is the decision D11 is missing.

### 2. `on_exit: restore` restores a value it does not own

**Pressures:** D3, and §8.2's ownership model.
**Screen:** 11.

`on_exit: restore` captures state at interval **entry**. If a rule enters while another rule's
interval is already live, it captures *that rule's* value, not the pre-almanac value — and when
it exits it re-applies a stale reading of a value it never owned. Because v1 reconciles at edges
only (D28), nothing ever corrects it.

Screen 11 renders the specific case. Two `During` rules on `climate.sanctuary`: *Shabbat —
sanctuary* 17:47 → Sat 20:08 at 22°C, and *Boiler — Shabbat mode* 18:00 → Sat 18:30 at 18°C. The
thermostat reads 22 → 18 → 18 → **16**. The last figure is the one nobody asked for: Shabbat puts
back the 16°C it read at 17:47, Boiler is long gone and will not re-assert 18°C, and it is
Saturday night.

Note what the screen deliberately does **not** do. It does not invent a priority system.
Last-writer-wins is not a policy choice here, it is a consequence of D28 — edges are the only
moments anything is written — so the honest rendering is a strip showing what the device will
actually read, not a ranking UI. The finding is not "there should be priorities". It is that
**`restore` has no concept of ownership.**

**Proposal, against D3 — three options, in preference order:**

- **(a) A per-entity ownership stack.** Each live `During` rule holding an entity pushes a frame.
  `restore` pops *its own* frame and re-applies whatever is now on top — the next rule still
  holding the entity, or the captured pre-almanac value if the stack empties. This is the only
  option that makes the screen's second radio ("Put back what was there, not what I found")
  implementable, and it is the one I would take. It requires almanac to remember **who** set a
  value, not only **what** it was, which is a schema addition, not a UI one.
- **(b) Warn at save.** Detect overlap on the same entity when a rule is written, and say so.
  Cheap, and it does not fix anything already saved.
- **(c) Make continuous reconciliation non-optional for multiply-held entities.** Correct, and it
  reintroduces exactly the write-storm D28 avoids. Not recommended for v1.

At minimum, whichever is taken, D3's documentation must say *restore returns the value seen at
entry, which may be another rule's*. Right now "restore" promises something it does not do.

### 3. `latch` is inert in the flagship, and the editor shows it anyway

**Pressures:** D4.
**Screen:** 02.

Scenario A specifies `latch: true`. Scenario A has no conditions. D4's latch governs exit when
the **activation predicate** goes false — so with no conditions in the predicate, `latch` cannot
ever be consulted. The brief asks for a control that does nothing.

This is a small finding with a sharp edge: a toggle that is present, settable, saved, and
provably without effect is worse than an absent one, because the user will reason about it later
when something misbehaves.

**Proposal, against D4:** `latch` is not a field on the rule form. It is a question that appears
**inside the conditions block, and only once a condition exists** — see the naming table. If a
rule has no conditions, the schema may keep the key, but the editor must not render it and the
YAML view should mark it inert.

### 4. D46's third axis is not independent of its first

**Pressures:** D46.
**Screen:** 12.

D46 gives three axes: *Finished when* · *Then* · *Counter increments on*. Rendered literally
that is three dropdowns side by side — a truthful picture of the model and a bad screen, because
**axis 3 is meaningless unless axis 1 is a count.** If a schedule finishes on a date, or when a
condition becomes true, "what counts as a run" has nothing to count.

Worse, the three options for axis 3 (*when it starts* / *when it finishes* / *only if it
actually did something*) are indistinguishable in the abstract. Users do not have an opinion
about them until they see that they produce different numbers.

**Proposal, against D46 — keep all three axes in the schema, change the rendering:**

- Four presets across the top (Never · After a few times · On a date · When something is true),
  which is what 90% of users will touch and nothing else.
- Below, the choice as a **sentence with inline selects** — *"Stop after it has run `[3]` times.
  Then `[turn it off]`."*
- **Axis 3 appears only when axis 1 is a count**, and each option shows **its own running
  count** — *when it starts · 3 so far* / *when it finishes · 2 so far* / *only if it did
  something · 1 so far*. The difference between the three becomes self-evident the moment they
  disagree, which they will for any rule with conditions.
- D47's promise (termination is never mid-interval) belongs on this screen in prose. It is the
  question the count immediately raises.

Expressiveness lost: none. Settings pages avoided: one.

### 5. Optional condition labels (D24) will be empty, and empty is load-bearing

**Pressures:** D24, and by extension D49.
**Screens:** 02, 07.

D24 makes the condition label optional. Optional text fields in a rule editor are empty. But the
label is not decoration — screen 07's whole argument depends on it. *"'cleaning crew present' was
true"* is an explanation; *"binary_sensor.cleaning_crew == 'on'"* is a log line the user has to
decode at the moment they are least inclined to. And D49 puts the blocking condition's label in
the event payload, so an empty label degrades the audit trail, not just the form.

**Proposal, against D24:** make the label **non-empty by construction**.

- Derive a label from the condition structure as it is built — *"Dan is home"*, *"cleaning crew
  present"* — and show the derived label as the collapsed title immediately.
- Let the user overwrite it. The field is pre-filled, not optional.
- Store `label` plus `label_source: derived | user`, so a later change to the condition can
  refresh a derived label and must not touch a user-written one.

The user never faces an empty box, and D49's payload is never empty either.

### 6. D49 says an action was dropped without saying who is holding the thing

**Pressures:** D49, D31.
**Screen:** 07.

D31 pre-flights scripts because a `mode: single` script that is already running logs *"Already
running"* and returns without raising — so the caller cannot tell it from success. Good. D49
records that the action was dropped. Also good. But *"`script.pa_announce` was already running"*
is the start of a question, not an answer: the user's next move is to find out what started it,
and nothing in the payload helps.

**Proposal, against D49:** extend the dropped-action record to
`dropped_because: { script, held_by }`, where `held_by` is resolved at pre-flight time from the
script's `last_triggered` and `Context.parent_id`. Screen 07 renders it as *"started 17:46 by
Evening PA — announcements"*, which closes the question in the line that raised it. The data is
already reachable; only the payload shape is missing.

### 7. Nothing is logged for a rule that never came due

**Pressures:** D48, D49.
**Screen:** 07.

This is the observability counterpart of finding 1, and it is why finding 1 would have shipped.
The event stream is per-**occurrence**. A rule whose day set never covers its start anchor
generates no occurrences, therefore no events, therefore **no rows** — and the log is exactly
where a user goes when a rule did not run. The system is silent precisely when it is asked the
question it exists to answer.

**Proposal, against D48/D49 — cheapest sufficient fix:** the log carries a permanent, unmissable
affordance for the absent case. Screen 07 renders it as an info banner above the entries:
*"Looking for a rule that isn't here at all? Nothing is logged for a rule that never came due.
Run it forward to see whether it ever does."* — linking to the dry run with that rule preloaded.

A stronger fix, if it is affordable: emit a distinct low-rate event when a rule's scheduling pass
produces **zero** occurrences within the horizon, so "this rule is inert" is a state the system
knows and not only a thing a user can go and discover. Screen 05 lifts such rules into its
attention band, which requires the engine to know.

### 8. D28's per-rule service-call escape hatch is on the wrong surface

**Pressures:** D28.
**Screen:** 07 (where it belongs), 02 (where it does not).

`During` bodies are desired state, reconciled through `async_reproduce_state`, with a per-rule
override to emit raw service calls instead. In the editor that override is a checkbox whose
label can only be honest if it explains reconciliation, which means the second field in the
actions block teaches the user about `reproduce_state`.

It is not an authoring choice. It is a **diagnostic** — you reach for it after watching something
not take. Surface it from the execution log, on a rule that has failed to apply, where the
sentence is *"this device didn't take the state we set; send it as a direct command instead"* and
means something. Keep the schema key; move the control.

### 9. D22's impact preview protects the editor and nobody else

**Pressures:** D22.
**Screen:** 06.

D22 requires an impact preview when a day set is edited. Screen 06 renders it: `14 → 12` days,
the two dates removed, **6 schedules across 3 owners**, and the four named schedules that stop
happening.

That protects the person doing the editing. It does nothing for the other two owners, who find
out when the room is cold. A shared object (D18) means editing it is not a private act, and the
preview as specified is a private act with a conscience.

**Proposal, against D22:** saving a day-set edit emits an event per affected schedule, so each
appears in **its own** logbook — *"Days changed by Daniel: 2 fewer this quarter"*. This is free.
The D48/D49 event machinery and the D51 `logbook.py` already exist; only the emission point is
new. Screen 06 renders it as a nudge under the preview.

### 10. `ha-textfield` does not exist, and HA is mid-migration

**Pressures:** D68.

See [Components used](#components-used). `ha-textfield` — probably the single most-reached-for
element in any integration's editor — is **absent** at sha `d8effd44…`, along with `ha-fab`,
`ha-tabs`, and a generic `ha-chip`. The frontend is migrating to
`@home-assistant/webawesome`, and the element inventory is moving under it.

This does not break D68's toolchain choice. It does mean an almanac editor written against
today's element names will acquire breakage that looks like almanac's fault. Proposal: pin the
element inventory in the repo with the sha it was verified at, re-verify on each HA minor, and
prefer `ha-form` + `ha-selector` (both present, both stable, both the layer HA itself treats as
public) over hand-assembling controls.

### 11. D62's more-info entry point exists — but not the one you would look for

**Pressures:** D62.
**Screen:** 09.

This one was checked in source rather than reasoned about, and the answer changed what the screen
could be. Both files at sha `d8effd44b2bced4479112fef0ce5a60291d9cbd3`.

**The obvious route does not exist.** In
`src/dialogs/more-info/state_more_info_control.ts` the per-domain control map is a **closed object
literal** of core domains, and `domainMoreInfoType()` returns `"default"` for anything not in it.
There is no registration hook. A custom `almanac` domain gets the plain attributes-and-history
dialog, and no amount of frontend code changes that.

**A different route does exist.** `src/dialogs/more-info/more-info-content.ts` checks the
entity's **attributes first**, before dispatching on domain: if the attribute
`custom_ui_more_info` is present, its value is treated as an element tag name and rendered via
`dynamicElement`, and the domain control is never consulted. Since almanac already ships an
always-loaded bundle (D70), it can register such an element. **This is the entry point for D62,
and structurally it is the only one.**

Two consequences the design should record:

- **almanac owns the body of that dialog and nothing around it.** The header, the cog, the
  history and logbook tabs are HA's. Screen 09 draws almanac's region inside a dashed border to
  make the boundary explicit; everything outside it is out of reach.
- **The Related tab cannot list almanac's entities.** The related-items graph's `ItemType` is a
  closed enum with five core components imported by name and no registration hook (recorded in
  `CLAUDE.md`'s verified facts). So almanac's own cross-links have to live in the panel, and the
  more-info dialog will always have one tab that is emptier than the user expects.

**Proposal, against D62:** record `custom_ui_more_info` as the mechanism, with the sha, and record
that it depends on D70's always-loaded stub — which makes D70 load-bearing for two features, not
one. It also means the attribute has to be published on every almanac entity, which is a schema
consequence, not a frontend one.

Per the source-integrity rule: the *structure* above (which file checks what, in what order,
absence of a hook) is the reliable part. The attribute key is quoted verbatim and is therefore the
one thing to re-check against the sha before writing code.

### 12. Upstream's tag filter is an OR with reserved words in the same namespace

**Pressures:** D18, D20.
**Screen:** 13.

The brief said to look at how `scheduler-card` does tag filtering, so this was read rather than
recalled. All line numbers below are in `nielsfaber/scheduler-card` at sha
`83b6dec82d3237fb2d85a70a2b861578165d13b6`, and per the source-integrity rule the structural
claims (which file, which branch, which key is absent) are the reliable part; the three reserved
words are quoted verbatim and should be re-checked against that sha before any code depends on
their spelling.

`src/data/schedule/is_included_schedule.ts` is the whole filter, and it is 38 lines. What it does:

- **Matching is OR, and only OR.** Line 24 is `(schedule.tags || []).some(e =>
  filterTags.includes(e))`. There is no `every` branch and no mode field anywhere in the config
  type, so "the schedules that are both `sanctuary` and `kiddush`" is not expressible.
- **Three reserved words share the tag namespace.** Lines 25-27 test `filterTags.includes('none')`,
  `'enabled'` and `'disabled'` against the same list the real tags come from, with no escape
  syntax. `src/scheduler-card.ts:350` strips the same three when computing the tag options, which
  confirms they are meant as reserved. The collision is one-directional but real: a schedule
  genuinely tagged `none` cannot be filtered for, because that string is intercepted.
- **`exclude_tags` exists, but only in YAML.** It is in `src/types.ts:20` and honoured at
  `is_included_schedule.ts:31`; it is **not** in `src/scheduler-card-editor.ts`, whose only tag
  control is the `tags` selector at line 60. So exclusion is invisible to anyone using the UI
  editor. (Noted in passing: the validator for it, `src/data/validate_config.ts:91`, tests
  `config.tags` rather than `config.exclude_tags` in both of its type checks -- a wrong-key
  copy-paste, which means a malformed `exclude_tags` is not caught.)
- **A typo becomes a new tag.** The editor's selector sets `custom_value: true`
  (`scheduler-card-editor.ts:64`), so a mistyped tag is accepted rather than rejected, matches
  nothing, and the card renders empty with no error. The failure mode reads as "almanac is
  broken", not "that tag does not exist".

Screen 13 answers each of these on one surface: **any / all** as an explicit radio pair rather
than an undocumented default; **Except when tagged** promoted out of YAML into the editor, which
is where the upstream option already should have been; a separate **Turned on or off** dropdown so
`enabled`/`disabled` stop being tags at all; and a live preview with a count (`3 of 14 schedules
match. 1 is hidden by archived.`) so a filter that matches nothing says so before it is saved. The
amber row is the typo case -- `sanctury` named as matching nothing, with the near-miss offered.

**Proposal, against D20:** store the match mode explicitly (`any` | `all`) from the first schema
version, keep the on/off filter out of the tag namespace, and require the card editor to show a
match count. None of this is expensive, and all of it is unrecoverable later: a bare list that
ships as an implied OR cannot gain an `all` mode without silently changing the meaning of every
card already saved.

### 13. The description is the payload, and the default card width truncates it

**Pressures:** D61.
**Screen:** 16.

Requested set 4 is "a list of schedules with their descriptions", and drawing it showed that the
description is not a decoration on the row -- it *is* the reason to look at the card. A column of
names is a directory.

Measured in the prototype: two lines of description at HA's body size need about **460px** of
card width before the second line truncates mid-sentence. HA's masonry default is a **340px**
column. At 340 the rows on screen 16 lose the end of every description -- "otherwise waits until
07:30. Heats Sanctuary..." -- and what is lost is the half that says something the name does not.
The light-mobile capture of screen 16 shows this happening at phone width, where there is no
wider column to move to.

This is a real fork and `DESIGN.md` does not take it. Either the description is the default and
the card documents that it wants a wide column (and behaves acceptably when it does not get one),
or the default row is name-and-next-run and the description is opt-in. Screen 13's **Each row
shows** radio pair exists because the screen could not be drawn without choosing, and it chooses
to make the fork the user's.

**Proposal, against D61:** ship both row shapes, default to name-and-description, and have the
card's `grid_options` declare a minimum column width so HA's own sections layout gives it the
space rather than the user discovering the truncation. Do not attempt to auto-shorten the text;
a truncated reason is worse than no reason, because it reads as complete.

### 14. A day-set term that removes nothing has to be named as inert, not accepted

**Pressures:** D19, D22.
**Screen:** 14.

Requested set 2 is "every weekday that isn't a holiday or Shabbat", and writing it exactly as
asked produces a rule with a term that does nothing: if the start set is Sunday-to-Friday,
**Except Shabbat** removes no days at all, because Saturday was never in the set and 07:15 on a
Friday is hours before candle lighting.

The dangerous part is that this is *correct*. The engine will do the right thing, the preview
will look right, and the user will believe a protection is in place that is in fact carried
entirely by the start set. Change the start set to include Saturday later and the term wakes up
-- or does not, depending on the time of day, which is worse.

D22's impact preview is the machinery that detects this; what was missing was that the preview
must be shown **on the term**, at the moment it is added, not only in a separate confirmation.
Screen 14 marks the term `removes nothing` in amber on the row itself, and the notice states the
reason in the user's own terms with both buttons -- **Remove it** and **Keep it anyway** -- because
keeping a defensive term against a future edit is a legitimate choice.

**Proposal, against D22:** define "inert term" as a first-class result of the impact computation
(a term whose removal does not change `candidate_dates()` over the preview window), surface it on
the term row, and record in the stored rule that the user was told. Without the last part the
notice fires again on every edit and becomes noise.

### 15. The horizon has to be printed in the footer, and the two footers must be readable against each other

**Pressures:** D13.
**Screen:** 17.

D13 already says a resolver declares UNBOUNDED, UNTIL(date) or NEXT_ONLY, and "What works" above
credits the three-word rendering in the picker. Screen 17 found the harder half: on a card that
prints a list, the horizon is not a property of the picker, it is a property of **the end of the
list**, and the two cases have to be distinguishable at a glance or neither is trustworthy.

The screen carries both, deliberately, one above the other. The candle-lighting schedule is
unbounded -- pure `hdate` computation, no network -- and its footer says the list can be extended
as far as you care to look. The blinds schedule depends on a holiday list read from a calendar
entity, which genuinely runs out, and its footer has to say where. A card that keeps printing
confident rows past its own horizon is the worst thing this surface can do, because the rows past
the edge look exactly like the rows before it.

A second case appeared here that the picker never shows: **a clock change inside the window**.
The enumeration correctly produces 16:57 on Fri 23 Oct and 15:50 on Fri 30 Oct, an hour and seven
minutes earlier, because Israel's DST ends on Sun 25 Oct. That discontinuity is right and looks
like a bug, so the card states it. Note the failure this nearly shipped with: the first draft of
that footer quoted **candle lighting** (16:35) instead of the schedule's **start** (15:50). Both
are real numbers for that date, the sentence parsed, and it was wrong. Any footer that quotes a
time must quote the same quantity as the rows above it.

**Proposal, against D13:** require every enumerating surface to render a horizon footer, give
UNBOUNDED its own wording rather than silence (silence is indistinguishable from "we did not
check"), and add a DST-crossing note as a derived annotation of the enumeration rather than
something an author writes per schedule.

### 16. An action row must show which route delivers it, because the routes fail differently

**Pressures:** D28, D31.
**Screens:** 15-2, 15-3.

The request named this itself -- "ACs can be controlled by a script or climate device" -- and
treating it as an implementation detail behind one abstraction is not available, because the two
routes fail in ways the user has to be able to tell apart:

- **Set the device.** HA has no atomic set-full-state for `climate`. `climate.set_temperature`
  takes an optional `hvac_mode`, and nothing takes `fan_mode` alongside anything else, so "temp
  and fan" is **two** service calls per unit and either can be refused on its own. The row can
  therefore end up half-succeeded, and almanac must report per attribute, not per row.
- **Run a script.** D31's pre-flight. A script already running under the default `mode: single`
  logs "Already running" and **returns without raising** -- the caller cannot distinguish that
  from success. Script entities expose `mode` / `current` / `max` as attributes, which is the only
  way to know before calling.

Screen 15-2 puts one unit on each route, side by side, so the comparison is concrete rather than a
paragraph of help text, and each row carries its own consequence inline. The route toggle lives in
the row header, not in a settings page, because it changes what the row can promise.

**Proposal, against D28 and D31:** make the delivery route an explicit per-action field in
storage rather than an inference from what the target is, and make the execution record carry a
per-attribute result for the device route and a pre-flight outcome (`skipped`, not `done`) for the
script route. Finding 6 asks for something adjacent; this is the same principle applied one level
down.

### 17. Amber has to mean exactly one thing: this can silently not happen

**Pressures:** D46, D49.
**Screens:** 13, 14, 15-2, 16.

By the time these five screens existed, amber was being asked to carry four unrelated jobs: a tag
that matches nothing (13), a day-set term that removes nothing (14), a script whose mode can make
it a no-op (15-2), and a schedule that was skipped last week (16). Only three of those are the
same thing.

The rule the screens settled on: **amber means the thing you are looking at can silently not
happen.** Not "failed" -- red is failure, and screen 16's boiler row is red because an action was
refused and the refusal is known. Not "off" -- disabled is dimmed, because a schedule someone
turned off is not a warning. Amber is reserved for the case where the system will report success,
or report nothing, and the user's expectation will still be wrong.

This matters more than a palette note because the silent-no-op cases are exactly the ones this
project exists to fix, and a colour that also means "heads up" stops being read within a week.

**Proposal:** state the three-colour contract in the design doc (red = known failure, amber =
silent no-op, dim = deliberately inactive) and treat any new amber use as requiring a reason that
fits it.

### 18. Fixture data in a prototype that claims to enumerate real moments has to be computed

**Pressures:** none -- this is a finding about the prototype, and about anything that demos this
integration.

The first draft of screens 14–17 used hand-written dates. Every screen looked plausible on its
own. They were wrong in a way no individual screen could reveal: the whole grid was shifted by one
day, so every date labelled "Friday" was in fact a Saturday, and screens 14 and 17 disagreed about
how many days away the same date was.

The fix was to compute the fixture -- the Hebrew calendar by the standard fixed arithmetic, checked
against three independent Rosh Hashanah anchors, and sunset by the NOAA algorithm, checked against
an equinox estimate and against the DST step appearing on the right date -- and rebase every screen
on it. `now` is Friday 18 September 2026, early evening, and it is stated in each file's header
comment.

Two things improved as a result rather than merely becoming correct, which is the argument for
doing it this way:

- **Erev Yom Kippur genuinely falls on Sunday 20 September 2026.** Screens 15 and 17 had been
  *asserting* that candle lighting is not always a Friday. Now the first run the wizard produces
  is a Sunday, unedited, and the claim is paid off by the enumeration instead of by a sentence.
- **Both Saturdays in screen 14's fortnight are also festivals** -- Sukkot day 1 on Sat 26 Sep and
  Shmini Atzeret on Sat 3 Oct. That strengthens the same screen's "Except Shabbat removes nothing"
  argument with dates rather than reasoning.

**Proposal:** any demo, screenshot set or documentation example for almanac pins an explicit
`now` and derives every date from the engine, never by hand. D64 -- nothing below the top-level
tick reads a clock, `now` is threaded as a parameter -- is what makes this cheap, and it is a
second reason to honour D64 from the first function.

**Known inconsistency, stated rather than hidden.** Only screens 13-17 were rebased. Screens
01-12 still carry the original hand-written fixture, so the same schedule reads *"until 20:08
tomorrow"* on screens 03, 05 and 09 and *"until 20:03 tomorrow"* on 13, 16 and 17. The five
shared clock times differ by five or six minutes throughout (sunset 18:50/18:44, candle lighting
18:32/18:26, havdalah 19:38/19:33, start 17:47/17:41, end 20:08/20:03).

This was left rather than patched, deliberately. Substituting the five times is trivial; the
dates are not -- screen 08 enumerates specific Fridays, and screen 01's timeline positions its
axis anchors by computed percentage, so a real rebase means recomputing geometry across about
175 date-bearing lines. Doing the cheap half would attach real times to invented dates and
produce exactly the individually-plausible, collectively-wrong state this finding is about. The
rebase of 01-12 is therefore outstanding work, not a decision.



---

## What I had to invent

The brief calls this the highest-value output. Each of these is a place where a screen could not
be drawn without deciding something `DESIGN.md` does not decide. They are listed as invented, not
as settled, and each is flagged in a comment at the top of the screen that needed it.

### How a dry run treats conditions

**Nothing in `DESIGN.md` says.** And there is no defensible default: evaluating conditions
against *current* state ("is Dan home right now?") tells you nothing about next Tuesday, while
ignoring conditions gives you a forecast the engine will not honour.

Invented, on screen 08, as an explicit **three-way control** in the run sentence — *"Treat
conditions as `[whatever they are now]`"*, with the other two being *assumed met* and *set by
hand*. Making it a visible control rather than a hidden default is the actual proposal here;
which of the three is the default matters much less than that the user can see which one ran.

This needs a decision number. It touches D64, because a dry run and the live engine are one code
path, which means the condition-evaluation strategy has to be a **parameter threaded exactly like
`now`** — and that constrains engine signatures, so it cannot be retrofitted.

### Notifying the owners of affected schedules

Finding 9's proposal. `DESIGN.md` has no concept of an event emitted by an **edit** rather than by
an occurrence. Invented as a logbook line per affected schedule.

### The ownership stack

Finding 2's option (a). `DESIGN.md` §8.2 has an ownership model; it does not have a stack, and it
does not say what `restore` restores when the captured value belongs to somebody else. Invented
to make screen 11's second option implementable, and flagged on the screen as *"a decision nobody
has taken yet"*.

### Which runs count, shown as three live counters

Finding 4. Showing each axis-3 option with its own current count is not in D46 and is the only
thing I found that makes the axis comprehensible. It implies the engine maintains three counters,
not one, which is a real implementation cost.

### The attention band at the top of the schedule list

Screen 05 lifts failing, unresolvable and repeatedly-skipped schedules **out of the sort order**
into a band that cannot be scrolled past. `DESIGN.md` has no concept of a schedule being in a bad
state as a first-class queryable thing — it has per-occurrence events, which is not the same.
Rendering 40 schedules made it obvious that *sorted correctly* and *impossible to miss* are
different requirements, and that the second one needs engine support (see finding 7's stronger
fix).

### Default sort order for the schedule list

Invented: **by next occurrence**, not alphabetically, not by creation. This follows from the
product thesis — if the claim is that you can see what will happen, the list is a schedule, not a
directory — but it is not written down anywhere, and every existing scheduler UI does it the
other way.

### Resolving and membership-testing the anchor at edit time

Screen 02 and screen 04 both show the anchor's resolved value for the next occurrence and, where
it applies, a warning that the resolved instant falls outside the rule's own day set. This is the
guard rail that catches finding 1. `DESIGN.md` describes resolution as a runtime activity.
Rendering it at edit time is new, and it requires the editor to be able to call `forecast()`
synchronously enough to type into.

### The word "resolver" is never shown

See [Naming](#naming). Not strictly an invention, but a standing decision applied across every
screen, and it constrains the extension API's user-facing vocabulary. Somebody writing a
third-party resolver needs to know that their `domain` string becomes a heading in a picker.

---

## The nine questions

### Q1 — Can a gabbai build Scenario A without documentation? Where exactly does it break?

**No, and the break is not where you would expect.**

Everything up to the point of saving is fine. The sentence stems ("From … until …"), the anchor
picker's five named sources, and the offset row ("45 minutes before candle lighting") are all
reachable by a competent non-technical user without help. I watched the screen assemble itself in
plain language and did not have to reach for jargon once.

**It breaks at the day set**, and it breaks silently. The user picks the day set called
*Shabbat* — which is correct, obvious, and the only sane choice — and produces a rule that never
fires (finding 1). There is no error, no empty state, and no red. The model discards the
occurrence as *outside the set*, which is the model working exactly as designed.

A gabbai cannot recover from this without understanding two-stage evaluation, which they should
never have to. **The fix is finding 1's proposal, not a documentation page.** Until then the
editor must, at minimum, refuse to save silently: resolve the first occurrence at edit time and
say *"as written, this never runs"*.

Secondary break, much smaller: `latch` (finding 3). The user is asked a question with no
consequence, which costs nothing now and costs a support conversation later.

### Q2 — Does `At` / `During` need to be visible?

**Yes, it must be visible, and it must not be a mode switch.**

It has to be visible because **D27 binds action kind to rule shape**: `At` takes service calls,
a `During` body takes desired state, `During` edges take service calls. A UI that inferred the
shape would silently change what the actions block *means* underneath the user. That is not
inferable — it is a different form.

But it should not be presented as a choice between two technical shapes. Screens 02 and 10 make
it the opening of a sentence:

- **"At `[a time]`, do this."**
- **"From `[a time]` until `[a time]`, keep things like this."**

The verb carries the distinction — *do* versus *keep* — which is exactly D27's binding expressed
in grammar.

**On conversion:** `At` → `During` is **lossless** (a moment becomes the start of a period;
service calls become `enter_actions`). The reverse is **lossy** — a body of desired state has
nowhere to go, and exit actions vanish. Offer only the lossless direction, as *"make this last a
while"*. Offering both and warning on one is worse than offering one.

### Q3 — At 40 schedules, what is the unit? What drops first?

**Two units, not one, on the same screen** (screen 01 at scale; screen 05 for the list):

1. **A ribbon of swimlanes for schedules with activity in the visible window only.** Not one row
   per schedule — that is O(n) height and mostly empty. In any 36-hour window a 40-schedule
   installation has maybe 8 active, so the ribbon is effectively O(1) and it is the view that
   answers "what is happening".
2. **A merged, day-grouped agenda beneath it**, which is the view that answers "what happens
   next" and is the only view that works on a phone.

Per-entity swimlanes were tried and rejected: entities are the axis on which conflicts appear
(screen 11) but they fragment a single schedule across four rows, and the user's mental unit is
the schedule.

The **list** (screen 05) is sorted by next occurrence, grouped by area, with problems lifted out
of the sort into the attention band.

**What drops as density rises, in order:**

1. Action detail (*"heat to 22°C"* → nothing; the colour already says climate).
2. Condition text (the condition *indicator* stays).
3. Entity lists collapse to counts (*"3 lights"*).
4. Lane labels truncate to two lines, then to the schedule name alone.

**What never drops, at any density:** the time; the *now* divider; and **anything not green** —
skipped, dropped, failed, unresolvable and outside-the-set survive every reduction. Density
degrades **toward exceptions**. A dense timeline should look emptier of successes and exactly as
loud about problems.

### Q4 — Does "outside the set" read as an explanation or a bug?

**As a bare label it reads as a bug. As a ghost occurrence carrying the boundary that excluded
it, it reads as an explanation.** The difference is one sentence and it is the whole answer.

*"Outside the set"* on a greyed row invites the reading *the software lost my rule*. The same row
rendered as a ghost with *"excluded — Shabbat begins at 18:32, this is 17:47"* invites the
reading *ah, by 45 minutes*. Screens 01 and 08 use the second form throughout; the ghost always
names the boundary and the gap.

Two supporting points:

- **It should only ever appear for instant-granular sets.** For a date-granular set, an excluded
  occurrence is just a day the rule does not run, and showing a ghost for every non-Tuesday would
  be noise. Ghosts are for the case where the *date* qualified and the *instant* did not — which
  is precisely the confusing case, and precisely finding 1's case.
- **D12's authoring nudge belongs inline**, on the ghost itself, not in a help page: *"want this
  to run anyway? change the days, or move the time inside them"* with both fixes as buttons.

Caveat worth stating plainly: this reading works *because* the ghost is rare and explained. If
finding 1 is not fixed, the first user meets a ghost every single week on their most important
rule, and no amount of copywriting survives that.

### Q5 — Does the card earn its existence?

**Partly. I disagree with D61 as stated.**

The card earns its existence as a **status object**: state, next occurrence, quick enable/disable,
and a compact time nudge (±15m) for the common "tonight only, a bit later" adjustment. Screen 03's
compact form is that, and it is worth having on a wall tablet.

The card does **not** earn a full rule editor, for two reasons:

1. **It collides with D70.** The card is a thin always-loaded stub with a dynamic import. Putting
   the editor in the card either inflates the stub — the one thing D70 exists to prevent and
   explicitly says cannot be retrofitted — or puts a second copy of the editor behind a second
   dynamic import, which is a maintenance liability for a screen that already exists in the panel.
2. Editing a rule needs the timeline beside it. Screen 02's right column is not decoration; it is
   how you tell the rule does what you meant. A Lovelace column is 300-odd pixels wide and cannot
   hold it.

**Proposal against D61:** keep both artefacts, and define the card's editing affordance as
*opening the panel's editor in a dialog via dynamic import* rather than reimplementing it. The
card stays small, the editor stays singular, and the user still edits without leaving their
dashboard. Screen 03's expanded state shows the boundary: everything above the fold is the card's,
the **Edit** button is a door.

### Q6 — Can D46's three axes be rendered without a settings page?

**Yes.** See finding 4 and screen 12: four presets, a sentence with inline selects, and axis 3
shown only when axis 1 is a count, with each option displaying its own live counter.

**Which axis collapses into which:** axis 3 collapses into axis 1 — not by deletion but by
*dependency*. It is hidden and defaulted whenever axis 1 is not a count.

**Expressiveness lost: none.** Every combination reachable in the model is reachable in the UI.
What is lost is the *appearance* of orthogonality, which was never real. A user who wants the raw
three-axis form has the YAML view.

### Q7 — How do you get a condition label written, and what is the default?

**You don't ask.** See finding 5. Derive the label from the condition structure, pre-fill it, and
let the user overwrite. The field is never empty and never optional-looking.

**The default when the user does not touch it** is the derived label — *"Dan is home"* from
`person.dan == home`, *"cleaning crew present"* only if a human wrote it. The derived form is
always *entity-friendly-name + operator + state*, which is mechanical and adequate; the user
overwrites precisely when the mechanical form is not what they mean, and that is the
5% of cases worth typing for.

Store `label_source: derived | user` so a later structural edit can refresh a derived label and
must never overwrite a user-written one. The collapsed condition row shows the label as its
title, which is what makes an unedited label visibly wrong and therefore likely to be corrected.

### Q8 — Where does mobile break, reduced view or different view?

**Almost everything is a reduced view. The timeline is a different view.** That is the answer.

Screens 02 and 03 degrade by reflow: columns stack, the right-hand preview moves below, controls
grow to full width. Nothing is removed; the phone captures at 390×844 carry the same information.

The **timeline cannot** be reduced. A horizontal 36-hour axis at 390px gives roughly 10px per
hour, which is below the width of the shortest label it must carry. Screen 01's phone rendering
therefore:

- **drops the ribbon entirely** — not shrinks it,
- **flips the axis to vertical**, time running downward,
- becomes **agenda-first**, grouped by day, with the *now* divider as a row rather than a line,
- keeps anchors as inline entries in the stream rather than on their own rails.

Screen 10 needed the same treatment and gets its own `.only-narrow` card, because a first-run
timeline with no rules on it is still the product's argument and still has to be made on a phone.

Two smaller breakages found by capturing rather than by reasoning: the day-set impact preview's
month grids (screen 06) do not fit side by side and must stack, becoming a scroll rather than a
comparison; and the execution log's evaluation trace (screen 07) needs its explanation text to
wrap under the marker rather than beside it.

### Q9 — What is the first-run experience?

**There is no empty state, and that is the entire product argument.**

The sun is a resolver. `jewish_calendar` is a resolver. Both are typically already installed, and
both already know their times. So on first run — zero schedules, zero day sets — **the timeline is
populated**. Screen 10 shows exactly screen 01's ruler and both anchor rails, with a dashed ghost
lane where the user's schedules will go and a footer reading *"almanac found 2 sources of times
already installed."*

A blank canvas saying *"create your first schedule"* would throw away the one thing that
distinguishes this from every other scheduler, at the only moment the user is deciding whether to
keep it.

**The first thing they do** is one of three starters, each of which opens the editor pre-filled:
*Between two times* · *Around sunset* · *Through Shabbat*. The third is the flagship, one click
from install — which is also why finding 1 must be fixed before shipping, since it is the starter
most likely to be clicked in the target deployment.

There is also a migration affordance — *"almanac can read what the old scheduler has and show you
what it would become, without changing anything"* — which is deliberately a **preview**, not an
import button. Screen 10 renders it as a nudge, not a wizard.

---

## Naming

The user-facing words, with reasoning. The left column is the `DESIGN.md` term; the right is what
appears on screen. Where they differ, the schema keeps its key.

| In the model | On screen | Why |
| --- | --- | --- |
| `At` | **"At"** — *"At `[a time]`, do this."* | Survives contact. Reads as a moment, which is what it is. |
| `During` | **"From … until …"** — *"From `[a time]` until `[a time]`, keep things like this."* | "During" needs an object ("during what?"). "From/until" carries two anchors, which is the shape. The verb *keep* (vs. *do*) is what makes D27's action binding legible. |
| `latch` | **not a field.** A question under the conditions block: *"If the conditions stop being true, keep going anyway until the end time."* | See finding 3. "Latch" is an electrical term. Phrasing it as the consequence removes the need to explain the word, and siting it under conditions makes its dependency structural rather than documented. |
| `on_exit` | **"When it ends"** — *Put everything back how it was* / *Set it to…* / *Leave it as is* | Three plain options. Note that per finding 2 the first one is currently a lie in the overlap case; the wording is only honest once the ownership question is settled. |
| `condition_policy` | **"If conditions aren't met"** — *Skip today* / *Wait until `[time]`* | Names the situation, not the mechanism. The deadline is inline in the second option, so `wait_until` never needs its own field. |
| `day set` | **"Day set"** in the library; **"Days"** in a schedule | Two names for one object, deliberately. Inside a rule the user is answering *which days?* and "day set" is a noun they have not met yet. In the library they are managing a shared object and need the noun. |
| `resolver` | **never shown.** Sources appear as *A time of day* · *Sun* · *Jewish calendar* · *A calendar* · *An entity* | The user is choosing where a time comes from. "Resolver" is an implementation role and would be the only word on screen 04 requiring explanation. Constrains the extension API: a third-party resolver's `domain` becomes a heading in this list. |
| `anchor` | **"Starts" / "Ends" / "At"** | The field label is the role, not the type. The word "anchor" never appears. |
| `offering` | **the thing's own name** — *Candle lighting*, *Havdalah*, *Sunset* | Offerings are listed, never labelled as a category. |
| `horizon` (D13) | **"Always" / "Until `[date]`" / "The next one only"** | Three phrases, one per class, in the picker's legend. The concept is hard; the words do not need to be. |
| occurrence *outside the set* (D12) | **a ghost that names the boundary** — *"excluded — Shabbat begins 18:32, this is 17:47"* | See Q4. The label alone reads as a bug. |

---

## Components used

Verified present in `home-assistant/frontend` at sha `d8effd44b2bced4479112fef0ce5a60291d9cbd3`
by searching `src/` for the custom-element registration, not by memory. 683 `ha-*` elements exist
in total; these are the ones an almanac UI would reach for.

**Present:** `ha-card` · `ha-dialog` · `ha-select` · `ha-list-item` · `ha-md-list` ·
`ha-md-list-item` · `ha-icon-button` · `ha-button` · `ha-switch` · `ha-checkbox` · `ha-alert` ·
`ha-expansion-panel` · `ha-textarea` · `ha-time-input` · `ha-duration-input` · `ha-date-input` ·
`ha-svg-icon` · `ha-icon` · `ha-badge` · `ha-tooltip` · `ha-yaml-editor` · `ha-code-editor` ·
`ha-control-select` · `ha-picker-field` · `ha-service-picker` · `ha-service-control` ·
`ha-tab-group` · `ha-tab-group-tab` · `ha-area-picker` · `ha-sortable` · `ha-label` ·
`ha-selector` · `ha-form` · `ha-entity-picker` · `ha-assist-chip` · `ha-filter-chip` · `ha-input` ·
`ha-input-search`

**Verified ABSENT** — do not write these: `ha-textfield` · `ha-fab` · `ha-md-dialog` · a generic
`ha-chip` · `ha-tabs` · `sl-tab-group` · `ha-attribute-picker`

The absences are not random. HA is mid-migration to `@home-assistant/webawesome`, which is why
`ha-textfield` — the most obvious element in the list — is gone and `ha-input` is there instead.
See finding 10. Prefer `ha-form` + `ha-selector` where a form can be described declaratively;
that pair is the layer HA treats as its integration-facing API and it is the least likely to move.

The prototype does not use any of these. It reimplements their *appearance* in plain CSS, because
the brief forbids a build step and these are Lit components. Every reimplementation is named after
the component it stands in for (`.tf` for a text field, `.sel` for `ha-select`, `.dlg` for
`ha-dialog`) so the mapping to real code is mechanical. **No `ha-*` component was invented**: if
it is drawn here, it exists at the sha above.

---

## Icons

The prototype ships its own 37-glyph inline SVG sprite
([`prototype/assets/boot.js`](prototype/assets/boot.js)), not MDI.

This is a prototype constraint, not a recommendation. MDI is ~7,500 icons delivered as a font or
a large JS module, and the brief forbids a build step and an npm install; embedding the subset by
hand would have meant transcribing path data, which is exactly the class of verbatim third-party
content `CLAUDE.md`'s source-integrity rule says not to trust. The sprite is drawn from scratch at
a 24px grid with a 2px stroke to match MDI's optical weight.

**Real almanac code should use `ha-svg-icon` with `mdi/*` paths**, as every core panel does. The
mapping is one-to-one and unsurprising — `clock` → `mdiClockOutline`, `flame` → `mdiFire`,
`ghost` → `mdiGhostOutline`, and so on. Nothing in the layout depends on the sprite's specific
artwork; only on there being a 24px icon in that slot.

One icon has no obvious MDI equivalent and is worth flagging: the **ghost occurrence** marker.
`mdiGhostOutline` is a literal ghost and reads as whimsical for what is an exclusion. A dashed
outline of the ordinary occurrence shape, as used here, is more honest, and may need to be a
custom path rather than an MDI glyph.

---

## How the screenshots were captured

21 PNGs in [`screenshots/`](screenshots/), named `<screen>-<theme>-<viewport>.png`. Tier 1 in all
four combinations (light/dark × desktop 1280×800 / mobile 390×844); tiers 2 and 3 desktop light,
as the brief allows.

Captured with **headless Chrome** at `--force-device-scale-factor=2`. Neither Playwright nor
Puppeteer was available in this environment, and nothing was faked — every PNG is a render of the
HTML in `prototype/` as committed.

**One thing about the phone captures is worth knowing, because it looked fine and was wrong.**
macOS Chrome refuses a window narrower than roughly 500 CSS pixels. Asking for a 390px window
therefore does not give you a 390px layout — it gives you a 500px layout written into a 390px
image, which is a **crop**, not a rendering. Media queries fire at the wrong breakpoints and
everything looks slightly better than it is.

The fix was a harness: a 1280px page containing a 390×844 `<iframe>` holding the screen, captured
and cropped to the iframe. The media queries then see 390px because the iframe genuinely is 390px.
**The phone PNGs in this directory are true 390×844 layouts.** Anyone regenerating them without
the harness will get subtly different, subtly wrong images and will not notice.

The capture scripts live in the session scratchpad rather than in `ux/`, since the brief scopes
the deliverable to prototype, screenshots and this document.

---

## Summary of proposals

Every change this document asks for, as a list, against its decision number. Nothing here has
been applied.

| # | Against | Proposal | Weight |
| --- | --- | --- | --- |
| 1 | **D11** | `recurrence: anchor` — test day-set membership against the anchor's own date, not the shifted instant; and/or publish `shabbat` and `shabbat_days` as separate offerings. Resolve and membership-test at edit time. | **Blocking.** The flagship does not work without it. |
| 2 | **D3** | Per-entity ownership stack, so `restore` pops to the value below rather than re-applying another rule's. | High — produces a wrong temperature on a real weekly schedule. |
| 3 | D4 | `latch` is not a rule field; it is a question inside the conditions block, hidden when there are no conditions. | Low, cheap. |
| 4 | D46 | Presets + a sentence; axis 3 hidden unless axis 1 is a count; each axis-3 option shows its own counter. | Medium. |
| 5 | D24 | Derive condition labels, pre-fill, allow overwrite; store `label_source`. | Medium — also fixes D49's payload. |
| 6 | D49 | `dropped_because: { script, held_by }`. | Low, cheap. |
| 7 | D48/D49 | Affordance in the log for rules that generated no occurrences; optionally a zero-occurrence event. | Medium — it is why finding 1 would have shipped. |
| 8 | D28 | Move the raw-service-call override from the editor to the execution log. | Low. |
| 9 | D22 | Day-set edits emit an event per affected schedule, into each owner's logbook. | Low, cheap. |
| 10 | D68 | Pin the verified `ha-*` inventory with its sha; prefer `ha-form`/`ha-selector`. | Low, ongoing. |
| 11 | D62 | Record `custom_ui_more_info` as the mechanism, with its sha; note that it makes D70 load-bearing for two features and requires the attribute on every almanac entity. | Medium — it is the only route, so there is no fallback to fall back to. |
| — | D61 | The card does not carry the rule editor; it opens the panel's editor via dynamic import. | Medium — collides with D70 if ignored. |
| — | **new** | Decide how a dry run evaluates conditions, and thread the strategy like `now` (D64). | Medium — constrains engine signatures, so it cannot be retrofitted. |
