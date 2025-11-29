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
    if branch_name in repository.branches.local:
        try:
            head = repository.head.shorthand
        except (KeyError, ValueError, pygit2.GitError):
            head = None
        if head == branch_name:
            try:
                repository.checkout(f"refs/heads/{base_branch}")
            except pygit2.GitError as exc:
                raise PersistenceError(
                    f"Unable to checkout base branch {base_branch!r} before "
                    f"recreating {branch_name!r}."
                ) from exc
            if repository.head.shorthand == branch_name:
                raise PersistenceError(
                    f"Failed to leave branch {branch_name!r} before recreation."
                )
        try:
            repository.branches.delete(branch_name)
        except pygit2.GitError as exc:
            raise PersistenceError(
                f"Unable to delete existing branch {branch_name!r}."
            ) from exc
    new_branch = repository.create_branch(branch_name, commit)
    repository.checkout(new_branch)
    for path in paths:
        rel = os.path.relpath(path, repository.workdir or ".")
        repository.index.add(rel)
    repository.index.write()
    tree_oid = repository.index.write_tree()
    try:
        signature = repository.default_signature
    except KeyError:
        signature = pygit2.Signature("concordat", "concordat@local")
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
    now = timestamp_factory() if timestamp_factory else dt.datetime.now(dt.timezone.utc)
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
