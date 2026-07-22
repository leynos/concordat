"""Behavioural tests for `concordat owner` commands."""

from __future__ import annotations

import shlex
import typing as typ

from pytest_bdd import parsers, scenarios, then, when

from concordat import cli

from .conftest import RunResult

if typ.TYPE_CHECKING:
    import pytest

scenarios("features/owner.feature")


@when(parsers.cfparse('I run "concordat {arguments}"'))
def when_run_owner_command(
    arguments: str,
    cli_invocation: dict[str, RunResult],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Invoke the CLI with the given arguments and record the outcome."""
    try:
        returncode = cli.main(shlex.split(arguments))
    except SystemExit as exc:
        returncode = int(exc.code or 0)
    captured = capsys.readouterr()
    cli_invocation["result"] = RunResult(
        stdout=captured.out,
        stderr=captured.err,
        returncode=returncode,
    )


@then(parsers.cfparse("the owner command exits with {code:d}"))
def then_owner_exit_status(
    cli_invocation: dict[str, RunResult],
    code: int,
) -> None:
    """Assert the recorded exit status."""
    result = cli_invocation["result"]
    assert result.returncode == code, result.stderr or result.stdout


@then(parsers.cfparse('the owner output is "{expected}"'))
def then_owner_output_is(
    cli_invocation: dict[str, RunResult],
    expected: str,
) -> None:
    """Assert the command printed exactly the expected line."""
    stdout = cli_invocation["result"].stdout
    assert stdout.strip() == expected, (
        f"expected owner output {expected!r}, got stdout {stdout!r}"
    )


@then(parsers.cfparse('the owner output mentions "{fragment}"'))
def then_owner_output_mentions(
    cli_invocation: dict[str, RunResult],
    fragment: str,
) -> None:
    """Assert the output (stdout or stderr) contains the fragment."""
    result = cli_invocation["result"]
    assert fragment in result.stdout + result.stderr, (
        f"expected fragment {fragment!r} in output; "
        f"stdout {result.stdout!r}, stderr {result.stderr!r}"
    )
