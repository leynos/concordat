"""Unit tests for the rule-run modules under `concordat.rules`."""

from __future__ import annotations

import json
import subprocess
import typing as typ

import pytest

from concordat.errors import OperationalRuleError
from concordat.rules import runner
from concordat.rules.envelope import build_envelope
from concordat.rules.makefile_facts import inspect_makefile
from concordat.rules.runner import (
    Finding,
    RuleRunResult,
    render_json,
    render_table,
    run_rule,
)

if typ.TYPE_CHECKING:
    import pathlib

    from tests.conftest import CmdMox

MINIMAL_REPORT: typ.Final = {
    "schema_version": 1,
    "tool": {
        "name": "makeutil",
        "version": "0.1.0",
        "parser": "makefile-lossless",
        "parser_version": "0.3.40",
    },
    "source": {"path": "Makefile", "sha256": "0" * 64, "byte_length": 20},
    "parse": {"status": "complete", "diagnostics": []},
    "rules": [],
    "variables": [],
    "includes": [],
}

CARGO_STUB = '[package]\nname = "fixture"\nversion = "0.1.0"\n'


def _write_checkout(root: pathlib.Path, *, cargo: bool, makefile: bool) -> None:
    root.mkdir(exist_ok=True)
    if cargo:
        (root / "Cargo.toml").write_text(CARGO_STUB)
    if makefile:
        (root / "Makefile").write_text("lint:\n\twhitaker --all\n")


