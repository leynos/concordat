"""Decide what one command value does, from the statements that build it.

The nested-cargo scan finds a spawn, then asks two questions of it: whether a
`cargo` command adds compiling work before it runs, and whether a `rustc`
command only asks the compiler who it is. Both answers come from one read: the
statements that add arguments to that command, with a helper's body spliced in
at its call site and each call attributed to the receiver it is called on. A
launch reached at the top level ends the read, so an argument added to a reused
command does not count as the first run's work; a launch inside a block ends
only that branch, so the command is read once for each path it runs on.

A shape the scan cannot attribute -- a helper defined twice, a call reached
through a path or method, a name a `use` item could import, a receiver that is
a field or a call, a parameter or binding reassigned or shadowed -- is declined
rather than guessed, because a false positive would demand configuration from a
repository that needs none. The reading is textual: comments are blanked and
literal contents kept, and braces come from the masked source, so a `;` or a
`{` in a string does not move a statement boundary. A cargo command's
subcommand is the first word its arguments give that is neither an option nor
an option's value, so `cargo help build` reads help as the subcommand and
`build` as its topic, while `-h` or `--help` makes the command compile nothing
at all.

:mod:`concordat.rules.nested_cargo_receivers` decides which calls belong to the
command; this module decides what those calls mean.
"""

from __future__ import annotations

import re
import typing as typ
from itertools import pairwise

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
_QUERY_SET: typ.Final = frozenset(QUERY_PRINTS.split("|"))
# A version flag, given as the word its literal has at run time. Anything
# computed (`.arg(if c { "--version" } ..)`) is not a known word and so is not
# a probe.
_VERSION_FLAGS: typ.Final = frozenset({"--version", "-V", "-vV", "-Vv"})
# Cargo and rustc print help and exit when given either flag, however much the
# subcommand before it looks like compilation (`cargo build --help`).
_HELP_FLAGS: typ.Final = frozenset({"-h", "--help"})
# One `.arg(..)`/`.args(..)` call, whose words are read in source order.
_ARGUMENT_CALL: typ.Final = re.compile(r"\.args?\s*\(")
# One string literal whose text is its value at run time.
_LITERAL: typ.Final = r'"[^"]*"'
_WORD: typ.Final = re.compile(_LITERAL)
# Options cargo reads one value for before the subcommand.
_VALUE_OPTIONS: typ.Final = frozenset({
    "--color",
    "--manifest-path",
    "--config",
    "-C",
    "-Z",
})
# A statement that opens an `else` arm is the other path through a launch that
# closed its `if` arm earlier in the read, rather than code that runs after it.
_ELSE: typ.Final = re.compile(r"\s*\}?\s*else\b")
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

    A `let`, a loop variable, a closure parameter or a plain assignment to the
    command's name gives it a new value: the caller's command is no longer what
    the name denotes. An unbound command has no name to move.

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
        # The name must be whole, so `my_command = ..` and `holder.command =
        # ..` move their own value rather than the command's.
        rf"|(?<![\w.]){identifier}\s*=(?!=)"
    )


def _launch_depth(masked_statement: str, launch: int, start_depth: int) -> int:
    """Return the block depth the launch at *launch* is reached at.

    The block it sits in may have been opened by the statement itself or by an
    earlier statement (`if metadata {`), so the depth the statement starts at
    is counted with the braces that precede the launch.

    Returns
    -------
    int
        The number of blocks still open where the launch runs.
    """
    opened = start_depth + masked_statement.count("{", 0, launch)
    return opened - masked_statement.count("}", 0, launch)


def _entry_depth(masked_statement: str, start_depth: int) -> int:
    """Return the block depth a statement reaches once its leading braces close.

    Statements are split at the previous `;`, so one can begin with the `}` that
    closes a block opened earlier (`} command.arg(..)`). The depth the statement
    is on is that of the text it starts with, not the depth before the brace: a
    shadow declared in the block has ended by the time the statement runs.

    Only braces before the statement's first other character count. A block the
    statement opens itself, and the `}` that closes it later in the statement,
    belong to the statement rather than before it.

    Returns
    -------
    int
        The depth after the leading closing braces.
    """
    depth = start_depth
    for char in masked_statement:
        if char.isspace():
            continue
        if char != "}":
            break
        depth -= 1
    return depth


