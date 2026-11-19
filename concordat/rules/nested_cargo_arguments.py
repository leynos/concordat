"""Decide what one command value does, from the statements that build it.

The nested-cargo scan finds a spawn, then asks two questions of it: whether a
`cargo` command adds compiling work before it runs, and whether a `rustc`
command only asks the compiler who it is. Both answers come from one read: the
statements that add arguments to that command, with a helper's body spliced in
at its call site and each call attributed to the receiver it is called on. A
launch reached at the top level ends the read, so an argument added to a reused
command does not count as the first run's work; a launch inside a block ends
only that branch, and a `return` inside a block ends its path, so the command
is read once for each path it runs on. A run that leaves a branch inherits the
arguments given before it, because they are on every path that reaches it. A
word is unconditional only when every block open at the word is also open at
the run's launch: a flag in a branch that launches counts, while one in a
sibling block that has already closed does not.

A shape the scan cannot attribute -- a helper defined twice, a call reached
through a path or method, a name a `use` item could import (a glob import names
none but could bring any in), a receiver that is a field or a call, a parameter
or binding reassigned or shadowed -- is declined
rather than guessed, because a false positive would demand configuration from a
repository that needs none. The reading is textual: comments are blanked and
literal contents kept, and braces come from the masked source, so a `;` or a
`{` in a string does not move a statement boundary. A cargo command's
subcommand is the first word its arguments give that is neither an option nor
an option's value, so `cargo help build` reads help as the subcommand and
`build` as its topic. A conditional word cannot hide the subcommand on the
path that skips its block, so a `help` inside an `if` leaves the unconditional
`build` still compiling. `-h` or `--help` makes the command compile nothing at
all when it is given on every path: a help flag inside a block does not excuse
the build on the path that skips it. A `--print` query asks the compiler what
it is only when its two halves are adjacent: an unreadable word between
`--print` and `cfg` may be `link-args`, which builds the crate, so the query is
not read as a probe.

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
# A `return` ends the path it sits on, so what follows a returning block runs
# only when the return was skipped.
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

    A `let`, a loop variable, a closure parameter or a plain assignment to the
    command's name gives it a new value: the caller's command is no longer what
    the name denotes. A declaration only moves the name inside the block that
    declares it, while an assignment moves it for everything that follows,
    wherever it sits, because the block it runs in may have been entered. The
    assignment is therefore kept in its own group, which is how the reader tells
    the two apart. An unbound command has no name to move.

    Returns
    -------
    re.Pattern[str] | None
        The declaration, loop, closure or assignment pattern, or None when the
        command is not bound to a name.
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
        rf"|(?<![\w.])(?P<assign>{identifier})\s*=(?!=)"
    )


def _depth_at(masked_statement: str, at: int, start_depth: int) -> int:
    """Return the block depth the text at *at* is reached at.

    The block it sits in may have been opened by the statement itself or by an
    earlier statement (`if metadata {`), so the depth the statement starts at
    is counted with the braces that precede *at*. A launch and a `return` are
    both judged by the depth they sit at rather than by the depth their
    statement starts or ends at.

    Returns
    -------
    int
        The number of blocks still open at *at*.
    """
    opened = start_depth + masked_statement.count("{", 0, at)
    return opened - masked_statement.count("}", 0, at)


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


class Statement(typ.NamedTuple):
    """One statement that adds to the command, and the blocks around it.

    *text* is the statement as written, so a flag inside a string stays
    readable, and *masked* is the same with comments and literal bodies
    blanked. *blocks* holds the opening braces still open where the statement
    begins, identified by their offset in the window rather than by depth so
    that sibling blocks are told apart; *launch_blocks* is the same at the
    statement's launch, or None when it does not launch. *start_depth* is the
    number of blocks open where the statement begins.
    """

    text: str
    masked: str
    start: int
    start_depth: int
    blocks: tuple[int, ...]
    launch_blocks: tuple[int, ...] | None


class CommandRead(typ.NamedTuple):
    """The statements that add to one command, grouped by run.

    Attributes
    ----------
    statements:
        Each statement read for the command.
    starts:
        Where each run begins: the index in *statements* of its first statement
        of its own, and the depth of the branch that run leaves. A command
        launched on more than one path is read once per path, and each run
        restarts the command's argument list.
    """

    statements: list[Statement]
    starts: list[tuple[int, int]]

    @property
    def runs(self) -> list[list[Statement]]:
        """The statements grouped into the runs they describe.

        A run that leaves a branch (`else`) or a block that returned still
        inherits the arguments given before the branch, because they are on
        every path that reaches it. Those statements are the ones that begin
        and end above the branch's depth, so a conditional argument is not
        carried into a run it may not be part of.
        """
        runs: list[list[Statement]] = []
        bounds = [*self.starts, (len(self.statements), 0)]
        for (first, depth), (last, _next) in pairwise(bounds):
            if first >= last:
                continue
            shared = [
                statement
                for statement in self.statements[:first]
                if _entirely_above(statement, depth)
            ]
            runs.append([*shared, *self.statements[first:last]])
        return runs


def _entirely_above(statement: Statement, depth: int) -> bool:
    """Report whether a statement begins and ends above *depth*.

    A statement that opens or closes a block at *depth* belongs to a branch,
    so its arguments are not on every path through that branch and are not
    carried into the run that leaves it.

    Returns
    -------
    bool
        True when the statement is wholly above the branch.
    """
    start_depth = statement.start_depth
    end_depth = start_depth + statement.masked.count("{") - statement.masked.count("}")
    return start_depth < depth and end_depth < depth


class _StatementRead:
    """Accumulate a command's statements, its runs and the shadows over it.

    The read keeps five facts that only make sense together: the statements
    kept, where the runs begin, the blocks that shadow the command's name, the
    block depth and the launch boundary. They live in one object so each step
    of the read can be a small, named method rather than one long loop.

    A statement that moves the command's name to another value is not the
    command's, and neither is anything after it on that path: a declaration
    shadows the name inside its block, and attribution resumes once that block
    closes, while an assignment ends the read because the block it ran in may
    have been entered.

    Attributes
    ----------
    stopped:
        True once the read has reached a launch at the top level, or a binding
        that moves the name for good.
    """

    def __init__(self, window: Window, name: str | None) -> None:
        """Start reading *window* for the command bound to *name*."""
        self._window = window
        self._name = name
        self._rebinding = _rebinding(name)
        self._statements: list[Statement] = []
        self._starts: list[tuple[int, int]] = [(0, 0)]
        self._shadows: list[int] = []
        self._offset = 0
        self._depth = 0
        # The offsets of the opening braces still open, so a word can be
        # placed in the same block the launch is, not merely at its depth.
        self._blocks: list[int] = []
        # The depth of the block the last conditional launch ran in, while the
        # statements that follow it on that path are still being read.
        self._boundary: int | None = None
        # The depth a `return` was met at, until a statement is recorded after
        # it: what follows is the first of the path that skipped the return.
        self._pending: int | None = None
        self.stopped = False

    def feed(self, at: int) -> bool:
        """Read the statement ending at *at*, returning whether the read goes on.

        Returns
        -------
        bool
            False once the read has stopped, so the caller can leave the loop.
        """
        masked_statement = self._window.masked[self._offset : at]
        start_depth = self._depth
        self._depth += masked_statement.count("{") - masked_statement.count("}")
        blocks = tuple(self._blocks)
        _track(self._blocks, masked_statement, self._offset)
        moved = (
            self._rebinding.search(masked_statement)
            if self._offset > 0 and self._rebinding is not None
            else None
        )
        returned = _RETURN.search(masked_statement)
        if moved is not None:
            if self._depth == 0 or moved.group("assign") is not None:
                self.stopped = True
            else:
                self._shadows.append(self._depth)
        else:
            self._attribute(masked_statement, start_depth, blocks)
        if returned is not None:
            depth = _depth_at(masked_statement, returned.start(), start_depth)
            if depth > 0:
                # A `return` ends a path inside a block, so the statements
                # after the block are the first of the path that skipped it.
                # A `return` at the top level of a helper hands control back
                # to the caller, whose statements continue the same run.
                self._pending = depth
        while self._shadows and self._depth < self._shadows[-1]:
            self._shadows.pop()
        self._offset = at + 1
        return not self.stopped

    def _attribute(
        self, masked_statement: str, start_depth: int, blocks: tuple[int, ...]
    ) -> None:
        """Keep the statement if it is the command's, and begin its run."""
        # A statement that begins by closing a block is no longer inside the
        # shadow that block declared, so the shadow ends before the statement
        # is attributed. A trailing `}` still belongs to the shadowed block, so
        # the depth the statement ends at is not used.
        _end_shadows(self._shadows, masked_statement, start_depth)
        offset = self._offset
        end = offset + len(masked_statement)
        owned = not self._shadows and (
            offset == 0 or _owns_statement(self._window.masked, offset, end, self._name)
        )
        launch = (
            launch_at(self._window.masked, offset, end, self._name) if owned else None
        )
        if self._boundary is not None and not _starts_a_run(
            masked_statement,
            start_depth,
            None if launch is None else launch - offset,
            self._boundary,
        ):
            # The statement is still inside the block the command ran in, so
            # it is that path's code after the run rather than another run.
            return
        if self._boundary is not None:
            self._start(self._boundary)
            self._boundary = None
        if not owned:
            return
        if self._pending is not None:
            self._start(self._pending)
            self._pending = None
        launched_at = _record_statement(
            self._statements,
            self._window,
            (offset, end),
            launch,
            start_depth,
            blocks,
        )
        if launched_at == 0:
            self.stopped = True
        elif launched_at is not None:
            self._boundary = launched_at

    def _start(self, depth: int) -> None:
        """Begin a run at the next statement recorded, leaving branch *depth*."""
        if self._starts[-1][0] != len(self._statements):
            self._starts.append((len(self._statements), depth))

    def result(self) -> CommandRead:
        """Return the statements kept and where their runs begin."""
        return CommandRead(self._statements, self._starts)


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
    the run, so they are not read. A `return` inside a block ends its path in
    the same way, wherever the `return` was written, so a helper that returns
    early does not hide the caller's later run.

    A later declaration of the command's name shadows it inside that block, so
    a statement inside the block is not the command's, and attribution resumes
    after it; a declaration at the command's own level ends the read. An
    assignment to the name ends the read wherever it sits, because the block
    that ran it may have been entered.

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
    read = _StatementRead(window, _bound_name(text, start))
    for at, char in enumerate(window.masked + ";"):
        if char == ";" and not read.feed(at):
            break
    return read.result()


