"""Delivering the panel and the card — §17's D69 and D70, and what step 9a found.

Named `frontend_setup` rather than `frontend` because `frontend/` next to it is
the bundle directory (D67) and a module that shadowed it would be a trap for
whoever first adds an `__init__.py` there.

Three facts shape everything in here, and none of them is obvious from D69's
two-row table:

- **Registration is not idempotent, and a config entry reload re-runs setup.**
  `frontend.async_register_built_in_panel` raises `ValueError` on a second
  registration of the same url path unless `update=True`, which
  `panel_custom.async_register_panel` does not expose; `add_extra_js_url` is a
  set and so quietly accumulates a second URL when a rebuilt bundle changes the
  cache-busting query; and aiohttp's router has no removal API at all, so a
  second static path for one url path is a duplicate route that nothing can take
  back. The panel and the card URL are therefore undone in
  `async_unregister_frontend`, and the static path is registered once per
  process and never undone. See D130.
- **The built directory may be absent.** D71 ships `dist/` only inside the
  release zip, so a developer running from a clone has nothing there until
  `npm run build`. That is not a reason to fail setup: the engine, the services,
  the entities and both websocket commands work without a frontend, and an
  integration that refuses to load because a UI is missing is harder to debug
  than one that loads and says the UI is missing. See D131.
- **Nothing here may read the clock or block the event loop.** `getmtime` is
  filesystem I/O and goes through an executor job, which is the only reason this
  module has an `async` function at all.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Final

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback
from homeassistant.loader import async_get_integration

from .const import (
    BUNDLE_CARD,
    BUNDLE_PANEL,
    CARD_COMPONENT,
    DOMAIN,
    FRONTEND_DIST,
    FRONTEND_URL_BASE,
    PANEL_COMPONENT,
    PANEL_ICON,
    PANEL_TITLE,
    PANEL_URL_PATH,
)

_LOGGER = logging.getLogger(__name__)

# Set once the static path has been registered on this `hass`. A reload must not
# register it a second time, and there is no API that would let it unregister.
_DATA_STATIC_PATH: Final = f"{DOMAIN}_static_path"

# The card URL that was handed to `add_extra_js_url`, kept so that unload hands
# back exactly the string that was added. `remove_extra_js_url` raises `KeyError`
# on anything else, and the cache-busting query makes "anything else" easy to
# produce after a rebuild.
_DATA_CARD_URL: Final = f"{DOMAIN}_card_url"


def _dist_dir() -> Path:
    """Where the built bundles are, per D67.

    Resolved against this file rather than against `hass.config.path(...)`. The
    two agree for every real installation, but only one of them is a fact: the
    bundles ship *inside* the integration directory, so the integration's own
    `__file__` is what knows where they are. Building the path out of the config
    directory would be a second claim about the same thing, and it is the claim
    that breaks first — under the test harness, where `config_dir` is a temporary
    directory that has no `custom_components` in it at all.
    """
    return Path(__file__).parent / FRONTEND_DIST


def _bust(url: str, version: str, mtime: int) -> str:
    """D69's cache-busting query.

    `version` alone is not enough — a developer rebuilds many times against one
    manifest version — and `mtime` alone is not enough either, because an install
    from the release zip gets whatever mtime extraction happened to give it. The
    pair is what `browser_mod` arrived at (A.12) and the reason is the same.
    """
    return f"{url}?v={version}&m={mtime}"


async def async_register_frontend(hass: HomeAssistant) -> None:
    """Serve the bundles, register the panel, and inject the card.

    Called from `async_setup_entry` after the websocket commands, because both
    surfaces are useless without them and a panel that loads before its API can
    answer renders an error on first paint.
    """
    dist = _dist_dir()
    bundles = {BUNDLE_PANEL: dist / BUNDLE_PANEL, BUNDLE_CARD: dist / BUNDLE_CARD}
    mtimes = await hass.async_add_executor_job(_async_stat_bundles, bundles)

    missing = [name for name, mtime in mtimes.items() if mtime is None]
    if missing:
        # D131 — a missing bundle is a missing UI, not a broken integration.
        _LOGGER.error(
            "almanac's frontend is not built: %s not found under %s. The"
            " integration will run without its panel and card; install from a"
            " HACS release (D71) or run `npm run build` in a clone",
            ", ".join(sorted(missing)),
            dist,
        )
        return

    integration = await async_get_integration(hass, DOMAIN)
    version = str(integration.version)

    if not hass.data.get(_DATA_STATIC_PATH):
        # D129 — the directory, not the two files. D70's dynamic import makes
        # Rollup emit the card's body as a separately named chunk, and the
        # browser resolves that name relative to the card's own URL, so the
        # chunk has to be reachable under the same prefix. Its filename carries
        # a content hash, which is what keeps `cache_headers` honest for a file
        # the query string above does not name.
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    FRONTEND_URL_BASE,
                    str(dist),
                    cache_headers=True,
                )
            ]
        )
        hass.data[_DATA_STATIC_PATH] = True

    panel_url = _bust(
        f"{FRONTEND_URL_BASE}/{BUNDLE_PANEL}", version, mtimes[BUNDLE_PANEL] or 0
    )
    card_url = _bust(
        f"{FRONTEND_URL_BASE}/{BUNDLE_CARD}", version, mtimes[BUNDLE_CARD] or 0
    )

    if not frontend.async_panel_exists(hass, PANEL_URL_PATH):
        await panel_custom.async_register_panel(
            hass,
            frontend_url_path=PANEL_URL_PATH,
            webcomponent_name=PANEL_COMPONENT,
            sidebar_title=PANEL_TITLE,
            sidebar_icon=PANEL_ICON,
            module_url=panel_url,
            # Not an iframe: the panel needs the live `hass` object and the open
            # websocket, and an iframe gets neither.
            embed_iframe=False,
            # D63's view is a read of what the schedule list already exposes, so
            # it is not admin-only. `almanac/dry_run` is the restricted command
            # and the panel does not call it; the editor, which does, will check
            # `hass.user.is_admin` itself rather than hiding the whole screen.
            require_admin=False,
        )

    # D69 — `add_extra_js_url`, so the card works with no manual Resource step in
    # both storage and YAML dashboard modes. D70 is what makes that affordable:
    # this file is fetched on every frontend page, and what it costs is one
    # `customElements.define` and one array push.
    frontend.add_extra_js_url(hass, card_url)
    hass.data[_DATA_CARD_URL] = card_url

    _LOGGER.debug(
        "almanac frontend registered: panel %s at /%s, card %s as <%s>",
        panel_url,
        PANEL_URL_PATH,
        card_url,
        CARD_COMPONENT,
    )


def _async_stat_bundles(bundles: dict[str, Path]) -> dict[str, int | None]:
    """`getmtime` for each bundle, or `None` where it is not there.

    Runs in an executor. One pass over both files rather than two jobs, because
    the pair is what a cache-busting query has to agree about.
    """
    found: dict[str, int | None] = {}
    for name, path in bundles.items():
        try:
            found[name] = int(os.path.getmtime(path))
        except OSError:
            found[name] = None
    return found


@callback
def async_unregister_frontend(hass: HomeAssistant) -> None:
    """Undo what can be undone — D130.

    The panel and the card URL are removed because leaving them would make the
    next setup raise and the one after that serve two copies of the card. The
    static path stays: aiohttp has no unregister, which is also why it was only
    registered once.
    """
    if frontend.async_panel_exists(hass, PANEL_URL_PATH):
        frontend.async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)

    card_url: Any = hass.data.pop(_DATA_CARD_URL, None)
    if card_url is not None:
        try:
            frontend.remove_extra_js_url(hass, card_url)
        except KeyError:
            # Something else removed it, which is not worth failing an unload
            # over — the set is global and almanac is not its only writer.
            _LOGGER.debug("almanac's card URL was already removed: %s", card_url)
