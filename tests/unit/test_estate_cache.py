"""Unit tests for estate cache handling."""

from __future__ import annotations

import typing as typ

import pygit2
import pytest

from concordat.estate_cache import cache_root
from concordat.estate_execution import EstateExecutionError, ensure_estate_cache
from tests.unit.conftest import _make_record

if typ.TYPE_CHECKING:  # pragma: no cover - type checking only
    from pathlib import Path

    from tests.conftest import GitRepo


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


def test_ensure_estate_cache_bare_destination(
    git_repo: GitRepo,
    tmp_path: Path,
) -> None:
    """Bare repositories at the cache destination raise an error."""
    record = _make_record(git_repo.path)
    cache_dir = tmp_path / "cache"
    bare_path = cache_dir / record.alias
    pygit2.init_repository(str(bare_path), bare=True)

    with pytest.raises(EstateExecutionError, match="bare"):
        ensure_estate_cache(record, cache_directory=cache_dir)


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


def test_ensure_estate_cache_requires_origin(git_repo: GitRepo, tmp_path: Path) -> None:
    """Missing origin remote triggers an execution error."""
    record = _make_record(git_repo.path)
    cache_dir = tmp_path / "cache"
    workdir = ensure_estate_cache(record, cache_directory=cache_dir)
    repo = pygit2.Repository(str(workdir))
    repo.remotes.delete("origin")

    with pytest.raises(EstateExecutionError, match="origin"):
        ensure_estate_cache(record, cache_directory=cache_dir)
