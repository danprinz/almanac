# almanac — home-assistant-scheduler-v2

**Integration domain: `almanac`** (D53). The repo directory keeps its old name; the domain is
the permanent identifier.

A replacement for the Home Assistant scheduler stack (`nielsfaber/scheduler-component` +
`scheduler-card`): a schedule engine and UI whose rule model is **enumerable**, so that
"what will happen between now and Friday night" is a view you can actually render.

## Status — 2026-10-01

**Build steps 1–9a of `DESIGN.md` §15 are written, tested and pushed.** Schema and storage, the
resolver contract with `clock` / `entity_time` / `sun`, the rule engine, conditions and day sets,
actions and completion and the tick that drives them, observability, the `hdate` resolver, and —
at step 8 — the timeline query and the dry run, with the two websocket commands the panel is
built on, at step 8a the anchor-span day set the flagship scenario turned out to need, and at
step 9a the frontend's delivery path: the Rollup toolchain, the Python registration, D70's
always-loaded stub, and thin-but-honest first drafts of the panel and the card. 580 tests pass,
among them `tests/test_flagship.py` and `tests/test_anchor_span_day_sets.py`, which enumerate
the brief's flagship scenario end to end twice over — once from `hdate`'s prebuilt Shabbat and
once from a span the user wrote. Remote is `git@github.com:danprinz/almanac.git`.

Decisions now run D1–D131. Each build step closes the gaps it found in its own subsection
(`DESIGN.md` §5.6, §5.7, §6.2, §7.4, §10.6a, §11.1, §12.1, §12.2, §17.1) rather than editing
the decision it refines.

**Step 8 fixed the engine, which is the part worth knowing.** `Plan` was reporting one fact where
D44 says there are two: `_Horizons` was built over the window *after* the ninety-day clamp, so
past the budget `known_through` collapsed onto `computed_through` and `fully_known` went false
however unbounded every source had declared itself. `Plan.solid_through` is now where the two are
recombined, deliberately, and they arrive uncombined. See §12.2.

**Step 7 added the project's first third-party runtime dependency:** `hdate[astral]==1.2.1`, in
`manifest.json` and repeated in `requirements_test.txt` because the harness does not install what
the manifest declares. The pin matches core's `jewish_calendar/manifest.json` exactly and must
keep matching it — two integrations in one instance share one `site-packages`.

| File | Contains |
| --- | --- |
| `CLAUDE.md` | this — orientation and working rules |
| `PRODUCT_BRIEF.md` | requirements (keep / fix / add), non-goals; Q1–Q5, now answered |
| `DESIGN.md` | the decisions, with reasoning; build order; verified-facts appendix |
| `README.md` | public-facing — what it is and why, pointers to the above |
| `LICENSE` | MIT |
| `hacs.json` | distribution — `zip_release`, `hide_default_branch` (D71) |
| `UX_BRIEF.md` | the task handed to the UX thread; outputs land in `ux/` |

**Decision numbers are identifiers, not an ordering.** D1–D63 were assigned in reading order at
the first commit; anything added since takes the next free number and lives in the section it
belongs to. Do **not** renumber to tidy the sequence — see the convention note at the top of
`DESIGN.md`.

**The flagship scenario did not run, and fixing it changed the rule model — read §5.8 and
§6.1 before touching the engine or the day-set schema.** The brief's Scenario A (*45 minutes
before candle lighting, 30 after havdalah, on Shabbat*) was producing nothing on any Friday,
because D11's stage two tested the *offset* instant and a setup window puts that before the day
begins. The cliff was one second wide, so no shorter window helped. **D122** is the ruling: the
day set is asked about the anchor's own event and the offset is arithmetic applied afterwards, so
`async_resolve_anchor_on_date` now returns a `ResolvedAnchor` carrying both instants. §5.4's
motivating example is untouched, because a clock anchor at 22:00 has no offset and its two
instants coincide — `test_the_motivating_example_still_holds` is there so that cannot silently
stop being true.

**The same ruling set out the model day sets are supposed to follow, which is §6.1.** Three
layers: basic (clock times and weekdays), **sets** (any span the user builds from two anchors),
and predefined (Shabbat, holidays, national days, shipped). Layer 3 is a convenience over layer
2, not a separate mechanism. **D124** was layer 2's missing piece — a day set sourced from a
span between two anchors, a seventh `source.kind` — and step 8a built it. *"The span from candle
lighting to havdalah"* is now sayable, and `tests/test_anchor_span_day_sets.py` runs the flagship
scenario over a set written that way to prove layer 3 is a convenience over layer 2 and not a
second mechanism.

