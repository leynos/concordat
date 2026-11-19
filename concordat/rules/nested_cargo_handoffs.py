"""Read which statements and helper bodies add to one command.

A command's arguments are not always written beside its construction. They
follow it to the next `Command::new` or the next function item, and a command
bound to a name and handed to a helper is also read against that helper's
body. This module finds that window, resolves each handoff, and splices the
helper's statements in at the call site so a launch in either place ends the
arguments in the order they run.

A handoff the scan cannot attribute -- a helper defined twice, a call reached
through a path or method, a name a `use` item could import, a caller binding
that shadows the helper, a parameter reassigned or shadowed -- is declined
rather than guessed, because a false positive would demand configuration from
a repository that needs none. The
reading is textual, and every boundary and brace is read from the masked
source, so a `;` or a `{` in a string moves nothing.

:mod:`concordat.rules.nested_cargo_arguments` decides what the statements this
module returns mean.
"""

from __future__ import annotations

import bisect
import re
import typing as typ

from .nested_cargo_syntax import _body_span, _parameter_at, parameter_names

# A call behind `&&` or `||` does not run on every path to the launch.
SHORT_CIRCUIT: typ.Final = re.compile(r"&&|\|\|")
# A new command's arguments belong to it, not to the one before it.
COMMAND_NEW: typ.Final = re.compile(r"Command::new\(")
# A binding may name the constructor through a path (`std::process::Command`),
# so the qualified path that follows `=` is part of the binding, not text
# between it and the command.
BINDING: typ.Final = re.compile(
    r"let[ \t]+(?:mut[ \t]+)?(?P<name>\w+)[ \t]*(?::[^=;]+)?=[ \t]*"
    r"(?:[A-Za-z_]\w*[ \t]*::[ \t]*)*$"
)
# A following item is another scope, so the command's arguments end there.
FUNCTION_BOUNDARY: typ.Final = re.compile(
    r"\n[ \t]*(?:pub(?:\([^)]*\))?[ \t]+)?(?:async[ \t]+)?(?:const[ \t]+)?fn[ \t]+\w+"
)


def _argument_position(before: str | None) -> int | None:
    """Return the zero-based position of the bound command in a call.

    *before* is the text of the arguments that precede the command. A segment
    with a bracket, a block or a closure can hide a top-level comma, so its
    comma count is not trusted and the position is declined.

    Returns
    -------
    int | None
        The position, or None when the arguments before the command make it
        uncertain.
    """
    if before is None:
        return 0
    if any(char in before for char in "[{|<"):
        return None
    return before.count(",") + 1


class Window(typ.NamedTuple):
    """A command's statements in original and masked form, offset for offset.

    Attributes
    ----------
    text:
        The statements as written, so a flag inside a string is still readable.
    masked:
        The same statements with comments and literal bodies blanked, so a
        brace or semicolon in a literal neither opens a block nor ends a
        statement.
    """

    text: str
    masked: str


def _identifier(pattern: str) -> str:
    """Return *pattern* anchored so it cannot begin inside a longer word."""
    return rf"(?<![\w]){pattern}"


