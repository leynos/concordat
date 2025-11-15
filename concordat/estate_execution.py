"""Helpers for executing OpenTofu commands against an estate cache."""

from __future__ import annotations

import contextlib
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

    root = cache_directory or cache_root()
    destination = root / record.alias
    callbacks = build_remote_callbacks(record.repo_url)

    try:
        if destination.exists():
            repository = pygit2.Repository(str(destination))
            _refresh_cache(repository, record.branch, callbacks)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            repository = pygit2.clone_repository(
                record.repo_url,
                str(destination),
                checkout_branch=record.branch,
                callbacks=callbacks,
            )
    except pygit2.GitError as error:  # pragma: no cover - pygit2 raises opaque errors
        raise _sync_failed_error(record.alias, str(error)) from error

    workdir = repository.workdir
    if not workdir:
        raise _bare_cache_error(record.alias, destination)
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
    *,
    github_owner: str,
    github_token: str,
    extra_args: cabc.Sequence[str] | cabc.Iterable[str] = (),
    keep_workdir: bool = False,
    cache_directory: Path | None = None,
    stdout: typ.IO[str],
    stderr: typ.IO[str],
) -> tuple[int, Path]:
    """Execute `tofu plan` within a prepared estate workspace."""
    additional = tuple(extra_args)
    command = ("plan", *additional)
    return _run_operation(
        record,
        github_owner=github_owner,
        github_token=github_token,
        command=command,
        keep_workdir=keep_workdir,
        prefix="plan",
        cache_directory=cache_directory,
        stdout=stdout,
        stderr=stderr,
    )


def run_apply(
    record: EstateRecord,
    *,
    github_owner: str,
    github_token: str,
    extra_args: cabc.Sequence[str] | cabc.Iterable[str] = (),
    keep_workdir: bool = False,
    cache_directory: Path | None = None,
    stdout: typ.IO[str],
    stderr: typ.IO[str],
) -> tuple[int, Path]:
    """Execute `tofu apply` within a prepared estate workspace."""
    additional = tuple(extra_args)
    command = ("apply", *additional)
    return _run_operation(
        record,
        github_owner=github_owner,
        github_token=github_token,
        command=command,
        keep_workdir=keep_workdir,
        prefix="apply",
        cache_directory=cache_directory,
        stdout=stdout,
        stderr=stderr,
    )


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
    try:
        remote = repository.remotes["origin"]
    except KeyError as error:  # pragma: no cover - defensive guard
        raise _missing_origin_error() from error
    remote.fetch(callbacks=callbacks)
    ref_name = f"refs/remotes/{remote.name}/{branch}"
    try:
        remote_ref = repository.lookup_reference(ref_name)
    except KeyError as error:
        remote_name = remote.name or remote.url or "origin"
        raise _missing_branch_error(branch, remote_name) from error

    commit = repository.get(remote_ref.target)
    if not isinstance(commit, pygit2.Commit):
        commit = repository[commit]  # type: ignore[index]
    local_branch = repository.lookup_branch(branch)
    if local_branch is None:
        local_branch = repository.create_branch(branch, commit)
    else:
        repository.lookup_reference(local_branch.name).set_target(commit.id)

    reset_mode = typ.cast("typ.Any", pygit2.GIT_RESET_HARD)
    repository.reset(commit.id, reset_mode)
    repository.checkout_head(strategy=pygit2.GIT_CHECKOUT_FORCE)


def _run_operation(
    record: EstateRecord,
    *,
    github_owner: str,
    github_token: str,
    command: cabc.Sequence[str],
    keep_workdir: bool,
    prefix: str,
    cache_directory: Path | None,
    stdout: typ.IO[str],
    stderr: typ.IO[str],
) -> tuple[int, Path]:
    with estate_workspace(
        record,
        cache_directory=cache_directory,
        keep_workdir=keep_workdir,
        prefix=prefix,
    ) as workdir:
        stderr.write(f"execution workspace: {workdir}\n")
        stderr.flush()
        write_tfvars(workdir, github_owner=github_owner)
        tofu = _initialise_tofu(workdir, github_token)
        init_code = _run_tofo(tofu, ["init", "-input=false"], stdout, stderr)
        if init_code != 0:
            return init_code, workdir
        exit_code = _run_tofo(tofu, command, stdout, stderr)
        return exit_code, workdir


def _initialise_tofu(workdir: Path, github_token: str) -> Tofu:
    try:
        return Tofu(cwd=str(workdir), env={"GITHUB_TOKEN": github_token})
    except FileNotFoundError as error:  # pragma: no cover - depends on system state
        raise _missing_tofu_error() from error
    except RuntimeError as error:  # pragma: no cover - version mismatch etc
        raise EstateExecutionError(str(error)) from error


def _run_tofo(
    tofu: Tofu,
    args: cabc.Sequence[str],
    stdout: typ.IO[str],
    stderr: typ.IO[str],
) -> int:
    results = tofu._run(list(args), raise_on_error=False)
    if results.stdout:
        stdout.write(results.stdout)
        if not results.stdout.endswith("\n"):
            stdout.write("\n")
    if results.stderr:
        stderr.write(results.stderr)
        if not results.stderr.endswith("\n"):
            stderr.write("\n")
    return results.returncode
