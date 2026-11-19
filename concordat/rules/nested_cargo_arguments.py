"""Decide what one command value does, from the statements that build it.

The nested-cargo scan finds a spawn, then asks two questions of it: whether a
`cargo` command adds compiling work before it runs, and whether a `rustc`
command only asks the compiler who it is. Both answers come from one read: the
statements that add arguments to that command, with a helper's body spliced in
at its call site and each call attributed to the receiver it is called on. The
arguments stop at the command's first launch, so an argument added to a reused
command does not count as the first run's work.

A shape the scan cannot attribute -- a helper defined twice, a call reached
through a path or method, a receiver that is a field or a call, a parameter
reassigned or shadowed -- is declined rather than guessed, because a false
positive would demand configuration from a repository that needs none. The
reading is textual: comments are blanked and literal contents kept, and braces
come from the masked source, so a `;` or a `{` in a string does not move a
statement boundary. A cargo command's subcommand is the first word its
arguments give that is neither an option nor an option's value, so
`cargo help build` reads help as the subcommand and `build` as its topic.

:mod:`concordat.rules.nested_cargo_receivers` decides which calls belong to the
command; this module decides what those calls mean.
"""

from __future__ import annotations

import re
import typing as typ

from .nested_cargo_handoffs import (
    SHORT_CIRCUIT,
    FileIndex,
    Window,
    _bound_name,
    command_window,
)
from .nested_cargo_receivers import call_chain, launch_at, owns_call, owns_chain
from .nested_cargo_text import argument_items
from .rust_source import blank_comments, scan_balanced

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
# One `.arg(..)`/`.args(..)` call, whose words are read in source order.
_ARGUMENT_CALL: typ.Final = re.compile(r"\.args?\s*\(")
# One string literal whose text is its value at run time.
_WORD: typ.Final = re.compile(_LITERAL)
# Options cargo reads one value for before the subcommand.
_VALUE_OPTIONS: typ.Final = frozenset({
    "--color",
    "--manifest-path",
    "--config",
    "-C",
    "-Z",
})
_RETURN: typ.Final = re.compile(r"\breturn\b")
SUBCOMMANDS: typ.Final = (
    "build|check|test|run|package|clippy|doc|rustc|install|publish|bench|miri"
    "|nextest|llvm-cov|rustdoc"
)
COMPILES: typ.Final = re.compile(
    rf"""\.args?\(\s*(?:&?\[\s*)?(?:["']\+\w+["']\s*,\s*)?["'](?:{SUBCOMMANDS})["']"""
)
_COMPILING: typ.Final = frozenset(SUBCOMMANDS.split("|"))


def _owns_statement(masked: str, start: int, end: int, name: str | None) -> bool:
    """Report whether the statement from *start* to *end* calls the command.

    Returns
    -------
    bool
        True when the statement's own chain is called on the command.
    """
    chain = call_chain(masked, start, end)
    return chain is not None and owns_chain(chain, name)


def _rebinding(name: str | None) -> re.Pattern[str] | None:
    """Return the pattern that moves *name* to another value, if it is bound.

    A `let`, a loop variable or a closure parameter of the command's name
    declares a new value: the caller's command is no longer what the name
    denotes inside that scope. An unbound command has no name to move.

    Returns
    -------
    re.Pattern[str] | None
        The declaration, loop or closure pattern, or None when the command is
        not bound to a name.
    """
    if name is None:
        return None
    identifier = re.escape(name)
    return re.compile(
        rf"\blet\s+(?:mut\s+)?{identifier}\b"
        rf"|\bfor\s+(?:mut\s+)?{identifier}\b"
        rf"|\|\s*(?:mut\s+)?{identifier}\s*(?:\||:)"
    )


def _is_conditional(masked_statement: str, launch: int, start_depth: int) -> bool:
    """Report whether the launch at *launch* sits inside a block.

    The block it sits in may have been opened by the statement itself or by an
    earlier statement (`if metadata {`), so the depth the statement starts at
    is counted with the braces that precede the launch.

    Returns
    -------
    bool
        True when the launch is reached with a block still open.
    """
    opened = start_depth + masked_statement.count("{", 0, launch)
    return opened > masked_statement.count("}", 0, launch)


