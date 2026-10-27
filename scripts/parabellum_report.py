"""Markdown rendering of the Parabellum baseline report from the ledger."""

from __future__ import annotations

import dataclasses
import itertools as it
import typing as typ
import unicodedata as ud

from scripts.parabellum_ledger import (
    MAKEUTIL_REV,
    RULE_PACKAGE,
    FindingRecord,
    Ledger,
    LedgerRecord,
    _load_ledger,
    rule_version,
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


def _records_for_package(ledger: Ledger, package: str) -> Ledger:
    """Return only the records *package* wrote.

    The filter runs before the latest-record selection: picking the latest
    record first would let a later survey record hide the baseline's verdict
    for the same repository, and the baseline report would silently lose a
    repository it had audited.

    Returns
    -------
    Ledger
        The records *package* wrote, in ledger order.
    """
    return [record for record in ledger if record["rule_package"] == package]


def _versions(records: dict[str, LedgerRecord], package: str) -> str:
    """Return the package version or versions the rendered records carry.

    The ledger is append-only, so a report can cover records written by more
    than one version: a campaign re-runs an updated package and only the
    repositories it reached are re-stamped. Naming the current manifest alone
    would then describe records the report is not showing, which is how the
    checked-in report came to claim a version no record in the ledger held.

    Parameters
    ----------
    records:
        The latest record per repository, already filtered to *package*.
    package:
        The package those records belong to.

    Returns
    -------
    str
        `vX` for one version, `vX, vY` for several, and the current manifest
        version when the selection is empty and there is no record to read.
    """
    versions = sorted({record["rule_version"] for record in records.values()})
    if not versions:
        return f"v{rule_version(package)}"
    return ", ".join(f"v{version}" for version in versions)


def _makeutil_revisions(records: dict[str, LedgerRecord]) -> str:
    """Return the `makeutil` revisions the rendered records were read with.

    The same append-only reasoning as `_versions`: an audit stamped by another
    worktree, or run before the submodule moved, keeps the revision it really
    used. Naming the checked-out constant would credit every finding to a
    revision that never judged it.

    Parameters
    ----------
    records:
        The latest record per repository, already filtered to the package.

    Returns
    -------
    str
        The distinct revisions, abbreviated, in sorted order; the constant
        when the selection is empty and there is no record to read.
    """
    revisions = sorted({record["makeutil_rev"] for record in records.values()})
    if not revisions:
        return MAKEUTIL_REV[:12]
    return ", ".join(revision[:12] for revision in revisions)


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


# Severity labels in descending order of weight. A rule can be reached through
# several records with different labels, and the row must not understate it.
_SEVERITY_ORDER: typ.Final = ("error", "warning", "info")


@dataclasses.dataclass(slots=True)
class _RuleTally:
    """Distinct-repository evidence for one rule across the selected records.

    Occurrence counts say how often a rule fired; these say how many
    repositories it reaches, which is the number a survey reports and the
    number a promotion decision is made on.
    """

    severities: set[str] = dataclasses.field(default_factory=set)
    noncompliant: set[str] = dataclasses.field(default_factory=set)
    indeterminate_only: set[str] = dataclasses.field(default_factory=set)

    def severity(self) -> str:
        """Return the highest severity recorded for this rule.

        Returns
        -------
        str
            The weightiest recorded severity, or ``"error"`` if none was
            recorded — the severities are filled from the same findings that
            create the tally, so this branch is defensive only.
        """
        for severity in _SEVERITY_ORDER:
            if severity in self.severities:
                return severity
        return _SEVERITY_ORDER[0]

    def would_fail(self) -> set[str]:
        """Return the repositories promotion to error would block.

        Any non-compliant or unresolved evidence blocks once the package's
        findings carry error severity, whether the finding is labelled
        ``warning`` today or not. A repository appears once however many
        findings it has for the rule.

        Returns
        -------
        set[str]
            The distinct affected repositories.
        """
        return self.noncompliant | self.indeterminate_only


def _tally_rules(latest: dict[str, LedgerRecord]) -> dict[str, _RuleTally]:
    """Count distinct repositories per rule, ignoring compliant evidence."""
    tallies: dict[str, _RuleTally] = {}
    for repository, record in latest.items():
        noncompliant_rules: set[str] = set()
        indeterminate_rules: set[str] = set()
        # A repository can report more than one finding for the same rule —
        # two cargo spawns in two files — so the sets deduplicate it.
        for finding in record["findings"]:
            if finding["verdict"] not in ("noncompliant", "indeterminate"):
                continue
            rule_id = finding["rule_id"]
            tally = tallies.setdefault(rule_id, _RuleTally())
            tally.severities.add(finding["severity"])
            if finding["verdict"] == "noncompliant":
                tally.noncompliant.add(repository)
                noncompliant_rules.add(rule_id)
            else:
                indeterminate_rules.add(rule_id)
        # A repository is "indeterminate-only" for a rule when that is its
        # strongest evidence: a proven violation is not also unresolved.
        for rule_id in indeterminate_rules - noncompliant_rules:
            tallies[rule_id].indeterminate_only.add(repository)
    return tallies


def _rule_rows(tallies: dict[str, _RuleTally]) -> list[tuple[str, ...]]:
    """Render one table row per rule that has any blocking evidence."""
    rows = [
        (
            "Rule",
            "Severity",
            "Noncompliant repositories",
            "Indeterminate-only repositories",
            "Would fail",
        )
    ]
    for rule_id in sorted(tallies):
        tally = tallies[rule_id]
        if not tally.noncompliant and not tally.indeterminate_only:
            continue
        rows.append((
            _cell(rule_id),
            _cell(tally.severity()),
            str(len(tally.noncompliant)),
            str(len(tally.indeterminate_only)),
            str(len(tally.would_fail())),
        ))
    return rows


def render_report(
    ledger_path: pathlib.Path = DEFAULT_LEDGER_PATH,
    package: str = RULE_PACKAGE,
) -> str:
    """Render the campaign baseline report from the ledger.

    Parameters
    ----------
    ledger_path:
        Path to the append-only ledger file. Defaults to
        ``DEFAULT_LEDGER_PATH``.
    package:
        Rule package to report on. Records written by other packages are
        filtered out before the latest record per repository is chosen, so
        campaigns sharing the ledger do not overwrite each other's reports.

    Returns
    -------
    str
        The rendered Markdown report. Where a repository has more than
        one ledger record for *package*, the latest one wins.

    """
    latest = _latest_records(_records_for_package(_load_ledger(ledger_path), package))
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
            f"Rule package: `{package}` {_versions(latest, package)}; "
            f"makeutil `{_makeutil_revisions(latest)}`."
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

    rule_rows = _rule_rows(_tally_rules(latest))
    if len(rule_rows) > 1:
        # An empty selection has no table to caption: a caption above nothing
        # reads as a rendering bug, and the first run of a new campaign has no
        # findings to tabulate.
        lines.extend([
            "## Repositories by rule",
            "",
            "Table 2: Distinct repositories with blocking evidence per rule.",
            "",
            *_aligned_table(rule_rows),
            "",
        ])
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
    """Render *rows* (header first) as a column-aligned Markdown table.

    Returns
    -------
    list[str]
        The table's lines, or an empty list when there are no rows: an empty
        selection has nothing to caption or head.
    """
    if not rows:
        return []
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
