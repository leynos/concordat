"""Record where each run of the command begins, and what surrounds it.

:mod:`concordat.rules.nested_cargo_statements` decides whether a statement is
the command's at all. This module takes the statements that are and records
them: where the launch was reached, which blocks are open at the statement's
start and at that launch, and so which run the statement belongs to. A run
restarts the command's argument list, so the boundary between one run and the
next is what makes a word unconditional or conditional.

A block is identified by the offset of its opening brace rather than by its
depth, so two sibling blocks that both closed before the launch are told
apart. That identity also decides whether a closing brace reaches the launch's
branch or merely closes a nested block, so an `else` inside the launched block
is code after the run rather than another path. A statement that begins by
closing a block (`} command.arg("-v")`) is judged by the depth its first kept
character is reached at, so its argument is on every path that reaches it
rather than only inside the block it leaves.

A statement that opens the branch its launch runs in (`if cond {`, `} else {`)
is recorded as two: the text before the branch's brace, which runs on every
path that reaches the choice, and the arm itself. The condition's arguments are
then inherited by the run of every arm, as they are at run time, rather than
being attributed to whichever arm happened to be read first. The arm keeps the
blocks the launch was reached with, so a block the statement closed before the
branch (`} else {` closes the arm beside it) is not left open at the arm and
the branch's own brace is. When one arm of an `if`/`else` returns, the other
arm is the surviving path, and its statements join the run that launches.
"""

from __future__ import annotations

import typing as typ
from itertools import pairwise

from .rust_source import blank_comments

if typ.TYPE_CHECKING:
    from .nested_cargo_handoffs import Window


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


class Run(typ.NamedTuple):
    """One run of the command, and the arm whose return spares its path.

    *statements* are the statements that give the command its arguments before
    it launches. *survivor* is the brace of the arm every launching path
    entered because the arm beside it returned, or None when both arms can
    reach the launch; a word given inside that arm is then on every path, so
    its block is struck out before the words are read.
    """

    statements: list[Statement]
    survivor: int | None


class CommandRead(typ.NamedTuple):
    """The statements that add to one command, grouped by run.

    Attributes
    ----------
    statements:
        Each statement read for the command.
    starts:
        Where each run begins: the index in *statements* of its first statement
        of its own, the depth of the branch that run leaves, and the brace of
        the surviving arm when a return left only one arm reaching the launch.
        A command launched on more than one path is read once per path, and
        each run restarts the command's argument list.
    arms:
        The `else` arms the read saw, for deciding whether a choice covers
        every path.
    """

    statements: list[Statement]
    starts: list[tuple[int, int, int | None]]
    arms: ArmChoices

    @property
    def runs(self) -> list[Run]:
        """The statements grouped into the runs they describe.

        A run that leaves a branch (`else`) or a block that returned still
        inherits the arguments given before the branch, because they are on
        every path that reaches it. Those statements are the ones that begin
        and end above the branch's depth, so a conditional argument is not
        carried into a run it may not be part of. The arm whose return left
        the other arm as the only path to the launch is carried in whole: its
        statements are on every path that launches, so they join the run with
        their arm's brace named as the surviving one.
        """
        runs: list[Run] = []
        bounds = [*self.starts, (len(self.statements), 0, None)]
        for (first, depth, survivor), (last, _next, _) in pairwise(bounds):
            if first >= last:
                continue
            shared = [
                statement
                for statement in self.statements[:first]
                if _entirely_above(statement, depth)
                or (
                    survivor is not None
                    and (survivor in statement.blocks or _opens(statement, survivor))
                )
            ]
            runs.append(Run([*shared, *self.statements[first:last]], survivor))
        return runs


def _opens(statement: Statement, brace: int) -> bool:
    """Report whether the statement opens the block at *brace*.

    Returns
    -------
    bool
        True when the brace lies inside the statement and opens a block there.
    """
    index = brace - statement.start
    return 0 <= index < len(statement.masked) and statement.masked[index] == "{"


def _entry_depth(statement: Statement) -> int:
    """Return the depth the statement's first kept character sits at.

    A statement that begins by closing a block (`} command.arg("-v")`) starts
    at the depth inside that block but its text runs at the depth outside it.
    The leading braces are applied before anything is read, so the rest of the
    statement is judged by where it is reached rather than where its leading
    `}` was written.

    Returns
    -------
    int
        The number of blocks still open once the leading braces close.
    """
    leading = 0
    for char in statement.masked:
        if char.isspace():
            continue
        if char != "}":
            break
        leading += 1
    return max(statement.start_depth - leading, 0)


def _entirely_above(statement: Statement, depth: int) -> bool:
    """Report whether a statement begins and ends above *depth*.

    A statement that opens a block at *depth* belongs to a branch, so its
    arguments are not on every path through that branch and are not carried
    into the run that leaves it. The statement's entry depth is what tells the
    two apart: a statement that closes the branch's block begins inside it but
    is written outside, so its arguments are on every path that reaches it.

    Returns
    -------
    bool
        True when the statement is wholly above the branch.
    """
    end_depth = (
        statement.start_depth
        + statement.masked.count("{")
        - statement.masked.count("}")
    )
    return _entry_depth(statement) < depth and end_depth < depth


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
        # The arm's own blocks are the ones the launch was reached with: the
        # blocks open at the branch are those before the branch's brace, and
        # the launch sits inside the branch itself. Tracking the arm's text
        # again from the statement's start would lose the blocks the statement
        # closed or opened before the branch (`} else {` closes a block the
        # launch is not in).
        _append_statement(
            statements,
            window,
            (branch, stop),
            start_depth,
            tuple(launch_blocks[:-1]),
            tuple(launch_blocks),
        )
        return _Launch(branch, depth)
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


def _block_at(
    masked_statement: str, at: int, blocks: tuple[int, ...], offset: int
) -> int | None:
    """Return the innermost block open at *at*, or None at the top level.

    A `return` names the block it sits in, so the arm it ends is known by the
    brace that opened it rather than by a depth two arms can share.

    Returns
    -------
    int | None
        The offset of the innermost open brace, or None when none is open.
    """
    open_blocks = list(blocks)
    _track(open_blocks, masked_statement[:at], offset)
    return open_blocks[-1] if open_blocks else None
