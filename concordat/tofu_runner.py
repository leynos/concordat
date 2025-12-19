"""OpenTofu command execution utilities."""

from __future__ import annotations

import os
import typing as typ

from tofupy import Tofu

from .tofu_output import normalize_tofu_result
from .tofu_yaml import TOFU_DIRNAME

if typ.TYPE_CHECKING:
    from pathlib import Path
    from types import SimpleNamespace

    from tofupy.tofu import CommandResults

    from .estate_execution import ExecutionIO

ERROR_MISSING_TOFU = "OpenTofu binary 'tofu' was not found in PATH."


def write_stream_output(stream: typ.IO[str], content: str) -> None:
    """Write content to a stream, ensuring it ends with a newline."""
    stream.write(content)
    if not content.endswith("\n"):
        stream.write("\n")
    stream.flush()


def resolve_tofu_workdir(workspace_root: Path) -> Path:
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


def initialize_tofu(workdir: Path, env: typ.Mapping[str, str]) -> Tofu:
    """Create a Tofu wrapper with mapped environment, surfacing friendly errors."""
    from .errors import ConcordatError

    try:
        return Tofu(cwd=str(workdir), env=dict(env))
    except FileNotFoundError as error:  # pragma: no cover - depends on PATH
        raise ConcordatError(ERROR_MISSING_TOFU) from error
    except RuntimeError as error:  # pragma: no cover - tofu misconfiguration
        raise ConcordatError(str(error)) from error


def stream_tofu_output(io: ExecutionIO, normalized: SimpleNamespace) -> int:
    """Write normalized tofu output to the provided IO streams and return exit code."""
    if normalized.stdout:
        write_stream_output(io.stdout, normalized.stdout)
    if normalized.stderr:
        write_stream_output(io.stderr, normalized.stderr)
    return normalized.returncode


def _run_tofu(tofu: Tofu, args: list[str]) -> CommandResults:
    """Execute tofu command and return raw result.

    This is the single primitive through which all tofu commands are executed.
    It handles the dispatch logic between direct `_run()` calls and tofupy's
    public method APIs.

    Parameters
    ----------
    tofu : Tofu
        The tofupy Tofu instance.
    args : list[str]
        The command arguments (e.g., ["apply", "-auto-approve"]).

    Returns
    -------
    CommandResults
        The raw result from tofu execution.

    """
    verb = args[0] if args else ""
    extra_args = args[1:] if args else []

    # Tests provide a fake tofu binary that only writes plain text; skip
    # tofupy's streaming JSON interface in that mode to avoid parse errors.
    if os.environ.get("FAKE_TOFU_LOG"):
        return tofu._run(args, raise_on_error=False)

    # Prefer the CLI output for human readability. `tofupy.plan()` returns a
    # structured log/plan tuple, which is useful for automation but does not
    # include the traditional plan diff output operators expect.
    if verb in {"plan", "apply", "import"}:
        return tofu._run(args, raise_on_error=False)

    method = getattr(tofu, verb, None)

    if callable(method):
        try:
            return method(extra_args=extra_args)
        except TypeError:
            # Fallback for methods that do not accept extra_args.
            return method()

    # Last-resort fallback when public APIs are unavailable.
    return tofu._run(args, raise_on_error=False)


def invoke_tofu_command(tofu: Tofu, args: list[str], io: ExecutionIO) -> int:
    """Run a tofu command, streaming stdout/stderr to the provided IO."""
    verb = args[0] if args else ""
    results = _run_tofu(tofu, args)
    normalized = normalize_tofu_result(verb, results)
    return stream_tofu_output(io, normalized)


def invoke_tofu_command_with_result(
    tofu: Tofu,
    args: list[str],
    io: ExecutionIO,
) -> SimpleNamespace:
    """Run tofu and return normalized output, while still streaming it."""
    verb = args[0] if args else ""
    results = _run_tofu(tofu, args)
    normalized = normalize_tofu_result(verb, results)
    stream_tofu_output(io, normalized)
    return normalized
