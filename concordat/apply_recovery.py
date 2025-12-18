"""Error recovery logic for tofu apply commands."""

from __future__ import annotations

import dataclasses
import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path
    from types import SimpleNamespace

    from tofupy import Tofu

    from .estate_execution import ExecutionIO


@dataclasses.dataclass
class RecoveryContext:
    """Encapsulates the execution context for recovery operations."""

    tofu: Tofu
    tofu_workdir: Path
    io: ExecutionIO


@dataclasses.dataclass
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
    normalize_tofu_result: typ.Callable[[str, object], SimpleNamespace] | None = None


def _execute_repository_imports(
    imports: list[tuple[str, str, str]],
    context: RecoveryContext,
    callbacks: RecoveryCallbacks,
) -> int:
    """Execute repository imports with fallback IDs, returning exit code.

    Args:
        imports: List of (address, slug, repo_name) tuples to import.
        context: Recovery execution context.
        callbacks: Recovery callback functions.

    Returns:
        0 if all imports succeeded, 1 if any import failed.

    """
    for address, slug, repo_name in imports:
        import_attempts = [repo_name, slug]
        imported = False
        for import_id in import_attempts:
            callbacks.write_stream_output(
                context.io.stderr,
                f"running: tofu import {address} {import_id} "
                f"(cwd={context.tofu_workdir})",
            )
            import_result = callbacks.invoke_tofu_with_result(
                context.tofu,
                ["import", address, import_id],
                context.io,
            )
            import_exit = int(import_result.returncode)
            if import_exit == 0:
                callbacks.write_stream_output(
                    context.io.stderr,
                    f"completed: tofu import {import_id}",
                )
                imported = True
                break

            failed_detail = import_result.stderr.strip() or "import failed"
            callbacks.write_stream_output(
                context.io.stderr,
                f"failed: tofu import {import_id}: {failed_detail}",
            )

        if not imported:
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
    combined_output = f"{latest_result.stdout}\n{latest_result.stderr}"

    if callbacks.detect_missing_repo_imports is None:
        return exit_code, latest_result

    imports = callbacks.detect_missing_repo_imports(combined_output)

    if not imports:
        return exit_code, latest_result

    repos = ", ".join(slug for _, slug, _ in imports)
    prompt_msg = (
        "One or more GitHub repositories already exist but are "
        "missing from state.\n"
        f"Import into state and retry apply? ({repos}) [y/N]: "
    )

    if not callbacks.can_prompt():
        callbacks.write_stream_output(
            context.io.stderr,
            (
                "cannot prompt for auto-import in non-interactive "
                "mode; "
                "re-run with --keep-workdir "
                "and import manually"
            ),
        )
        return exit_code, latest_result

    if not callbacks.prompt_yes_no(prompt_msg, context.io.stderr):
        return exit_code, latest_result

    import_exit_code = _execute_repository_imports(imports, context, callbacks)

    if import_exit_code == 0:
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

    return import_exit_code, latest_result


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
    combined_output = f"{latest_result.stdout}\n{latest_result.stderr}"

    if callbacks.detect_prevent_destroy_forgets is None:
        return exit_code, latest_result

    forget_slugs = callbacks.detect_prevent_destroy_forgets(combined_output)

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

    if not callbacks.can_prompt():
        callbacks.write_stream_output(
            context.io.stderr,
            (
                "cannot prompt for state cleanup in "
                "non-interactive mode; re-run with "
                "--keep-workdir and remove resources with "
                "`tofu state rm` manually"
            ),
        )
        return exit_code, latest_result

    if not callbacks.prompt_yes_no(prompt_msg, context.io.stderr):
        return exit_code, latest_result

    callbacks.write_stream_output(
        context.io.stderr,
        f"running: tofu state list (cwd={context.tofu_workdir})",
    )

    if callbacks.normalize_tofu_result is None:
        return exit_code, latest_result

    state_list_raw = context.tofu._run(
        ["state", "list"],
        raise_on_error=False,
    )
    state_list = callbacks.normalize_tofu_result("state", state_list_raw)
    if int(state_list.returncode) != 0:
        return int(state_list.returncode), latest_result

    addresses = _find_matching_state_addresses(
        state_list.stdout or "",
        forget_slugs,
    )

    if not addresses:
        callbacks.write_stream_output(
            context.io.stderr,
            "no matching state entries found; nothing to remove",
        )
        return exit_code, latest_result

    rm_exit_code = _remove_state_entries(
        context,
        addresses,
        callbacks,
    )

    if rm_exit_code != 0:
        return rm_exit_code, latest_result

    retry_result = _retry_apply_after_state_cleanup(
        context,
        args,
        callbacks,
    )
    return int(retry_result.returncode), retry_result
