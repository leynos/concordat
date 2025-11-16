"""Unit tests for concordat plan/apply CLI commands."""

from __future__ import annotations

from pathlib import Path

import pytest

from concordat import cli
from concordat.errors import ConcordatError
from concordat.estate import EstateRecord


def _estate_record() -> EstateRecord:
    return EstateRecord(
        alias="core",
        repo_url="git@github.com:example/core.git",
        github_owner="example",
    )


def test_plan_requires_active_estate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plan fails when no estate is active."""
    monkeypatch.setattr(cli, "get_active_estate", lambda: None)

    with pytest.raises(ConcordatError):
        cli.plan()


def test_plan_runs_with_injected_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plan resolves the token and forwards arguments to run_plan."""
    record = _estate_record()
    monkeypatch.setattr(cli, "get_active_estate", lambda: record)
    monkeypatch.setenv("GITHUB_TOKEN", "token")

    called: dict[str, object] = {}

    def fake_run_plan(record: EstateRecord, **kwargs: object) -> tuple[int, Path]:
        called["record"] = record
        called.update(kwargs)
        return 2, Path("dummy-workdir")

    monkeypatch.setattr(cli, "run_plan", fake_run_plan)
    exit_code = cli.plan("-detailed-exitcode", keep_workdir=True)

    assert exit_code == 2
    assert called["record"] is record
    assert called["github_owner"] == "example"
    assert called["extra_args"] == ("-detailed-exitcode",)
    assert called["keep_workdir"] is True


def test_plan_allows_explicit_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """--github-token overrides the environment variable."""
    record = _estate_record()
    monkeypatch.setattr(cli, "get_active_estate", lambda: record)

    captured: dict[str, object] = {}

    def fake_run_plan(record: EstateRecord, **kwargs: object) -> tuple[int, Path]:
        captured["record"] = record
        captured.update(kwargs)
        return 0, Path("dummy-workdir")

    monkeypatch.setattr(cli, "run_plan", fake_run_plan)
    auth_value = "placeholder-value"
    cli.plan(github_token=auth_value)

    assert captured["github_token"] == auth_value


def test_apply_requires_auto_approve(monkeypatch: pytest.MonkeyPatch) -> None:
    """Apply refuses to run without --auto-approve."""
    record = _estate_record()
    monkeypatch.setattr(cli, "get_active_estate", lambda: record)
    monkeypatch.setenv("GITHUB_TOKEN", "token")

    with pytest.raises(ConcordatError):
        cli.apply()


def test_apply_injects_auto_approve(monkeypatch: pytest.MonkeyPatch) -> None:
    """Apply prefixes -auto-approve before calling run_apply."""
    record = _estate_record()
    monkeypatch.setattr(cli, "get_active_estate", lambda: record)
    monkeypatch.setenv("GITHUB_TOKEN", "token")

    called: dict[str, object] = {}

    def fake_run_apply(record: EstateRecord, **kwargs: object) -> tuple[int, Path]:
        called["record"] = record
        called.update(kwargs)
        return 0, Path("dummy-workdir")

    monkeypatch.setattr(cli, "run_apply", fake_run_apply)
    cli.apply("-var", "foo=1", auto_approve=True)

    assert called["extra_args"][0] == "-auto-approve"
    assert list(called["extra_args"][1:]) == ["-var", "foo=1"]


def test_apply_passes_keep_workdir(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting --keep-workdir forwards the flag to run_apply."""
    record = _estate_record()
    monkeypatch.setattr(cli, "get_active_estate", lambda: record)
    monkeypatch.setenv("GITHUB_TOKEN", "token")

    captured: dict[str, object] = {}

    def fake_run_apply(record: EstateRecord, **kwargs: object) -> tuple[int, Path]:
        captured["record"] = record
        captured.update(kwargs)
        return 0, Path("dummy-workdir")

    monkeypatch.setattr(cli, "run_apply", fake_run_apply)
    cli.apply(auto_approve=True, keep_workdir=True)

    assert captured["keep_workdir"] is True
