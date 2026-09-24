# UX brief — almanac

**You are designing the UX for a Home Assistant scheduler that has a settled data model and no
code.** Your job is to find out whether that model can be made to feel obvious, and to produce
screenshots good enough that someone can look at them and know what the product *is*.

This is a validation exercise, not a decoration exercise. A brief that comes back saying
"everything works, here are some pretty screens" has failed. The model is 71 numbered decisions
written by someone who had not yet tried to draw them; some of them are going to be wrong, and
finding out which is the whole point.

---

## 0. Before anything else

Read, in this order:

1. `CLAUDE.md` — orientation, working rules, and the verified-facts blocks
2. `PRODUCT_BRIEF.md` — what the product is for, and the five framing questions
3. `DESIGN.md` — all of it. §3–§8 and §11–§13 are the model you are rendering; §16–§17 are the
   constraints you are rendering it *within*

Decisions are cited as **D<n>** throughout this brief and throughout `DESIGN.md`. Use those
numbers when you report back — "D46's three axes don't fit on a screen" is actionable, "the
completion settings are confusing" is not.

**Do not edit `DESIGN.md`, `PRODUCT_BRIEF.md`, `CLAUDE.md` or `README.md`.** This project records
decisions as decisions, with reasoning, and that is the lead's job. Everything you want changed
goes in your findings document as a proposal against a decision number.

---

## 1. What the product is, in one paragraph

Home Assistant's scheduling story splits badly. Native automations are fully general and
therefore opaque — you cannot ask them what will happen on Friday. The existing
`scheduler-component` + `scheduler-card` stack is legible but hemmed in. almanac's bet is that a
rule model can be **enumerable**: every rule must be able to say what it will do across a future
window, enforced by a type signature rather than a policy. The payoff, and the product's single
claim, is **the timeline** — "what will happen between now and Friday night" as a view you can
render.

If your screenshots do not make that claim land, nothing else about them matters.

## 2. Who uses it

**The first user runs a synagogue.** Schedules are anchored to zmanim — candle lighting,
havdalah — and when one is wrong, a room full of people sees it. They are technical enough to
have set up Home Assistant, not technical enough to enjoy YAML, and the thing they actually
need is *confidence before the fact*: to look at a screen on Thursday and know Friday is right.

**The second user is an ordinary HA household.** Lights, heating, a coffee machine. They want
what the current scheduler gives them — drop a card on a dashboard, drag a time, done — without
the current scheduler's walls.

Design for the first and do not regress the second. The current card's directness is the thing
people like about the incumbent; losing it to gain expressiveness would be a bad trade.

---

## 3. The model you must render faithfully

This is a reference sheet, not a substitute for reading `DESIGN.md`.

### Rules come in exactly two shapes (D2)

```
At:      { anchor, actions[], condition_policy }

During:  { start_anchor, end, state, on_exit, latch, enter_actions[], exit_actions[] }
         end = { duration } | { anchor }
```

A moment, or an interval. The 24-hour bar people know from the current card is a *rendering*,
not the model. Intervals are idempotent and reconcilable; moments are not — that property is
what makes restart recovery and desired-state actions well-defined (D2).

- `on_exit` is three-valued: `apply(state)` | `restore` | `leave` (D3)
- `latch` decides whether leaving the *condition* predicate ends the interval, or only leaving
  the *time window* does (D4)
- `enter_actions` / `exit_actions` fire at the edges, separately from the state the body
  reconciles (D5)

### Anchors are a tagged union of three kinds (D6)

```yaml
{ kind: clock,       at: "17:00" }
{ kind: entity_time, entity_id: sensor.x, offset: -45m }
{ kind: resolver,    domain: sun,   key: sunset,          offset: -20m }
{ kind: resolver,    domain: hdate, key: candle_lighting, offset: -45m, edge: start }
```

Offsets are signed durations with **no magnitude clamp** — a warning past 24h, nothing rejected
(D7). Resolver offerings are addressed by stable machine key, never by display text (D8).

### Conditions are structured only — no templates, ever (D23)

The escape hatch is a template `binary_sensor` helper the user defines once in HA's own UI and
references here by entity. Every condition carries an optional **label**, and the UI pushes for
one (D24), because the execution log's whole value is answering *why didn't it fire*: "Skipped
by condition 2" is noise, "Skipped: *cleaning crew present*" is the feature.

Evaluation differs by shape: for `During` rules conditions are part of the activation predicate
and always live (D25); for `At` rules the policy is explicit per rule — `skip` or
`wait_until(deadline)` (D26).

### Day sets are first-class, shared, named objects (D18–D22)

Sourced from weekdays, dates, nth-weekday, every-N, a resolver offering, or **one level** of
union / intersect / minus — no nesting (D19). Each publishes a `binary_sensor` whose state is
*does the set cover now* (D21). Editing one changes every schedule referencing it, which is the
point and also the danger, so **editing requires an impact preview** showing reference count and
the resulting timeline delta before saving (D22).

Day sets evaluate in two stages (D11): `candidate_dates` generates the dates, then
`covers(instant)` drops the parts that fall outside. Shabbat generates Friday *and* Saturday;
Saturday 22:00 is then dropped because it falls after havdalah. Occurrences dropped by stage two
are rendered as **"outside the set"**, not omitted (D12).

