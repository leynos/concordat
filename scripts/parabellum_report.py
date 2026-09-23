"""Markdown rendering of the Parabellum baseline report from the ledger."""

from __future__ import annotations

import itertools as it
import typing as typ
import unicodedata as ud

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

# Unicode general categories that occupy no column: non-spacing and enclosing
# marks, and format characters such as the emoji variation selector.
_ZERO_WIDTH_CATEGORIES: typ.Final = frozenset({"Mn", "Me", "Cf"})

if typ.TYPE_CHECKING:
    import collections.abc as cabc
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
        (
            f"Rule package: `{RULE_PACKAGE}` v{RULE_VERSION}; "
            f"makeutil `{MAKEUTIL_REV[:12]}`."
        ),
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
    lines.extend([
        "",
        "## Repositories",
        "",
        "Table 1: Latest verdict and findings per estate repository.",
        "",
    ])
    rows = [("Repository", "Verdict", "Commit", "Findings")]
    for repository in sorted(latest):
        record = latest[repository]
        commit = (record["commit_sha"] or "")[:12]
        # Every cell is escaped, not only the free-text one. The ledger is an
        # editable file and its load path checks types, not charsets, so a
        # hand-edited `repository`, `verdict`, or `commit_sha` can carry a
        # pipe or a newline and break the row it sits in.
        rows.append((
            _cell(repository),
            _cell(record["verdict"]),
            _cell(commit),
            _finding_summary(record),
        ))
    lines.extend(_aligned_table(rows))
    lines.append("")
    return "\n".join(lines)


def _display_width(text: str) -> int:
    """Return *text*'s rendered column count, as `mdtablefix` measures it."""
    # `mdtablefix` sizes columns with the `unicode-width` crate, so a cell
    # counted by code points misaligns on CJK, emoji, and combining marks and
    # the formatter then rewrites the checked-in report. Zero-width marks and
    # format characters contribute nothing; East Asian wide and fullwidth
    # characters contribute two columns.
    width = 0
    for character in text:
        if ud.category(character) in _ZERO_WIDTH_CATEGORIES:
            continue
        width += 2 if ud.east_asian_width(character) in "WF" else 1
    return width


def _pad(text: str, width: int) -> str:
    """Return *text* padded with spaces to *width* rendered columns."""
    return text + " " * max(width - _display_width(text), 0)


def _aligned_table(rows: cabc.Sequence[tuple[str, ...]]) -> list[str]:
    """Render *rows* (header first) as a column-aligned Markdown table."""
    # Columns are padded to their widest cell, in the shape `mdtablefix`
    # produces, so the generated report already satisfies `make check-fmt`
    # and the checked-in snapshot is not rewritten by the formatter.
    widths = [
        max(_display_width(row[column]) for row in rows)
        for column in range(len(rows[0]))
    ]
    header, *body = rows

    def line(cells: tuple[str, ...]) -> str:
        return (
            "| " + " | ".join(it.starmap(_pad, zip(cells, widths, strict=True))) + " |"
        )

    delimiter = tuple("-" * width for width in widths)
    return [line(header), line(delimiter), *(line(row) for row in body)]
