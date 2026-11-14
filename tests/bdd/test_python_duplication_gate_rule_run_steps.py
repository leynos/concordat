"""Behavioural tests for `concordat artefact rule run` on NS-001 to NS-004.

These scenarios run the real policy over a real `pyproject.toml` and tree
through `cli.main`, so they cover the whole boundary an operator meets: a
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

scenarios("features/python_duplication_gate_rule_run.feature")

RULE_ID: typ.Final = "python-duplication-gate"
_CODE: typ.Final = "def run() -> None:\n    pass\n"


@pytest.fixture
def nose_checkout(tmp_path: pathlib.Path) -> pathlib.Path:
    """Provide the checkout directory one scenario audits."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    return checkout


def _write_checkout(
    checkout: pathlib.Path, *, roots: str = '["pkg"]', top: int = 0, exclude: str
) -> None:
    """Write a package with a test tree and the `[tool.nose]` table for it."""
    (checkout / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n\n[tool.nose]\n'
        f'version = "0.20.0"\nroots = {roots}\ntop = {top}\nexclude = {exclude}\n',
        encoding="utf-8",
    )
    (checkout / "pkg" / "tests").mkdir(parents=True)
    (checkout / "pkg" / "mod.py").write_text(_CODE, encoding="utf-8")
    (checkout / "pkg" / "tests" / "test_mod.py").write_text(_CODE, encoding="utf-8")


@given("a checkout with no nose table")
def given_no_nose(nose_checkout: pathlib.Path) -> None:
    """Leave a project file that configures only another tool."""
    (nose_checkout / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8"
    )


@given("a checkout whose scan is uncapped and excludes its test tree")
def given_compliant(nose_checkout: pathlib.Path) -> None:
    """Write the reference scope."""
    _write_checkout(nose_checkout, exclude='["**/tests/**"]')


@given("a checkout whose scan is capped at 30 families")
def given_capped(nose_checkout: pathlib.Path) -> None:
    """Write a scope with the detector's default cap."""
    _write_checkout(nose_checkout, top=30, exclude='["**/tests/**"]')


@given("a checkout whose scan includes its test tree")
def given_test_tree(nose_checkout: pathlib.Path) -> None:
    """Write a scope that excludes nothing."""
    _write_checkout(nose_checkout, exclude="[]")


@given("a checkout with a scan root that selects no Python file")
def given_empty_root(nose_checkout: pathlib.Path) -> None:
    """Write a scope with a second root that holds only prose."""
    _write_checkout(nose_checkout, roots='["pkg", "docs"]', exclude='["**/tests/**"]')
    (nose_checkout / "docs").mkdir()
    (nose_checkout / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")


@given("a checkout whose scan roots are not a list")
def given_bad_roots(nose_checkout: pathlib.Path) -> None:
    """Write a scope whose `roots` is a bare string."""
    _write_checkout(nose_checkout, roots='"pkg"', exclude='["**/tests/**"]')


@when("I audit the checkout for its duplication gate")
def when_audit_checkout(
    nose_checkout: pathlib.Path,
    cli_invocation: dict[str, RunResult],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Invoke the command on the scenario's checkout and record the result."""
    try:
        returncode = cli.main(
            ["artefact", "rule", "run", RULE_ID, "--repo", str(nose_checkout)],
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
    assert "NS-0" not in stdout, stdout


@then(parsers.cfparse('the audit output reports "{rule_id}" "{message}"'))
def then_audit_reports(
    cli_invocation: dict[str, RunResult], rule_id: str, message: str
) -> None:
    """Assert the rendered table carries one finding with this rule and text."""
    stdout = cli_invocation["result"].stdout
    assert any(rule_id in line and message in line for line in stdout.splitlines()), (
        stdout
    )
