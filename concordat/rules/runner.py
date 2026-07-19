"""Evaluate a canon lint rule package against a local checkout."""

from __future__ import annotations

import dataclasses
import json
import pathlib
import subprocess
import tempfile
import typing as typ

from concordat.errors import OperationalRuleError

from .envelope import build_envelope

RULE_PACKAGES_DIR: typ.Final = (
    pathlib.Path(__file__).resolve().parents[2]
    / "platform-standards"
    / "canon"
    / "lint-rules"
)

CONFTEST_TIMEOUT: typ.Final = 60.0

VERDICT_COMPLIANT: typ.Final = "compliant"
VERDICT_NONCOMPLIANT: typ.Final = "noncompliant"
VERDICT_INDETERMINATE: typ.Final = "indeterminate"


@dataclasses.dataclass(frozen=True, slots=True)
class Finding:
    """One structured policy finding."""

    rule_id: str
    severity: str
    verdict: str
    path: str
    line: int
    message: str


@dataclasses.dataclass(frozen=True, slots=True)
class RuleRunResult:
    """Outcome of evaluating one rule package against one checkout."""

    rule_package: str
    verdict: str
    findings: tuple[Finding, ...]

    @property
    def exit_code(self) -> int:
        """0 when compliant; 1 when any finding (fail closed) exists."""
        return 0 if self.verdict == VERDICT_COMPLIANT else 1


def _rule_package_dir(rule_id: str) -> pathlib.Path:
    rule_dir = RULE_PACKAGES_DIR / rule_id
    if not (rule_dir / "policy").is_dir():
        message = f"unknown rule package {rule_id!r}; expected {rule_dir}/policy"
        raise OperationalRuleError(message)
    return rule_dir


def _policy_namespace(rule_id: str) -> str:
    return "canon.lint_rules." + rule_id.replace("-", "_")


def _invoke_conftest(
    rule_id: str,
    envelope: dict[str, typ.Any],
) -> list[dict[str, typ.Any]]:
    policy_dir = _rule_package_dir(rule_id) / "policy"
    with tempfile.TemporaryDirectory(prefix="concordat-rule-") as scratch:
        envelope_path = pathlib.Path(scratch) / "envelope.json"
        envelope_path.write_text(json.dumps(envelope), encoding="utf-8")
        argv = [
            "conftest",
            "test",
            "--policy",
            str(policy_dir),
            "--namespace",
            _policy_namespace(rule_id),
            "--output",
            "json",
            str(envelope_path),
        ]
        try:
            completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
                argv,
                capture_output=True,
                text=True,
                timeout=CONFTEST_TIMEOUT,
                check=False,
            )
        except FileNotFoundError as error:
            message = "conftest is required but was not found on PATH"
            raise OperationalRuleError(message) from error
        except subprocess.TimeoutExpired as error:
            message = f"conftest timed out after {CONFTEST_TIMEOUT}s"
            raise OperationalRuleError(message) from error

    # Conftest exits 0 on success and 1 on policy failures; both emit a JSON
    # result document. Anything else (or unparseable output) is operational.
    try:
        results: list[dict[str, typ.Any]] = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        detail = (completed.stderr or completed.stdout or "").strip()
        message = f"conftest produced no usable output: {detail}"
        raise OperationalRuleError(message) from error
    return results


def _findings_from_results(
    results: list[dict[str, typ.Any]],
) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    for result in results:
        for failure in result.get("failures", []):
            metadata = failure.get("metadata", {})
            findings.append(
                Finding(
                    rule_id=str(metadata.get("rule_id", "UNKNOWN")),
                    severity=str(metadata.get("severity", "error")),
                    verdict=str(metadata.get("verdict", VERDICT_NONCOMPLIANT)),
                    path=str(metadata.get("path", "")),
                    line=int(metadata.get("line", 0)),
                    message=str(failure.get("msg", "")),
                )
            )
    return tuple(findings)


def _overall_verdict(findings: tuple[Finding, ...]) -> str:
    if any(f.verdict == VERDICT_NONCOMPLIANT for f in findings):
        return VERDICT_NONCOMPLIANT
    if findings:
        return VERDICT_INDETERMINATE
    return VERDICT_COMPLIANT


def run_rule(rule_id: str, checkout: pathlib.Path) -> RuleRunResult:
    """Evaluate *rule_id* against *checkout* and return the structured result."""
    _rule_package_dir(rule_id)
    if not checkout.is_dir():
        message = f"checkout path {checkout} is not a directory"
        raise OperationalRuleError(message)
    envelope = build_envelope(checkout)
    results = _invoke_conftest(rule_id, envelope)
    findings = _findings_from_results(results)
    return RuleRunResult(
        rule_package=rule_id,
        verdict=_overall_verdict(findings),
        findings=findings,
    )


def render_table(result: RuleRunResult) -> str:
    """Render a result as an aligned plain-text table."""
    header = f"{result.rule_package}: {result.verdict}"
    if not result.findings:
        return header
    rows = [
        (
            finding.rule_id,
            finding.verdict,
            f"{finding.path}:{finding.line}",
            finding.message,
        )
        for finding in result.findings
    ]
    widths = [max(len(row[column]) for row in rows) for column in range(3)]
    lines = [header]
    lines.extend(
        "  ".join(
            (
                row[0].ljust(widths[0]),
                row[1].ljust(widths[1]),
                row[2].ljust(widths[2]),
                row[3],
            )
        )
        for row in rows
    )
    return "\n".join(lines)


def render_json(result: RuleRunResult) -> str:
    """Render a result as a stable JSON document."""
    return json.dumps(
        {
            "rule_package": result.rule_package,
            "verdict": result.verdict,
            "findings": [dataclasses.asdict(finding) for finding in result.findings],
        },
        indent=2,
    )
