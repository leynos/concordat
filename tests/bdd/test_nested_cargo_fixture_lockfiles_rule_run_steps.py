"""Exercise the nested cargo lockfile rule through the actual operator CLI."""

from __future__ import annotations

import typing as typ

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from concordat import cli

from .conftest import RunResult

if typ.TYPE_CHECKING:
    import pathlib

scenarios("features/nested_cargo_fixture_lockfiles_rule_run.feature")

REGISTRY = "registry+https://github.com/rust-lang/crates.io-index"
WORKSPACE_LOCK = (
    "version = 4\n\n"
    f'[[package]]\nname = "serde"\nversion = "1.0.0"\nsource = "{REGISTRY}"\n'
)
DRIFTED_LOCK = (
    WORKSPACE_LOCK
    + f'\n[[package]]\nname = "anyhow"\nversion = "1.0.99"\nsource = "{REGISTRY}"\n'
)
FIXTURE_MANIFEST = (
    '[package]\nname = "fixture"\nversion = "0.0.0"\n[dependencies]\nserde = "1"\n'
)
FIXTURE_LOCKS = {
    "matching": WORKSPACE_LOCK,
    "drifting": DRIFTED_LOCK,
    "broken": "not = [toml",
}


@pytest.fixture
def nested_checkout(tmp_path: pathlib.Path) -> pathlib.Path:
    """Return the scenario's isolated checkout directory."""
    return tmp_path


@given(parsers.parse('a workspace whose lock resolves "{package}"'))
def given_workspace(nested_checkout: pathlib.Path, package: str) -> None:
    """Write a workspace root whose lock resolves one registry package."""
    assert package == "serde"
    (nested_checkout / "Cargo.toml").write_text(
        '[package]\nname = "root"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    (nested_checkout / "Cargo.lock").write_text(WORKSPACE_LOCK, encoding="utf-8")


@given(parsers.parse('a fixture crate with a "{lock}" lockfile'))
def given_fixture(nested_checkout: pathlib.Path, lock: str) -> None:
    """Write a fixture crate; a missing lock leaves the crate unlocked."""
    fixture = nested_checkout / "tests" / "fixtures" / "demo"
    fixture.mkdir(parents=True)
    (fixture / "Cargo.toml").write_text(FIXTURE_MANIFEST, encoding="utf-8")
    if lock in FIXTURE_LOCKS:
        (fixture / "Cargo.lock").write_text(FIXTURE_LOCKS[lock], encoding="utf-8")


@when("I audit the nested cargo fixture lockfiles")
def when_audit(
    nested_checkout: pathlib.Path,
    cli_invocation: dict[str, RunResult],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Record the real CLI's table, diagnostics and exit status."""
    try:
        code = cli.main([
            "artefact",
            "rule",
            "run",
            "nested-cargo-fixture-lockfiles",
            "--repo",
            str(nested_checkout),
        ])
    except SystemExit as error:
        code = int(error.code or 0)
    captured = capsys.readouterr()
    cli_invocation["result"] = RunResult(
        stdout=captured.out, stderr=captured.err, returncode=code
    )


@then(parsers.cfparse("the nested cargo exit status is {status:d}"))
def then_status(cli_invocation: dict[str, RunResult], status: int) -> None:
    """Assert the exit status presented to an operator or CI job."""
    result = cli_invocation["result"]
    assert result.returncode == status, result.stderr or result.stdout


@then(parsers.parse('the nested cargo output contains "{finding}"'))
def then_output(cli_invocation: dict[str, RunResult], finding: str) -> None:
    """Assert the actual rendered verdict or rule identifier."""
    output = cli_invocation["result"].stdout
    assert finding in output, output
