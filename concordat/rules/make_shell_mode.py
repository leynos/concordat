"""Say whether a Makefile makes each rule's recipe lines one shell.

`.ONESHELL:` makes the lines of a recipe run in one shell, so a `cd` or an `if`
opened on one line governs the lines after it. Make reads the special target
after expanding variables, so `S := .ONESHELL` with `$(S):` sets it too, and the
rule index keeps the unexpanded name `$(S)`. A reader that looks only for the
literal name credits commands that never run (an `if false` block whose body is
on later lines) and misses a `cd`.

The answer has three values. The target is present (one shell), absent, or
unknown: some target name is an unresolved Make reference that might expand to
`.ONESHELL`, so the shell mode cannot be told, and the caller reports it instead
of guessing.
"""

from __future__ import annotations

import enum
import typing as typ

from .makefile_expansion import expand

if typ.TYPE_CHECKING:
    import collections.abc as cabc

__all__ = ["ShellMode", "shell_mode"]

ONESHELL_TARGET: typ.Final = ".ONESHELL"


class ShellMode(enum.Enum):
    """How a Makefile runs the lines of a recipe."""

    PER_LINE = "per-line"
    ONE_SHELL = "one-shell"
    UNKNOWN = "unknown"


def shell_mode(
    target_names: cabc.Iterable[str], values: cabc.Mapping[str, str]
) -> ShellMode:
    """Return the shell mode a Makefile's rule targets set.

    Parameters
    ----------
    target_names:
        The names of every rule target, as the rule index holds them.
    values:
        The Make variables that have exactly one assignment.

    Returns
    -------
    ShellMode
        `ONE_SHELL` when a target is or expands to `.ONESHELL`, `UNKNOWN` when
        a target name holds a reference that could not be expanded and none
        settles it, and `PER_LINE` otherwise.
    """
    is_unknown = False
    for name in target_names:
        if name == ONESHELL_TARGET:
            return ShellMode.ONE_SHELL
        if "$" not in name:
            continue
        expanded = expand(name, values)
        if expanded.text.strip() == ONESHELL_TARGET:
            return ShellMode.ONE_SHELL
        is_unknown = is_unknown or not expanded.is_resolved
    return ShellMode.UNKNOWN if is_unknown else ShellMode.PER_LINE