def _skips_on_a_return(masked: str, start: int) -> bool:
    """Report whether a `return` leaves the block a conditional launch sits in.

    A launch inside a block that returns on the way out is not the only run the
    command makes: the statements after the block are the first run on the path
    that skips it, so they still add arguments. The block ends at its closing
    brace, and a `return` nested deeper only leaves an inner block.

    Returns
    -------
    bool
        True when a `return` at the block's own level precedes its closing
        brace.
    """
    returns = {found.start() for found in _RETURN.finditer(masked, start)}
    depth = 0
    for index in range(start, len(masked)):
        match masked[index]:
            case "{":
                depth += 1
            case "}":
                depth -= 1
                if depth < 0:
                    return False
        if depth == 0 and index in returns:
            return True
    return False


class CommandRead(typ.NamedTuple):
    """The statements that add to one command, and whether they span runs.

    Attributes
    ----------
    statements:
        Each statement as written, its masked twin, and the number of blocks
        still open where it starts.
    several_runs:
        True when a conditional launch that returns partway through was read
        past, so the statements describe more than one run of the command.
    """

    statements: list[tuple[str, str, int]]
    several_runs: bool


def _own_statements(text: str, start: int, window: Window) -> CommandRead:
    """Return the statements that can add arguments to the command being built.

    A command built in one chain owns only that chain. A command bound to a name
    owns the statements that call it (`command.arg(..)`), plus the binding
    itself, up to its first launch (`.status()`, `.output()`, `.spawn()`); a
    statement that calls another command, or an argument added when the command
    is reused after a launch, is not its argument. A statement is read only as
    far as its own launch, so an argument added after the run is not the run's.

    A launch inside a block that returns on the way out does not end the read:
    the statements after the block are the first run on the path that skips it.

    A later declaration of the command's name moves the name to another value
    for the rest of that block, so a statement inside the block is not the
    command's, and attribution resumes after it; a declaration at the command's
    own level ends the read.

    Statement boundaries and block depth come from the masked window, so a
    brace in a string literal does not make a later flag look conditional,
    while each statement is also returned with its comments blanked and its
    literals as written, so a flag in a string can still be read. The depth a
    statement starts at is counted from its whole text, so a block that closes
    within it does not lift the statements after it.

    Returns
    -------
    CommandRead
        The statements and whether reading continued past a conditional launch.
    """
    name = _bound_name(text, start)
    rebinding = _rebinding(name)
    statements: list[tuple[str, str, int]] = []
    shadows: list[int] = []
    offset = 0
    depth = 0
    several_runs = False
    for at, char in enumerate(window.masked + ";"):
        if char != ";":
            continue
        masked_statement = window.masked[offset:at]
        start_depth = depth
        depth += masked_statement.count("{") - masked_statement.count("}")
        rebinds = (
            offset > 0
            and rebinding is not None
            and rebinding.search(masked_statement) is not None
        )
        if rebinds and depth == 0:
            break
        if rebinds:
            shadows.append(depth)
        elif not shadows and (
            offset == 0 or _owns_statement(window.masked, offset, at, name)
        ):
            launch = launch_at(window.masked, offset, at, name)
            end = at if launch is None else launch
            statements.append((
                blank_comments(window.text[offset:end]),
                window.masked[offset:end],
                start_depth,
            ))
            if launch is not None:
                if not (
                    _is_conditional(masked_statement, launch - offset, start_depth)
                    and _skips_on_a_return(window.masked, at + 1)
                ):
                    break
                several_runs = True
        while shadows and depth < shadows[-1]:
            shadows.pop()
        offset = at + 1
    return CommandRead(statements, several_runs)


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
    read = _own_statements(text, start, window)
    return any(
        _adds_a_probe(statement, masked_statement, depth, name)
        for statement, masked_statement, depth in read.statements
    )


