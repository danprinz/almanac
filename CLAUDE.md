# almanac — home-assistant-scheduler-v2

**Integration domain: `almanac`** (D53). The repo directory keeps its old name; the domain is
the permanent identifier.

A replacement for the Home Assistant scheduler stack (`nielsfaber/scheduler-component` +
`scheduler-card`): a schedule engine and UI whose rule model is **enumerable**, so that
"what will happen between now and Friday night" is a view you can actually render.

## Status — 2026-10-06

**Build steps 1–9 of `DESIGN.md` §15 are written, tested and pushed — step 9 is finished.** Schema and storage, the
resolver contract with `clock` / `entity_time` / `sun`, the rule engine, conditions and day sets,
actions and completion and the tick that drives them, observability, the `hdate` resolver, and —
at step 8 — the timeline query and the dry run, with the two websocket commands the panel is
built on, at step 8a the anchor-span day set the flagship scenario turned out to need, and at
step 9a the frontend's delivery path: the Rollup toolchain, the Python registration, D70's
always-loaded stub, and thin-but-honest first drafts of the panel and the card, and at step 9b
D73's anchor-relative track, drawn in the panel's lanes and in the card's rows, and at step 9c
the boundary the editor sits on: `almanac/resolvers`, core's three write commands, and the
pure draft algebra that produces what they are sent — at step 9d the editor itself,
mounted in the panel, drawing an unsaved schedule through the same geometry as a saved one, and
at step 9e the four builders that make it a whole editor: actions, conditions, desired state and
the schedule's own completion — and at step 9f the links between the three surfaces: almanac's own
region inside core's more-info dialog, the footprint's names turned into things that open, and
`/almanac?edit=<id>` from a card row.
637 Python tests pass, plus 78 TypeScript ones (`npm test`); among them
`tests/test_flagship.py` and
`tests/test_anchor_span_day_sets.py`, which enumerate the brief's flagship scenario end to end
twice over — once from `hdate`'s prebuilt Shabbat and once from a span the user wrote. Remote
is `git@github.com:danprinz/almanac.git`.

Decisions now run D1–D165. Each build step closes the gaps it found in its own subsection
(`DESIGN.md` §5.6, §5.7, §6.2, §7.4, §10.6a, §11.1, §12.1, §12.2, §16.2, §16.3, §16.4, §16.5,
§16.6, §17.1, §17.2) rather than editing the decision it refines. **D149 is the one exception and it is marked
as such:** step 9e lifted it, so its entry says so in place rather than being deleted — the
argument under it is why the payload question had to be answered first, and it is what forbids
the schema-driven service form that looks attractive every time somebody reads `hass.services`.

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
| `scripts/package.sh` | D71's zip — run by CI and by `npm run package` (§17.2) |
| `.github/workflows/release.yml` | checks, build, zip, upload; also runnable by hand |

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

**What step 9b built, and what it found, is §16.2. The part worth knowing is where the track's
facts come from.** An occurrence carries no anchor — `Occurrence.as_dict()` emits the rule and
the instant — and D73's axis *is* the anchor, so the panel and the card read
`almanac/schedule/list` alongside `almanac/timeline` and join the two on `rule_id` (**D133**).
That is why `frontend/src/stored.ts` exists and why `tests/test_wire_contract.py` now covers
two boundaries. The arithmetic lives in `frontend/src/rails.ts`, which imports nothing at run
time (**D132**) — every import is `import type` — and that is the whole reason `node --test` can
run it with no bundler and no framework; `tests/test_frontend_assets.py` fails if a plain
`import` appears there. Four more: **D134** a rail's identity excludes the offset and includes
the resolver edge (D122 read as a layout rule); **D135** the track is added *above* the window
halves and does **not** replace the chips, because a track is one occurrence's anatomy and the
window is a list of many; **D136** a duration end draws on the start anchor's rail; **D137**
D76's order check samples only dates where every rail resolved, so an unresolved anchor is not
reported as a swap. And **D138** records that two of D74's four sizes are built, deliberately.

**Two tsconfigs, and they must stay two.** `tsconfig.json` is what Rollup reads;
`@rollup/plugin-typescript` sets `noEmit: false`, so `allowImportingTsExtensions` there fails
the build with TS5096 — measured. The flag and `types: ["node"]` live in `tsconfig.test.json`.
`npm run typecheck` checks the first, `npm run typecheck:test` the second, and a test in
`tests/test_frontend_assets.py` asserts the division because merging them is the obvious tidy.

**`npm run build` now cleans first.** Rollup does not clear `dist/`, D129 registers the whole
directory and D71 zips it, so a stale hashed chunk is a shipped chunk — and under D131 that
ships quietly.

**Two tests exist because nothing else would notice the thing they check.**
`tests/test_wire_contract.py` sweeps every `as_dict()` by AST and compares the key sets against
`frontend/src/wire.ts`'s interfaces, and since step 9b does the same for `schema.py` against
`frontend/src/stored.ts` — those two boundaries are the places in the project where a rename is
silent in both directions. `tests/test_frontend_assets.py` does the same for
the delivery path: the bundle filenames against `rollup.config.mjs`, the element names against
the TypeScript, and D70's rule that `card.ts` has no run-time import. Both read source rather
than running it, for the same reason `tests/test_design_constraints.py` does for D64. Step 9c
widened the second one: the no-run-time-import rule now covers `draft.ts` as well as
`rails.ts`, because both are executed by `node --test` and a plain import in either fails
nothing but the unit tests. Step 9e added `form.ts` under a stronger version of the same rule —
it has no import at all, type-only included — and that is checked separately, because the
non-empty assertion guarding against a dead regex would fail on it for the opposite reason.

