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
# reader — the top-level scheduler tick — and as of build step 5 it exists, so the
# allowed set names it. That is the point of keeping the list rather than deleting
# the check: "who reads the clock" is a question with a written answer, and adding
# a second name to this list is a change a reviewer has to look at.
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

# `tick.py` is D64's one permitted reader: it samples the clock and threads `now`
# down, which is what makes every layer beneath it a pure function of the instant
# and the dry run the same code path as the live engine. Nothing else goes here
# without a decision number.
#
# The allow-list names *functions*, not the file, because excusing the whole file
# would retire the check exactly where it now matters most. D64's substance is not
# "the tick may read a clock" but "the clock is sampled once, at an entry point,
# and threaded down" — a sixth reader inside `async_tick`'s own call tree would
# satisfy a file-level exemption while breaking the thing the constraint is for.
# Every name below is a callback or a service handler whose whole body is: sample
# the instant, hand it to `async_tick`. The timer wake is deliberately absent — it
# uses the instant the timer fired for, so it has nothing to sample.
_CLOCK_SAMPLERS: dict[str, frozenset[str]] = {
    "tick.py": frozenset(
        {
            "_async_started",
            "_async_settle_changed",
            "_async_collection_changed",
            "async_refresh",
            "async_run_now",
        }
    )
}


def _enclosing_function(tree: ast.Module, node: ast.AST) -> str:
    """The name of the innermost function containing `node`, or `"<module>"`."""
    functions = [
        item
        for item in ast.walk(tree)
        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
        and item.lineno <= node.lineno <= (item.end_lineno or item.lineno)
    ]
    if not functions:
        return "<module>"
    return min(functions, key=lambda f: (f.end_lineno or f.lineno) - f.lineno).name


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
    permitted = _CLOCK_SAMPLERS.get(path.name, frozenset())
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders = [
        f"{node.func.value.id}.{node.func.attr}"
        f" in {_enclosing_function(tree, node)} (line {node.lineno})"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _CLOCK_READERS
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in _CLOCK_OWNERS
        and _enclosing_function(tree, node) not in permitted
    ]
    assert not offenders, f"{path.name} reads the clock: {', '.join(offenders)}"


def test_the_tick_is_the_only_file_with_a_clock_exemption() -> None:
    """The allow-list itself is the thing a reviewer has to look at (D64).

    Asserted rather than left to inspection because the failure mode is a quiet
    one: a second entry here would pass every other test in the suite while
    making the dry run and the engine two different implementations, which is
    the outcome D64 exists to prevent.
    """
    assert set(_CLOCK_SAMPLERS) == {"tick.py"}


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
