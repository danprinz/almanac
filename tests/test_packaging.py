"""The install path — D71, and §17.2's D162–D165.

This is the only part of almanac whose failure mode is *nothing happens*. Every
other surface reports: a bad websocket command is rejected with a message, a
missing resolver raises, an unparseable payload turns a border red. A release
whose zip has the wrong internal layout, or no frontend in it, installs cleanly
and then is simply not there — HACS reports success either way, and D131 means a
missing bundle is one log line in a file nobody opens.

So the three kinds of test here are the three ways that can happen.

*The layout* is tested by running `scripts/package.sh` against a fabricated
integration tree and reading the archive back with `zipfile`. The claim being
tested is A.12's: HACS's `zip_release` extractor calls `extractall()` with no
member rewriting, so `manifest.json` must be at the zip root. One wrong `cd` in
the script produces `almanac/manifest.json`, which extracts to
`custom_components/almanac/almanac/manifest.json` — a directory HA does not
load, with no error anywhere.

*The refusal* is tested by taking a bundle away. D163 is the whole reason the
script stages and checks rather than calling `zip` directly.

*What must not drift* is tested by reading the script and the workflow as text,
the same technique as `tests/test_frontend_assets.py` and for the same reason:
the bundle filenames are Python constants, a shell array and a Rollup config,
and nothing in any of the three builds would notice them disagreeing.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import zipfile

import pytest

from custom_components.almanac.const import BUNDLE_CARD, BUNDLE_PANEL, DOMAIN

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "package.sh"
WORKFLOW = REPO / ".github" / "workflows" / "release.yml"
HACS = REPO / "hacs.json"

SCRIPT_TEXT = SCRIPT.read_text(encoding="utf-8")
WORKFLOW_TEXT = WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture
def packageable(tmp_path: pathlib.Path) -> pathlib.Path:
    """A repository shaped like this one, with a built frontend in it.

    The real `custom_components/almanac` is not used, for the same reason
    `conftest.py`'s `built_frontend` does not use the real `dist/`: whether it
    has been built is a property of the machine. What the script has to get
    right is the archive's shape, and a two-line stub has the same shape as a
    114 KB bundle.

    The tree is laid out at the depth `package.sh` resolves its own root from —
    `$(dirname "$0")/..` — so the copied script finds the copied integration.
    """
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy2(SCRIPT, scripts / SCRIPT.name)

    package = tmp_path / "custom_components" / DOMAIN
    (package / "frontend" / "dist" / "chunks").mkdir(parents=True)
    (package / "frontend" / "src").mkdir(parents=True)
    (package / "frontend" / "test").mkdir(parents=True)
    (package / "engine").mkdir()
    (package / "__pycache__").mkdir()

    (package / "manifest.json").write_text(
        json.dumps({"domain": DOMAIN, "version": "0.0.0"}) + "\n", encoding="utf-8"
    )
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "engine" / "plan.py").write_text("", encoding="utf-8")
    for name in (BUNDLE_PANEL, BUNDLE_CARD):
        (package / "frontend" / "dist" / name).write_text(
            "export default null;\n", encoding="utf-8"
        )
    (package / "frontend" / "dist" / "chunks" / "card-body-BiGWNZuI.js").write_text(
        "export default null;\n", encoding="utf-8"
    )
    (package / "frontend" / "src" / "card.ts").write_text("", encoding="utf-8")
    (package / "frontend" / "test" / "rails.test.ts").write_text("", encoding="utf-8")
    (package / "__pycache__" / "const.cpython-314.pyc").write_text(
        "", encoding="utf-8"
    )

    return tmp_path


def run_package(root: pathlib.Path, *args: str) -> subprocess.CompletedProcess[str]:
    """`scripts/package.sh` inside `root`, writing its zip under `root/build`."""
    return subprocess.run(
        ["bash", str(root / "scripts" / "package.sh"), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def names(root: pathlib.Path) -> set[str]:
    """Every file member of the built archive, by its path inside the zip."""
    with zipfile.ZipFile(root / "build" / "almanac.zip") as archive:
        return {
            info.filename for info in archive.infolist() if not info.is_dir()
        }


# --- the layout, which is the thing that fails silently --------------------


def test_the_archive_extracts_straight_into_the_integration_directory(
    packageable: pathlib.Path,
) -> None:
    """A.12 — `extractall()` with no member rewriting, so no wrapping directory.

    The negative half is the real assertion. A zip built from the repository
    root, or from the staging directory's parent, is a perfectly valid zip whose
    every member is one segment too deep; HACS extracts it without complaint and
    Home Assistant then finds `custom_components/almanac` containing nothing but
    a directory called `almanac`.
    """
    result = run_package(packageable)
    assert result.returncode == 0, result.stderr

    members = names(packageable)
    assert "manifest.json" in members
    assert "engine/plan.py" in members
    assert f"frontend/dist/{BUNDLE_PANEL}" in members
    assert not [name for name in members if name.startswith(f"{DOMAIN}/")]


def test_the_built_frontend_ships_and_its_sources_do_not(
    packageable: pathlib.Path,
) -> None:
    """D71's `dist/`, and nothing that produced it.

    The chunk matters as much as the two entries: D129 serves the directory
    precisely because D70 makes Rollup invent that filename, so an exclusion
    pattern written against the two constants would ship a card that defines its
    element and 404s its body.
    """
    assert run_package(packageable).returncode == 0

    members = names(packageable)
    assert f"frontend/dist/{BUNDLE_CARD}" in members
    assert "frontend/dist/chunks/card-body-BiGWNZuI.js" in members
    assert not [name for name in members if name.startswith("frontend/src/")]
    assert not [name for name in members if name.startswith("frontend/test/")]
    assert not [name for name in members if "__pycache__" in name]


def test_a_version_argument_is_stamped_into_the_zipped_manifest(
    packageable: pathlib.Path,
) -> None:
    """The tag becomes the version Home Assistant reports, with no `v`.

    `frontend_setup.py` builds the cache-busting query out of this value and the
    bundle's mtime, so a manifest left at the previous version is a release that
    can serve the previous bundle from a browser cache.
    """
    assert run_package(packageable, "v1.2.3").returncode == 0

    with zipfile.ZipFile(packageable / "build" / "almanac.zip") as archive:
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
    assert manifest["version"] == "1.2.3"

    # And the repository is not touched: the stamp happens in the staging copy,
    # so a local packaging run does not leave the working tree dirty.
    on_disk = json.loads(
        (packageable / "custom_components" / DOMAIN / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert on_disk["version"] == "0.0.0"


def test_no_version_argument_leaves_the_committed_version_alone(
    packageable: pathlib.Path,
) -> None:
    """`workflow_dispatch` passes an empty string — D165's manual build."""
    assert run_package(packageable, "").returncode == 0

    with zipfile.ZipFile(packageable / "build" / "almanac.zip") as archive:
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
    assert manifest["version"] == "0.0.0"


