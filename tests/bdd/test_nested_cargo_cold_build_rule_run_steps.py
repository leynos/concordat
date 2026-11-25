"""Exercise the nested cargo cold-build rule through the actual operator CLI."""

from __future__ import annotations

import typing as typ

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from concordat import cli

from .conftest import RunResult

if typ.TYPE_CHECKING:
    import pathlib

scenarios("features/nested_cargo_cold_build_rule_run.feature")

RULE_ID = "nested-cargo-cold-build"
MANIFEST = '[package]\nname = "app"\nversion = "0.0.0"\n'
TRYBUILD_TEST = (
    "#[test]\nfn compile_fail() {\n    let t = trybuild::TestCases::new();\n"
    '    t.compile_fail("tests/ui/*.rs");\n}\n'
)
SETUP_SCRIPT = (
    '[scripts.setup.prepare]\ncommand = "make prepare"\n\n'
    '[[profile.default.scripts]]\nfilter = "all()"\nsetup = "prepare"\n'
)
# The first pass has its own build and filtered run ahead of it; the second builds
# `--no-default-features`, which that run never compiled (the rstest-bdd shape).
ONE_LEG_PREPARED_MAKEFILE = (
    ".PHONY: test\n"
    "test:\n"
    "\tcargo build --all-features\n"
    "\tcargo nextest run --all-features -E 'test(/prepare$$/)'\n"
    "\tcargo nextest run --all-features\n"
    "\tcargo nextest run --no-default-features\n"
)
TEMPORARY_TARGET = (
    "fn build() {\n    let scratch = tempfile::tempdir().unwrap();\n"
    '    std::process::Command::new("cargo")\n        .arg("build")\n'
    '        .env("CARGO_TARGET_DIR", scratch.path())\n'
    "        .status()\n        .unwrap();\n}\n"
)


@pytest.fixture
def cold_checkout(tmp_path: pathlib.Path) -> pathlib.Path:
    """Return the scenario's isolated checkout directory."""
    (tmp_path / "Cargo.toml").write_text(MANIFEST, encoding="utf-8")
    (tmp_path / "tests").mkdir()
    return tmp_path


@given("a test that drives trybuild")
def given_trybuild(cold_checkout: pathlib.Path) -> None:
    """Write a test that compiles UI cases through trybuild."""
    (cold_checkout / "tests/ui.rs").write_text(TRYBUILD_TEST, encoding="utf-8")


@given("a nextest setup script that covers every test")
def given_setup_script(cold_checkout: pathlib.Path) -> None:
    """Write a nextest configuration whose setup script selects every test."""
    (cold_checkout / ".config").mkdir()
    (cold_checkout / ".config/nextest.toml").write_text(SETUP_SCRIPT, encoding="utf-8")


@given("a Makefile that prepares only the first of two test passes")
def given_one_pass_prepared(cold_checkout: pathlib.Path) -> None:
    """Write a Makefile whose second pass builds features nothing prepared."""
    (cold_checkout / "Makefile").write_text(ONE_LEG_PREPARED_MAKEFILE, encoding="utf-8")


@given("a test that builds into a temporary target directory")
def given_temporary_target(cold_checkout: pathlib.Path) -> None:
    """Write a test whose nested cargo builds into a temporary directory."""
    (cold_checkout / "tests/scenario.rs").write_text(TEMPORARY_TARGET, encoding="utf-8")


@given("a plain library with a plain test")
def given_plain(cold_checkout: pathlib.Path) -> None:
    """Write a library and a test that start no nested build."""
    (cold_checkout / "tests/add.rs").write_text(
        "#[test]\nfn adds() {\n    assert_eq!(1 + 1, 2);\n}\n", encoding="utf-8"
    )


@given("a test source the reader cannot decode")
def given_undecodable_source(cold_checkout: pathlib.Path) -> None:
    """Write a test file that is not UTF-8, which the reader must report."""
    (cold_checkout / "tests/broken.rs").write_bytes(b"fn run() { \xff\xfe }\n")


@when("I audit the nested cargo cold build")
def when_audit(
    cold_checkout: pathlib.Path,
    cli_invocation: dict[str, RunResult],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Record the real CLI's table, diagnostics and exit status."""
    try:
        code = cli.main([
            "artefact",
            "rule",
            "run",
            RULE_ID,
            "--repo",
            str(cold_checkout),
        ])
    except SystemExit as error:
        code = int(error.code or 0)
    captured = capsys.readouterr()
    cli_invocation["result"] = RunResult(
        stdout=captured.out, stderr=captured.err, returncode=code
    )


@then(parsers.cfparse("the nested cargo cold build exit status is {status:d}"))
def then_status(cli_invocation: dict[str, RunResult], status: int) -> None:
    """Assert the exit status presented to an operator or CI job."""
    result = cli_invocation["result"]
    assert result.returncode == status, result.stderr or result.stdout


@then(parsers.parse('the nested cargo cold build output contains "{text}"'))
def then_output(cli_invocation: dict[str, RunResult], text: str) -> None:
    """Assert the actual rendered verdict, rule identifier or path."""
    output = cli_invocation["result"].stdout
    assert text in output, output
