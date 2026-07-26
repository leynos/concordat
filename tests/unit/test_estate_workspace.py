"""Unit tests for estate workspace lifecycle handling."""

from __future__ import annotations

import dataclasses
import shutil
import tempfile
import typing as typ

from concordat import xdg
from concordat.estate_execution import estate_workspace
from tests.unit.conftest import _make_record

if typ.TYPE_CHECKING:  # pragma: no cover - type checking only
    from pathlib import Path

    from tests.conftest import GitRepo


def test_estate_workspace_cleans_up(git_repo: GitRepo, tmp_path: Path) -> None:
    """Workspaces are removed when keep_workdir is False."""
    record = _make_record(git_repo.path)
    cache_dir = tmp_path / "cache"

    with estate_workspace(record, cache_directory=cache_dir) as workdir:
        workspace_path = workdir
        assert (workspace_path / ".git").exists()

    assert not workspace_path.exists()


def test_estate_workspace_preserves_directory_when_requested(
    git_repo: GitRepo,
    tmp_path: Path,
) -> None:
    """Workspaces remain on disk when keep_workdir=True."""
    record = _make_record(git_repo.path)
    cache_dir = tmp_path / "cache"

    with estate_workspace(
        record,
        cache_directory=cache_dir,
        keep_workdir=True,
    ) as workdir:
        workspace_path = workdir
        marker = workspace_path / "marker.txt"
        marker.write_text("marker\n", encoding="utf-8")

    assert workspace_path.exists()
    shutil.rmtree(workspace_path)


def test_estate_workspace_uses_owner_scoped_run_dir(
    git_repo: GitRepo,
    tmp_path: Path,
) -> None:
    """The run directory nests under the owner's XDG state runs directory."""
    record = _make_record(git_repo.path)  # github_owner="example"
    cache_dir = tmp_path / "cache"

    with estate_workspace(record, cache_directory=cache_dir) as workdir:
        assert workdir.parent == xdg.owner_runs_dir("example"), (
            f"workspace should nest under the owner run dir "
            f"{xdg.owner_runs_dir('example')}, got {workdir}"
        )


def test_estate_workspace_falls_back_to_temp_without_owner(
    git_repo: GitRepo,
    tmp_path: Path,
) -> None:
    """With no resolvable owner, the workspace falls back to the system temp dir."""
    record = dataclasses.replace(_make_record(git_repo.path), github_owner=None)
    cache_dir = tmp_path / "cache"

    with estate_workspace(record, cache_directory=cache_dir) as workdir:
        assert workdir.parent != xdg.owner_runs_dir("example"), (
            f"a record without an owner must not use an owner run dir, got {workdir}"
        )
        assert str(workdir).startswith(tempfile.gettempdir()), (
            f"no-owner workspace should use the system temp dir "
            f"{tempfile.gettempdir()}, got {workdir}"
        )
