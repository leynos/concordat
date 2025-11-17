"""Helpers for executing OpenTofu commands against an estate cache."""

from __future__ import annotations

import contextlib
import dataclasses
import os
import shutil
import typing as typ
from pathlib import Path
from tempfile import mkdtemp

import pygit2
from tofupy import Tofu

from .errors import ConcordatError
from .gitutils import build_remote_callbacks

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from pygit2.enums import ResetMode as _Pygit2ResetMode

    from .estate import EstateRecord

XDG_CACHE_HOME = "XDG_CACHE_HOME"
CACHE_SEGMENT = ("concordat", "estates")
TFVARS_FILENAME = "terraform.tfvars"
ERROR_ALIAS_REQUIRED = "Estate alias is required to cache the repository."
ERROR_BARE_CACHE = "Cached estate {alias!r} is bare; remove {destination} and retry."
ERROR_MISSING_ORIGIN = (
    "Cached estate is missing the 'origin' remote; remove it and retry."
)
ERROR_MISSING_BRANCH = "Branch {branch!r} is missing from remote {remote!r}."
ERROR_MISSING_TOFU = "OpenTofu binary 'tofu' was not found in PATH."


class EstateExecutionError(ConcordatError):
    """Raised when preparing an estate workspace fails."""


@dataclasses.dataclass(frozen=True)
class ExecutionOptions:
    """User-configurable knobs for running tofu against an estate."""

    github_owner: str
    github_token: str
    extra_args: cabc.Sequence[str] = dataclasses.field(default_factory=tuple)
    keep_workdir: bool = False
    cache_directory: Path | None = None


@dataclasses.dataclass(frozen=True)
class ExecutionIO:
    """Output streams used by the tofu runner."""

    stdout: typ.IO[str]
    stderr: typ.IO[str]


def cache_root(env: dict[str, str] | None = None) -> Path:
    """Return the directory used for caching estate repositories."""
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
        raise EstateExecutionError(ERROR_ALIAS_REQUIRED)

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
                raise EstateExecutionError(detail)
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
        raise EstateExecutionError(detail) from error


def _workdir_from_repository(
    alias: str,
    destination: Path,
    repository: pygit2.Repository,
) -> Path:
    if workdir := repository.workdir:
        return Path(workdir)
    detail = ERROR_BARE_CACHE.format(alias=alias, destination=destination)
    raise EstateExecutionError(detail)


@contextlib.contextmanager
def estate_workspace(
    record: EstateRecord,
    *,
    cache_directory: Path | None = None,
    keep_workdir: bool = False,
    prefix: str = "plan",
) -> cabc.Iterator[Path]:
    """Yield a throwaway working tree populated from the estate cache."""
    cache_path = ensure_estate_cache(record, cache_directory=cache_directory)
    workdir = _clone_into_temp(cache_path, prefix)
    try:
        yield workdir
    finally:
        if not keep_workdir and workdir.exists():
            shutil.rmtree(workdir)


def run_plan(
    record: EstateRecord,
    options: ExecutionOptions,
    io: ExecutionIO,
) -> tuple[int, Path]:
    """Execute `tofu plan` within a prepared estate workspace."""
    return _run_estate_command(record, "plan", options, io)


def run_apply(
    record: EstateRecord,
    options: ExecutionOptions,
    io: ExecutionIO,
) -> tuple[int, Path]:
    """Execute `tofu apply` within a prepared estate workspace."""
    return _run_estate_command(record, "apply", options, io)


def _write_stream_output(stream: typ.IO[str], content: str) -> None:
    stream.write(content)
    if not content.endswith("\n"):
        stream.write("\n")
    stream.flush()


def _initialize_tofu(workdir: Path, github_token: str) -> Tofu:
    env = os.environ.copy()
    env["GITHUB_TOKEN"] = github_token
    try:
        return Tofu(cwd=str(workdir), env=env)
    except FileNotFoundError as error:  # pragma: no cover - depends on PATH
        raise EstateExecutionError(ERROR_MISSING_TOFU) from error
    except RuntimeError as error:  # pragma: no cover - tofu misconfiguration
        raise EstateExecutionError(str(error)) from error


def _invoke_tofu_command(tofu: Tofu, args: list[str], io: ExecutionIO) -> int:
    results = tofu._run(args, raise_on_error=False)
    if results.stdout:
        _write_stream_output(io.stdout, results.stdout)
    if results.stderr:
        _write_stream_output(io.stderr, results.stderr)
    return results.returncode


def _run_estate_command(
    record: EstateRecord,
    verb: str,
    options: ExecutionOptions,
    io: ExecutionIO,
) -> tuple[int, Path]:
    command = [verb, *options.extra_args]
    with estate_workspace(
        record,
        cache_directory=options.cache_directory,
        keep_workdir=options.keep_workdir,
        prefix=verb,
    ) as workdir:
        io.stderr.write(f"execution workspace: {workdir}\n")
        io.stderr.flush()
        tfvars = workdir / TFVARS_FILENAME
        tfvars.write_text(
            f'github_owner = "{options.github_owner}"\n',
            encoding="utf-8",
        )
        tofu = _initialize_tofu(workdir, options.github_token)

        for args in [["init", "-input=false"], command]:
            exit_code = _invoke_tofu_command(tofu, list(args), io)
            if exit_code != 0:
                break
        return exit_code, workdir


def _clone_into_temp(cache_path: Path, prefix: str) -> Path:
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
        raise EstateExecutionError(ERROR_MISSING_ORIGIN) from error
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
        raise EstateExecutionError(detail) from error

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
