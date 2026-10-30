"""The exit contract for rule-run results.

A gate is a status word, not a verdict word. The verdict and the JSON document
keep saying exactly what the policy found; the exit status says only whether a
caller should stop. Warning-severity findings are advisory, so a result whose
findings are all warnings must not fail a gate — but it must still print the
finding, because the evidence is the point.
"""

from __future__ import annotations

import json
import typing as typ

import pytest

from concordat.rules.runner import (
    VERDICT_COMPLIANT,
    VERDICT_INDETERMINATE,
    VERDICT_NONCOMPLIANT,
    Finding,
    RuleRunResult,
    _overall_verdict,
    render_json,
    render_table,
)


def _finding(
    rule_id: str,
    severity: str,
    verdict: str,
    *,
    path: str = "tests/probe.rs",
    line: int = 1,
    message: str = "a finding",
) -> Finding:
    """Return one finding with the given severity and verdict."""
    return Finding(rule_id, severity, verdict, path, line, message)


def _result(*findings: Finding) -> RuleRunResult:
    """Return a result whose overall verdict is reduced from *findings*."""
    return RuleRunResult(
        rule_package="nested-cargo-offline-tests",
        verdict=_overall_verdict(findings),
        findings=findings,
    )


class TestExitCodePartitions:
    """Every verdict/severity partition of the exit contract."""

    def test_a_clean_run_exits_zero(self) -> None:
        """No findings means nothing to report and nothing to block."""
        result = _result()
        assert result.verdict == VERDICT_COMPLIANT
        assert result.exit_code == 0
        assert result.blocking is False

    @pytest.mark.parametrize(
        "verdict",
        [pytest.param(VERDICT_NONCOMPLIANT), pytest.param(VERDICT_INDETERMINATE)],
    )
    def test_error_findings_block(self, verdict: str) -> None:
        """An error-severity finding of either verdict fails the gate."""
        result = _result(_finding("NC-007", "error", verdict))
        assert result.exit_code == 1
        assert result.blocking is True

    @pytest.mark.parametrize(
        "verdict",
        [pytest.param(VERDICT_NONCOMPLIANT), pytest.param(VERDICT_INDETERMINATE)],
    )
    def test_warning_only_findings_do_not_block(self, verdict: str) -> None:
        """Advisory evidence must not stop a caller that only asked for status.

        The verdict still records what was found, so an operator, the JSON
        document, and the ledger all keep the evidence.
        """
        result = _result(_finding("NC-007", "warning", verdict))
        assert result.verdict == verdict
        assert result.findings == (_finding("NC-007", "warning", verdict),)
        assert result.exit_code == 0
        assert result.blocking is False

    def test_one_error_among_warnings_still_blocks(self) -> None:
        """Warnings are advisory individually, not as a way to dilute an error."""
        result = _result(
            _finding("NC-007", "warning", VERDICT_NONCOMPLIANT),
            _finding("NC-008", "error", VERDICT_NONCOMPLIANT),
        )
        assert result.verdict == VERDICT_NONCOMPLIANT
        assert result.exit_code == 1
        assert result.blocking is True

    def test_an_error_among_warnings_of_the_other_verdict_still_blocks(self) -> None:
        """The aggregate verdict follows the error even when warnings dominate."""
        result = _result(
            _finding("NC-007", "warning", VERDICT_NONCOMPLIANT),
            _finding("EN-001", "error", VERDICT_INDETERMINATE),
        )
        assert result.verdict == VERDICT_NONCOMPLIANT
        assert result.exit_code == 1
        assert result.blocking is True

    def test_an_unknown_severity_is_not_silently_advisory(self) -> None:
        """Only the exact `warning` label is advisory; a typo fails closed."""
        result = _result(_finding("NC-007", "warn", VERDICT_NONCOMPLIANT))
        assert result.exit_code == 1
        assert result.blocking is True

    def test_blocking_agrees_with_the_exit_code(self) -> None:
        """`blocking` is the same fact as a non-zero status, read as a boolean."""
        results = [
            _result(),
            _result(_finding("NC-007", "error", VERDICT_NONCOMPLIANT)),
            _result(_finding("NC-007", "warning", VERDICT_NONCOMPLIANT)),
        ]
        for result in results:
            assert result.blocking == (result.exit_code != 0), result


class TestSeverityRendering:
    """The table names the severity; the JSON document is unchanged in shape."""

    @staticmethod
    def _mixed_result() -> RuleRunResult:
        """Return one error finding and one warning finding."""
        return _result(
            _finding(
                "NC-007",
                "error",
                VERDICT_NONCOMPLIANT,
                path="tests/one.rs",
                line=7,
                message="a nested cargo invocation can reach the network",
            ),
            _finding(
                "NC-008",
                "warning",
                VERDICT_NONCOMPLIANT,
                path="Makefile",
                line=0,
                message="the test target starts cargo before prefetching",
            ),
        )

    def test_the_table_shows_each_finding_severity(self) -> None:
        """Both severity labels appear beside their findings."""
        table = render_table(self._mixed_result())
        finding_lines = table.splitlines()[1:]
        assert "error" in finding_lines[0], table
        assert "warning" in finding_lines[1], table

    def test_the_table_header_and_verdict_are_unchanged(self) -> None:
        """Severity is added to the finding rows, not to the header contract."""
        table = render_table(self._mixed_result())
        assert table.splitlines()[0] == "nested-cargo-offline-tests: noncompliant"

    def test_the_table_keeps_the_message_column_last(self) -> None:
        """The message stays unobstructed, so an operator can read it first."""
        table = render_table(self._mixed_result())
        for line in table.splitlines()[1:]:
            assert line.endswith((
                "a nested cargo invocation can reach the network",
                "the test target starts cargo before prefetching",
            )), line

    def test_json_carries_the_severity_and_the_verdict(self) -> None:
        """The machine-readable document keeps both, whatever the exit status."""
        document = json.loads(render_json(self._mixed_result()))
        assert document["verdict"] == VERDICT_NONCOMPLIANT, document
        severities = [finding["severity"] for finding in document["findings"]]
        assert severities == ["error", "warning"], document

    def test_a_warning_only_result_renders_its_finding(self) -> None:
        """An advisory result that exits zero still shows what it saw."""
        result = _result(_finding("NC-007", "warning", VERDICT_NONCOMPLIANT))
        rendered = render_table(result)
        assert "NC-007" in rendered, rendered
        assert "warning" in rendered, rendered


def test_findings_are_immutable_and_ordered() -> None:
    """The contract reads the stored tuple, not a copy that could drift."""
    findings: typ.Final = (
        _finding("NC-007", "warning", VERDICT_NONCOMPLIANT),
        _finding("NC-008", "error", VERDICT_INDETERMINATE),
    )
    result = _result(*findings)
    assert result.findings == findings
