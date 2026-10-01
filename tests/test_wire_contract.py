"""The wire form, in two languages — `as_dict()` against `wire.ts`.

`custom_components/almanac/frontend/src/wire.ts` is written out by hand, one
interface per `as_dict()`, and its header says this test is the reason. Here is
that reason in full.

The websocket boundary is the one place in the project where a rename is silent.
Python has no idea what the panel reads; `npm run typecheck` has no idea what
Python emits; and a key that disappears does not raise anywhere — it reads as
`undefined`, which in a timeline renders as a missing row, an empty label, or a
`null` where an instant should be. The first notice would be a screenshot.

So the test reads both sides as *structure* rather than as behaviour. The Python
side is an AST sweep for the string keys of every dict an `as_dict()` returns,
which is why those methods end in a dict display and not in a comprehension or a
`dict(...)` call: an unreadable body fails this test rather than skipping it. The
TypeScript side is a parse of the interface bodies. Neither needs the other's
toolchain to run, which matters because the Python suite is the only thing CI is
guaranteed to run.

Two things this deliberately does **not** check. It does not check types, only
names — `wire.ts` carries the `T | null` convention and the ISO-8601 instants,
and nothing short of generating one side from the other would verify them. And
it does not check the *enum members*, because `str(SomeEnum.MEMBER)` is a runtime
fact; `tests/test_observability.py` and the resolver tests already pin those
spellings against the payloads they appear in.

Related: `tests/test_design_constraints.py` enforces D64 the same way, by reading
the source rather than running it. Both exist because the constraint they protect
is invisible to every other check in the project.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

from custom_components.almanac.const import DOMAIN

PKG = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / DOMAIN
WIRE_TS = PKG / "frontend" / "src" / "wire.ts"

# Each Python producer against the interface that mirrors it. The left column is
# a class with an `as_dict()`, or -- for the two envelopes that are assembled by
# hand rather than by a dataclass -- a module-level function in `events.py` or a
# websocket handler.
PAIRS = {
    "Timeline": "WireTimeline",
    "ScheduleTimeline": "WireScheduleTimeline",
    "PastOccurrence": "WirePastOccurrence",
    "Plan": "WirePlan",
    "Occurrence": "WireOccurrence",
    "Transition": "WireTransition",
    "Reconciliation": "WireReconciliation",
    "EngineState": "WireEngineState",
    "HeldInterval": "WireHeldInterval",
    "AtRecord": "WireAtRecord",
    "PendingAt": "WirePendingAt",
    "ActionResult": "WireActionResult",
    "websocket_dry_run": "WireDryRun",
}

# The nested dicts, which have no class of their own: `(producer, key)` against
# the interface for the value at that key.
NESTED = {
    ("Plan", "window"): "WireWindow",
    ("Plan", "problem"): "WireProblem",
    ("Transition", "conditions"): "WireConditionVerdict",
}

# `as_dict()` methods that never reach a websocket result. Listed rather than
# ignored: a new one appearing here is a question -- is this on the wire now? --
# and an empty list of unpaired producers is not the same answer as a list that
# somebody looked at.
#
# `ExecutionReport` is the odd one. It is not paired because it does not appear
# on the wire as itself: `execution_payload` spreads it into the past-occurrence
# row, so its keys are checked as part of `WirePastOccurrence`.
OFF_WIRE = {
    "CompletionState",  # D47's per-schedule completion, stored not sent
    "StateSnapshot",  # D29's restore stack, stored not sent
    "ExecutionReport",  # spread into `execution_payload`
    "_ExitPromise",  # tick-internal
}

# `**` spreads the AST cannot follow, each against the producer whose keys it
# contributes. The text is `ast.unparse` of the spread expression, so this table
# is also the list of joins the wire form makes -- there are three, and every one
# of them is a place where two events are merged into one row.
SPREADS = {
    "self.occurrence": "occurrence_payload",
    "self.execution or {}": "execution_payload",
    "report.as_dict()": "ExecutionReport",
    "reconciliation.as_dict()": "Reconciliation",
}


def attr_constants() -> dict[str, str]:
    """The named keys the payloads use, resolved to their values.

    `occurrence_payload` writes `ATTR_SCHEDULE_ID` rather than `"schedule_id"`,
    which is right -- the same constants name the event fields the recorder
    stores -- and it means a key sweep has to resolve them. Some of them are
    ours and some are core's (`ATTR_NAME`, `ATTR_ENTITY_ID`), so both modules
    are consulted, ours first.

    Resolved by *importing* rather than by parsing, which is the one place in
    this module that reads a value instead of a structure. Appendix B is why
    that distinction matters: a rendered read of third-party source is not
    trustworthy for a verbatim identifier, and an attribute on an imported
    module is not a rendered read.
    """
    import homeassistant.const as core_const

    from custom_components.almanac import const as almanac_const

    found: dict[str, str] = {}
    for module in (core_const, almanac_const):
        for name in dir(module):
            value = getattr(module, name)
            if name.isupper() and isinstance(value, str):
                found[name] = value
    return found


CONSTANTS = attr_constants()


def unwrap(node: ast.expr) -> ast.expr:
    """Look through a conditional to the dict literal inside it.

    `"problem": {...} if self.problem is not None else None` is the shape every
    optional nested dict has, and the keys are in the branch that is a dict.
    """
    while isinstance(node, ast.IfExp):
        if isinstance(node.body, ast.Dict):
            return node.body
        node = node.orelse
    return node


def dict_literal(node: ast.AST) -> ast.Dict:
    """The dict display a producer returns, or a readable failure.

    Not a fallback: a producer whose body this cannot read is a producer whose
    keys nothing checks, which is the failure this whole module exists to catch.
    """
    found = unwrap(node)  # type: ignore[arg-type]
    assert isinstance(found, ast.Dict), (
        f"expected a dict display, got {type(found).__name__}: "
        f"{ast.unparse(node)[:120]}"
    )
    return found


def producers() -> dict[str, ast.Dict]:
    """Every dict a class's `as_dict()` or a payload function returns.

    Keyed by class name for methods and by function name for the module-level
    builders, in one namespace because nothing in the package collides and
    `SPREADS` has to be able to name either kind.
    """
    found: dict[str, ast.Dict] = {}
    for path in sorted(PKG.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) and item.name == "as_dict":
                        returned = _returned(item)
                        if returned is not None:
                            found[node.name] = dict_literal(returned)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                returned = _returned(node) if node.name.endswith("_payload") else None
                if returned is not None:
                    found[node.name] = dict_literal(returned)

    found["websocket_dry_run"] = _sent_result()
    return found


def _returned(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.expr | None:
    """The first returned expression that is not a bare `return`."""
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and node.value is not None:
            return node.value
    return None


def _sent_result() -> ast.Dict:
    """The dry run's envelope, which is assembled in the handler.

    D120's dry run is one evaluation at one instant and has no dataclass of its
    own -- it is a `Reconciliation` per schedule plus the two things the request
    asked for. So the envelope is read where it is written, out of the
    `send_result` call.
    """
    tree = ast.parse((PKG / "websocket.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        if node.name != "websocket_dry_run":
            continue
        for call in ast.walk(node):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "send_result"
            ):
                return dict_literal(call.args[1])
    pytest.fail("websocket_dry_run no longer sends a dict literal")


def keys_of(name: str, registry: dict[str, ast.Dict]) -> set[str]:
    """Every key one producer emits, following the spreads in `SPREADS`."""
    assert name in registry, f"no producer named {name}"
    found: set[str] = set()
    for key, value in zip(registry[name].keys, registry[name].values):
        if key is None:
            source = ast.unparse(value)
            assert source in SPREADS, (
                f"{name} spreads `{source}`, which SPREADS does not name. "
                "Add it, or the keys it contributes go unchecked."
            )
            found |= keys_of(SPREADS[source], registry)
        elif isinstance(key, ast.Constant) and isinstance(key.value, str):
            found.add(key.value)
        elif isinstance(key, ast.Name) and key.id in CONSTANTS:
            found.add(CONSTANTS[key.id])
        else:
            pytest.fail(f"{name} has an unreadable key: {ast.unparse(key)}")
    return found


def nested_keys(name: str, key: str, registry: dict[str, ast.Dict]) -> set[str]:
    """The keys of the dict a producer puts at one of its own keys."""
    for node_key, value in zip(registry[name].keys, registry[name].values):
        literal = isinstance(node_key, ast.Constant) and node_key.value == key
        named = isinstance(node_key, ast.Name) and CONSTANTS.get(node_key.id) == key
        if literal or named:
            inner = dict_literal(value)
            return {
                k.value
                for k in inner.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
    pytest.fail(f"{name} no longer emits `{key}`")


def interfaces() -> dict[str, set[str]]:
    """Field names per `export interface` in `wire.ts`.

    Two spaces of indentation is the whole parser, which is enough because the
    file is hand-written to one shape and `prettier` is not in the toolchain to
    change it. A nested object literal inside an interface would break this, and
    that is the intended pressure: a nested shape gets its own interface.
    """
    source = WIRE_TS.read_text(encoding="utf-8")
    found: dict[str, set[str]] = {}
    for match in re.finditer(r"export interface (\w+) \{(.*?)\n\}", source, re.S):
        found[match.group(1)] = set(
            re.findall(r"^ {2}(\w+)\??:", match.group(2), re.M)
        )
    assert found, "the interface parser stopped matching"
    return found


@pytest.fixture(scope="module")
def registry() -> dict[str, ast.Dict]:
    return producers()


@pytest.fixture(scope="module")
def wire() -> dict[str, set[str]]:
    return interfaces()


@pytest.mark.parametrize(("producer", "interface"), sorted(PAIRS.items()))
def test_every_payload_matches_its_interface(
    producer: str,
    interface: str,
    registry: dict[str, ast.Dict],
    wire: dict[str, set[str]],
) -> None:
    """Both directions, because the two failures are different.

    A key Python emits and TypeScript does not declare is a feature the panel
    cannot use. A field TypeScript declares and Python does not emit is worse:
    it type-checks, it reads `undefined`, and D13's whole argument is that a
    frontend which quietly treats missing as false is the defect.
    """
    assert interface in wire, f"{interface} is gone from wire.ts"
    assert keys_of(producer, registry) == wire[interface]


@pytest.mark.parametrize(("where", "interface"), sorted(NESTED.items()))
def test_every_nested_shape_matches_its_interface(
    where: tuple[str, str],
    interface: str,
    registry: dict[str, ast.Dict],
    wire: dict[str, set[str]],
) -> None:
    """The three dicts that are built inline rather than by a dataclass.

    `window` and `problem` recur -- `Occurrence` and `Plan` both emit a problem,
    in the same shape -- so one interface describes several producers and the
    table names the one that is checked. If they ever diverge this test will not
    notice, which is the argument for them not diverging: the frontend has one
    renderer for a problem.
    """
    producer, key = where
    assert nested_keys(producer, key, registry) == wire[interface]


def test_every_payload_is_either_paired_or_declared_off_the_wire(
    registry: dict[str, ast.Dict],
) -> None:
    """A new `as_dict()` has to be classified, not merely added.

    The payload functions are excluded because they are reached through
    `SPREADS` rather than on their own, and the recorder -- not the websocket --
    is their first consumer.
    """
    classified = set(PAIRS) | OFF_WIRE | {
        name for name in registry if name.endswith("_payload")
    }
    assert set(registry) - classified == set()


def test_the_interfaces_are_all_used(wire: dict[str, set[str]]) -> None:
    """No interface describes a shape nothing sends.

    `WireDryRunSchedule` is a type alias rather than an interface -- it is a
    `Reconciliation` plus the schedule id the envelope's list adds -- so it is
    not in `wire` and is checked by the `WireDryRun` pairing instead.
    """
    paired = set(PAIRS.values()) | set(NESTED.values())
    assert set(wire) - paired == set()


def test_nothing_on_the_wire_is_optional() -> None:
    """`wire.ts`'s first shape rule, and it is a rule about Python's behaviour.

    Every `as_dict()` emits every key, using `null` for absence rather than
    omitting the key -- the one exception is `PastOccurrence`, whose two halves
    are two separate recorded events. So the TypeScript is `T | null`, never
    `T?`, and a `?` sneaking in would let `strictNullChecks` stop asking the
    question the convention exists to force.
    """
    source = WIRE_TS.read_text(encoding="utf-8")
    optional = re.findall(r"^ {2}(\w+)\?:.*$", source, re.M)

    past = interfaces()["WirePastOccurrence"]
    assert set(optional) <= past, [field for field in optional if field not in past]


def test_every_instant_is_declared_as_an_instant() -> None:
    """`wire.ts`'s second shape rule.

    D64 pushes every clock read to the top of the tick, which makes the frontend
    the thing that supplies instants -- and A.13 makes an instant that lost its
    offset a wall-clock comparison that is wrong by an hour twice a year. The
    `Instant` and `Day` aliases are how the file keeps saying which is which, so
    a raw `string` on a field whose name ends in a time word is a field that
    stopped saying it.
    """
    source = WIRE_TS.read_text(encoding="utf-8")
    raw: list[str] = []
    for field, declared in re.findall(r"^ {2}(\w+)\??: ([^;]+);", source, re.M):
        if field in {"start_date"}:
            continue
        timeish = field in {"at", "start", "end", "deadline"} or field.endswith(
            ("_at", "_through")
        )
        if timeish and "Instant" not in declared and "Day" not in declared:
            raw.append(f"{field}: {declared}")
    assert raw == []


def test_the_day_alias_is_only_used_for_civil_dates() -> None:
    """D10's split, which is the one the two aliases exist to keep visible.

    A day set answers in civil dates and an anchor answers in instants, and
    `start_date` is the occurrence's identity -- the civil day its rule fired
    for, not a time. A.11 is why that cannot be an instant: core's all-day
    events are built with zero extent and lose the evening-to-evening boundary,
    and this model does not mirror that encoding.
    """
    source = WIRE_TS.read_text(encoding="utf-8")
    days = re.findall(r"^ {2}(\w+)\??: Day[;,\s]", source, re.M)
    assert set(days) == {"start_date"}
