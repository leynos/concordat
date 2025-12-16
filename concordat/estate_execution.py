"""Helpers for executing OpenTofu commands against an estate cache."""

from __future__ import annotations

import contextlib
import dataclasses
import os
import shutil
import sys  # noqa: F401 - Required for test patching of sys.stdin
import typing as typ
from pathlib import Path
from tempfile import mkdtemp
from types import SimpleNamespace  # noqa: TC003

import pygit2
from tofupy import Tofu

from concordat.persistence import backend as persistence_backend
from concordat.persistence import models as persistence_models

from .errors import ConcordatError
from .gitutils import build_remote_callbacks
from .tofu_github_errors import (
    detect_missing_repo_imports as _detect_missing_repo_imports,
)
from .tofu_github_errors import (
    detect_state_forgets_for_prevent_destroy as _detect_prevent_destroy_forgets,
)
from .tofu_output import normalize_tofu_result as _normalize_tofu_result
from .tofu_yaml import TOFU_DIRNAME
from .tofu_yaml import sanitize_inventory_for_tofu as _sanitize_inventory_for_tofu
from .user_interaction import can_prompt as _can_prompt
from .user_interaction import prompt_yes_no as _prompt_yes_no

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from pygit2.enums import ResetMode as _Pygit2ResetMode

    from .estate import EstateRecord

# Re-export constants for backward compatibility.
AWS_BACKEND_ENV = persistence_backend.AWS_BACKEND_ENV
SCW_BACKEND_ENV = persistence_backend.SCW_BACKEND_ENV
SPACES_BACKEND_ENV = persistence_backend.SPACES_BACKEND_ENV
AWS_SESSION_TOKEN_VAR = persistence_backend.AWS_SESSION_TOKEN_VAR
ALL_BACKEND_ENV_VARS = persistence_backend.ALL_BACKEND_ENV_VARS

# Re-export functions for backward compatibility with tests.
_build_object_key = persistence_backend.build_object_key


def _resolve_backend_environment(env: typ.Mapping[str, str]) -> dict[str, str]:
    """Resolve backend environment with EstateExecutionError on failure."""
    try:
        return persistence_backend.resolve_backend_environment(env)
    except persistence_backend.BackendConfigurationError as error:
        raise EstateExecutionError(str(error)) from error


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
    environment: cabc.Mapping[str, str] | None = None


@dataclasses.dataclass(frozen=True)
class ExecutionIO:
    """Output streams used by the tofu runner."""

    stdout: typ.IO[str]
    stderr: typ.IO[str]


@dataclasses.dataclass(frozen=True)
class PersistenceRuntime:
    """Runtime data for invoking tofu with a remote backend."""

    descriptor: persistence_models.PersistenceDescriptor
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


def _resolve_tofu_workdir(workspace_root: Path) -> Path:
    """Return the directory containing the OpenTofu root module.

    Estates are expected to carry OpenTofu configuration under `tofu/` (matching
    the bundled `platform-standards` template). Some tests and legacy layouts
    place configuration at the repository root, so we fall back to the root when
    `tofu/` is absent or does not appear to contain OpenTofu files.
    """
    candidate = workspace_root / TOFU_DIRNAME
    if not candidate.is_dir():
        return workspace_root

    has_config = any(candidate.glob("*.tofu")) or any(candidate.glob("*.tf"))
    return candidate if has_config else workspace_root


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
    """Create a Tofu wrapper with mapped environment, surfacing friendly errors."""
    try:
        return Tofu(cwd=str(workdir), env=dict(env))
    except FileNotFoundError as error:  # pragma: no cover - depends on PATH
        raise EstateExecutionError(ERROR_MISSING_TOFU) from error
    except RuntimeError as error:  # pragma: no cover - tofu misconfiguration
        raise EstateExecutionError(str(error)) from error


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

    if verb in {"plan", "apply", "import"}:
        # Prefer the CLI output for human readability. `tofupy.plan()` returns a
        # structured log/plan tuple, which is useful for automation but does not
        # include the traditional plan diff output operators expect.
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


