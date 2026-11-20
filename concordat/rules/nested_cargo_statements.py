"""Read the statements that add to one command.

The nested-cargo scan finds a spawn, then asks what the statements around it
add to that command. This module decides which statements are the command's,
and how a path through a choice is followed; where each run begins and which
blocks surround each word is recorded in
:mod:`concordat.rules.nested_cargo_runs`, and what the words mean is decided in
:mod:`concordat.rules.nested_cargo_arguments`.

A launch reached at the top level ends the read, so an argument added to a
reused command does not count as the first run's work; a launch inside a block
ends only that branch, and a `return` inside a block ends its path, so the
command is read once for each path it runs on.

A statement that moves the command's name to another value is not the
command's, and neither is anything after it on that path: a declaration
shadows the name inside its block, and attribution resumes once that block
closes, while an assignment ends the read because the block that ran it may
have been entered. A field assignment (`holder . command = ..`) moves the
field rather than the binding, so it does not end the read. A closure parameter
of that name is different again: it shadows the command only inside the closure
body, so the closure's own span is blanked before the statement is read and the
rest of the statement is still the command's, wherever in the `|..|` list the
parameter sits. A launch beside the closure is therefore read
(`(|command: &mut Command| {}, command.arg("build").status())`), while a launch
on the closure's own parameter is not. The body outlives a `;` written inside
it, and the statement reader splits there, so the span is found in the text
before the statement rather than in the statement alone; the statements after
the body closes are the command's again.

The identity of the open braces pairs an `else` arm with the `if` arm beside
it -- inline (`let _ = if c { .. } else { .. };`) as well as across statements
-- which is what lets a probe given in every arm cover both paths. When a
`return` ends one arm of an `if`/`else`, the other arm is the only path that
reaches the launch, so that arm's statements are read as given on every path.
An `else if` is a condition of its own rather than the pair's second arm, so
the arm after it is not the only path and its words are not read as
unconditional.
A `;` inside a block argument of the command's own call does not end the
statement, so the chain's launch is still read
(`command.arg({ let flag = "--quiet"; flag }).status()`).

:mod:`concordat.rules.nested_cargo_handoffs` finds the window and splices in a
helper's body; this module reads the statements that window holds.
"""

from __future__ import annotations

import re
import typing as typ

from .nested_cargo_handoffs import Window, _bound_name
from .nested_cargo_receivers import (
    call_chains,
    launch_at,
    owns_chain,
    reaches_through,
)
from .nested_cargo_runs import (
    ArmChoices,
    CommandRead,
    Statement,
    _block_at,
    _depth_at,
    _record_statement,
    _starts_a_run,
    _track,
)
from .nested_cargo_syntax import closure_scopes
from .nested_cargo_words import _ARGUMENT_CALL

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

    A `let`, a loop variable or a plain assignment to the command's name gives
    it a new value: the caller's command is no longer what the name denotes. A
    binding that borrows the value (`let ref command = ..`) denotes the
    borrowed value, so it moves the name in the same way. A declaration only
    moves the name inside the block that declares it, while an assignment moves
    it for everything that follows, wherever it sits, because the block it runs
    in may have been entered. The assignment is therefore kept in its own
    group, which is how the reader tells the two apart. An unbound command has
    no name to move.

    A closure's own parameter is not read here: it shadows the name only
    inside the closure body, and :meth:`_StatementRead.feed` blanks that span
    before the statement is read, so the pattern never sees it and the name is
    not moved for everything after it.

    Returns
    -------
    re.Pattern[str] | None
        The declaration, loop or assignment pattern, or None when the command
        is not bound to a name.
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


def _closure_hiding(
    masked: str, name: str, start: int, stop: int
) -> list[tuple[int, int]]:
    """Return the closure spans around *name*, rebased onto a statement.

    The spans are found in *masked*, the whole read so far, rather than in the
    statement alone. A closure's body outlives a `;` written inside it
    (`|command: &mut Command| { let _ = 1; command.arg("--version"); }`) and
    the statement reader splits there, so the statement after the `;` holds no
    header of its own. Reading the spans over everything before the statement
    keeps the later half of the body hidden, while a statement after the body
    closes is not hidden at all. A body still open at *stop* hides the rest of
    the statement, because the closure is not finished.

    Parameters
    ----------
    masked:
        The masked source up to the end of the current statement.
    name:
        The command's name, as bound at the spawn.
    start:
        The offset where the current statement begins.
    stop:
        The offset where the current statement ends.

    Returns
    -------
    list[tuple[int, int]]
        The part of each span that lies inside the statement, relative to
        *start*, in source order.
    """
    hidden: list[tuple[int, int]] = []
    for begin, end in closure_scopes(masked, name):
        if end <= start or begin >= stop:
            continue
        hidden.append((max(begin, start) - start, min(end, stop) - start))
    return hidden


