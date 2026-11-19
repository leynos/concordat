"""Decide what one command value does, from the statements that build it.

The nested-cargo scan finds a spawn, then asks two questions of it: whether a
`cargo` command adds compiling work before it runs, and whether a `rustc`
command only asks the compiler who it is. Both answers come from one read: the
statements that add arguments to that command, with a helper's body spliced in
at its call site and each call attributed to the receiver it is called on. The
arguments stop at the command's first launch, so an argument added to a reused
command does not count as the first run's work.

A shape the scan cannot attribute -- a helper defined twice, a call reached
through a path or method, a parameter reassigned or shadowed -- is declined
rather than guessed, because a false positive would demand configuration from a
repository that needs none. The reading is textual: comments are blanked and
literal contents kept, and braces come from the masked source, so a `;` or a
`{` in a string does not move a statement boundary.
"""

from __future__ import annotations

import bisect
import re
import typing as typ

from .nested_cargo_syntax import _body_span, _parameter_at, parameter_names
from .rust_source import blank_comments

# The `--print` requests that read a table or a path and exit without compiling
# an input. `link-args` and `native-static-libs` are deliberately absent: they
# build the crate first.
QUERY_PRINTS: typ.Final = (
    "sysroot|target-libdir|host-tuple|cfg|target-list|target-cpus|target-features"
    "|code-models|relocation-models|tls-models"
)
# A version or query flag given as a plain string literal: either the sole
# argument of `.arg("..")`, or an item of an array literal of string literals
# passed to `.args([..])`. Anything computed (`.arg(if c { "--version" } ..)`)
# is not a known value and is not a probe.
_LITERAL: typ.Final = r'"[^"]*"'
_SINGLE_FLAG: typ.Final = rf'"(?:--version|-V|-vV|-Vv|--print=(?:{QUERY_PRINTS}))"'
_FLAG: typ.Final = rf'{_SINGLE_FLAG}|"--print"\s*,\s*"(?:{QUERY_PRINTS})"'
RUSTC_PROBE: typ.Final = re.compile(
    rf"""\.arg\(\s*&?{_SINGLE_FLAG}\s*\)"""
    rf"""|\.args\(\s*&?\[\s*(?:{_LITERAL}\s*,\s*)*(?:{_FLAG})\s*"""
    rf"""(?:,\s*{_LITERAL}\s*)*,?\s*\]\s*\)"""
)
LAUNCH: typ.Final = re.compile(r"\.(?:status|output|spawn)\(")
# A call behind `&&` or `||` does not run on every path to the launch.
_SHORT_CIRCUIT: typ.Final = re.compile(r"&&|\|\|")
# One call on a named command, from its last receiver pivot (or the start of
# the statement) to the method call itself: `command.x().y(z).w(..)`, and
# `command.arg("build").status()` in the same form.
_CHAIN: typ.Final = re.compile(
    r"(?P<before>(?:\)|\]|\})?[^;]*?)"
    r"(?<![\w.])(?P<name>\w+)\s*\.\s*(?P<method>\w+)\s*\("
)
SUBCOMMANDS: typ.Final = (
    "build|check|test|run|package|clippy|doc|rustc|install|publish|bench|miri"
    "|nextest|llvm-cov|rustdoc"
)
COMPILES: typ.Final = re.compile(
    rf"""\.args?\(\s*(?:&?\[\s*)?(?:["']\+\w+["']\s*,\s*)?["'](?:{SUBCOMMANDS})["']"""
)
COMMAND_NEW: typ.Final = re.compile(r"Command::new\(")
BINDING: typ.Final = re.compile(
    r"let[ \t]+(?:mut[ \t]+)?(?P<name>\w+)[ \t]*(?::[^=;]+)?=[ \t]*$"
)
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
    the caller's binding. Any other statement keeps only its braces, because
    they move the block depth a later statement is judged at. A call behind
    `&&` or `||` may not run, so it is wrapped in a block of its own.

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
        piece = f"{piece[: found.start()]}{receiver}.{piece[found.end() :]}"
        masked_piece = (
            f"{statement[: found.start()]}{receiver}.{statement[found.end() :]}"
        )
    if _SHORT_CIRCUIT.search(masked_piece, 0, found.start()) is not None:
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
    call is declined rather than resolved by name alone. The definition's text
    still stops at the next function item, so a helper at the end of the file
    cannot run into unrelated code.

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
    comes first. A command bound to a name and handed to a function defined
    once in the same file is also read against that function's body, because
    the callee adds the arguments. Those statements are inserted at the call
    site, so a launch in either the helper or the caller ends the arguments in
    the order they run. A window that ends early can miss an argument, which
    errs towards not demanding configuration.

    Returns
    -------
    Window
        The statements from the spawn to the end of the window, in original and
        masked form, with each callee body spliced in at its call.
    """
    tail = text[start + 1 :]
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
        pieces.extend((window[previous:at], f";\n{body.text};"))
        masked_pieces.extend((masked_window[previous:at], f";\n{body.masked};"))
        previous = at
    pieces.append(window[previous:])
    masked_pieces.append(masked_window[previous:])
    return Window("".join(pieces), "".join(masked_pieces))


def _bound_name(text: str, start: int) -> str | None:
    """Return the name the command built at *start* is bound to, if any."""
    bound = BINDING.search(text[max(0, start - 120) : start])
    return None if bound is None else bound["name"]


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
    between. A receiver that is not a plain name (`(&command).arg(..)`,
    `f().arg(..)`) is declined rather than guessed.

    Returns
    -------
    str | None
        The receiver's name, or None when the text does not name one.
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
        return masked[index + 1 : end + 1] if end != index else None
    return None


def _owns_call(masked: str, at: int, name: str | None) -> bool:
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


def _call_chain(masked: str, start: int, end: int) -> re.Match[str] | None:
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
    for index in range(start, end):
        match masked[index]:
            case "(" | "[" | "{":
                depth += 1
            case ")" | "]" | "}":
                depth -= 1
            case ";" if depth == 0:
                break
    else:
        index = end
    return _CHAIN.match(masked, start, index)


def _owns_chain(chain: re.Match[str], name: str | None) -> bool:
    """Report whether *chain* calls the command bound to *name*.

    Returns
    -------
    bool
        True for the command's own name or for `Command::new(..)`.
    """
    return chain["name"] == "Command" or chain["name"] == name


def _chain_depth(chain: re.Match[str], depth: int) -> int:
    """Return the block depth just before *chain*'s launch, from *depth*.

    A call segment inside a block (`if enabled { command.arg(..) }`) is read
    for braces, because they move the later depth; the launch segment is not,
    so the braces of a block it lands in (`if x { command.status() }`) are not
    counted twice. A chain written behind `&&` or `||` is at least one block
    deep, because it may not run at all.

    Returns
    -------
    int
        The block depth the launch is reached at.
    """
    before = chain["before"]
    opened = depth + before.count("{") - before.count("}")
    if _SHORT_CIRCUIT.search(before) is not None:
        return max(opened, 1)
    return opened


def _launches(masked: str, start: int, end: int, name: str | None) -> bool:
    """Report whether the statement from *start* to *end* launches this command.

    A launch reached through another receiver (`other.status()`, or
    `command.env("P", other.output())`) belongs to something else, so it does
    not end the arguments of the command under study.

    Returns
    -------
    bool
        True when a launch in the statement is called on this command.
    """
    return any(
        _owns_call(masked, found.start(), name)
        for found in LAUNCH.finditer(masked, start, end)
    )


def _own_statements(
    text: str, start: int, window: Window
) -> list[tuple[str, str, int]]:
    """Return the statements that can add arguments to the command being built.

    A command built in one chain owns only that chain. A command bound to a name
    owns the statements that call it (`command.arg(..)`), plus the binding
    itself, up to its first launch (`.status()`, `.output()`, `.spawn()`); a
    statement that calls another command, or an argument added when the command
    is reused after a launch, is not its argument. Each statement runs to its
    own launch, so a compile that launches before a later probe still counts.

    Statement boundaries and block depth come from the masked window, so a
    brace in a string literal does not make a later flag look conditional,
    while each statement is also returned with its comments blanked and its
    literals as written, so a flag in a string can still be read.

    Returns
    -------
    list[tuple[str, str, int]]
        Each statement as written, its masked twin, and the number of blocks
        still open where it starts.
    """
    name = _bound_name(text, start)
    statements: list[tuple[str, str, int]] = []
    offset = 0
    depth = 0
    launched = False
    for at, char in enumerate(window.masked):
        if char != ";":
            continue
        chain = _call_chain(window.masked, offset, at)
        owns = chain is not None and _owns_chain(chain, name)
        if offset == 0 or owns:
            statements.append((
                blank_comments(window.text[offset:at]),
                window.masked[offset:at],
                depth,
            ))
            if chain is not None:
                depth = _chain_depth(chain, depth)
                launched = owns and _launches(window.masked, offset, at, name)
            else:
                depth += window.masked.count("{", offset, at)
                depth -= window.masked.count("}", offset, at)
                launched = LAUNCH.search(window.masked, offset, at) is not None
            if launched:
                break
        else:
            depth += window.masked.count("{", offset, at)
            depth -= window.masked.count("}", offset, at)
        offset = at + 1
    else:
        chain = _call_chain(window.masked, offset, len(window.masked))
        if offset == 0 or (chain is not None and _owns_chain(chain, name)):
            statements.append((
                blank_comments(window.text[offset:]),
                window.masked[offset:],
                depth,
            ))
    return statements


def is_probe(text: str, start: int, window: Window) -> bool:
    """Report whether a `rustc` command only asks a question and compiles nothing.

    Only arguments added to this command count, so a `"--version"` in a comment,
    in an assertion after it or on another command is not its argument.

    Returns
    -------
    bool
        True when an argument of the command is a version or query flag.
    """
    name = _bound_name(text, start)
    return any(
        _adds_a_probe(statement, masked_statement, depth, name)
        for statement, masked_statement, depth in _own_statements(text, start, window)
    )


def _adds_a_probe(
    statement: str, masked_statement: str, depth: int, name: str | None
) -> bool:
    """Report whether a statement adds a probe flag on every path through it.

    A flag added inside a block (`if query_only { cmd.arg("--print=cfg") }`),
    whether in the same statement or in one that follows the opening brace, may
    not be there when the command runs, so it proves nothing. A block may also
    close before the flag in the same statement, so the braces before the flag
    are counted against the depth the statement starts at. The braces are
    counted in the masked statement, so one in a string is not a block, and a
    flag on another command in the same statement is not this command's flag.

    Returns
    -------
    bool
        True when the statement holds a probe flag of this command outside any
        block.
    """
    for found in RUSTC_PROBE.finditer(statement):
        if not _owns_call(masked_statement, found.start(), name):
            continue
        before = masked_statement[: found.start()]
        if depth + before.count("{") - before.count("}") == 0:
            return True
    return False


def compiles(
    text: str,
    masked: str,
    closers: dict[int, int],
    start: int,
    index: FileIndex,
) -> bool:
    """Report whether a cargo command adds compiling work before it runs.

    Only calls on the command count, and only up to its first launch: an
    argument added after the command has run belongs to a reuse, which compiles
    nothing. The calls include a helper's body, spliced in at the call site, so
    a launch in either place ends the arguments.

    Returns
    -------
    bool
        True when an argument before the first launch selects a compiling
        subcommand.
    """
    window = command_window(text, masked, closers, start, index)
    name = _bound_name(text, start)
    return any(
        _owns_call(masked_statement, found.start(), name)
        for statement, masked_statement, _depth in _own_statements(text, start, window)
        for found in COMPILES.finditer(statement)
    )
