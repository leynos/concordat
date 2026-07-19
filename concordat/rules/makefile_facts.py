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


def inspect_makefile(path: pathlib.Path, *, timeout: float = 10.0) -> MakefileFacts:
    """Run `makeutil parse` on *path* and return its validated report.

    The subprocess runs with the Makefile's directory as its working
    directory so the recorded ``source.path`` stays repository-relative.
    """
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
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

    if completed.returncode not in (0, 1):
        detail = (completed.stderr or "").strip() or "no diagnostic output"
        message = f"makeutil failed on {path}: {detail}"
        raise OperationalRuleError(message)

    try:
        report: dict[str, typ.Any] = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        message = f"makeutil emitted invalid JSON for {path}"
        raise OperationalRuleError(message) from error

    if report.get("schema_version") != SCHEMA_VERSION:
        message = (
            f"makeutil report for {path} has unsupported schema version "
            f"{report.get('schema_version')!r}; expected {SCHEMA_VERSION}"
        )
        raise OperationalRuleError(message)

    status = str(report["parse"]["status"])
    return MakefileFacts(report=report, status=status)
