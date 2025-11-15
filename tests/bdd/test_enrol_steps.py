"""Behavioural tests for the concordat CLI."""

from __future__ import annotations

import pathlib
import subprocess
import sys
import typing as typ

import pygit2
import pytest
from pytest_bdd import given, parsers, scenarios, then, when
from ruamel.yaml import YAML

from concordat.enrol import CONCORDAT_DOCUMENT, CONCORDAT_FILENAME
from concordat.estate import EstateRecord, register_estate

from .conftest import RunResult

if typ.TYPE_CHECKING:
    from tests.conftest import GitRepo
else:
    GitRepo = typ.Any  # pragma: no cover - runtime fallback for type hints

scenarios("features/enrol.feature")


@pytest.fixture(autouse=True)
def configure_active_estate(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Record an active estate with a github_owner for CLI tests."""
    config_home = tmp_path / "xdg"
    config_home.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("CONCORDAT_SKIP_PLATFORM_PR", "1")
    config_path = config_home / "concordat" / "config.yaml"
    register_estate(
        EstateRecord(
            alias="core",
            repo_url="git@github.com:test-owner/platform.git",
            github_owner="test-owner",
        ),
        config_path=config_path,
        set_active_if_missing=True,
    )


@given("a git repository", target_fixture="repository_path")
def given_git_repository(git_repo: GitRepo) -> pathlib.Path:
    """Provide the repository path for the scenario."""
    repository = git_repo.repository
    try:
        repository.remotes.delete("origin")
    except KeyError:
        pass
    repository.remotes.create("origin", "git@github.com:test-owner/sample.git")
    return git_repo.path


@given("the repository is enrolled with concordat")
def given_repository_is_enrolled(repository_path: pathlib.Path) -> None:
    """Ensure the repository starts in an enrolled state."""
    result = _run_cli(["enrol", str(repository_path)])
    if result.returncode != 0:
        raise AssertionError(_cli_failure_message(result))


@given(parsers.cfparse('the repository remote targets owner "{owner}"'))
def given_repository_remote_owner(repository_path: pathlib.Path, owner: str) -> None:
    """Point the repository origin at a different GitHub owner."""
    repository = pygit2.Repository(str(repository_path / ".git"))
    try:
        repository.remotes.delete("origin")
    except KeyError:
        pass
    repository.remotes.create("origin", f"git@github.com:{owner}/sample.git")


def _run_cli(arguments: list[str]) -> RunResult:
    command = [sys.executable, "-m", "concordat.cli", *arguments]
    completed = subprocess.run(  # noqa: S603
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    return RunResult(
        stdout=completed.stdout,
        stderr=completed.stderr,
        returncode=completed.returncode,
    )


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
    """Execute the CLI enrol command."""
    result = _run_cli(["enrol", str(repository_path)])
    cli_invocation["result"] = result
    if result.returncode != 0:
        raise AssertionError(_cli_failure_message(result))


@when("I run concordat disenrol for that repository")
def when_run_concordat_disenrol(
    repository_path: pathlib.Path,
    cli_invocation: dict[str, RunResult],
) -> None:
    """Execute the CLI disenrol command."""
    result = _run_cli(["disenrol", str(repository_path)])
    cli_invocation["result"] = result
    if result.returncode != 0:
        raise AssertionError(_cli_failure_message(result))


@when("I attempt to enrol that repository")
def when_attempt_to_enrol(
    repository_path: pathlib.Path,
    cli_invocation: dict[str, RunResult],
) -> None:
    """Execute the CLI and capture the failure."""
    result = _run_cli(["enrol", str(repository_path)])
    cli_invocation["result"] = result


@then("the repository contains the concordat document")
def then_document_exists(repository_path: pathlib.Path) -> None:
    """Ensure that `.concordat` exists."""
    document_path = pathlib.Path(repository_path, CONCORDAT_FILENAME)
    assert document_path.exists()


@then("the concordat document declares enrolled true")
def then_document_enrolled_true(repository_path: pathlib.Path) -> None:
    """Verify the concordat document contents."""
    contents = _load_document(repository_path)
    assert contents == CONCORDAT_DOCUMENT


@then("the concordat document declares enrolled false")
def then_document_enrolled_false(repository_path: pathlib.Path) -> None:
    """Verify the concordat document was cleared."""
    contents = _load_document(repository_path)
    assert contents.get("enrolled") is False


@then("concordat reports the owner mismatch")
def then_owner_mismatch(cli_invocation: dict[str, RunResult]) -> None:
    """Ensure the CLI surfaces the github_owner guard."""
    result = cli_invocation["result"]
    assert result.returncode != 0
    output = result.stderr or result.stdout
    assert "github_owner" in output


def _load_document(repository_path: pathlib.Path) -> dict[str, object]:
    document_path = pathlib.Path(repository_path, CONCORDAT_FILENAME)
    parser = YAML(typ="safe")
    parser.version = (1, 2)
    parser.default_flow_style = False
    with document_path.open("r", encoding="utf-8") as handle:
        contents = parser.load(handle) or {}
    return dict(contents)
