"""Unit tests for concordat plan/apply CLI commands."""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest

from concordat import cli
from concordat.errors import ConcordatError
from concordat.estate import EstateRecord

if typ.TYPE_CHECKING:
    from concordat.estate_execution import ExecutionIO, ExecutionOptions


def _estate_record() -> EstateRecord:
    return EstateRecord(
        alias="core",
        repo_url="git@github.com:example/core.git",
        github_owner="example",
    )


def _apply_and_capture(
    monkeypatch: pytest.MonkeyPatch,
    *args: object,
    **kwargs: object,
) -> dict[str, object]:
    """Run cli.apply with a fake executor and capture forwarded kwargs."""
    record = _estate_record()
    monkeypatch.setattr(cli, "get_active_estate", lambda: record)
    monkeypatch.setenv("GITHUB_TOKEN", "token")

    captured: dict[str, object] = {}

    def fake_run_apply(
        record: EstateRecord,
        options: ExecutionOptions,
        io: ExecutionIO,
    ) -> tuple[int, Path]:
        captured["record"] = record
        captured["options"] = options
        captured["io"] = io
        return 0, Path("dummy-workdir")

    monkeypatch.setattr(cli, "run_apply", fake_run_apply)
    cli.apply(*args, **kwargs)
    return captured


def _get_applied_args(
    monkeypatch: pytest.MonkeyPatch,
    *args: object,
    **kwargs: object,
) -> tuple[str, ...]:
    """Return the extra_args tuple captured from cli.apply."""
    captured = _apply_and_capture(monkeypatch, *args, **kwargs)
    options = typ.cast("ExecutionOptions", captured["options"])
    return tuple(options.extra_args)


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

    def fake_run_plan(
        record: EstateRecord,
        options: ExecutionOptions,
        io: ExecutionIO,
    ) -> tuple[int, Path]:
        called["record"] = record
        called["options"] = options
        called["io"] = io
        return 2, Path("dummy-workdir")

    monkeypatch.setattr(cli, "run_plan", fake_run_plan)
    exit_code = cli.plan("-detailed-exitcode", keep_workdir=True)

    assert exit_code == 2
    assert called["record"] is record
    options = typ.cast("ExecutionOptions", called["options"])
    assert options.github_owner == "example"
    assert options.extra_args == ("-detailed-exitcode",)
    assert options.keep_workdir is True


def test_plan_allows_explicit_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """--github-token overrides the environment variable."""
    record = _estate_record()
    monkeypatch.setattr(cli, "get_active_estate", lambda: record)

    captured: dict[str, object] = {}

    def fake_run_plan(
        record: EstateRecord,
        options: ExecutionOptions,
        io: ExecutionIO,
    ) -> tuple[int, Path]:
        captured["record"] = record
        captured["options"] = options
        captured["io"] = io
        return 0, Path("dummy-workdir")

    monkeypatch.setattr(cli, "run_plan", fake_run_plan)
    auth_value = "placeholder-value"
    cli.plan(github_token=auth_value)

    assert typ.cast("ExecutionOptions", captured["options"]).github_token == auth_value


def test_apply_requires_auto_approve(monkeypatch: pytest.MonkeyPatch) -> None:
    """Apply refuses to run without --auto-approve."""
    record = _estate_record()
    monkeypatch.setattr(cli, "get_active_estate", lambda: record)
    monkeypatch.setenv("GITHUB_TOKEN", "token")

    with pytest.raises(ConcordatError):
        cli.apply()


@pytest.mark.parametrize(
    (
        "input_args",
        "kwargs",
        "expected_first_arg",
        "expected_auto_approve_count",
    ),
    [
        pytest.param(
            ("-var", "foo=1"),
            {"auto_approve": True},
            "-auto-approve",
            None,
            id="injects_auto_approve",
        ),
        pytest.param(
            ("-auto-approve", "-var", "foo=1"),
            {"auto_approve": True},
            "-auto-approve",
            1,
            id="does_not_duplicate_auto_approve",
        ),
        pytest.param(
            ("-auto-approve=true", "-var", "foo=1"),
            {"auto_approve": True},
            "-auto-approve=true",
            None,
            id="respects_auto_approve_true",
        ),
    ],
)
def test_apply_auto_approve_handling(
    monkeypatch: pytest.MonkeyPatch,
    input_args: tuple[str, ...],
    kwargs: dict[str, object],
    expected_first_arg: str,
    expected_auto_approve_count: int | None,
) -> None:
    """Apply correctly handles -auto-approve flag injection and deduplication."""
    extra_args = _get_applied_args(monkeypatch, *input_args, **kwargs)

    assert extra_args[0] == expected_first_arg
    assert list(extra_args[1:]) == ["-var", "foo=1"]
    if expected_auto_approve_count is not None:
        assert extra_args.count("-auto-approve") == expected_auto_approve_count


def test_apply_passes_keep_workdir(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting --keep-workdir forwards the flag to run_apply."""
    captured = _apply_and_capture(
        monkeypatch,
        auto_approve=True,
        keep_workdir=True,
    )

    assert typ.cast("ExecutionOptions", captured["options"]).keep_workdir is True
