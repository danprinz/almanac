#!/usr/bin/env bash
#
# D71's zip, and the one thing it refuses to ship.
#
# `hacs.json` sets `zip_release` with `filename: almanac.zip`, and HACS's
# handling of that is not the handling of a repository archive. Verified against
# `hacs/integration` `repositories/base.py` (A.12):
#
#   - `async_download_zip_file` calls `zip_file.extractall(self.content.path.local)`
#     with no member rewriting, where `repositories/integration.py::localpath` is
#     `f"{config_path}/custom_components/{domain}"`.
#   - the *other* extractor, `download_repository_zip`, is the one that strips a
#     leading path segment (`"/".join(path.filename.split("/")[1:])`), and it is
#     the path taken when `zip_release` is false.
#
# So the archive's internal paths must already be what belongs directly under
# `custom_components/almanac/`: `manifest.json` at the zip root, not
# `almanac/manifest.json`. That is why this script zips from *inside* the staged
# directory and never from the repository root.
#
# The refusal is D163: no bundle, no zip. D131 lets a missing `frontend/dist/`
# load as an engine with no UI and one log line, which is right for a clone and
# wrong for a release — a release that quietly shipped no frontend would look
# exactly like a working install until someone opened the sidebar. Packaging is
# the last moment where that is still a loud failure, so it fails here.
#
# Usage:
#   scripts/package.sh            # version left as `manifest.json` has it
#   scripts/package.sh v0.1.0     # version stamped into the zipped manifest
#
# Environment:
#   OUT_DIR   where `almanac.zip` is written (default: `build/` at the repo root)

set -euo pipefail

VERSION="${1:-}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/custom_components/almanac"
OUT="${OUT_DIR:-$ROOT/build}"
ZIP="$OUT/almanac.zip"

# The two entry points, spelled as `const.py`'s BUNDLE_PANEL and BUNDLE_CARD
# spell them. `tests/test_packaging.py` asserts the three agree.
BUNDLES=("almanac-panel.js" "almanac-card.js")

# Not shipped. `frontend/src` and `frontend/test` are inputs to the build, and
# the build output is what runs; shipping the TypeScript would put a second,
# divergeable copy of every element inside every installation.
EXCLUDE=("frontend/src" "frontend/test")

say() { printf 'package: %s\n' "$1"; }

die() { printf 'package: %s\n' "$1" >&2; exit 1; }

for bundle in "${BUNDLES[@]}"; do
  if [ ! -f "$SRC/frontend/dist/$bundle" ]; then
    die "frontend/dist/$bundle is missing. Run \`npm run build\` first — D163: a
         release zip without a frontend is an install that looks finished and has
         no UI, and D131 means nothing but one log line would say so."
  fi
done

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
PKG="$STAGE/almanac"

cp -R "$SRC" "$PKG"

for path in "${EXCLUDE[@]}"; do
  rm -rf "$PKG/$path"
done

find "$PKG" -name '__pycache__' -type d -prune -exec rm -rf {} +
find "$PKG" -name '*.pyc' -delete
find "$PKG" -name '.DS_Store' -delete

if [ -n "$VERSION" ]; then
  # A tag is `v0.1.0`; a manifest version is `0.1.0`. HA reads this value and
  # `frontend_setup.py` puts it in the cache-busting query, so a release whose
  # manifest said the previous version would serve the previous bundle from
  # cache to anyone whose `?m=` mtime happened to survive extraction.
  STAMP="${VERSION#v}"
  MANIFEST="$PKG/manifest.json" STAMP="$STAMP" python3 - <<'PY'
import json
import os
import pathlib

path = pathlib.Path(os.environ["MANIFEST"])
manifest = json.loads(path.read_text(encoding="utf-8"))
manifest["version"] = os.environ["STAMP"]
path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
PY
  say "manifest version stamped as $STAMP"
fi

mkdir -p "$OUT"
rm -f "$ZIP"

# `-X` drops the extra file attributes: they are the only thing that would make
# two zips of identical content differ.
(cd "$PKG" && zip -r -q -X "$ZIP" .)

say "wrote $ZIP"
unzip -l "$ZIP"
