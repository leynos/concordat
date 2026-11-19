"""Attribute a call to the command it is made on, from masked text alone.

One statement can mention several commands, so an argument or a launch counts
only when its method is called on the command under study:
`other.arg("build")` and `command.env("P", other.output())` are not this
command's. The receiver is read backwards from the method call, through any
chained calls, to the name the chain starts on.

A name reached through a `.` or a `::` is a field or a path item, not the
command's own binding (`holder.command.arg(..)`, `support::command.arg(..)`),
so it is declined rather than guessed, however much whitespace sits around the
separator; so is a receiver that is not a plain name (`(&command).arg(..)`,
`build().arg(..)`). `Command::new(..)` is the one chain a command that is never
bound to a name can be called on, so the constructor counts as the command's
own receiver.

A launch is the command's `.status()`, `.output()` or `.spawn()`, with or
without whitespace before the parenthesis, since Rust allows `command.status ()`.

A statement that calls the command is also the only place its block depth can
be read: the chain's text before the call says which blocks the call sits in,
which decides whether its flag runs on every path.
"""

from __future__ import annotations

import re
import typing as typ

LAUNCH: typ.Final = re.compile(r"\.(?:status|output|spawn)\s*\(")
# One call on a named command, from its last receiver pivot (or the start of
# the statement) to the method call itself: `command.x().y(z).w(..)`, and
# `command.arg("build").status()` in the same form.
_CHAIN: typ.Final = re.compile(
    r"(?P<before>(?:\)|\]|\})?[^;]*?)"
    r"(?<![\w.])(?P<name>\w+)\s*\.\s*(?P<method>\w+)\s*\("
)
_OPENERS: typ.Final = {")": "(", "]": "[", "}": "{"}


def _matching_opener(masked: str, closer_at: int) -> int | None:
    """Return the offset of the bracket that opens the one at *closer_at*.

    Returns
    -------
    int | None
        The opener's offset, or None when the text is unbalanced.
    """
    closer = masked[closer_at]
    opened = _OPENERS[closer]
    depth = 0
    for index in range(closer_at, -1, -1):
        if masked[index] == closer:
            depth += 1
        elif masked[index] == opened:
            depth -= 1
            if depth == 0:
                return index
    return None


def _receiver_of(masked: str, at: int) -> str | None:
    """Return the name the method call at *at* is chained on.

    `command.arg(..)` is called on `command`, and
    `Command::new("cargo").arg(..)` on `Command`, however many calls sit
    between. A receiver reached through `.` or `::` names a field or a path
    item rather than the command's binding, so it is declined: the flag or
    launch belongs to some other value. Rust allows whitespace around `.` and
    `::`, so `holder . command` is read the same way as `holder.command`.

    Returns
    -------
    str | None
        The receiver's name, or None when the text does not name a plain one.
    """
    index = at - 1
    while index >= 0:
        while index >= 0 and masked[index].isspace():
            index -= 1
        if index < 0:
            return None
        char = masked[index]
        if char in ")]}":
            opener = _matching_opener(masked, index)
            if opener is None:
                return None
            index = opener - 1
            while index >= 0 and masked[index].isspace():
                index -= 1
            end = index
            while index >= 0 and (masked[index].isalnum() or masked[index] == "_"):
                index -= 1
            if index == end:
                return None
            continue
        if char == ":" and index > 0 and masked[index - 1] == ":":
            index -= 2
            continue
        if char == ".":
            index -= 1
            continue
        end = index
        while index >= 0 and (masked[index].isalnum() or masked[index] == "_"):
            index -= 1
        if index == end:
            return None
        # The separator that reaches a field or a path item may be spaced away
        # from the name it qualifies, so the whitespace before it is skipped
        # before the name is accepted as the command's own.
        before = index
        while before >= 0 and masked[before].isspace():
            before -= 1
        if before >= 0 and masked[before] in (".", ":"):
            return None
        return masked[index + 1 : end + 1]
    return None


def owns_call(masked: str, at: int, name: str | None) -> bool:
    """Report whether the call at *at* is made on the command under study.

    A command that is not bound to a name is only ever the `Command::new(..)`
    chain being built, so the constructor is its owner. A bound command is
    called by its binding, and its own construction chain stays its first
    statement. Another command in the same expression (`other.arg("build")`)
    belongs to neither, so its arguments are not read as this command's.

    Returns
    -------
    bool
        True when the receiver is the command's name or its constructor.
    """
    root = _receiver_of(masked, at)
    if root is None:
        return False
    return root in ("Command", name)


def call_chain(masked: str, start: int, end: int) -> re.Match[str] | None:
    """Return the call that ends the statement at *start* by the next `;`.

    A statement's first launch belongs to the command it is called on:
    `command.arg("build").status()` runs the command, while a launch inside an
    argument (`command.env("P", other.output())`) belongs to something else.

    Returns
    -------
    re.Match[str] | None
        The chain's match, or None when the statement calls no named command.
    """
    depth = 0
    index = end
    for index in range(start, end):
        match masked[index]:
            case "(" | "[" | "{":
                depth += 1
            case ")" | "]" | "}":
                depth -= 1
            case ";" if depth == 0:
                break
    return _CHAIN.match(masked, start, index)


def owns_chain(chain: re.Match[str], name: str | None) -> bool:
    """Report whether *chain* calls the command bound to *name*.

    Returns
    -------
    bool
        True for the command's own name or for `Command::new(..)`.
    """
    return chain["name"] == "Command" or chain["name"] == name


def launch_at(masked: str, start: int, end: int, name: str | None) -> int | None:
    """Return the offset of the first launch the command itself makes.

    A launch reached through another receiver (`other.status()`, or
    `command.env("P", other.output())`) belongs to something else, so it does
    not end the arguments of the command under study.

    Returns
    -------
    int | None
        The offset of the launch's method name, or None when the command is
        not launched in the statement.
    """
    for found in LAUNCH.finditer(masked, start, end):
        if owns_call(masked, found.start(), name):
            return found.start()
    return None
