"""Unit tests for the Parabellum baseline report renderer."""

from __future__ import annotations

import json
import typing as typ

import pytest

from scripts import parabellum_sweep as sweep

if typ.TYPE_CHECKING:
    import pathlib


@pytest.fixture
def ledger_path(tmp_path: pathlib.Path) -> pathlib.Path:
    """Return a ledger path inside the test's temporary directory."""
    return tmp_path / "ledger.jsonl"


class TestReport:
    """Baseline report generation from the ledger."""

    def _record(
        self,
        repository: str,
        verdict: str,
        **extra: str,
    ) -> dict[str, typ.Any]:
        finding = {
            "rule_id": "QG-001",
            "severity": "error",
            "verdict": "noncompliant",
            "path": "Makefile",
            "line": 1,
            "message": "gate-critical variable uses ?=",
        }
        record: dict[str, typ.Any] = {
            "schema_version": 1,
            "repository": repository,
            "commit_sha": "b" * 40,
            "audited_at": "2026-07-19T16:00:00Z",
            "rule_package": "rust-makefile-baseline",
            "rule_version": "0.1.0",
            "makeutil_rev": sweep.MAKEUTIL_REV,
            "verdict": verdict,
            "findings": [finding] if verdict == "noncompliant" else [],
        }
        record.update(extra)
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
                exclusion_reason="not ready",
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
