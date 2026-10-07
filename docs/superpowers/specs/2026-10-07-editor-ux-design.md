# Editor UX pass — design

Status: draft for owner review, 2026-10-07. Proposes D166–D170.

## Brief

The editor is built for developers; this pass makes it a home-user tool.

1. **HA's own pickers.** Entities, scripts, targets and services use HA's components, loaded the
   way scheduler-card and Alarmo load them. If loading fails, the editor falls back to today's
   inputs rather than breaking.
2. **Real payload forms.** Service and script fields render from their own schema (number,
   select, colour…). Unknown keys and unloaded services keep a JSON box, so nothing is lost.
3. **A proper time picker.** Tiles (Clock / Sun / Jewish times / Entity…), then one focused
   control: big digits for a clock time, event chips for the others, and an offset stepper with
   *before / at / after*.
4. **Plain language, real buttons.** No D-numbers or § on screen. Every button is an `ha-button`.
5. **Less on screen.** Name, days, times and actions are visible. The rest sits under *Advanced*.

Open for the owner: §6.

## 1. Loading HA components — D167

- Add a new `frontend/src/ha-elements.ts` exporting `loadHaElements(): Promise<boolean>`.
  - It follows the technique in scheduler-card's `src/lib/load_ha_form.js` (sha 83b6dec82d32) and
    Alarmo's `load-ha-elements.ts` (sha 47b31a68c61c).
  - It creates a `partial-panel-resolver`, loads the `config` route, then the `automation`
    sub-route. That defines `ha-form`, `ha-selector` and `ha-entity-picker`.
- Guard and wait only on the elements almanac uses: `ha-form`, `ha-selector` and
  `ha-entity-picker`. Allow a ~4 s budget. Return `false` on timeout or on any throw.
- The panel calls it once in `firstUpdated`. The editor receives a boolean and renders either
  HA components or the fallback.
  - The fallback is the current `<input list>` and the JSON textarea.
  - A fallback editor shows one line: *"Home Assistant's pickers didn't load — basic inputs
    shown."*
