"""Helpers for executing OpenTofu commands against an estate cache."""

from __future__ import annotations

import contextlib
import dataclasses
import logging
import os
import shutil
import typing as typ
from pathlib import Path
from tempfile import mkdtemp
from types import SimpleNamespace

import pygit2
from tofupy import Tofu

from concordat.persistence import models as persistence_models

from .errors import ConcordatError
from .gitutils import build_remote_callbacks

_logger = logging.getLogger(__name__)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from pygit2.enums import ResetMode as _Pygit2ResetMode

    from concordat.persistence.models import PersistenceDescriptor

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
ERROR_BACKEND_CONFIG_MISSING = (
    "Remote backend config {path!r} was not found in the estate workspace."
)
ERROR_BACKEND_ENV_MISSING = (
    "Remote state backend requires credentials in the environment: either "
    "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY, SCW_ACCESS_KEY and "
    "SCW_SECRET_KEY, or SPACES_ACCESS_KEY_ID and SPACES_SECRET_ACCESS_KEY."
)
ERROR_BACKEND_PATH_OUTSIDE = (
    "Remote backend config must live inside the estate workspace (got {path})."
)

AWS_BACKEND_ENV = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")
SCW_BACKEND_ENV = ("SCW_ACCESS_KEY", "SCW_SECRET_KEY")
SPACES_BACKEND_ENV = (
    "SPACES_ACCESS_KEY_ID",
    "SPACES_SECRET_ACCESS_KEY",
)
AWS_SESSION_TOKEN_VAR = "AWS_SESSION_TOKEN"  # noqa: S105
ALL_BACKEND_ENV_VARS = (
    AWS_BACKEND_ENV + SCW_BACKEND_ENV + SPACES_BACKEND_ENV + (AWS_SESSION_TOKEN_VAR,)
)


def _session_token_overrides(env: typ.Mapping[str, str]) -> dict[str, str]:
    """Return a mapping containing AWS session token when present and non-empty."""
    token = env.get(AWS_SESSION_TOKEN_VAR, "").strip()
    return {AWS_SESSION_TOKEN_VAR: token} if token else {}


def _remove_blank_session_token(env: dict[str, str]) -> None:
    """Drop AWS session token when present but blank to avoid leaking empties."""
    token = env.get(AWS_SESSION_TOKEN_VAR)
    if token is not None and not token.strip():
        env.pop(AWS_SESSION_TOKEN_VAR, None)


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
    environment: cabc.Mapping[str, str] | None = None


@dataclasses.dataclass(frozen=True)
class ExecutionIO:
    """Output streams used by the tofu runner."""

    stdout: typ.IO[str]
    stderr: typ.IO[str]


@dataclasses.dataclass(frozen=True)
class PersistenceRuntime:
    """Runtime data for invoking tofu with a remote backend."""

    descriptor: PersistenceDescriptor
    backend_config: str
    object_key: str
    env_overrides: dict[str, str]


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


def _resolve_backend_environment(env: typ.Mapping[str, str]) -> dict[str, str]:
    """Return env overrides for tofu, erroring when credentials are missing."""

    def present(*names: str) -> bool:
        return all(env.get(name, "").strip() for name in names)

    if present(*AWS_BACKEND_ENV):
        return {
            "AWS_ACCESS_KEY_ID": env["AWS_ACCESS_KEY_ID"].strip(),
            "AWS_SECRET_ACCESS_KEY": env["AWS_SECRET_ACCESS_KEY"].strip(),
            **_session_token_overrides(env),
        }

    if present(*SCW_BACKEND_ENV):
        return {
            "AWS_ACCESS_KEY_ID": env["SCW_ACCESS_KEY"].strip(),
            "AWS_SECRET_ACCESS_KEY": env["SCW_SECRET_KEY"].strip(),
            **_session_token_overrides(env),
        }

    if present(*SPACES_BACKEND_ENV):
        return {
            "AWS_ACCESS_KEY_ID": env["SPACES_ACCESS_KEY_ID"].strip(),
            "AWS_SECRET_ACCESS_KEY": env["SPACES_SECRET_ACCESS_KEY"].strip(),
            **_session_token_overrides(env),
        }

    raise EstateExecutionError(ERROR_BACKEND_ENV_MISSING)


