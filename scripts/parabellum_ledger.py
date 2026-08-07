"""The append-only Parabellum campaign ledger: its schema and its records.

One JSON object per line. The record types are the contract between the
sweep that writes them and the report that reads them back.
"""

from __future__ import annotations

import datetime as dt
import json
import typing as typ

from concordat.errors import OperationalRuleError

if typ.TYPE_CHECKING:
    import pathlib

    from concordat.rules import Finding

RULE_PACKAGE: typ.Final = "rust-makefile-baseline"
RULE_VERSION: typ.Final = "0.2.0"
MAKEUTIL_REV: typ.Final = "29fc5a1634ffbaa18a773eed9dff1b2838a45d9c"
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

# Derived from the TypedDict so a new required key cannot be forgotten here.
_LEDGER_REQUIRED_KEYS: typ.Final = frozenset(LedgerRequiredFields.__annotations__)


def _ledger_record(
    decoded: object, path: pathlib.Path, line_number: int
) -> LedgerRecord:
    """Return *decoded* as a ledger record, or reject the malformed line.

    The ledger is append-only and read back on every sweep, so a truncated or
    hand-edited line has to surface as an operational error rather than be
    trusted into the typed flow by a cast.
    """
    if not isinstance(decoded, dict):
        message = f"ledger {path} line {line_number} is not a JSON object"
        raise _ledger_error(message, path)
    record = typ.cast("dict[str, object]", decoded)
    missing = sorted(_LEDGER_REQUIRED_KEYS - record.keys())
    if missing:
        message = f"ledger {path} line {line_number} is missing {missing}"
        raise _ledger_error(message, path)
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


def _require_field(
    value: object, expected: type, label: str, path: pathlib.Path
) -> None:
    """Reject a ledger field whose decoded type is not the one readers assume.

    `bool` is excluded from the integer check: it subclasses `int`, so `true`
    would otherwise pass as a schema version or a line number.
    """
    if isinstance(value, expected) and not (
        expected is int and isinstance(value, bool)
    ):
        return
    message = f"{label} is {type(value).__name__}, not {expected.__name__}"
    raise _ledger_error(message, path)


def _validate_record_fields(
    record: dict[str, object], path: pathlib.Path, line_number: int
) -> None:
    """Check every field's type, not merely that its key is present.

    Key presence alone let a hand-edited ledger through with a numeric
    `repository` or a string `findings`, which then failed far from here —
    in the report renderer, or as a `TypeError` mid-sweep.
    """
    where = f"ledger {path} line {line_number}"
    for field, expected in _LEDGER_FIELD_TYPES.items():
        _require_field(record[field], expected, f"{where} field {field!r}", path)
    commit_sha = record["commit_sha"]
    if commit_sha is not None and not isinstance(commit_sha, str):
        message = f"{where} field 'commit_sha' is {type(commit_sha).__name__}"
        raise _ledger_error(message, path)
    for field, expected in _OPTIONAL_FIELD_TYPES.items():
        if field in record:
            _require_field(record[field], expected, f"{where} field {field!r}", path)
    findings = record["findings"]
    if not isinstance(findings, list):
        message = f"{where} field 'findings' is {type(findings).__name__}, not a list"
        raise _ledger_error(message, path)
    for index, finding in enumerate(typ.cast("list[object]", findings)):
        _validate_finding_fields(finding, f"{where} findings[{index}]", path)


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


def _load_ledger(path: pathlib.Path) -> Ledger:
    """Read the append-only ledger, rejecting any line it cannot decode."""
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        message = f"cannot read ledger {path}: {error}"
        raise _ledger_error(message, path) from error
    records: Ledger = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            decoded: object = json.loads(line)
        except json.JSONDecodeError as error:
            # Decoding inside the comprehension let this escape as a bare
            # `JSONDecodeError`, naming neither the ledger nor the line.
            message = f"ledger {path} line {number} is not valid JSON: {error}"
            raise _ledger_error(message, path) from error
        records.append(_ledger_record(decoded, path, number))
    return records


def _timestamp() -> str:
    return (
        dt.datetime.now(dt.UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _base_record(repository: str) -> LedgerRecord:
    return {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "repository": repository,
        "commit_sha": None,
        "audited_at": _timestamp(),
        "rule_package": RULE_PACKAGE,
        "rule_version": RULE_VERSION,
        "makeutil_rev": MAKEUTIL_REV,
        "verdict": "error",
        "findings": [],
    }


def _excluded_record(repository: str, reason: str) -> LedgerRecord:
    record = _base_record(repository)
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
    ledger: Ledger,
    repository: str,
    *,
    commit_sha: str | None,
) -> bool:
    if commit_sha is None:
        return any(
            record["repository"] == repository and record["verdict"] == "excluded"
            for record in ledger
        )
    return any(
        record["repository"] == repository and record["commit_sha"] == commit_sha
        for record in ledger
    )
