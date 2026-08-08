"""Unit tests for estate cache handling."""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pygit2
import pytest

from concordat import estate_cache, xdg
from concordat.estate_cache import cache_destination
from concordat.estate_execution import EstateExecutionError, ensure_estate_cache
from tests.unit.conftest import _make_record

if typ.TYPE_CHECKING:  # pragma: no cover - type checking only
    from tests.conftest import GitRepo


def test_cache_destination_honours_xdg(xdg_env: dict[str, str], tmp_path: Path) -> None:
    """The cache path derives from XDG_CACHE_HOME, namespaced by owner.

    The record's own github_owner outranks the headline active owner.
    """
    cache_home = Path(xdg_env["XDG_CACHE_HOME"])
    xdg.set_active_owner("acme")
    record = _make_record(tmp_path / "estate.git")
    assert record.github_owner == "example", (
        f"record owner should outrank the active owner: {record.github_owner!r}"
    )

    destination = cache_destination(record)

    expected_root = cache_home / "concordat" / "owners" / "example" / "estates"
    assert destination == expected_root / record.alias, (
        f"expected {expected_root / record.alias}, got {destination}"
    )
    assert not expected_root.exists(), (
        "cache_destination is a pure query and must not create "
        f"{expected_root}; only ensure_estate_cache may"
    )


def test_ensure_estate_cache_creates_the_parent_before_cloning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The owner-scoped parent exists by the time the clone boundary runs.

    `cache_destination` no longer creates it, so this is the step that must,
    and it must happen before pygit2 is asked to write into it.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    record = _make_record(tmp_path / "estate.git")
    observed: list[bool] = []

    def fake_open_or_clone(
        _record: object,
        *,
        destination: Path,
        callbacks: object,
    ) -> object:
        observed.append(destination.parent.is_dir())
        destination.mkdir(parents=True, exist_ok=True)
        return pygit2.init_repository(str(destination))

    monkeypatch.setattr(estate_cache, "_open_or_clone_cache", fake_open_or_clone)

    ensure_estate_cache(record)

    assert observed == [True], (
        "the cache parent should already exist when the clone boundary is "
        f"reached, got {observed}"
    )


def test_ensure_estate_cache_clones_repository(
    git_repo: GitRepo, tmp_path: Path
) -> None:
    """Cloning a repository populates the estate cache."""
    record = _make_record(git_repo.path)
    cache_dir = tmp_path / "cache"

    workdir = ensure_estate_cache(record, cache_directory=cache_dir)

    assert workdir == cache_dir / record.alias, (
        f"expected {cache_dir / record.alias}, got {workdir}"
    )
    assert (workdir / ".git").exists(), (
        f"cloned cache workdir should contain a .git directory: {workdir}"
    )


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
    assert cached_repo.head.target != initial_head, (
        f"refreshed cache HEAD should advance from {initial_head}"
    )


def test_ensure_estate_cache_requires_origin(git_repo: GitRepo, tmp_path: Path) -> None:
    """Missing origin remote triggers an execution error."""
    record = _make_record(git_repo.path)
    cache_dir = tmp_path / "cache"
    workdir = ensure_estate_cache(record, cache_directory=cache_dir)
    repo = pygit2.Repository(str(workdir))
    repo.remotes.delete("origin")

    with pytest.raises(EstateExecutionError, match="origin"):
        ensure_estate_cache(record, cache_directory=cache_dir)