def _receiver_statements(
    text: str, masked: str, span: tuple[int, int], parameter: str, receiver: str
) -> Window:
    """Return the body statements that add arguments to *parameter*.

    A statement that calls the parameter is that command's argument, wherever
    the call sits (`if enabled { command.arg("build") }` included). Statement
    boundaries come from the masked text, so a `;` inside a string does not
    split a statement, while each statement is also kept as written so a flag
    in a string stays readable. The parameter's name is replaced by the
    caller's binding in both forms, because the probe consumer reads the
    helper's statements as part of the caller's window.

    Every statement's braces are kept, in place, because a later statement's
    block depth decides whether its probe flag is unconditional; a statement
    that follows the closing brace of a block keeps that brace in front of it.
    A later declaration of the parameter's name, a reassignment of it, or
    another command built in the helper, means the name no longer denotes the
    caller's command, so nothing after that statement is read. That declines a
    helper the scan cannot attribute, which can miss compiler work rather than
    invent it.

    A flag behind a short circuit (`query && command.arg("--version")`) is not
    on every path, so a statement with a `&&` or `||` before the call keeps a
    block open around the call; the caller's depth is closed again at the end,
    because the helper has returned by then.

    Returns
    -------
    Window
        The statements that call the parameter, semicolon-separated, with the
        braces of the statements between them retained.
    """
    start, end = span
    name = _identifier(re.escape(parameter))
    own = re.compile(rf"{name}\s*\.")
    shadow = re.compile(
        rf"\bfor\s+(?:mut\s+)?{name}\b"
        rf"|\blet\b[^=;]*\b{name}\b"
        rf"|{name}\s*=(?!=)"
        rf"|\|\s*(?:mut\s+)?{name}\s*(?:\||:)"
    )
    pieces: list[str] = []
    masked_pieces: list[str] = []
    offset = start
    for statement in masked[start:end].split(";"):
        if COMMAND_NEW.search(statement) or shadow.search(statement):
            break
        piece, masked_piece = _receiver_piece(
            text, statement, own, parameter, receiver, offset
        )
        pieces.append(piece)
        masked_pieces.append(masked_piece)
        offset += len(statement) + 1
    if not pieces:
        return Window("", "")
    # The read may stop inside a block the helper still holds open (a
    # `Command::new` in the block ends it), so the window is closed before it
    # is spliced, and the caller's own depth resumes after the call.
    unclosed = sum(piece.count("{") - piece.count("}") for piece in masked_pieces)
    closers = "}" * max(unclosed, 0)
    return Window(";\n".join([*pieces, closers]), ";\n".join([*masked_pieces, closers]))


def _receiver_piece(
    text: str,
    statement: str,
    own: re.Pattern[str],
    parameter: str,
    receiver: str,
    offset: int,
) -> tuple[str, str]:
    """Return one helper statement as written and masked, for the caller.

    A statement that calls the parameter is kept with its argument, rewritten to
    the caller's binding. Every use in the statement is rewritten, because a
    statement can read the parameter once and then add to it
    (`if target.get_program() != "x" { target.arg("build") }`). Any other
    statement keeps only its braces, because they move the block depth a later
    statement is judged at. A call behind `&&` or `||` may not run, so it is
    wrapped in a block of its own.

    Returns
    -------
    tuple[str, str]
        The statement as written and its masked twin, both with the braces that
        place it in the caller's depth.
    """
    found = own.search(statement)
    if found is None:
        braces = "{" * statement.count("{") + "}" * statement.count("}")
        return braces, braces
    piece = text[offset : offset + len(statement)]
    masked_piece = statement
    if receiver != parameter:
        # The uses are found in the masked statement and spliced into both
        # forms at the same offsets, so the two stay the same length and the
        # reader can still index one by the other.
        for use in reversed(list(own.finditer(masked_piece))):
            piece = f"{piece[: use.start()]}{receiver}.{piece[use.end() :]}"
            masked_piece = (
                f"{masked_piece[: use.start()]}{receiver}.{masked_piece[use.end() :]}"
            )
    if SHORT_CIRCUIT.search(masked_piece, 0, found.start()) is not None:
        return "{" + piece + "}", "{" + masked_piece + "}"
    return piece, masked_piece


class FileIndex(typ.NamedTuple):
    """The `fn` definitions and item boundaries of one masked source.

    Attributes
    ----------
    definitions:
        Each `fn` name with the matches that define it, in source order.
    boundaries:
        The start offset of every function item, in source order.
    """

    definitions: dict[str, list[re.Match[str]]]
    boundaries: list[int]


def file_index(masked: str) -> FileIndex:
    """Index the `fn` definitions and item boundaries of *masked* once.

    Each handoff in a file asks the same questions about the same text, so the
    names and boundaries are collected once rather than rescanned per call.

    Returns
    -------
    FileIndex
        The definitions by name and the ordered boundary offsets.
    """
    definitions: dict[str, list[re.Match[str]]] = {}
    for found in re.finditer(r"\bfn\s+(?P<name>\w+)", masked):
        definitions.setdefault(found["name"], []).append(found)
    return FileIndex(
        definitions, [found.start() for found in FUNCTION_BOUNDARY.finditer(masked)]
    )