def _end_shadows(shadows: list[int], masked_statement: str, start_depth: int) -> None:
    """Drop the shadows the statement's leading braces have closed.

    A shadow records that the command's name denotes another value inside a
    block. A statement that begins by closing that block is outside it, so the
    shadow ends before the statement is attributed and the command's name is
    read again.
    """
    entry = _entry_depth(masked_statement, start_depth)
    while shadows and entry < shadows[-1]:
        shadows.pop()


class CommandRead(typ.NamedTuple):
    """The statements that add to one command, grouped by run.

    Attributes
    ----------
    statements:
        Each statement as written, its masked twin, and the number of blocks
        still open where it starts.
    starts:
        The index in *statements* where each run begins. A command launched on
        more than one path is read once per path, and each run restarts the
        command's argument list.
    """

    statements: list[tuple[str, str, int]]
    starts: list[int]

    @property
    def runs(self) -> list[list[tuple[str, str, int]]]:
        """The statements grouped into the runs they describe."""
        bounds = [*self.starts, len(self.statements)]
        return [
            self.statements[first:last]
            for first, last in pairwise(bounds)
            if first < last
        ]


def _own_statements(text: str, start: int, window: Window) -> CommandRead:
    """Return the statements that can add arguments to the command being built.

    A command built in one chain owns only that chain. A command bound to a name
    owns the statements that call it (`command.arg(..)`), plus the binding
    itself, up to its first launch (`.status()`, `.output()`, `.spawn()`); a
    statement that calls another command, or an argument added when the command
    is reused after a launch, is not its argument. A statement is read only as
    far as its own launch, so an argument added after the run is not the run's.

    A launch inside a block does not end the read, because the command runs
    again on the path that skips the block: the statements after the block are
    that run's first arguments, and so are those of an `else` arm. A launch
    reached with no block open does end the read, because everything after it
    is a reuse of the command rather than another run. The statements after a
    conditional launch but still inside its block are that path's code after
    the run, so they are not read.

    A later declaration of the command's name, or an assignment to it, moves
    the name to another value for the rest of that block, so a statement inside
    the block is not the command's, and attribution resumes after it; a
    declaration at the command's own level ends the read.

    Statement boundaries and block depth come from the masked window, so a
    brace in a string literal does not make a later flag look conditional,
    while each statement is also returned with its comments blanked and its
    literals as written, so a flag in a string can still be read. The depth a
    statement starts at is counted from its whole text, so a block that closes
    within it does not lift the statements after it.

    Returns
    -------
    CommandRead
        The statements, grouped by the run they belong to.
    """
    name = _bound_name(text, start)
    rebinding = _rebinding(name)
    statements: list[tuple[str, str, int]] = []
    starts = [0]
    shadows: list[int] = []
    offset = 0
    depth = 0
    # The depth of the block the last conditional launch ran in, while the
    # statements that follow it on that path are still being read.
    boundary: int | None = None
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
        else:
            # A statement that begins by closing a block is no longer inside
            # the shadow that block declared, so the shadow ends before the
            # statement is attributed. A trailing `}` still belongs to the
            # shadowed block, so the depth the statement ends at is not used.
            _end_shadows(shadows, masked_statement, start_depth)
            owned = not shadows and (
                offset == 0 or _owns_statement(window.masked, offset, at, name)
            )
            launch = launch_at(window.masked, offset, at, name) if owned else None
            skip = False
            if boundary is not None:
                # The launch is measured within the statement, which is the
                # text :func:`_launch_depth` counts braces in.
                if _starts_a_run(
                    masked_statement,
                    start_depth,
                    None if launch is None else launch - offset,
                    boundary,
                ):
                    starts.append(len(statements))
                    boundary = None
                else:
                    skip = True
            if owned and not skip:
                launched_at = _record_statement(
                    statements, window, (offset, at), launch, start_depth
                )
                if launched_at == 0:
                    break
                if launched_at is not None:
                    boundary = launched_at
        while shadows and depth < shadows[-1]:
            shadows.pop()
        offset = at + 1
    return CommandRead(statements, starts)


