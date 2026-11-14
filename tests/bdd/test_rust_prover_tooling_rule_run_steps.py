"""Exercise the rust-prover-tooling rule through the actual operator CLI."""

from __future__ import annotations

import typing as typ

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from concordat import cli

from .conftest import RunResult

if typ.TYPE_CHECKING:
    import pathlib

scenarios("features/rust_prover_tooling_rule_run.feature")

REF = "592558f7183b554fc749696246984c5d7300d029"
ACTION = "leynos/shared-actions/.github/actions/install-prover-tools@"
SHA = "a" * 40
MAKEFILE = (
    f"PROVER_TOOLS ?= uvx --from git+https://github.com/leynos/rust-prover-tools@{REF}"
    " prover-tools\n\nverus:\n\t$(PROVER_TOOLS) verus run --repo-root .\n"
)
HEAD = (
    "name: Verus\non: push\njobs:\n  verify:\n    runs-on: ubuntu-latest\n    steps:\n"
)
SHARED = f"      - uses: {ACTION}{SHA}\n        with:\n          tools: verus\n"
RUN = "      - run: make verus\n"
CHECKOUTS: dict[str, dict[str, str]] = {
    "installs through the shared action": {
        "Makefile": MAKEFILE,
        ".github/workflows/verus.yml": HEAD + SHARED + RUN,
    },
    "installs Verus in a workflow step": {
        "Makefile": MAKEFILE,
        ".github/workflows/verus.yml": HEAD
        + "      - run: prover-tools verus install\n"
        + RUN,
    },
    "uses the shared action by tag": {
        "Makefile": MAKEFILE,
        ".github/workflows/verus.yml": HEAD
        + f"      - uses: {ACTION}v1\n        with:\n          tools: verus\n"
        + RUN,
    },
    "keeps a local installer script": {
        "Makefile": MAKEFILE,
        ".github/workflows/verus.yml": HEAD + SHARED + RUN,
        "scripts/install_verus.sh": "echo hi\n",
    },
    "uses no prover": {"Makefile": "build:\n\tcargo build\n"},
}


@pytest.fixture
def prover_checkout(tmp_path: pathlib.Path) -> pathlib.Path:
    """Return the scenario's isolated checkout directory."""
    return tmp_path


@given(parsers.parse('a repository that "{wiring}"'))
def given_repository(prover_checkout: pathlib.Path, wiring: str) -> None:
    """Write the named repository's files."""
    for relative, text in CHECKOUTS[wiring].items():
        path = prover_checkout / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


@when("I audit the prover tooling")
def when_audit(
    prover_checkout: pathlib.Path,
    cli_invocation: dict[str, RunResult],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Record the real CLI's table, diagnostics and exit status."""
    try:
        code = cli.main([
            "artefact",
            "rule",
            "run",
            "rust-prover-tooling",
            "--repo",
            str(prover_checkout),
        ])
    except SystemExit as error:
        code = int(error.code or 0)
    captured = capsys.readouterr()
    cli_invocation["result"] = RunResult(
        stdout=captured.out, stderr=captured.err, returncode=code
    )


@then(parsers.cfparse("the prover tooling exit status is {status:d}"))
def then_status(cli_invocation: dict[str, RunResult], status: int) -> None:
    """Assert the exit status presented to an operator or CI job."""
    result = cli_invocation["result"]
    assert result.returncode == status, result.stderr or result.stdout


@then(parsers.parse('the prover tooling output contains "{finding}"'))
def then_output(cli_invocation: dict[str, RunResult], finding: str) -> None:
    """Assert the actual rendered verdict or rule identifier."""
    output = cli_invocation["result"].stdout
    assert finding in output, output