def _blank_spans(masked: str, spans: list[tuple[int, int]]) -> str:
    """Return *masked* with every span in *spans* replaced by spaces.

    The text keeps its length, so an offset taken from it still names the same
    character in the window.

    Returns
    -------
    str
        The masked statement with the spans blanked.
    """
    if not spans:
        return masked
    blanked = list(masked)
    for start, stop in spans:
        blanked[start:stop] = " " * (stop - start)
    return "".join(blanked)


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
        self._starts: list[tuple[int, int, int | None]] = [(0, 0, None)]
        self._shadows: list[int] = []
        self._offset = 0
        self._depth = 0
        # The offsets of the opening braces still open, so a word can be
        # placed in the same block the launch is, not merely at its depth.
        self._blocks: list[int] = []
        # The block the last conditional launch ran in and the depth it sat
        # at, while the statements that follow it on that path are read.
        self._boundary: tuple[int, int] | None = None
        # The depth and block a `return` was met at, until a statement is
        # recorded after it: what follows is the first of the path that
        # skipped the return.
        self._pending: tuple[int, int | None] | None = None
        # The blocks a `return` was met in, so an arm that returns is not read
        # as an arm that reaches the launch.
        self._returns: set[int] = set()
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
        # A closure's own parameter names its value, not the caller's command,
        # but only inside the closure: a call beside the closure in the same
        # statement is still the caller's. The closure's header and body are
        # therefore blanked rather than the whole statement being declined.
        # The spans are read over the whole text before this statement, since
        # a `;` inside the body ends the statement and leaves the rest of the
        # body in a later one.
        scopes = (
            _closure_hiding(self._window.masked[:at], self._name, self._offset, at)
            if self._name is not None
            else []
        )
        read_masked = _blank_spans(masked_statement, scopes)
        read_window = (
            self._window
            if not scopes
            else Window(
                self._window.text,
                self._window.masked[: self._offset]
                + read_masked
                + self._window.masked[at:],
            )
        )
        moved = (
            _moved(self._rebinding, read_masked)
            if self._offset > 0 and self._rebinding is not None
            else None
        )
        returned = _RETURN.search(read_masked)
        if moved is not None:
            if self._depth == 0 or moved.group("assign") is not None:
                self.stopped = True
            else:
                self._shadows.append(self._depth)
        else:
            self._attribute(read_masked, start_depth, blocks, read_window)
        if returned is not None:
            depth = _depth_at(read_masked, returned.start(), start_depth)
            if depth > 0:
                # A `return` ends a path inside a block, so the statements
                # after the block are the first of the path that skipped it.
                # A `return` at the top level of a helper hands control back
                # to the caller, whose statements continue the same run.
                brace = _block_at(read_masked, returned.start(), blocks, self._offset)
                self._pending = (depth, brace)
                if brace is not None:
                    self._returns.add(brace)
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
        open_blocks = list(blocks)
        seen = 0
        for found in _ELSE.finditer(masked_statement):
            # The braces before the arm's closer are walked once for the whole
            # statement rather than once per `else`, so a statement that holds
            # many short `if`/`else` expressions is still read in one pass.
            for index in range(seen, found.start()):
                char = masked_statement[index]
                if char == "{":
                    open_blocks.append(self._offset + index)
                elif char == "}" and open_blocks:
                    open_blocks.pop()
            seen = found.start() + 1
            if not open_blocks:
                continue
            closed = open_blocks.pop()
            brace = masked_statement.find("{", found.end())
            if brace < 0:
                continue
            self._arms[closed] = self._offset + brace
            if not masked_statement[found.end() :].lstrip().startswith("if"):
                self._exhaustive.add(closed)

    def _attribute(
        self,
        masked_statement: str,
        start_depth: int,
        blocks: tuple[int, ...],
        read_window: Window,
    ) -> None:
        """Keep the statement if it is the command's, and begin its run.

        *read_window* is the window as the statement should be read, with any
        closure that binds the command's name blanked out of it.
        """
        # A statement that begins by closing a block is no longer inside the
        # shadow that block declared, so the shadow ends before the statement
        # is attributed. A trailing `}` still belongs to the shadowed block, so
        # the depth the statement ends at is not used.
        _end_shadows(self._shadows, masked_statement, start_depth)
        offset = self._offset
        end = offset + len(masked_statement)
        owned = not self._shadows and (
            offset == 0 or _owns_statement(read_window.masked, offset, end, self._name)
        )
        launch = (
            launch_at(read_window.masked, offset, end, self._name) if owned else None
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
            depth, brace = self._pending
            self._start(depth, self._survivor(brace))
            self._pending = None
        launched = _record_statement(
            self._statements,
            read_window,
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

    def _survivor(self, brace: int | None) -> int | None:
        """Return the arm that is the only path to the launch, or None.

        A `return` inside one arm of an `if`/`else` leaves the other arm as the
        only path reaching the launch, so the words given there are given on
        every path that runs the command. The arm is the only path, though,
        only when the `else` beside it carries no condition: an `else if` has a
        path of its own beyond the pair, and the arm after it is not reached
        when that condition is false. When that other arm returns as well, the
        choice before it is walked in turn, so a chain of returning arms
        narrows to the arm that does launch; every step of the walk takes the
        returning arm out of the paths, so only the pair the surviving arm is
        promoted from must cover both.

        Returns
        -------
        int | None
            The brace of the surviving arm, or None when both arms can launch.
        """
        seen: set[int] = set()
        while brace is not None and brace not in seen:
            seen.add(brace)
            paired = self._paired(brace)
            if paired is None:
                return None
            if paired not in self._returns:
                return paired if self._pair_is_exhaustive(brace, paired) else None
            brace = paired
        return None

    def _pair_is_exhaustive(self, brace: int, paired: int) -> bool:
        """Report whether the choice between *brace* and *paired* has no other path.

        An `if` arm of an `if`/`else` chain is the key of the pair, and only a
        pair whose `else` carries no condition is exhaustive. A pair whose
        `else` is another `if` leaves the path beyond that arm open.

        Returns
        -------
        bool
            True when every path through the choice enters one of the two arms.
        """
        key = brace if brace in self._arms else paired
        return key in self._exhaustive

    def _paired(self, brace: int) -> int | None:
        """Return the arm paired with *brace*, on either side of its `else`.

        Returns
        -------
        int | None
            The sibling arm's brace, or None when *brace* has no `else` pair.
        """
        if brace in self._arms:
            return self._arms[brace]
        for closed, other in self._arms.items():
            if other == brace:
                return closed
        return None

    def _start(self, depth: int, survivor: int | None = None) -> None:
        """Begin a run at the next statement recorded, leaving branch *depth*.

        *survivor* names the arm every launching path entered, when a return
        closed the arm beside it.
        """
        if self._starts[-1][0] != len(self._statements):
            self._starts.append((len(self._statements), depth, survivor))

    def result(self) -> CommandRead:
        """Return the statements kept and where their runs begin."""
        return CommandRead(
            self._statements,
            self._starts,
            ArmChoices(dict(self._arms), frozenset(self._exhaustive)),
        )


def _statement_ends(masked: str) -> typ.Iterator[int]:
    """Yield the offset of each `;` that ends a statement.

    A `;` inside a block given to one of the command's own argument calls does
    not end the statement, because the chain continues after the block closes
    (`command.arg({ let flag = "--quiet"; flag }).status()`). Ending there
    would leave the rest of the chain with no receiver, and so lose the launch
    and the run it begins. Every other `;` ends a statement, whether or not it
    sits inside parentheses, so a spliced helper's body splits as it would on
    its own.

    Yields
    ------
    int
        The offset of a `;` that ends a statement, in source order.
    """
    argument_parens = {found.end() - 1 for found in _ARGUMENT_CALL.finditer(masked)}
    enclosing: list[bool] = []
    blocks = 0
    for at, char in enumerate(masked):
        if char == "(":
            enclosing.append(at in argument_parens)
        elif char == "[":
            enclosing.append(False)
        elif char in ")]":
            if enclosing:
                enclosing.pop()
        elif char == "{":
            blocks += 1
        elif char == "}":
            blocks = max(blocks - 1, 0)
        elif char == ";" and not (blocks and any(enclosing)):
            yield at


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
    literals as written, so a flag in a string can still be read. A `;` inside
    a block argument of one of the command's calls does not end the statement,
    so the chain's launch is still read; any other `;` ends it. The depth a
    statement starts at is counted from its whole text, so a block that closes
    within it does not lift the statements after it.

    Returns
    -------
    CommandRead
        The statements, grouped by the run they belong to.
    """
    read = _StatementRead(window, _bound_name(text, start))
    for at in [*_statement_ends(window.masked), len(window.masked)]:
        if not read.feed(at):
            break
    return read.result()
