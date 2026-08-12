"""Unit tests for the Parabellum baseline report renderer."""

from __future__ import annotations

import json
import typing as typ

import pytest

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
    ) -> sweep.LedgerRecord:
        """Build one ledger record, optionally carrying an excluded/error detail.

        `detail` is the production `LedgerOptionalFields`, so a fixture cannot
        introduce a key the ledger schema does not define. `audited_at` is a
        *required* field and gets its own parameter: the latest-record test
        varies it, which is not the same thing as supplying an optional detail.

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
        record: sweep.LedgerRecord = {
            "schema_version": 1,
            "repository": repository,
            "commit_sha": "b" * 40,
            "audited_at": audited_at,
            "rule_package": "rust-makefile-baseline",
            "rule_version": "0.1.0",
            "makeutil_rev": sweep.MAKEUTIL_REV,
            "verdict": verdict,
            "findings": [finding] if verdict == "noncompliant" else [],
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

        row = next(
            line for line in report.splitlines() if line.startswith("| leynos/alpha ")
        )
        assert row.rstrip().endswith("| none |"), row
        assert row.count("|") == 5, f"the row should keep four cells: {row}"

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
        assert "| leynos/alpha | compliant |" in report, (
            "alpha's latest (compliant) record should win over its earlier one"
        )
        assert "| leynos/beta | indeterminate |" in report, (
            "beta should be reported as indeterminate"
        )
        assert "| leynos/gamma | excluded |" in report, (
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
