"""Behavioural tests for the `concordat ls` command."""

from __future__ import annotations

import io
import unittest.mock as mock
from contextlib import redirect_stdout

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from concordat import cli

from .conftest import RunResult

scenarios("features/ls.feature")


@pytest.fixture
def listing_state() -> dict[str, list[str]]:
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
    listing_state: dict[str, list[str]],
) -> None:
    """Store the repositories that the mocked API should return."""
    values = [value.strip() for value in repositories.split(",") if value.strip()]
    listing_state["repositories"] = values


@when("I run concordat ls for those namespaces")
def when_run_concordat_ls(
    monkeypatch: pytest.MonkeyPatch,
    github_namespaces: list[str],
    cli_invocation: dict[str, RunResult],
    listing_state: dict[str, list[str]],
) -> None:
    """Execute the CLI with the mocked GitHub API."""
    expected = listing_state.get("repositories", [])
    async_mock = mock.AsyncMock(return_value=expected)
    monkeypatch.setattr("concordat.cli.list_namespace_repositories", async_mock)

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        cli.ls(*github_namespaces)
    cli_invocation["result"] = RunResult(
        stdout=buffer.getvalue(),
        stderr="",
        returncode=0,
    )
    async_mock.assert_awaited_once_with(tuple(github_namespaces), token=None)


@then("the CLI prints the repository SSH URLs")
def then_cli_prints_repositories(
    cli_invocation: dict[str, RunResult],
    listing_state: dict[str, list[str]],
) -> None:
    """Validate CLI output."""
    expected = listing_state.get("repositories", [])
    result = cli_invocation["result"]
    assert result.returncode == 0
    output_lines = [
        line.strip() for line in result.stdout.strip().splitlines() if line.strip()
    ]
    assert output_lines == expected
