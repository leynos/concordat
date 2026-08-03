"""Snapshot tests for the three rendered outputs users actually read.

A snapshot catches an unintended reformat that no targeted assertion would;
the semantic assertions alongside each one catch a snapshot updated without
thought. The inputs are fixed values, and `_base_record`'s timestamp never
reaches any of these renderers, so the outputs are deterministic.

No credential or token value appears in any snapshot.
"""

from __future__ import annotations

import json
import pathlib
import typing as typ

import pytest

from concordat.rules import Finding, RuleRunResult, render_json, render_table
from scripts import parabellum_sweep as sweep

SNAPSHOTS = pathlib.Path(__file__).parent / "snapshots"

RESULT: typ.Final = RuleRunResult(
    rule_package="rust-makefile-baseline",
    verdict="noncompliant",
    findings=(
        Finding(
            "FP-003",
            "error",
            "noncompliant",
            "Makefile",
            12,
            "required target 'test' is not defined unconditionally",
        ),
        Finding(
            "QG-001",
            "error",
            "noncompliant",
            "Makefile",
            27,
            "the lint recipe ignores errors, so the gate cannot fail the build",
        ),
        Finding(
            "QG-001",
            "warning",
            "indeterminate",
            "Makefile",
            0,
            "an include directive makes the gate unprovable",
        ),
    ),
)


def _snapshot(name: str) -> str:
    """Return the checked-in snapshot *name*."""
    return (SNAPSHOTS / name).read_text(encoding="utf-8")


class TestRuleRunRendering:
    """`render_table` and `render_json` keep a stable shape."""

    def test_table_matches_the_snapshot(self) -> None:
        """The human-readable table is unchanged."""
        assert render_table(RESULT) == _snapshot("render_table.txt")

    def test_table_states_the_verdict_and_every_finding(self) -> None:
        """Semantics, not just bytes: the verdict and each rule appear."""
        rendered = render_table(RESULT)
        assert "noncompliant" in rendered, rendered
        for finding in RESULT.findings:
            assert finding.rule_id in rendered, finding.rule_id
        assert RESULT.exit_code == 1, RESULT.exit_code

    def test_json_matches_the_snapshot(self) -> None:
        """The machine-readable document is unchanged."""
        assert render_json(RESULT) == _snapshot("render_json.json")

    def test_json_carries_the_key_fields(self) -> None:
        """The decoded document keeps the fields consumers depend on."""
        document = json.loads(render_json(RESULT))
        assert document["rule_package"] == "rust-makefile-baseline", document
        assert document["verdict"] == "noncompliant", document
        assert [f["rule_id"] for f in document["findings"]] == [
            "FP-003",
            "QG-001",
            "QG-001",
        ], document


class TestReportRendering:
    """`render_report` keeps a stable Markdown shape."""

    @pytest.fixture
    def ledger_path(self, tmp_path: pathlib.Path) -> pathlib.Path:
        """Write a fixed three-repository ledger and return its path."""
        records = [
            sweep._excluded_record(
                "leynos/gauss", "test-framework migration in flight"
            ),
            {
                **sweep._base_record("leynos/alpha"),
                "commit_sha": "a" * 40,
                "verdict": "compliant",
                "findings": [],
            },
            {
                **sweep._base_record("leynos/beta"),
                "commit_sha": "b" * 40,
                "verdict": "noncompliant",
                "findings": [
                    {
                        "rule_id": "QG-001",
                        "severity": "error",
                        "verdict": "noncompliant",
                        "path": "Makefile",
                        "line": 9,
                        "message": "soft-skipped lint gate",
                    }
                ],
            },
        ]
        path = tmp_path / "ledger.jsonl"
        path.write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )
        return path

    def test_report_matches_the_snapshot(self, ledger_path: pathlib.Path) -> None:
        """The baseline report is unchanged.

        `_base_record` stamps a timestamp, but the report never renders one,
        so nothing needs normalizing for this to be stable.
        """
        assert sweep.render_report(ledger_path) == _snapshot("render_report.md")

    def test_report_counts_each_verdict(self, ledger_path: pathlib.Path) -> None:
        """Semantics: every repository is summarized under its verdict."""
        report = sweep.render_report(ledger_path)
        for line in ("compliant: 1", "noncompliant: 1", "excluded: 1"):
            assert f"- {line}" in report, report
        for repository in ("leynos/alpha", "leynos/beta", "leynos/gauss"):
            assert repository in report, repository

    def test_no_snapshot_contains_a_secret(self) -> None:
        """No credential name or value may be captured in a snapshot."""
        for path in sorted(SNAPSHOTS.iterdir()):
            body = path.read_text(encoding="utf-8")
            for marker in ("GITHUB_TOKEN", "SECRET", "ghp_", "AWS_"):
                assert marker not in body, f"{path.name} contains {marker!r}"
