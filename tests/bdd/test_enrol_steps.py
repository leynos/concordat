"""Behavioural tests for the concordat CLI."""

from __future__ import annotations

import dataclasses
import pathlib
import subprocess
import sys
import typing as typ

import pytest
from pytest_bdd import given, scenarios, then, when
from ruamel.yaml import YAML

from concordat.enrol import CONCORDAT_DOCUMENT, CONCORDAT_FILENAME

if typ.TYPE_CHECKING:
    from tests.conftest import GitRepo
else:
    GitRepo = typ.Any  # pragma: no cover - runtime fallback for type hints

scenarios("features/enrol.feature")


@dataclasses.dataclass
class RunResult:
    """Record CLI invocation results."""

    stdout: str
    stderr: str
    returncode: int


@pytest.fixture
def cli_invocation() -> dict[str, RunResult]:
    """Collect the result of running the CLI within a scenario."""
    return {}


@given("a git repository", target_fixture="repository_path")
def given_git_repository(git_repo: GitRepo) -> pathlib.Path:
    """Provide the repository path for the scenario."""
    return git_repo.path


def _cli_failure_message(result: RunResult) -> str:
    return (
        "concordat CLI failed "
        f"(exit {result.returncode}):\n{result.stderr or result.stdout}"
    )


@when("I run concordat enrol for that repository")
def when_run_concordat_enrol(
    repository_path: pathlib.Path,
    cli_invocation: dict[str, RunResult],
) -> None:
    """Execute the CLI against the repository."""
    command = [
        sys.executable,
        "-m",
        "concordat.cli",
        "enrol",
        str(repository_path),
    ]
    # The command is constructed from trusted values within the test harness.
    completed = subprocess.run(  # noqa: S603
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    result = RunResult(
        stdout=completed.stdout,
        stderr=completed.stderr,
        returncode=completed.returncode,
    )
    cli_invocation["result"] = result
    if completed.returncode != 0:
        raise AssertionError(_cli_failure_message(result))


@then("the repository contains the concordat document")
def then_document_exists(repository_path: pathlib.Path) -> None:
    """Ensure that `.concordat` exists."""
    document_path = pathlib.Path(repository_path, CONCORDAT_FILENAME)
    assert document_path.exists()


@then("the concordat document declares enrolled true")
def then_document_contents(repository_path: pathlib.Path) -> None:
    """Verify the concordat document contents."""
    document_path = pathlib.Path(repository_path, CONCORDAT_FILENAME)
    parser = YAML(typ="safe")
    parser.version = (1, 2)
    parser.default_flow_style = False
    with document_path.open("r", encoding="utf-8") as handle:
        contents = parser.load(handle)
    assert dict(contents) == CONCORDAT_DOCUMENT
