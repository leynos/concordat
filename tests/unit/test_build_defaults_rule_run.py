"""End-to-end coverage for `concordat artefact rule run rust-build-defaults`.

Everything else about this rule is tested one layer at a time: the readers
against written files, the policy against recorded envelopes. Neither proves
the two halves meet. These tests drive the public boundary with a real
Conftest over the shipped fixture checkouts, so a fact the reader renames and
the policy still asks for by its old name fails here rather than in the field.

`conftest` is a prerequisite of `make test`, as `makeutil` already was.
"""

from __future__ import annotations

import pathlib
import typing as typ

import pytest
from ruamel.yaml import YAML

from concordat.rules import runner
from concordat.rules.runner import (
    VERDICT_COMPLIANT,
    VERDICT_INDETERMINATE,
    VERDICT_NONCOMPLIANT,
    run_rule,
)

RULE_ID: typ.Final = "rust-build-defaults"
RULE_DIR: typ.Final = (
    pathlib.Path(__file__).resolve().parents[2]
    / "platform-standards"
    / "canon"
    / "lint-rules"
    / RULE_ID
)
REPOS_DIR: typ.Final = RULE_DIR / "fixtures" / "repos"


def checkout(name: str) -> pathlib.Path:
    """Return the shipped fixture checkout called *name*."""
    path = REPOS_DIR / name
    if not path.is_dir():
        message = f"no fixture checkout named {name!r} under {REPOS_DIR}"
        raise AssertionError(message)
    return path


@pytest.mark.parametrize(
    ("fixture", "verdict", "expected"),
    [
        pytest.param("compliant-exception", VERDICT_COMPLIANT, set(), id="exception"),
        pytest.param("compliant-cranelift", VERDICT_COMPLIANT, set(), id="backend"),
        pytest.param(
            "no-config",
            VERDICT_NONCOMPLIANT,
            {"BD-001", "BD-002", "BD-004"},
            id="no-configuration",
        ),
        pytest.param(
            "stale-exception", VERDICT_NONCOMPLIANT, {"BD-006"}, id="stale-exception"
        ),
        pytest.param(
            "unclassified-only", VERDICT_INDETERMINATE, {"BD-002"}, id="indeterminate"
        ),
    ],
)
def test_the_rule_runs_against_a_real_checkout(
    fixture: str, verdict: str, expected: set[str]
) -> None:
    """The reader and the policy agree about a checkout on disk."""
    result = run_rule(RULE_ID, checkout(fixture))
    assert result.verdict == verdict, (
        f"{fixture} should be {verdict}, got {result.verdict}: {result.findings}"
    )
    reported = {finding.rule_id for finding in result.findings}
    assert reported == expected, (
        f"{fixture} should report {expected or 'no findings'}, got {reported}"
    )


def test_the_compliant_exit_code_is_zero() -> None:
    """The command's exit status is the verdict, which is what gates a lane."""
    result = run_rule(RULE_ID, checkout("compliant-exception"))
    assert result.exit_code == 0, "a compliant checkout exits zero"


def test_an_indeterminate_verdict_does_not_exit_zero() -> None:
    """Failing closed is only a gate if the status says so."""
    result = run_rule(RULE_ID, checkout("unclassified-only"))
    assert result.exit_code == 1, "an indeterminate verdict must not pass a gate"


def test_the_manifest_defaults_reach_the_envelope_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A declared default that never arrives is a default in name only.

    The exception-document list and keyword are read while the envelope is
    built, not by the policy, so the manifest is their only source. If the
    resolver passed nothing, the builder's own fallbacks would silently stand
    in and the manifest could be changed with no effect at all.
    """
    captured: dict[str, object] = {}

    def record(
        checkout_path: pathlib.Path, parameters: object = None
    ) -> runner.RuleEnvelope:
        captured["parameters"] = parameters
        return typ.cast(
            "runner.RuleEnvelope",
            {"schema_version": 1, "kind": "recorded", "path": str(checkout_path)},
        )

    monkeypatch.setattr(
        runner,
        "PACKAGE_ENVELOPE_BUILDERS",
        {RULE_ID: record},
    )
    runner.default_envelope_builder(RULE_ID, checkout("compliant-exception"))

    manifest = YAML(typ="safe").load((RULE_DIR / "rule.yaml").read_text("utf-8"))
    declared = manifest["parameters"]["defaults"]
    assert captured["parameters"] == declared, (
        f"the manifest defaults must reach the builder, got {captured!r}"
    )


def test_a_substituted_resolver_is_the_one_used() -> None:
    """The resolver is a dependency of the run, not a global the run reaches for.

    Proved by giving the run an envelope no checkout could produce and
    watching the policy answer about that instead.
    """
    substitute_calls: list[str] = []

    def substitute(rule_id: str, _checkout: pathlib.Path) -> runner.RuleEnvelope:
        substitute_calls.append(rule_id)
        # An envelope of the wrong schema version, which every policy refuses.
        # Typed through the cast the resolver contract expects; the whole point
        # is that the policy sees a document no checkout could have produced.
        return typ.cast(
            "runner.RuleEnvelope",
            {"schema_version": 99, "kind": "policy-input/rust-build-defaults"},
        )

    result = run_rule(
        RULE_ID,
        checkout("compliant-exception"),
        envelope_builder=substitute,
    )
    assert substitute_calls == [RULE_ID], (
        f"the substituted resolver must be called, got {substitute_calls!r}"
    )
    reported = {finding.rule_id for finding in result.findings}
    assert reported == {"EN-001"}, (
        f"the policy must answer about the substituted envelope, got {reported}"
    )