def _validate_backend_path(workdir: Path, backend_config_path: str) -> Path:
    """Validate backend config path is inside workspace and exists.

    Returns the relative path to the backend config file. Raises
    EstateExecutionError when the path escapes the workspace or is missing.
    """
    backend_path = (workdir / backend_config_path).resolve()
    workdir_resolved = workdir.resolve()

    try:
        relative_backend = backend_path.relative_to(workdir_resolved)
    except ValueError as error:
        message = ERROR_BACKEND_PATH_OUTSIDE.format(path=backend_config_path)
        raise EstateExecutionError(message) from error

    if not backend_path.is_file():
        message = ERROR_BACKEND_CONFIG_MISSING.format(path=backend_config_path)
        raise EstateExecutionError(message)

    return relative_backend


def _build_object_key(descriptor: PersistenceDescriptor) -> str:
    """Construct the full S3 object key from prefix and suffix."""
    prefix = descriptor.key_prefix.rstrip("/")
    suffix = descriptor.key_suffix.lstrip("/")
    return f"{prefix}/{suffix}" if prefix else suffix


def _get_persistence_runtime(
    workdir: Path, env: typ.Mapping[str, str]
) -> PersistenceRuntime | None:
    """Load the persistence manifest and derive backend runtime details."""
    manifest_path = workdir / persistence_models.MANIFEST_FILENAME
    try:
        descriptor = persistence_models.PersistenceDescriptor.from_yaml(manifest_path)
    except persistence_models.PersistenceError as error:
        raise EstateExecutionError(str(error)) from error

    if descriptor is None or not descriptor.enabled:
        return None

    relative_backend = _validate_backend_path(workdir, descriptor.backend_config_path)
    env_overrides = _resolve_backend_environment(env)
    object_key = _build_object_key(descriptor)

    return PersistenceRuntime(
        descriptor=descriptor,
        backend_config=str(relative_backend),
        object_key=object_key,
        env_overrides=env_overrides,
    )


def _initialize_tofu(workdir: Path, env: typ.Mapping[str, str]) -> Tofu:
    """Create a Tofu wrapper with mapped environment, surfacing friendly errors."""
    try:
        return Tofu(cwd=str(workdir), env=dict(env))
    except FileNotFoundError as error:  # pragma: no cover - depends on PATH
        raise EstateExecutionError(ERROR_MISSING_TOFU) from error
    except RuntimeError as error:  # pragma: no cover - tofu misconfiguration
        raise EstateExecutionError(str(error)) from error


def _normalize_init_result(result: object) -> SimpleNamespace:
    """Normalize tofupy.init boolean result."""
    return SimpleNamespace(stdout="", stderr="", returncode=0 if result else 1)


def _normalize_plan_result(result: object) -> SimpleNamespace:
    """Normalize tofupy.plan (PlanLog, Plan|None) tuple result."""
    if not isinstance(result, tuple) or len(result) != 2:
        return SimpleNamespace(stdout="", stderr="", returncode=1)

    plan_log, plan = result
    stdout = getattr(plan_log, "stdout", "") if plan_log else ""
    stderr = getattr(plan_log, "stderr", "") if plan_log else ""
    errored = getattr(plan_log, "errored", False) or getattr(plan, "errored", False)
    return SimpleNamespace(
        stdout=stdout or "", stderr=stderr or "", returncode=1 if errored else 0
    )


def _normalize_apply_result(result: object) -> SimpleNamespace:
    """Normalize tofupy.apply ApplyLog result."""
    apply_log = result
    stdout = getattr(apply_log, "stdout", "") if apply_log else ""
    stderr = getattr(apply_log, "stderr", "") if apply_log else ""
    errored = getattr(apply_log, "errored", False)
    return SimpleNamespace(
        stdout=stdout or "", stderr=stderr or "", returncode=1 if errored else 0
    )


