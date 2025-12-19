"""Output normalization for tofupy command results.

The tofupy library returns various result types depending on the command
executed (booleans, tuples, dataclasses). This module normalizes these into a
consistent SimpleNamespace with stdout, stderr, and returncode attributes for
uniform handling throughout concordat.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

_logger = logging.getLogger(__name__)


def normalize_init_result(result: object) -> SimpleNamespace:
    """Normalize tofupy.init boolean result.

    Args:
        result: The result from tofupy.init(), typically a boolean.

    Returns:
        SimpleNamespace with stdout, stderr, and returncode.

    """
    return SimpleNamespace(stdout="", stderr="", returncode=0 if result else 1)


def normalize_plan_result(result: object) -> SimpleNamespace:
    """Normalize tofupy.plan (PlanLog, Plan|None) tuple result.

    Args:
        result: The result from tofupy.plan(), a (PlanLog, Plan) tuple.

    Returns:
        SimpleNamespace with stdout, stderr, and returncode.

    """
    if not isinstance(result, tuple) or len(result) != 2:
        return SimpleNamespace(stdout="", stderr="", returncode=1)

    plan_log, plan = result
    if plan_log is None:
        return SimpleNamespace(stdout="", stderr="", returncode=1)

    if hasattr(plan_log, "stdout") or hasattr(plan_log, "stderr"):
        stdout = getattr(plan_log, "stdout", "") or ""
        stderr = getattr(plan_log, "stderr", "") or ""
        errored = bool(
            getattr(plan_log, "errored", False) or getattr(plan, "errored", False)
        )
        return SimpleNamespace(
            stdout=stdout, stderr=stderr, returncode=1 if errored else 0
        )

    stdout, stderr, errored = _summarize_tofu_log("plan", plan_log)
    errored = bool(errored or getattr(plan, "errored", False))
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=1 if errored else 0)


def normalize_apply_result(result: object) -> SimpleNamespace:
    """Normalize tofupy.apply ApplyLog result.

    Args:
        result: The result from tofupy.apply(), an ApplyLog.

    Returns:
        SimpleNamespace with stdout, stderr, and returncode.

    """
    apply_log = result
    if apply_log is None:
        return SimpleNamespace(stdout="", stderr="", returncode=1)

    if hasattr(apply_log, "stdout") or hasattr(apply_log, "stderr"):
        stdout = getattr(apply_log, "stdout", "") or ""
        stderr = getattr(apply_log, "stderr", "") or ""
        errored = bool(getattr(apply_log, "errored", False))
        return SimpleNamespace(
            stdout=stdout, stderr=stderr, returncode=1 if errored else 0
        )

    stdout, stderr, errored = _summarize_tofu_log("apply", apply_log)
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=1 if errored else 0)


def _summarize_tofu_log(
    verb: str,
    log: object,
) -> tuple[str, str, bool]:
    """Format a concise summary from tofupy PlanLog/ApplyLog structures.

    `tofupy.plan()` and `tofupy.apply()` return dataclasses with structured
    fields such as `added`, `changed`, and lists of diagnostics. Concordat
    previously expected raw stdout/stderr fields (which do not exist on these
    dataclasses), leading to confusingly silent output even when tofu ran
    successfully.

    Args:
        verb: The tofu command verb (plan, apply, etc.).
        log: The structured log object from tofupy.

    Returns:
        A tuple of (stdout, stderr, errored).

    """
    added = int(getattr(log, "added", 0) or 0)
    changed = int(getattr(log, "changed", 0) or 0)
    removed = int(getattr(log, "removed", 0) or 0)
    imported = int(getattr(log, "imported", 0) or 0)
    operation = str(getattr(log, "operation", verb) or verb)

    has_changes = any([added, changed, removed, imported])
    summary = (
        f"{operation}: no changes."
        if not has_changes
        else (
            f"{operation}: {added} to add, {changed} to change, "
            f"{removed} to destroy, {imported} to import."
        )
    )

    errors = getattr(log, "errors", []) or []
    warnings = getattr(log, "warnings", []) or []
    diagnostics_text = _format_tofu_diagnostics(errors, warnings)
    errored = bool(errors)

    stdout = f"{summary}\n"
    stderr = diagnostics_text
    return stdout, stderr, errored


def _format_tofu_diagnostics(errors: list[object], warnings: list[object]) -> str:
    """Format structured tofu diagnostics for terminal display.

    Args:
        errors: List of error diagnostic objects.
        warnings: List of warning diagnostic objects.

    Returns:
        Formatted string for terminal output.

    """

    def render(diagnostic: object) -> list[str]:
        severity = str(getattr(diagnostic, "severity", "error") or "error").lower()
        summary = str(getattr(diagnostic, "summary", "") or "").strip()
        detail = str(getattr(diagnostic, "detail", "") or "").strip()
        header = f"{severity}: {summary}" if summary else f"{severity}"
        lines = [header]
        if detail:
            lines.append(detail)
        return lines

    lines: list[str] = []
    for diagnostic in errors:
        lines.extend(render(diagnostic))
    for diagnostic in warnings:
        lines.extend(render(diagnostic))

    return "\n".join(lines) + ("\n" if lines else "")


def normalize_tofu_result(verb: str, result: object) -> SimpleNamespace:
    """Coerce tofupy results into a consistent stdout/stderr/returncode shape.

    Args:
        verb: The tofu command verb (init, plan, apply, etc.).
        result: The result from tofupy command execution.

    Returns:
        SimpleNamespace with stdout, stderr, and returncode attributes.

    """
    # Direct tofupy _run result already matches the expected shape.
    if hasattr(result, "returncode"):
        return SimpleNamespace(
            stdout=getattr(result, "stdout", "") or "",
            stderr=getattr(result, "stderr", "") or "",
            returncode=getattr(result, "returncode", 0) or 0,
        )

    if verb == "init":
        # Dispatch to verb-specific normalizers.
        return normalize_init_result(result)

    if verb == "plan":
        return normalize_plan_result(result)

    if verb == "apply":
        return normalize_apply_result(result)

    _logger.debug("Unhandled tofu verb %r, assuming success", verb)
    return SimpleNamespace(stdout="", stderr="", returncode=0)
