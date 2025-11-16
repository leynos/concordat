"""Unit tests for estate execution helpers."""

from __future__ import annotations

import io
import shutil
import typing as typ

import pygit2
import pytest

from concordat.estate import EstateRecord
from concordat.estate_execution import (
    EstateExecutionError,
    _emit_stream,
    cache_root,
    ensure_estate_cache,
    estate_workspace,
    write_tfvars,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

    from tests.conftest import GitRepo
else:  # pragma: no cover - fallback for runtime typing
    Path = typ.Any
    GitRepo = typ.Any


def _make_record(repo_path: Path, alias: str = "core") -> EstateRecord:
    return EstateRecord(
        alias=alias,
        repo_url=str(repo_path),
        github_owner="example",
    )


def test_cache_root_honours_xdg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The cache path is derived from XDG_CACHE_HOME when provided."""
    cache_home = tmp_path / "xdg-cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home))

    root = cache_root()

    assert root == cache_home / "concordat" / "estates"
    assert root.exists()


def test_ensure_estate_cache_clones_repository(
    git_repo: GitRepo, tmp_path: Path
) -> None:
    """Cloning a repository populates the estate cache."""
    record = _make_record(git_repo.path)
    cache_dir = tmp_path / "cache"

    workdir = ensure_estate_cache(record, cache_directory=cache_dir)

    assert workdir == cache_dir / record.alias
    assert (workdir / ".git").exists()


def test_ensure_estate_cache_fetches_updates(git_repo: GitRepo, tmp_path: Path) -> None:
    """Refreshing the cache resets it to the remote HEAD."""
    record = _make_record(git_repo.path)
    cache_dir = tmp_path / "cache"

    workdir = ensure_estate_cache(record, cache_directory=cache_dir)
    cached_repo = pygit2.Repository(str(workdir))
    initial_head = cached_repo.head.target

    (git_repo.path / "NEW.txt").write_text("update\n", encoding="utf-8")
    repo = pygit2.Repository(str(git_repo.path))
    index = repo.index
    index.add("NEW.txt")
    index.write()
    tree_oid = index.write_tree()
    sig = pygit2.Signature("Test User", "test@example.com")
    repo.create_commit(
        "refs/heads/main", sig, sig, "update", tree_oid, [repo.head.target]
    )

    ensure_estate_cache(record, cache_directory=cache_dir)

    cached_repo = pygit2.Repository(str(workdir))
    assert cached_repo.head.target != initial_head


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


def test_write_tfvars_records_owner(tmp_path: Path) -> None:
    """The generated terraform.tfvars contains github_owner."""
    workdir = tmp_path / "work"
    workdir.mkdir()

    tfvars_path = write_tfvars(workdir, github_owner="example")

    assert tfvars_path.read_text(encoding="utf-8") == 'github_owner = "example"\n'


def test_write_tfvars_requires_owner(tmp_path: Path) -> None:
    """write_tfvars raises when github_owner is missing."""
    workdir = tmp_path / "work"
    workdir.mkdir()

    with pytest.raises(EstateExecutionError):
        write_tfvars(workdir, github_owner="")


def test_emit_stream_appends_newline() -> None:
    """_emit_stream ensures trailing newline when missing."""
    buffer = io.StringIO()

    _emit_stream("line", buffer)

    assert buffer.getvalue() == "line\n"


def test_emit_stream_preserves_newline() -> None:
    """Existing trailing newline is preserved."""
    buffer = io.StringIO()

    _emit_stream("line\n", buffer)

    assert buffer.getvalue() == "line\n"


def test_emit_stream_skips_empty_values() -> None:
    """None or empty text produces no output."""
    buffer = io.StringIO()

    _emit_stream(None, buffer)
    _emit_stream("", buffer)

    assert buffer.getvalue() == ""