class TestInspectMakefile:
    """Behaviour of the makeutil subprocess boundary."""

    def test_complete_parse_returns_report(
        self,
        tmp_path: pathlib.Path,
        cmd_mox: CmdMox,
    ) -> None:
        """Complete parse returns report."""
        _write_checkout(tmp_path, cargo=False, makefile=True)
        cmd_mox.mock("makeutil").with_args("parse", "Makefile").returns(
            stdout=json.dumps(MINIMAL_REPORT)
        )
        cmd_mox.replay()
        facts = inspect_makefile(tmp_path / "Makefile")
        cmd_mox.verify()
        assert facts.status == "complete", facts.status
        assert facts.report["schema_version"] == 1, facts.report

    def test_recovered_parse_is_retained(
        self,
        tmp_path: pathlib.Path,
        cmd_mox: CmdMox,
    ) -> None:
        """Recovered parse is retained."""
        _write_checkout(tmp_path, cargo=False, makefile=True)
        report = dict(MINIMAL_REPORT, parse={"status": "recovered", "diagnostics": []})
        cmd_mox.mock("makeutil").returns(exit_code=1, stdout=json.dumps(report))
        cmd_mox.replay()
        facts = inspect_makefile(tmp_path / "Makefile")
        assert facts.status == "recovered", facts.status

    def test_missing_binary_raises_operational_error(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Missing binary raises operational error."""
        _write_checkout(tmp_path, cargo=False, makefile=True)

        def raise_missing(*args: object, **kwargs: object) -> typ.NoReturn:
            raise FileNotFoundError("makeutil")

        monkeypatch.setattr(subprocess, "run", raise_missing)
        with pytest.raises(OperationalRuleError, match="makeutil") as exc_info:
            inspect_makefile(tmp_path / "Makefile")
        error = exc_info.value
        assert error.operation == "parse-makefile", error.operation
        assert error.tool == "makeutil", error.tool
        assert error.resource == tmp_path / "Makefile", error.resource

    def test_fatal_exit_raises_operational_error(
        self,
        tmp_path: pathlib.Path,
        cmd_mox: CmdMox,
    ) -> None:
        """Fatal exit raises operational error."""
        _write_checkout(tmp_path, cargo=False, makefile=True)
        cmd_mox.mock("makeutil").returns(
            exit_code=2,
            stderr="makeutil: source-utf8: invalid",
        )
        cmd_mox.replay()
        with pytest.raises(OperationalRuleError, match="source-utf8"):
            inspect_makefile(tmp_path / "Makefile")

    def test_unknown_schema_version_raises(
        self,
        tmp_path: pathlib.Path,
        cmd_mox: CmdMox,
    ) -> None:
        """Unknown schema version raises."""
        _write_checkout(tmp_path, cargo=False, makefile=True)
        report = dict(MINIMAL_REPORT, schema_version=99)
        cmd_mox.mock("makeutil").returns(stdout=json.dumps(report))
        cmd_mox.replay()
        with pytest.raises(OperationalRuleError, match="schema"):
            inspect_makefile(tmp_path / "Makefile")

    def test_timeout_raises_operational_error(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A makeutil timeout surfaces as an operational error."""
        _write_checkout(tmp_path, cargo=False, makefile=True)

        def raise_timeout(*args: object, **kwargs: object) -> typ.NoReturn:
            raise subprocess.TimeoutExpired(cmd="makeutil", timeout=10.0)

        monkeypatch.setattr(subprocess, "run", raise_timeout)
        with pytest.raises(OperationalRuleError, match="timed out"):
            inspect_makefile(tmp_path / "Makefile")

    def test_invalid_json_raises(
        self,
        tmp_path: pathlib.Path,
        cmd_mox: CmdMox,
    ) -> None:
        """Non-JSON stdout raises before the report is inspected."""
        _write_checkout(tmp_path, cargo=False, makefile=True)
        cmd_mox.mock("makeutil").returns(stdout="not json at all")
        cmd_mox.replay()
        with pytest.raises(OperationalRuleError, match="invalid JSON") as exc_info:
            inspect_makefile(tmp_path / "Makefile")
        error = exc_info.value
        assert error.operation == "parse-makefile", error.operation
        assert error.tool == "makeutil", error.tool
        assert error.resource == tmp_path / "Makefile", error.resource

    def test_non_object_json_raises(
        self,
        tmp_path: pathlib.Path,
        cmd_mox: CmdMox,
    ) -> None:
        """Valid JSON that is not an object raises."""
        _write_checkout(tmp_path, cargo=False, makefile=True)
        cmd_mox.mock("makeutil").returns(stdout=json.dumps([1, 2, 3]))
        cmd_mox.replay()
        with pytest.raises(OperationalRuleError, match="not a JSON object"):
            inspect_makefile(tmp_path / "Makefile")

    def test_missing_parse_object_raises(
        self,
        tmp_path: pathlib.Path,
        cmd_mox: CmdMox,
    ) -> None:
        """A report whose `parse` is not an object raises."""
        _write_checkout(tmp_path, cargo=False, makefile=True)
        report = dict(MINIMAL_REPORT, parse="complete")
        cmd_mox.mock("makeutil").returns(stdout=json.dumps(report))
        cmd_mox.replay()
        with pytest.raises(OperationalRuleError, match="no `parse` object"):
            inspect_makefile(tmp_path / "Makefile")

    def test_unknown_parse_status_raises(
        self,
        tmp_path: pathlib.Path,
        cmd_mox: CmdMox,
    ) -> None:
        """An unrecognised parse status raises."""
        _write_checkout(tmp_path, cargo=False, makefile=True)
        report = dict(MINIMAL_REPORT, parse={"status": "bogus", "diagnostics": []})
        cmd_mox.mock("makeutil").returns(stdout=json.dumps(report))
        cmd_mox.replay()
        with pytest.raises(OperationalRuleError, match="unknown parse status"):
            inspect_makefile(tmp_path / "Makefile")

    def test_exit_code_status_disagreement_raises(
        self,
        tmp_path: pathlib.Path,
        cmd_mox: CmdMox,
    ) -> None:
        """A status that contradicts the exit code raises."""
        _write_checkout(tmp_path, cargo=False, makefile=True)
        # Exit 1 promises a recovered parse, but the report claims complete.
        report = dict(MINIMAL_REPORT, parse={"status": "complete", "diagnostics": []})
        cmd_mox.mock("makeutil").returns(exit_code=1, stdout=json.dumps(report))
        cmd_mox.replay()
        with pytest.raises(OperationalRuleError, match="disagrees with its exit code"):
            inspect_makefile(tmp_path / "Makefile")

    def test_malformed_source_object_raises(
        self,
        tmp_path: pathlib.Path,
        cmd_mox: CmdMox,
    ) -> None:
        """A `source` that is not an object is rejected as nested malformed data."""
        _write_checkout(tmp_path, cargo=False, makefile=True)
        report = dict(MINIMAL_REPORT, source="Makefile")
        cmd_mox.mock("makeutil").returns(stdout=json.dumps(report))
        cmd_mox.replay()
        with pytest.raises(
            OperationalRuleError, match="malformed `source`"
        ) as exc_info:
            inspect_makefile(tmp_path / "Makefile")
        assert exc_info.value.operation == "parse-makefile", exc_info.value.operation

    def test_malformed_rules_array_raises(
        self,
        tmp_path: pathlib.Path,
        cmd_mox: CmdMox,
    ) -> None:
        """A `rules` field that is not a list is rejected before narrowing."""
        _write_checkout(tmp_path, cargo=False, makefile=True)
        report = dict(MINIMAL_REPORT, rules={"not": "a list"})
        cmd_mox.mock("makeutil").returns(stdout=json.dumps(report))
        cmd_mox.replay()
        with pytest.raises(OperationalRuleError, match="malformed `rules`"):
            inspect_makefile(tmp_path / "Makefile")


class TestBuildEnvelope:
    """Envelope construction from a checkout directory."""

    def test_empty_checkout_yields_inapplicable_envelope(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """Empty checkout yields inapplicable envelope."""
        _write_checkout(tmp_path, cargo=False, makefile=False)
        envelope = build_envelope(tmp_path)
        assert envelope["schema_version"] == 1, envelope
        applicability = typ.cast("dict[str, object]", envelope["applicability"])
        assert applicability["root_cargo_toml"] is False, applicability
        assert applicability["root_makefile"] is False, applicability
        assert envelope["makefile"] is None, envelope["makefile"]

    def test_full_checkout_yields_facts(
        self,
        tmp_path: pathlib.Path,
        cmd_mox: CmdMox,
    ) -> None:
        """Full checkout yields facts."""
        _write_checkout(tmp_path, cargo=True, makefile=True)
        cmd_mox.mock("makeutil").returns(stdout=json.dumps(MINIMAL_REPORT))
        cmd_mox.replay()
        envelope = build_envelope(tmp_path)
        cmd_mox.verify()
        applicability = typ.cast("dict[str, object]", envelope["applicability"])
        assert applicability["root_cargo_toml"] is True, applicability
        assert applicability["root_makefile"] is True, applicability
        cargo = typ.cast("dict[str, object]", envelope["cargo"])
        parsed = typ.cast("dict[str, object]", cargo["parsed"])
        assert parsed["package"] == {"name": "fixture", "version": "0.1.0"}, parsed
        makefile = typ.cast("dict[str, object]", envelope["makefile"])
        assert makefile["schema_version"] == 1, makefile

    def test_invalid_cargo_toml_raises(self, tmp_path: pathlib.Path) -> None:
        """Invalid cargo toml raises with structured context."""
        tmp_path.mkdir(exist_ok=True)
        (tmp_path / "Cargo.toml").write_text("not = [valid")
        with pytest.raises(OperationalRuleError, match=r"Cargo\.toml") as exc_info:
            build_envelope(tmp_path)
        error = exc_info.value
        assert error.operation == "parse-cargo-toml", error.operation
        assert error.tool is None, error.tool
        assert error.resource == tmp_path / "Cargo.toml", error.resource

    def test_non_table_cargo_structure_raises(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Cargo TOML that parses to a non-table cannot fill the envelope."""
        from concordat.rules import envelope as envelope_module

        tmp_path.mkdir(exist_ok=True)
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "x"\n')
        monkeypatch.setattr(
            envelope_module.tomllib,
            "loads",
            lambda _text: ["not", "a", "table"],
        )
        with pytest.raises(
            OperationalRuleError, match="did not parse to a table"
        ) as exc_info:
            build_envelope(tmp_path)
        assert exc_info.value.operation == "parse-cargo-toml", exc_info.value.operation


class TestRunConftest:
    """The Conftest subprocess wrapper translates spawn failures."""

    def test_missing_conftest_translates(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A missing conftest binary becomes a structured operational error."""

        def raise_missing(*args: object, **kwargs: object) -> typ.NoReturn:
            raise FileNotFoundError("conftest")

        monkeypatch.setattr(runner.subprocess, "run", raise_missing)
        with pytest.raises(
            OperationalRuleError, match="conftest is required"
        ) as exc_info:
            runner._run_conftest(["conftest", "test"], "rust-makefile-baseline")
        error = exc_info.value
        assert error.operation == "invoke-conftest", error.operation
        assert error.tool == "conftest", error.tool
        assert error.resource == "rust-makefile-baseline", error.resource

    def test_conftest_timeout_translates(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A conftest timeout becomes a structured operational error."""

        def raise_timeout(*args: object, **kwargs: object) -> typ.NoReturn:
            raise subprocess.TimeoutExpired(cmd="conftest", timeout=60.0)

        monkeypatch.setattr(runner.subprocess, "run", raise_timeout)
        with pytest.raises(OperationalRuleError, match="timed out") as exc_info:
            runner._run_conftest(["conftest", "test"], "rust-makefile-baseline")
        assert exc_info.value.operation == "invoke-conftest", exc_info.value.operation
        assert exc_info.value.tool == "conftest", exc_info.value.tool


class TestRunRule:
    """End-to-end behaviour of run_rule with mocked externals."""

    def test_unknown_rule_package_raises(self, tmp_path: pathlib.Path) -> None:
        """Unknown rule package raises."""
        _write_checkout(tmp_path, cargo=True, makefile=False)
        with pytest.raises(OperationalRuleError, match="no-such-rule"):
            run_rule("no-such-rule", tmp_path)

    def test_failures_map_to_findings(
        self,
        tmp_path: pathlib.Path,
        cmd_mox: CmdMox,
    ) -> None:
        """Failures map to findings."""
        _write_checkout(tmp_path, cargo=True, makefile=True)
        cmd_mox.mock("makeutil").returns(stdout=json.dumps(MINIMAL_REPORT))
        conftest_doc = json.dumps(
            [
                {
                    "filename": "envelope.json",
                    "namespace": "canon.lint_rules.rust_makefile_baseline",
                    "successes": 12,
                    "failures": [
                        {
                            "msg": 'required Make target "lint" is absent',
                            "metadata": {
                                "line": 0,
                                "path": "Makefile",
                                "rule_id": "FP-003",
                                "severity": "error",
                                "verdict": "noncompliant",
                            },
                        },
                        {
                            "msg": "cannot prove the gate",
                            "metadata": {
                                "line": 3,
                                "path": "Makefile",
                                "rule_id": "QG-001",
                                "severity": "error",
                                "verdict": "indeterminate",
                            },
                        },
                    ],
                }
            ]
        )
        cmd_mox.mock("conftest").returns(exit_code=1, stdout=conftest_doc)
        cmd_mox.replay()
        result = run_rule("rust-makefile-baseline", tmp_path)
        cmd_mox.verify()
        assert result.verdict == "noncompliant", result
        assert result.exit_code == 1, result
        assert [f.rule_id for f in result.findings] == ["FP-003", "QG-001"], (
            result.findings
        )
        assert result.findings[1].line == 3, result.findings[1]

    def test_clean_run_is_compliant(
        self,
        tmp_path: pathlib.Path,
        cmd_mox: CmdMox,
    ) -> None:
        """Clean run is compliant."""
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
        result = run_rule("rust-makefile-baseline", tmp_path)
        assert result.verdict == "compliant", result
        assert result.exit_code == 0, result
        assert result.findings == (), result.findings

    def test_indeterminate_only_yields_indeterminate_verdict(
        self,
        tmp_path: pathlib.Path,
        cmd_mox: CmdMox,
    ) -> None:
        """Indeterminate only yields indeterminate verdict."""
        _write_checkout(tmp_path, cargo=True, makefile=True)
        cmd_mox.mock("makeutil").returns(stdout=json.dumps(MINIMAL_REPORT))
        doc = json.dumps(
            [
                {
                    "filename": "envelope.json",
                    "namespace": "canon.lint_rules.rust_makefile_baseline",
                    "successes": 13,
                    "failures": [
                        {
                            "msg": "cannot prove the gate",
                            "metadata": {
                                "line": 0,
                                "path": "Makefile",
                                "rule_id": "QG-001",
                                "severity": "error",
                                "verdict": "indeterminate",
                            },
                        }
                    ],
                }
            ]
        )
        cmd_mox.mock("conftest").returns(exit_code=1, stdout=doc)
        cmd_mox.replay()
        result = run_rule("rust-makefile-baseline", tmp_path)
        assert result.verdict == "indeterminate", result
        assert result.exit_code == 1, result


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
