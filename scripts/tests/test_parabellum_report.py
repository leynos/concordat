"""Unit tests for the Parabellum baseline report renderer."""

from __future__ import annotations

import json
import typing as typ

import pytest
from hypothesis import given
from hypothesis import strategies as st

from scripts import parabellum_report as report
from scripts import parabellum_sweep as sweep

if typ.TYPE_CHECKING:
    import pathlib


class TestReport:
    """Baseline report generation from the ledger."""

    def _record(
        self,
        repository: str,
        verdict: str,
        *,
        audited_at: str = "2026-07-19T16:00:00Z",
        detail: sweep.LedgerOptionalFields | None = None,
        package: str = sweep.RULE_PACKAGE,
        version: str = "0.1.0",
        findings: list[sweep.FindingRecord] | None = None,
    ) -> sweep.LedgerRecord:
        """Build one ledger record, optionally carrying an excluded/error detail.

        `detail` is the production `LedgerOptionalFields`, so a fixture cannot
        introduce a key the ledger schema does not define. `audited_at` is a
        *required* field and gets its own parameter: the latest-record test
        varies it, which is not the same thing as supplying an optional detail.
        `package` and `version` choose which campaign the record belongs to;
        `findings` replaces the default single-finding list where a test needs
        a different shape.

        Returns
        -------
        sweep.LedgerRecord
            A complete ledger record for the test.
        """
        finding: sweep.FindingRecord = {
            "rule_id": "QG-001",
            "severity": "error",
            "verdict": "noncompliant",
            "path": "Makefile",
            "line": 1,
            "message": "gate-critical variable uses ?=",
        }
        if findings is None:
            findings = [finding] if verdict == "noncompliant" else []
        record: sweep.LedgerRecord = {
            "schema_version": 1,
            "repository": repository,
            "commit_sha": "b" * 40,
            "audited_at": audited_at,
            "rule_package": package,
            "rule_version": version,
            "makeutil_rev": sweep.MAKEUTIL_REV,
            "verdict": verdict,
            "findings": findings,
        }
        # Keyed explicitly rather than by update: `is not None` keeps a blank
        # detail distinct from an absent one, which is exactly what the
        # `-blank` and `-without-` cases below exist to tell apart.
        if detail is not None:
            if (reason := detail.get("exclusion_reason")) is not None:
                record["exclusion_reason"] = reason
            if (error := detail.get("error_detail")) is not None:
                record["error_detail"] = error
        return record

    def test_a_caption_precedes_the_repositories_table(
        self,
        ledger_path: pathlib.Path,
    ) -> None:
        """The repositories table is captioned, immediately before its header.

        The caption has to sit between the heading and the header row: a
        caption after the header would be read as a table row.
        """
        lines = sweep.render_report(ledger_path).splitlines()
        heading = lines.index("## Repositories")
        header = next(
            i for i, line in enumerate(lines) if line.startswith("| Repository ")
        )

        caption = "Table 1: Latest verdict and findings per estate repository."
        assert lines[heading + 2] == caption, lines[heading : header + 1]
        assert header == heading + 4, (
            f"expected caption then a blank line before the header: "
            f"{lines[heading : header + 1]}"
        )

    @staticmethod
    def _row_cells(report: str, repository: str) -> list[str]:
        """Return the data cells of *repository*'s row in the rendered table."""
        row = next(
            line for line in report.splitlines() if line.startswith(f"| {repository} ")
        )
        return [cell.strip() for cell in row.strip().strip("|").split("|")]

    @pytest.mark.parametrize(
        ("verdict", "extra", "expected_detail"),
        [
            pytest.param(
                "excluded",
                {"exclusion_reason": "not ready"},
                "not ready",
                id="excluded-with-reason",
            ),
            # The absent case renders the "none" placeholder rather than an
            # empty cell: `_finding_summary` falls back for every verdict, so a
            # blank cell cannot distinguish "nothing to report" from "the
            # detail is missing".
            pytest.param("excluded", {}, "none", id="excluded-without-reason"),
            pytest.param(
                "error",
                {"error_detail": "conftest is required"},
                "conftest is required",
                id="error-with-detail",
            ),
        ],
    )
    def test_optional_detail_fills_the_final_cell(
        self,
        ledger_path: pathlib.Path,
        verdict: str,
        extra: sweep.LedgerOptionalFields,
        expected_detail: str,
    ) -> None:
        """The row keeps four cells, and the last carries the detail."""
        ledger_path.write_text(
            json.dumps(self._record("leynos/alpha", verdict, detail=extra)) + "\n",
            encoding="utf-8",
        )

        cells = self._row_cells(sweep.render_report(ledger_path), "leynos/alpha")

        assert len(cells) == 4, f"expected four data cells, got {cells}"
        assert cells[0] == "leynos/alpha", cells
        assert cells[1] == verdict, cells
        assert cells[3] == expected_detail, cells

    @pytest.mark.parametrize(
        ("verdict", "extra"),
        [
            pytest.param("excluded", {}, id="excluded-without-reason"),
            pytest.param("excluded", {"exclusion_reason": ""}, id="excluded-blank"),
            pytest.param("error", {}, id="error-without-detail"),
            pytest.param("error", {"error_detail": ""}, id="error-blank"),
        ],
    )
    def test_absent_detail_renders_a_visible_placeholder(
        self,
        ledger_path: pathlib.Path,
        verdict: str,
        extra: sweep.LedgerOptionalFields,
    ) -> None:
        """A missing or empty detail still fills the final table cell.

        A blank cell does not distinguish "nothing to report" from "the
        detail is missing", so every branch says which it is.
        """
        ledger_path.write_text(
            json.dumps(self._record("leynos/alpha", verdict, detail=extra)) + "\n",
            encoding="utf-8",
        )

        report = sweep.render_report(ledger_path)

        cells = self._row_cells(report, "leynos/alpha")
        assert cells[-1] == "none", cells
        assert len(cells) == 4, f"the row should keep four cells: {cells}"

    def test_report_command_writes_the_rendered_report(
        self,
        ledger_path: pathlib.Path,
        tmp_path: pathlib.Path,
    ) -> None:
        """`report_command` writes what `render_report` returns, creating dirs.

        The renderer is covered above; what is untested is the command around
        it. The output path is deliberately nested under a directory that does
        not exist: the command is run against a fresh checkout, so it must
        create the report's parent rather than fail on it.
        """
        ledger_path.write_text(
            "".join(
                json.dumps(record) + "\n"
                for record in (
                    self._record("leynos/alpha", "compliant"),
                    self._record("leynos/beta", "noncompliant"),
                )
            ),
            encoding="utf-8",
        )
        output_path = tmp_path / "docs" / "parabellum" / "baseline-report.md"
        assert not output_path.parent.exists(), "the parent must start absent"

        status = sweep.report_command(ledger=ledger_path, output=output_path)

        assert status == 0, f"the report command should succeed, got {status}"
        assert output_path.is_file(), f"{output_path} should have been written"
        assert output_path.read_text(encoding="utf-8") == sweep.render_report(
            ledger_path
        ), "the written file should be exactly the rendered report"

    @staticmethod
    def _delimiter_count(row: str) -> int:
        """Return the number of cell delimiters, ignoring escaped pipes.

        A bare `row.count("|")` would count the escaping as a delimiter and
        so could not tell a broken row from a correctly escaped one.

        Returns
        -------
        int
            The number of unescaped cell delimiters in *row*.
        """
        count = 0
        escaped = False
        for character in row:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == "|":
                count += 1
        return count

    @pytest.mark.parametrize(
        ("detail", "expected"),
        [
            pytest.param(
                "breaks | the | row",
                r"breaks \| the \| row",
                id="pipes-are-escaped",
            ),
            pytest.param(
                "first line\nsecond line",
                "first line second line",
                id="newlines-are-folded",
            ),
            pytest.param(
                r"a \ backslash",
                r"a \\ backslash",
                id="backslashes-are-escaped",
            ),
            pytest.param(
                r"already \| escaped",
                r"already \\\| escaped",
                id="an-escape-is-not-reapplied",
            ),
        ],
    )
    def test_free_text_cannot_break_the_table(
        self,
        ledger_path: pathlib.Path,
        detail: str,
        expected: str,
    ) -> None:
        """Ledger prose is escaped before it reaches a table cell.

        An exclusion reason comes from the manifest and an error detail from a
        tool, so both are free text. An unescaped `|` ends the cell early and
        a newline ends the row, silently corrupting every column after it.
        """
        ledger_path.write_text(
            json.dumps(
                self._record("leynos/alpha", "error", detail={"error_detail": detail})
            )
            + "\n",
            encoding="utf-8",
        )

        report = sweep.render_report(ledger_path)
        row = next(
            line for line in report.splitlines() if line.startswith("| leynos/alpha ")
        )

        assert self._delimiter_count(row) == 5, f"the row should keep four cells: {row}"
        assert expected in row, f"expected {expected!r} in {row!r}"
        assert "\n" not in row.strip(), row

    def test_every_finding_reaches_the_cell_in_ledger_order(
        self,
        ledger_path: pathlib.Path,
    ) -> None:
        """A record's findings are all rendered, in the order stored.

        The two special verdicts read a single optional field; every other
        verdict folds a whole list into one cell, so a dropped or reordered
        finding is the failure mode this pins.
        """
        record = self._record("leynos/alpha", "noncompliant")
        record["findings"] = [
            {
                "rule_id": "FP-003",
                "severity": "error",
                "verdict": "noncompliant",
                "path": "Makefile",
                "line": 12,
                "message": "required target 'test' is not defined",
            },
            {
                "rule_id": "QG-001",
                "severity": "warning",
                "verdict": "indeterminate",
                "path": "Makefile",
                "line": 27,
                "message": "an include directive makes the gate unprovable",
            },
        ]
        ledger_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

        cells = self._row_cells(sweep.render_report(ledger_path), "leynos/alpha")

        expected = (
            "FP-003 (noncompliant) required target 'test' is not defined; "
            "QG-001 (indeterminate) an include directive makes the gate unprovable"
        )
        assert cells[3] == expected, (
            f"both findings should render as `<rule_id> (<verdict>) <message>` "
            f"in ledger order, got {cells[3]!r}"
        )

    @pytest.mark.parametrize(
        "verdict",
        [
            pytest.param("compliant", id="compliant"),
            pytest.param("indeterminate", id="indeterminate"),
        ],
    )
    def test_a_verdict_without_findings_renders_the_placeholder(
        self,
        ledger_path: pathlib.Path,
        verdict: str,
    ) -> None:
        """An empty findings list fills the cell rather than leaving it blank.

        The placeholder is not only for the two special verdicts: a compliant
        record has nothing to list, and a blank cell would not say so.
        """
        record = self._record("leynos/alpha", verdict)
        record["findings"] = []
        ledger_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

        cells = self._row_cells(sweep.render_report(ledger_path), "leynos/alpha")

        assert cells[3] == "none", (
            f"a verdict with no findings should render the placeholder, "
            f"got {cells[3]!r}"
        )

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            pytest.param("repository", "leynos/a | b", id="repository"),
            pytest.param("verdict", "compliant | x", id="verdict"),
            pytest.param("commit_sha", "ab|cd" + "e" * 35, id="commit-sha"),
        ],
    )
    def test_a_hand_edited_identifier_cannot_break_the_row(
        self,
        ledger_path: pathlib.Path,
        field: str,
        value: str,
    ) -> None:
        """Identifier cells are escaped too, not only the free-text one.

        The manifest patterns constrain these on the way in, but they govern
        manifest input, not the ledger — whose load path checks types, not
        charsets. An edited file can therefore carry a pipe here.
        """
        record = self._record("leynos/alpha", "compliant")
        # Cast for the dynamic key: the parametrized field name is a `str`,
        # and a TypedDict admits only literal keys.
        typ.cast("dict[str, object]", record)[field] = value
        ledger_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

        report = sweep.render_report(ledger_path)
        row = next(
            line
            for line in report.splitlines()
            if line.startswith("| ")
            and "compliant" in line
            and "Repository" not in line
        )

        assert self._delimiter_count(row) == 5, f"the row should keep four cells: {row}"

    def test_a_hand_edited_rule_id_cannot_inject_a_summary_line(
        self,
        ledger_path: pathlib.Path,
    ) -> None:
        """The findings-by-rule counts escape their rule identifier."""
        record = self._record("leynos/alpha", "noncompliant")
        record["findings"] = [
            {
                "rule_id": "QG-001\n- injected: 99",
                "severity": "error",
                "verdict": "noncompliant",
                "path": "Makefile",
                "line": 1,
                "message": "m",
            }
        ]
        ledger_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

        report = sweep.render_report(ledger_path)

        # `_cell` folds the newline, so the text survives inside the one
        # legitimate count line rather than becoming a line of its own.
        injected = [
            line for line in report.splitlines() if line.startswith("- injected")
        ]
        assert not injected, f"the rule id created its own line: {injected}"
        assert any(
            line.startswith("- QG-001 - injected: 99:") for line in report.splitlines()
        ), report

    def test_table_columns_are_aligned(self, tmp_path: pathlib.Path) -> None:
        """Every table line pads its cells to the column's widest cell.

        The generated report then already satisfies `mdtablefix --check`, so
        `make check-fmt` does not rewrite the checked-in snapshot.
        """
        ledger_path = tmp_path / "ledger.jsonl"
        records = [
            self._record("leynos/alpha", "compliant"),
            self._record("leynos/beta-longer-name", "indeterminate"),
        ]
        ledger_path.write_text("".join(json.dumps(record) + "\n" for record in records))
        report = sweep.render_report(ledger_path)
        # Each table is aligned on its own, so the assertion is per contiguous
        # block: two tables in one report need not share a line length.
        for table in self._table_blocks(report):
            assert len({len(line) for line in table}) == 1, table
            header, delimiter = table[0], table[1]
            for cell, dashes in zip(
                header.split("|"), delimiter.split("|"), strict=True
            ):
                assert len(cell) == len(dashes), (header, delimiter)
                assert set(dashes.strip()) <= {"-"}, delimiter

    @staticmethod
    def _table_blocks(report: str) -> list[list[str]]:
        """Return each contiguous run of table lines in *report*.

        Returns
        -------
        list[list[str]]
            One list of table lines per table in the report.
        """
        blocks: list[list[str]] = []
        current: list[str] = []
        for line in report.splitlines():
            if line.startswith("|"):
                current.append(line)
            elif current:
                blocks.append(current)
                current = []
        if current:
            blocks.append(current)
        return blocks

    def test_report_uses_latest_record_per_repository(
        self,
        ledger_path: pathlib.Path,
    ) -> None:
        """The latest ledger record per repository wins."""
        records = [
            self._record("leynos/alpha", "noncompliant"),
            self._record(
                "leynos/alpha",
                "compliant",
                audited_at="2026-07-19T17:00:00Z",
            ),
            self._record("leynos/beta", "indeterminate"),
            self._record(
                "leynos/gamma",
                "excluded",
                detail={"exclusion_reason": "not ready"},
            ),
        ]
        ledger_path.write_text("".join(json.dumps(record) + "\n" for record in records))
        report = sweep.render_report(ledger_path)
        assert self._row_cells(report, "leynos/alpha")[1] == "compliant", (
            "alpha's latest (compliant) record should win over its earlier one"
        )
        assert self._row_cells(report, "leynos/beta")[1] == "indeterminate", (
            "beta should be reported as indeterminate"
        )
        assert self._row_cells(report, "leynos/gamma")[1] == "excluded", (
            "gamma should be reported as excluded"
        )
        assert "compliant: 1" in report, (
            "the summary should count one compliant repository"
        )
        assert "indeterminate: 1" in report, (
            "the summary should count one indeterminate repository"
        )
        assert "excluded: 1" in report, (
            "the summary should count one excluded repository"
        )


