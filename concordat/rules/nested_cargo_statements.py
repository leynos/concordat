"""Read the statements that add to one command, and the runs they form.

The nested-cargo scan finds a spawn, then asks what the statements around it
add to that command. This module decides which statements are the command's,
where each run begins and which blocks surround each word; what the words mean
is decided in :mod:`concordat.rules.nested_cargo_arguments`.

A launch reached at the top level ends the read, so an argument added to a
reused command does not count as the first run's work; a launch inside a block
ends only that branch, and a `return` inside a block ends its path, so the
command is read once for each path it runs on. A run that leaves a branch
inherits the arguments given before it, because they are on every path that
reaches it; a statement that opens the branch its launch runs in is therefore
recorded in two, so the arguments its condition adds belong to every run
through the branch. A word is unconditional only when every block open at the
word is also open at the run's launch: a flag in the branch that launches
counts, while one in a sibling block that has already closed does not.

A block is identified by the offset of its opening brace rather than by its
depth, so two sibling blocks that both closed before the launch are told
apart. That identity also decides whether a closing brace reaches the launch's
branch or merely closes a nested block, so an `else` inside the launched block
is code after the run rather than another path. The same identity pairs an
`else` arm with the `if` arm beside it -- inline (`let _ = if c { .. } else
{ .. };`) as well as across statements -- which is what lets a probe given in
every arm cover both paths.

A statement that moves the command's name to another value is not the
command's, and neither is anything after it on that path: a declaration
shadows the name inside its block, and attribution resumes once that block
closes, while an assignment ends the read because the block that ran it may
have been entered. A field assignment (`holder . command = ..`) moves the
field rather than the binding, so it does not end the read.

:mod:`concordat.rules.nested_cargo_handoffs` finds the window and splices in a
helper's body; this module reads the statements that window holds.
"""

from __future__ import annotations

import re
import typing as typ
from itertools import pairwise

from .nested_cargo_handoffs import Window, _bound_name
from .nested_cargo_receivers import (
    call_chains,
    launch_at,
    owns_chain,
    reaches_through,
)
from .rust_source import blank_comments

# An `else` arm is the other path through the `if` beside it. It may open the
# statement (`} else {`) or sit inside one (`let _ = if c { .. } else { .. };`),
# so the `}` that closes the arm is what identifies it, not the statement start.
_ELSE: typ.Final = re.compile(r"\}\s*else\b")
# A `return` ends the path it sits on, so what follows a returning block runs
# only when the return was skipped.
_RETURN: typ.Final = re.compile(r"\breturn\b")


def _owns_statement(masked: str, start: int, end: int, name: str | None) -> bool:
    """Report whether the statement from *start* to *end* calls the command.

    A statement may mention another command first
    (`(other.get_program(), command.arg(..))`), so every chain in it is
    checked rather than only the one it opens with: the second element still
    runs. A statement that only calls another receiver owns none of them.

    Returns
    -------
    bool
        True when one of the statement's own chains is called on the command.
    """
    return any(owns_chain(chain, name) for chain in call_chains(masked, start, end))