def _invoke_tofu_command_with_result(
    tofu: Tofu,
    args: list[str],
    io: ExecutionIO,
) -> SimpleNamespace:
    """Run tofu and return normalized output, while still streaming it."""
    verb = args[0] if args else ""
    if os.environ.get("FAKE_TOFU_LOG") or verb in {"plan", "apply", "import"}:
        results = tofu._run(args, raise_on_error=False)
    else:
        method = getattr(tofu, verb, None)
        if callable(method):
            try:
                results = method(extra_args=args[1:])
            except TypeError:
                results = method()
        else:
            results = tofu._run(args, raise_on_error=False)

    normalized = _normalize_tofu_result(verb, results)
    _stream_tofu_output(io, normalized)
    return normalized


def _prepare_execution_environment(options: ExecutionOptions) -> dict[str, str]:
    """Compose the base environment for tofu invocation."""
    env_source = dict(os.environ)
    if options.environment is not None:
        env_source.update(options.environment)
    persistence_backend.remove_blank_session_token(env_source)
    return env_source


def _setup_tofu_workspace(
    workdir: Path,
    tofu_workdir: Path,
    record: EstateRecord,
    env_source: dict[str, str],
    options: ExecutionOptions,
    io: ExecutionIO,
) -> tuple[Path, list[str], dict[str, str], Tofu]:
    """Prepare the workspace for tofu execution.

    Sanitizes inventory, writes tfvars, configures backend, and initialises the
    tofu wrapper. Returns the tfvars path, backend arguments, environment, and
    tofu instance.
    """
    sanitized_inventory = _sanitize_inventory_for_tofu(
        workdir,
        tofu_workdir,
        record.inventory_path,
    )
    if sanitized_inventory:
        _write_stream_output(
            io.stderr,
            "inventory sanitized for tofu (removed YAML directives)",
        )
    tfvars = tofu_workdir / TFVARS_FILENAME
    tfvars.write_text(
        f'github_owner = "{options.github_owner}"\n',
        encoding="utf-8",
    )

    backend_args, env = _prepare_backend_configuration(
        workdir,
        tofu_workdir,
        env_source,
        io,
    )
    env["GITHUB_TOKEN"] = options.github_token
    tofu = _initialize_tofu(tofu_workdir, env)

    return tfvars, backend_args, env, tofu


def _handle_apply_import_errors(
    tofu: Tofu,
    latest_result: SimpleNamespace,
    tofu_workdir: Path,
    args: list[str],
    io: ExecutionIO,
) -> tuple[int, SimpleNamespace]:
    """Handle apply failures due to repos existing but missing from state.

    Detects GitHub repository existence errors, prompts for import, and retries
    apply. Returns updated exit code and latest result.
    """
    exit_code = int(latest_result.returncode)
    combined_output = f"{latest_result.stdout}\n{latest_result.stderr}"
    imports = _detect_missing_repo_imports(combined_output)

    if not imports:
        return exit_code, latest_result

    repos = ", ".join(slug for _, slug, _ in imports)
    prompt_msg = (
        "One or more GitHub repositories already exist but are "
        "missing from state.\n"
        f"Import into state and retry apply? ({repos}) [y/N]: "
    )

    if not _can_prompt():
        _write_stream_output(
            io.stderr,
            (
                "cannot prompt for auto-import in non-interactive "
                "mode; "
                "re-run with --keep-workdir "
                "and import manually"
            ),
        )
        return exit_code, latest_result

    if not _prompt_yes_no(prompt_msg, output=io.stderr):
        return exit_code, latest_result

    import_exit_code = 0
    for address, slug, repo_name in imports:
        import_attempts = [repo_name, slug]
        imported = False
        for import_id in import_attempts:
            _write_stream_output(
                io.stderr,
                f"running: tofu import {address} {import_id} (cwd={tofu_workdir})",
            )
            import_result = _invoke_tofu_command_with_result(
                tofu,
                ["import", address, import_id],
                io,
            )
            import_exit = int(import_result.returncode)
            if import_exit == 0:
                _write_stream_output(
                    io.stderr,
                    f"completed: tofu import {import_id}",
                )
                imported = True
                break

            failed_detail = import_result.stderr.strip() or "import failed"
            _write_stream_output(
                io.stderr,
                f"failed: tofu import {import_id}: {failed_detail}",
            )

        if not imported:
            import_exit_code = 1
            break

    if import_exit_code == 0:
        _write_stream_output(
            io.stderr,
            f"retrying: tofu apply (cwd={tofu_workdir})",
        )
        retry_result = _invoke_tofu_command_with_result(
            tofu,
            list(args),
            io,
        )
        return int(retry_result.returncode), retry_result

    return import_exit_code, latest_result


