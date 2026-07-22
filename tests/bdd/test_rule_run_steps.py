"""Behavioural tests for `concordat artefact rule run`."""

from __future__ import annotations

import json
import pathlib
import subprocess
import typing as typ

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from concordat import cli

from .conftest import RunResult

if typ.TYPE_CHECKING:
    from tests.conftest import CmdMox

scenarios("features/rule_run.feature")

RULE_ID = "rust-makefile-baseline"
NAMESPACE = "canon.lint_rules.rust_makefile_baseline"
ENVELOPES_DIR = (
    pathlib.Path(__file__).resolve().parents[2]
    / "platform-standards"
    / "canon"
    / "lint-rules"
    / "rust-makefile-baseline"
    / "fixtures"
    / "envelopes"
)

CARGO_STUB = '[package]\nname = "fixture"\nversion = "0.1.0"\n'


def _conftest_result(failures: list[dict[str, object]]) -> str:
    """Render a conftest JSON document in the observed output shape."""
    result: dict[str, object] = {
        "filename": "envelope.json",
        "namespace": NAMESPACE,
        "successes": 14 - len(failures),
    }
    if failures:
        result["failures"] = failures
    return json.dumps([result])


def _failure(
    rule_id: str,
    verdict: str,
    line: int,
    msg: str,
) -> dict[str, object]:
    return {
        "msg": msg,
        "metadata": {
            "line": line,
            "path": "Makefile",
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


@given("a checkout with a root Cargo.toml")
def given_rust_checkout(checkout: pathlib.Path) -> None:
    """Create a checkout containing a root Cargo.toml and Makefile."""
    checkout.mkdir()
    (checkout / "Cargo.toml").write_text(CARGO_STUB)
    (checkout / "Makefile").write_text("lint:\n\twhitaker --all\n")


@given("a checkout with a root Cargo.toml and a root Makefile")
def given_rust_checkout_with_makefile(checkout: pathlib.Path) -> None:
    """Create a checkout containing a root Cargo.toml and Makefile."""
    given_rust_checkout(checkout)


@given(parsers.cfparse('makeutil reports the "{fixture}" fixture facts'))
def given_makeutil_fixture(cmd_mox: CmdMox, fixture: str) -> None:
    """Program the fake makeutil with a checked-in fixture report."""
    envelope = json.loads((ENVELOPES_DIR / f"{fixture}.json").read_text())
    report = envelope["makefile"]
    exit_code = 0 if report["parse"]["status"] == "complete" else 1
    cmd_mox.mock("makeutil").returns(exit_code=exit_code, stdout=json.dumps(report))


@given("conftest reports no failures")
def given_conftest_clean(cmd_mox: CmdMox) -> None:
    """Program the fake conftest with a passing result."""
    cmd_mox.mock("conftest").returns(exit_code=0, stdout=_conftest_result([]))


@given("conftest reports the soft-skip failure")
def given_conftest_soft_skip(cmd_mox: CmdMox) -> None:
    """Program the fake conftest with the QG-001 soft-skip failure."""
    failure = _failure(
        "QG-001",
        "noncompliant",
        12,
        'lint-path recipe soft-skips the gate ("command -v")',
    )
    cmd_mox.mock("conftest").returns(exit_code=1, stdout=_conftest_result([failure]))


@given("conftest reports the include indeterminate failure")
def given_conftest_include(cmd_mox: CmdMox) -> None:
    """Program the fake conftest with the include indeterminate failure."""
    failure = _failure(
        "QG-001",
        "indeterminate",
        3,
        "Makefile includes other files; the lint gate cannot be proven binding",
    )
    cmd_mox.mock("conftest").returns(exit_code=1, stdout=_conftest_result([failure]))


@given("conftest reports the missing-target failures")
def given_conftest_missing_target(cmd_mox: CmdMox) -> None:
    """Program the fake conftest with FP-003 and QG-001 failures."""
    failures = [
        _failure(
            "FP-003",
            "noncompliant",
            0,
            'required Make target "lint" is absent',
        ),
        _failure(
            "QG-001",
            "noncompliant",
            0,
            'no recipe invokes the "WHITAKER" lint gate',
        ),
    ]
    cmd_mox.mock("conftest").returns(exit_code=1, stdout=_conftest_result(failures))


@given("no makeutil executable is available")
def given_no_makeutil(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every subprocess launch fail as if the binary were absent."""

    def raise_missing(*args: object, **kwargs: object) -> typ.NoReturn:
        raise FileNotFoundError("makeutil")

    monkeypatch.setattr(subprocess, "run", raise_missing)


@when("I run the rule against the checkout")
def when_run_rule(
    checkout: pathlib.Path,
    cmd_mox: CmdMox,
    cli_invocation: dict[str, RunResult],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Invoke the CLI and capture its output and exit status."""
    if cmd_mox.is_programmed:
        cmd_mox.replay()
    try:
        returncode = cli.main(
            ["artefact", "rule", "run", RULE_ID, "--repo", str(checkout)],
        )
    except SystemExit as exc:
        returncode = int(exc.code or 0)
    captured = capsys.readouterr()
    cli_invocation["result"] = RunResult(
        stdout=captured.out,
        stderr=captured.err,
        returncode=returncode,
    )


@then(parsers.cfparse("the exit status is {code:d}"))
def then_exit_status(cli_invocation: dict[str, RunResult], code: int) -> None:
    """Assert the recorded exit status."""
    result = cli_invocation["result"]
    assert result.returncode == code, result.stderr or result.stdout


@then("the table output reports zero findings")
def then_zero_findings(cli_invocation: dict[str, RunResult]) -> None:
    """Assert the table names the compliant verdict and no findings."""
    stdout = cli_invocation["result"].stdout
    assert "compliant" in stdout, stdout
    assert "QG-001" not in stdout, stdout
    assert "FP-003" not in stdout, stdout


@then("the output contains a QG-001 finding citing Makefile line 12")
def then_qg001_with_line(cli_invocation: dict[str, RunResult]) -> None:
    """Assert the QG-001 finding carries its source location."""
    stdout = cli_invocation["result"].stdout
    assert "QG-001" in stdout, stdout
    assert "Makefile:12" in stdout, stdout
    assert "command -v" in stdout, stdout


@then("the output reports QG-001 as indeterminate")
def then_qg001_indeterminate(cli_invocation: dict[str, RunResult]) -> None:
    """Assert the indeterminate verdict is surfaced."""
    stdout = cli_invocation["result"].stdout
    assert "QG-001" in stdout, stdout
    assert "indeterminate" in stdout, stdout


@then(
    parsers.cfparse(
        'the output contains an FP-003 finding naming the "{target}" target'
    )
)
def then_fp003_names_target(
    cli_invocation: dict[str, RunResult],
    target: str,
) -> None:
    """Assert the FP-003 finding names the absent target."""
    stdout = cli_invocation["result"].stdout
    assert "FP-003" in stdout, stdout
    assert f'"{target}"' in stdout, stdout


@then("stderr explains that makeutil is required")
def then_stderr_mentions_makeutil(cli_invocation: dict[str, RunResult]) -> None:
    """Assert the operational failure names the missing tool."""
    assert "makeutil" in cli_invocation["result"].stderr, cli_invocation[
        "result"
    ].stderr
