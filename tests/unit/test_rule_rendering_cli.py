"""Unit tests for result rendering and the `artefact rule run` CLI surface.

`render_table` and `render_json` format a `RuleRunResult`; the CLI chooses
between them and maps the verdict onto an exit code. Rendering is separate
from evaluation, so it is tested apart from the runner.
"""

from __future__ import annotations

import json
import typing as typ

import pytest

from concordat.rules.runner import (
    Finding,
    RuleRunResult,
    render_json,
    render_table,
)
from tests.unit.rule_test_support import MINIMAL_REPORT, _write_checkout

if typ.TYPE_CHECKING:
    import pathlib

    from tests.conftest import CmdMox


class TestRendering:
    """Table and JSON rendering of rule run results."""

    @pytest.fixture
    def result(self) -> RuleRunResult:
        """Provide a single-finding noncompliant result."""
        finding = Finding(
            rule_id="QG-001",
            severity="error",
            verdict="noncompliant",
            path="Makefile",
            line=1,
            message="gate-critical variable uses ?=",
        )
        return RuleRunResult(
            rule_package="rust-makefile-baseline",
            verdict="noncompliant",
            findings=(finding,),
        )

    def test_table_lists_findings(self, result: RuleRunResult) -> None:
        """Table lists findings."""
        table = render_table(result)
        assert "QG-001" in table, table
        assert "Makefile:1" in table, table
        assert "noncompliant" in table, table

    def test_json_round_trips(self, result: RuleRunResult) -> None:
        """Json round trips."""
        document = json.loads(render_json(result))
        assert document["rule_package"] == "rust-makefile-baseline", document
        assert document["verdict"] == "noncompliant", document
        assert document["findings"][0]["rule_id"] == "QG-001", document


class TestRuleRunCli:
    """End-to-end wiring of the `artefact rule run` CLI command."""

    def test_json_format_emits_structured_document(
        self,
        tmp_path: pathlib.Path,
        cmd_mox: CmdMox,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--format json` prints a parseable rule-run document and exits 0."""
        from concordat import cli

        _write_checkout(tmp_path, cargo=True, makefile=True)
        cmd_mox.mock("makeutil").returns(stdout=json.dumps(MINIMAL_REPORT))
        clean_doc = json.dumps(
            [
                {
                    "filename": "envelope.json",
                    "namespace": "canon.lint_rules.rust_makefile_baseline",
                    "successes": 14,
                }
            ]
        )
        cmd_mox.mock("conftest").returns(exit_code=0, stdout=clean_doc)
        cmd_mox.replay()
        try:
            exit_code = cli.main(
                [
                    "artefact",
                    "rule",
                    "run",
                    "rust-makefile-baseline",
                    "--repo",
                    str(tmp_path),
                    "--format",
                    "json",
                ]
            )
        except SystemExit as exc:
            exit_code = int(exc.code or 0)
        cmd_mox.verify()
        captured = capsys.readouterr()
        assert exit_code == 0, captured.out
        document = json.loads(captured.out)
        assert document["rule_package"] == "rust-makefile-baseline", document
        assert document["verdict"] == "compliant", document
        assert document["findings"] == [], document
