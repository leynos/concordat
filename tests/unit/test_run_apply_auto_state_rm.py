"""Unit tests covering automatic state removal during run_apply."""

from __future__ import annotations

import io
import typing as typ
from types import SimpleNamespace

from concordat.estate_execution import ExecutionIO, ExecutionOptions, run_apply
from tests.unit.conftest import _make_record

if typ.TYPE_CHECKING:  # pragma: no cover
    import pytest

    from tests.conftest import GitRepo


def test_run_apply_offers_to_forget_resources_on_prevent_destroy(
    monkeypatch: pytest.MonkeyPatch,
    git_repo: GitRepo,
) -> None:
    """When prevent_destroy blocks deletes, concordat offers `tofu state rm`."""
    tofu_root = git_repo.path / "tofu"
    tofu_root.mkdir()
    (tofu_root / "main.tofu").write_text("terraform {}\n", encoding="utf-8")

    monkeypatch.setattr(
        "concordat.estate_execution.ensure_estate_cache",
        lambda *_, **__: git_repo.path,
    )
    monkeypatch.setattr("concordat.estate_execution._can_prompt", lambda: True)
    monkeypatch.setattr("concordat.user_interaction.sys.stdin", io.StringIO("y\n"))

    calls: list[list[str]] = []

    class _TofuWithStateRm:
        def __init__(self, cwd: str, env: dict[str, str]) -> None:
            self.cwd = cwd
            self.env = env

        applied_once = False

        def _run(self, args: list[str], *, raise_on_error: bool = False) -> object:
            calls.append(list(args))
            verb = args[0] if args else ""

            if verb == "apply" and not self.applied_once:
                self.applied_once = True
                return SimpleNamespace(
                    stdout="",
                    stderr=(
                        "Error: Instance cannot be destroyed\n"
                        'Resource module.repository[\\"leynos/test-repo\\"].'
                        "github_repository.this has lifecycle.prevent_destroy set\n"
                    ),
                    returncode=1,
                )

            if verb == "state" and args[1:] == ["list"]:
                return SimpleNamespace(
                    stdout=(
                        'module.repository["leynos/test-repo"].github_repository.this\n'
                    ),
                    stderr="",
                    returncode=0,
                )

            if verb == "state" and args[1] == "rm":
                return SimpleNamespace(stdout="", stderr="", returncode=0)

            return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr("concordat.estate_execution.Tofu", _TofuWithStateRm)
    monkeypatch.setattr("concordat.tofu_runner.Tofu", _TofuWithStateRm)

    io_streams = ExecutionIO(stdout=io.StringIO(), stderr=io.StringIO())
    options = ExecutionOptions(
        github_owner="leynos",
        github_token="token",  # noqa: S106
        extra_args=("-auto-approve",),
    )

    exit_code, _ = run_apply(_make_record(git_repo.path), options, io_streams)

    assert exit_code == 0
    assert ["state", "list"] in calls
    assert [
        "state",
        "rm",
        'module.repository["leynos/test-repo"].github_repository.this',
    ] in calls
    assert calls.count(["apply", "-auto-approve"]) == 2


def test_run_apply_prevent_destroy_non_interactive_no_state_rm(
    monkeypatch: pytest.MonkeyPatch,
    git_repo: GitRepo,
) -> None:
    """Non-interactive runs should not invoke state list/rm, but show suggestion."""
    tofu_root = git_repo.path / "tofu"
    tofu_root.mkdir()
    (tofu_root / "main.tofu").write_text("terraform {}\n", encoding="utf-8")

    monkeypatch.setattr(
        "concordat.estate_execution.ensure_estate_cache",
        lambda *_, **__: git_repo.path,
    )
    monkeypatch.setattr("concordat.estate_execution._can_prompt", lambda: False)

    calls: list[list[str]] = []

    class _TofuPreventDestroy:
        def __init__(self, cwd: str, env: dict[str, str]) -> None:
            self.cwd = cwd
            self.env = env

        def _run(self, args: list[str], *, raise_on_error: bool = False) -> object:
            calls.append(list(args))
            verb = args[0] if args else ""

            if verb == "apply":
                return SimpleNamespace(
                    stdout="",
                    stderr=(
                        "Error: Instance cannot be destroyed\n"
                        'Resource module.repository[\\"leynos/test-repo\\"].'
                        "github_repository.this has lifecycle.prevent_destroy set\n"
                    ),
                    returncode=1,
                )

            return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr("concordat.estate_execution.Tofu", _TofuPreventDestroy)
    monkeypatch.setattr("concordat.tofu_runner.Tofu", _TofuPreventDestroy)

    stderr_buffer = io.StringIO()
    io_streams = ExecutionIO(stdout=io.StringIO(), stderr=stderr_buffer)
    options = ExecutionOptions(
        github_owner="leynos",
        github_token="token",  # noqa: S106
        extra_args=("-auto-approve",),
    )

    exit_code, _ = run_apply(_make_record(git_repo.path), options, io_streams)

    assert exit_code != 0
    assert not any(call[0] == "state" for call in calls)
    stderr_output = stderr_buffer.getvalue()
    assert "tofu state rm" in stderr_output


