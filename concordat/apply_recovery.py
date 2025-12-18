"""Error recovery logic for tofu apply commands."""

from __future__ import annotations

import dataclasses
import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path
    from types import SimpleNamespace

    from tofupy import Tofu

    from .estate_execution import ExecutionIO


@dataclasses.dataclass(slots=True)
class RecoveryContext:
    """Encapsulates the execution context for recovery operations."""

    tofu: Tofu
    tofu_workdir: Path
    io: ExecutionIO


@dataclasses.dataclass(slots=True)
class RecoveryCallbacks:
    """Encapsulates dependency-injected callbacks for recovery operations."""

    invoke_tofu_with_result: typ.Callable[
        [Tofu, list[str], ExecutionIO], SimpleNamespace
    ]
    write_stream_output: typ.Callable[[typ.IO[str], str], None]
    can_prompt: typ.Callable[[], bool]
    prompt_yes_no: typ.Callable[[str, typ.IO[str]], bool]
    detect_missing_repo_imports: (
        typ.Callable[[str], list[tuple[str, str, str]]] | None
    ) = None
    detect_prevent_destroy_forgets: typ.Callable[[str], list[str]] | None = None


def _combined_output(result: SimpleNamespace) -> str:
    """Combine stdout and stderr from a result into a single string."""
    return f"{result.stdout}\n{result.stderr}"


def _collect_missing_repo_imports(
    latest_result: SimpleNamespace,
    callbacks: RecoveryCallbacks,
) -> list[tuple[str, str, str]]:
    """Collect missing repository imports from the latest result."""
    if callbacks.detect_missing_repo_imports is None:
        return []
    return callbacks.detect_missing_repo_imports(_combined_output(latest_result))


def _collect_prevent_destroy_forgets(
    latest_result: SimpleNamespace,
    callbacks: RecoveryCallbacks,
) -> list[str]:
    """Collect slugs for resources blocked by prevent_destroy."""
    if callbacks.detect_prevent_destroy_forgets is None:
        return []
    return callbacks.detect_prevent_destroy_forgets(_combined_output(latest_result))


def _attempt_one_import(
    context: RecoveryContext,
    callbacks: RecoveryCallbacks,
    address: str,
    repo_name: str,
    slug: str,
) -> bool:
    """Attempt to import a single repository, trying repo_name then slug.

    Returns True if import succeeded, False otherwise.
    """
    import_attempts = [repo_name, slug]
    for import_id in import_attempts:
        callbacks.write_stream_output(
            context.io.stderr,
            f"running: tofu import {address} {import_id} (cwd={context.tofu_workdir})",
        )
        import_result = callbacks.invoke_tofu_with_result(
            context.tofu,
            ["import", address, import_id],
            context.io,
        )
        if int(import_result.returncode) == 0:
            callbacks.write_stream_output(
                context.io.stderr,
                f"completed: tofu import {import_id}",
            )
            return True

        failed_detail = import_result.stderr.strip() or "import failed"
        callbacks.write_stream_output(
            context.io.stderr,
            f"failed: tofu import {import_id}: {failed_detail}",
        )

    return False


def _prompt_for_recovery_action(
    prompt_message: str,
    non_interactive_message: str,
    context: RecoveryContext,
    callbacks: RecoveryCallbacks,
    exit_code: int,
    latest_result: SimpleNamespace,
) -> tuple[bool, int, SimpleNamespace]:
    """Prompt user for recovery action approval.

    Args:
        prompt_message: The message to display to the user.
        non_interactive_message: Message to show when prompting is not possible.
        context: Recovery execution context.
        callbacks: Recovery callback functions.
        exit_code: Current exit code to return if prompting fails.
        latest_result: Latest result to return if prompting fails.

    Returns:
        Tuple of (should_proceed, exit_code, latest_result).
        If should_proceed is False, caller should return (exit_code, latest_result).

    """
    if not callbacks.can_prompt():
        callbacks.write_stream_output(context.io.stderr, non_interactive_message)
        return False, exit_code, latest_result

    if not callbacks.prompt_yes_no(prompt_message, context.io.stderr):
        return False, exit_code, latest_result

    return True, exit_code, latest_result


def _execute_repository_imports(
    imports: list[tuple[str, str, str]],
    context: RecoveryContext,
    callbacks: RecoveryCallbacks,
) -> int:
    """Execute repository imports with fallback IDs, returning exit code.

    Parameters
    ----------
    imports : list[tuple[str, str, str]]
        List of (address, slug, repo_name) tuples to import.
    context : RecoveryContext
        Recovery execution context.
    callbacks : RecoveryCallbacks
        Recovery callback functions.

    Returns
    -------
    int
        0 if all imports succeeded, 1 if any import failed.

    """
    for address, slug, repo_name in imports:
        if not _attempt_one_import(context, callbacks, address, repo_name, slug):
            return 1
    return 0


