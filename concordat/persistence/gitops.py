"""Git operations for persistence workflow."""
# ruff: noqa: TRY003

from __future__ import annotations

import datetime as dt
import os
import typing as typ
from pathlib import Path

import pygit2

from concordat.gitutils import build_remote_callbacks

from .models import PersistenceError


def _get_current_head(repository: pygit2.Repository) -> str | None:
    """Return the current HEAD branch name, or None if unable to determine."""
    try:
        return repository.head.shorthand
    except (KeyError, ValueError, pygit2.GitError):
        return None


def _verify_checkout_succeeded(repository: pygit2.Repository, branch_name: str) -> None:
    """Verify repository is no longer on branch_name after checkout."""
    try:
        current = repository.head.shorthand
    except (KeyError, ValueError, pygit2.GitError) as exc:
        raise PersistenceError(
            f"Failed to confirm checkout away from {branch_name!r}."
        ) from exc

    if current == branch_name:
        raise PersistenceError(
            f"Failed to leave branch {branch_name!r} before recreation."
        )


def _ensure_not_on_branch(
    repository: pygit2.Repository, branch_name: str, base_branch: str
) -> None:
    """Leave branch_name if currently checked out, raising on failure."""
    head = _get_current_head(repository)

    if head != branch_name:
        return

    try:
        repository.checkout(f"refs/heads/{base_branch}")
    except pygit2.GitError as exc:
        raise PersistenceError(
            f"Unable to checkout base branch {base_branch!r} before "
            f"recreating {branch_name!r}."
        ) from exc

    _verify_checkout_succeeded(repository, branch_name)


def _recreate_branch_if_exists(
    repository: pygit2.Repository, branch_name: str, base_branch: str
) -> None:
    """Delete branch_name if present, ensuring we are not currently on it."""
    if branch_name not in repository.branches.local:
        return

    _ensure_not_on_branch(repository, branch_name, base_branch)

    try:
        repository.branches.delete(branch_name)
    except pygit2.GitError as exc:
        raise PersistenceError(
            f"Unable to delete existing branch {branch_name!r}."
        ) from exc


def _stage_paths(repository: pygit2.Repository, paths: list[Path]) -> pygit2.Oid:
    """Stage provided paths and return the resulting tree OID."""
    for path in paths:
        rel = os.path.relpath(path, repository.workdir or ".")
        repository.index.add(rel)
    repository.index.write()
    return repository.index.write_tree()


def _get_signature_or_default(repository: pygit2.Repository) -> pygit2.Signature:
    """Return repository signature or a safe default."""
    try:
        return repository.default_signature
    except KeyError:
        return pygit2.Signature("concordat", "concordat@local")


def _commit_changes(
    repository: pygit2.Repository,
    base_branch: str,
    paths: list[Path],
    *,
    timestamp_factory: typ.Callable[[], dt.datetime] | None = None,
) -> str:
    target = repository.revparse_single(f"refs/heads/{base_branch}")
    commit = target.peel(pygit2.Commit)
    branch_name = _branch_name(timestamp_factory)
    _recreate_branch_if_exists(repository, branch_name, base_branch)
    new_branch = repository.create_branch(branch_name, commit)
    repository.checkout(new_branch)
    tree_oid = _stage_paths(repository, paths)
    signature = _get_signature_or_default(repository)
    commit_message = "chore: configure remote state persistence"
    repository.create_commit(
        "HEAD",
        signature,
        signature,
        commit_message,
        tree_oid,
        [commit.id],
    )
    return branch_name


def _branch_name(timestamp_factory: typ.Callable[[], dt.datetime] | None = None) -> str:
    now = timestamp_factory() if timestamp_factory else dt.datetime.now(dt.UTC)
    return f"estate/persist-{now.strftime('%Y%m%d%H%M%S')}"


def _push_branch(repository: pygit2.Repository, branch: str, repo_url: str) -> None:
    remote = _resolve_remote(repository, repo_url)
    callbacks = build_remote_callbacks(remote.url or repo_url)
    refspec = f"+refs/heads/{branch}:refs/heads/{branch}"
    remote.push([refspec], callbacks=callbacks)


def _resolve_remote(repository: pygit2.Repository, repo_url: str) -> pygit2.Remote:
    """Select a remote matching repo_url, falling back to origin or the first remote."""
    remotes = list(repository.remotes)
    if not remotes:
        raise PersistenceError("Repository has no remotes configured for persistence.")

    for remote in remotes:
        if _urls_match(remote.url, repo_url):
            return remote

    try:
        return repository.remotes["origin"]
    except KeyError:
        pass

    return remotes[0]


def _urls_match(remote_url: str | None, requested_url: str) -> bool:
    """Best-effort comparison of remote URLs including local path schemes."""
    if remote_url is None:
        return False
    if remote_url == requested_url:
        return True
    try:
        return Path(remote_url).resolve() == Path(requested_url).resolve()
    except OSError:
        return False
