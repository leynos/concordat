"""Follow the shell control flow that decides whether a gate command runs.

A gate command inside `if false; then ...; fi` never runs, and one in the
`else` of an `if true` never runs either, so counting them as coverage reads a
gate as stronger than it is. This module walks a script's simple commands, one
lexed line at a time, and keeps those that can run. It knows two constant
conditions, `true` (or `:`) and `false`; any other condition is taken to hold
on some run, as a workflow step's `if` is, so both of its branches count as
able to run.

A script spans lines, and a condition can open on one and close on another, so
the state it leaves (which branches enclose the next command, and whether a
`cd` has left the root) is a value the caller carries from line to line.
"""

from __future__ import annotations

import typing as typ

__all__ = ["FRESH_SHELL", "Branch", "Kept", "ShellState", "merge_states", "runnable"]


class Branch(typ.NamedTuple):
    """The `if` branch the shell is in.

    `taken` is true when an earlier branch of the same `if` is known to run, so
    later ones do not. `active` is true when this branch can run. `uid` names an
    `if` whose condition is not constant (``-1`` for a constant one), so the
    commands of its branches can be told apart from the commands around it.
    """

    taken: bool
    active: bool
    uid: int = -1


class ShellState(typ.NamedTuple):
    """What an earlier line of one shell leaves for the lines after it.

    `moved` is true once a `cd` has left the root, or the step's working
    directory is not the root. `branches` holds the `if` branches that enclose
    the next command, outermost first. `forks` holds the `uid` of every
    non-constant `if` that has an `elif` or `else`, whose branches are
    alternatives: one run takes one. `count` numbers the non-constant `if`s.
    """

    moved: bool = False
    branches: tuple[Branch, ...] = ()
    forks: frozenset[int] = frozenset()
    count: int = 0


FRESH_SHELL: typ.Final = ShellState()

type Kept = tuple[list[str], tuple[int, ...]]
type _Advance = tuple[ShellState, list[str] | None]


def _truth(condition: list[str]) -> bool | None:
    """Return the constant a condition is, or ``None`` when it is not constant."""
    match tuple(condition):
        case ("true",) | (":",):
            return True
        case ("false",):
            return False
        case _:
            return None


def _is_live(branches: tuple[Branch, ...]) -> bool:
    """Return whether every enclosing branch can run."""
    return all(branch.active for branch in branches)


def _frames(branches: tuple[Branch, ...]) -> tuple[int, ...]:
    """Return the `uid` of every enclosing non-constant `if`."""
    return tuple(branch.uid for branch in branches if branch.uid >= 0)


def _open(state: ShellState, words: list[str]) -> _Advance:
    """Enter an `if`: its condition is a command unless it is constant."""
    verdict = _truth(words[1:])
    if verdict is not None:
        branch = Branch(verdict is True, verdict is True)
        return state._replace(branches=(*state.branches, branch)), None
    branch = Branch(taken=False, active=True, uid=state.count)
    entered = state._replace(branches=(*state.branches, branch))._replace(
        count=state.count + 1
    )
    return entered, words if _is_live(state.branches) else None


def _next_test(state: ShellState, words: list[str]) -> _Advance:
    """Move to an `elif`: it is not tested when an earlier branch is known to run."""
    if not state.branches:
        return state, None
    *outer, current = state.branches
    forks = state.forks | {current.uid} if current.uid >= 0 else state.forks
    if current.taken:
        skipped = Branch(taken=True, active=False, uid=current.uid)
        return state._replace(branches=(*outer, skipped))._replace(forks=forks), None
    verdict = _truth(words[1:])
    uid, count = current.uid, state.count
    if verdict is None and uid < 0:
        # An unknown test that follows a constant one is a condition of its own;
        # it is a fork only if an `elif` or `else` follows it.
        uid, count = count, count + 1
    branch = Branch(verdict is True, verdict is not False, uid)
    moved = state._replace(branches=(*outer, branch), forks=forks, count=count)
    runs = verdict is None and _is_live(moved.branches)
    return moved, words if runs else None


def _otherwise(state: ShellState, words: list[str]) -> _Advance:
    """Move to an `else`: it runs unless an earlier branch is known to have."""
    if not state.branches:
        return state, words[1:] or None
    *outer, current = state.branches
    forks = state.forks | {current.uid} if current.uid >= 0 else state.forks
    branch = Branch(current.taken, not current.taken, current.uid)
    moved = state._replace(branches=(*outer, branch))._replace(forks=forks)
    return moved, (words[1:] or None) if _is_live(moved.branches) else None


def _close(state: ShellState, _words: list[str]) -> _Advance:
    """Leave an `if` at its `fi`."""
    return state._replace(branches=state.branches[:-1]), None