SURVEY_PACKAGE = "nested-cargo-offline-tests"


def _finding(
    rule_id: str,
    *,
    severity: str = "warning",
    verdict: str = "noncompliant",
    message: str = "spawns cargo without offline evidence",
) -> sweep.FindingRecord:
    """Return one finding record for a report fixture."""
    return {
        "rule_id": rule_id,
        "severity": severity,
        "verdict": verdict,
        "path": "tests/spawn.rs",
        "line": 4,
        "message": message,
    }


class TestPackageFiltering:
    """The report can be narrowed to one rule package's records."""

    @staticmethod
    def _rule_cells(report: str, rule_id: str) -> list[str]:
        """Return the data cells of *rule_id*'s row in the per-rule table."""
        row = next(
            line for line in report.splitlines() if line.startswith(f"| {rule_id} ")
        )
        return [cell.strip() for cell in row.strip().strip("|").split("|")]

    def test_the_filter_applies_before_latest_record_selection(
        self,
        ledger_path: pathlib.Path,
    ) -> None:
        """Each package keeps its own latest record.

        Selecting the latest record first would let a later survey record hide
        the baseline's earlier verdict for the same repository, and the
        baseline report would silently lose a repository it audited.
        """
        baseline_bad = TestReport()._record("leynos/alpha", "noncompliant")
        survey_good = TestReport()._record(
            "leynos/alpha",
            "compliant",
            package=SURVEY_PACKAGE,
            audited_at="2026-07-19T17:00:00Z",
        )
        ledger_path.write_text(
            json.dumps(baseline_bad) + "\n" + json.dumps(survey_good) + "\n",
            encoding="utf-8",
        )

        baseline = sweep.render_report(ledger_path, package=sweep.RULE_PACKAGE)
        survey = sweep.render_report(ledger_path, package=SURVEY_PACKAGE)

        assert TestReport._row_cells(baseline, "leynos/alpha")[1] == "noncompliant", (
            "the baseline report should keep its own latest record"
        )
        assert TestReport._row_cells(survey, "leynos/alpha")[1] == "compliant", (
            "the survey report should keep its own latest record"
        )

    def test_a_mixed_ledger_reports_only_the_selected_package(
        self,
        ledger_path: pathlib.Path,
    ) -> None:
        """Records from another package do not appear in rows or summary counts."""
        records = [
            TestReport()._record("leynos/alpha", "noncompliant"),
            TestReport()._record("leynos/beta", "compliant"),
            TestReport()._record(
                "leynos/gamma",
                "noncompliant",
                package=SURVEY_PACKAGE,
                findings=[_finding("NC-007")],
            ),
        ]
        ledger_path.write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )

        report = sweep.render_report(ledger_path, package=SURVEY_PACKAGE)

        assert "leynos/alpha" not in report, report
        assert "leynos/beta" not in report, report
        assert "leynos/gamma" in report, report
        assert "- noncompliant: 1" in report, report
        assert "QG-001" not in report, (
            f"another package's rule should not be counted: {report}"
        )

    def test_the_default_selection_is_the_campaign_package(
        self,
        ledger_path: pathlib.Path,
    ) -> None:
        """The checked-in baseline report keeps meaning the baseline campaign.

        Survey records share the ledger, so an unfiltered default would mix
        both campaigns into a file titled after one of them.
        """
        records = [
            TestReport()._record("leynos/alpha", "compliant"),
            TestReport()._record(
                "leynos/gamma",
                "noncompliant",
                package=SURVEY_PACKAGE,
                findings=[_finding("NC-007")],
            ),
        ]
        ledger_path.write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )

        report = sweep.render_report(ledger_path)

        assert "leynos/alpha" in report, report
        assert "leynos/gamma" not in report, report

    def test_the_header_names_the_selected_package(
        self,
        ledger_path: pathlib.Path,
    ) -> None:
        """The header names the package whose records are rendered.

        The version is deliberately not the manifest's: naming the manifest
        would let a record the report does not show supply the header's
        version, and the assertion would pass without reading the ledger.
        """
        ledger_path.write_text(
            json.dumps(
                TestReport()._record(
                    "leynos/gamma",
                    "compliant",
                    package=SURVEY_PACKAGE,
                    version="9.9.9",
                )
            )
            + "\n",
            encoding="utf-8",
        )

        report = sweep.render_report(ledger_path, package=SURVEY_PACKAGE)

        assert f"Rule package: `{SURVEY_PACKAGE}` v9.9.9" in report, report
        assert sweep.RULE_PACKAGE not in report, report

    def test_the_header_names_the_versions_the_records_carry(
        self,
        ledger_path: pathlib.Path,
    ) -> None:
        """A report spanning two versions names both, not the manifest's.

        The ledger is append-only and a campaign re-runs an updated package
        over part of the estate, so the records the report renders can be
        stamped by two versions at once. Claiming the current manifest
        version would describe versions the report does not show.
        """
        ledger_path.write_text(
            "".join(
                json.dumps(record) + "\n"
                for record in (
                    TestReport()._record(
                        "leynos/alpha", "compliant", version="0.1.0"
                    ),
                    TestReport()._record(
                        "leynos/beta", "noncompliant", version="0.2.0"
                    ),
                )
            ),
            encoding="utf-8",
        )

        report = sweep.render_report(ledger_path)

        assert (
            f"Rule package: `{sweep.RULE_PACKAGE}` v0.1.0, v0.2.0" in report
        ), report
        assert f"v{sweep.rule_version(sweep.RULE_PACKAGE)}" not in report, report

    def test_the_header_falls_back_to_the_manifest_without_records(
        self,
        ledger_path: pathlib.Path,
    ) -> None:
        """An empty ledger has no record to name, so the manifest supplies one."""
        ledger_path.write_text("", encoding="utf-8")

        report = sweep.render_report(ledger_path)

        version = sweep.rule_version(sweep.RULE_PACKAGE)
        assert f"Rule package: `{sweep.RULE_PACKAGE}` v{version}" in report, report

    def test_an_empty_selection_renders_without_a_finding_table(
        self,
        ledger_path: pathlib.Path,
    ) -> None:
        """A package with no records yet renders a readable empty report.

        The survey's first run happens before any record exists, so the
        renderer must not assume a table has rows.
        """
        ledger_path.write_text(
            json.dumps(TestReport()._record("leynos/alpha", "compliant")) + "\n",
            encoding="utf-8",
        )

        report = sweep.render_report(ledger_path, package=SURVEY_PACKAGE)

        assert "leynos/alpha" not in report, report
        assert "Table 2" not in report, (
            f"an empty table should not be captioned: {report}"
        )
        assert not any(line.startswith("| Rule ") for line in report.splitlines()), (
            report
        )