def test_run_apply_prevent_destroy_user_answers_no(
    monkeypatch: pytest.MonkeyPatch,
    git_repo: GitRepo,
) -> None:
    """User declining state removal preserves failure and skips state commands."""
    tofu_root = git_repo.path / "tofu"
    tofu_root.mkdir()
    (tofu_root / "main.tofu").write_text("terraform {}\n", encoding="utf-8")

    monkeypatch.setattr(
        "concordat.estate_execution.ensure_estate_cache",
        lambda *_, **__: git_repo.path,
    )
    monkeypatch.setattr("concordat.estate_execution._can_prompt", lambda: True)
    monkeypatch.setattr("concordat.user_interaction.sys.stdin", io.StringIO("n\n"))

    calls: list[list[str]] = []

    class _TofuPreventDestroy:
        def __init__(self, cwd: str, env: dict[str, str]) -> None:
            self.cwd = cwd
            self.env = env

        def _run(self, args: list[str], *, raise_on_error: bool = False) -> object:
            calls.append(list(args))
            verb = args[0] if args else ""

            if verb == "apply":
                return SimpleNamespace(
                    stdout="",
                    stderr=(
                        "Error: Instance cannot be destroyed\n"
                        'Resource module.repository[\\"leynos/test-repo\\"].'
                        "github_repository.this has lifecycle.prevent_destroy set\n"
                    ),
                    returncode=1,
                )

            return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr("concordat.estate_execution.Tofu", _TofuPreventDestroy)
    monkeypatch.setattr("concordat.tofu_runner.Tofu", _TofuPreventDestroy)

    io_streams = ExecutionIO(stdout=io.StringIO(), stderr=io.StringIO())
    options = ExecutionOptions(
        github_owner="leynos",
        github_token="token",  # noqa: S106
        extra_args=("-auto-approve",),
    )

    exit_code, _ = run_apply(_make_record(git_repo.path), options, io_streams)

    assert exit_code != 0
    assert not any(call[0] == "state" for call in calls)
    assert calls.count(["apply", "-auto-approve"]) == 1


def test_run_apply_prevent_destroy_state_list_no_matches(
    monkeypatch: pytest.MonkeyPatch,
    git_repo: GitRepo,
) -> None:
    """When state list finds no matching addresses, no state rm and no retry."""
    tofu_root = git_repo.path / "tofu"
    tofu_root.mkdir()
    (tofu_root / "main.tofu").write_text("terraform {}\n", encoding="utf-8")

    monkeypatch.setattr(
        "concordat.estate_execution.ensure_estate_cache",
        lambda *_, **__: git_repo.path,
    )
    monkeypatch.setattr("concordat.estate_execution._can_prompt", lambda: True)
    monkeypatch.setattr("concordat.user_interaction.sys.stdin", io.StringIO("y\n"))

    calls: list[list[str]] = []

    class _TofuStateListNoMatch:
        def __init__(self, cwd: str, env: dict[str, str]) -> None:
            self.cwd = cwd
            self.env = env

        def _run(self, args: list[str], *, raise_on_error: bool = False) -> object:
            calls.append(list(args))
            verb = args[0] if args else ""

            if verb == "apply":
                return SimpleNamespace(
                    stdout="",
                    stderr=(
                        "Error: Instance cannot be destroyed\n"
                        'Resource module.repository[\\"leynos/test-repo\\"].'
                        "github_repository.this has lifecycle.prevent_destroy set\n"
                    ),
                    returncode=1,
                )

            if verb == "state" and args[1:] == ["list"]:
                # Return empty state list - no matching addresses
                return SimpleNamespace(stdout="", stderr="", returncode=0)

            if verb == "state" and args[1] == "rm":
                msg = "state rm should not be called with no matches"
                raise AssertionError(msg)

            return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr("concordat.estate_execution.Tofu", _TofuStateListNoMatch)
    monkeypatch.setattr("concordat.tofu_runner.Tofu", _TofuStateListNoMatch)

    stderr_buffer = io.StringIO()
    io_streams = ExecutionIO(stdout=io.StringIO(), stderr=stderr_buffer)
    options = ExecutionOptions(
        github_owner="leynos",
        github_token="token",  # noqa: S106
        extra_args=("-auto-approve",),
    )

    exit_code, _ = run_apply(_make_record(git_repo.path), options, io_streams)

    assert exit_code != 0
    assert ["state", "list"] in calls
    assert calls.count(["apply", "-auto-approve"]) == 1


