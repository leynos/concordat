"""Behavioural tests for `concordat artefact rule run` on GC-001.

These scenarios run the real policy over a real Makefile, workflow, manifest and
prose through `cli.main`, so they cover the whole boundary an operator meets: a
checkout on disk, the envelope built from it, Conftest's verdict, the process
exit status and the rendered table. The Rego suite proves the clauses; this
proves the command delivers them.
"""

from __future__ import annotations

import re
import typing as typ

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from concordat import cli

from .conftest import RunResult

if typ.TYPE_CHECKING:
    import pathlib

scenarios("features/gate_workspace_coverage_rule_run.feature")

RULE_ID: typ.Final = "gate-workspace-coverage"
_MANIFESTS: typ.Final = {
    "Cargo.toml": (
        '[package]\nname = "app"\nversion = "0.1.0"\n\n'
        '[workspace]\nmembers = ["crates/*"]\n'
    ),
    "crates/b/Cargo.toml": '[package]\nname = "b"\nversion = "0.1.0"\n',
}


@pytest.fixture
def rust_checkout(tmp_path: pathlib.Path) -> pathlib.Path:
    """Provide the checkout directory one scenario audits."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    return checkout


def _write(checkout: pathlib.Path, files: dict[str, str]) -> None:
    """Write *files* under the checkout, creating directories."""
    for relative, text in files.items():
        path = checkout / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _workspace(checkout: pathlib.Path, *, lint: str, test: str = "@echo ok") -> None:
    """Write a two-member workspace whose targets run the given commands."""
    makefile = f".PHONY: lint test\n\nlint:\n\t{lint}\ntest:\n\t{test}\n"
    _write(checkout, {**_MANIFESTS, "Makefile": makefile})


@given("a checkout with no Cargo manifest")
def given_no_manifest(rust_checkout: pathlib.Path) -> None:
    """Leave a checkout that holds only prose."""
    _write(rust_checkout, {"README.md": "# Demo\n"})


@given("a workspace whose gate runs every tool with --workspace")
def given_compliant(rust_checkout: pathlib.Path) -> None:
    """Write a gate that selects the whole workspace for every tool."""
    _workspace(
        rust_checkout,
        lint="cargo clippy --workspace && whitaker --all -- --workspace",
        test="cargo nextest run --workspace && cargo test --doc --workspace",
    )


@given("a workspace whose lint target runs a bare cargo clippy")
def given_bare_clippy(rust_checkout: pathlib.Path) -> None:
    """Write a gate whose clippy selects the root package alone."""
    _workspace(rust_checkout, lint="cargo clippy --all-targets")


@given("a workspace whose lint target runs whitaker with only its own --all")
def given_whitaker_all(rust_checkout: pathlib.Path) -> None:
    """Write a gate whose Whitaker `--all` selects libraries, not packages."""
    _workspace(rust_checkout, lint="whitaker --all -- --all-targets")


@given("an AGENTS.md that shows the bare command")
def given_agents_example(rust_checkout: pathlib.Path) -> None:
    """Show the narrowing command to contributors."""
    _write(
        rust_checkout,
        {"AGENTS.md": "```sh\ncargo clippy --all-targets\n```\n"},
    )


@given("a workspace whose CI matrix runs cargo test without selecting members")
def given_matrix(rust_checkout: pathlib.Path) -> None:
    """Write a CI job whose every matrix entry narrows to the root package."""
    workflow = (
        "name: ci\non: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n"
        "    strategy:\n      matrix:\n        include:\n"
        "          - flags: --features sqlite\n"
        "          - flags: --features postgres\n"
        "    steps:\n      - run: cargo test ${{ matrix.flags }}\n"
    )
    _workspace(rust_checkout, lint="@echo lint")
    _write(rust_checkout, {".github/workflows/ci.yml": workflow})


@given("a workspace whose lint target runs a wrapper script for whitaker")
def given_wrapper(rust_checkout: pathlib.Path) -> None:
    """Write a gate that runs Whitaker through a script the audit cannot read."""
    _workspace(rust_checkout, lint="./scripts/run_whitaker.py --packages app")


@when("I audit the checkout for gate workspace coverage")
def when_audit_checkout(
    rust_checkout: pathlib.Path,
    cli_invocation: dict[str, RunResult],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Invoke the command on the scenario's checkout and record the result."""
    try:
        returncode = cli.main(
            ["artefact", "rule", "run", RULE_ID, "--repo", str(rust_checkout)],
        )
    except SystemExit as exc:
        returncode = int(exc.code or 0)
    captured = capsys.readouterr()
    cli_invocation["result"] = RunResult(
        stdout=captured.out, stderr=captured.err, returncode=returncode
    )


@then(parsers.cfparse("the audit exit status is {code:d}"))
def then_audit_exit_status(cli_invocation: dict[str, RunResult], code: int) -> None:
    """Assert the process exit status."""
    assert cli_invocation["result"].returncode == code, cli_invocation["result"].stdout


@then("the audit table reports zero findings")
def then_audit_clean(cli_invocation: dict[str, RunResult]) -> None:
    """Assert the table reports a compliant checkout."""
    stdout = cli_invocation["result"].stdout
    assert re.search(r"\bcompliant\b", stdout), stdout
    assert "GC-0" not in stdout, stdout


@then(parsers.cfparse('the audit output reports "{rule_id}" "{message}"'))
def then_audit_reports(
    cli_invocation: dict[str, RunResult], rule_id: str, message: str
) -> None:
    """Assert the rendered table carries a finding with this rule and text."""
    stdout = cli_invocation["result"].stdout
    assert re.sub(r"\s+", " ", stdout).count(rule_id) >= 1, stdout
    assert message in re.sub(r"\s+", " ", stdout), stdout