def _record_statement(
    statements: list[tuple[str, str, int]],
    window: Window,
    span: tuple[int, int],
    launch: int | None,
    start_depth: int,
) -> int | None:
    """Append one of the command's statements, ending it at its launch.

    An argument after the launch in the same statement is added to a reused
    command, so only the text before it is kept as the run's argument.

    Returns
    -------
    int | None
        None when the statement does not launch the command, otherwise the
        block depth the launch is reached at. Zero means the launch is reached
        with no block open, which ends the read.
    """
    start, end = span
    stop = end if launch is None else launch
    statements.append((
        blank_comments(window.text[start:stop]),
        window.masked[start:stop],
        start_depth,
    ))
    if launch is None:
        return None
    return _launch_depth(window.masked[start:end], launch - start, start_depth)


def _starts_a_run(
    masked_statement: str,
    start_depth: int,
    launch: int | None,
    boundary: int,
) -> bool:
    """Report whether a statement begins the next run of the command.

    A statement that reaches the top level again (`}` closing the block the
    launch sat in, or an `else` arm) is on another path, so its arguments are a
    run of their own. A statement still inside the launched block is code that
    runs after the command has already run on that path.

    Returns
    -------
    bool
        True when the statement is reached without the boundary block open.
    """
    if _ELSE.match(masked_statement) is not None:
        return True
    if launch is None:
        return start_depth < boundary
    return _launch_depth(masked_statement, launch, start_depth) < boundary


def is_probe(text: str, start: int, window: Window) -> bool:
    """Report whether a `rustc` command only asks a question and compiles nothing.

    Only arguments added to this command count, so a `"--version"` in a comment,
    in an assertion after it or on another command is not its argument. Every
    run of the command must ask its question: a version flag added on the path
    that skips a compiling run says nothing about the run that compiles.

    Returns
    -------
    bool
        True when every run of the command is given a version or query flag.
    """
    name = _bound_name(text, start)
    read = _own_statements(text, start, window)
    runs = read.runs
    return bool(runs) and all(
        _asks_a_question([
            word
            for statement, masked_statement, depth in run
            for word in _probe_words(statement, masked_statement, depth, name)
        ])
        for run in runs
    )


def _asks_a_question(words: list[str]) -> bool:
    """Report whether an argument sequence makes the compiler print and exit.

    A `--print` query may be one word (`--print=cfg`) or two (`--print cfg`),
    and the two forms ask the same question however they are split across
    calls. `--print` with any other word after it still compiles. A help flag
    makes the compiler print its help and exit, so it asks nothing either.

    Returns
    -------
    bool
        True when a version flag, a help flag or a `--print` query is among the
        words.
    """
    for index, word in enumerate(words):
        if word in _VERSION_FLAGS or word in _HELP_FLAGS:
            return True
        if word.startswith("--print=") and word[len("--print=") :] in _QUERY_SET:
            return True
        if (
            word == "--print"
            and index + 1 < len(words)
            and words[index + 1] in _QUERY_SET
        ):
            return True
    return False