def _body(state: ShellState, words: list[str]) -> _Advance:
    """Enter a `then` or `do` body: what follows the keyword is a command."""
    rest = words[1:]
    return state, rest if rest and _is_live(state.branches) else None


_KEYWORDS: typ.Final = {
    "if": _open,
    "elif": _next_test,
    "else": _otherwise,
    "fi": _close,
    "then": _body,
    "do": _body,
}


def _advance(state: ShellState, words: list[str]) -> _Advance:
    """Return the state after one simple command, and the command if it runs."""
    head = words[0].lstrip("@-+") if words else ""
    handler = _KEYWORDS.get(head)
    if handler is not None:
        return handler(state, words)
    return state, words if _is_live(state.branches) else None


def runnable(
    segments: list[list[str]], state: ShellState
) -> tuple[list[Kept], ShellState]:
    """Return the simple commands of a line that can run, and the shell after it.

    Each command comes with the `uid` of every non-constant `if` around it (an
    `if` or `elif` test is framed by what precedes it, not the branch it opens), so a
    caller can mark the commands of an `if` that turns out to have alternative
    branches (see `ShellState.forks`). *state* is the shell at the line's start.

    Returns
    -------
    tuple[list[Kept], ShellState]
        The commands that can run with their enclosing `if`s, and the state the
        next line starts from.
    """
    kept: list[Kept] = []
    for words in segments:
        before = state.branches
        state, command = _advance(state, words)
        if command is not None:
            # The test of an `if` or `elif` runs before the branch it opens, and
            # only when no earlier branch did: it is framed by what came before.
            is_test = words[0].lstrip("@-+") in {"if", "elif"}
            kept.append((command, _frames(before if is_test else state.branches)))
    return kept, state


def merge_states(states: list[ShellState]) -> ShellState:
    """Return the state a line starts in when it follows several alternatives.

    One earlier reading leaves one state. Several are alternatives of probes the
    next line's own readings need not correspond to (the two probes may test
    different tools), so the next line starts from all of them: moved if any
    moved, with each open `if` block kept when the states agree on it, so a
    branch known not to run stays skipped, and a fork when they disagree.

    Returns
    -------
    ShellState
        The merged state.
    """
    if len(states) <= 1:
        return states[0] if states else FRESH_SHELL
    count = max(state.count for state in states)
    branches, forks = _merge_blocks([state.branches for state in states], count)
    return ShellState(
        moved=any(state.moved for state in states),
        branches=branches,
        forks=frozenset().union(*(state.forks for state in states), forks),
        count=count + len(branches),
    )


def _merge_blocks(
    open_blocks: list[tuple[Branch, ...]], count: int
) -> tuple[tuple[Branch, ...], frozenset[int]]:
    """Return the `if` blocks open in every alternative, and the forks they add.

    Returns
    -------
    tuple[tuple[Branch, ...], frozenset[int]]
        The merged blocks, and the `uid` of each new fork.
    """
    merged: list[Branch] = []
    forks: set[int] = set()
    for depth in range(max(len(blocks) for blocks in open_blocks)):
        at_depth = {
            blocks[depth] if depth < len(blocks) else None for blocks in open_blocks
        }
        block, is_fork = _merge_depth(at_depth, count + depth)
        merged.append(block)
        if is_fork:
            forks.add(block.uid)
    return tuple(merged), frozenset(forks)


def _merge_depth(at_depth: set[Branch | None], uid: int) -> tuple[Branch, bool]:
    """Return the block the alternatives leave at one depth, and whether it forks.

    A block all alternatives agree on is kept as it is. One that no alternative
    can run stays inactive. Any other is a condition the probe decides, so it
    becomes a fork whose commands are indeterminate.

    Returns
    -------
    tuple[Branch, bool]
        The merged block, and whether it is a new fork.
    """
    present = {block for block in at_depth if block is not None}
    if len(present) == 1 and None not in at_depth:
        return next(iter(present)), False
    if None not in at_depth and not any(block.active for block in present):
        return _merge_inactive(present, uid), False
    return Branch(taken=False, active=True, uid=uid), True


def _merge_inactive(blocks: set[Branch], uid: int) -> Branch:
    """Return the block of alternatives none of which can run it.

    Whether an earlier branch of the `if` was taken may still differ, and a
    later `else` then runs on some alternatives only: an unnamed fork the `else`
    will settle.

    Returns
    -------
    Branch
        An inactive block, taken when every alternative says so.
    """
    takens = {block.taken for block in blocks}
    if len(takens) == 1:
        return Branch(taken=takens.pop(), active=False)
    return Branch(taken=False, active=False, uid=uid)