def _rebinding(name: str | None) -> re.Pattern[str] | None:
    """Return the pattern that moves *name* to another value, if it is bound.

    A `let`, a loop variable, a closure parameter or a plain assignment to the
    command's name gives it a new value: the caller's command is no longer what
    the name denotes. A binding that borrows the value (`let ref command = ..`)
    denotes the borrowed value, so it moves the name in the same way. A
    declaration only moves the name inside the block that declares it, while an
    assignment moves it for everything that follows,
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
    # `ref` and `mut` may each be present before the name a pattern binds, and
    # neither changes which name is bound.
    binding = r"(?:(?:ref\s+)?mut\s+|ref\s+)?"
    return re.compile(
        rf"\blet\s+{binding}{identifier}\b"
        rf"|\bfor\s+{binding}{identifier}\b"
        rf"|\|\s*{binding}{identifier}\s*(?:\||:)"
        # The name must be whole, so `my_command = ..` and `holder.command =
        # ..` move their own value rather than the command's.
        rf"|(?<![\w.])(?P<assign>{identifier})\s*=(?!=)"
    )


def _moved(pattern: re.Pattern[str], masked_statement: str) -> re.Match[str] | None:
    """Return the first binding in the statement that moves the command's name.

    Rust allows whitespace around a field's dot, so `holder . command = ..`
    reaches the same field as `holder.command = ..`. An assignment reached
    through a separator moves the field, not the binding, so it is skipped.
    A declaration or a loop variable is never reached through a separator and
    is returned as it is.

    Returns
    -------
    re.Match[str] | None
        The binding that moves the name, or None when the statement moves
        nothing of the command's.
    """
    for found in pattern.finditer(masked_statement):
        assignment = found.group("assign") is not None
        if assignment and reaches_through(masked_statement, found.start()):
            continue
        return found
    return None


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


class ArmChoices(typ.NamedTuple):
    """The `else` arms the read saw, so a choice between paths can be walked.

    *next_arm* maps the offset of an arm's opening brace to the brace of the
    `else` arm beside it, and *exhaustive* holds the arms whose `else` carries
    no condition, which is what makes the chain cover both paths.
    """

    next_arm: dict[int, int]
    exhaustive: frozenset[int]


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
    arms:
        The `else` arms the read saw, for deciding whether a choice covers
        every path.
    """

    statements: list[Statement]
    starts: list[tuple[int, int]]
    arms: ArmChoices

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
        # The block the last conditional launch ran in and the depth it sat
        # at, while the statements that follow it on that path are read.
        self._boundary: tuple[int, int] | None = None
        # The depth a `return` was met at, until a statement is recorded after
        # it: what follows is the first of the path that skipped the return.
        self._pending: int | None = None
        # The `else` arms seen, keyed by the brace that opens each arm.
        self._arms: dict[int, int] = {}
        self._exhaustive: set[int] = set()
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
        self._note_else(masked_statement, blocks)
        _track(self._blocks, masked_statement, self._offset)
        moved = (
            _moved(self._rebinding, masked_statement)
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

    def _note_else(self, masked_statement: str, blocks: tuple[int, ...]) -> None:
        """Record the `else` arms a statement holds, wherever they are written.

        An arm is identified by the brace that opens it, so a chain of `else
        if` arms can be walked from the first `if` to an unconditional `else`.
        The arm that closed is found by applying the statement's braces to the
        blocks already open, which pairs an arm opened by an earlier statement
        (`} else {`) and one opened in this statement (an inline `if`/`else`
        expression) alike. Only a pair whose `else` has no condition covers
        both paths.
        """
        for found in _ELSE.finditer(masked_statement):
            open_blocks = list(blocks)
            closed: int | None = None
            for index, char in enumerate(masked_statement[: found.start() + 1]):
                if char == "{":
                    open_blocks.append(self._offset + index)
                elif char == "}" and open_blocks:
                    closed = open_blocks.pop()
            if closed is None:
                continue
            brace = masked_statement.find("{", found.end())
            if brace < 0:
                continue
            self._arms[closed] = self._offset + brace
            if not masked_statement[found.end() :].lstrip().startswith("if"):
                self._exhaustive.add(closed)

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
            blocks,
            None if launch is None else launch - offset,
            self._boundary,
        ):
            # The statement is still inside the block the command ran in, so
            # it is that path's code after the run rather than another run.
            return
        if self._boundary is not None:
            self._start(self._boundary[1])
            self._boundary = None
        if not owned:
            return
        if self._pending is not None:
            self._start(self._pending)
            self._pending = None
        launched = _record_statement(
            self._statements,
            self._window,
            (offset, end),
            launch,
            start_depth,
            blocks,
        )
        if launched is None:
            return
        if launched.block is None:
            # No block was open at the launch, so the command is a reuse for
            # everything after it and the read ends here.
            self.stopped = True
            return
        self._boundary = (launched.block, launched.depth)

    def _start(self, depth: int) -> None:
        """Begin a run at the next statement recorded, leaving branch *depth*."""
        if self._starts[-1][0] != len(self._statements):
            self._starts.append((len(self._statements), depth))

    def result(self) -> CommandRead:
        """Return the statements kept and where their runs begin."""
        return CommandRead(
            self._statements,
            self._starts,
            ArmChoices(dict(self._arms), frozenset(self._exhaustive)),
        )


def own_statements(text: str, start: int, window: Window) -> CommandRead:
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
    the run, so they are not read; a launch in a sibling block that has
    already closed is another path and begins a run of its own. A `return`
    inside a block ends its path in the same way, wherever the `return` was
    written, so a helper that returns early does not hide the caller's later
    run.

    A later declaration of the command's name shadows it inside that block, so
    a statement inside the block is not the command's, and attribution resumes
    after it; a declaration at the command's own level ends the read. An
    assignment to the name ends the read wherever it sits, because the block
    that ran it may have been entered; an assignment to a field of the same
    name moves the field instead.

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


class _Launch(typ.NamedTuple):
    """Where a statement launches the command.

    *block* is the opening brace of the block the launch ran in, or None when
    no block was open, which ends the read: every later statement is a reuse
    and no path continues past it. *depth* is the number of blocks open at the
    launch.
    """

    block: int | None
    depth: int