def _shadowed(masked: str, name: str, at: int, index: FileIndex) -> bool:
    """Report whether *name* may be a local binding at *at* rather than a `fn`.

    A parameter of the caller's own function, a closure, loop variable, `let`,
    `const` or `static` with the helper's name shadows the free function, so
    the call reaches something the scan cannot read. The caller's parameter
    list is read because `fn run(configure: fn(&mut Command))` calls the
    argument, not the free function.

    Returns
    -------
    bool
        True when a binding of that name is in scope at *at*.
    """
    at = min(at, len(masked))
    boundary = bisect.bisect_left(index.boundaries, at) - 1
    start = index.boundaries[boundary] if boundary >= 0 else 0
    pattern = rf"\b{re.escape(name)}\b"
    binder = re.compile(
        rf"\b(?:let\s+(?:mut\s+)?|const\s+|static\s+|for\s+(?:mut\s+)?){pattern}"
        rf"|\|\s*(?:mut\s+)?{pattern}\s*(?:\||:)"
    )
    if binder.search(masked, start, at) is not None:
        return True
    return name in parameter_names(masked, start)


def _imported(masked: str, name: str) -> bool:
    """Report whether a `use` in the file could bring *name* in.

    A call to an imported function resolves to another file, so a same-file
    `fn` of that name is not necessarily the one the call reaches. The scan
    cannot tell the two apart, so the handoff is declined rather than read from
    the local namesake; a false positive here would hide a real compile.

    Returns
    -------
    bool
        True when a `use` item names *name*.
    """
    return re.search(rf"\buse\b[^;]*\b{re.escape(name)}\b", masked) is not None


def _callee_arguments(
    text: str,
    masked: str,
    closers: dict[int, int],
    call: re.Match[str],
    receiver: str,
    base: int,
    index: FileIndex,
) -> Window | None:
    """Return the statements a call hands to its helper's bound parameter.

    The helper must be defined exactly once in the file: with two definitions
    the caller's lexical scope is unknown, so no argument is read from either.
    A local binding of the helper's name before the call shadows it, so the
    call is declined rather than resolved by name alone; so is a name a `use`
    item could import, because the call then reaches another file and a local
    namesake is not what it runs. The definition's text still stops at the next
    function item, so a helper at the end of the file cannot run into unrelated
    code.

    *base* is the file offset of the window *call* was matched in, used to find
    the caller's scope for the shadowing check.

    Returns
    -------
    Window | None
        The statements on the parameter that receives the command, rewritten to
        *receiver*, or None when the helper is undefined, duplicated, shadowed
        or shaped uncertainly.
    """
    if _shadowed(masked, call["callee"], base + call.start(), index):
        return None
    if _imported(masked, call["callee"]):
        return None
    definitions = index.definitions.get(call["callee"], [])
    if len(definitions) != 1:
        return None
    definition = definitions[0]
    position = _argument_position(call["before"])
    if position is None:
        return None
    found = _parameter_at(masked, definition.end(), position)
    if found is None:
        return None
    parameter, close_at = found
    span = _body_span(masked, closers, close_at + 1)
    if span is None:
        return None
    later = bisect.bisect_right(index.boundaries, definition.end())
    limit = index.boundaries[later] if later < len(index.boundaries) else len(masked)
    start, end = span
    end = min(end, limit)
    if end <= start:
        return None
    return _receiver_statements(text, masked, (start, end), parameter, receiver)


