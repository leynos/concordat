"""Evaluate a canon lint rule package against a local checkout."""

from __future__ import annotations

import dataclasses
import importlib.resources
import json
import pathlib
import subprocess
import tempfile
import typing as typ

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from concordat.errors import OperationalRuleError

from .envelope import PolicyEnvelope, build_envelope


def _resolve_rule_packages_dir() -> pathlib.Path:
    """Return the canon lint-rule tree, whether installed or run from source.

    A wheel ships the policies inside the package (see the
    ``force-include`` in ``pyproject.toml``), reachable via
    ``importlib.resources``. A source checkout keeps them in the sibling
    ``platform-standards`` tree, so that layout is used as a fallback.
    """
    packaged = importlib.resources.files("concordat") / "canon" / "lint-rules"
    if isinstance(packaged, pathlib.Path) and packaged.is_dir():
        return packaged
    source = (
        pathlib.Path(__file__).resolve().parents[2]
        / "platform-standards"
        / "canon"
        / "lint-rules"
    )
    if source.is_dir():
        return source
    return pathlib.Path(str(packaged))


RULE_PACKAGES_DIR: typ.Final = _resolve_rule_packages_dir()

CONFTEST_TIMEOUT: typ.Final = 60.0

_yaml = YAML(typ="safe")

VERDICT_COMPLIANT: typ.Final = "compliant"
VERDICT_NONCOMPLIANT: typ.Final = "noncompliant"
VERDICT_INDETERMINATE: typ.Final = "indeterminate"


class _ConftestMetadata(typ.TypedDict, total=False):
    """The finding metadata a policy rule attaches to a Conftest failure."""

    rule_id: str
    severity: str
    verdict: str
    path: str
    line: int


class _ConftestFailure(typ.TypedDict, total=False):
    """One failing assertion in a Conftest result document."""

    metadata: _ConftestMetadata
    msg: str


class _ConftestResult(typ.TypedDict, total=False):
    """One Conftest result document (one per evaluated input file)."""

    failures: list[_ConftestFailure]


@dataclasses.dataclass(frozen=True, slots=True)
class Finding:
    """One structured policy finding.

    Attributes
    ----------
    rule_id:
        Identifier of the policy rule that produced the finding.
    severity:
        Severity label reported by the policy (for example ``"error"``).
    verdict:
        The finding's verdict: ``compliant``, ``noncompliant``, or
        ``indeterminate``.
    path:
        Repository-relative path the finding refers to.
    line:
        One-based line number, or ``0`` when the finding is not line-specific.
    message:
        Human-readable description of the finding.

    """

    rule_id: str
    severity: str
    verdict: str
    path: str
    line: int
    message: str


@dataclasses.dataclass(frozen=True, slots=True)
class RuleRunResult:
    """Outcome of evaluating one rule package against one checkout.

    Attributes
    ----------
    rule_package:
        Identifier of the evaluated rule package.
    verdict:
        The overall verdict aggregated across every finding.
    findings:
        The structured findings emitted by the policy.

    """

    rule_package: str
    verdict: str
    findings: tuple[Finding, ...]

    @property
    def exit_code(self) -> int:
        """Return 0 when compliant; 1 when any finding exists (fail closed)."""
        return 0 if self.verdict == VERDICT_COMPLIANT else 1


def _rule_package_dir(rule_id: str) -> pathlib.Path:
    """Return the rule package directory for *rule_id*, or raise if unknown."""
    rule_dir = RULE_PACKAGES_DIR / rule_id
    if not (rule_dir / "policy").is_dir():
        message = f"unknown rule package {rule_id!r}; expected {rule_dir}/policy"
        raise OperationalRuleError(
            message,
            operation="load-rule-package",
            resource=rule_id,
        )
    return rule_dir


def _policy_namespace(rule_id: str) -> str:
    """Return the Rego package namespace for *rule_id*."""
    return "canon.lint_rules." + rule_id.replace("-", "_")


def _rule_parameters(rule_dir: pathlib.Path) -> dict[str, typ.Any]:
    """Return the rule manifest's parameter defaults.

    The policies read their tunables from ``data.parameters``; without this
    the manifest's declared defaults would be inert and only the ``default``
    rules baked into the Rego would ever apply.
    """
    manifest_path = rule_dir / "rule.yaml"
    if not manifest_path.is_file():
        return {}
    try:
        manifest = _yaml.load(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, YAMLError) as error:
        message = f"cannot read rule manifest {manifest_path}: {error}"
        raise OperationalRuleError(
            message,
            operation="load-rule-manifest",
            resource=manifest_path,
        ) from error
    if not isinstance(manifest, dict):
        message = f"rule manifest {manifest_path} is not a mapping"
        raise OperationalRuleError(
            message,
            operation="load-rule-manifest",
            resource=manifest_path,
        )
    parameters = manifest.get("parameters")
    if not isinstance(parameters, dict):
        return {}
    defaults = parameters.get("defaults")
    return dict(defaults) if isinstance(defaults, dict) else {}


