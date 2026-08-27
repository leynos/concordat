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


class MakeLocation(typ.TypedDict):
    """Source span of a Make construct; only ``start_line`` is consumed.

    Required rather than optional: the policy reads ``start_line`` directly
    when reporting a finding's line, and a missing one makes the whole
    ``finding(...)`` term undefined — which silently drops a finding that
    should have been reported.
    """

    start_line: int


class MakeRecipe(typ.TypedDict):
    """One recipe line within a rule.

    Every field here is read by the policy without a guard, so all three are
    required and validated before the report is forwarded.
    """

    text: str
    ignore_errors: bool
    location: MakeLocation


class MakeRule(typ.TypedDict):
    """One parsed Make rule and the recipes/conditions attached to it.

    All six fields are consumed by the policy without a guard, so none is
    optional. ``conditions`` reaches Rego's ``count()``, which raises on a
    non-collection rather than degrading to undefined.
    """

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

    Returns
    -------
    subprocess.CompletedProcess[str]
        Completed makeutil process result.

    Raises
    ------
    _makeutil_error
        If makeutil cannot be started.
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
    except OSError as error:
        # Permission denied, bad working directory, and similar launch
        # failures must reach the CLI's operational-error boundary too.
        message = f"could not launch makeutil for {path}: {error}"
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


def _require_schema_version(report: dict[str, object], path: pathlib.Path) -> None:
    """Reject a report whose schema version this build cannot interpret."""
    if report.get("schema_version") != SCHEMA_VERSION:
        message = (
            f"makeutil report for {path} has unsupported schema version "
            f"{report.get('schema_version')!r}; expected {SCHEMA_VERSION}"
        )
        raise _makeutil_error(message, path)


def _read_parse_status(report: dict[str, object], path: pathlib.Path) -> str:
    """Return the recognized parse status carried by *report*."""
    parse = report.get("parse")
    if not isinstance(parse, dict):
        message = f"makeutil report for {path} has no `parse` object"
        raise _makeutil_error(message, path)

    status = typ.cast("dict[str, object]", parse).get("status")
    known_statuses = EXPECTED_STATUS_FOR_EXIT_CODE.values()
    if not isinstance(status, str) or status not in known_statuses:
        message = f"makeutil report for {path} has unknown parse status {status!r}"
        raise _makeutil_error(message, path)
    return status


def _require_status_matches_exit(
    status: str, returncode: int, path: pathlib.Path
) -> None:
    """Reject a report whose parse status contradicts makeutil's exit code."""
    expected = EXPECTED_STATUS_FOR_EXIT_CODE[returncode]
    if status != expected:
        message = (
            f"makeutil report for {path} disagrees with its exit code: "
            f"status {status!r} with exit {returncode} "
            f"(expected {expected!r})"
        )
        raise _makeutil_error(message, path)


def _validate_status(
    report: dict[str, object], returncode: int, path: pathlib.Path
) -> str:
    """Validate the schema version and parse status, returning the status."""
    _require_schema_version(report, path)
    status = _read_parse_status(report, path)
    _require_status_matches_exit(status, returncode, path)
    return status


def _malformed(label: str, path: pathlib.Path) -> OperationalRuleError:
    """Return the rejection for a report field that has the wrong shape."""
    message = f"makeutil report for {path} has a malformed `{label}`"
    return _makeutil_error(message, path)


def _require_object(value: object, label: str, path: pathlib.Path) -> dict[str, object]:
    """Return *value* as a mapping, or reject the report."""
    if not isinstance(value, dict):
        raise _malformed(label, path)
    return typ.cast("dict[str, object]", value)


def _require_list(value: object, label: str, path: pathlib.Path) -> list[object]:
    """Return *value* as a list, or reject the report."""
    if not isinstance(value, list):
        raise _malformed(label, path)
    return typ.cast("list[object]", value)


def _require_bool(value: object, label: str, path: pathlib.Path) -> None:
    """Reject *value* unless it is a boolean.

    Checked before `isinstance(value, int)` would matter: `bool` is a subclass
    of `int`, so an explicit type check is what keeps `1` from passing as
    `True`.

    Raises
    ------
    _malformed
        If *value* is not a boolean.
    """
    if not isinstance(value, bool):
        raise _malformed(label, path)


def _require_str_list(value: object, label: str, path: pathlib.Path) -> None:
    """Reject *value* unless it is a list of strings."""
    for index, item in enumerate(_require_list(value, label, path)):
        if not isinstance(item, str):
            raise _malformed(f"{label}[{index}]", path)


def _require_location(value: object, label: str, path: pathlib.Path) -> None:
    """Reject *value* unless it is an object with an integer ``start_line``.

    The policy uses ``start_line`` as a finding's reported line. A bool would
    satisfy `isinstance(..., int)`, so it is excluded explicitly.

    Raises
    ------
    _malformed
        If *value* lacks a valid integer ``start_line``.
    """
    location = _require_object(value, label, path)
    start_line = location.get("start_line")
    if not isinstance(start_line, int) or isinstance(start_line, bool):
        raise _malformed(f"{label}.start_line", path)


def _validate_recipe(value: object, label: str, path: pathlib.Path) -> None:
    """Validate one recipe entry of a rule."""
    recipe = _require_object(value, label, path)
    if not isinstance(recipe.get("text"), str):
        raise _malformed(f"{label}.text", path)
    _require_bool(recipe.get("ignore_errors"), f"{label}.ignore_errors", path)
    _require_location(recipe.get("location"), f"{label}.location", path)


def _validate_rule(value: object, label: str, path: pathlib.Path) -> None:
    """Validate one rule entry and every recipe attached to it."""
    rule = _require_object(value, label, path)
    _require_str_list(rule.get("targets"), f"{label}.targets", path)
    _require_str_list(rule.get("prerequisites"), f"{label}.prerequisites", path)
    _require_list(rule.get("conditions"), f"{label}.conditions", path)
    _require_location(rule.get("location"), f"{label}.location", path)
    _require_bool(rule.get("double_colon"), f"{label}.double_colon", path)
    for index, recipe in enumerate(
        _require_list(rule.get("recipes"), f"{label}.recipes", path)
    ):
        _validate_recipe(recipe, f"{label}.recipes[{index}]", path)


def _validate_report_shape(report: dict[str, object], path: pathlib.Path) -> None:
    """Reject malformed nested report data beyond the top-level object.

    The Rego policy reads these fields without guarding their types. Most bad
    shapes make a Rego expression *undefined* rather than raising, which
    silently drops a finding or invents one — a wrong verdict is worse than a
    refusal, so the report is rejected here instead. Two paths would raise
    outright: `count()` over `conditions`/`includes` and `contains()` over a
    recipe's `text`.

    Validation only inspects; the report mapping is forwarded to Conftest
    unchanged, with any keys makeutil adds still present.
    """
    _require_object(report.get("source"), "source", path)
    lists = {
        key: _require_list(report.get(key), key, path)
        for key in ("rules", "variables", "includes")
    }
    for index, rule in enumerate(lists["rules"]):
        _validate_rule(rule, f"rules[{index}]", path)
    for index, include in enumerate(lists["includes"]):
        label = f"includes[{index}]"
        entry = _require_object(include, label, path)
        _require_location(entry.get("location"), f"{label}.location", path)


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

    Returns
    -------
    MakefileFacts
        Validated Makefile facts from the makeutil report.

    """
    completed = _run_makeutil(path, timeout)
    _validate_exit_code(completed, path)
    report = _decode_report(completed.stdout, path)
    status = _validate_report(report, completed.returncode, path)
    return MakefileFacts(report=typ.cast("MakeutilReport", report), status=status)