**What step 8a found is §6.2, and three of its four decisions have one cause:** before D124 no
day set had an anchor, so nothing that exists because anchors are uncertain had ever been pointed
at one. D125 bounds the end-edge search at eight days (*owner's judgement call* — unbounded, and
"stop at the next start edge", are both recorded as rejected, the second because it breaks the
festival weekend D122 had just fixed). D126 says two spans in one set overlap freely, because
that is a union and not D39's collision. D127 threads a day set's own anchors into D44's
`known_through`, under the empty rule id `index.Reference` already uses for a day-set recurrence.
D128 adds `Usage.DAY_SET_ANCHOR`, because a span edge is the first entity reference whose
referrer is not a schedule. And one non-gap: **D122 does not apply inside a set** — a set's edge
*is* its offset, which is the opposite question from the one D122 answers.

**What step 9a built, and what it found, is §17.1.** The toolchain is at the repo root
(`package.json`, `tsconfig.json`, `rollup.config.mjs`); the TypeScript is in
`custom_components/almanac/frontend/src/`; `custom_components/almanac/frontend_setup.py` is the
Python side, named that way because `frontend/` beside it is the bundle directory. Three
decisions came out of writing it, and all three are consequences of D70 rather than of D69's
table: **D129** one static path for the whole `dist/` directory, because D70's dynamic import
makes Rollup emit a chunk whose name no Python constant can know; **D130** the panel and the card
URL are undone on unload, because none of the three registrations is idempotent and a config
entry reload is ordinary; **D131** a missing bundle logs and skips rather than failing setup.

**Two tests exist because nothing else would notice the thing they check.**
`tests/test_wire_contract.py` sweeps every `as_dict()` by AST and compares the key sets against
`frontend/src/wire.ts`'s interfaces — the websocket boundary is the one place in the project
where a rename is silent in both directions. `tests/test_frontend_assets.py` does the same for
the delivery path: the bundle filenames against `rollup.config.mjs`, the element names against
the TypeScript, and D70's rule that `card.ts` has no run-time import. Both read source rather
than running it, for the same reason `tests/test_design_constraints.py` does for D64.

