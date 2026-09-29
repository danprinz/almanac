"""Constraints that hold over the whole package, checked over the whole package.

Both decisions here are ones DESIGN.md calls impossible to retrofit, which is a
claim about what a *later* commit must not do. A test that only exercises the
code written today cannot make that claim; these read the source instead.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).parent.parent / "custom_components" / "almanac"

# Everything that would hand a caller the wall clock. D64 allows exactly one
# reader — the top-level scheduler tick — and it does not exist yet, so the
# allowed set is empty. When the tick lands it goes on this list by name, which
# makes "who reads the clock" a question with a written answer.
_CLOCK_READERS = frozenset(
    {
        "now",
        "utcnow",
        "today",
        "monotonic",
        "time",
        "perf_counter",
        "start_of_local_day",
    }
)
_CLOCK_OWNERS = frozenset({"dt_util", "datetime", "date", "time", "dt"})

_FILES_ALLOWED_TO_READ_THE_CLOCK: frozenset[str] = frozenset()


def _sources() -> list[Path]:
    return sorted(PACKAGE.rglob("*.py"))


def test_there_are_sources_to_check() -> None:
    """Guard against a path typo silently making the checks below vacuous."""
    assert len(_sources()) >= 7


@pytest.mark.parametrize("path", _sources(), ids=lambda p: p.name)
def test_nothing_reads_a_clock(path: Path) -> None:
    """D64 — `now` is a parameter, not an ambient value.

    This is the precondition for the dry run the brief commits to: "show me this
    Friday without waiting for Friday" is the pipeline evaluated at a
    hypothetical `now`. If any layer reads the wall clock internally, the dry
    run and the timeline become a second implementation of the engine, and a
    timeline that disagrees with what fires is worse than no timeline.

    Threading an injected instant through afterwards is a rewrite rather than a
    refactor, which is why this is checked from the first commit and not from
    the first engine.
    """
    if path.name in _FILES_ALLOWED_TO_READ_THE_CLOCK:
        pytest.skip(f"{path.name} is the tick, and the tick is allowed one clock")

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders = [
        f"{node.func.value.id}.{node.func.attr} (line {node.lineno})"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _CLOCK_READERS
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in _CLOCK_OWNERS
    ]
    assert not offenders, f"{path.name} reads the clock: {', '.join(offenders)}"


def test_object_id_is_settled_in_exactly_one_place() -> None:
    """D66 — the slug is never re-derived, and that is structural, not careful.

    `suggested_object_id` is the only way to turn a name into a slug, and it is
    called from one place: the collection's create path. Anywhere else would be
    a second opportunity for a rename to move an entity_id, which would break
    every automation referencing the old one — later, and without warning.
    """
    callers = [
        path.name
        for path in _sources()
        if "suggested_object_id(" in path.read_text(encoding="utf-8")
        and path.name != "schema.py"
    ]
    assert callers == ["storage.py"]


def test_the_update_schema_has_no_object_id() -> None:
    """The same decision, seen from the schema rather than from the call sites."""
    from custom_components.almanac.schema import CREATE_FIELDS, UPDATE_FIELDS

    assert "object_id" in {str(key) for key in CREATE_FIELDS}
    assert "object_id" not in {str(key) for key in UPDATE_FIELDS}
