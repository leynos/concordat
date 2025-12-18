"""Helpers for executing OpenTofu commands against an estate cache."""

from __future__ import annotations

import contextlib
import dataclasses
import os
import shutil
import sys  # noqa: F401 - Required for test patching of sys.stdin
import typing as typ

from tofupy import Tofu  # noqa: TC002 - Used at runtime

from concordat.persistence import backend as persistence_backend
from concordat.persistence import models as persistence_models

from . import apply_recovery
from .apply_recovery import RecoveryCallbacks, RecoveryContext
from .errors import ConcordatError
from .estate_cache import (
    EstateCacheError,
    cache_root,
    clone_into_temp,
    ensure_estate_cache,
)
from .tofu_github_errors import (
    detect_missing_repo_imports as _detect_missing_repo_imports,
)
from .tofu_github_errors import (
    detect_state_forgets_for_prevent_destroy as _detect_prevent_destroy_forgets,
)
from .tofu_runner import (
    initialize_tofu,
    invoke_tofu_command,
    invoke_tofu_command_with_result,
    resolve_tofu_workdir,
    write_stream_output,
)
from .tofu_yaml import sanitize_inventory_for_tofu as _sanitize_inventory_for_tofu
from .user_interaction import can_prompt as _can_prompt
from .user_interaction import prompt_yes_no as _prompt_yes_no

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

    from .estate import EstateRecord

# Re-export constants for backward compatibility.
AWS_BACKEND_ENV = persistence_backend.AWS_BACKEND_ENV
SCW_BACKEND_ENV = persistence_backend.SCW_BACKEND_ENV
SPACES_BACKEND_ENV = persistence_backend.SPACES_BACKEND_ENV
AWS_SESSION_TOKEN_VAR = persistence_backend.AWS_SESSION_TOKEN_VAR
ALL_BACKEND_ENV_VARS = persistence_backend.ALL_BACKEND_ENV_VARS

# Re-export functions for backward compatibility with tests.
_build_object_key = persistence_backend.build_object_key

# Re-export cache functions for backward compatibility.
cache_root = cache_root
ensure_estate_cache = ensure_estate_cache

TFVARS_FILENAME = "terraform.tfvars"


class EstateExecutionError(ConcordatError):
    """Raised when preparing an estate workspace fails."""


# Make EstateCacheError raise as EstateExecutionError for backward compatibility.
_T = typ.TypeVar("_T")
_P = typ.ParamSpec("_P")


def _wrap_cache_error(func: typ.Callable[_P, _T]) -> typ.Callable[_P, _T]:
    """Wrap a function to convert EstateCacheError to EstateExecutionError."""

    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _T:
        try:
            return func(*args, **kwargs)
        except EstateCacheError as error:
            raise EstateExecutionError(str(error)) from error

    return wrapper


ensure_estate_cache = _wrap_cache_error(ensure_estate_cache)  # type: ignore[assignment]


def _resolve_backend_environment(env: typ.Mapping[str, str]) -> dict[str, str]:
    """Resolve backend environment with EstateExecutionError on failure."""
    try:
        return persistence_backend.resolve_backend_environment(env)
    except persistence_backend.BackendConfigurationError as error:
        raise EstateExecutionError(str(error)) from error


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
class WorkspaceContext:
    """Workspace directory paths for tofu operations."""

    root: Path
    tofu_dir: Path


@dataclasses.dataclass(frozen=True)
class ExecutionContext:
    """Execution environment for tofu commands."""

    options: ExecutionOptions
    io: ExecutionIO
    env: dict[str, str]


@dataclasses.dataclass(frozen=True)
class PersistenceRuntime:
    """Runtime data for invoking tofu with a remote backend."""

    descriptor: persistence_models.PersistenceDescriptor
    backend_config: str
    object_key: str
    env_overrides: dict[str, str]


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
    workdir = clone_into_temp(cache_path, prefix)
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


def _get_persistence_runtime(
    workspace_root: Path,
    tofu_workdir: Path,
    env: typ.Mapping[str, str],
) -> PersistenceRuntime | None:
    """Load the persistence manifest and derive backend runtime details."""
    try:
        descriptor, backend_config, object_key, env_overrides = (
            persistence_backend.get_persistence_runtime(
                workspace_root, tofu_workdir, env
            )
        )
    except persistence_backend.BackendConfigurationError as error:
        raise EstateExecutionError(str(error)) from error

    if descriptor is None:
        return None

    assert backend_config is not None  # noqa: S101
    assert object_key is not None  # noqa: S101
    assert env_overrides is not None  # noqa: S101
    return PersistenceRuntime(
        descriptor=descriptor,
        backend_config=backend_config,
        object_key=object_key,
        env_overrides=env_overrides,
    )


def _initialize_tofu(workdir: Path, env: typ.Mapping[str, str]) -> Tofu:
    """Create a Tofu wrapper, converting errors to EstateExecutionError."""
    try:
        return initialize_tofu(workdir, env)
    except ConcordatError as error:
        raise EstateExecutionError(str(error)) from error