def _probe_words(
    statement: str, masked_statement: str, depth: int, name: str | None
) -> typ.Iterator[str]:
    """Yield the words a statement gives the command unconditionally, in order.

    A word inside a block (`if query_only { cmd.arg("--print=cfg") }`), whether
    added in the same statement or in one that follows the opening brace, may
    not be there when the command runs, so it is not yielded. A block may also
    close before the word in the same statement, so the braces before the word
    are counted against the depth the statement starts at. A word behind a
    `&&` or `||` short circuit may be skipped on the path that runs the
    command, so it is not yielded either. The braces are counted in the masked
    statement, so one in a string is not a block, and a word given to another
    command in the same statement is not this command's word.

    Yields
    ------
    str
        The literal text of each unconditional word.
    """
    for found in _ARGUMENT_CALL.finditer(masked_statement):
        if not owns_call(masked_statement, found.start(), name):
            continue
        before = masked_statement[: found.start()]
        if SHORT_CIRCUIT.search(before) is not None:
            continue
        if depth + before.count("{") - before.count("}") != 0:
            continue
        open_at = found.end() - 1
        close = scan_balanced(masked_statement, open_at)
        if close is None:
            return
        for item in argument_items(statement[open_at + 1 : close - 1]):
            if _WORD.fullmatch(item) is not None:
                yield item[1:-1]


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

    An unreadable word may be the subcommand, so it ends the scan with no
    answer at all -- unless it fills the value slot of the known option before
    it (`["--color", color, "build"]`), where its position alone rules it out.

    Returns
    -------
    str | None
        The word's text, an empty string when the command gives no positional
        word, or None when no word before one can be read.
    """
    skip_value = False
    for word in words:
        if skip_value:
            skip_value = False
            continue
        if word is None:
            return None
        if word in _VALUE_OPTIONS:
            skip_value = True
        elif not word.startswith(("-", "+")):
            return word
    return ""


def _cargo_options(words: typ.Iterable[str | None]) -> list[str | None]:
    """Return the words cargo itself reads, in order.

    A word after a `--` goes to the program the command runs (`cargo test --
    --help` passes `--help` to the test binary), so it is not an option or a
    subcommand of cargo and is not read here.

    Returns
    -------
    list[str | None]
        The words before the first `--`, unreadable ones included.
    """
    options: list[str | None] = []
    for word in words:
        if word == "--":
            break
        options.append(word)
    return options


def _run_compiles(run: list[tuple[str, str, int]], name: str | None) -> bool:
    """Report whether one run of the command adds compiling work before it launches.

    A help flag makes cargo print its help and exit, so a run that is given one
    compiles nothing whatever its subcommand says. Otherwise the subcommand,
    the first word that is neither an option nor an option's value, decides.
    A word the reader cannot see leaves the subcommand open, and then any
    compiling argument before the launch is enough.

    Returns
    -------
    bool
        True when the run starts compiling work.
    """
    words = _cargo_options(
        word
        for statement, masked_statement, _depth in run
        for word in _argument_words(statement, masked_statement, name)
    )
    if any(word in _HELP_FLAGS for word in words):
        return False
    first = _first_positional(words)
    if first is not None:
        return first in _COMPILING
    return any(
        owns_call(masked_statement, found.start(), name)
        for statement, masked_statement, _depth in run
        for found in COMPILES.finditer(statement)
    )


def compiles(
    text: str,
    masked: str,
    closers: dict[int, int],
    start: int,
    index: FileIndex,
) -> bool:
    """Report whether a cargo command adds compiling work before it runs.

    Each run of the command is read on its own, because a launch on one path
    restarts the argument list for the next: a probe on the if-path and a
    compile on the else-path are two answers, and either compiling one is
    enough. Within a run the subcommand is the first word that is neither an
    option nor an option's value, so `cargo help build` prints help rather than
    building (`build` is the topic help is asked about), while `-h` or
    `--help` makes cargo print help whatever the subcommand is. Only calls on
    the command count, and only up to its first launch: an argument added after
    the command has run belongs to a reuse, which compiles nothing. The calls
    include a helper's body, spliced in at the call site, so a launch in either
    place ends the arguments.

    Returns
    -------
    bool
        True when any run of the command starts compiling work.
    """
    window = command_window(text, masked, closers, start, index)
    name = _bound_name(text, start)
    return any(
        _run_compiles(run, name) for run in _own_statements(text, start, window).runs
    )
