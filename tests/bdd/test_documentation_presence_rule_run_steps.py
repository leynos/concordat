"""Behavioural tests for `concordat artefact rule run` on DG-001 and DG-002.

These scenarios run the real policy over a real Makefile (parsed by the pinned
`makeutil`), a real Python tree and a real `pyproject.toml` through `cli.main`,
so they cover the whole boundary an operator meets: a checkout on disk, the
envelope built from it, Conftest's verdict, the process exit status and the
rendered table. The Rego suite proves the clauses; this proves the command
delivers them.
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

scenarios("features/documentation_presence_rule_run.feature")

RULE_ID: typ.Final = "documentation-presence"
_PYTHON_FILES: typ.Final = ("pkg/core.py", "tests/test_core.py", "scripts/tool.py")
_DOCSTRING: typ.Final = '"""Documented."""\n'
_GUARDED: typ.Final = (
    '\t@report="$$($(INTERROGATE) $(FLAGS) $(TARGETS))"; \\\n'
    "\t  status=$$?; printf '%s\\n' \"$$report\"; \\\n"
    '\t  test "$$status" -eq 0 || exit "$$status"; \\\n'
    "\t  if printf '%s\\n' \"$$report\" | grep -q '| *MISSED |'; then exit 1; fi\n"
)
_UNGUARDED: typ.Final = "\t$(INTERROGATE) $(FLAGS) $(TARGETS)\n"
_MAKEFILE: typ.Final = """\
INTERROGATE ?= interrogate
TARGETS = {targets}
FLAGS = {flags}

.PHONY: lint interrogate
lint: interrogate
\truff check .

interrogate:
{recipe}"""


@pytest.fixture
def python_checkout(tmp_path: pathlib.Path) -> pathlib.Path:
    """Provide the checkout directory one scenario audits."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "README.md").write_text("# Checkout\n", encoding="utf-8")
    return checkout


def _write_python(checkout: pathlib.Path) -> None:
    """Write the documented Python files every scenario but the first owns."""
    for relative in _PYTHON_FILES:
        path = checkout / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_DOCSTRING, encoding="utf-8")


def _write_makefile(
    checkout: pathlib.Path,
    *,
    targets: str = "pkg scripts tests",
    flags: str = "-vv --fail-under 100",
    recipe: str = _GUARDED,
) -> None:
    """Write a Makefile whose `lint` reaches the interrogate target."""
    (checkout / "Makefile").write_text(
        _MAKEFILE.format(targets=targets, flags=flags, recipe=recipe),
        encoding="utf-8",
    )


@given("a checkout that owns no Python")
def given_no_python(python_checkout: pathlib.Path) -> None:
    """Leave the checkout with prose and a Makefile that never mentions Python."""
    (python_checkout / "Makefile").write_text(
        "build:\n\tcc -o x x.c\n", encoding="utf-8"
    )


@given("a checkout with an exact interrogate gate over every Python directory")
def given_exact_gate(python_checkout: pathlib.Path) -> None:
    """Write the reference wiring: verbose, threshold 100, guarded."""
    _write_python(python_checkout)
    _write_makefile(python_checkout)


@given("a checkout whose lint recipe never runs interrogate")
def given_no_gate(python_checkout: pathlib.Path) -> None:
    """Own Python, and lint it with Ruff alone."""
    _write_python(python_checkout)
    (python_checkout / "Makefile").write_text(
        "lint:\n\truff check .\n", encoding="utf-8"
    )


@given("a checkout whose gate skips the tests and ignores nested functions")
def given_relaxed_gate(python_checkout: pathlib.Path) -> None:
    """Cover only the package and relax the gate."""
    _write_python(python_checkout)
    _write_makefile(
        python_checkout,
        targets="pkg",
        flags="-vv --fail-under 100 --ignore-nested-functions",
    )


@given("a checkout whose gate does not fail on a MISSED row")
def given_rounded_gate(python_checkout: pathlib.Path) -> None:
    """Rely on the percentage alone, at the default verbosity."""
    _write_python(python_checkout)
    _write_makefile(python_checkout, flags="--fail-under 100", recipe=_UNGUARDED)


@when("I audit the checkout for its documentation gate")
def when_audit_checkout(
    python_checkout: pathlib.Path,
    cli_invocation: dict[str, RunResult],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Audit the scenario's checkout and record the command's result."""
    try:
        returncode = cli.main(
            ["artefact", "rule", "run", RULE_ID, "--repo", str(python_checkout)],
        )
    except SystemExit as exc:
        returncode = int(exc.code or 0)
    captured = capsys.readouterr()
    cli_invocation["result"] = RunResult(
        stdout=captured.out, stderr=captured.err, returncode=returncode
    )


@then(parsers.cfparse("the audit exit status is {code:d}"))
def then_audit_exit_status(cli_invocation: dict[str, RunResult], code: int) -> None:
    """Assert the recorded exit status."""
    result = cli_invocation["result"]
    assert result.returncode == code, result.stderr or result.stdout


@then("the audit table reports zero findings")
def then_audit_clean(cli_invocation: dict[str, RunResult]) -> None:
    """Assert the table names the compliant verdict and no findings."""
    stdout = cli_invocation["result"].stdout
    assert re.search(r"\bcompliant\b", stdout), stdout
    assert "DG-0" not in stdout, stdout


@then(parsers.cfparse('the audit output reports "{rule_id}" "{message}"'))
def then_audit_reports(
    cli_invocation: dict[str, RunResult], rule_id: str, message: str
) -> None:
    """Assert the rendered table carries one finding with this rule and text."""
    stdout = cli_invocation["result"].stdout
    assert any(rule_id in line and message in line for line in stdout.splitlines()), (
        stdout
    )
