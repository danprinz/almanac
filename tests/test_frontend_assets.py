"""The delivery path — D69, D70 and §17.1's D129–D131.

Three kinds of test live here, and they are different kinds on purpose.

*What the Python side registers* — the panel, the card URL, the static path —
is tested through `hass`, against the real core APIs, because the whole point of
§17.1 is that none of those three registrations behaves the way D69's table
implies. The reload tests are the ones that matter: a config entry reload is
ordinary, and before D130 it would have raised on the panel and silently
double-injected the card.

*What is served* is tested with a real HTTP GET, because D129 is a claim about a
URL prefix and a chunk filename that nothing in Python ever names. A test that
only inspected `hass.data` would pass with the per-file registration D69
originally described, which cannot serve the chunk at all.

*What must not drift* is tested by reading the TypeScript and the Rollup config
as text. Two languages hold one contract here — the element names, the bundle
filenames, and D70's rule that the card entry imports nothing at run time — and
nothing else in the build would notice them disagreeing. `npm run typecheck` is
blind to the Python constants, and the Python is blind to the bundle. This is
the same technique as `tests/test_design_constraints.py`'s AST sweep for D64,
for the same reason: a constraint that cannot be retrofitted needs a test from
the first commit, not from the first bug.
"""

from __future__ import annotations

import json
import logging
import pathlib
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from homeassistant.components import frontend
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.almanac import frontend_setup
from custom_components.almanac.const import (
    BUNDLE_CARD,
    BUNDLE_PANEL,
    CARD_COMPONENT,
    DOMAIN,
    FRONTEND_DIST,
    FRONTEND_URL_BASE,
    MORE_INFO_COMPONENT,
    PANEL_COMPONENT,
    PANEL_EDIT_PARAM,
    PANEL_ICON,
    PANEL_TITLE,
    PANEL_URL_PATH,
)

REPO = pathlib.Path(__file__).resolve().parent.parent
SRC = REPO / "custom_components" / DOMAIN / "frontend" / "src"

# Read rather than spelled out, which is the opposite of this file's usual
# technique and is deliberate. Everything else here is asserted against a
# literal so that a rename fails the test instead of agreeing with it; a version
# is not a name. D164 moves it on every release, and a literal here would make
# every release an edit to a file about URLs — the kind of edit that gets made
# without reading what it is in.
MANIFEST_VERSION = json.loads(
    (REPO / "custom_components" / DOMAIN / "manifest.json").read_text(encoding="utf-8")
)["version"]


def card_urls(hass: HomeAssistant) -> list[str]:
    """Every extra module URL that belongs to almanac.

    The set is global and almanac is not its only possible writer, so a count of
    the whole set would be a test of whatever else is loaded.
    """
    urls: Any = hass.data[frontend.DATA_EXTRA_MODULE_URL].urls
    return sorted(url for url in urls if BUNDLE_CARD in url)


def panel_config(hass: HomeAssistant) -> dict[str, Any]:
    """`panel_custom`'s own block inside the registered panel's config."""
    panel = hass.data[frontend.DATA_PANELS][PANEL_URL_PATH]
    assert panel.config is not None
    custom: Any = panel.config["_panel_custom"]
    return custom


# --- what gets registered -------------------------------------------------


async def test_panel_is_registered_for_the_panel_bundle(
    hass: HomeAssistant, setup_almanac: MockConfigEntry
) -> None:
    """D69's left-hand column: `panel_custom`, not an iframe, not admin-only."""
    assert frontend.async_panel_exists(hass, PANEL_URL_PATH)

    panel = hass.data[frontend.DATA_PANELS][PANEL_URL_PATH]
    assert panel.component_name == "custom"
    assert panel.sidebar_title == PANEL_TITLE
    assert panel.sidebar_icon == PANEL_ICON
    assert panel.require_admin is False

    custom = panel_config(hass)
    assert custom["name"] == PANEL_COMPONENT
    # An iframe would get neither the live `hass` object nor the websocket, and
    # the panel is nothing without both.
    assert custom["embed_iframe"] is False
    assert urlparse(custom["module_url"]).path == f"{FRONTEND_URL_BASE}/{BUNDLE_PANEL}"


async def test_card_url_is_injected_exactly_once(
    hass: HomeAssistant, setup_almanac: MockConfigEntry
) -> None:
    """D69's right-hand column: `add_extra_js_url`, so no Resource step."""
    urls = card_urls(hass)
    assert len(urls) == 1
    assert urlparse(urls[0]).path == f"{FRONTEND_URL_BASE}/{BUNDLE_CARD}"