### Actions are bound to position (D27)

| Position | Actions |
| --- | --- |
| `At` rule | service calls and scripts — heterogeneous, multiple per rule |
| `During` body, `state` | desired state — entity → attributes, reconciled |
| `During` `enter_actions` / `exit_actions` | service calls and scripts |

### Completion decomposes into three independent axes (D46)

| Axis | Values |
| --- | --- |
| Finished when | one rule fired · all rules fired once · N occurrences · a date · a condition became true |
| Then | keep running · disable self · delete self · run an action |
| Counter increments on | every scheduled occurrence · only those whose conditions passed · only those whose actions succeeded |

Termination never takes effect mid-interval (D47).

### Observability (D48–D52)

One rich event per occurrence: result (fired / skipped / dropped / failed), **the label of the
blocking condition**, and per-action results. A script already running in `mode: single` does not
raise — it silently does nothing — so the engine pre-flights and logs *dropped: script already
running* (D31). That failure is invisible in HA today.

---

## 4. Hard constraints

These are settled. Design inside them; if one of them is what breaks your design, say so
explicitly rather than quietly working around it.

- **A card and a panel, one frontend bundle** (D61). Card: per-schedule and per-area view, quick
  enable/disable, rule editing. Panel: timeline, day sets, audit log.
- **The schedule editor is reachable from an entity's more-info dialog** (D62) — this is what
  closes the discoverability gap.
- **The timeline shows past and future in one view, with *now* as the divider** (D63). Predicted
  to the right, actual (from the recorder) to the left.
- **The card entry point must be cheap** (D70). `add_extra_js_url` injects the card bundle into
  every frontend page, so the entry holds only a placeholder element registration; the editor and
  timeline load on first use. If your card design needs the full editor inline on load, that is a
  real collision and it is worth knowing now rather than in month three.
- **TypeScript + Lit** (D68). You are not writing production code, but do not design anything that
  assumes a framework HA does not have.
- **UI/YAML parity** (§9.1, D33) — every schedule has a code view, and anything the UI can build
  must be expressible in the stored form and vice versa. No UI-only concepts.
- **No templates in the UI** (D23). If your design wants a free-text expression field anywhere,
  that is a finding, not a feature.

---

## 5. Scenarios to render

Use these. Real names, real values — placeholder lorem makes a mockup impossible to judge.

**A — Shabbat Lights** *(the flagship; if this is not effortless, the product has failed)*
A `During` rule: start at `hdate:candle_lighting` **−45m**, end at `hdate:havdalah` **+30m**.
State: `light.sanctuary` on at 80%, `climate.sanctuary` heat to 22°C. `on_exit: restore`,
`latch: true`. `enter_actions`: `script.pa_announce` with `message: "Shabbat begins"`.
`exit_actions`: the same script with a different message. Day set: **Shabbat**
(evening-to-evening).

**B — Weekday morning warmup** *(the mundane case that must stay easy)*
An `At` rule at **06:30**, day set **Weekdays**, condition `person.dan is home` labelled
*"someone's home"*, `condition_policy: wait_until(07:30)`. Action: set the thermostat.

**C — Cleaning days** *(shared object, large blast radius)*
Day set: `NthWeekday(2nd and 4th Tuesday)` **minus** `Dates(holiday list)`. Referenced by 6
schedules owned by 3 different people. Render the **impact preview** (D22) for an edit that
changes it.

**D — Two rules fighting** *(deliberately not settled in DESIGN.md)*
Two `During` rules both targeting `climate.sanctuary` with overlapping windows and different
setpoints. Decide how the timeline shows this and whether the editor warns. Whatever you choose,
flag it — this is a real gap and your answer may become a decision.

**E — The Friday it didn't fire** *(what earns the audit log)*
One occurrence skipped with the condition label *"cleaning crew present"*; one action dropped
because `script.pa_announce` was already running (D31); one action failed. Render the screen a
user lands on when they ask *why didn't it fire*.

**F — Scale** *(where pretty designs die)*
40 schedules, 12 day sets, ~120 rules. Render the list and the timeline at this size.

---

## 6. Screens

**Tier 1 — required, fully rendered, in both light and dark theme, at both desktop and mobile
width.**

1. **Panel → Timeline.** The hero screen. Friday 14:00 → Sunday 02:00, *now* as the divider,
   actual to the left and predicted to the right, at least one occurrence marked *"outside the
   set"* (D12) and one skipped with its condition label.
2. **Rule editor — the `During` rule from Scenario A.** This is where the model is most likely to
   collapse into a form only its author could love. Two resolver anchors with offsets, a desired
   state, two action hooks, `on_exit`, `latch`.
3. **Dashboard card**, compact and expanded, showing Scenario A.

**Tier 2 — required, desktop light theme is sufficient.**

4. **Anchor picker.** The "45 minutes before candle lighting" moment. Three kinds in storage (D6)
   — decide whether the user should see three kinds or one picker with three modes.