def _run_conftest(argv: list[str], rule_id: str) -> subprocess.CompletedProcess[str]:
    """Run the fixed Conftest argv, translating spawn and timeout failures."""
    try:
        return subprocess.run(  # noqa: S603 - fixed argv, no shell
            argv,
            capture_output=True,
            text=True,
            timeout=CONFTEST_TIMEOUT,
            check=False,
        )
    except FileNotFoundError as error:
        message = "conftest is required but was not found on PATH"
        raise OperationalRuleError(
            message,
            operation="invoke-conftest",
            tool="conftest",
            resource=rule_id,
        ) from error
    except subprocess.TimeoutExpired as error:
        message = f"conftest timed out after {CONFTEST_TIMEOUT}s"
        raise OperationalRuleError(
            message,
            operation="invoke-conftest",
            tool="conftest",
            resource=rule_id,
        ) from error


def _invoke_conftest(
    rule_id: str,
    envelope: PolicyEnvelope,
) -> list[_ConftestResult]:
    """Evaluate *envelope* against *rule_id*'s policy and return the results."""
    rule_dir = _rule_package_dir(rule_id)
    policy_dir = rule_dir / "policy"
    parameters = _rule_parameters(rule_dir)
    with tempfile.TemporaryDirectory(prefix="concordat-rule-") as scratch:
        envelope_path = pathlib.Path(scratch) / "envelope.json"
        envelope_path.write_text(json.dumps(envelope), encoding="utf-8")
        data_path = pathlib.Path(scratch) / "parameters.json"
        data_path.write_text(
            json.dumps({"parameters": parameters}),
            encoding="utf-8",
        )
        argv = [
            "conftest",
            "test",
            "--policy",
            str(policy_dir),
            "--data",
            str(data_path),
            "--namespace",
            _policy_namespace(rule_id),
            "--output",
            "json",
            str(envelope_path),
        ]
        completed = _run_conftest(argv, rule_id)

    # Conftest exits 0 on success and 1 on policy failures; both emit a JSON
    # result document. Anything else (or unparseable output) is operational.
    try:
        results: list[_ConftestResult] = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        detail = (completed.stderr or completed.stdout or "").strip()
        message = f"conftest produced no usable output: {detail}"
        raise OperationalRuleError(
            message,
            operation="invoke-conftest",
            tool="conftest",
            resource=rule_id,
        ) from error
    return results


def _finding_from_failure(failure: _ConftestFailure) -> Finding:
    """Convert one Conftest failure document into a structured Finding."""
    metadata = failure.get("metadata", {})
    return Finding(
        rule_id=str(metadata.get("rule_id", "UNKNOWN")),
        severity=str(metadata.get("severity", "error")),
        verdict=str(metadata.get("verdict", VERDICT_NONCOMPLIANT)),
        path=str(metadata.get("path", "")),
        line=int(metadata.get("line", 0)),
        message=str(failure.get("msg", "")),
    )


def _findings_from_results(
    results: list[_ConftestResult],
) -> tuple[Finding, ...]:
    """Flatten every Conftest failure across *results* into a tuple of findings."""
    return tuple(
        _finding_from_failure(failure)
        for result in results
        for failure in result.get("failures", [])
    )


def _overall_verdict(findings: tuple[Finding, ...]) -> str:
    """Reduce findings to noncompliant, indeterminate, or compliant."""
    if any(f.verdict == VERDICT_NONCOMPLIANT for f in findings):
        return VERDICT_NONCOMPLIANT
    if findings:
        return VERDICT_INDETERMINATE
    return VERDICT_COMPLIANT


def run_rule(rule_id: str, checkout: pathlib.Path) -> RuleRunResult:
    """Evaluate *rule_id* against *checkout* and return the structured result.

    Parameters
    ----------
    rule_id:
        Identifier of the rule package to evaluate.
    checkout:
        Path to the local checkout to audit.

    Returns
    -------
    RuleRunResult
        The overall verdict and the findings produced by the policy.

    Raises
    ------
    OperationalRuleError
        If the rule package is unknown, *checkout* is not a directory, or
        Conftest cannot be run or produces no usable output.

    """
    _rule_package_dir(rule_id)
    if not checkout.is_dir():
        message = f"checkout path {checkout} is not a directory"
        raise OperationalRuleError(
            message,
            operation="audit-checkout",
            resource=checkout,
        )
    envelope = build_envelope(checkout)
    results = _invoke_conftest(rule_id, envelope)
    findings = _findings_from_results(results)
    return RuleRunResult(
        rule_package=rule_id,
        verdict=_overall_verdict(findings),
        findings=findings,
    )


def render_table(result: RuleRunResult) -> str:
    """Render a result as an aligned plain-text table.

    Parameters
    ----------
    result:
        The rule-run result to render.

    Returns
    -------
    str
        A header line, followed by one aligned row per finding.

    """
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
    """Render a result as a stable JSON document.

    Parameters
    ----------
    result:
        The rule-run result to render.

    Returns
    -------
    str
        A pretty-printed JSON object with the package, verdict, and findings.

    """
    return json.dumps(
        {
            "rule_package": result.rule_package,
            "verdict": result.verdict,
            "findings": [dataclasses.asdict(finding) for finding in result.findings],
        },
        indent=2,
    )
