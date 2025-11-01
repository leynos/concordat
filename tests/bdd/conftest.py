"""Shared fixtures for behaviour-driven CLI tests."""

from __future__ import annotations

import dataclasses

import pytest


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
