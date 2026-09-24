# almanac — home-assistant-scheduler-v2

**Integration domain: `almanac`** (D53). The repo directory keeps its old name; the domain is
the permanent identifier.

A replacement for the Home Assistant scheduler stack (`nielsfaber/scheduler-component` +
`scheduler-card`): a schedule engine and UI whose rule model is **enumerable**, so that
"what will happen between now and Friday night" is a view you can actually render.

## Status — 2026-09-24

**Design settled. No code yet.** The brief's five open questions are answered, and the model
is recorded in `DESIGN.md` as numbered decisions D1–D71.

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

**Next step:** `DESIGN.md` §15 build order, step 1 — schema, storage collection, entity model,
config flow. The `almanac` namespace check is **done** — clear in core, in the HACS default
list, and in a GitHub manifest-domain search (`DESIGN.md` "Remaining checks").

**Frontend toolchain: decided** — §17, D67–D71. One repository, HACS category `integration`,
TypeScript + Lit built by Rollup into `custom_components/almanac/frontend/dist/`, panel via
`panel_custom` and card via `add_extra_js_url`, `dist/` gitignored and shipped only as a
`zip_release` asset. The single-installation requirement is met with **no** manual Resource step.
`hacs.json` is written. A.12 has the verification; read D70 before writing any card code, because
the always-loaded-stub constraint cannot be retrofitted.

**Next after that:** the UX thread. §16's three decisions (D61–D63) plus §17's constraints are
what gets handed over — constraints, not a layout.

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
