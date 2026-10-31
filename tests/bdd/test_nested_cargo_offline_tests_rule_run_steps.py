"""Exercise the nested cargo offline rule through the actual operator CLI."""

from __future__ import annotations

import typing as typ

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from concordat import cli

from .conftest import RunResult

if typ.TYPE_CHECKING:
    import pathlib

scenarios("features/nested_cargo_offline_tests_rule_run.feature")

RULE_ID = "nested-cargo-offline-tests"
SPAWN = 'fn run() {{\n    Command::new("cargo").args(["{words}"]).status();\n}}\n'
MAKEFILE_COLD = """\
CARGO ?= cargo
CARGO_LOCKED ?=

test: ## Run tests
\t$(CARGO) nextest run $(CARGO_LOCKED)
"""
MAKEFILE_FETCHED = """\
CARGO ?= cargo
CARGO_LOCKED ?= --locked

.PHONY: fetch test

fetch: ## Fetch dependencies before tests
\t$(CARGO) fetch $(CARGO_LOCKED)

test: fetch ## Run tests
\t$(CARGO) nextest run $(CARGO_LOCKED)
"""
EVIDENCE = {
    "frozen": "--frozen build",
    "none": "build",
}


@pytest.fixture
def offline_checkout(tmp_path: pathlib.Path) -> pathlib.Path:
    """Return the scenario's isolated checkout directory."""
    return tmp_path


@given(parsers.parse('a test that runs cargo with "{evidence}" offline evidence'))
def given_spawn(offline_checkout: pathlib.Path, evidence: str) -> None:
    """Write a test-scope spawn carrying the named offline evidence."""
    source = SPAWN.format(words=EVIDENCE[evidence])
    (offline_checkout / "tests").mkdir(parents=True)
    (offline_checkout / "tests/build_discovery_bdd.rs").write_text(
        source, encoding="utf-8"
    )


@given("a Makefile whose test target fetches nothing first")
def given_cold_makefile(offline_checkout: pathlib.Path) -> None:
    """Write a test target with no prefetch before its runner."""
    (offline_checkout / "Makefile").write_text(MAKEFILE_COLD, encoding="utf-8")


@given("a Makefile whose test target fetches first")
def given_fetched_makefile(offline_checkout: pathlib.Path) -> None:
    """Write a test target that depends on a `cargo fetch --locked` target."""
    (offline_checkout / "Makefile").write_text(MAKEFILE_FETCHED, encoding="utf-8")


@given("a test source the reader cannot decode")
def given_undecodable_source(offline_checkout: pathlib.Path) -> None:
    """Write a test file that is not UTF-8, which the reader must report."""
    (offline_checkout / "tests").mkdir(parents=True)
    (offline_checkout / "tests/broken.rs").write_bytes(b"fn run() { \xff\xfe }\n")


@when("I audit the nested cargo offline tests")
def when_audit(
    offline_checkout: pathlib.Path,
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
            str(offline_checkout),
        ])
    except SystemExit as error:
        code = int(error.code or 0)
    captured = capsys.readouterr()
    cli_invocation["result"] = RunResult(
        stdout=captured.out, stderr=captured.err, returncode=code
    )


@then(parsers.cfparse("the nested cargo offline exit status is {status:d}"))
def then_status(cli_invocation: dict[str, RunResult], status: int) -> None:
    """Assert the exit status presented to an operator or CI job."""
    result = cli_invocation["result"]
    assert result.returncode == status, result.stderr or result.stdout


@then(parsers.parse('the nested cargo offline output contains "{text}"'))
def then_output(cli_invocation: dict[str, RunResult], text: str) -> None:
    """Assert the actual rendered verdict, rule identifier or path."""
    output = cli_invocation["result"].stdout
    assert text in output, output