@pytest.mark.parametrize("bundle", [BUNDLE_PANEL, BUNDLE_CARD])
async def test_both_urls_are_busted_by_version_and_mtime(
    hass: HomeAssistant,
    setup_almanac: MockConfigEntry,
    built_frontend: pathlib.Path,
    bundle: str,
) -> None:
    """D69's cache-busting query, and both halves of it are load-bearing.

    `v` alone does not change while a developer rebuilds against one manifest
    version; `m` alone is whatever zip extraction happened to set. The pair is
    what makes a rebuilt bundle a new URL.
    """
    if bundle == BUNDLE_PANEL:
        url = panel_config(hass)["module_url"]
    else:
        url = card_urls(hass)[0]

    query = parse_qs(urlparse(url).query)
    assert query["v"] == [MANIFEST_VERSION]
    assert query["m"] == [str(int((built_frontend / bundle).stat().st_mtime))]


# --- what gets served -----------------------------------------------------


async def test_the_whole_dist_directory_is_served(
    hass: HomeAssistant,
    setup_almanac: MockConfigEntry,
    built_frontend: pathlib.Path,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """D129 — the chunk is the test, not the two entries.

    D70 makes Rollup emit the card's body as a file whose name it invents, and
    the browser resolves that name relative to the card's own URL. A
    `StaticPathConfig` per bundle serves both entries and 404s the chunk, which
    is a card that defines its element and then fails to load its body. So the
    assertion that distinguishes the two designs is a GET of a file no Python
    constant names.
    """
    chunks = built_frontend / "chunks"
    chunks.mkdir()
    (chunks / "card-body-BiGWNZuI.js").write_text("export default null;\n")

    client = await hass_client_no_auth()
    for path in (BUNDLE_PANEL, BUNDLE_CARD, "chunks/card-body-BiGWNZuI.js"):
        response = await client.get(f"{FRONTEND_URL_BASE}/{path}")
        assert response.status == 200, path

    # And the prefix serves the directory, not everything: a name that is not
    # there is a 404 rather than a fallthrough to the frontend's index.
    missing = await client.get(f"{FRONTEND_URL_BASE}/almanac-nothing.js")
    assert missing.status == 404


# --- reload, which is what D130 is about ----------------------------------


async def test_unload_undoes_the_panel_and_the_card(
    hass: HomeAssistant, setup_almanac: MockConfigEntry
) -> None:
    """D130 — both of the undoable registrations are undone."""
    assert await hass.config_entries.async_unload(setup_almanac.entry_id)
    await hass.async_block_till_done()

    assert not frontend.async_panel_exists(hass, PANEL_URL_PATH)
    assert card_urls(hass) == []


async def test_reload_neither_raises_nor_duplicates(
    hass: HomeAssistant, setup_almanac: MockConfigEntry
) -> None:
    """The failure D130 exists to prevent, in the order it would happen.

    Without the unload half, `panel_custom.async_register_panel` raises
    `ValueError` on the second setup — it has no `update=True` to pass through —
    and the entry comes back `SETUP_ERROR`. Without keeping the exact URL string,
    the card's set grows a second entry whenever a rebuild changed `?m=`, and the
    browser then loads two copies of a file whose whole job is to win a
    `customElements.define` race.
    """
    assert await hass.config_entries.async_reload(setup_almanac.entry_id)
    await hass.async_block_till_done()

    assert setup_almanac.state is ConfigEntryState.LOADED
    assert frontend.async_panel_exists(hass, PANEL_URL_PATH)
    assert len(card_urls(hass)) == 1


async def test_static_path_is_registered_once_per_process(
    hass: HomeAssistant,
    websocket_api: None,
    config_entry: MockConfigEntry,
) -> None:
    """D130's asymmetry: aiohttp's router has no unregister, so neither do we.

    Counted across a reload rather than asserted on the `hass.data` flag,
    because the flag is the mechanism and the call count is the requirement.
    Filtered to almanac's own prefix, because setting almanac up sets `frontend`
    up, and `frontend` registers static paths of its own.
    """
    calls: list[Any] = []
    original = hass.http.async_register_static_paths

    async def counting(configs: Any) -> None:
        calls.extend(
            config for config in configs if config.url_path == FRONTEND_URL_BASE
        )
        await original(configs)

    hass.http.async_register_static_paths = counting  # type: ignore[method-assign]

    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    assert len(calls) == 1


# --- D131 — the path the autouse fixture exists to keep out of the suite ---


async def test_a_missing_bundle_logs_and_the_entry_still_loads(
    hass: HomeAssistant,
    websocket_api: None,
    config_entry: MockConfigEntry,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """D131 — a clone before `npm run build` is a working integration.

    The engine, the services, the entities and both websocket commands do not
    need a bundle. Failing setup here would turn "the UI is not built" into
    "almanac is broken", which is a strictly worse thing to have to diagnose.
    """
    empty = tmp_path / "unbuilt"
    empty.mkdir()
    monkeypatch.setattr(frontend_setup, "_dist_dir", lambda: empty)

    config_entry.add_to_hass(hass)
    with caplog.at_level(logging.ERROR, logger="custom_components.almanac"):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    assert not frontend.async_panel_exists(hass, PANEL_URL_PATH)
    assert card_urls(hass) == []

    # And it says which files and where, because the only way out of this state
    # is a build, and the message is the only place that is written down.
    assert "frontend is not built" in caplog.text
    assert BUNDLE_PANEL in caplog.text
    assert BUNDLE_CARD in caplog.text
    assert str(empty) in caplog.text


async def test_unload_after_a_missing_bundle_is_quiet(
    hass: HomeAssistant,
    websocket_api: None,
    config_entry: MockConfigEntry,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unload must not assume setup got as far as registering anything.

    `remove_extra_js_url` raises `KeyError` on a URL that was never added, and
    `async_remove_panel` warns on a url path that was never registered. D131
    makes "never added" an ordinary outcome, so both have to be guarded.
    """
    empty = tmp_path / "unbuilt"
    empty.mkdir()
    monkeypatch.setattr(frontend_setup, "_dist_dir", lambda: empty)

    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.NOT_LOADED


# --- what must not drift between the two languages ------------------------


def test_the_constants_name_the_files_rollup_emits() -> None:
    """`const.py` and `rollup.config.mjs` are one contract in two files.

    D68 chose Rollup precisely so the output filenames would be predictable
    enough to hard-code in Python. Nothing in either toolchain checks that the
    hard-coded names are the ones being emitted: a renamed entry produces a
    successful build and a 404 at run time.
    """
    config = (REPO / "rollup.config.mjs").read_text(encoding="utf-8")
    emitted = dict(re.findall(r'bundle\("(\w+)\.ts",\s*"([\w-]+)"\)', config))

    assert emitted == {"card": BUNDLE_CARD[:-3], "panel": BUNDLE_PANEL[:-3]}
    assert BUNDLE_CARD.endswith(".js")
    assert BUNDLE_PANEL.endswith(".js")

    # D129 — the chunk is cache-busted by its own name, because the Python side
    # cannot append a query to a URL it does not construct.
    assert "chunkFileNames: \"chunks/[name]-[hash].js\"" in config


def test_the_element_names_match_the_bundles() -> None:
    """A webcomponent name that is not defined renders an empty panel.

    `panel_custom` takes the tag on faith and the browser reports nothing — the
    element simply never upgrades. Same for the card: Lovelace looks the type up
    in `customElements` and shows its own "custom element doesn't exist" card.
    """
    assert f'@customElement("{PANEL_COMPONENT}")' in (SRC / "panel.ts").read_text(
        encoding="utf-8"
    )
    assert f'const CARD_TAG = "{CARD_COMPONENT}"' in (SRC / "card.ts").read_text(
        encoding="utf-8"
    )


def test_the_card_entry_imports_nothing_at_run_time() -> None:
    """D70, which §17 says cannot be retrofitted — so it is tested from day one.

    `add_extra_js_url` injects this file into the shared `index.html`, so it
    executes on every frontend page whether or not a dashboard has an almanac
    card on it. A single static import pulls its whole transitive graph onto
    that path, and Lit is the first thing anybody would reach for. `import type`
    is erased by the compiler and `await import(...)` is the deferral itself;
    anything else is a regression that no build step would report.
    """
    source = (SRC / "card.ts").read_text(encoding="utf-8")
    statements = re.findall(r"^import\b.*$", source, flags=re.MULTILINE)

    assert statements, "the regex stopped matching, not the constraint holding"
    for statement in statements:
        assert statement.startswith("import type "), statement

    assert 'await import("./card-body")' in source


def test_the_bundles_are_resolved_from_the_package_not_the_config_dir() -> None:
    """§17.1 — the integration's own module path is what knows where they are.

    D67 ships the bundles inside `custom_components/almanac/`, which makes
    `__file__` a fact and `hass.config.path(...)` a second claim about the same
    thing. The committed source directory is the thing that proves the layout;
    `dist/` beside it is gitignored by D71 and absent on a fresh clone.
    """
    package = pathlib.Path(frontend_setup.__file__).parent

    assert SRC.is_dir()
    assert SRC.parent == package / FRONTEND_DIST.split("/")[0]


def test_the_pure_modules_import_nothing_at_run_time() -> None:
    """D132 — D70's technique again, for an unrelated reason.

    Three files hold everything in the frontend that can be decided without a
    browser. `rails.ts` has the arithmetic: D73's rails and their local scales,
    D76's order check, D79's two endpoints. `draft.ts` has the edit algebra:
    D139's draft and D140's diff, which is to say what the Save button sends.
    `form.ts` has the conversions between a native input and the schema, which
    joined them at step 9e when five components started reading the same
    `<input>` elements.
    Both are tested by `node --test`, which runs TypeScript by stripping the
    types and nothing else — in particular it does not resolve a bare specifier
    like `lit` or an extensionless relative one. So a single run-time import in
    either does not fail the build and does not fail `tsc`. It fails the unit
    tests, with a module-resolution error that reads like a broken toolchain, and
    the cheapest way out of that is to delete the test file.

    Hence this test, which names the real constraint while it still holds. It is
    also why `api.ts` imports the two write shapes *from* `draft.ts` rather than
    defining them: the dependency only points this way.
    """
    for name in ("rails.ts", "draft.ts"):
        source = (SRC / name).read_text(encoding="utf-8")
        statements = re.findall(r"^import\b.*$", source, flags=re.MULTILINE)

        assert statements, f"{name}: the regex stopped matching, not the rule"
        for statement in statements:
            assert statement.startswith("import type "), f"{name}: {statement}"

    # These are checked by the stronger rule: no import at all, type-only
    # included, so the non-empty assertion above would fail on them for the
    # opposite reason to the one it guards against. They declare their own
    # structural types instead of importing the stored ones (D132).
    for name in ("form.ts", "ha-elements.ts", "payload.ts", "pickers.ts", "timepick.ts"):
        assert not re.findall(
            r"^import\b.*$",
            (SRC / name).read_text(encoding="utf-8"),
            re.MULTILINE,
        ), name


def test_the_test_tsconfig_carries_the_flag_the_build_cannot() -> None:
    """Why there are two configs, measured rather than assumed.

    `allowImportingTsExtensions` is what lets the test file name `./rails.ts`
    with its extension, which is what Node's loader requires. TypeScript permits
    the flag only with `noEmit`, and `@rollup/plugin-typescript` sets
    `noEmit: false` so it can produce output — so the flag in `tsconfig.json`
    fails the build with TS5096. The split is therefore forced, and a future
    tidy-up that merges the two configs breaks `npm run build` rather than
    anything a type-checker would mention.
    """
    base = (REPO / "tsconfig.json").read_text(encoding="utf-8")
    scoped = (REPO / "tsconfig.test.json").read_text(encoding="utf-8")

    assert "allowImportingTsExtensions" not in base
    assert "allowImportingTsExtensions" in scoped
    # And `@types/node` stays out of the sources' scope, because the panel and
    # the card run in a browser.
    assert '"types": ["node"]' in scoped
    assert '"types"' not in base


def test_the_unit_tests_are_wired_to_a_script_and_a_directory() -> None:
    """A test suite nothing runs is a test suite that does not exist.

    Node's `--test` takes a glob here rather than the directory, which it tries
    to load as a module. The project has no CI yet (the release workflow D71
    needs is still missing), so this assertion is what records that `npm test`
    is the entry point.
    """
    package = (REPO / "package.json").read_text(encoding="utf-8")
    tests = SRC.parent / "test"

    assert "node --test" in package
    assert tests.is_dir()
    assert sorted(path.name for path in tests.glob("*.test.ts")) == [
        "draft.test.ts",
        "form.test.ts",
        "ha-elements.test.ts",
        "moreinfo.test.ts",
        "payload.test.ts",
        "pickers.test.ts",
        "rails.test.ts",
        "timepick.test.ts",
    ]


def test_the_editor_says_so_when_the_pickers_did_not_load() -> None:
    """D167's fallback is only honest if it is announced.

    A fallback that silently shows plain inputs looks like the editor is broken.
    The sentence is asserted as source text because the failing path is only
    reachable in a browser with the loader blocked, which is a manual check.
    """
    source = (SRC / "editor.ts").read_text(encoding="utf-8")
    assert "Home Assistant's pickers didn't load" in source
    assert '"failed"' in source


def test_the_more_info_element_is_the_tag_the_switch_publishes() -> None:
    """D160 -- the second contract `card.ts` carries, and the quieter one.

    A card whose tag nobody defined gets Lovelace's own "custom element doesn't
    exist" card, which is at least a visible complaint. The more-info element
    gets nothing: `more-info-content` reads the entity's `custom_ui_more_info`
    attribute, creates that tag with no loader and no fallback, and an undefined
    tag renders as an empty region. The dialog opens, the header and the history
    tabs are there, and the middle is blank.

    So this is the only check that the attribute `switch.py` publishes names an
    element this repository actually defines -- and it has to be a text check,
    because the two sides never meet at run time in any process that could
    compare them.
    """
    card = (SRC / "card.ts").read_text(encoding="utf-8")

    assert f'const MORE_INFO_TAG = "{MORE_INFO_COMPONENT}"' in card
    # The stub's lazy half, and the name is derived rather than independent so
    # that the two cannot drift into two unrelated strings.
    assert f'const MORE_INFO_IMPL_TAG = "{MORE_INFO_COMPONENT}-body"' in card
    assert 'await import("./more-info-body")' in card
    assert f'@customElement("{MORE_INFO_COMPONENT}-body")' in (
        SRC / "more-info-body.ts"
    ).read_text(encoding="utf-8")


def test_cores_more_info_event_is_spelled_in_exactly_one_file() -> None:
    """D159, and the reason it is a decision rather than a tidy-up.

    The event name is a verbatim third-party identifier, which Appendix B says
    this environment cannot certify, and it is the kind that fails *silently*:
    `dispatchEvent` with a name nobody listens for returns `True`, logs nothing
    and opens nothing. One spelling can be verified once against the installed
    frontend bundle and then trusted; a second spelling somewhere else is a
    second thing to verify that nobody will know to check.

    The assertion is a plain substring sweep rather than a parse, because the
    failure it guards against is someone writing the string inline in a `@click`
    -- which no import graph would show.
    """
    holder = SRC / "moreinfo.ts"
    literal = "hass-more-info"

    assert literal in holder.read_text(encoding="utf-8")
    elsewhere = [
        path.name
        for path in sorted(SRC.glob("*.ts"))
        if path != holder and literal in path.read_text(encoding="utf-8")
    ]

    assert elsewhere == [], f"D159: the event is also spelled in {elsewhere}"


def test_the_edit_link_and_the_panel_agree_on_the_route() -> None:
    """D161 -- three files build or read one URL, and nothing joins them.

    The card row and the more-info dialog both write
    `/<panel>?<param>=<schedule id>`; the panel reads the parameter back out of
    `window.location.search`. A disagreement is not an error anywhere: the panel
    loads, the list renders, and the schedule the user asked for simply is not
    open -- which looks like a slow page rather than a broken link.

    The two literals are deliberately repeated rather than imported from a shared
    module, because D70 keeps the card and panel bundles disjoint and a shared
    import is permission for Rollup to hoist a chunk onto every page. So the
    repetition is the design and this is what holds it together.
    """
    writers = ("card-body.ts", "more-info-body.ts")
    for name in writers:
        source = (SRC / name).read_text(encoding="utf-8")
        assert f'const PANEL_PATH = "/{PANEL_URL_PATH}"' in source, name
        assert f'const EDIT_PARAM = "{PANEL_EDIT_PARAM}"' in source, name
        # Built, not hand-spelled: a literal `?edit=` beside the constants would
        # be a fourth copy that this test does not see.
        assert "${PANEL_PATH}?${EDIT_PARAM}=" in source, name

    panel = (SRC / "panel.ts").read_text(encoding="utf-8")

    assert f'const EDIT_PARAM = "{PANEL_EDIT_PARAM}"' in panel
    assert "window.location.search" in panel


def test_every_ha_selector_sets_required_explicitly() -> None:
    """`ha-selector`'s `required` defaults to **true** (frontend 20260826.7).

    A selector that does not say otherwise refuses to clear, which for an optional
    field means a value the user can change but never remove. The default is a
    property default, not a template one, so nothing but a read of the template
    notices when it is forgotten.
    """
    found = 0
    for path in sorted(SRC.glob("*.ts")):
        source = path.read_text(encoding="utf-8")
        for match in re.finditer(r"<ha-selector\b", source):
            found += 1
            tag = source[match.start() : source.index(">", match.start())]
            assert ".required=" in tag, f"{path.name}: <ha-selector> without .required"
    assert found > 0, "the regex stopped matching, not the rule"