def _append_statement(
    statements: list[Statement],
    window: Window,
    span: tuple[int, int],
    start_depth: int,
    blocks: tuple[int, ...],
    launch_blocks: tuple[int, ...] | None,
) -> None:
    """Append one of the command's statements, with its blocks around it.

    The statement is kept as written, so a flag inside a string stays readable,
    and masked, so its braces and the calls on other receivers are read from
    the same offsets. *launch_blocks* is the blocks open at the statement's
    launch, or None when it does not launch.
    """
    start, stop = span
    statements.append(
        Statement(
            blank_comments(window.text[start:stop]),
            window.masked[start:stop],
            start,
            start_depth,
            blocks,
            launch_blocks,
        )
    )


def _record_statement(
    statements: list[Statement],
    window: Window,
    span: tuple[int, int],
    launch: int | None,
    start_depth: int,
    blocks: tuple[int, ...],
) -> _Launch | None:
    """Append one of the command's statements, ending it at its launch.

    An argument after the launch in the same statement is added to a reused
    command, so only the text before it is kept as the run's argument. The
    statement also keeps the blocks open at its start and at its launch, so a
    flag is placed in the block that launches, not merely at its depth.

    A statement that opens the branch its launch runs in (`if cond {`, `} else
    {`) is recorded as two: the text before the branch's brace, which runs on
    every path that reaches the choice, and the arm itself. The condition's
    arguments are then inherited by the run of every arm, as they are at run
    time, rather than being attributed to whichever arm happened to be read
    first.

    Returns
    -------
    _Launch | None
        None when the statement does not launch the command, otherwise where
        the launch was reached.
    """
    start, end = span
    stop = end if launch is None else launch
    if launch is None:
        _append_statement(statements, window, (start, stop), start_depth, blocks, None)
        return None
    launch_blocks = list(blocks)
    _track(launch_blocks, window.masked[start:stop], start)
    branch = launch_blocks[-1] if launch_blocks else None
    depth = _depth_at(window.masked[start:end], launch - start, start_depth)
    if branch is None:
        _append_statement(statements, window, (start, stop), start_depth, blocks, ())
        return _Launch(None, depth)
    if start <= branch < stop:
        # The statement opened the branch, so its condition is above the
        # branch and belongs to every run through it.
        _append_statement(
            statements, window, (start, branch), start_depth, blocks, None
        )
        arm_blocks = list(blocks)
        _track(arm_blocks, window.masked[branch:stop], branch)
        _append_statement(
            statements,
            window,
            (branch, stop),
            start_depth,
            blocks,
            tuple(arm_blocks),
        )
        return _Launch(arm_blocks[-1] if arm_blocks else None, depth)
    _append_statement(
        statements, window, (start, stop), start_depth, blocks, tuple(launch_blocks)
    )
    return _Launch(branch, depth)


def _starts_a_run(
    masked_statement: str,
    blocks: tuple[int, ...],
    launch: int | None,
    boundary: tuple[int, int],
) -> bool:
    """Report whether a statement begins the next run of the command.

    A statement that reaches the top level again (`}` closing the block the
    launch sat in, or the `else` arm beside it) is on another path, so its
    arguments are a run of their own. A statement still inside the launched
    block is code that runs after the command has already run on that path,
    and an `else` arm nested inside the launched block is the same: it closes
    an inner `if` the run is not in, so only the boundary's identity decides,
    not the word `else`. The blocks are compared by the identity of their
    opening brace, so a sibling block that sits at the same depth as the
    launch's block is another path, while a nested block inside it is not.

    Returns
    -------
    bool
        True when the statement is reached without the boundary block open.
    """
    block, _depth = boundary
    if launch is None:
        return block not in _entry_blocks(blocks, masked_statement)
    return block not in _blocks_at(blocks, masked_statement, launch)


def _entry_blocks(blocks: tuple[int, ...], masked_statement: str) -> list[int]:
    """Return the blocks open once the statement's leading braces close."""
    open_blocks = list(blocks)
    for char in masked_statement:
        if char.isspace():
            continue
        if char != "}":
            break
        if open_blocks:
            open_blocks.pop()
    return open_blocks


def _blocks_at(blocks: tuple[int, ...], masked_statement: str, at: int) -> list[int]:
    """Return the blocks open where *at* sits in *masked_statement*.

    Only membership is asked of the result, so a brace the statement opens is
    pushed as a placeholder: it is never the boundary block, whose identity is
    an offset in the window.

    Returns
    -------
    list[int]
        The blocks open at *at*, the boundary included when it is still open.
    """
    open_blocks = list(blocks)
    for char in masked_statement[:at]:
        if char == "{":
            open_blocks.append(-1)
        elif char == "}" and open_blocks:
            open_blocks.pop()
    return open_blocks


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
