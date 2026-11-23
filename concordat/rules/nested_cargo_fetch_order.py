"""Read whether a locked fetch is certain to run before a test on one line.

A fetch proves the cache warm only for what it runs ahead of on every path: the
commands before it on the line must succeed for the line to reach it (a fetch
after `||` runs only when the command before it failed), it must not sit in a
conditional construct that may skip it, and the commands after it up to the one
it orders must succeed for that one to be reached.
"""

from __future__ import annotations

import typing as typ

if typ.TYPE_CHECKING:
    from .nested_cargo_scripts import ScriptCommand


def line_fetches(commands: list[ScriptCommand], *, is_resolved: bool) -> bool:
    """Return whether this line runs a fetch that must succeed before it ends.

    A fetch proves the cache warm only when the command is read from text the
    expansion settled, it is not inside a conditional construct that may skip
    it, the line reaches it only after the commands before it succeeded, and
    every command after it is reached only when it succeeded, all the way to
    the end of the line.

    Returns
    -------
    bool
        True when such a fetch appears on this line.
    """
    if not is_resolved:
        return False
    return any(
        command.meaning.is_prefetch
        and not command.constructs
        and _is_reached_after_success(commands, index)
        and all(later.gates_next for later in commands[index:-1])
        for index, command in enumerate(commands)
    )


def gates_fetch(commands: list[ScriptCommand], position: int) -> bool:
    """Return whether a fetch earlier in this line gates the command at *position*.

    A fetch proves the order only for a command it runs on every path to. One
    inside an `if` body still gates a command in that same body, because both
    skip together; it proves nothing for a command outside the construct,
    which runs on the paths that skipped the fetch.

    Returns
    -------
    bool
        True when a fetch precedes *position*, encloses it or runs beside it,
        the line reaches the fetch only after the commands before it succeeded,
        and every command between the two must succeed for *position* to be
        reached.
    """
    target = commands[position].constructs
    return any(
        command.meaning.is_prefetch
        and _encloses(command.constructs, target)
        and _is_reached_after_success(commands, index)
        and all(later.gates_next for later in commands[index:position])
        for index, command in enumerate(commands[:position])
    )


def conditional_fetch_precedes(commands: list[ScriptCommand], position: int) -> bool:
    """Report whether a fetch before *position* runs only on some paths.

    A fetch and a command inside the same construct skip together, so that
    fetch is answerable by :func:`gates_fetch`. One in a different construct
    is not: the command at *position* also runs on the paths that skipped the
    fetch, and whether the cache was warm there is exactly what the reader
    cannot see.

    Returns
    -------
    bool
        True when a locked fetch precedes *position* inside a conditional
        construct the command at *position* is not also inside.
    """
    target = commands[position].constructs
    return any(
        command.meaning.is_prefetch and not _encloses(command.constructs, target)
        for command in commands[:position]
    )


def _is_reached_after_success(commands: list[ScriptCommand], index: int) -> bool:
    """Return whether the line reaches the command at *index* only after success.

    Only a command outside every construct is judged. Inside an `if` body the
    guard's own command precedes it by `;`, and the body is skipped together
    with the fetch, so what runs before the body does not decide the order. At
    the top level, a `||` or `;` before the command means it may run without
    what came first having succeeded.

    Returns
    -------
    bool
        True when the command is inside a construct, or every earlier command
        at the top level gates the next.
    """
    if commands[index].constructs:
        return True
    return all(
        earlier.gates_next for earlier in commands[:index] if not earlier.constructs
    )


def _encloses(outer: tuple[int, ...], inner: tuple[int, ...]) -> bool:
    """Report whether every path through *inner* also passes through *outer*.

    Returns
    -------
    bool
        True when *outer* is a prefix of *inner* — the same construct, or one
        the command is nested inside.
    """
    return inner[: len(outer)] == outer
