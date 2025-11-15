"""Behavioural tests for the `concordat ls` command."""

from __future__ import annotations

import io
import types
import typing as typ
import unittest.mock as mock
from contextlib import redirect_stdout

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from concordat import cli

from .conftest import RunResult

scenarios("features/ls.feature")


@pytest.fixture(autouse=True)
def unset_github_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure ls tests do not inherit a real GITHUB_TOKEN."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)


@pytest.fixture
def listing_state() -> dict[str, object]:
    """Scenario state for namespace listing tests."""
    return {}


@given(
    parsers.cfparse('GitHub namespaces "{namespaces}"'),
    target_fixture="github_namespaces",
)
def given_github_namespaces(namespaces: str) -> list[str]:
    """Collect namespaces from the scenario string."""
    return [item.strip() for item in namespaces.split(",") if item.strip()]


@given(parsers.cfparse('the GitHub API returns repositories "{repositories}"'))
def given_expected_repositories(
    repositories: str,
    listing_state: dict[str, object],
) -> None:
    """Store the repositories that the mocked API should return."""
    values = [value.strip() for value in repositories.split(",") if value.strip()]
    listing_state["repositories"] = values


@given(parsers.cfparse('an active estate records github owner "{owner}"'))
def given_active_estate(
    monkeypatch: pytest.MonkeyPatch,
    owner: str,
    listing_state: dict[str, object],
) -> None:
    """Mock the active estate for namespace defaults."""
    record = types.SimpleNamespace(
        alias="core",
        github_owner=owner,
        repo_url="git@github.com:test/core.git",
        branch="main",
        inventory_path="tofu/inventory/repositories.yaml",
    )
    listing_state["owner"] = owner
    monkeypatch.setattr("concordat.cli.get_active_estate", lambda: record)


def _expected_token() -> str | None:
    """Helper mirroring cli.ls token resolution for assertions."""
    return None


@when("I run concordat ls for those namespaces")
def when_run_concordat_ls(
    monkeypatch: pytest.MonkeyPatch,
    github_namespaces: list[str],
    cli_invocation: dict[str, RunResult],
    listing_state: dict[str, object],
) -> None:
    """Execute the CLI with the mocked GitHub API."""
    expected = typ.cast(list[str], listing_state.get("repositories", []))
    mock_list = mock.Mock(return_value=expected)
    monkeypatch.setattr("concordat.cli.list_namespace_repositories", mock_list)

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        cli.ls(*github_namespaces)
    cli_invocation["result"] = RunResult(
        stdout=buffer.getvalue(),
        stderr="",
        returncode=0,
    )
    mock_list.assert_called_once_with(
        tuple(github_namespaces),
        token=_expected_token(),
    )


@when("I run concordat ls without specifying namespaces")
def when_run_concordat_ls_without_namespaces(
    monkeypatch: pytest.MonkeyPatch,
    cli_invocation: dict[str, RunResult],
    listing_state: dict[str, object],
) -> None:
    """Execute ls with the recorded estate owner."""
    expected = typ.cast(list[str], listing_state.get("repositories", []))
    owner = listing_state.get("owner")
    assert owner, "owner fixture must set github_owner"

    mock_list = mock.Mock(return_value=expected)
    monkeypatch.setattr("concordat.cli.list_namespace_repositories", mock_list)

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        cli.ls()
    cli_invocation["result"] = RunResult(
        stdout=buffer.getvalue(),
        stderr="",
        returncode=0,
    )
    mock_list.assert_called_once_with(
        (typ.cast(str, owner),),
        token=_expected_token(),
    )


@then("the CLI prints the repository SSH URLs")
def then_cli_prints_repositories(
    cli_invocation: dict[str, RunResult],
    listing_state: dict[str, object],
) -> None:
    """Validate CLI output."""
    expected = typ.cast(list[str], listing_state.get("repositories", []))
    result = cli_invocation["result"]
    assert result.returncode == 0
    output_lines = [
        line.strip() for line in result.stdout.strip().splitlines() if line.strip()
    ]
    assert output_lines == expected
