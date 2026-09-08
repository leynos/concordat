"""Independent property tests for Rust Makefile gate reachability.

The generated relation is deliberately smaller than the policy's input
language. It supplies only unconditional prerequisite edges and complete,
literal recursive-Make commands, then compares Conftest's result to an ordinary
Python breadth-first search. Shell grammar remains covered by adversarial Rego
fixtures because duplicating it here would only test two copies of the same
parser.
"""

from __future__ import annotations

import collections
import copy
import dataclasses
import json
import typing as typ
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from concordat.errors import OperationalRuleError
from concordat.rules import runner
from concordat.rules.rust_surfaces import resolve_rust_surfaces

if typ.TYPE_CHECKING:
    from concordat.rules.envelope import PolicyEnvelope
    from concordat.rules.makefile_facts import MakeRule

_REPOSITORY_ROOT = Path(__file__).parents[2]
_RULE_ID: typ.Final = "rust-makefile-baseline"
_ENVELOPE_FIXTURE: typ.Final = (
    _REPOSITORY_ROOT
    / "platform-standards"
    / "canon"
    / "lint-rules"
    / _RULE_ID
    / "fixtures"
    / "envelopes"
    / "compliant.json"
)
_LOCATION: typ.Final = {"start_line": 1}
type Edge = tuple[str, str]


@dataclasses.dataclass(frozen=True)
class ReachabilityCase:
    """Generated literal edge relations for one real Conftest evaluation.

    This test-only data shape is intentionally local to this policy package:
    it represents generated facts, not a reusable Makefile parser or product
    interface. Other policy packages should use their own independently
    specified fact domain.
    """

    prerequisite_edges: tuple[Edge, ...]
    recursive_edges: tuple[Edge, ...]
    gate_target: str
    target_order: tuple[str, ...]


def _reachability_case_strategy(node_count: int) -> st.SearchStrategy[ReachabilityCase]:
    """Build a bounded relation generator with both supported edge forms."""
    nodes = ("lint", *(f"stage{index}" for index in range(node_count - 1)))
    edge = st.tuples(st.sampled_from(nodes), st.sampled_from(nodes))
    return st.builds(
        ReachabilityCase,
        prerequisite_edges=st.lists(edge, min_size=1, max_size=4).map(tuple),
        recursive_edges=st.lists(edge, min_size=1, max_size=4).map(tuple),
        gate_target=st.sampled_from(nodes),
        target_order=st.permutations(nodes),
    )


_REACHABILITY_CASES = st.integers(min_value=2, max_value=4).flatmap(
    _reachability_case_strategy
)


def _fixture_envelope() -> PolicyEnvelope:
    """Return a fresh typed copy of the valid rule-package input envelope."""
    loaded = json.loads(_ENVELOPE_FIXTURE.read_text(encoding="utf-8"))
    return typ.cast("PolicyEnvelope", loaded)


def _recipe(text: str) -> dict[str, object]:
    """Build one unconditional recipe fact from a known literal command."""
    return {
        "text": text,
        "ignore_errors": False,
        "location": _LOCATION,
    }


def _rule(
    target: str,
    prerequisites: list[str],
    recipes: list[dict[str, object]],
) -> dict[str, object]:
    """Build one unconditional parsed Make rule for the synthetic envelope."""
    return {
        "targets": [target],
        "prerequisites": prerequisites,
        "conditions": [],
        "recipes": recipes,
        "location": _LOCATION,
        "double_colon": False,
    }


def _children_by_source(edges: tuple[Edge, ...]) -> dict[str, list[str]]:
    """Group generated literal recursive commands by their parent target."""
    children: collections.defaultdict[str, list[str]] = collections.defaultdict(list)
    for source, destination in edges:
        children[source].append(destination)
    return children


def _synthetic_envelope(
    *,
    prerequisite_edges: tuple[Edge, ...],
    recursive_edges: tuple[Edge, ...],
    gate_target: str,
    target_order: tuple[str, ...],
) -> PolicyEnvelope:
    """Build valid Make facts without reproducing the policy's parser."""
    envelope = copy.deepcopy(_fixture_envelope())
    makefile = envelope["makefile"]
    assert makefile is not None, "the compliant fixture must carry Make facts"
    prerequisites = _children_by_source(prerequisite_edges)
    recursive_children = _children_by_source(recursive_edges)
    rules = [
        _rule("build", [], [_recipe("cargo build")]),
        _rule("test", [], [_recipe("cargo test")]),
    ]
    for target in target_order:
        recipes: list[dict[str, object]] = []
        if target == gate_target:
            recipes.append(_recipe("$(WHITAKER) --all"))
        children = recursive_children.get(target, [])
        if children:
            commands = " && ".join(f"$(MAKE) {child}" for child in children)
            recipes.append(_recipe(commands))
        if target == "lint" and not recipes:
            recipes.append(_recipe("cargo clippy --all-targets"))
        rules.append(_rule(target, prerequisites.get(target, []), recipes))
    makefile["rules"] = typ.cast("list[MakeRule]", rules)
    return envelope