class TestDistinctRepositoryCounts:
    """Per-rule counts deduplicate repositories and carry severity."""

    @staticmethod
    def _rule_cells(report: str, rule_id: str) -> list[str]:
        """Return the data cells of *rule_id*'s row in the per-rule table."""
        row = next(
            line for line in report.splitlines() if line.startswith(f"| {rule_id} ")
        )
        return [cell.strip() for cell in row.strip().strip("|").split("|")]

    def test_the_caption_precedes_the_per_rule_table(
        self,
        ledger_path: pathlib.Path,
    ) -> None:
        """The second table is captioned like the first, before its header."""
        ledger_path.write_text(
            json.dumps(
                TestReport()._record(
                    "leynos/gamma",
                    "noncompliant",
                    package=SURVEY_PACKAGE,
                    findings=[_finding("NC-007")],
                )
            )
            + "\n",
            encoding="utf-8",
        )

        lines = sweep.render_report(ledger_path, package=SURVEY_PACKAGE).splitlines()
        heading = lines.index("## Repositories by rule")
        header = next(i for i, line in enumerate(lines) if line.startswith("| Rule "))

        assert lines[heading + 2].startswith("Table 2:"), lines[heading : header + 1]
        assert header == heading + 4, lines[heading : header + 1]

    def test_one_repository_counts_once_however_many_findings_it_has(
        self,
        ledger_path: pathlib.Path,
    ) -> None:
        """Distinct-repository counts deduplicate; occurrence counts do not."""
        ledger_path.write_text(
            json.dumps(
                TestReport()._record(
                    "leynos/gamma",
                    "noncompliant",
                    package=SURVEY_PACKAGE,
                    findings=[
                        _finding("NC-007", message="first spawn"),
                        _finding("NC-007", message="second spawn"),
                    ],
                )
            )
            + "\n",
            encoding="utf-8",
        )

        report = sweep.render_report(ledger_path, package=SURVEY_PACKAGE)
        cells = self._rule_cells(report, "NC-007")

        assert cells[2] == "1", f"one repository should count once: {cells}"
        assert "- NC-007: 2" in report, (
            f"both occurrences should still be counted: {report}"
        )

    def test_a_warning_noncompliant_finding_counts_as_would_fail(
        self,
        ledger_path: pathlib.Path,
    ) -> None:
        """Warning-phase evidence is exactly the promotion decision's input.

        A `warning` noncompliant finding does not block today, but promoting
        the package to `error` is what the survey exists to justify, so the
        repository must be counted as one that would fail.
        """
        ledger_path.write_text(
            json.dumps(
                TestReport()._record(
                    "leynos/gamma",
                    "noncompliant",
                    package=SURVEY_PACKAGE,
                    findings=[_finding("NC-008", severity="warning")],
                )
            )
            + "\n",
            encoding="utf-8",
        )

        report = sweep.render_report(ledger_path, package=SURVEY_PACKAGE)
        cells = self._rule_cells(report, "NC-008")

        assert cells[1] == "warning", f"the severity should be shown: {cells}"
        assert cells[2] == "1", f"the repository is noncompliant: {cells}"
        assert cells[4] == "1", f"it would fail once promoted: {cells}"

    def test_indeterminate_only_evidence_is_counted_separately(
        self,
        ledger_path: pathlib.Path,
    ) -> None:
        """Unresolved evidence is reported apart from a noncompliance.

        A repository whose only evidence for a rule is indeterminate is not
        the same finding as one that provably violates it; both would block
        after promotion, but only the second is a defect to fix.
        """
        records = [
            TestReport()._record(
                "leynos/gamma",
                "noncompliant",
                findings=[_finding("NC-007")],
            ),
            TestReport()._record(
                "leynos/delta",
                "indeterminate",
                findings=[_finding("NC-007", verdict="indeterminate")],
            ),
        ]
        ledger_path.write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )

        report = sweep.render_report(ledger_path, package=sweep.RULE_PACKAGE)
        cells = self._rule_cells(report, "NC-007")

        assert cells[2] == "1", f"gamma is noncompliant: {cells}"
        assert cells[3] == "1", f"delta is indeterminate-only: {cells}"
        assert cells[4] == "2", f"both would block after promotion: {cells}"

    def test_a_compliant_finding_is_not_counted(
        self,
        ledger_path: pathlib.Path,
    ) -> None:
        """Only noncompliant and indeterminate evidence reaches the counts."""
        ledger_path.write_text(
            json.dumps(
                TestReport()._record(
                    "leynos/alpha",
                    "compliant",
                    findings=[_finding("QG-001", verdict="compliant")],
                )
            )
            + "\n",
            encoding="utf-8",
        )

        report = sweep.render_report(ledger_path, package=sweep.RULE_PACKAGE)

        assert not any(line.startswith("| QG-001 ") for line in report.splitlines()), (
            report
        )

    def test_counts_use_each_repositorys_latest_record(
        self,
        ledger_path: pathlib.Path,
    ) -> None:
        """A fixed repository is not still counted from its earlier record."""
        records = [
            TestReport()._record(
                "leynos/gamma",
                "noncompliant",
                findings=[_finding("NC-007")],
            ),
            TestReport()._record(
                "leynos/gamma",
                "compliant",
                audited_at="2026-07-19T17:00:00Z",
                findings=[],
            ),
        ]
        ledger_path.write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )

        report = sweep.render_report(ledger_path, package=sweep.RULE_PACKAGE)

        assert not any(line.startswith("| NC-007 ") for line in report.splitlines()), (
            f"the fixed repository should no longer be counted: {report}"
        )


