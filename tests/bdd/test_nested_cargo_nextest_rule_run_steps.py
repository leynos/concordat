"""Exercise the nested cargo nextest rule through the actual operator CLI."""

from __future__ import annotations

import typing as typ

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from concordat import cli

from .conftest import RunResult

if typ.TYPE_CHECKING:
    import pathlib

scenarios("features/nested_cargo_nextest_rule_run.feature")

BOUNDED = (
    "[test-groups.cargo-spawning]\nmax-threads = 1\n"
    '[profile.default]\nslow-timeout = { period = "60s", terminate-after = 5 }\n'
    'global-timeout = "30m"\n'
    '[[profile.default.overrides]]\nfilter = "binary(ui)"\n'
    'test-group = "cargo-spawning"\n'
)


@pytest.fixture
def nextest_checkout(tmp_path: pathlib.Path) -> pathlib.Path:
    """Return the scenario's isolated checkout directory."""
    return tmp_path


@given(parsers.parse('a crate whose test runs "{command}"'))
def given_crate(nextest_checkout: pathlib.Path, command: str) -> None:
    """Write a crate with one integration test that spawns the command."""
    subcommand = command.split()[1]
    (nextest_checkout / "Cargo.toml").write_text(
        '[package]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    tests = nextest_checkout / "tests"
    tests.mkdir()
    (tests / "ui.rs").write_text(
        "use std::process::Command;\n#[test]\nfn spawns() {\n"
        f'    Command::new("cargo").args(["{subcommand}"]).status().unwrap();\n}}\n',
        encoding="utf-8",
    )


@given(parsers.parse('a nextest configuration that "{config}"'))
def given_config(nextest_checkout: pathlib.Path, config: str) -> None:
    """Write the nextest configuration, or leave it absent."""
    if config == "is absent":
        return
    text = BOUNDED if config == "bounds" else BOUNDED.replace("binary(ui)", "binary(x)")
    path = nextest_checkout / ".config" / "nextest.toml"
    path.parent.mkdir()
    path.write_text(text, encoding="utf-8")


@when("I audit the nested cargo nextest configuration")
def when_audit(
    nextest_checkout: pathlib.Path,
    cli_invocation: dict[str, RunResult],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Record the real CLI's table, diagnostics and exit status."""
    try:
        code = cli.main([
            "artefact",
            "rule",
            "run",
            "nested-cargo-nextest",
            "--repo",
            str(nextest_checkout),
        ])
    except SystemExit as error:
        code = int(error.code or 0)
    captured = capsys.readouterr()
    cli_invocation["result"] = RunResult(
        stdout=captured.out, stderr=captured.err, returncode=code
    )


@then(parsers.cfparse("the nested nextest exit status is {status:d}"))
def then_status(cli_invocation: dict[str, RunResult], status: int) -> None:
    """Assert the exit status presented to an operator or CI job."""
    result = cli_invocation["result"]
    assert result.returncode == status, result.stderr or result.stdout


@then(parsers.parse('the nested nextest output contains "{finding}"'))
def then_output(cli_invocation: dict[str, RunResult], finding: str) -> None:
    """Assert the actual rendered verdict or rule identifier."""
    output = cli_invocation["result"].stdout
    assert finding in output, output