- **Risk, accepted:** this is private API (`_updateRoutes`, `routerOptions.routes.X.load()`).
  - The loader trick has no reported breakage in either project.
  - What did break was a specific component: `ha-combo-box`, removed in HA 2026.1
    (scheduler-card #1061/#1070, browser_mod #1113).
  - So depend on `ha-selector` and `ha-form` only. These are what both projects migrated to.
- `ha-button` is in HA's entry bundle (verified, frontend 20260826.7), so it needs no loading.
- Selector interface used: `.hass`, `.selector`, `.value`, `.label`, `.helper`, `.required`
  (**defaults to true — always set it**), and the `value-changed` event with `detail.value`.
  This is from `ha-selector.ts` at tag 20260826.7.

## 2. Pickers and payload forms — D166 (supersedes D152, refines D156)

| Field | Control | Fallback |
| --- | --- | --- |
| Entity (conditions, desired state) | `ha-selector` `{entity:{}}` | `<input list>` |
| Entity-time anchor | `{entity:{filter:{device_class:"timestamp"}}}` | `<input list>` |
| Script | `{entity:{domain:"script"}}` | `<input list>` |
| Action target | `{target:{}}` — replaces the comma-separated box | text input |
| Service | searchable service picker | free text |

**Payload.**
- When the service or script has an entry in `hass.services[domain][service]`, render each
  entry of `fields` as an `ha-selector`. Use its `selector`, `name`, `description` and
  `required`.
  - Script fields appear there too: `script/__init__.py` calls `async_set_service_schema` with
    the script's `fields`.
  - Keys present in the stored data but absent from the schema render in a collapsed
    **Other data (JSON)** box.
  - The saved mapping is the form values merged with that box. That keeps D152's lossless
    round trip.
- When there is no entry (integration unloaded, or a misspelt name), show only the JSON box,
  with the line *"This service isn't loaded right now, so its fields can't be shown."*
  - D17 (a schedule may name an unloaded service) still holds. D156's concern, that a picker
    can't name an unloaded service, is met by the free-text escape.
- **Why D152 is superseded, not just lifted:** D152 rejected schema forms because they cannot
  represent unknown keys or unknown services. The JSON box for exactly those cases removes the
  objection. The form is the convenience and the box is the guarantee.
- **Known gap:** a just-registered service has `fields:{}` for up to ~5 s, until core's debounced
  refetch. The form re-renders when `hass` updates.
- The pure partition of stored data into *schema keys* and *other keys* goes in a no-import
  module, under D132's rule. D132 says modules run by `node --test` have no run-time imports.

## 3. Time picker — D168

Applies to every anchor (`_anchorFields`, `editor.ts:1076`). This is layout A plus clock
control 1 from the mockups. It reverses the comment at `editor.ts:1071`. A single list was the
problem: one long native `<select>`.

**Step 1 — tiles.**
- One tile for **Clock**, one per resolver domain in `almanac/resolvers` (today: Sun, Jewish
  times), and one for **From an entity**.
- The tiles are data-driven, so a new resolver adds a tile with no editor change.

**Step 2 — the value.**
- **Clock:** big `HH:MM` digits, each one tappable to type, with arrow keys and scrolling to step
  it. Below them are quick chips: times already used in this schedule, then 06:00, 07:00,
  18:00, 22:00.
  - Seconds stay in storage but are not shown.
  - Drop the help line. D40's wall-time behaviour is what users expect anyway.
- **Resolver tile:** chips of that domain's offerings, using `display_name`.
  - Up to ~5 common ones show, then **More…**, which opens a searchable list.
  - If the offering has edges, a *begins / ends* segmented control appears.
- **Entity:** the timestamp-filtered entity picker from §2.

**Offset** (every tile except Clock):
- A stepper (±5 min, with a long press stepping faster) plus a *before / at / after* segment.
  *at* sets the offset to 0 and hides the stepper.
- Storage is unchanged: signed seconds, so *before* is negative (D7).

**Summary line** under the control, for example *"45 min before candle lighting — next: Fri
9 Oct, 16:56"*. The "next" part needs a resolver query; see §6.

## 4. Language and buttons — D169

- Remove every D-number, `§` and code-ish phrasing from visible template text (14 sites,
  including `editor.ts:1142`, `:1177`, `desired.ts:203` and `mapping.ts:117`). Rewrite each as
  a plain sentence, or delete it.
- Add a test to `tests/test_frontend_assets.py` that fails on `\bD\d+\b` or `§` inside
  `html\`` literals.
  - Comments are exempt. D-numbers belong in code comments.
- Replace every `<button>` with `ha-button`. A selectable chip, such as *Days of the week /
  Day set*, becomes a segmented control so it reads as a choice.
- Add/Remove actions get icons and labels (*＋ Add a moment*), so nothing looks like plain text.

## 5. Advanced section — D170

- **Visible by default:** name, which days, the shape of the day (moments and intervals with
  their times), what to do, and *Only when…* conditions.
- **Collapsed under *Advanced*:** policy, on-exit behaviour, completion, and any other field a
  first schedule doesn't need.
- The section opens itself if any of its fields differs from the default, so nothing hidden is
  ever silently set.

## 6. Open — owner

1. **"Next: …" on chips and the summary line — decided (owner, 2026-10-07): build it.** A new
   websocket command, `almanac/anchor/preview`, takes an anchor and returns its next few
   instants. It is built on `async_resolve_anchor_on_date`. `now` is read only at the websocket
   handler, under D64. D64 says nothing below the top-level tick reads a clock.
2. **Which offerings count as "common"** for the first ~5 chips. **Proposal:** a fixed short list
   per domain in the resolver catalogue, and everything else under *More…*.

## 7. Testing

- `node --test`: offset sign mapping, partition and merge of payload keys, and the selection of
  quick chips. All of these are pure modules.
- Python: the no-internal-ids test, the import-rule tests extended to the new pure modules, and
  wire-contract coverage if §6.1's command is added.
- Manual, on the owner's HA: open the panel directly, as a cold load with no dashboard visited
  first. Confirm the pickers render. Block the loader and confirm the fallback. Edit a
  `light.turn_on` action and a script with fields. Edit a schedule naming an unloaded service.
  Build *45 min before candle lighting*.
