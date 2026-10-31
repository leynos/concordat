"""Exercise the nested cargo manifest rule through the actual operator CLI."""

from __future__ import annotations

import typing as typ

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from concordat import cli

from .conftest import RunResult

if typ.TYPE_CHECKING:
    import pathlib

scenarios("features/nested_cargo_fixture_manifests_rule_run.feature")

FIXTURE_DIRECTORY = "tests/fixtures/demo"
DEPENDENCIES = {
    "serde": 'serde = "1"',
    "a path": 'sibling = { path = "../../.." }',
}


@pytest.fixture
def manifest_checkout(tmp_path: pathlib.Path) -> pathlib.Path:
    """Return the scenario's isolated checkout directory."""
    return tmp_path


@given(parsers.parse('a workspace with a fixture crate that declares "{dependency}"'))
def given_fixture(manifest_checkout: pathlib.Path, dependency: str) -> None:
    """Write a workspace root and a standalone fixture crate."""
    (manifest_checkout / "Cargo.toml").write_text(
        '[package]\nname = "root"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    fixture = manifest_checkout / FIXTURE_DIRECTORY
    fixture.mkdir(parents=True)
    (fixture / "Cargo.toml").write_text(
        f'[package]\nname = "fixture"\nversion = "0.0.0"\n'
        f"[dependencies]\n{DEPENDENCIES[dependency]}\n",
        encoding="utf-8",
    )


@given(parsers.parse('a Dependabot configuration "{coverage}" the fixture directory'))
def given_dependabot(manifest_checkout: pathlib.Path, coverage: str) -> None:
    """Write a cargo entry that lists the fixture directory, or only the root."""
    directories = ["/", f"/{FIXTURE_DIRECTORY}"] if coverage == "lists" else ["/"]
    listed = "".join(f'      - "{item}"\n' for item in directories)
    config = manifest_checkout / ".github" / "dependabot.yml"
    config.parent.mkdir()
    config.write_text(
        'version: 2\nupdates:\n  - package-ecosystem: "cargo"\n    directories:\n'
        + listed
        + "    schedule:\n      interval: daily\n",
        encoding="utf-8",
    )


@when("I audit the nested cargo fixture manifests")
def when_audit(
    manifest_checkout: pathlib.Path,
    cli_invocation: dict[str, RunResult],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Record the real CLI's table, diagnostics and exit status."""
    try:
        code = cli.main([
            "artefact",
            "rule",
            "run",
            "nested-cargo-fixture-manifests",
            "--repo",
            str(manifest_checkout),
        ])
    except SystemExit as error:
        code = int(error.code or 0)
    captured = capsys.readouterr()
    cli_invocation["result"] = RunResult(
        stdout=captured.out, stderr=captured.err, returncode=code
    )


@then(parsers.cfparse("the nested manifest exit status is {status:d}"))
def then_status(cli_invocation: dict[str, RunResult], status: int) -> None:
    """Assert the exit status presented to an operator or CI job."""
    result = cli_invocation["result"]
    assert result.returncode == status, result.stderr or result.stdout


@then(parsers.parse('the nested manifest output contains "{finding}"'))
def then_output(cli_invocation: dict[str, RunResult], finding: str) -> None:
    """Assert the actual rendered verdict or rule identifier."""
    output = cli_invocation["result"].stdout
    assert finding in output, output
