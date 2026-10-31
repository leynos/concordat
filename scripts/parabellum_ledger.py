"""The append-only Parabellum campaign ledger: its schema and its records.

One JSON object per line. The record types are the contract between the
sweep that writes them and the report that reads them back.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import typing as typ

from concordat.errors import OperationalRuleError
from concordat.rules import packages

if typ.TYPE_CHECKING:
    import pathlib

    from concordat.rules import Finding

# The campaign default. The sweep can audit any installed package, but the
# baseline report and every record written without a selection name this one.
RULE_PACKAGE: typ.Final = "rust-makefile-baseline"
# The commit makeutil v0.1.0 is tagged at, which CI installs as a release.
MAKEUTIL_REV: typ.Final = "d33acd3ecb8d7d84042a28807243752e1f4dccee"
LEDGER_SCHEMA_VERSION: typ.Final = 1


class FindingRecord(typ.TypedDict):
    """One serialized :class:`concordat.rules.Finding` as stored in the ledger.

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
        One-based line number, or ``0`` when the finding is not
        line-specific.
    message:
        Human-readable description of the finding.

    """

    rule_id: str
    severity: str
    verdict: str
    path: str
    line: int
    message: str


class LedgerRequiredFields(typ.TypedDict):
    """The keys every ledger record carries.

    Attributes
    ----------
    schema_version:
        The ledger schema version the record was written under.
    repository:
        The repository's name.
    commit_sha:
        The audited commit's SHA, or ``None`` when no commit was
        resolved (excluded or errored records).
    audited_at:
        UTC timestamp at which the record was written.
    rule_package:
        Identifier of the evaluated rule package.
    rule_version:
        Version of the evaluated rule package.
    makeutil_rev:
        Revision of the makeutil helper used during the audit.
    verdict:
        The record's overall verdict.
    findings:
        The structured findings produced by the audit.

    """

    schema_version: int
    repository: str
    commit_sha: str | None
    audited_at: str
    rule_package: str
    rule_version: str
    makeutil_rev: str
    verdict: str
    findings: list[FindingRecord]


class LedgerOptionalFields(typ.TypedDict, total=False):
    """Keys present only on excluded and errored records.

    Attributes
    ----------
    exclusion_reason:
        The operator-supplied exclusion reason, present only on excluded
        records.
    error_detail:
        The tool or validation error detail, present only on errored
        records.

    """

    exclusion_reason: str
    error_detail: str


class LedgerRecord(LedgerRequiredFields, LedgerOptionalFields):
    """One append-only ledger line.

    Attributes
    ----------
    schema_version, repository, commit_sha, audited_at, rule_package,
    rule_version, makeutil_rev, verdict, findings:
        See :class:`LedgerRequiredFields`; every record carries these
        keys.
    exclusion_reason, error_detail:
        See :class:`LedgerOptionalFields`; present only on excluded and
        errored records respectively.

    """


type Ledger = list[LedgerRecord]


@dataclasses.dataclass(slots=True)
class LedgerIndex:
    """Bounded in-memory views needed to resume sweeps and render reports.

    The append-only file remains the source of truth. For sweeps, the index
    keeps package-scoped latest records, commit keys, and exclusions without
    retaining every decoded JSON object. Reports select one package and omit
    commit history entirely because it is not needed to render them.
    """

    package: str | None = None
    include_commit_history: bool = True
    latest_by_repository: dict[str, LedgerRecord] = dataclasses.field(
        default_factory=dict
    )
    commits_by_repository: dict[str, set[tuple[str, str, str]]] = dataclasses.field(
        default_factory=dict
    )
    excluded_repositories_by_package: dict[str, set[str]] = dataclasses.field(
        default_factory=dict
    )

    def add(self, record: LedgerRecord) -> None:
        """Index one newly read or appended ledger record."""
        package = record["rule_package"]
        if self.package is not None and package != self.package:
            return
        repository = record["repository"]
        self.latest_by_repository[repository] = record
        commit_sha = record["commit_sha"]
        if commit_sha is not None and self.include_commit_history:
            key = (package, record["rule_version"], commit_sha)
            self.commits_by_repository.setdefault(repository, set()).add(key)
        if record["verdict"] == "excluded":
            self.excluded_repositories_by_package.setdefault(package, set()).add(
                repository
            )


# Derived from the TypedDict so a new required key cannot be forgotten here.
_LEDGER_REQUIRED_KEYS: typ.Final = frozenset(LedgerRequiredFields.__annotations__)


def _ledger_record(
    decoded: object, path: pathlib.Path, line_number: int
) -> LedgerRecord:
    """Return *decoded* as a ledger record, or reject the malformed line.

    The ledger is append-only and read back on every sweep, so a truncated or
    hand-edited line has to surface as an operational error rather than be
    trusted into the typed flow by a cast.

    Returns
    -------
    LedgerRecord
        The validated decoded ledger record.

    Raises
    ------
    OperationalRuleError
        If the decoded line is not a valid ledger record.
    """
    if not isinstance(decoded, dict):
        message = f"ledger {path} line {line_number} is not a JSON object"
        raise OperationalRuleError(message, operation="load-ledger", resource=path)
    record = typ.cast("dict[str, object]", decoded)
    missing = sorted(_LEDGER_REQUIRED_KEYS - record.keys())
    if missing:
        message = f"ledger {path} line {line_number} is missing {missing}"
        raise OperationalRuleError(message, operation="load-ledger", resource=path)
    _validate_record_fields(record, path, line_number)
    return typ.cast("LedgerRecord", record)


def _ledger_error(message: str, path: pathlib.Path) -> OperationalRuleError:
    """Return the rejection for a ledger this build cannot read."""
    return OperationalRuleError(message, operation="load-ledger", resource=path)


# Every required field, and the type the readers assume it has. `commit_sha`
# is nullable: an excluded or errored record has no commit.
_LEDGER_FIELD_TYPES: typ.Final = {
    "schema_version": int,
    "repository": str,
    "audited_at": str,
    "rule_package": str,
    "rule_version": str,
    "makeutil_rev": str,
    "verdict": str,
}
_FINDING_FIELD_TYPES: typ.Final = {
    "rule_id": str,
    "severity": str,
    "verdict": str,
    "path": str,
    "line": int,
    "message": str,
}
_OPTIONAL_FIELD_TYPES: typ.Final = {"exclusion_reason": str, "error_detail": str}


def _matches_expected_type(value: object, expected: type) -> bool:
    """Return whether a decoded value has the required ledger field type.

    `bool` is excluded from the integer check: it subclasses `int`, so `true`
    would otherwise pass as a schema version or a line number.

    Returns
    -------
    bool
        Whether *value* has the expected type.
    """
    if expected is int:
        if isinstance(value, bool):
            return False
        return isinstance(value, int)
    return isinstance(value, expected)


def _require_field(
    value: object, expected: type, label: str, path: pathlib.Path
) -> None:
    """Reject a ledger field whose decoded type is not the one readers assume."""
    if _matches_expected_type(value, expected):
        return
    message = f"{label} is {type(value).__name__}, not {expected.__name__}"
    raise _ledger_error(message, path)


def _validate_standard_record_fields(
    record: dict[str, object], where: str, path: pathlib.Path
) -> None:
    """Check each required scalar field against its declared type."""
    for field, expected in _LEDGER_FIELD_TYPES.items():
        _require_field(record[field], expected, f"{where} field {field!r}", path)


def _validate_commit_sha(commit_sha: object, where: str, path: pathlib.Path) -> None:
    """Accept a commit SHA string, or the null an excluded record carries."""
    if commit_sha is None or isinstance(commit_sha, str):
        return
    message = f"{where} field 'commit_sha' is {type(commit_sha).__name__}"
    raise _ledger_error(message, path)


def _validate_optional_record_fields(
    record: dict[str, object], where: str, path: pathlib.Path
) -> None:
    """Check the optional fields that are present, ignoring those absent."""
    for field, expected in _OPTIONAL_FIELD_TYPES.items():
        if field in record:
            _require_field(record[field], expected, f"{where} field {field!r}", path)


def _validate_findings(findings: object, where: str, path: pathlib.Path) -> None:
    """Check the findings list and every serialized finding within it."""
    if not isinstance(findings, list):
        message = f"{where} field 'findings' is {type(findings).__name__}, not a list"
        raise _ledger_error(message, path)
    for index, finding in enumerate(typ.cast("list[object]", findings)):
        _validate_finding_fields(finding, f"{where} findings[{index}]", path)


def _validate_record_fields(
    record: dict[str, object], path: pathlib.Path, line_number: int
) -> None:
    """Check every record field before readers use the record.

    Key presence alone let a hand-edited ledger through with a numeric
    `repository` or a string `findings`, which then failed far from here —
    in the report renderer, or as a `TypeError` mid-sweep.
    """
    where = f"ledger {path} line {line_number}"
    _validate_standard_record_fields(record, where, path)
    _validate_commit_sha(record["commit_sha"], where, path)
    _validate_optional_record_fields(record, where, path)
    _validate_findings(record["findings"], where, path)


def _validate_finding_fields(finding: object, label: str, path: pathlib.Path) -> None:
    """Check one serialized finding's shape and field types."""
    if not isinstance(finding, dict):
        message = f"{label} is {type(finding).__name__}, not an object"
        raise _ledger_error(message, path)
    entry = typ.cast("dict[str, object]", finding)
    for field, expected in _FINDING_FIELD_TYPES.items():
        if field not in entry:
            message = f"{label} is missing {field!r}"
            raise _ledger_error(message, path)
        _require_field(entry[field], expected, f"{label} field {field!r}", path)