def _record_statement(
    statements: list[Statement],
    window: Window,
    span: tuple[int, int],
    launch: int | None,
    start_depth: int,
    blocks: tuple[int, ...],
) -> int | None:
    """Append one of the command's statements, ending it at its launch.

    An argument after the launch in the same statement is added to a reused
    command, so only the text before it is kept as the run's argument. The
    statement also keeps the blocks open at its start and at its launch, so a
    flag is placed in the block that launches, not merely at its depth.

    Returns
    -------
    int | None
        None when the statement does not launch the command, otherwise the
        block depth the launch is reached at. Zero means the launch is reached
        with no block open, which ends the read.
    """
    start, end = span
    stop = end if launch is None else launch
    launch_blocks = None if launch is None else list(blocks)
    if launch_blocks is not None:
        _track(launch_blocks, window.masked[start:stop], start)
    statements.append(
        Statement(
            blank_comments(window.text[start:stop]),
            window.masked[start:stop],
            start,
            start_depth,
            blocks,
            None if launch_blocks is None else tuple(launch_blocks),
        )
    )
    if launch is None:
        return None
    return _depth_at(window.masked[start:end], launch - start, start_depth)


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
    runs after the command has already run on that path. A statement that
    begins by closing the block is judged by the depth it reaches, not by the
    depth it starts at, because the arguments it adds are outside the block.

    Returns
    -------
    bool
        True when the statement is reached without the boundary block open.
    """
    if _ELSE.match(masked_statement) is not None:
        return True
    if launch is None:
        return _entry_depth(masked_statement, start_depth) < boundary
    return _depth_at(masked_statement, launch, start_depth) < boundary


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
        _asks_a_question(list(_probe_words(run, name))) for run in runs
    )


def _asks_a_question(words: list[str | None]) -> bool:
    """Report whether an argument sequence makes the compiler print and exit.

    A `--print` query may be one word (`--print=cfg`) or two (`--print cfg`),
    and the two forms ask the same question however they are split across
    calls. `--print` with any other word after it still compiles, and so does a
    query whose two halves are separated by an argument the reader cannot see
    (`.args(["--print", print_kind, "cfg"])`): the unseen word may be
    `link-args`, which builds the crate. A help flag makes the compiler print
    its help and exit, so it asks nothing either.

    Returns
    -------
    bool
        True when a version flag, a help flag or a `--print` query is among the
        words.
    """
    for index, word in enumerate(words):
        if word is None:
            continue
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


def _track(blocks: list[int], text: str, base: int) -> None:
    """Apply the braces in *text* to *blocks*, in place.

    Each opening brace is identified by its offset in the window, not its
    depth, so sibling blocks are told apart. *base* is the offset of *text*.
    """
    for index, char in enumerate(text):
        if char == "{":
            blocks.append(base + index)
        elif char == "}" and blocks:
            blocks.pop()


def _probe_words(run: list[Statement], name: str | None) -> typ.Iterator[str | None]:
    """Yield the arguments a run gives the command unconditionally.

    Every block open at the argument must also be open at the launch, so the
    branch that launches contributes its flags while a sibling block that
    closed earlier does not; comparing the opening offsets rather than their
    number is what tells the two apart. A word behind a `&&` or `||` short
    circuit may be skipped on the path that runs the command, so it is not
    yielded either. Braces are counted in the masked text, and another
    command's word is not this command's.

    Yields
    ------
    str | None
        The literal text of each unconditional argument, or None when it is
        not a literal the scan can read.
    """
    # A statement's text ends at its launch, so the blocks it records are the
    # ones the launch sits in. None means the run never launches.
    launch: tuple[int, ...] | None = None
    for statement in run:
        if statement.launch_blocks is not None:
            launch = statement.launch_blocks
            break
    if launch is None:
        return
    for statement in run:
        open_blocks = list(statement.blocks)
        seen = 0
        for found in _ARGUMENT_CALL.finditer(statement.masked):
            _track(
                open_blocks,
                statement.masked[seen : found.start()],
                statement.start + seen,
            )
            seen = found.start()
            if not owns_call(statement.masked, found.start(), name):
                continue
            before = statement.masked[: found.start()]
            if SHORT_CIRCUIT.search(before) is not None:
                continue
            if tuple(open_blocks) != launch[: len(open_blocks)]:
                continue
            open_at = found.end() - 1
            close = scan_balanced(statement.masked, open_at)
            if close is None:
                return
            yield from (
                item[1:-1] if _WORD.fullmatch(item) is not None else None
                for item in argument_items(statement.text[open_at + 1 : close - 1])
            )


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


def _run_compiles(run: list[Statement], name: str | None) -> bool:
    """Report whether one run of the command adds compiling work before it launches.

    A help flag makes cargo print its help and exit, so a run that is given one
    compiles nothing whatever its subcommand says. Only a help flag given on
    every path counts: one added inside a block is absent when that block does
    not run, and the run then compiles. Otherwise the subcommand, the first
    word that is neither an option nor an option's value, decides. A
    conditional word may change the subcommand on the path that runs its
    block, so the unconditional subcommand is read as well: a conditional
    `help` does not hide the `build` that runs when the block is skipped. A
    word the reader cannot see leaves the subcommand open, and then any
    compiling argument before the launch is enough.

    Returns
    -------
    bool
        True when the run starts compiling work.
    """
    words = _cargo_options(
        word
        for statement in run
        for word in _argument_words(statement.text, statement.masked, name)
    )
    unconditional = _cargo_options(_probe_words(run, name))
    if any(word in _HELP_FLAGS for word in unconditional):
        return False
    first = _first_positional(words)
    if first is not None:
        return first in _COMPILING or _first_positional(unconditional) in _COMPILING
    return any(
        owns_call(statement.masked, found.start(), name)
        for statement in run
        for found in COMPILES.finditer(statement.text)
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
