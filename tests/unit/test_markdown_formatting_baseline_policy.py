"""Real-Conftest checks of the markdown-formatting-baseline rule package.

The Rego suite under the package's `policy/` pins each fixture's finding
profile; this module proves the same profiles through the production runner,
so the manifest's declared parameters, the policy namespace, and the finding
metadata the runner decodes are all exercised together. It needs `conftest`
on PATH, as the rule itself does.
"""

from __future__ import annotations

import json
import pathlib
import typing as typ

import pytest

from concordat.rules import runner

_RULE_ID: typ.Final = "markdown-formatting-baseline"
_ENVELOPES_DIR: typ.Final = (
    pathlib.Path(__file__).resolve().parents[2]
    / "platform-standards"
    / "canon"
    / "lint-rules"
    / _RULE_ID
    / "fixtures"
    / "envelopes"
)

type Profile = frozenset[tuple[str, str]]

_NONCOMPLIANT_RECIPES: typ.Final[Profile] = frozenset({
    ("PD-002", "noncompliant"),
    ("PD-003", "noncompliant"),
    ("PD-004", "noncompliant"),
})
_INDETERMINATE_RECIPES: typ.Final[Profile] = frozenset({
    ("PD-002", "indeterminate"),
    ("PD-003", "indeterminate"),
    ("PD-004", "indeterminate"),
})

# Every fixture envelope and the exact (rule, verdict) profile it must yield.
EXPECTED_PROFILES: typ.Final[dict[str, Profile]] = {
    "compliant": frozenset(),
    "literal_tools": frozenset(),
    "delegated": frozenset(),
    "probe_nested": frozenset(),
    "config_extended": frozenset(),
    "no_markdown": frozenset(),
    "no_makefile": frozenset({("FP-003", "noncompliant")}),
    "missing_targets": frozenset({("FP-003", "noncompliant")}),
    "mdformat_wrapper": _NONCOMPLIANT_RECIPES,
    "missing_flags": _NONCOMPLIANT_RECIPES,
    "soft_skip": _NONCOMPLIANT_RECIPES,
    "echo_decoy": _NONCOMPLIANT_RECIPES,
    "mode_swapped": frozenset({("PD-002", "noncompliant"), ("PD-003", "noncompliant")}),
    "conditional": frozenset({
        ("PD-003", "indeterminate"),
        ("PD-004", "indeterminate"),
    }),
    "with_include": _INDETERMINATE_RECIPES,
    "recovered": _INDETERMINATE_RECIPES,
    "undefined_variable": _INDETERMINATE_RECIPES,
    "ambiguous_variable": frozenset({
        ("PD-002", "indeterminate"),
        ("PD-003", "indeterminate"),
    }),
    "config_missing": frozenset({("PD-005", "noncompliant")}),
    "config_alternate": frozenset({("PD-005", "noncompliant")}),
    "config_drifted": frozenset({("PD-005", "noncompliant")}),
    "config_malformed": frozenset({("PD-005", "indeterminate")}),
    "workflow_shell_lint": frozenset({("PD-006", "noncompliant")}),
    "workflow_floating_tag": frozenset({("PD-006", "noncompliant")}),
    "workflow_narrow_globs": frozenset({("PD-006", "noncompliant")}),
    "workflow_none": frozenset({("PD-006", "noncompliant")}),
    "workflow_absent": frozenset({("PD-006", "noncompliant")}),
    "workflow_mixed": frozenset({("PD-006", "noncompliant")}),
    "workflow_reusable_only": frozenset({("PD-006", "indeterminate")}),
    "workflow_malformed": frozenset({("PD-006", "indeterminate")}),
}


def _load_envelope(name: str) -> runner.PolicyInput:
    """Return one checked-in fixture envelope."""
    loaded = json.loads((_ENVELOPES_DIR / f"{name}.json").read_text(encoding="utf-8"))
    return typ.cast("runner.PolicyInput", loaded)


def test_expectation_table_covers_every_fixture() -> None:
    """A fixture without an expected profile is a behaviour nobody verifies."""
    recorded = {path.stem for path in _ENVELOPES_DIR.glob("*.json")}
    assert recorded == set(EXPECTED_PROFILES), recorded ^ set(EXPECTED_PROFILES)


@pytest.mark.parametrize("name", sorted(EXPECTED_PROFILES))
def test_runner_reports_the_expected_profile(name: str) -> None:
    """The production runner decodes exactly the pinned finding profile."""
    results = runner._invoke_conftest(_RULE_ID, _load_envelope(name))
    findings = runner._findings_from_results(results)
    profile = frozenset((f.rule_id, f.verdict) for f in findings)
    assert profile == EXPECTED_PROFILES[name], findings


def test_soft_skip_findings_cite_recipe_lines() -> None:
    """Recipe-level findings carry the Makefile line of the offending recipe."""
    results = runner._invoke_conftest(_RULE_ID, _load_envelope("soft_skip"))
    findings = runner._findings_from_results(results)
    lines = {f.rule_id: f.line for f in findings}
    assert lines == {"PD-003": 7, "PD-004": 8, "PD-002": 11}, findings
    assert all(f.path == "Makefile" for f in findings), findings