def _prepare_execution_environment(options: ExecutionOptions) -> dict[str, str]:
    """Compose the base environment for tofu invocation."""
    env_source = dict(os.environ)
    if options.environment is not None:
        env_source.update(options.environment)
    persistence_backend.remove_blank_session_token(env_source)
    return env_source


def _setup_tofu_workspace(
    workspace: WorkspaceContext,
    record: EstateRecord,
    execution: ExecutionContext,
) -> tuple[Path, list[str], dict[str, str], Tofu]:
    """Prepare the workspace for tofu execution.

    Sanitizes inventory, writes tfvars, configures backend, and initialises the
    tofu wrapper. Returns the tfvars path, backend arguments, environment, and
    tofu instance.
    """
    sanitized_inventory = _sanitize_inventory_for_tofu(
        workspace.root,
        workspace.tofu_dir,
        record.inventory_path,
    )
    if sanitized_inventory:
        write_stream_output(
            execution.io.stderr,
            "inventory sanitized for tofu (removed YAML directives)",
        )
    tfvars = workspace.tofu_dir / TFVARS_FILENAME
    tfvars.write_text(
        f'github_owner = "{execution.options.github_owner}"\n',
        encoding="utf-8",
    )

    backend_args, env = _prepare_backend_configuration(
        workspace.root,
        workspace.tofu_dir,
        execution.env,
        execution.io,
    )
    env["GITHUB_TOKEN"] = execution.options.github_token
    tofu = _initialize_tofu(workspace.tofu_dir, env)

    return tfvars, backend_args, env, tofu


def _execute_apply_command(
    tofu: Tofu,
    args: list[str],
    tofu_workdir: Path,
    io: ExecutionIO,
) -> int:
    """Execute tofu apply with automatic error recovery.

    Runs apply, then handles import and prevent_destroy errors if they occur.
    Returns the final exit code.
    """
    result = invoke_tofu_command_with_result(tofu, list(args), io)
    exit_code = int(result.returncode)
    latest_result = result

    context = RecoveryContext(tofu=tofu, tofu_workdir=tofu_workdir, io=io)
    import_callbacks = RecoveryCallbacks(
        invoke_tofu_with_result=invoke_tofu_command_with_result,
        write_stream_output=write_stream_output,
        can_prompt=_can_prompt,
        prompt_yes_no=_prompt_yes_no,
        detect_missing_repo_imports=_detect_missing_repo_imports,
    )

    if exit_code != 0:
        exit_code, latest_result = apply_recovery.handle_apply_import_errors(
            context,
            latest_result,
            args,
            import_callbacks,
        )

    prevent_destroy_callbacks = RecoveryCallbacks(
        invoke_tofu_with_result=invoke_tofu_command_with_result,
        write_stream_output=write_stream_output,
        can_prompt=_can_prompt,
        prompt_yes_no=_prompt_yes_no,
        detect_prevent_destroy_forgets=_detect_prevent_destroy_forgets,
    )

    if exit_code != 0:
        exit_code, latest_result = apply_recovery.handle_apply_prevent_destroy_errors(
            context,
            latest_result,
            args,
            prevent_destroy_callbacks,
        )

    return exit_code


def _prepare_backend_configuration(
    workspace_root: Path,
    tofu_workdir: Path,
    env_source: dict[str, str],
    io: ExecutionIO,
) -> tuple[list[str], dict[str, str]]:
    """Derive backend args and environment overrides for tofu."""
    persistence_runtime = _get_persistence_runtime(
        workspace_root, tofu_workdir, env_source
    )
    backend_args: list[str] = []
    if persistence_runtime is not None:
        backend_args.append(f"-backend-config={persistence_runtime.backend_config}")
        descriptor = persistence_runtime.descriptor
        write_stream_output(
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
    persistence_backend.remove_blank_session_token(env)
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

        tofu_workdir = resolve_tofu_workdir(workdir)
        workspace = WorkspaceContext(root=workdir, tofu_dir=tofu_workdir)
        execution = ExecutionContext(options=options, io=io, env=env_source)
        _, backend_args, _, tofu = _setup_tofu_workspace(
            workspace,
            record,
            execution,
        )

        init_args = ["init", "-input=false", *backend_args]

        for args in [init_args, command]:
            write_stream_output(
                io.stderr,
                f"running: tofu {' '.join(args)} (cwd={tofu_workdir})",
            )
            if args[0] == "apply":
                exit_code = _execute_apply_command(tofu, list(args), tofu_workdir, io)
            else:
                exit_code = invoke_tofu_command(tofu, list(args), io)
            if exit_code == 0:
                write_stream_output(io.stderr, f"completed: tofu {args[0]}")
            if exit_code != 0:
                write_stream_output(io.stderr, f"failed: tofu {args[0]}")
                break
        return exit_code, workdir
