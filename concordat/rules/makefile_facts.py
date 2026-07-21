"""Makefile fact extraction via the pinned external `makeutil` CLI.

Concordat never parses GNU Make syntax itself: `makeutil parse` is the sole
source of Makefile facts. Exit code 0 is a complete parse, 1 a recovered
parse (JSON still emitted, facts possibly incomplete), and 2 a fatal error.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import typing as typ

from concordat.errors import OperationalRuleError

if typ.TYPE_CHECKING:
    import pathlib

SCHEMA_VERSION: typ.Final = 1

# `makeutil parse` contract: exit 0 is a complete parse, 1 a recovered one.
EXPECTED_STATUS_FOR_EXIT_CODE: typ.Final = {0: "complete", 1: "recovered"}

ERROR_MAKEUTIL_MISSING = (
    "makeutil is required but was not found on PATH; install the pinned "
    "revision with `cargo install --git https://github.com/leynos/makeutil "
    "--rev <pin> makeutil`"
)


@dataclasses.dataclass(frozen=True, slots=True)
class MakefileFacts:
    """A validated `makeutil parse` report plus its parse status."""

    report: dict[str, typ.Any]
    status: str


def _run_makeutil(
    path: pathlib.Path, timeout: float
) -> subprocess.CompletedProcess[str]:
    """Run `makeutil parse` on *path*, translating spawn failures.

    The subprocess runs with the Makefile's directory as its working
    directory so the recorded ``source.path`` stays repository-relative.
    """
    try:
        return subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["makeutil", "parse", path.name],  # noqa: S607 - resolved from PATH
            cwd=path.parent,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as error:
        raise OperationalRuleError(ERROR_MAKEUTIL_MISSING) from error
    except subprocess.TimeoutExpired as error:
        message = f"makeutil timed out after {timeout}s on {path}"
        raise OperationalRuleError(message) from error


def _validate_exit_code(
    completed: subprocess.CompletedProcess[str], path: pathlib.Path
) -> None:
    """Reject fatal makeutil exits before its stdout is trusted."""
    if completed.returncode not in EXPECTED_STATUS_FOR_EXIT_CODE:
        detail = (completed.stderr or "").strip() or "no diagnostic output"
        message = f"makeutil failed on {path}: {detail}"
        raise OperationalRuleError(message)


def _decode_report(stdout: str, path: pathlib.Path) -> dict[str, typ.Any]:
    """Decode makeutil stdout into a JSON object."""
    try:
        report: typ.Any = json.loads(stdout)
    except json.JSONDecodeError as error:
        message = f"makeutil emitted invalid JSON for {path}"
        raise OperationalRuleError(message) from error

    if not isinstance(report, dict):
        message = f"makeutil report for {path} is not a JSON object"
        raise OperationalRuleError(message)
    return report


def _validate_report(
    report: dict[str, typ.Any], returncode: int, path: pathlib.Path
) -> str:
    """Validate the report contract and return its agreed parse status."""
    if report.get("schema_version") != SCHEMA_VERSION:
        message = (
            f"makeutil report for {path} has unsupported schema version "
            f"{report.get('schema_version')!r}; expected {SCHEMA_VERSION}"
        )
        raise OperationalRuleError(message)

    parse = report.get("parse")
    if not isinstance(parse, dict):
        message = f"makeutil report for {path} has no `parse` object"
        raise OperationalRuleError(message)

    status = parse.get("status")
    if status not in EXPECTED_STATUS_FOR_EXIT_CODE.values():
        message = f"makeutil report for {path} has unknown parse status {status!r}"
        raise OperationalRuleError(message)

    expected = EXPECTED_STATUS_FOR_EXIT_CODE[returncode]
    if status != expected:
        message = (
            f"makeutil report for {path} disagrees with its exit code: "
            f"status {status!r} with exit {returncode} "
            f"(expected {expected!r})"
        )
        raise OperationalRuleError(message)
    return status


def inspect_makefile(path: pathlib.Path, *, timeout: float = 10.0) -> MakefileFacts:
    """Run `makeutil parse` on *path* and return its validated report.

    Fatal process exits are rejected before stdout is decoded, and the
    JSON/report contract is validated before exit-code/status agreement.
    """
    completed = _run_makeutil(path, timeout)
    _validate_exit_code(completed, path)
    report = _decode_report(completed.stdout, path)
    status = _validate_report(report, completed.returncode, path)
    return MakefileFacts(report=report, status=status)
