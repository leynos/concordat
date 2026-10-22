"""Exercise the nested cargo CI wiring rule through the actual operator CLI."""

from __future__ import annotations

import typing as typ

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from concordat import cli

from .conftest import RunResult

if typ.TYPE_CHECKING:
    import pathlib

scenarios("features/nested_cargo_ci_wiring_rule_run.feature")

SETUP = "leynos/shared-actions/.github/actions/setup-rust@" + "a" * 40
HEAD = "name: ci\non: push\n"
OPT_OUT_STEP = (
    f"      - uses: {SETUP}\n        with:\n          export-rustc-wrapper: false\n"
)
JOB = "jobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"
WORKFLOWS = {
    "keeps the defaults": HEAD + JOB + f"      - uses: {SETUP}\n",
    "builds into the runner temp dir": (
        HEAD
        + "env:\n  CARGO_TARGET_DIR: ${{ runner.temp }}/target\n"
        + JOB
        + f"      - uses: {SETUP}\n"
    ),
    "opts out of the wrapper silently": (HEAD + JOB + OPT_OUT_STEP),
    "opts out and scopes the wrapper": (
        HEAD
        + JOB
        + OPT_OUT_STEP
        + '      - run: RUSTC_WRAPPER="$SCCACHE_PATH" cargo test\n'
    ),
}


@pytest.fixture
def wiring_checkout(tmp_path: pathlib.Path) -> pathlib.Path:
    """Return the scenario's isolated checkout directory."""
    return tmp_path


@given(parsers.parse('a workflow that "{wiring}"'))
def given_workflow(wiring_checkout: pathlib.Path, wiring: str) -> None:
    """Write one workflow with the named wiring."""
    path = wiring_checkout / ".github" / "workflows" / "ci.yml"
    path.parent.mkdir(parents=True)
    path.write_text(WORKFLOWS[wiring], encoding="utf-8")


@when("I audit the nested cargo CI wiring")
def when_audit(
    wiring_checkout: pathlib.Path,
    cli_invocation: dict[str, RunResult],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Record the real CLI's table, diagnostics and exit status."""
    try:
        code = cli.main([
            "artefact",
            "rule",
            "run",
            "nested-cargo-ci-wiring",
            "--repo",
            str(wiring_checkout),
        ])
    except SystemExit as error:
        code = int(error.code or 0)
    captured = capsys.readouterr()
    cli_invocation["result"] = RunResult(
        stdout=captured.out, stderr=captured.err, returncode=code
    )


@then(parsers.cfparse("the nested wiring exit status is {status:d}"))
def then_status(cli_invocation: dict[str, RunResult], status: int) -> None:
    """Assert the exit status presented to an operator or CI job."""
    result = cli_invocation["result"]
    assert result.returncode == status, result.stderr or result.stdout


@then(parsers.parse('the nested wiring output contains "{finding}"'))
def then_output(cli_invocation: dict[str, RunResult], finding: str) -> None:
    """Assert the actual rendered verdict or rule identifier."""
    output = cli_invocation["result"].stdout
    assert finding in output, output
