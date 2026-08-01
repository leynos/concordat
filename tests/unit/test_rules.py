"""Unit tests for the rule-run modules under `concordat.rules`."""

from __future__ import annotations

import dataclasses
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


def _report_with(**overrides: object) -> dict[str, object]:
    """Return an isolated minimal makeutil report with selected overrides."""
    return dict(MINIMAL_REPORT, **overrides)


# A case supplies stdout either verbatim (non-JSON or hand-built payloads) or as
# a factory, so each parametrized run gets its own report mapping.
type MakeutilOutput = str | typ.Callable[[], dict[str, object]]


@dataclasses.dataclass(frozen=True)
class MakeutilFailureCase:
    """A malformed or rejected makeutil subprocess result."""

    exit_code: int
    stdout: MakeutilOutput
    stderr: str
    message: str


@dataclasses.dataclass(frozen=True)
class SpawnFailureCase:
    """One subprocess launch failure and its expected error message."""

    error: Exception
    match: str


def _raise_process_error(
    error: BaseException,
) -> typ.Callable[..., typ.NoReturn]:
    """Return a subprocess replacement that raises *error*."""

    def raise_error(*args: object, **kwargs: object) -> typ.NoReturn:
        raise error

    return raise_error


def _assert_operational_context(
    error: OperationalRuleError,
    *,
    operation: str,
    tool: str,
    resource: pathlib.Path | str,
) -> None:
    """Assert stable structured context on an operational rule error."""
    assert error.operation == operation, error.operation
    assert error.tool == tool, error.tool
    assert error.resource == resource, error.resource


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

    @pytest.mark.parametrize(
        "case",
        [
            pytest.param(
                SpawnFailureCase(FileNotFoundError("makeutil"), "makeutil"),
                id="missing-binary",
            ),
            pytest.param(
                SpawnFailureCase(
                    subprocess.TimeoutExpired(cmd="makeutil", timeout=10.0),
                    "timed out",
                ),
                id="timeout",
            ),
            pytest.param(
                SpawnFailureCase(
                    PermissionError("makeutil"),
                    "could not launch makeutil",
                ),
                id="launch-oserror",
            ),
        ],
    )
    def test_spawn_failure_raises_operational_error(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        case: SpawnFailureCase,
    ) -> None:
        """Every makeutil launch failure carries parse-makefile context.

        ``_run_makeutil`` translates a missing binary, a timeout, and a generic
        ``OSError`` such as a permission denial; each must reach the CLI's
        operational-error boundary naming the Makefile it was parsing.
        """
        _write_checkout(tmp_path, cargo=False, makefile=True)
        monkeypatch.setattr(subprocess, "run", _raise_process_error(case.error))

        with pytest.raises(OperationalRuleError, match=case.match) as exc_info:
            inspect_makefile(tmp_path / "Makefile")

        _assert_operational_context(
            exc_info.value,
            operation="parse-makefile",
            tool="makeutil",
            resource=tmp_path / "Makefile",
        )

    @pytest.mark.parametrize(
        "case",
        [
            pytest.param(
                MakeutilFailureCase(
                    exit_code=2,
                    stdout="",
                    stderr="makeutil: source-utf8: invalid",
                    message="source-utf8",
                ),
                id="fatal-exit",
            ),
            pytest.param(
                MakeutilFailureCase(
                    exit_code=0,
                    stdout=lambda: _report_with(schema_version=99),
                    stderr="",
                    message="schema",
                ),
                id="unsupported-schema",
            ),
            pytest.param(
                MakeutilFailureCase(
                    exit_code=0,
                    stdout="not json at all",
                    stderr="",
                    message="invalid JSON",
                ),
                id="invalid-json",
            ),
            pytest.param(
                MakeutilFailureCase(
                    exit_code=0,
                    stdout=json.dumps([1, 2, 3]),
                    stderr="",
                    message="not a JSON object",
                ),
                id="non-object-json",
            ),
            pytest.param(
                MakeutilFailureCase(
                    exit_code=0,
                    stdout=lambda: _report_with(parse="complete"),
                    stderr="",
                    message="no `parse` object",
                ),
                id="non-object-parse",
            ),
            pytest.param(
                MakeutilFailureCase(
                    exit_code=0,
                    stdout=lambda: _report_with(
                        parse={"status": "bogus", "diagnostics": []}
                    ),
                    stderr="",
                    message="unknown parse status",
                ),
                id="unknown-status",
            ),
            pytest.param(
                MakeutilFailureCase(
                    exit_code=1,
                    stdout=lambda: _report_with(
                        parse={"status": "complete", "diagnostics": []}
                    ),
                    stderr="",
                    message="disagrees with its exit code",
                ),
                id="exit-status-disagreement",
            ),
            pytest.param(
                MakeutilFailureCase(
                    exit_code=0,
                    stdout=lambda: _report_with(source="Makefile"),
                    stderr="",
                    message="malformed `source`",
                ),
                id="malformed-source",
            ),
            pytest.param(
                MakeutilFailureCase(
                    exit_code=0,
                    stdout=lambda: _report_with(rules={"not": "a list"}),
                    stderr="",
                    message="malformed `rules`",
                ),
                id="malformed-rules",
            ),
        ],
    )
    def test_rejects_invalid_makeutil_output(
        self,
        tmp_path: pathlib.Path,
        cmd_mox: CmdMox,
        case: MakeutilFailureCase,
    ) -> None:
        """Invalid makeutil output raises a parse-makefile operational error.

        Each case receives an isolated report mapping (callables are resolved
        per invocation), so no parametrization mutates ``MINIMAL_REPORT``.
        """
        _write_checkout(tmp_path, cargo=False, makefile=True)
        stdout_text = (
            case.stdout if isinstance(case.stdout, str) else json.dumps(case.stdout())
        )
        cmd_mox.mock("makeutil").returns(
            exit_code=case.exit_code,
            stdout=stdout_text,
            stderr=case.stderr,
        )
        cmd_mox.replay()
        with pytest.raises(OperationalRuleError, match=case.message) as exc_info:
            inspect_makefile(tmp_path / "Makefile")
        error = exc_info.value
        assert error.operation == "parse-makefile", error.operation
        assert error.tool == "makeutil", error.tool
        assert error.resource == tmp_path / "Makefile", error.resource


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

    @pytest.mark.parametrize(
        "case",
        [
            pytest.param(
                SpawnFailureCase(
                    FileNotFoundError("conftest"),
                    "conftest is required",
                ),
                id="missing-binary",
            ),
            pytest.param(
                SpawnFailureCase(
                    subprocess.TimeoutExpired(cmd="conftest", timeout=60.0),
                    "timed out",
                ),
                id="timeout",
            ),
        ],
    )
    def test_spawn_failure_translates(
        self,
        monkeypatch: pytest.MonkeyPatch,
        case: SpawnFailureCase,
    ) -> None:
        """Every conftest launch failure carries invoke-conftest context.

        Unlike ``_run_makeutil`` this wrapper translates only a missing binary
        and a timeout, so a generic ``OSError`` is deliberately not covered
        here; the rule ID stands in for the resource being evaluated.
        """
        monkeypatch.setattr(runner.subprocess, "run", _raise_process_error(case.error))

        with pytest.raises(OperationalRuleError, match=case.match) as exc_info:
            runner._run_conftest(["conftest", "test"], "rust-makefile-baseline")

        _assert_operational_context(
            exc_info.value,
            operation="invoke-conftest",
            tool="conftest",
            resource="rust-makefile-baseline",
        )


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
