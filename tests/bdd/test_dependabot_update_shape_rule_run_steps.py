"""Behavioural tests for `concordat artefact rule run` on DB-005.

These scenarios run the real policy over a real configuration and real local
actions through `cli.main`, so they cover the whole boundary an operator
meets: a checkout on disk, the envelope built from it, Conftest's verdict, the
process exit status and the rendered table. The Rego suite proves the clauses;
this proves the command delivers them.
"""

from __future__ import annotations

import typing as typ

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from concordat import cli

from .conftest import RunResult

if typ.TYPE_CHECKING:
    import pathlib

scenarios("features/dependabot_update_shape_rule_run.feature")

RULE_ID: typ.Final = "dependabot-update-shape"

_CONFIG: typ.Final = """\
version: 2
updates:
  - package-ecosystem: cargo
    directory: /
    schedule:
      interval: {interval}
    groups:
      rstest-bdd:
        patterns: ['rstest-bdd*']
      minor-and-patch:
        patterns: ['*']
{update_types}
  - package-ecosystem: github-actions
    directories:
{directories}
    schedule:
      interval: daily
    groups:
      minor-and-patch:
        patterns: ['*']
        update-types: [minor, patch]
"""

_UPDATE_TYPES: typ.Final = "        update-types: [minor, patch]"
_COVERED: typ.Final = "      - /\n      - /.github/actions/*"
_ROOT_ONLY: typ.Final = "      - /"


@pytest.fixture
def dependabot_checkout(tmp_path: pathlib.Path) -> pathlib.Path:
    """Provide the checkout directory one scenario audits."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    return checkout


def _write_checkout(checkout: pathlib.Path, config: str) -> None:
    """Write the configuration and one composite action."""
    github = checkout / ".github"
    action = github / "actions" / "setup"
    action.mkdir(parents=True)
    (action / "action.yml").write_text(
        "runs:\n  using: composite\n  steps: []\n", encoding="utf-8"
    )
    (github / "dependabot.yml").write_text(config, encoding="utf-8")


def _config(*, interval: str, update_types: str, directories: str) -> str:
    """Render the configuration with the named departures."""
    return _CONFIG.format(
        interval=interval, update_types=update_types, directories=directories
    )


@given("a checkout with no Dependabot configuration")
def given_no_configuration(dependabot_checkout: pathlib.Path) -> None:
    """Leave the checkout without `.github/dependabot.yml`."""


@given("a checkout whose Dependabot configuration has the estate shape")
def given_estate_shape(dependabot_checkout: pathlib.Path) -> None:
    """Write a configuration that every clause accepts."""
    _write_checkout(
        dependabot_checkout,
        _config(interval="daily", update_types=_UPDATE_TYPES, directories=_COVERED),
    )


@given("a checkout whose cargo entry runs weekly with an ungrouped-majors wildcard")
def given_weekly_wildcard(dependabot_checkout: pathlib.Path) -> None:
    """Write the pre-sweep shape: weekly, and a wildcard group taking majors."""
    _write_checkout(
        dependabot_checkout,
        _config(interval="weekly", update_types="", directories=_COVERED),
    )


@given("a checkout whose composite action is not in the github-actions directories")
def given_uncovered_action(dependabot_checkout: pathlib.Path) -> None:
    """Write the syrupy-mdast shape: workflows covered, the action not."""
    _write_checkout(
        dependabot_checkout,
        _config(interval="daily", update_types=_UPDATE_TYPES, directories=_ROOT_ONLY),
    )


@given("a checkout whose Dependabot configuration is not YAML")
def given_unreadable_configuration(dependabot_checkout: pathlib.Path) -> None:
    """Write a configuration the envelope cannot decode."""
    _write_checkout(dependabot_checkout, "updates: [\n")


@when("I audit the checkout for its Dependabot update shape")
def when_audit_checkout(
    dependabot_checkout: pathlib.Path,
    cli_invocation: dict[str, RunResult],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Invoke the command and record its output and exit status."""
    try:
        returncode = cli.main(
            ["artefact", "rule", "run", RULE_ID, "--repo", str(dependabot_checkout)],
        )
    except SystemExit as exc:
        returncode = int(exc.code or 0)
    captured = capsys.readouterr()
    cli_invocation["result"] = RunResult(
        stdout=captured.out,
        stderr=captured.err,
        returncode=returncode,
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
    assert "compliant" in stdout, stdout
    assert "DB-005" not in stdout, stdout


@then(parsers.cfparse('the audit output reports "{message}"'))
def then_audit_reports(cli_invocation: dict[str, RunResult], message: str) -> None:
    """Assert the rendered finding carries its rule and message."""
    stdout = cli_invocation["result"].stdout
    assert "DB-005" in stdout, stdout
    assert message in stdout, stdout