**Step 9e's one idea is the thing to know about the editor, and it is a sentence long: a
payload is JSON text (D152).** `data`, `fields` and `attributes` are edited in a `<textarea>` and
nothing else, because `_service_data` stops at `vol.Schema(dict)` on purpose — the valid key set
belongs to the target service and almanac does not have it. A key/value widget would be an editor
for the keys it happened to know, which is D149's data-loss bug at a smaller scale and harder to
see because the widget would look finished. A text area has no key set, so the round trip through
it is the identity, and *that* is what made it safe to let D140's diff run over an action list the
editor has opened. The same argument rejects a form built from `hass.services`: D17 says a
schedule may name a service whose integration is unloaded, and such a form would render that
action as having no fields at all.

Two more worth knowing before touching the builders. **A builder owns its whole value and reports
it in one event tagged with a `name` the host assigned** (D157) — `track.ts`'s `STAGE_SELECTED`
arrangement, four more times, because a rule panel holds up to four action lists and Lit's `@name=`
cannot take a constant. And **`completion` is written whole, every time** (D158): each of its three
axes has a `vol.Optional` default and D140's update is a shallow merge, so a partial `completion`
silently re-defaults the axes left out.

**Step 9f is the one to read before touching any link between surfaces, and §16.6 has it.**
Core's more-info event is spelled in exactly one file, `frontend/src/moreinfo.ts`, and a test
asserts the literal appears nowhere else under `src/` (**D159**) — because it is the only
identifier in the repository that is wrong *silently*: `dispatchEvent` with a name nobody listens
for returns `true`, logs nothing, opens nothing. A.15 is the verification: five presence searches
over the installed `home-assistant-frontend` 20260826.7, which also established that the detail key
is `entityId`, that the listener is on an **ancestor** element, and therefore that `bubbles` and
`composed` are both required and neither is a default. **D160** put a second always-loaded stub in
`card.ts` — allowed despite D70 because `more-info-content` creates the tag with *no loader at
all*, so the definition must already exist before any dialog opens; the entry still builds to
1.4 KB. **D161** is the route into the editor: `/almanac?edit=<id>` by a plain anchor, not core's
navigation event, because an anchor's worst case is a full page load and a misspelled event's is
nothing happening.

**Next step: step 10 — the importer, last (D60).** The card's `getConfigElement` is still
deliberately absent rather than stubbed. The other 16 UX findings in `ux/FINDINGS.md` are still
unapplied — except half of #2, which 9e applied on screen, and #11, which 9f applied: the
footprint's entities, time reads and scripts now open. #2's larger half, per-entity ownership when
two schedules hold one entity, still has no D-number. `ux/prototype/15-new-shabbat-2.html` still
claims the pre-flight check is a guarantee, which is what D80 says to fix. The `almanac` namespace
check is **done** — clear in core, in the HACS default list, and in a GitHub manifest-domain
search (`DESIGN.md` "Remaining checks").

**almanac is installable, and §17.2 is what to read before touching how.** D71's remaining
half — the workflow that builds and zips from *inside* `custom_components/almanac/` — is built:
`scripts/package.sh` plus `.github/workflows/release.yml`, with `tests/test_packaging.py` running
the script against a fabricated tree and reading the archive back. **The one fact everything there
rests on:** HACS's `zip_release` extractor calls `extractall()` with no member rewriting, so
`manifest.json` must sit at the *zip root* — a zip one segment too deep installs without
complaint, and Home Assistant then finds a directory it does not load, with nothing in any log
naming the cause. A.12, re-verified 2026-10-06. **No release is tagged yet**, and
`hide_default_branch` plus `zip_release` means that until one is, HACS has nothing installable to
offer at all — the hand route in `README.md` (`npm run package`, then unpack `build/almanac.zip`
into `<config>/custom_components/almanac/`) is what works today.

**Eleven judgement calls are flagged for the owner** and are marked as such where they are
recorded: D91 (disarming mid-interval exits immediately), D97 (an unreadable condition changes
nothing), D104 (the re-enumeration period when D44's horizon comes back empty), D109 (`run_now`
fires no occurrence event) and D114 (`diaspora` and the two candle-lighting offsets have no user
surface — this one moves Simchat Torah by a day if the inference is wrong), plus step 8's two:
D119 (§5.5's third coverage state, *estimated*, has no producer and is not synthesised — the
alternative is a fourth `HorizonKind`, which is a contract change) and D120 (a dry run is one
evaluation at one instant, not a replay of the interval leading to it), plus step 9a's one:
D131 (a missing frontend bundle logs one line and the integration loads without a UI, which is
the opposite trade from §17's reason for not committing `dist/` — the two are consistent, but a
bad release is quiet), plus step 9e's one: D153 (an unparseable payload does not block Save —
the text stays as typed, the border goes red, the line under it says the payload is left as it
was, and the schedule saves carrying the mapping that last parsed; blocking would need the editor
to track every payload's validity across rule additions, removals and selection changes, and a
counter that drifts either blocks a save for no visible reason or allows one the screen says is
broken), plus step 9f's one: **D160's cost** (publishing `custom_ui_more_info` on the schedule
switch **replaces** the switch's native control rather than adding to it — core checks that
attribute *before* it dispatches on domain, so the stock toggle is gone from the dialog. That is
why almanac's dialog body leads with its own Arm/Disarm button, and the alternative — not
publishing the attribute, and a schedule's dialog saying nothing about the schedule — is a real
option the owner may prefer), plus the release workflow's one: **D164** (a tag stamps
`manifest.json`'s version inside the zip and nowhere else, so a clone always says `0.0.1` however
many releases exist and the version a developer sees locally is never the version a user has. The
alternative is a bump committed before tagging — more honest and one more manual step; a workflow
that committed it would need write access to the branch it was triggered from).
**Step 9d's D149 has left this list:** 9e lifted it.

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
