"""Follow the shell control flow that decides whether a gate command runs.

A gate command inside `if false; then ...; fi` never runs, and one in the
`else` of an `if true` never runs either, so counting them as coverage reads a
gate as stronger than it is. This module walks a script's simple commands, one
lexed line at a time, and keeps those that can run. It knows two constant
conditions, `true` (or `:`) and `false`; any other condition is taken to hold
on some run, as a workflow step's `if` is, so both of its branches count as
able to run.

A script spans lines, and a condition can open on one and close on another, so
the state it leaves (which branches enclose the next command, and whether a
`cd` has left the root) is a value the caller carries from line to line.
"""

from __future__ import annotations

import typing as typ

__all__ = ["FRESH_SHELL", "Branch", "ShellState", "runnable"]


class Branch(typ.NamedTuple):
    """The `if` branch the shell is in.

    `taken` is true when an earlier branch of the same `if` is known to run, so
    later ones do not. `active` is true when this branch can run.
    """

    taken: bool
    active: bool


class ShellState(typ.NamedTuple):
    """What an earlier line of one shell leaves for the lines after it.

    `moved` is true once a `cd` has left the root, or the step's working
    directory is not the root. `branches` holds the `if` branches that enclose
    the next command, outermost first.
    """

    moved: bool = False
    branches: tuple[Branch, ...] = ()


FRESH_SHELL: typ.Final = ShellState()

type _Advance = tuple[tuple[Branch, ...], list[str] | None]


def _truth(condition: list[str]) -> bool | None:
    """Return the constant a condition is, or ``None`` when it is not constant."""
    match tuple(condition):
        case ("true",) | (":",):
            return True
        case ("false",):
            return False
        case _:
            return None


def _is_live(branches: tuple[Branch, ...]) -> bool:
    """Return whether every enclosing branch can run."""
    return all(branch.active for branch in branches)


def _condition_command(
    branches: tuple[Branch, ...], words: list[str]
) -> list[str] | None:
    """Return the words of a non-constant condition when it runs, else ``None``."""
    runs = _is_live(branches) and _truth(words[1:]) is None
    return words if runs else None


def _open(branches: tuple[Branch, ...], words: list[str]) -> _Advance:
    """Enter an `if`: its condition is a command unless it is constant."""
    verdict = _truth(words[1:])
    command = _condition_command(branches, words)
    return (*branches, Branch(verdict is True, verdict is not False)), command


def _next_test(branches: tuple[Branch, ...], words: list[str]) -> _Advance:
    """Move to an `elif`: it is not tested when an earlier branch is known to run."""
    if not branches:
        return branches, None
    *outer, current = branches
    if current.taken:
        return (*outer, Branch(taken=True, active=False)), None
    verdict = _truth(words[1:])
    moved = (*outer, Branch(verdict is True, verdict is not False))
    return moved, _condition_command(moved, words)


def _otherwise(branches: tuple[Branch, ...], words: list[str]) -> _Advance:
    """Move to an `else`: it runs unless an earlier branch is known to have."""
    if not branches:
        return branches, words[1:] or None
    *outer, current = branches
    moved = (*outer, Branch(current.taken, active=not current.taken))
    return moved, (words[1:] or None) if _is_live(moved) else None


def _close(branches: tuple[Branch, ...], _words: list[str]) -> _Advance:
    """Leave an `if` at its `fi`."""
    return branches[:-1], None


def _body(branches: tuple[Branch, ...], words: list[str]) -> _Advance:
    """Enter a `then` or `do` body: what follows the keyword is a command."""
    rest = words[1:]
    return branches, rest if rest and _is_live(branches) else None


_KEYWORDS: typ.Final = {
    "if": _open,
    "elif": _next_test,
    "else": _otherwise,
    "fi": _close,
    "then": _body,
    "do": _body,
}


def _advance(branches: tuple[Branch, ...], words: list[str]) -> _Advance:
    """Return the branches after one simple command, and the command if it runs."""
    head = words[0].lstrip("@-+") if words else ""
    handler = _KEYWORDS.get(head)
    if handler is not None:
        return handler(branches, words)
    return branches, words if _is_live(branches) else None


def runnable(
    segments: list[list[str]], branches: tuple[Branch, ...]
) -> tuple[list[list[str]], tuple[Branch, ...]]:
    """Return the simple commands of a line that can run, and the branches after it.

    *branches* are those enclosing the line's start; the branches enclosing its
    end are returned for the next line of the same shell.

    Returns
    -------
    tuple[list[list[str]], tuple[Branch, ...]]
        The segments that can run, and the branches after them.
    """
    kept: list[list[str]] = []
    for words in segments:
        branches, command = _advance(branches, words)
        if command is not None:
            kept.append(command)
    return kept, branches