def test_run_apply_prevent_destroy_state_rm_failure(
    monkeypatch: pytest.MonkeyPatch,
    git_repo: GitRepo,
) -> None:
    """When state rm fails, the overall exit code should be non-zero."""
    tofu_root = git_repo.path / "tofu"
    tofu_root.mkdir()
    (tofu_root / "main.tofu").write_text("terraform {}\n", encoding="utf-8")

    monkeypatch.setattr(
        "concordat.estate_execution.ensure_estate_cache",
        lambda *_, **__: git_repo.path,
    )
    monkeypatch.setattr("concordat.estate_execution._can_prompt", lambda: True)
    monkeypatch.setattr("concordat.user_interaction.sys.stdin", io.StringIO("y\n"))

    calls: list[list[str]] = []

    class _TofuStateRmFails:
        def __init__(self, cwd: str, env: dict[str, str]) -> None:
            self.cwd = cwd
            self.env = env

        def _run(self, args: list[str], *, raise_on_error: bool = False) -> object:
            calls.append(list(args))
            verb = args[0] if args else ""

            if verb == "apply":
                return SimpleNamespace(
                    stdout="",
                    stderr=(
                        "Error: Instance cannot be destroyed\n"
                        'Resource module.repository[\\"leynos/test-repo\\"].'
                        "github_repository.this has lifecycle.prevent_destroy set\n"
                    ),
                    returncode=1,
                )

            if verb == "state" and args[1:] == ["list"]:
                return SimpleNamespace(
                    stdout=(
                        'module.repository["leynos/test-repo"].github_repository.this\n'
                    ),
                    stderr="",
                    returncode=0,
                )

            if verb == "state" and args[1] == "rm":
                return SimpleNamespace(
                    stdout="",
                    stderr="Error: failed to remove state entry",
                    returncode=1,
                )

            return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr("concordat.estate_execution.Tofu", _TofuStateRmFails)
    monkeypatch.setattr("concordat.tofu_runner.Tofu", _TofuStateRmFails)

    io_streams = ExecutionIO(stdout=io.StringIO(), stderr=io.StringIO())
    options = ExecutionOptions(
        github_owner="leynos",
        github_token="token",  # noqa: S106
        extra_args=("-auto-approve",),
    )

    exit_code, _ = run_apply(_make_record(git_repo.path), options, io_streams)

    assert exit_code != 0
    assert ["state", "list"] in calls
    assert any(call[0] == "state" and call[1] == "rm" for call in calls)


def test_run_apply_state_list_returns_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    git_repo: GitRepo,
) -> None:
    """When state list itself fails, no state rm should be attempted."""
    tofu_root = git_repo.path / "tofu"
    tofu_root.mkdir()
    (tofu_root / "main.tofu").write_text("terraform {}\n", encoding="utf-8")

    monkeypatch.setattr(
        "concordat.estate_execution.ensure_estate_cache",
        lambda *_, **__: git_repo.path,
    )
    monkeypatch.setattr("concordat.estate_execution._can_prompt", lambda: True)
    monkeypatch.setattr("concordat.user_interaction.sys.stdin", io.StringIO("y\n"))

    calls: list[list[str]] = []

    class _TofuStateListFails:
        def __init__(self, cwd: str, env: dict[str, str]) -> None:
            self.cwd = cwd
            self.env = env

        def _run(self, args: list[str], *, raise_on_error: bool = False) -> object:
            calls.append(list(args))
            verb = args[0] if args else ""

            if verb == "apply":
                return SimpleNamespace(
                    stdout="",
                    stderr=(
                        "Error: Instance cannot be destroyed\n"
                        'Resource module.repository[\\"leynos/test-repo\\"].'
                        "github_repository.this has lifecycle.prevent_destroy set\n"
                    ),
                    returncode=1,
                )

            if verb == "state" and args[1:] == ["list"]:
                return SimpleNamespace(
                    stdout="",
                    stderr="Error: failed to list state",
                    returncode=1,
                )

            if verb == "state" and args[1] == "rm":
                msg = "state rm should not be called if state list fails"
                raise AssertionError(msg)

            return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr("concordat.estate_execution.Tofu", _TofuStateListFails)
    monkeypatch.setattr("concordat.tofu_runner.Tofu", _TofuStateListFails)

    io_streams = ExecutionIO(stdout=io.StringIO(), stderr=io.StringIO())
    options = ExecutionOptions(
        github_owner="leynos",
        github_token="token",  # noqa: S106
        extra_args=("-auto-approve",),
    )

    exit_code, _ = run_apply(_make_record(git_repo.path), options, io_streams)

    assert exit_code != 0
    assert ["state", "list"] in calls
    assert not any(
        call[0] == "state" and len(call) > 1 and call[1] == "rm" for call in calls
    )
