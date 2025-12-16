"""Error recovery logic for tofu apply commands."""

from __future__ import annotations

import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path
    from types import SimpleNamespace

    from tofupy import Tofu

    from .estate_execution import ExecutionIO


def handle_apply_import_errors(
    tofu: Tofu,
    latest_result: SimpleNamespace,
    tofu_workdir: Path,
    args: list[str],
    io: ExecutionIO,
    *,
    invoke_tofu_with_result: typ.Callable[
        [Tofu, list[str], ExecutionIO], SimpleNamespace
    ],
    detect_missing_repo_imports: typ.Callable[[str], list[tuple[str, str, str]]],
    can_prompt: typ.Callable[[], bool],
    prompt_yes_no: typ.Callable[[str, typ.IO[str]], bool],
    write_stream_output: typ.Callable[[typ.IO[str], str], None],
) -> tuple[int, SimpleNamespace]:
    """Handle apply failures due to repos existing but missing from state.

    Detects GitHub repository existence errors, prompts for import, and retries
    apply. Returns updated exit code and latest result.
    """
    exit_code = int(latest_result.returncode)
    combined_output = f"{latest_result.stdout}\n{latest_result.stderr}"
    imports = detect_missing_repo_imports(combined_output)

    if not imports:
        return exit_code, latest_result

    repos = ", ".join(slug for _, slug, _ in imports)
    prompt_msg = (
        "One or more GitHub repositories already exist but are "
        "missing from state.\n"
        f"Import into state and retry apply? ({repos}) [y/N]: "
    )

    if not can_prompt():
        write_stream_output(
            io.stderr,
            (
                "cannot prompt for auto-import in non-interactive "
                "mode; "
                "re-run with --keep-workdir "
                "and import manually"
            ),
        )
        return exit_code, latest_result

    if not prompt_yes_no(prompt_msg, io.stderr):
        return exit_code, latest_result

    import_exit_code = 0
    for address, slug, repo_name in imports:
        import_attempts = [repo_name, slug]
        imported = False
        for import_id in import_attempts:
            write_stream_output(
                io.stderr,
                f"running: tofu import {address} {import_id} (cwd={tofu_workdir})",
            )
            import_result = invoke_tofu_with_result(
                tofu,
                ["import", address, import_id],
                io,
            )
            import_exit = int(import_result.returncode)
            if import_exit == 0:
                write_stream_output(
                    io.stderr,
                    f"completed: tofu import {import_id}",
                )
                imported = True
                break

            failed_detail = import_result.stderr.strip() or "import failed"
            write_stream_output(
                io.stderr,
                f"failed: tofu import {import_id}: {failed_detail}",
            )

        if not imported:
            import_exit_code = 1
            break

    if import_exit_code == 0:
        write_stream_output(
            io.stderr,
            f"retrying: tofu apply (cwd={tofu_workdir})",
        )
        retry_result = invoke_tofu_with_result(
            tofu,
            list(args),
            io,
        )
        return int(retry_result.returncode), retry_result

    return import_exit_code, latest_result


def handle_apply_prevent_destroy_errors(
    tofu: Tofu,
    latest_result: SimpleNamespace,
    tofu_workdir: Path,
    args: list[str],
    io: ExecutionIO,
    *,
    invoke_tofu_with_result: typ.Callable[
        [Tofu, list[str], ExecutionIO], SimpleNamespace
    ],
    detect_prevent_destroy_forgets: typ.Callable[[str], list[str]],
    normalize_tofu_result: typ.Callable[[str, object], SimpleNamespace],
    can_prompt: typ.Callable[[], bool],
    prompt_yes_no: typ.Callable[[str, typ.IO[str]], bool],
    write_stream_output: typ.Callable[[typ.IO[str], str], None],
) -> tuple[int, SimpleNamespace]:
    """Handle apply failures due to lifecycle.prevent_destroy.

    Detects resources blocked by prevent_destroy, prompts for state removal,
    and retries apply. Returns updated exit code and latest result.
    """
    exit_code = int(latest_result.returncode)
    combined_output = f"{latest_result.stdout}\n{latest_result.stderr}"
    forget_slugs = detect_prevent_destroy_forgets(combined_output)

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

    if not can_prompt():
        write_stream_output(
            io.stderr,
            (
                "cannot prompt for state cleanup in "
                "non-interactive mode; re-run with "
                "--keep-workdir and remove resources with "
                "`tofu state rm` manually"
            ),
        )
        return exit_code, latest_result

    if not prompt_yes_no(prompt_msg, io.stderr):
        return exit_code, latest_result

    write_stream_output(
        io.stderr,
        f"running: tofu state list (cwd={tofu_workdir})",
    )
    state_list_raw = tofu._run(
        ["state", "list"],
        raise_on_error=False,
    )
    state_list = normalize_tofu_result("state", state_list_raw)
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
        write_stream_output(
            io.stderr,
            "no matching state entries found; nothing to remove",
        )
        return exit_code, latest_result

    rm_exit_code = 0
    for address in addresses:
        write_stream_output(
            io.stderr,
            f"running: tofu state rm {address} (cwd={tofu_workdir})",
        )
        rm_result = invoke_tofu_with_result(
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

    write_stream_output(
        io.stderr,
        f"retrying: tofu apply (cwd={tofu_workdir})",
    )
    retry_result = invoke_tofu_with_result(
        tofu,
        list(args),
        io,
    )
    return int(retry_result.returncode), retry_result