# --- the refusal ----------------------------------------------------------


@pytest.mark.parametrize("bundle", [BUNDLE_PANEL, BUNDLE_CARD])
def test_a_missing_bundle_refuses_to_produce_an_archive(
    packageable: pathlib.Path, bundle: str
) -> None:
    """D163 — the one place where a missing frontend is still loud.

    Either entry is enough to fail on. The panel is the obvious one; the card is
    the one that would otherwise ship, because its 1.4 KB entry is easy to
    believe is a build artefact of no consequence.
    """
    (packageable / "custom_components" / DOMAIN / "frontend" / "dist" / bundle).unlink()

    result = run_package(packageable)

    assert result.returncode != 0
    assert bundle in result.stderr
    assert not (packageable / "build" / "almanac.zip").exists()


def test_a_rebuild_replaces_the_archive_rather_than_updating_it(
    packageable: pathlib.Path,
) -> None:
    """`zip` adds to an existing archive, which is why the script deletes first.

    Without the `rm -f`, a file removed from the package between two runs stays
    in the zip, and the one that matters is a renamed hashed chunk: D129 serves
    the whole directory, so a stale chunk is a served chunk.
    """
    assert run_package(packageable).returncode == 0
    assert "engine/plan.py" in names(packageable)

    (packageable / "custom_components" / DOMAIN / "engine" / "plan.py").unlink()
    assert run_package(packageable).returncode == 0

    assert "engine/plan.py" not in names(packageable)


# --- what must not drift --------------------------------------------------


def test_the_script_checks_for_the_bundles_the_constants_name() -> None:
    """Three languages hold one pair of filenames.

    `const.py` serves them, `rollup.config.mjs` emits them, and the script
    refuses without them. A rename in any one of the three is silent in the
    other two.
    """
    declared = re.search(r"^BUNDLES=\(([^)]*)\)", SCRIPT_TEXT, re.MULTILINE)
    assert declared is not None
    assert set(re.findall(r'"([^"]+)"', declared.group(1))) == {
        BUNDLE_PANEL,
        BUNDLE_CARD,
    }


def test_the_script_and_hacs_json_agree_on_the_asset_name() -> None:
    """HACS downloads by name — `repository_manifest.filename`, A.12.

    A release asset under any other name is a release HACS reports as available
    and cannot install, which is the failure this file exists for: the version
    appears in the UI and the download is what fails.
    """
    filename = json.loads(HACS.read_text(encoding="utf-8"))["filename"]
    assert filename == "almanac.zip"
    assert f'ZIP="$OUT/{filename}"' in SCRIPT_TEXT
    assert f"path: build/{filename}" in WORKFLOW_TEXT
    assert f"build/{filename} --clobber" in WORKFLOW_TEXT


def test_the_workflow_builds_before_it_packages() -> None:
    """Order, because D163 turns the wrong order into a failed release.

    Which is the right trade — but only if the build is actually in the
    workflow. A workflow that packaged a checkout without building would fail
    every release, loudly, forever.
    """
    # The `run:` forms, not the bare names: the header paragraph names the
    # script too, and it comes first.
    build = WORKFLOW_TEXT.index("run: npm run build")
    package = WORKFLOW_TEXT.index("run: bash scripts/package.sh")
    assert build < package


def test_the_workflow_passes_the_release_tag_to_the_packager() -> None:
    """The stamp is the tag, and nothing else knows the tag."""
    assert 'scripts/package.sh "${{ github.event.release.tag_name }}"' in WORKFLOW_TEXT


def test_the_workflow_can_be_run_by_hand() -> None:
    """D165 — D71's accepted risk, with a one-click recovery.

    The upload-artifact step is the half that makes a manual run worth having:
    without it the zip exists only inside a finished runner.
    """
    assert "workflow_dispatch:" in WORKFLOW_TEXT
    assert "actions/upload-artifact" in WORKFLOW_TEXT
    assert "if-no-files-found: error" in WORKFLOW_TEXT
