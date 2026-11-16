"""Read a cargo-command helper's definition and the commands its callers build.

A test that builds its cargo command in a function (`fn cargo_command() ->
Command`) and finishes it at each call site starts a different process at every
site: the helper supplies what is common and the caller adds the rest. The
definition is the helper's own construction, and each call site is a command of
its own whose chain and bound-variable calls are read like any other.
"""

from __future__ import annotations

import re
import typing as typ

from .nested_cargo_commands import (
    COMMAND_NEW,
    CargoCommand,
    bound_name,
    collect_variable,
    construction,
    follow_chain,
)
from .nested_cargo_source import enclosing_block
from .rust_source import scan_balanced

if typ.TYPE_CHECKING:
    from .nested_cargo_source import RustSource

_DECLARATION: typ.Final = re.compile(r"\bfn\s*$")


def helper_definition(
    source: RustSource, body: tuple[int, int], pairs: list[tuple[int, int]]
) -> CargoCommand | None:
    """Return the command a helper function itself builds, if it is readable.

    Returns
    -------
    CargoCommand | None
        The first cargo construction in the body, or None.
    """
    for match in COMMAND_NEW.finditer(source.masked, body[0], body[1]):
        command = construction(source, match, pairs)
        if command is not None:
            return command
    return None


def helper_call_sites(
    source: RustSource,
    name: str,
    body: tuple[int, int],
    pairs: list[tuple[int, int]],
) -> list[CargoCommand]:
    """Return the call sites of one helper and the commands they build.

    Returns
    -------
    list[CargoCommand]
        One command per call site outside the helper's own body.
    """
    pattern = re.compile(rf"\b{re.escape(name)}\s*\(")
    found: list[CargoCommand] = []
    for match in pattern.finditer(source.masked):
        if body[0] < match.start() < body[1]:
            continue
        if _DECLARATION.search(
            source.masked[max(0, match.start() - 8) : match.start()]
        ):
            continue
        close = scan_balanced(source.masked, match.end() - 1)
        if close is None:
            continue
        command = CargoCommand(start=match.start(), form="helper")
        after = follow_chain(source, command, close)
        block = enclosing_block(pairs, match.start())
        bound = bound_name(source.masked, match.start())
        if block is not None and bound is not None:
            collect_variable(source, command, bound, after, block[1], block, pairs)
        found.append(command)
    return found