def _load_ledger(
    path: pathlib.Path,
    *,
    package: str | None = None,
    include_commit_history: bool = True,
) -> LedgerIndex:
    """Stream and index the append-only ledger, validating each line.

    A report can set *package* and disable *include_commit_history* so its
    memory use depends on current report rows rather than campaign history.

    Returns
    -------
    LedgerIndex
        Latest records, exclusions, and the requested commit-history index.

    Raises
    ------
    _ledger_error
        If a line is invalid or the ledger cannot be read.
    """
    if not path.exists():
        return LedgerIndex(package, include_commit_history)
    index = LedgerIndex(package, include_commit_history)
    try:
        with path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    decoded: object = json.loads(line)
                except json.JSONDecodeError as error:
                    message = f"ledger {path} line {number} is not valid JSON: {error}"
                    raise _ledger_error(message, path) from error
                index.add(_ledger_record(decoded, path, number))
    except (OSError, UnicodeDecodeError) as error:
        message = f"cannot read ledger {path}: {error}"
        raise _ledger_error(message, path) from error
    return index


def _timestamp() -> str:
    return (
        dt.datetime
        .now(dt.UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def rule_version(package: str = RULE_PACKAGE) -> str:
    """Return the version *package*'s manifest declares.

    A record must name the policy that judged it, so the version is read from
    the package manifest at audit time rather than kept in a constant here. A
    hand-maintained copy drifts: this module carried ``0.2.0`` while
    ``rust-makefile-baseline`` had reached ``0.3.2``, which would have stamped
    false provenance on every new record.

    Parameters
    ----------
    package:
        Rule package identifier. Defaults to the campaign package.

    Returns
    -------
    str
        The declared package version.

    Raises
    ------
    OperationalRuleError
        If the package is unknown, or its manifest declares no version.
    """
    rule_dir = packages.rule_package_dir(package)
    version = packages.rule_manifest(rule_dir).get("version")
    if not isinstance(version, str) or not version.strip():
        message = f"rule package {package!r} declares no version"
        raise OperationalRuleError(
            message,
            operation="load-rule-manifest",
            resource=rule_dir / "rule.yaml",
        )
    return version


def _base_record(
    repository: str,
    *,
    package: str = RULE_PACKAGE,
    version: str | None = None,
) -> LedgerRecord:
    """Return a fresh record naming *package* and the version it is stamped with.

    Without an explicit *version* the manifest is consulted, so a caller that
    only knows the package still writes truthful provenance.

    Returns
    -------
    LedgerRecord
        An ``error``-verdict record awaiting a commit, verdict and findings.
    """
    return {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "repository": repository,
        "commit_sha": None,
        "audited_at": _timestamp(),
        "rule_package": package,
        "rule_version": rule_version(package) if version is None else version,
        "makeutil_rev": MAKEUTIL_REV,
        "verdict": "error",
        "findings": [],
    }


def _excluded_record(
    repository: str,
    reason: str,
    *,
    package: str = RULE_PACKAGE,
    version: str | None = None,
) -> LedgerRecord:
    record = _base_record(repository, package=package, version=version)
    record["verdict"] = "excluded"
    record["exclusion_reason"] = reason
    return record


def _append_record(
    ledger_path: pathlib.Path,
    appended: Ledger,
    record: LedgerRecord,
) -> None:
    """Append one record to the ledger immediately.

    Auditing the estate takes many minutes and clones over the network, so
    each record is durable before the next repository is attempted; an
    interrupted sweep resumes rather than restarts.
    """
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    appended.append(record)


def _finding_record(finding: Finding) -> FindingRecord:
    """Serialize one finding, naming each field so the result stays typed.

    `dataclasses.asdict` returns `dict[str, Any]`, which would defeat the
    point of the record types.

    Returns
    -------
    FindingRecord
        The typed serialized representation of *finding*.
    """
    return {
        "rule_id": finding.rule_id,
        "severity": finding.severity,
        "verdict": finding.verdict,
        "path": finding.path,
        "line": finding.line,
        "message": finding.message,
    }


def _already_ledgered(
    ledger: LedgerIndex,
    repository: str,
    *,
    commit_sha: str | None,
    package: str = RULE_PACKAGE,
    package_version: str | None = None,
) -> bool:
    """Report whether *repository* is already covered by *package*.

    The resume key is repository, commit, package and package version. The
    ledger is shared by several campaigns, so a record only answers for the
    package that wrote it; and the same commit under the same package is not
    the same evidence when the policy has moved, so a version bump re-opens
    the repositories it had already covered.

    ``commit_sha=None`` asks the other question — "is this repository excluded
    from the campaign" — which has no commit and no version to match.

    Returns
    -------
    bool
        Whether a matching record is already present.
    """
    if ledger.package is not None and ledger.package != package:
        return False
    if commit_sha is None:
        return repository in ledger.excluded_repositories_by_package.get(package, set())
    version = rule_version(package) if package_version is None else package_version
    key = (package, version, commit_sha)
    return key in ledger.commits_by_repository.get(repository, set())
