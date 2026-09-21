"""Evaluate a canon lint rule package against a local checkout."""

from __future__ import annotations

import collections.abc as cabc
import dataclasses
import functools
import importlib.resources
import json
import pathlib
import re
import subprocess
import tempfile
import types
import typing as typ

from concordat.errors import OperationalRuleError

from . import manifest
from .envelope import ENVELOPE_KIND as RUST_ENVELOPE_KIND
from .envelope import (
    BuildDefaultsEnvelope,
    PolicyEnvelope,
    build_build_defaults_envelope,
    build_envelope,
)
from .markdown_envelope import ENVELOPE_KIND as MARKDOWN_ENVELOPE_KIND
from .markdown_envelope import MarkdownEnvelope, build_markdown_envelope

type RuleEnvelope = PolicyEnvelope | BuildDefaultsEnvelope | MarkdownEnvelope
type EnvelopeResolver = cabc.Callable[[str, pathlib.Path], RuleEnvelope]


def _resolve_rule_packages_dir() -> pathlib.Path:
    """Return the canon lint-rule tree, whether installed or run from source.

    A wheel ships the policies inside the package at ``concordat/canon/
    lint-rules`` (see the ``concordat.canon`` package-data mapping in
    ``pyproject.toml``), reachable via ``importlib.resources``. A source
    checkout keeps them in the sibling ``platform-standards`` tree, so that
    layout is used as a fallback.

    Returns
    -------
    pathlib.Path
        Directory containing the canon lint-rule packages.
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


@functools.lru_cache(maxsize=1)
def _rule_packages_dir() -> pathlib.Path:
    """Return the cached canon lint-rule tree."""
    return _resolve_rule_packages_dir()


CONFTEST_TIMEOUT: typ.Final = 60.0
# Conftest reports an evaluated policy with 0 (clean) or 1 (failures); any
# other status means it did not evaluate one, whatever it printed on stdout.
POLICY_EXIT_CODES: typ.Final = frozenset({0, 1})
# Diagnostics are quoted back to the operator, so cap how much of a runaway
# stderr reaches the error message.
_MAX_ERROR_DETAIL: typ.Final = 500

# A rule manifest names the policy-input document its sensor evaluates under
# `sensor.input`; the builder that assembles that document from a checkout is
# looked up here. The Rust kind is also the default for a manifest that
# predates the field, so the first rule package keeps working unchanged.
ENVELOPE_BUILDERS: typ.Final[
    typ.Mapping[str, cabc.Callable[[pathlib.Path], RuleEnvelope]]
] = {
    RUST_ENVELOPE_KIND: build_envelope,
    MARKDOWN_ENVELOPE_KIND: build_markdown_envelope,
}

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
        """Process exit code: 0 when compliant, otherwise 1 (fail closed).

        Returns
        -------
        int
            ``0`` when compliant; otherwise ``1``.
        """
        return 0 if self.verdict == VERDICT_COMPLIANT else 1


# A rule package is one canonical name: lower-case ASCII words joined by
# single hyphens. Anything else — a separator, a dot segment, punctuation — is
# refused before it can be joined to a path.
_RULE_ID_PATTERN: typ.Final = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _validated_rule_id(rule_id: str) -> str:
    """Return *rule_id* if it is a canonical package name, else raise."""
    if not _RULE_ID_PATTERN.fullmatch(rule_id):
        message = (
            f"invalid rule package {rule_id!r}; expected lower-case words "
            "joined by single hyphens"
        )
        raise OperationalRuleError(
            message,
            operation="load-rule-package",
            resource=rule_id,
        )
    return rule_id


def _rule_package_dir(rule_id: str) -> pathlib.Path:
    """Return the rule package directory for *rule_id*, or raise if unknown.

    The identifier is validated before it is joined to a path, and the joined
    path is then confirmed to stay under the packages root. The pattern alone
    already excludes traversal, but the containment check means a future
    loosening of the pattern cannot silently reach outside the root.

    The packages root is resolved here rather than at import, so a missing or
    unreadable rule tree fails when a rule is run rather than when the module
    is imported — importing the CLI should not depend on the policy tree.

    Returns
    -------
    pathlib.Path
        Directory containing the requested rule package.

    Raises
    ------
    OperationalRuleError
        If *rule_id* is invalid or its package directory is unavailable.
    """
    # Validation first: a malformed identifier is a local error, and must not
    # cost the packages-root lookup (which touches the filesystem) to reject.
    validated = _validated_rule_id(rule_id)
    packages_root = _rule_packages_dir()
    rule_dir = packages_root / validated
    root = packages_root.resolve()
    candidate = rule_dir.resolve()
    if not candidate.is_relative_to(root):
        message = f"rule package {rule_id!r} resolves outside {root}"
        raise OperationalRuleError(
            message,
            operation="load-rule-package",
            resource=rule_id,
        )
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


def _rule_parameters(rule_dir: pathlib.Path) -> dict[str, object]:
    """Return the rule manifest's parameter defaults.

    Returns
    -------
    dict[str, object]
        Parameter defaults declared by the rule manifest.
    """
    return manifest.parameter_defaults(rule_dir)


def _envelope_builder(
    rule_dir: pathlib.Path,
) -> cabc.Callable[[pathlib.Path], RuleEnvelope]:
    """Return the builder for the policy input the rule manifest declares.

    An `OperationalRuleError` propagates from the manifest read.

    Returns
    -------
    cabc.Callable[[pathlib.Path], RuleEnvelope]
        The function assembling the declared policy-input document.

    Raises
    ------
    OperationalRuleError
        If the manifest declares a kind this build cannot assemble.
    """
    declared = manifest.declared_input(rule_dir, RUST_ENVELOPE_KIND)
    builder = ENVELOPE_BUILDERS.get(declared)
    if builder is not None:
        return builder
    known = ", ".join(sorted(ENVELOPE_BUILDERS))
    manifest_path = rule_dir / manifest.MANIFEST_FILENAME
    message = (
        f"rule manifest {manifest_path} declares the policy input "
        f"{declared!r}; expected one of: {known}"
    )
    raise OperationalRuleError(
        message,
        operation=manifest.OPERATION,
        resource=manifest_path,
    )


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


def _bounded_detail(completed: subprocess.CompletedProcess[str]) -> str:
    """Return Conftest's diagnostic, truncated to a bounded length."""
    # The diagnostic is whatever the tool printed, so it can be arbitrarily
    # long — a stack trace, or a whole result document echoed to stderr. Both
    # failure paths that interpolate it use this, so neither can put unbounded
    # tool output in front of an operator.
    detail = (completed.stderr or completed.stdout or "").strip()
    if len(detail) > _MAX_ERROR_DETAIL:
        return f"{detail[:_MAX_ERROR_DETAIL]}..."
    return detail