def _normalize_tofu_result(verb: str, result: object) -> SimpleNamespace:
    """Coerce tofupy results into a consistent stdout/stderr/returncode shape."""
    # Direct tofupy _run result already matches the expected shape.
    if hasattr(result, "returncode"):
        return SimpleNamespace(
            stdout=getattr(result, "stdout", "") or "",
            stderr=getattr(result, "stderr", "") or "",
            returncode=getattr(result, "returncode", 0) or 0,
        )

    if verb == "init":
        # Dispatch to verb-specific normalizers.
        return _normalize_init_result(result)

    if verb == "plan":
        return _normalize_plan_result(result)

    if verb == "apply":
        return _normalize_apply_result(result)

    _logger.debug("Unhandled tofu verb %r, assuming success", verb)
    return SimpleNamespace(stdout="", stderr="", returncode=0)


def _stream_tofu_output(io: ExecutionIO, normalized: SimpleNamespace) -> int:
    """Write normalized tofu output to the provided IO streams and return exit code."""
    if normalized.stdout:
        _write_stream_output(io.stdout, normalized.stdout)
    if normalized.stderr:
        _write_stream_output(io.stderr, normalized.stderr)
    return normalized.returncode


def _invoke_tofu_command(tofu: Tofu, args: list[str], io: ExecutionIO) -> int:
    """Run a tofu command, streaming stdout/stderr to the provided IO."""
    verb, *extra_args = args
    # Tests provide a fake tofu binary that only writes plain text; skip
    # tofupy's streaming JSON interface in that mode to avoid parse errors.
    if os.environ.get("FAKE_TOFU_LOG"):
        results = tofu._run(args, raise_on_error=False)
        normalized = _normalize_tofu_result(verb, results)
        return _stream_tofu_output(io, normalized)

    method = getattr(tofu, verb, None)

    if callable(method):
        try:
            results = method(extra_args=extra_args)
        except TypeError:
            # Fallback for methods that do not accept extra_args.
            results = method()
    else:
        # Last-resort fallback when public APIs are unavailable.
        results = tofu._run(args, raise_on_error=False)

    normalized = _normalize_tofu_result(verb, results)
    return _stream_tofu_output(io, normalized)


def _prepare_execution_environment(options: ExecutionOptions) -> dict[str, str]:
    """Compose the base environment for tofu invocation."""
    if (
        AWS_SESSION_TOKEN_VAR in os.environ
        and not os.environ[AWS_SESSION_TOKEN_VAR].strip()
    ):
        os.environ.pop(AWS_SESSION_TOKEN_VAR, None)
    env_source = dict(os.environ)
    if options.environment is not None:
        env_source.update(options.environment)
    _remove_blank_session_token(env_source)
    return env_source


def _prepare_backend_configuration(
    workdir: Path, env_source: dict[str, str], io: ExecutionIO
) -> tuple[list[str], dict[str, str]]:
    """Derive backend args and environment overrides for tofu."""
    persistence_runtime = _get_persistence_runtime(workdir, env_source)
    backend_args: list[str] = []
    if persistence_runtime is not None:
        backend_args.append(f"-backend-config={persistence_runtime.backend_config}")
        descriptor = persistence_runtime.descriptor
        _write_stream_output(
            io.stderr,
            (
                "remote backend: "
                f"bucket={descriptor.bucket} "
                f"key={persistence_runtime.object_key} "
                f"region={descriptor.region} "
                f"config={persistence_runtime.backend_config}"
            ),
        )

    env: dict[str, str] = dict(env_source)
    if persistence_runtime is not None:
        env |= persistence_runtime.env_overrides
    token_value = env.get(AWS_SESSION_TOKEN_VAR, "")
    if token_value and token_value.strip():
        env[AWS_SESSION_TOKEN_VAR] = token_value.strip()
    else:
        env.pop(AWS_SESSION_TOKEN_VAR, None)
    return backend_args, env


def _run_estate_command(
    record: EstateRecord,
    verb: str,
    options: ExecutionOptions,
    io: ExecutionIO,
) -> tuple[int, Path]:
    command = [verb, *options.extra_args]
    env_source = _prepare_execution_environment(options)
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

        backend_args, env = _prepare_backend_configuration(workdir, env_source, io)
        env["GITHUB_TOKEN"] = options.github_token
        tofu = _initialize_tofu(workdir, env)

        init_args = ["init", "-input=false", *backend_args]

        for args in [init_args, command]:
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
