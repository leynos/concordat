"""Markdown rendering of the Parabellum baseline report from the ledger."""

from __future__ import annotations

import typing as typ

from scripts.parabellum_ledger import (
    MAKEUTIL_REV,
    RULE_PACKAGE,
    RULE_VERSION,
    FindingRecord,
    Ledger,
    LedgerRecord,
    _load_ledger,
)
from scripts.parabellum_paths import DEFAULT_LEDGER_PATH

if typ.TYPE_CHECKING:
    import pathlib


VERDICT_ORDER: typ.Final = (
    "noncompliant",
    "indeterminate",
    "error",
    "compliant",
    "excluded",
)


def _latest_records(
    ledger: Ledger,
) -> dict[str, LedgerRecord]:
    latest: dict[str, LedgerRecord] = {}
    for record in ledger:
        latest[record["repository"]] = record
    return latest


# A table cell ends at an unescaped `|`, and a newline ends the row. Ledger
# prose — an exclusion reason from the manifest, an error detail from a tool,
# a finding message from the policy — is free text that may contain either.
_CELL_ESCAPES: typ.Final = str.maketrans({"\\": "\\\\", "|": "\\|"})


def _cell(value: str) -> str:
    """Return *value* rendered safely inside a Markdown table cell."""
    # `split()` with no argument folds every run of whitespace, newlines
    # included, so a multi-line detail becomes one line rather than breaking
    # the table. Backslash is escaped in the same pass as the pipe, so an
    # already-escaped pipe cannot be produced by escaping twice.
    return " ".join(value.split()).translate(_CELL_ESCAPES)


def _findings_summary(findings: list[FindingRecord]) -> str:
    """Render rule findings as one Markdown table-cell value."""
    parts = [
        f"{_cell(finding['rule_id'])} ({_cell(finding['verdict'])}) "
        f"{_cell(finding['message'])}"
        for finding in findings
    ]
    return "; ".join(parts) or "none"


def _finding_summary(record: LedgerRecord) -> str:
    """Return the final Markdown table-cell value for one ledger record."""
    # Every branch falls back to the same placeholder, `_findings_summary`
    # included. A blank final cell is ambiguous to a reader — it does not
    # distinguish "nothing to report" from "the detail is missing" — so each
    # verdict says which it is.
    if record["verdict"] == "excluded":
        return _cell(record.get("exclusion_reason") or "") or "none"
    if record["verdict"] == "error":
        return _cell(record.get("error_detail") or "") or "none"
    return _findings_summary(record["findings"])


def render_report(ledger_path: pathlib.Path = DEFAULT_LEDGER_PATH) -> str:
    """Render the campaign baseline report from the ledger.

    Parameters
    ----------
    ledger_path:
        Path to the append-only ledger file. Defaults to
        ``DEFAULT_LEDGER_PATH``.

    Returns
    -------
    str
        The rendered Markdown report. Where a repository has more than
        one ledger record, the latest one wins.

    """
    latest = _latest_records(_load_ledger(ledger_path))
    counts: dict[str, int] = {}
    rule_counts: dict[str, int] = {}
    for record in latest.values():
        counts[record["verdict"]] = counts.get(record["verdict"], 0) + 1
        for finding in record["findings"]:
            rule_id = finding["rule_id"]
            rule_counts[rule_id] = rule_counts.get(rule_id, 0) + 1

    lines = [
        "# Operation Parabellum baseline report",
        "",
        "Generated from `docs/parabellum/ledger.jsonl` by",
        "`python -m scripts.parabellum_sweep report`. Do not edit by hand.",
        "",
        f"Rule package: `{RULE_PACKAGE}` v{RULE_VERSION}; "
        f"makeutil `{MAKEUTIL_REV[:12]}`.",
        "",
        "## Summary",
        "",
    ]
    lines.extend(
        f"- {verdict}: {counts[verdict]}"
        for verdict in VERDICT_ORDER
        if verdict in counts
    )
    lines.extend(["", "Findings by rule:", ""])
    lines.extend(
        f"- {_cell(rule_id)}: {rule_counts[rule_id]}" for rule_id in sorted(rule_counts)
    )
    lines.extend(
        [
            "",
            "## Repositories",
            "",
            "Table 1: Latest verdict and findings per estate repository.",
            "",
            "| Repository | Verdict | Commit | Findings |",
            "| ---------- | ------- | ------ | -------- |",
        ]
    )
    for repository in sorted(latest):
        record = latest[repository]
        commit = (record["commit_sha"] or "")[:12]
        summary = _finding_summary(record)
        # Every cell is escaped, not only the free-text one. The ledger is an
        # editable file and its load path checks types, not charsets, so a
        # hand-edited `repository`, `verdict`, or `commit_sha` can carry a
        # pipe or a newline and break the row it sits in.
        lines.append(
            f"| {_cell(repository)} | {_cell(record['verdict'])} "
            f"| {_cell(commit)} | {summary} |"
        )
    lines.append("")
    return "\n".join(lines)