def handle_apply_import_errors(
    context: RecoveryContext,
    latest_result: SimpleNamespace,
    args: list[str],
    callbacks: RecoveryCallbacks,
) -> tuple[int, SimpleNamespace]:
    """Handle apply failures due to repos existing but missing from state.

    Detects GitHub repository existence errors, prompts for import, and retries
    apply. Returns updated exit code and latest result.
    """
    exit_code = int(latest_result.returncode)

    imports = _collect_missing_repo_imports(latest_result, callbacks)
    if not imports:
        return exit_code, latest_result

    repos = ", ".join(slug for _, slug, _ in imports)
    prompt_msg = (
        "One or more GitHub repositories already exist but are "
        "missing from state.\n"
        f"Import into state and retry apply? ({repos}) [y/N]: "
    )
    non_interactive_msg = (
        "cannot prompt for auto-import in non-interactive mode; "
        "re-run with --keep-workdir and import manually"
    )

    should_proceed, exit_code, latest_result = _prompt_for_recovery_action(
        prompt_msg,
        non_interactive_msg,
        context,
        callbacks,
        exit_code,
        latest_result,
    )
    if not should_proceed:
        return exit_code, latest_result

    import_exit_code = _execute_repository_imports(imports, context, callbacks)
    if import_exit_code != 0:
        return import_exit_code, latest_result

    callbacks.write_stream_output(
        context.io.stderr,
        f"retrying: tofu apply (cwd={context.tofu_workdir})",
    )
    retry_result = callbacks.invoke_tofu_with_result(
        context.tofu,
        list(args),
        context.io,
    )
    return int(retry_result.returncode), retry_result


def _line_matches_any_slug(line: str, slugs: list[str]) -> str | None:
    """Check if a non-empty line matches any slug pattern.

    Args:
        line: A line from tofu state list output.
        slugs: List of repository slugs to match against.

    Returns:
        The line itself if it matches any slug pattern, None otherwise.

    """
    stripped = line.strip()
    if not stripped:
        return None

    for slug in slugs:
        needle = f'module.repository["{slug}"].'
        if stripped.startswith(needle):
            return stripped

    return None


def _find_matching_state_addresses(
    state_output: str,
    forget_slugs: list[str],
) -> list[str]:
    """Filter state list output to find addresses matching the given slugs."""
    return [
        matched
        for line in state_output.splitlines()
        if (matched := _line_matches_any_slug(line, forget_slugs)) is not None
    ]


def _remove_state_entries(
    context: RecoveryContext,
    addresses: list[str],
    callbacks: RecoveryCallbacks,
) -> int:
    """Remove the given addresses from tofu state, returning exit code."""
    for address in addresses:
        callbacks.write_stream_output(
            context.io.stderr,
            f"running: tofu state rm {address} (cwd={context.tofu_workdir})",
        )
        rm_result = callbacks.invoke_tofu_with_result(
            context.tofu,
            ["state", "rm", address],
            context.io,
        )
        rm_exit = int(rm_result.returncode)
        if rm_exit != 0:
            return rm_exit
    return 0


def _retry_apply_after_state_cleanup(
    context: RecoveryContext,
    args: list[str],
    callbacks: RecoveryCallbacks,
) -> SimpleNamespace:
    """Retry tofu apply after state cleanup."""
    callbacks.write_stream_output(
        context.io.stderr,
        f"retrying: tofu apply (cwd={context.tofu_workdir})",
    )
    return callbacks.invoke_tofu_with_result(
        context.tofu,
        list(args),
        context.io,
    )


def _execute_state_list_command(
    context: RecoveryContext,
    callbacks: RecoveryCallbacks,
) -> SimpleNamespace:
    """Execute tofu state list and return the result."""
    callbacks.write_stream_output(
        context.io.stderr,
        f"running: tofu state list (cwd={context.tofu_workdir})",
    )
    return callbacks.invoke_tofu_with_result(
        context.tofu,
        ["state", "list"],
        context.io,
    )


def handle_apply_prevent_destroy_errors(
    context: RecoveryContext,
    latest_result: SimpleNamespace,
    args: list[str],
    callbacks: RecoveryCallbacks,
) -> tuple[int, SimpleNamespace]:
    """Handle apply failures due to lifecycle.prevent_destroy.

    Detects resources blocked by prevent_destroy, prompts for state removal,
    and retries apply. Returns updated exit code and latest result.
    """
    exit_code = int(latest_result.returncode)

    forget_slugs = _collect_prevent_destroy_forgets(latest_result, callbacks)
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
    non_interactive_msg = (
        "cannot prompt for state cleanup in non-interactive mode; "
        "re-run with --keep-workdir and remove resources with "
        "`tofu state rm` manually"
    )

    should_proceed, exit_code, latest_result = _prompt_for_recovery_action(
        prompt_msg,
        non_interactive_msg,
        context,
        callbacks,
        exit_code,
        latest_result,
    )
    if not should_proceed:
        return exit_code, latest_result

    state_list = _execute_state_list_command(context, callbacks)
    if int(state_list.returncode) != 0:
        return int(state_list.returncode), latest_result

    addresses = _find_matching_state_addresses(state_list.stdout or "", forget_slugs)
    if not addresses:
        callbacks.write_stream_output(
            context.io.stderr,
            "no matching state entries found; nothing to remove",
        )
        return exit_code, latest_result

    rm_exit_code = _remove_state_entries(context, addresses, callbacks)
    if rm_exit_code != 0:
        return rm_exit_code, latest_result

    retry_result = _retry_apply_after_state_cleanup(context, args, callbacks)
    return int(retry_result.returncode), retry_result
