"""Attribute a call to the command it is made on, from masked text alone.

One statement can mention several commands, so an argument or a launch counts
only when its method is called on the command under study:
`other.arg("build")` and `command.env("P", other.output())` are not this
command's. The receiver is read backwards from the method call, through any
chained calls, to the name the chain starts on.

A name reached through a `.` or a `::` is a field or a path item, not the
command's own binding (`holder.command.arg(..)`, `support::command.arg(..)`),
so it is declined rather than guessed, however much whitespace sits around the
separator; a name called as a namespace (`command::factory().arg(..)`) is
declined in the same way, because the path root is a type or module rather than
the binding. A single colon is not a separator: `Result { status: command }`
is a field initializer whose value is the command, so the binding keeps its
arguments there. So is a receiver that is not a plain name
(`(&command).arg(..)`, `build().arg(..)`). `Command::new(..)` is the one chain
a command that is never bound to a name can be called on, so the constructor
counts as the command's own receiver -- but only when `Command` is reached
through `::`. A bare `Command` receiver is an ordinary name the caller may have
bound (`fn probe(Command: &mut Command)`), so its calls are declined like any
other name's.

A launch is the command's `.status()`, `.output()` or `.spawn()`, with or
without whitespace on either side of the dot or before the parenthesis, since
Rust allows `command . status ()`.

A statement that calls the command is also the only place its block depth can
be read: the chain's text before the call says which blocks the call sits in,
which decides whether its flag runs on every path.
"""

from __future__ import annotations

import re
import typing as typ

LAUNCH: typ.Final = re.compile(r"\.\s*(?:status|output|spawn)\s*\(")
# One call on a named command, from its last receiver pivot (or the start of
# the statement) to the method call itself: `command.x().y(z).w(..)`, and
# `command.arg("build").status()` in the same form.
_CHAIN: typ.Final = re.compile(
    r"(?P<before>(?:\)|\]|\})?[^;]*?)"
    r"(?<![\w.])(?P<name>\w+)\s*\.\s*(?P<method>\w+)\s*\("
)
_OPENERS: typ.Final = {")": "(", "]": "[", "}": "{"}


def _reached_through_separator(before: str) -> bool:
    """Report whether the text before a name ends in a field or path separator.

    A single colon is not a separator. Rust writes a struct field initializer as
    `field: value`, so `Settings { status: command }` passes the command as a
    value and the binding still owns the call. A path separator is the two
    adjacent colons of `support::command`, which may be spaced away from the
    name it qualifies.

    Returns
    -------
    bool
        True when the text ends in `.` or `::`.
    """
    before = before.rstrip()
    return before.endswith((".", "::"))


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
    launch belongs to some other value. A path root is declined too, unless it
    is the `Command` namespace itself, because `command::factory().arg(..)`
    calls a function on a type or module rather than on the binding. Only a
    `Command` reached through `::` is that namespace: a bare `Command` receiver
    is an ordinary name the caller may have bound (`fn probe(Command: &mut
    Command)`), so it is declined like any other name. Rust allows whitespace
    around `.` and `::`, so `holder . command` is read the same way as
    `holder.command`; a single colon is a field initializer, not a separator,
    so `Settings { status: command }` keeps the binding.

    Returns
    -------
    str | None
        The receiver's name, or None when the text does not name a plain one.
    """
    index = at - 1
    through_path = False
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
            through_path = True
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
        field = before >= 0 and masked[before] == "."
        qualified = before > 0 and masked[before] == ":" and masked[before - 1] == ":"
        if field or qualified:
            return None
        name = masked[index + 1 : end + 1]
        if through_path:
            # `std::process::Command::new(..)` is the constructor, and it is
            # the only path root that is this command's own receiver.
            return name if name == "Command" else None
        # A bare `Command` is a name like any other, and the caller may have
        # bound a value to it, so it is not the constructor's namespace.
        return None if name == "Command" else name
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


def _statement_end(masked: str, start: int, end: int) -> int:
    """Return where the statement at *start* ends: its top-level `;` or *end*.

    Returns
    -------
    int
        The offset of the statement's terminating `;`, or *end* when it has
        none within the window.
    """
    depth = 0
    for index in range(start, end):
        match masked[index]:
            case "(" | "[" | "{":
                depth += 1
            case ")" | "]" | "}":
                depth -= 1
            case ";" if depth == 0:
                return index
    return end


def call_chains(masked: str, start: int, end: int) -> typ.Iterator[re.Match[str]]:
    """Yield every call chain the statement at *start* makes, in source order.

    A statement can mention several commands (`(other.get_program(),
    command.arg("build").status())`), so every chain is offered rather than
    only the one the statement opens with; the later elements still run.

    Yields
    ------
    re.Match[str]
        One match per `name.method(..)` chain in the statement.
    """
    yield from _CHAIN.finditer(masked, start, _statement_end(masked, start, end))


def owns_chain(chain: re.Match[str], name: str | None) -> bool:
    """Report whether *chain* calls the command bound to *name*.

    The chain's text before the name is kept as `before`, so a name reached
    through a `.` or a `::` (`support::command.arg(..)`, `holder . command`) is
    declined here too, even when it spells the binding's own name. A single
    colon is a field initializer, so `Settings { status: command.arg(..) }`
    keeps the binding. A `Command` root is only the constructor when the method
    is `new`: `Command.anything(..)` names whatever the caller bound to
    `Command`, not the command under study.

    Returns
    -------
    bool
        True for the command's own name or for `Command::new(..)`, reached
        without a field or path separator.
    """
    if _reached_through_separator(chain["before"]):
        return False
    if chain["name"] == "Command":
        return chain["method"] == "new"
    return chain["name"] == name


def reaches_through(masked: str, at: int) -> bool:
    """Report whether the name at *at* is reached through `::` or a `.`.

    Rust allows whitespace around both separators, so `support:: configure(..)`
    is a path call and `harness . configure(..)` is a method call; neither can
    be a free function of the current file, however the call is spaced.

    Returns
    -------
    bool
        True when the preceding non-whitespace character is a separator.
    """
    index = at - 1
    while index >= 0 and masked[index].isspace():
        index -= 1
    if index < 0:
        return False
    if masked[index] == ".":
        return True
    return index > 0 and masked[index] == ":" and masked[index - 1] == ":"


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
