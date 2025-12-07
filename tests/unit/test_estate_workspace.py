"""Unit tests for estate workspace lifecycle handling."""

from __future__ import annotations

import shutil
import typing as typ

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