5. **Schedule list at scale** (Scenario F).
6. **Day set editor with impact preview** (Scenario C).
7. **Execution log / why-didn't-it-fire** (Scenario E).
8. **Dry run** — the engine evaluated at a hypothetical *now* (D64 makes this the same code path
   as the live engine, so it is a real feature, not a mock).

**Tier 3 — sketch or describe in prose if you run short.**

9. More-info dialog entry point (D62)
10. First-run empty state
11. Conflict / error state (Scenario D)
12. Completion settings (D46's three axes) — known-hard, and worth an honest attempt

---

## 7. Fidelity and technique

**Build static HTML + CSS prototypes and screenshot them.** No build step, no framework, no npm
install. One self-contained file per screen is fine; a `ux/prototype/index.html` that links them
all so the whole thing can be browsed is better, because the point is to *feel* the product.

**Get Home Assistant's visual language right, from source, not from memory.** Clone
`home-assistant/frontend` and take the actual theme tokens — `--primary-color`,
`--card-background-color`, `--primary-text-color`, `--secondary-text-color`, `--divider-color`,
the dark-theme overrides, card elevation, border radius, the sidebar width, the type scale. Cite
the file path and pin the commit sha next to any value you copy. Mockups that are approximately
HA-coloured read as fake immediately, and this project's standing rule is that upstream behaviour
is verified against source rather than asserted from memory — that applies to pixels too.

> **Source-integrity caveat** (`CLAUDE.md`, and `DESIGN.md` Appendix B): every read of file
> content in this environment has `voluptuous` rewritten to `probatio`. Structural facts — token
> *names*, file paths, presence or absence — are unaffected. The observed rewrite is of a package
> identifier, so CSS values are very unlikely to be touched, but pin the sha so a reader can
> check.

Do not invent `ha-*` components. If you use one, confirm it exists.

**Viewports:** desktop **1280×800** (HA's sidebar is a fixed width — take the real number), mobile
**390×844**. HA is used on phones constantly; a design that only works at desktop width is not a
design.

**Capture:** Playwright or Puppeteer if either is available. Otherwise headless Chrome
(`--headless --screenshot --window-size=`) works fine. If neither is available, deliver the HTML
and say plainly that no PNGs were captured — do not fake them.

---

## 8. What to deliver

```
ux/
  prototype/         the HTML/CSS, browsable from index.html
  screenshots/       PNGs, named <screen>-<theme>-<viewport>.png
  FINDINGS.md        the actual deliverable
```

`FINDINGS.md` carries:

- **What works.** Which parts of the model draw cleanly and why.
- **What doesn't.** Each one citing the decision it pressures, what specifically breaks, and a
  proposed alternative. Be concrete: *"D6's three anchor kinds force three different-shaped forms;
  a single picker with a mode switch collapses them, at the cost of X"*.
- **What you had to invent** because the design is silent. Scenario D is one of these; there will
  be others. These are the highest-value output in the document, because they are gaps nobody has
  noticed yet.
- **Answers to §9.**
- **Naming.** You will need user-facing words for `At`, `During`, `latch`, `on_exit`,
  `condition_policy`, `day set`, `resolver`. Propose them. Internal names are settled and are not
  changing; the user-facing vocabulary is entirely open and is one of the most valuable things
  you can hand back.

---

## 9. Questions to answer explicitly

1. Can a synagogue gabbai build Scenario A without reading documentation? Where exactly does it
   break?
2. Does the `At` / `During` distinction need to be visible to the user at all, or can the UI infer
   the shape from what they do? If it must be visible, what do you call the two things?
3. The timeline is the product's one claim. At 40 schedules, what is the unit — one row per
   schedule, one merged stream, swimlanes per entity, something else? What gets dropped first as
   density rises?
4. Does "outside the set" (D12) read as an explanation or as a bug? This is the single most
   unusual thing in the model and the first user hits it every single week.
5. Does the card earn its existence next to the panel, or should it be a link to the panel with a
   state summary? D61 says both exist; say if you disagree and why.
6. D46's three completion axes — can they be rendered without a settings page nobody understands?
   If not, which axis collapses into which, and what expressiveness is lost?
7. How do you get a user to write a condition label (D24) when the field is optional? What is the
   default when they don't?
8. Where does the mobile experience break down, and is the answer "a reduced view" or "a different
   view"?
9. What is the first-run experience? A user installs this with zero schedules and zero day sets —
   what is on screen, and what is the first thing they do?

---

## 10. How to work

- Verify against source; cite paths and pin shas. This project exists because checking changed the
  answer three times.
- Distinguish *"not supported"* from *"rejected"* — some gaps upstream are unimplemented, at least
  one was explicitly refused. The same discipline applies to your own findings: say whether
  something is impossible or merely unbuilt.
- No Python. You are not implementing the integration.
- Do not touch anything outside `ux/`.
- Shell rules from the global `CLAUDE.md` apply and are enforced by a hook: **one command per
  call, no `&&`, `||` or `;`, no `git -C`**. `cd` does not persist between calls — use absolute
  paths.
- If the brief and `DESIGN.md` disagree, `DESIGN.md` wins and the disagreement is itself a finding.
