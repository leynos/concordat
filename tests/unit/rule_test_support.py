"""Shared support for the `concordat.rules` test modules.

Test-only: nothing here belongs to a production module. It holds the values
and helpers used by more than one of `test_makefile_facts`, `test_runner`,
and `test_rule_rendering_cli`. Support with a single consumer stays private
to that module, so what lives here is exactly what would otherwise be
duplicated.
"""

from __future__ import annotations

import dataclasses
import typing as typ

if typ.TYPE_CHECKING:
    import pathlib

    from concordat.errors import OperationalRuleError

MINIMAL_REPORT: typ.Final = {
    "schema_version": 1,
    "tool": {
        "name": "makeutil",
        "version": "0.1.0",
        "parser": "makefile-lossless",
        "parser_version": "0.3.40",
    },
    "source": {"path": "Makefile", "sha256": "0" * 64, "byte_length": 20},
    "parse": {"status": "complete", "diagnostics": []},
    "rules": [],
    "variables": [],
    "includes": [],
}


@dataclasses.dataclass(frozen=True)
class SpawnFailureCase:
    """One subprocess launch failure and its expected error message."""

    error: Exception
    match: str


def _raise_process_error(
    error: BaseException,
) -> typ.Callable[..., typ.NoReturn]:
    """Return a subprocess replacement that raises *error*."""

    def raise_error(*args: object, **kwargs: object) -> typ.NoReturn:
        raise error

    return raise_error


def _assert_operational_context(
    error: OperationalRuleError,
    *,
    operation: str,
    tool: str,
    resource: pathlib.Path | str,
) -> None:
    """Assert stable structured context on an operational rule error."""
    assert error.operation == operation, error.operation
    assert error.tool == tool, error.tool
    assert error.resource == resource, error.resource


CARGO_STUB = '[package]\nname = "fixture"\nversion = "0.1.0"\n'


def _write_checkout(root: pathlib.Path, *, cargo: bool, makefile: bool) -> None:
    root.mkdir(exist_ok=True)
    if cargo:
        (root / "Cargo.toml").write_text(CARGO_STUB)
    if makefile:
        (root / "Makefile").write_text("lint:\n\twhitaker --all\n")
