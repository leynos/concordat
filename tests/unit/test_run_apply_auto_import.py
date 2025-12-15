"""Unit tests covering automatic import during run_apply."""

from __future__ import annotations

import io
import typing as typ
from types import SimpleNamespace

from concordat.estate_execution import ExecutionIO, ExecutionOptions, run_apply
from tests.unit.conftest import _make_record

if typ.TYPE_CHECKING:  # pragma: no cover
    import pytest

    from tests.conftest import GitRepo


def test_run_apply_offers_to_import_existing_github_repositories(
    monkeypatch: pytest.MonkeyPatch,
    git_repo: GitRepo,
) -> None:
    """When GitHub returns 422 name already exists, concordat imports and retries."""
    tofu_root = git_repo.path / "tofu"
    tofu_root.mkdir()
    (tofu_root / "main.tofu").write_text("terraform {}\n", encoding="utf-8")

    monkeypatch.setattr(
        "concordat.estate_execution.ensure_estate_cache",
        lambda *_, **__: git_repo.path,
    )
    monkeypatch.setattr("concordat.estate_execution._can_prompt", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "y")

    calls: list[list[str]] = []

    class _TofuWithImport:
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
                        'vertex "module.repository[\\"leynos/test-repo\\"].'
                        'github_repository.this" error: POST https://api.github.com/'
                        "user/repos: 422 Repository creation failed. "
                        "[{Resource:Repository Field:name Code:custom "
                        "Message:name already exists on this account}]"
                    ),
                    returncode=1,
                )

            return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr("concordat.estate_execution.Tofu", _TofuWithImport)

    io_streams = ExecutionIO(stdout=io.StringIO(), stderr=io.StringIO())
    options = ExecutionOptions(
        github_owner="leynos",
        github_token="token",  # noqa: S106
        extra_args=("-auto-approve",),
    )

    exit_code, _ = run_apply(_make_record(git_repo.path), options, io_streams)

    assert exit_code == 0
    assert [
        "import",
        'module.repository["leynos/test-repo"].github_repository.this',
        "leynos/test-repo",
    ] in calls