**Next step:** step 9b — the anchor-relative track (D72–D74, D76, D77), which replaces each
lane's list of chips in `panel.ts`. Then 9c the editor (D72, D78, D80) plus the card's
`getConfigElement` and D79's generated summary, which needs the stored rule bodies and therefore
the schedule CRUD commands; then 9d D62's more-info entry point. The other 17 UX findings in
`ux/FINDINGS.md` are all still unapplied, and `ux/prototype/15-new-shabbat-2.html` still claims
the pre-flight check is a guarantee, which is what D80 says to fix. Then 10
(importer, last — D60). The `almanac` namespace check is **done** — clear in
core, in the HACS default list, and in a GitHub manifest-domain search (`DESIGN.md` "Remaining
checks").

**Still missing for a release:** the D71 workflow that builds and zips from *inside*
`custom_components/almanac/`. Nothing exists yet, and D131 means a broken one degrades quietly.

**Eight judgement calls are flagged for the owner** and are marked as such where they are
recorded: D91 (disarming mid-interval exits immediately), D97 (an unreadable condition changes
nothing), D104 (the re-enumeration period when D44's horizon comes back empty), D109 (`run_now`
fires no occurrence event) and D114 (`diaspora` and the two candle-lighting offsets have no user
surface — this one moves Simchat Torah by a day if the inference is wrong), plus step 8's two:
D119 (§5.5's third coverage state, *estimated*, has no producer and is not synthesised — the
alternative is a fourth `HorizonKind`, which is a contract change) and D120 (a dry run is one
evaluation at one instant, not a replay of the interval leading to it), plus step 9a's one:
D131 (a missing frontend bundle logs one line and the integration loads without a UI, which is
the opposite trade from §17's reason for not committing `dist/` — the two are consistent, but a
bad release is quiet).

D116 is the one decision step 8 made that changes existing behaviour rather than adding to it:
`run_now` now fires `almanac_execution`, so a manual run appears on the timeline's past half.
D109 still holds — no occurrence event — and §12.2 has why the two do not conflict.

**Two things a reader of the code needs before touching the engine.** A.13 — Python compares
*and subtracts* two aware datetimes sharing a `tzinfo` object by wall clock, so every instant
comparison goes through `absolute()` in `resolver/contract.py`, `key=absolute` included. And D64
below, which `tests/test_design_constraints.py` enforces by an AST sweep: the allow-list names the
five functions in `tick.py` that may sample the clock, and nothing else in the package may.

**Frontend toolchain: built** — §17 and §17.1, D67–D71 and D129–D131. One repository, HACS
category `integration`, TypeScript + Lit built by Rollup into
`custom_components/almanac/frontend/dist/`, panel via `panel_custom` and card via
`add_extra_js_url`, `dist/` gitignored and shipped only as a `zip_release` asset. The
single-installation requirement is met with **no** manual Resource step. `npm install` then
`npm run build`; `npx tsc --noEmit` is the typecheck. **Read D70 before touching `card.ts`** —
that file is fetched on every frontend page, so its only imports are `import type` and one
`await import("./card-body")`, and `tests/test_frontend_assets.py` fails if that stops being
true. The built proof: the card entry is 838 bytes against a 24 KB lazy body.

**Two Rollup configs, not one with two entries** — a single build would hoist shared code into a
chunk the card entry imports statically, which is what D70 forbids. The duplicated shared code is
the price, and it lands only on the two non-always-loaded paths.

**Standing constraint, decided:** D64 — nothing below the top-level scheduler tick reads a
clock. `now` is threaded as a parameter. This is what makes the dry run and the timeline the
same code path as the live engine, and it constrains every engine signature, so honour it from
the first function.

## Working rules

- **Verify upstream behaviour against source; never assert it from memory.** This project
  exists because checking changed the answer three times — the card *does* support script
  parameters, `track_conditions` *does* already exist, and SmartIR's codes file *is*
  atomic. Each correction moved the design. Cite a URL (ideally a raw source file or an
  issue) for any claim about how HA or the current scheduler behaves.
- **Content reads are unreliable whatever the transport — clones included.** *Corrected
  2026-09-24; the earlier rule here said clones were clean, and that was wrong.* Every read of
  file content in this environment has `voluptuous` rewritten to `probatio`, in a clone exactly
  as over HTTP. The stored bytes are genuine: `git fsck` passes, and the clone's commit sha is a
  real upstream commit, which cryptographically fixes the tree — so the rewrite happens when
  content is rendered into context, not in transit or on disk.
  - **Trust** git hashes, `git fsck`, commit shas and API *metadata* (commits, PRs, listings) —
    they are what caught this.
  - **Trust in practice** structural facts: names, signatures, parameter lists, line numbers,
    presence or absence of a symbol. A one-token rename cannot change them.
  - **Do not trust** a verbatim quote of a third-party identifier. Pin the commit sha beside any
    claim so a reader can check it.
  - The environment cannot self-certify — all channels share the rewriting layer. Only an
    out-of-band check settles true bytes. `DESIGN.md` Appendix B has the full evidence and the
    clone recipe.
- **Record decisions as decisions.** The brief holds requirements and open questions only.
  When something is decided, it goes in a design doc with the reasoning, not silently into
  the brief.
- **Distinguish "not supported" from "rejected".** Some gaps are unimplemented; at least
  one (entity time anchors) was explicitly refused upstream. That difference is what
  justifies building instead of contributing, so keep it visible.

## Established facts — verified 2026-09-22, do not re-research

Sources checked directly (raw source files, GitHub API, HA core `dev`).

> Both fact blocks below are **structural** — key names, signatures, presence or absence, counts
> — which is the class the read-layer rewrite cannot affect. Verbatim spellings of third-party
> identifiers are the exception; see the source-integrity rule above.

**Current scheduler**
- Storage schema is in
  [`const.py`](https://github.com/nielsfaber/scheduler-component/blob/main/custom_components/scheduler/const.py).
  Schedule = `weekdays`, `start_date`, `end_date`, `timeslots[]`, `repeat_type`, `name`,
  `tags`. Timeslot = `start`, `stop`, `conditions[]`, `condition_type` (and/or),
  `track_conditions`, `actions[]`.
- `validate_time()` whitelists **only** `sunrise`/`sunset` as anchors. Entity-based anchors
  were explicitly refused by the maintainer in
  [component#154](https://github.com/nielsfaber/scheduler-component/issues/154) — *"I don't
  consider this as a good addition... I don't want to go this route."* **Not upstreamable.**
- Card constraint, from the component README: *"Actions list may only consist of a single
  service/service_data combination (multiple actions may only have different entity_id)."*
  This is what forces the script-per-permutation workaround.
- Completion modes: UI **Stop** = storage `pause`; UI **Delete** = storage `single`. For a
  multi-slot scheme, "after it triggers" is undocumented.
- `track_conditions` exists and is surfaced as *"Re-evaluate when conditions change"*. Known
  flaw ([card#878](https://github.com/nielsfaber/scheduler-card/issues/878)): re-fires when
  any individual condition flips, not when the overall and/or result flips.
- No timeline/preview view exists in either repo, and no feature request for one was found.
- Health: component last release v3.3.8 (2024-11-16), last commit 2026-03-01, 3 open issues.
  Card v4.0.19 (2026-06-30), last commit 2026-09-16, 10 open issues. Same sole maintainer.
- [`snadboy/sb-scheduler`](https://github.com/snadboy/sb-scheduler) is a hard fork that
  deliberately preserves the storage format so the stock card keeps working — useful as a
  reference for migration shape. Very new, no adoption.

**HA core, relevant to the actions model**
- No atomic "set full state" service exists for `climate`. `climate.set_temperature` accepts
  an optional `hvac_mode`; nothing accepts `fan_mode` alongside anything else.
- `scene.apply` is not atomic: `climate/reproduce_state.py` issues up to **7 sequential
  blocking service calls** and does not skip attributes already matching current state.
- Core has no `climate` group. [PR #77737](https://github.com/home-assistant/core/pull/77737)
  sat ~2 years and died to the stale bot; [issue #111482](https://github.com/home-assistant/core/issues/111482)
  closed as not planned. Maintainer `iMicknl` on the PR: *"We are still looking into
  combining multiple commands in one execution, but this work hasn't been prioritized yet."*
- [`homeassistant.helpers.debounce.Debouncer`](https://github.com/home-assistant/core/blob/dev/homeassistant/helpers/debounce.py)
  exists and is available to integration authors, but nothing in core debounces *outbound*
  writes — only inbound refreshes via `DataUpdateCoordinator`.

## Established facts — verified 2026-09-23, do not re-research

Added while settling the design. Full statements, and what each one decided, are in `DESIGN.md`
Appendix A. The first two **correct** the 2026-09-22 block above.

- **Correction — the ±4h offset limit is card-only and arbitrary.** `MAX_OFFFSET_HOURS = 4`
  (sic, three F's) in the card's `scheduler-time-picker.ts`, changed 3 → 2 → 6 → 4 since 2020,
  never with a stated reason. The component has no magnitude check at all.
- **Correction — the anchor gap is wider than recorded.** `validate_time`'s whitelist excludes
  `dawn`, `dusk` and `noon` even though `sun.sun` publishes all three.
- `timer.py` clamps offsets to the day boundary instead of rolling over. This, not the UI, is
  why schemes cannot cross midnight.
- Core's `jewish_calendar` **does** ship a calendar platform (PR #145140, merged 2026-05-19).
  `candle_lighting` and `havdalah` are timed, zero-length events, and enumeration is pure
  `hdate` computation with no network. Event `summary` is translated, so match on the
  `const.py` enum keys, never on display text.
- Six `sensor.sun_next_*` entities are `device_class: timestamp` and enabled by default, but
  each holds only the *next* occurrence. `helpers/sun.py::get_astral_event_date` computes any
  date locally.
- The recorder subscribes `MATCH_ALL`. Custom events are persisted by default — no allow-list,
  no logbook-registration gate. The logbook's causation chain is generic: automations get
  "triggered by" only because they ship a `logbook.py`.
- `purge_keep_days` is global only, and long-term statistics hold numeric aggregates only, so
  they cannot serve as an audit trail.
- Websocket CRUD in `helpers/collection.py` touches only the storage collection. A paired
  `YamlCollection` has no update or delete path, so YAML-defined items are un-editable from
  the UI by construction.
- The search / related-items graph is **not** extensible: `ItemType` is a closed enum and five
  core components are imported by name. No registration hook exists.
- Core's all-day `CalendarEvent`s are built as `start=target_date, end=target_date` — a civil
  `date` with **zero extent**, so they fail any overlap test and lose the evening-to-evening
  boundary. Do not mirror this encoding.
- The entity registry records **when, never who**: `created_at` / `modified_at` (storage v1.15)
  exist, but there is no `created_by`, `modified_by` or `user_id` field anywhere. User
  attribution in HA exists only as `Context.user_id` reaching the recorder.
- `script.turn_on` starts a script non-blocking (`wait=False`); calling `script.<name>` as a
  service waits (`wait=True`, `SupportsResponse.OPTIONAL`) and passes `variables=service.data`,
  so script fields work. Both propagate `context=service.context` into the run.
- `DEFAULT_SCRIPT_MODE` is `single`, and an already-running `single` script **logs
  "Already running" and returns without raising** — a caller cannot tell it from a successful
  run. Script entities expose `mode` / `current` / `max` as attributes, which is how to
  pre-flight the call.

## Related but out of scope

A sibling project, `../smartir-atomicity/`, vendors SmartIR as a private `smartir_atomic`
integration that debounces transmission so one burst of climate service calls produces one
IR frame. Its README carries the reasoning. That work is **not part of this project** and
must not be absorbed into it — it demonstrates the principle that per-device atomicity
belongs in the device integration, which is why this project's actions model stops at
desired state.

Also relevant context: the owner's synagogue system (`hoshen-system`) publishes zmanim
sensors to HA. "45 minutes before candle lighting" is the motivating case for entity-based
time anchors, and those sensors already exist.
