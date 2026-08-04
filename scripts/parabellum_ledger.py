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
    """One serialized :class:`concordat.rules.Finding` as stored in the ledger."""

    rule_id: str
    severity: str
    verdict: str
    path: str
    line: int
    message: str


class LedgerRequiredFields(typ.TypedDict):
    """The keys every ledger record carries."""

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
    """Keys present only on excluded and errored records."""

    exclusion_reason: str
    error_detail: str


class LedgerRecord(LedgerRequiredFields, LedgerOptionalFields):
    """One append-only ledger line."""


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
        raise OperationalRuleError(
            message,
            operation="load-ledger",
            resource=path,
        )
    missing = sorted(_LEDGER_REQUIRED_KEYS - decoded.keys())
    if missing:
        message = f"ledger {path} line {line_number} is missing {missing}"
        raise OperationalRuleError(
            message,
            operation="load-ledger",
            resource=path,
        )
    return typ.cast("LedgerRecord", decoded)


def _load_ledger(path: pathlib.Path) -> Ledger:
    if not path.exists():
        return []
    return [
        _ledger_record(json.loads(line), path, number)
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if line.strip()
    ]


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
