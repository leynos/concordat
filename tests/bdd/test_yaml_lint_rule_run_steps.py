"""Behavioural tests for `concordat artefact rule run yaml-lint-baseline`."""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import pathlib
import sys
import typing as typ

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from concordat import cli

from .conftest import RunResult

if typ.TYPE_CHECKING:
    import types

    from tests.conftest import CmdMox

scenarios("features/yaml_lint_rule_run.feature")

RULE_ID = "yaml-lint-baseline"
NAMESPACE = "canon.lint_rules.yaml_lint_baseline"
PACKAGE_DIR = (
    pathlib.Path(__file__).resolve().parents[2]
    / "platform-standards"
    / "canon"
    / "lint-rules"
    / RULE_ID
)
ENVELOPES_DIR = PACKAGE_DIR / "fixtures" / "envelopes"

# The repository-relative path and one-based line a finding cites.
FailureLocation = tuple[str, int]


def _load_generator() -> types.ModuleType:
    """Import the package's `fixtures/generate.py` by path."""
    spec = importlib.util.spec_from_file_location(
        "yaml_lint_baseline_generate_bdd", PACKAGE_DIR / "fixtures" / "generate.py"
    )
    if spec is None or spec.loader is None:
        message = "could not load the fixture generator"
        raise ImportError(message)
    module = importlib.util.module_from_spec(spec)
    # `Scenario` is a dataclass under `from __future__ import annotations`;
    # dataclasses resolve those string annotations through `sys.modules`, so
    # the module must be registered before its body runs.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _conftest_result(failures: list[dict[str, object]]) -> str:
    """Render a conftest JSON document in the observed output shape."""
    result: dict[str, object] = {
        "filename": "envelope.json",
        "namespace": NAMESPACE,
        "successes": 30 - len(failures),
    }
    if failures:
        result["failures"] = failures
    return json.dumps([result])


def _failure(
    rule_id: str, verdict: str, location: FailureLocation, msg: str
) -> dict[str, object]:
    """Build one conftest failure entry in the observed metadata shape."""
    path, line = location
    return {
        "msg": msg,
        "metadata": {
            "line": line,
            "path": path,
            "query": f"data.{NAMESPACE}.deny",
            "rule_id": rule_id,
            "severity": "error",
            "verdict": verdict,
        },
    }


@pytest.fixture
def checkout(tmp_path: pathlib.Path) -> pathlib.Path:
    """Provide an empty checkout directory for the scenario."""
    return tmp_path / "checkout"


@given(
    parsers.cfparse(
        'a YAML lint checkout laid out from the "{scenario}" fixture scenario'
    )
)
def given_yaml_lint_checkout(checkout: pathlib.Path, scenario: str) -> None:
    """Lay the named generator scenario out as the checkout under audit."""
    generate = _load_generator()
    checkout.mkdir()
    generate.lay_out(generate.SCENARIOS[scenario], checkout)


@given(parsers.cfparse('makeutil reports the YAML lint "{fixture}" fixture facts'))
def given_makeutil_fixture(cmd_mox: CmdMox, fixture: str) -> None:
    """Program the fake makeutil with the checked-in fixture report."""
    envelope = json.loads((ENVELOPES_DIR / f"{fixture}.json").read_text())
    report = envelope["makefile"]
    exit_code = 0 if report["parse"]["status"] == "complete" else 1
    cmd_mox.mock("makeutil").returns(exit_code=exit_code, stdout=json.dumps(report))


def _program_conftest(cmd_mox: CmdMox, failures: list[dict[str, object]]) -> None:
    """Program the fake conftest with *failures*, passing when there are none."""
    cmd_mox.mock("conftest").returns(
        exit_code=1 if failures else 0, stdout=_conftest_result(failures)
    )


@given("conftest reports no YAML lint failures")
def given_conftest_clean(cmd_mox: CmdMox) -> None:
    """Program the fake conftest with a passing result."""
    _program_conftest(cmd_mox, [])


@given("conftest reports the discarded file list failure")
def given_conftest_discarded_list(cmd_mox: CmdMox) -> None:
    """Program the fake conftest with the YL-001 failure."""
    failure = _failure(
        "YL-001",
        "noncompliant",
        ("Makefile", 8),
        '"lint"-path recipe runs yamllint without a `git ls-files` list covering '
        "*.yml and *.yaml; select tracked YAML only",
    )
    _program_conftest(cmd_mox, [failure])