def _adds_a_probe(
    statement: str, masked_statement: str, depth: int, name: str | None
) -> bool:
    """Report whether a statement adds a probe flag on every path through it.

    A flag added inside a block (`if query_only { cmd.arg("--print=cfg") }`),
    whether in the same statement or in one that follows the opening brace, may
    not be there when the command runs, so it proves nothing. A block may also
    close before the flag in the same statement, so the braces before the flag
    are counted against the depth the statement starts at. A flag behind a
    `&&` or `||` short circuit may be skipped on the path that runs the
    command, so it proves nothing either. The braces are counted in the masked
    statement, so one in a string is not a block, and a flag on another command
    in the same statement is not this command's flag.

    Returns
    -------
    bool
        True when the statement holds a probe flag of this command outside any
        block and behind no short circuit.
    """
    for found in RUSTC_PROBE.finditer(statement):
        if not owns_call(masked_statement, found.start(), name):
            continue
        before = masked_statement[: found.start()]
        if SHORT_CIRCUIT.search(before) is not None:
            continue
        if depth + before.count("{") - before.count("}") == 0:
            return True
    return False


def _argument_words(
    statement: str, masked_statement: str, name: str | None
) -> typ.Iterator[str | None]:
    """Yield the words one statement passes to the command, in order.

    Each `.arg(..)`/`.args(..)` call on the command contributes its arguments in
    source order, so the first word of the whole command is the subcommand that
    runs. A call on another receiver is not read, and a word that is not a
    plain literal is yielded as unreadable rather than guessed.

    Yields
    ------
    str | None
        The literal text of each argument, or None where it is not a literal.
    """
    for found in _ARGUMENT_CALL.finditer(masked_statement):
        if not owns_call(masked_statement, found.start(), name):
            continue
        open_at = found.end() - 1
        close = scan_balanced(masked_statement, open_at)
        if close is None:
            yield None
            return
        yield from (
            item[1:-1] if _WORD.fullmatch(item) is not None else None
            for item in argument_items(statement[open_at + 1 : close - 1])
        )


def _first_positional(words: typ.Iterable[str | None]) -> str | None:
    """Return the first word that is neither an option nor an option's value.

    An unreadable word may be the subcommand or the value of an option before
    it, so it ends the scan with no answer at all.

    Returns
    -------
    str | None
        The word's text, an empty string when the command gives no positional
        word, or None when no word before one can be read.
    """
    skip_value = False
    for word in words:
        if word is None:
            return None
        if skip_value:
            skip_value = False
        elif word in _VALUE_OPTIONS:
            skip_value = True
        elif not word.startswith(("-", "+")):
            return word
    return ""


def compiles(
    text: str,
    masked: str,
    closers: dict[int, int],
    start: int,
    index: FileIndex,
) -> bool:
    """Report whether a cargo command adds compiling work before it runs.

    The subcommand is the first positional word of the command's own arguments,
    so `cargo help build` prints help rather than building (`build` is the
    topic help is asked about). A word the reader cannot see leaves the
    question open, and then any compiling argument before the first launch is
    enough. Only calls on the command count, and only up to its first launch:
    an argument added after the command has run belongs to a reuse, which
    compiles nothing. The calls include a helper's body, spliced in at the call
    site, so a launch in either place ends the arguments.

    Returns
    -------
    bool
        True when the first readable positional word of the arguments is a
        compiling subcommand.
    """
    window = command_window(text, masked, closers, start, index)
    name = _bound_name(text, start)
    read = _own_statements(text, start, window)
    words = (
        word
        for statement, masked_statement, _depth in read.statements
        for word in _argument_words(statement, masked_statement, name)
    )
    first = _first_positional(words)
    # Statements from more than one run each restart the argument list, so the
    # first word of the whole command is not the first word of either run; any
    # compiling subcommand among them is still the answer.
    if first is not None and not read.several_runs:
        return first in _COMPILING
    return any(
        owns_call(masked_statement, found.start(), name)
        for statement, masked_statement, _depth in read.statements
        for found in COMPILES.finditer(statement)
    )
