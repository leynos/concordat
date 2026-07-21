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

# Stable `operation` identifier and tool name for structured error context.
OPERATION_PARSE_MAKEFILE: typ.Final = "parse-makefile"
TOOL_MAKEUTIL: typ.Final = "makeutil"

# `makeutil parse` contract: exit 0 is a complete parse, 1 a recovered one.
EXPECTED_STATUS_FOR_EXIT_CODE: typ.Final = {0: "complete", 1: "recovered"}

ERROR_MAKEUTIL_MISSING = (
    "makeutil is required but was not found on PATH; install the pinned "
    "revision with `cargo install --git https://github.com/leynos/makeutil "
    "--rev <pin> makeutil`"
)


class MakeLocation(typ.TypedDict, total=False):
    """Source span of a Make construct; only ``start_line`` is consumed."""

    start_line: int


class MakeRecipe(typ.TypedDict, total=False):
    """One recipe line within a rule."""

    text: str
    ignore_errors: bool
    location: MakeLocation


class MakeRule(typ.TypedDict, total=False):
    """One parsed Make rule and the recipes/conditions attached to it."""

    targets: list[str]
    prerequisites: list[str]
    conditions: list[object]
    recipes: list[MakeRecipe]
    location: MakeLocation
    double_colon: bool


class MakeVariable(typ.TypedDict, total=False):
    """One parsed Make variable assignment (opaque to the policy)."""

    name: str
    operator: str


class MakeInclude(typ.TypedDict, total=False):
    """One include directive."""

    location: MakeLocation


class MakeParse(typ.TypedDict):
    """The parse-status block of a makeutil report."""

    status: str


class MakeSource(typ.TypedDict, total=False):
    """The source-file descriptor of a makeutil report."""

    path: str


class MakeutilReport(typ.TypedDict):
    """A validated `makeutil parse` report (schema version 1).

    Only the fields consumed by the policy/envelope are modelled; makeutil
    emits further keys (``tool``, per-node diagnostics) that are preserved
    verbatim in the report mapping and forwarded to Conftest untouched.
    """

    schema_version: int
    parse: MakeParse
    source: MakeSource
    rules: list[MakeRule]
    variables: list[MakeVariable]
    includes: list[MakeInclude]


@dataclasses.dataclass(frozen=True, slots=True)
class MakefileFacts:
    """A validated `makeutil parse` report plus its parse status."""

    report: MakeutilReport
    status: str


def _makeutil_error(message: str, path: pathlib.Path) -> OperationalRuleError:
    """Build a `makeutil`-parse operational error carrying the Makefile path."""
    return OperationalRuleError(
        message,
        operation=OPERATION_PARSE_MAKEFILE,
        tool=TOOL_MAKEUTIL,
        resource=path,
    )


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
        raise _makeutil_error(ERROR_MAKEUTIL_MISSING, path) from error
    except subprocess.TimeoutExpired as error:
        message = f"makeutil timed out after {timeout}s on {path}"
        raise _makeutil_error(message, path) from error


def _validate_exit_code(
    completed: subprocess.CompletedProcess[str], path: pathlib.Path
) -> None:
    """Reject fatal makeutil exits before its stdout is trusted."""
    if completed.returncode not in EXPECTED_STATUS_FOR_EXIT_CODE:
        detail = (completed.stderr or "").strip() or "no diagnostic output"
        message = f"makeutil failed on {path}: {detail}"
        raise _makeutil_error(message, path)


def _decode_report(stdout: str, path: pathlib.Path) -> dict[str, object]:
    """Decode makeutil stdout, treating it as unknown JSON until validated."""
    try:
        decoded: object = json.loads(stdout)
    except json.JSONDecodeError as error:
        message = f"makeutil emitted invalid JSON for {path}"
        raise _makeutil_error(message, path) from error

    if not isinstance(decoded, dict):
        message = f"makeutil report for {path} is not a JSON object"
        raise _makeutil_error(message, path)
    return typ.cast("dict[str, object]", decoded)


def _validate_status(
    report: dict[str, object], returncode: int, path: pathlib.Path
) -> str:
    """Validate the schema version and parse status, returning the status."""
    if report.get("schema_version") != SCHEMA_VERSION:
        message = (
            f"makeutil report for {path} has unsupported schema version "
            f"{report.get('schema_version')!r}; expected {SCHEMA_VERSION}"
        )
        raise _makeutil_error(message, path)

    parse = report.get("parse")
    if not isinstance(parse, dict):
        message = f"makeutil report for {path} has no `parse` object"
        raise _makeutil_error(message, path)

    status = typ.cast("dict[str, object]", parse).get("status")
    known_statuses = EXPECTED_STATUS_FOR_EXIT_CODE.values()
    if not isinstance(status, str) or status not in known_statuses:
        message = f"makeutil report for {path} has unknown parse status {status!r}"
        raise _makeutil_error(message, path)

    expected = EXPECTED_STATUS_FOR_EXIT_CODE[returncode]
    if status != expected:
        message = (
            f"makeutil report for {path} disagrees with its exit code: "
            f"status {status!r} with exit {returncode} "
            f"(expected {expected!r})"
        )
        raise _makeutil_error(message, path)
    return status


def _validate_report_shape(report: dict[str, object], path: pathlib.Path) -> None:
    """Reject malformed nested report data beyond the top-level object."""
    if not isinstance(report.get("source"), dict):
        message = f"makeutil report for {path} has a malformed `source` object"
        raise _makeutil_error(message, path)
    for key in ("rules", "variables", "includes"):
        if not isinstance(report.get(key), list):
            message = f"makeutil report for {path} has a malformed `{key}` list"
            raise _makeutil_error(message, path)


def _validate_report(
    report: dict[str, object], returncode: int, path: pathlib.Path
) -> str:
    """Validate the report contract and return its agreed parse status."""
    status = _validate_status(report, returncode, path)
    _validate_report_shape(report, path)
    return status


def inspect_makefile(path: pathlib.Path, *, timeout: float = 10.0) -> MakefileFacts:
    """Run `makeutil parse` on *path* and return its validated report.

    Fatal process exits are rejected before stdout is decoded, and the
    JSON/report contract — including the nested ``source``/``rules``/
    ``variables``/``includes`` shapes — is validated before exit-code/status
    agreement. The decoded mapping is only narrowed to :class:`MakeutilReport`
    once every required shape has been checked.
    """
    completed = _run_makeutil(path, timeout)
    _validate_exit_code(completed, path)
    report = _decode_report(completed.stdout, path)
    status = _validate_report(report, completed.returncode, path)
    return MakefileFacts(report=typ.cast("MakeutilReport", report), status=status)