@given("conftest reports the missing base failure")
def given_conftest_missing_base(cmd_mox: CmdMox) -> None:
    """Program the fake conftest with the YL-003 missing-base failure."""
    failure = _failure(
        "YL-003",
        "noncompliant",
        (".yamllint-estate.yml", 0),
        ".yamllint-estate.yml is missing; copy the canonical estate yamllint "
        "base verbatim",
    )
    _program_conftest(cmd_mox, [failure])


@given("conftest reports the missing lint job failure")
def given_conftest_missing_lint_job(cmd_mox: CmdMox) -> None:
    """Program the fake conftest with the YL-005 absent-job failure."""
    failure = _failure(
        "YL-005",
        "noncompliant",
        (".github/workflows", 0),
        "no CI job runs `make lint`; the YAML lint gate is not enforced in CI",
    )
    _program_conftest(cmd_mox, [failure])


@given("conftest reports the undecodable workflow failure")
def given_conftest_undecodable_workflow(cmd_mox: CmdMox) -> None:
    """Program the fake conftest with the YL-005 indeterminate failure."""
    failure = _failure(
        "YL-005",
        "indeterminate",
        (".github/workflows/broken.yml", 0),
        ".github/workflows/broken.yml could not be decoded: invalid YAML",
    )
    _program_conftest(cmd_mox, [failure])


@dataclasses.dataclass(frozen=True, slots=True)
class _RuleCli:
    """The command line with its subprocess doubles attached."""

    cmd_mox: CmdMox

    def run(self, checkout: pathlib.Path) -> int:
        """Run the YAML lint rule over *checkout*, returning the exit status."""
        self.cmd_mox.replay()
        try:
            returncode = cli.main([
                "artefact",
                "rule",
                "run",
                RULE_ID,
                "--repo",
                str(checkout),
            ])
        except SystemExit as exc:
            returncode = int(exc.code or 0)
        self.cmd_mox.verify()
        return returncode


@pytest.fixture
def rule_cli(cmd_mox: CmdMox) -> _RuleCli:
    """Provide the command line bound to this test's doubles."""
    return _RuleCli(cmd_mox)


@when("I run the YAML lint rule against the checkout")
def when_run_rule(
    checkout: pathlib.Path,
    rule_cli: _RuleCli,
    cli_invocation: dict[str, RunResult],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Invoke the CLI and capture its output and exit status."""
    returncode = rule_cli.run(checkout)
    captured = capsys.readouterr()
    cli_invocation["result"] = RunResult(
        stdout=captured.out, stderr=captured.err, returncode=returncode
    )


@then(parsers.cfparse("the YAML lint rule exit status is {code:d}"))
def then_exit_status(cli_invocation: dict[str, RunResult], code: int) -> None:
    """Assert the recorded exit status."""
    result = cli_invocation["result"]
    assert result.returncode == code, result.stderr or result.stdout


@then("the table output reports the YAML lint rule as compliant")
def then_compliant(cli_invocation: dict[str, RunResult]) -> None:
    """Assert the table names the compliant verdict and no findings."""
    stdout = cli_invocation["result"].stdout
    assert f"{RULE_ID}: compliant" in stdout, stdout
    assert "YL-" not in stdout, stdout


@then("the output contains a YL-001 finding citing Makefile line 8")
def then_yl001_with_line(cli_invocation: dict[str, RunResult]) -> None:
    """Assert the YL-001 finding carries its source location."""
    stdout = cli_invocation["result"].stdout
    assert "YL-001" in stdout, stdout
    assert "Makefile:8" in stdout, stdout
    assert "git ls-files" in stdout, stdout


@then("the output reports YL-003 as noncompliant for the base file")
def then_yl003_base(cli_invocation: dict[str, RunResult]) -> None:
    """Assert the noncompliant verdict names the base file."""
    stdout = cli_invocation["result"].stdout
    assert "YL-003" in stdout, stdout
    assert "noncompliant" in stdout, stdout
    assert ".yamllint-estate.yml" in stdout, stdout


@then("the output contains a YL-005 finding naming the workflows directory")
def then_yl005_names_directory(cli_invocation: dict[str, RunResult]) -> None:
    """Assert the YL-005 finding cites the workflows directory."""
    stdout = cli_invocation["result"].stdout
    assert "YL-005" in stdout, stdout
    assert ".github/workflows" in stdout, stdout


@then("the output reports YL-005 as indeterminate")
def then_yl005_indeterminate(cli_invocation: dict[str, RunResult]) -> None:
    """Assert the indeterminate verdict is surfaced with the workflow path."""
    stdout = cli_invocation["result"].stdout
    assert "YL-005" in stdout, stdout
    assert "indeterminate" in stdout, stdout
    assert "broken.yml" in stdout, stdout