def _require_policy_exit_code(
    completed: subprocess.CompletedProcess[str],
    rule_id: str,
) -> None:
    """Reject any Conftest exit status that is not a policy verdict.

    Only 0 (no failures) and 1 (policy failures) describe an evaluated policy.
    A higher status means Conftest could not evaluate it — a malformed policy,
    a bad flag, a missing file — and it may still print well-formed JSON on
    stdout. Decoding that would report an operational failure as a clean run.

    Raises
    ------
    OperationalRuleError
        If Conftest exits without producing a policy verdict.

    """
    if completed.returncode in POLICY_EXIT_CODES:
        return
    detail = _bounded_detail(completed)
    message = (
        f"conftest exited {completed.returncode} without evaluating the policy"
        f"{f': {detail}' if detail else ''}"
    )
    raise OperationalRuleError(
        message,
        operation="invoke-conftest",
        tool="conftest",
        resource=rule_id,
    )


def _invoke_conftest(
    rule_id: str,
    envelope: RuleEnvelope,
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
    _require_policy_exit_code(completed, rule_id)
    detail = _bounded_detail(completed)
    try:
        decoded: object = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        message = f"conftest produced no usable output: {detail}"
        raise OperationalRuleError(
            message,
            operation="invoke-conftest",
            tool="conftest",
            resource=rule_id,
        ) from error
    # A result document is an array, one entry per evaluated input. Valid JSON
    # of another shape is still unusable, and the annotation alone would not
    # stop it: it would be returned as-is and fail later, far from the cause.
    if not isinstance(decoded, list):
        message = (
            "conftest produced no usable output: expected a JSON array of "
            f"results, got {type(decoded).__name__}: {detail}"
        )
        raise OperationalRuleError(
            message,
            operation="invoke-conftest",
            tool="conftest",
            resource=rule_id,
        )
    return [
        _validated_result(entry, index, rule_id, detail)
        for index, entry in enumerate(typ.cast("list[object]", decoded))
    ]


def _conftest_shape_error(
    label: str, rule_id: str, detail: str
) -> OperationalRuleError:
    """Return the rejection for a Conftest result of the wrong shape."""
    message = f"conftest produced no usable output: {label}: {detail}"
    return OperationalRuleError(
        message,
        operation="invoke-conftest",
        tool="conftest",
        resource=rule_id,
    )


def _validated_result(
    entry: object, index: int, rule_id: str, detail: str
) -> _ConftestResult:
    """Return one validated Conftest result."""
    # The outer array check says nothing about its elements, and the consumers
    # read them with `.get`. A non-mapping result, a non-list `failures`, or a
    # non-mapping failure or `metadata` therefore surfaced as a bare
    # `AttributeError` or `TypeError` from the finding conversion — untagged,
    # and naming neither the tool nor the rule package.
    if not isinstance(entry, dict):
        label = f"result[{index}] is {type(entry).__name__}, not an object"
        raise _conftest_shape_error(label, rule_id, detail)
    result = typ.cast("dict[str, object]", entry)
    failures = result.get("failures", [])
    if not isinstance(failures, list):
        kind = type(failures).__name__
        label = f"result[{index}].failures is {kind}, not an array"
        raise _conftest_shape_error(label, rule_id, detail)
    for position, failure in enumerate(typ.cast("list[object]", failures)):
        _validate_failure(
            failure, f"result[{index}].failures[{position}]", rule_id, detail
        )
    return typ.cast("_ConftestResult", result)


def _validate_failure(failure: object, label: str, rule_id: str, detail: str) -> None:
    """Reject a failure entry, or its metadata, that is not an object."""
    if not isinstance(failure, dict):
        message = f"{label} is {type(failure).__name__}, not an object"
        raise _conftest_shape_error(message, rule_id, detail)
    metadata = typ.cast("dict[str, object]", failure).get("metadata", {})
    if not isinstance(metadata, dict):
        message = f"{label}.metadata is {type(metadata).__name__}, not an object"
        raise _conftest_shape_error(message, rule_id, detail)
    _validate_metadata_line(
        typ.cast("dict[str, object]", metadata), label, rule_id, detail
    )


def _validate_metadata_line(
    metadata: dict[str, object], label: str, rule_id: str, detail: str
) -> None:
    """Reject a metadata line number that `int()` could not accept."""
    if "line" not in metadata:
        return
    line = metadata["line"]
    # `bool` subclasses `int` and `int()` truncates a float, so neither raises:
    # `true` and `1.5` would both become line 1. They are rejected here rather
    # than silently reported as a line the policy never named.
    if isinstance(line, bool | float):
        message = f"{label}.metadata.line is not a line number: {line!r}"
        raise _conftest_shape_error(message, rule_id, detail)
    # `_finding_from_failure` calls `int()` on this. A null, array, object, or
    # non-numeric string raises there instead — after validation has already
    # passed, and with none of the structured context this boundary adds.
    try:
        int(typ.cast("typ.Any", line))
    except (TypeError, ValueError) as error:
        message = f"{label}.metadata.line is not a line number: {line!r}"
        raise _conftest_shape_error(message, rule_id, detail) from error


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


# A rule package reads the facts its checks need, and those differ. Two ways
# of saying which are in play, and they compose rather than compete. A package
# named here supplies its own builder and receives the manifest parameters
# that builder needs; that is the only reason to name one. Every other package
# declares its policy input in its own manifest under `sensor.input`, which
# `_envelope_builder` reads, and a manifest that predates the field takes the
# historic Makefile envelope, so existing packages are untouched. The mapping
# is a read-only view: package selection is a composition decision, not state
# a caller may reach in and change.
PACKAGE_ENVELOPE_BUILDERS: typ.Final = types.MappingProxyType({
    "rust-build-defaults": build_build_defaults_envelope,
})


def default_envelope_builder(rule_id: str, checkout: pathlib.Path) -> RuleEnvelope:
    """Return the policy input *rule_id* is evaluated over.

    This is the composition layer's resolver: it decides which builder a
    package takes and supplies the manifest parameters that builder needs.
    `run_rule` calls whatever resolver it is given, so a caller — including a
    test — can substitute one without touching the mapping above.

    Returns
    -------
    RuleEnvelope
        The envelope built by the package's own builder, or the default one.
    """
    rule_dir = _rule_package_dir(rule_id)
    builder = PACKAGE_ENVELOPE_BUILDERS.get(rule_id)
    if builder is not None:
        return builder(checkout, _rule_parameters(rule_dir))
    return _envelope_builder(rule_dir)(checkout)


def run_rule(
    rule_id: str,
    checkout: pathlib.Path,
    *,
    envelope_builder: EnvelopeResolver = default_envelope_builder,
) -> RuleRunResult:
    """Evaluate *rule_id* against *checkout* and return the structured result.

    Parameters
    ----------
    rule_id:
        Identifier of the rule package to evaluate.
    checkout:
        Path to the local checkout to audit.
    envelope_builder:
        Resolver that builds the policy input for a package. The default
        resolves the package's own builder; a caller may substitute one.

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
    envelope = envelope_builder(rule_id, checkout)
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
        "  ".join((
            row[0].ljust(widths[0]),
            row[1].ljust(widths[1]),
            row[2].ljust(widths[2]),
            row[3],
        ))
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
