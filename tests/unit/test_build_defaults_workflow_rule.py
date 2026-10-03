"""End-to-end tests of the BD-007 and BD-009 workflow audit through `run_rule`.

Each case runs the public `runner.run_rule` over a fixture checkout, so the
workflow files are discovered and decoded by the real envelope builder and
judged by the real policy, rather than checked as separate halves. The Rego
suite pins the exact messages; these tests pin that the halves meet.
"""

from __future__ import annotations

import pathlib
import typing as typ

import pytest

from concordat.rules import runner

_REPOS: typ.Final = (
    pathlib.Path(__file__).resolve().parents[2]
    / "platform-standards/canon/lint-rules/rust-build-defaults/fixtures/repos"
)
_RULE_ID: typ.Final = "rust-build-defaults"


def _verdicts(fixture: str) -> tuple[str, set[tuple[str, str]]]:
    """Return the overall verdict and the (rule, verdict) pairs for *fixture*."""
    result = runner.run_rule(_RULE_ID, _REPOS / fixture)
    pairs = {(finding.rule_id, finding.verdict) for finding in result.findings}
    return result.verdict, pairs


@pytest.mark.parametrize(
    ("fixture", "verdict", "findings"),
    [
        pytest.param("coverage-workflow-action", "compliant", set(), id="bd007-pass"),
        pytest.param(
            "coverage-workflow-run",
            "noncompliant",
            {("BD-007", "noncompliant"), ("BD-007", "indeterminate")},
            id="bd007-fail",
        ),
        pytest.param(
            "workflow-setup-rust",
            "noncompliant",
            {("BD-009", "noncompliant")},
            id="bd009-setup-action-default",
        ),
        pytest.param(
            "workflow-rustflags-env",
            "noncompliant",
            {("BD-009", "noncompliant"), ("BD-009", "indeterminate")},
            id="bd009-step-env",
        ),
        pytest.param(
            "workflow-undecodable",
            "indeterminate",
            {("BD-009", "indeterminate")},
            id="undecodable-workflow",
        ),
        pytest.param(
            "printed-cargo",
            "noncompliant",
            {("BD-009", "noncompliant")},
            id="printed-text",
        ),
    ],
)
def test_a_workflow_checkout_is_judged_end_to_end(
    fixture: str, verdict: str, findings: set[tuple[str, str]]
) -> None:
    """The workflow files a checkout carries reach the policy and decide it.

    Covers a passing and a failing BD-007 workflow, a BD-009 step whose
    `RUSTFLAGS` come from the setup action's default and from the step's own
    `env`, and a workflow that does not decode.
    """
    actual_verdict, actual = _verdicts(fixture)
    assert actual_verdict == verdict, (actual_verdict, actual)
    assert {pair for pair in actual if pair[0] in {"BD-007", "BD-009"}} == findings, (
        actual
    )
