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

    from .estate import EstateRecord

XDG_CACHE_HOME = "XDG_CACHE_HOME"
CACHE_SEGMENT = ("concordat", "estates")
TFVARS_FILENAME = "terraform.tfvars"


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


def _alias_required_error() -> EstateExecutionError:
    return EstateExecutionError("Estate alias is required to cache the repository.")


def _sync_failed_error(alias: str, detail: str) -> EstateExecutionError:
    return EstateExecutionError(f"Failed to sync estate {alias!r}: {detail}")


def _bare_cache_error(alias: str, destination: Path) -> EstateExecutionError:
    return EstateExecutionError(
        f"Cached estate {alias!r} is bare; remove {destination} and retry."
    )


def _owner_missing_error() -> EstateExecutionError:
    return EstateExecutionError("github_owner must be recorded for the estate.")


def _missing_origin_error() -> EstateExecutionError:
    return EstateExecutionError(
        "Cached estate is missing the 'origin' remote; remove it and retry."
    )


def _missing_branch_error(branch: str, remote_name: str) -> EstateExecutionError:
    return EstateExecutionError(
        f"Branch {branch!r} is missing from remote {remote_name!r}."
    )


def _missing_tofu_error() -> EstateExecutionError:
    return EstateExecutionError("OpenTofu binary 'tofu' was not found in PATH.")


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
        raise _alias_required_error()

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
        raise _sync_failed_error(record.alias, str(error)) from error


def _workdir_from_repository(
    alias: str,
    destination: Path,
    repository: pygit2.Repository,
) -> Path:
    if not (workdir := repository.workdir):
        raise _bare_cache_error(alias, destination)
    return Path(workdir)


def write_tfvars(
    workdir: Path,
    *,
    github_owner: str,
) -> Path:
    """Write terraform.tfvars values required by concordat."""
    if not github_owner:
        raise _owner_missing_error()
    path = workdir / TFVARS_FILENAME
    path.write_text(f'github_owner = "{github_owner}"\n', encoding="utf-8")
    return path


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


def _run_estate_command(
    record: EstateRecord,
    verb: str,
    options: ExecutionOptions,
    io: ExecutionIO,
) -> tuple[int, Path]:
    command = (verb, *tuple(options.extra_args))
    with estate_workspace(
        record,
        cache_directory=options.cache_directory,
        keep_workdir=options.keep_workdir,
        prefix=verb,
    ) as workdir:
        io.stderr.write(f"execution workspace: {workdir}\n")
        io.stderr.flush()
        write_tfvars(workdir, github_owner=options.github_owner)
        tofu = _initialise_tofu(workdir, options.github_token)
        init_code = _run_tofu(tofu, ["init", "-input=false"], io.stdout, io.stderr)
        if init_code != 0:
            return init_code, workdir
        exit_code = _run_tofu(tofu, command, io.stdout, io.stderr)
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
        raise _missing_origin_error() from error
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
        raise _missing_branch_error(branch, remote_name) from error

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
    reset_mode = typ.cast("typ.Any", pygit2.GIT_RESET_HARD)
    repository.reset(commit.id, reset_mode)
    repository.checkout_head(strategy=pygit2.GIT_CHECKOUT_FORCE)


def _initialise_tofu(workdir: Path, github_token: str) -> Tofu:
    try:
        return Tofu(cwd=str(workdir), env={"GITHUB_TOKEN": github_token})
    except FileNotFoundError as error:  # pragma: no cover - depends on system state
        raise _missing_tofu_error() from error
    except RuntimeError as error:  # pragma: no cover - version mismatch etc
        raise EstateExecutionError(str(error)) from error


def _run_tofu(
    tofu: Tofu,
    args: cabc.Sequence[str],
    stdout: typ.IO[str],
    stderr: typ.IO[str],
) -> int:
    results = tofu._run(list(args), raise_on_error=False)
    _emit_stream(results.stdout, stdout)
    _emit_stream(results.stderr, stderr)
    return results.returncode


def _emit_stream(text: str | None, handle: typ.IO[str]) -> None:
    """Write command output, ensuring newline termination when needed."""
    if not text:
        return
    handle.write(text)
    if not text.endswith("\n"):
        handle.write("\n")
