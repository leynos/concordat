"""Git repository caching for estate workspaces."""

from __future__ import annotations

import shutil
import typing as typ
from pathlib import Path
from tempfile import mkdtemp

import pygit2

from .errors import ConcordatError
from .gitutils import build_remote_callbacks

if typ.TYPE_CHECKING:
    from pygit2.enums import ResetMode as _Pygit2ResetMode

    from .estate import EstateRecord

XDG_CACHE_HOME = "XDG_CACHE_HOME"
CACHE_SEGMENT = ("concordat", "estates")
ERROR_ALIAS_REQUIRED = "Estate alias is required to cache the repository."
ERROR_BARE_CACHE = "Cached estate {alias!r} is bare; remove {destination} and retry."
ERROR_MISSING_ORIGIN = (
    "Cached estate is missing the 'origin' remote; remove it and retry."
)
ERROR_MISSING_BRANCH = "Branch {branch!r} is missing from remote {remote!r}."


class EstateCacheError(ConcordatError):
    """Raised when caching an estate repository fails."""


def cache_root(env: dict[str, str] | None = None) -> Path:
    """Return the directory used for caching estate repositories."""
    import os

    source = env if env is not None else os.environ
    root = source.get(XDG_CACHE_HOME)
    base = Path(root).expanduser() if root else Path.home() / ".cache"
    path = base
    for segment in CACHE_SEGMENT:
        path /= segment
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_estate_cache(
    record: EstateRecord,
    *,
    cache_directory: Path | None = None,
) -> Path:
    """Ensure the estate repository is cloned and fresh in the cache."""
    if not record.alias:
        raise EstateCacheError(ERROR_ALIAS_REQUIRED)

    destination = _cache_destination(record.alias, cache_directory)
    callbacks = build_remote_callbacks(record.repo_url)
    repository = _open_or_clone_cache(
        record,
        destination=destination,
        callbacks=callbacks,
    )
    return _workdir_from_repository(record.alias, destination, repository)


def _cache_destination(alias: str, cache_directory: Path | None) -> Path:
    root = cache_directory or cache_root()
    return root / alias


def _open_or_clone_cache(
    record: EstateRecord,
    *,
    destination: Path,
    callbacks: pygit2.RemoteCallbacks | None,
) -> pygit2.Repository:
    try:
        if destination.exists():
            repository = pygit2.Repository(str(destination))
            if repository.is_bare:
                detail = ERROR_BARE_CACHE.format(
                    alias=record.alias,
                    destination=destination,
                )
                raise EstateCacheError(detail)
            _refresh_cache(repository, record.branch, callbacks)
            return repository

        destination.parent.mkdir(parents=True, exist_ok=True)
        return pygit2.clone_repository(
            record.repo_url,
            str(destination),
            checkout_branch=record.branch,
            callbacks=callbacks,
        )
    except pygit2.GitError as error:  # pragma: no cover - pygit2 raises opaque errors
        detail = f"Failed to sync estate {record.alias!r}: {error}"
        raise EstateCacheError(detail) from error


def _workdir_from_repository(
    alias: str,
    destination: Path,
    repository: pygit2.Repository,
) -> Path:
    if workdir := repository.workdir:
        return Path(workdir)
    detail = ERROR_BARE_CACHE.format(alias=alias, destination=destination)
    raise EstateCacheError(detail)


def clone_into_temp(cache_path: Path, prefix: str) -> Path:
    """Copy the cached repository into an isolated temporary directory."""
    temp_root = Path(mkdtemp(prefix=f"concordat-{prefix}-"))
    shutil.rmtree(temp_root)
    shutil.copytree(cache_path, temp_root, symlinks=True)
    return temp_root


def _refresh_cache(
    repository: pygit2.Repository,
    branch: str,
    callbacks: pygit2.RemoteCallbacks | None,
) -> None:
    """Fetch and reset the cached repository to the remote branch."""
    remote = _fetch_origin_remote(repository, callbacks)
    commit = _resolve_remote_commit(repository, remote, branch)
    _sync_local_branch(repository, branch, commit)
    _reset_to_commit(repository, commit)


def _fetch_origin_remote(
    repository: pygit2.Repository,
    callbacks: pygit2.RemoteCallbacks | None,
) -> pygit2.Remote:
    try:
        remote = repository.remotes["origin"]
    except KeyError as error:  # pragma: no cover - defensive guard
        raise EstateCacheError(ERROR_MISSING_ORIGIN) from error
    remote.fetch(callbacks=callbacks)
    return remote


def _resolve_remote_commit(
    repository: pygit2.Repository,
    remote: pygit2.Remote,
    branch: str,
) -> pygit2.Commit:
    ref_name = f"refs/remotes/{remote.name}/{branch}"
    try:
        remote_ref = repository.lookup_reference(ref_name)
    except KeyError as error:
        remote_name = remote.name or remote.url or "origin"
        detail = ERROR_MISSING_BRANCH.format(branch=branch, remote=remote_name)
        raise EstateCacheError(detail) from error

    commit = repository.get(remote_ref.target)
    if isinstance(commit, pygit2.Commit):
        return commit

    resolved = repository[commit]  # type: ignore[index]
    return typ.cast("pygit2.Commit", resolved)


def _sync_local_branch(
    repository: pygit2.Repository,
    branch: str,
    commit: pygit2.Commit,
) -> None:
    local_branch = repository.lookup_branch(branch)
    if local_branch is None:
        repository.create_branch(branch, commit)
        return
    repository.lookup_reference(local_branch.name).set_target(commit.id)


def _reset_to_commit(repository: pygit2.Repository, commit: pygit2.Commit) -> None:
    reset_mode = typ.cast("_Pygit2ResetMode", pygit2.GIT_RESET_HARD)
    repository.reset(commit.id, reset_mode)
    repository.checkout_head(strategy=pygit2.GIT_CHECKOUT_FORCE)