def _handle_apply_prevent_destroy_errors(
    tofu: Tofu,
    latest_result: SimpleNamespace,
    tofu_workdir: Path,
    args: list[str],
    io: ExecutionIO,
) -> tuple[int, SimpleNamespace]:
    """Handle apply failures due to lifecycle.prevent_destroy.

    Detects resources blocked by prevent_destroy, prompts for state removal,
    and retries apply. Returns updated exit code and latest result.
    """
    exit_code = int(latest_result.returncode)
    combined_output = f"{latest_result.stdout}\n{latest_result.stderr}"
    forget_slugs = _detect_prevent_destroy_forgets(combined_output)

    if not forget_slugs:
        return exit_code, latest_result

    repos = ", ".join(forget_slugs)
    prompt_msg = (
        "One or more resources are protected by "
        "lifecycle.prevent_destroy.\n"
        "This often happens when a repository is removed "
        "from the inventory and should be disenrolled.\n"
        "Remove these resources from state and retry apply? "
        f"({repos}) [y/N]: "
    )

    if not _can_prompt():
        _write_stream_output(
            io.stderr,
            (
                "cannot prompt for state cleanup in "
                "non-interactive mode; re-run with "
                "--keep-workdir and remove resources with "
                "`tofu state rm` manually"
            ),
        )
        return exit_code, latest_result

    if not _prompt_yes_no(prompt_msg, output=io.stderr):
        return exit_code, latest_result

    _write_stream_output(
        io.stderr,
        f"running: tofu state list (cwd={tofu_workdir})",
    )
    state_list_raw = tofu._run(
        ["state", "list"],
        raise_on_error=False,
    )
    state_list = _normalize_tofu_result("state", state_list_raw)
    if int(state_list.returncode) != 0:
        return int(state_list.returncode), latest_result

    addresses: list[str] = []
    for line in (state_list.stdout or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        for slug in forget_slugs:
            needle = f'module.repository["{slug}"].'
            if stripped.startswith(needle):
                addresses.append(stripped)
                break

    if not addresses:
        _write_stream_output(
            io.stderr,
            "no matching state entries found; nothing to remove",
        )
        return exit_code, latest_result

    rm_exit_code = 0
    for address in addresses:
        _write_stream_output(
            io.stderr,
            f"running: tofu state rm {address} (cwd={tofu_workdir})",
        )
        rm_result = _invoke_tofu_command_with_result(
            tofu,
            ["state", "rm", address],
            io,
        )
        rm_exit = int(rm_result.returncode)
        if rm_exit != 0:
            rm_exit_code = rm_exit
            break

    if rm_exit_code != 0:
        return rm_exit_code, latest_result

    _write_stream_output(
        io.stderr,
        f"retrying: tofu apply (cwd={tofu_workdir})",
    )
    retry_result = _invoke_tofu_command_with_result(
        tofu,
        list(args),
        io,
    )
    return int(retry_result.returncode), retry_result


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
    result = _invoke_tofu_command_with_result(tofu, list(args), io)
    exit_code = int(result.returncode)
    latest_result = result

    if exit_code != 0:
        exit_code, latest_result = _handle_apply_import_errors(
            tofu,
            latest_result,
            tofu_workdir,
            args,
            io,
        )

    if exit_code != 0:
        exit_code, latest_result = _handle_apply_prevent_destroy_errors(
            tofu,
            latest_result,
            tofu_workdir,
            args,
            io,
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

        tofu_workdir = _resolve_tofu_workdir(workdir)
        _, backend_args, _, tofu = _setup_tofu_workspace(
            workdir,
            tofu_workdir,
            record,
            env_source,
            options,
            io,
        )

        init_args = ["init", "-input=false", *backend_args]

        for args in [init_args, command]:
            _write_stream_output(
                io.stderr,
                f"running: tofu {' '.join(args)} (cwd={tofu_workdir})",
            )
            if args[0] == "apply":
                exit_code = _execute_apply_command(tofu, list(args), tofu_workdir, io)
            else:
                exit_code = _invoke_tofu_command(tofu, list(args), io)
            if exit_code == 0:
                _write_stream_output(io.stderr, f"completed: tofu {args[0]}")
            if exit_code != 0:
                _write_stream_output(io.stderr, f"failed: tofu {args[0]}")
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