def _qg_verdicts(envelope: PolicyEnvelope) -> set[str]:
    """Return QG-001 verdicts from a real Conftest policy invocation."""
    results = runner._invoke_conftest(_RULE_ID, envelope)
    return {
        finding.verdict
        for finding in runner._findings_from_results(results)
        if finding.rule_id == "QG-001"
    }


def _reachable_targets(case: ReachabilityCase) -> set[str]:
    """Traverse the generated graph independently of the Rego implementation."""
    adjacency: dict[str, set[str]] = collections.defaultdict(set)
    for source, destination in case.prerequisite_edges + case.recursive_edges:
        adjacency[source].add(destination)
    reached = {"lint"}
    pending = collections.deque(["lint"])
    while pending:
        source = pending.popleft()
        for destination in adjacency[source]:
            if destination not in reached:
                reached.add(destination)
                pending.append(destination)
    return reached


def _case_envelope(case: ReachabilityCase) -> PolicyEnvelope:
    """Convert one generated relation into the rule package's input shape."""
    return _synthetic_envelope(
        prerequisite_edges=case.prerequisite_edges,
        recursive_edges=case.recursive_edges,
        gate_target=case.gate_target,
        target_order=case.target_order,
    )


def test_real_conftest_reports_an_unreachable_gate() -> None:
    """Prove the real runner emits the QG-001 output shape used by the property."""
    envelope = _synthetic_envelope(
        prerequisite_edges=(("lint", "prepare"),),
        recursive_edges=(("prepare", "stage"),),
        gate_target="isolated",
        target_order=("isolated", "lint", "prepare", "stage"),
    )

    assert _qg_verdicts(envelope) == {"noncompliant"}


def test_real_conftest_accepts_mixed_cycle_and_multiple_recursions() -> None:
    """Specify a reachable gate through both edge forms and a cycle."""
    case = ReachabilityCase(
        prerequisite_edges=(("lint", "prepare"), ("cycle", "prepare")),
        recursive_edges=(("prepare", "cycle"), ("prepare", "gate")),
        gate_target="gate",
        target_order=("gate", "cycle", "lint", "prepare"),
    )

    assert case.gate_target in _reachable_targets(case), case
    assert _qg_verdicts(_case_envelope(case)) == set()


@settings(max_examples=12, deadline=None)
@given(_REACHABILITY_CASES)
def test_policy_matches_the_independent_generated_graph(case: ReachabilityCase) -> None:
    """Conftest agrees with the generated prerequisite and recursion relation."""
    expected_gate_is_reachable = case.gate_target in _reachable_targets(case)
    actual_verdicts = _qg_verdicts(_case_envelope(case))
    expected_verdicts = set() if expected_gate_is_reachable else {"noncompliant"}

    assert actual_verdicts == expected_verdicts, (
        f"case={case!r}, expected={expected_verdicts}, actual={actual_verdicts}"
    )


@settings(max_examples=12, deadline=None)
@given(st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=12))
def test_declared_surface_path_is_emitted_canonically(segment: str) -> None:
    """Equivalent dot-segment declarations emit one canonical surface identity."""
    with TemporaryDirectory(prefix="concordat-surface-property-") as temporary_root:
        checkout = Path(temporary_root)
        cargo_path = checkout / "rust" / segment / "Cargo.toml"
        cargo_path.parent.mkdir(parents=True)
        cargo_path.write_text(
            '[package]\nname = "property-fixture"\nversion = "0.1.0"\n',
            encoding="utf-8",
        )
        (checkout / ".concordat").write_text(
            "language:\n"
            "  rust:\n"
            "    surfaces:\n"
            f"      - path: rust/./{segment}/Cargo.toml\n",
            encoding="utf-8",
        )

        resolution = resolve_rust_surfaces(checkout)

    assert resolution.declared, resolution
    assert [surface["path"] for surface in resolution.surfaces] == [
        f"rust/{segment}/Cargo.toml"
    ], resolution


def _unexpected_surface_resolution(
    path: Path,
    *,
    strict: bool = False,
) -> Path:
    """Fail if an invalid declaration reaches a Cargo filesystem boundary."""
    message = f"unexpected filesystem resolution for {path}, strict={strict}"
    raise AssertionError(message)


@settings(max_examples=12, deadline=None)
@given(st.sampled_from((*range(32), 127)))
def test_control_character_declarations_stop_before_filesystem_resolution(
    code_point: int,
) -> None:
    """Every generated ASCII control character is rejected before path resolution."""
    with TemporaryDirectory(prefix="concordat-control-property-") as temporary_root:
        checkout = Path(temporary_root)
        manifest_path = checkout / ".concordat"
        manifest_path.write_text(
            "language:\n"
            "  rust:\n"
            "    surfaces:\n"
            f'      - path: "rust/Cargo\\x{code_point:02x}.toml"\n',
            encoding="utf-8",
        )
        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(Path, "resolve", _unexpected_surface_resolution)
            with pytest.raises(OperationalRuleError, match="control characters"):
                resolve_rust_surfaces(checkout)