def _callee_bodies(
    text: str,
    masked: str,
    closers: dict[int, int],
    window: str,
    name: str,
    base: int,
    index: FileIndex,
) -> list[tuple[int, Window]]:
    """Return the helper statements each call adds, with the call's end offset.

    `let mut command = Command::new(cargo); add_build(&mut command)` adds its
    arguments in `add_build`, so those arguments are read from that function's
    body. Only statements on the parameter that receives the command count, so
    another command built in the helper does not contribute. A callee defined
    in another file is not seen, which errs towards not demanding
    configuration, and a call written as a path (`support::configure(..)`) or a
    method (`self.configure(..)`) names something else, so it is not read as
    this file's free function.

    Returns
    -------
    list[tuple[int, Window]]
        The offset in *window* just past each call, with the statements it adds;
        empty when no helper is read.
    """
    # Rust allows whitespace around `::` and `.`, so the regex can only rule
    # out a separator that touches the name; :func:`_reaches_through` finds one
    # that sits a few spaces before it.
    passed = re.compile(
        rf"(?<![.:\w])(?P<callee>\w+)\s*\(\s*(?:(?P<before>[^()]*),\s*)?"
        rf"(?<![.:\w])(?:&(?:mut\s+)?)?{re.escape(name)}\s*[,)]"
    )
    bodies: list[tuple[int, Window]] = []
    for call in passed.finditer(window):
        if _reaches_through(window, call.start()):
            continue
        body = _callee_arguments(text, masked, closers, call, name, base, index)
        if body is not None:
            bodies.append((call.end(), body))
    return bodies


def _reaches_through(masked: str, at: int) -> bool:
    """Report whether the name at *at* is reached through `::` or a `.`.

    Rust allows whitespace around both separators, so `support:: configure(..)`
    is a path call and `harness . configure(..)` is a method call; neither can
    be this file's free function, however the call is spaced.

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


def command_window(
    text: str,
    masked: str,
    closers: dict[int, int],
    start: int,
    index: FileIndex,
) -> Window:
    """Return the statements of the command built at *start*, up to a likely end.

    A command's arguments follow its construction, so the window runs to the
    next `Command::new` (another command) or the next function item, whichever
    comes first. Both boundaries are found in the masked source, so a
    `Command::new(` inside a string is data and does not end the window early.
    A command bound to a name and handed to a function defined once in the same
    file is also read against that function's body, because the callee adds the
    arguments. Those statements are inserted at the call site, so a launch in
    either the helper or the caller ends the arguments in the order they run. A
    window that ends early can miss an argument, which errs towards not
    demanding configuration.

    Returns
    -------
    Window
        The statements from the spawn to the end of the window, in original and
        masked form, with each callee body spliced in at its call.
    """
    tail = masked[start + 1 :]
    ends = [len(tail)]
    ends.extend(
        found.start()
        for found in (COMMAND_NEW.search(tail), FUNCTION_BOUNDARY.search(tail))
        if found is not None
    )
    length = 1 + min(ends)
    window = text[start : start + length]
    masked_window = masked[start : start + length]
    bound = BINDING.search(text[max(0, start - 120) : start])
    if bound is None:
        return Window(window, masked_window)
    pieces: list[str] = []
    masked_pieces: list[str] = []
    previous = 0
    for at, body in _callee_bodies(
        text, masked, closers, masked_window, bound["name"], start, index
    ):
        pieces.extend((
            window[previous:at],
            _spliced(body.text, masked_window, at),
        ))
        masked_pieces.extend((
            masked_window[previous:at],
            _spliced(body.masked, masked_window, at),
        ))
        previous = at
    pieces.append(window[previous:])
    masked_pieces.append(masked_window[previous:])
    return Window("".join(pieces), "".join(masked_pieces))


def _spliced(body: str, masked_window: str, end: int) -> str:
    """Return a helper body to insert where the call ending at *end* runs.

    A call behind `&&` or `||` may not run at all, so its helper's statements
    are wrapped in a block of their own: a flag the helper adds there does not
    sit on every path to the caller's launch.

    Returns
    -------
    str
        The body, wrapped in braces when the call is short-circuited.
    """
    statement = masked_window.rfind(";", 0, end) + 1
    if SHORT_CIRCUIT.search(masked_window, statement, end) is None:
        return f";\n{body};"
    return f";\n{{{body}}};"


def _bound_name(text: str, start: int) -> str | None:
    """Return the name the command built at *start* is bound to, if any."""
    bound = BINDING.search(text[max(0, start - 120) : start])
    return None if bound is None else bound["name"]