class TestDisplayWidthAlignment:
    """Table columns are sized by rendered width, not by code-point count.

    `mdtablefix` sizes columns with the `unicode-width` crate. A renderer
    that counts code points pads a CJK, emoji, or combining-mark cell too
    narrowly, so `make check-fmt` rejects the checked-in report and
    `make fmt` rewrites it.
    """

    @staticmethod
    def _column_widths(line: str) -> list[int]:
        """Return the rendered width of each cell in one table line."""
        cells = line.removeprefix("| ").removesuffix(" |").split(" | ")
        return [report._display_width(cell) for cell in cells]

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            pytest.param("abc", 3, id="ascii"),
            pytest.param("\u65e5\u672c\u8a9e", 6, id="cjk-wide"),
            pytest.param("\uff41\uff42", 4, id="fullwidth"),
            pytest.param("e\u0301", 1, id="combining-acute"),
            pytest.param("\u26a0\ufe0f", 1, id="variation-selector"),
            pytest.param("", 0, id="empty"),
        ],
    )
    def test_display_width_of_one_cell(self, text: str, expected: int) -> None:
        """Each character class contributes its rendered column count."""
        assert report._display_width(text) == expected, text

    def test_wide_cells_align_with_the_delimiter(self) -> None:
        """Every row and the delimiter agree on each column's width.

        The oracle is `_display_width` applied to the rendered line rather
        than the source cells, so a padding bug cannot hide behind the same
        arithmetic that produced it.
        """
        rows = [
            ("Repository", "Verdict"),
            ("\u65e5\u672c\u8a9e\u30d7\u30ed\u30b8\u30a7\u30af\u30c8", "compliant"),
            ("cafe\u0301", "noncompliant"),
            ("\u26a0\ufe0f alert", "indeterminate"),
        ]
        lines = report._aligned_table(rows)
        widths = [self._column_widths(line) for line in lines]
        assert all(row == widths[0] for row in widths), lines
        assert widths[0] == [18, 13], widths[0]

    @given(
        st.lists(
            st.lists(
                st.text(
                    alphabet="ab \u65e5\u672c\u0301\ufe0f\u26a0",
                    max_size=6,
                ),
                min_size=2,
                max_size=2,
            ).map(tuple),
            min_size=2,
            max_size=5,
        )
    )
    def test_every_rendered_line_has_equal_column_widths(
        self, rows: list[tuple[str, ...]]
    ) -> None:
        """Whatever the cells, the rendered columns line up.

        The invariant holds over the whole input space, not only the cases
        the table above names: wide, combining, and format characters mixed
        freely with ASCII.
        """
        lines = report._aligned_table(rows)
        widths = [self._column_widths(line) for line in lines]
        assert all(row == widths[0] for row in widths), lines
