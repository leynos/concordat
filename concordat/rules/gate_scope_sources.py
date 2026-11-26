"""Collect the gate commands a checkout runs: Makefile, CI and prose examples.

The gate is `make lint`, `make test` and CI. The Makefile half follows those
targets through their prerequisites and `make name` commands
(`makefile_reach`), expands each recipe line, and reads the commands in it. The
CI half reads every `run:` step of every workflow and composite action, with
each `${{ matrix.name }}` filled in from `strategy.matrix`, one reading per
matrix entry, so a gate that varies its cargo flags by matrix is read as the
commands it really runs. A reference that cannot be filled in stays unresolved.

A line that names a gate tool but cannot be read as a command (the shell lexer
refuses it, or a variable stands where the command word should be) is recorded
as unreadable, because dropping it would read as no gate rather than an unknown
one. A script that wraps a gate tool is an unresolved invocation for the same
reason.

Prose examples are collected separately: a fenced command in `AGENTS.md` or
`docs/` that runs a gate tool without selecting the whole workspace. They are
not findings; the policy names them so that the fix updates what contributors
copy.
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import re
import typing as typ

from .gate_scope_commands import (
    FRESH_SHELL,
    Invocation,
    ShellState,
    Tagged,
    cargo_subcommand_word,
    invocations_in,
    read_commands,
    settle,
)
from .gate_scope_shell import Branch
from .gate_scope_workflow_model import Place, logical_lines, places, step_runs
from .interrogate_invocation import lex_segments
from .make_commands import blank_references, command_start
from .makefile_expansion import (
    SHELL_DOLLAR,
    UNRESOLVED_MARK,
    Expansion,
    expand_readings,
    joined,
    shown,
    single_assignments,
)
from .makefile_reach import Rule, reachable, rules_by_target
from .markdown_envelope import (
    PRUNED_DIRECTORIES,
    is_symlink,
    raise_walk_error,
    read_text,
    within_checkout,
)
from .rust_source import scan_balanced

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from .makefile_facts import MakeutilReport
    from .markdown_envelope import WorkflowFile

__all__ = [
    "GATE_TARGETS",
    "GateLines",
    "makefile_lines",
    "markdown_examples",
    "workflow_lines",
]

GATE_TARGETS: typ.Final = ("lint", "test")
OPERATION_READ_EXAMPLES: typ.Final = "read-gate-scope-examples"
# A gate tool named as a word of its own. An identifier that merely contains the
# name (`WHITAKER_INSTALLER_VERSION`, `whitaker-installer`, `$whitaker`) is not
# a command, so a line that only mentions one is not unreadable.
_NAMES_TOOL: typ.Final = re.compile(
    r"(?<![\w$./-])(?:whitaker|clippy|nextest|dylint)(?![\w-])", re.IGNORECASE
)
# The same, loosely, for a step of a job whose matrix cannot be expanded: the
# commands are unknown there, so any mention of a tool is reason to say so.
_MATRIX_TOOL: typ.Final = re.compile(
    r"whitaker|clippy|nextest|dylint|cargo"
    # an expression standing where the command word goes, before a gate subcommand
    r"|\$\{\{[^}]*\}\}\s+(?:\+\S+\s+)?(?:test|nextest|clippy|dylint)\b",
    re.IGNORECASE,
)
# Words that, beside a command word the expansion left unresolved, say the
# command is one of the gate tools.
_TOOL_ARGUMENTS: typ.Final = frozenset({
    "clippy",
    "nextest",
    "dylint",
    "test",
    "--workspace",
    "--all-targets",
    "--all-features",
})
_FENCE: typ.Final = re.compile(r"^\s*(`{3,}|~{3,})")
_PROMPT: typ.Final = re.compile(r"^\s*\$\s+")
_EXAMPLE_ROOTS: typ.Final = ("AGENTS.md", "README.md", "docs")


@dataclasses.dataclass(frozen=True, slots=True)
class GateLines:
    """The invocations found, and the lines that name a tool but could not be read."""

    invocations: list[Invocation]
    unreadable: list[str]
    targets: list[str]
    is_resolved: bool


# A Make reference that stands for the Whitaker command: the estate's
# `$(WHITAKER)`, or the call a conditional `WHITAKER` variable expands to, such
# as `$(or $(shell command -v whitaker), whitaker)` or
# `$(firstword $(wildcard $(BIN)/whitaker) whitaker)`. A reference that merely
# names a variable beginning `WHITAKER_` is an argument, not the command.
_WHITAKER_REFERENCE: typ.Final = re.compile(
    r"^\$[({]\s*(?:WHITAKER(?:_BIN|_CMD)?\s*[)}]$"
    r"|(?:or|firstword|wildcard|shell|if)\s.*(?<![\w-])whitaker(?![\w-]))",
    re.DOTALL,
)
# A quoted string that holds a parenthesis and no shell expansion. The shared
# lexer rewrites `)"` to close a command substitution, which unbalances such a
# string, and nothing in it can select a package.
_PARENTHESISED_STRING: typ.Final = re.compile(
    r"\"[^\"$`\\]*[()][^\"$`\\]*\"|'[^'$`\\]*[()][^'$`\\]*'"
)


def _name_whitaker(text: str) -> str:
    """Replace each reference that stands for the Whitaker command by its name."""
    out: list[str] = []
    index = 0
    while index < len(text):
        end = (
            scan_balanced(text, index + 1)
            if text.startswith(("$(", "${"), index)
            else None
        )
        if end is None:
            out.append(text[index])
            index += 1
        elif _WHITAKER_REFERENCE.match(text[index:end]):
            out.append(" whitaker ")
            index = end
        else:
            out.append(text[index:end])
            index = end
    return "".join(out)


def _lexed(text: str) -> list[list[str]] | None:
    """Return the simple commands of one expanded line, or ``None`` if it won't lex."""
    muted = _PARENTHESISED_STRING.sub('"_"', joined(text))
    return lex_segments(blank_references(_name_whitaker(muted)))


def _read_line(
    text: str, where: tuple[str, str], state: ShellState = FRESH_SHELL
) -> tuple[list[Tagged], bool, ShellState]:
    """Return the invocations in one expanded line, whether it read, and the shell.

    A line is unreadable when it will not lex while naming a gate tool, or when
    any command in it has a command word (or Cargo subcommand) the expansion
    left unresolved beside words that name a gate tool. A line that only
    mentions a tool in a string or an identifier is not.

    *state* says what an earlier line of the same shell left: a `cd` that left
    the root, or an open `if false`. The state after this line is returned for
    the next.

    Returns
    -------
    tuple[list[Tagged], bool, ShellState]
        The tagged invocations found, whether the line could be read, and the state
        the next line of the shell starts from.
    """
    segments = _lexed(text)
    if segments is None:
        is_unreadable = _NAMES_TOOL.search(shown(text)) is not None
        return [], not is_unreadable, state
    found, after = read_commands(segments, where, state)
    # Each command is judged alone: a recognised `cargo clippy` beside an
    # unresolved `$(RUNNER) test` does not make the second readable.
    is_unreadable = any(_hides_tool(words) for words in segments)
    return found, not is_unreadable, after


def _holds_reference(word: str) -> bool:
    """Return whether *word* holds a Make reference or a shell expansion."""
    return UNRESOLVED_MARK in word or "$" in word or SHELL_DOLLAR in word


def _hides_tool(words: list[str]) -> bool:
    """Return whether one command might run a gate tool it does not name.

    That is a `cargo` command whose subcommand the expansion left unresolved,
    whatever its arguments, or a command word it left unresolved beside an
    argument only a gate tool takes.

    Returns
    -------
    bool
        Whether the command may be a gate tool.
    """
    start = command_start(words)
    if start >= len(words):
        return False
    head = words[start]
    if head.rsplit("/", 1)[-1] == "cargo":
        subcommand = cargo_subcommand_word(words[start + 1 :])
        return subcommand is not None and _holds_reference(subcommand)
    return _holds_reference(head) and any(
        word in _TOOL_ARGUMENTS for word in words[start + 1 :]
    )


def makefile_lines(
    report: MakeutilReport,
    *,
    starts: cabc.Sequence[str] = GATE_TARGETS,
    elsewhere: cabc.Collection[str] = (),
) -> GateLines:
    """Return the gate-tool commands in the recipes the *starts* targets reach.

    *elsewhere* names the tools the gate also runs outside the Makefile (in CI):
    a probe cannot make such a tool safe, because the other command may narrow.

    Returns
    -------
    GateLines
        The invocations in target and line order, the unreadable lines named by
        file and line, the targets reached, and whether the walk resolved.
    """
    index = rules_by_target(report)
    values = single_assignments(report["variables"])
    walk = reachable(index, values, starts)
    # Under `.ONESHELL` a rule's recipe lines are one shell, so a `cd` on one
    # moves the lines after it; otherwise each line is a shell of its own.
    is_one_shell = ".ONESHELL" in index
    lines: list[_Line] = []
    unreadable: list[str] = []
    for target in walk.names:
        is_conditional_target = target in walk.conditional
        for rule in index[target]:
            read, missed = _read_rule(target, rule, values, is_one_shell=is_one_shell)
            if rule.is_conditional or is_conditional_target:
                read = [
                    _Line([
                        _unplaced(found, _MAKE_CONDITION_REASON)
                        for found in line.readings
                    ])
                    for line in read
                ]
            lines.extend(read)
            unreadable.extend(missed)
    return GateLines(
        _distinct(_resolve_probes(lines, elsewhere)),
        _distinct_text(unreadable),
        walk.names,
        walk.is_resolved,
    )


class _Line(typ.NamedTuple):
    """One recipe line: the invocations of each reading its variables allow."""

    readings: list[list[Invocation]]


def _read_rule(
    target: str,
    rule: Rule,
    values: cabc.Mapping[str, str],
    *,
    is_one_shell: bool,
) -> tuple[list[_Line], list[str]]:
    """Read every recipe line of one rule, in each reading its variables allow.

    Returns
    -------
    tuple[list[_Line], list[str]]
        The lines with their readings, and the lines that could not be read.
    """
    pending: list[_Readings] = []
    unreadable: list[str] = []
    before: dict[int, ShellState] = {}
    every_state: list[ShellState] = []
    for number, text in rule.recipes:
        source = f"Makefile:{number} ({target})"
        readings = list(expand_readings(text, values))
        start = _shell_before(before) if is_one_shell else FRESH_SHELL
        read, before, missed = _read_recipe_line(source, readings, start)
        pending.append(read)
        unreadable.extend(missed)
        every_state.extend(before.values())
    # Under `.ONESHELL` a later `else` can turn an earlier branch into one of
    # two alternatives, so the forks are known only once the recipe is read.
    every_fork = frozenset().union(*(state.forks for state in every_state))
    lines = [
        _Line([
            settle(found, every_fork if is_one_shell else forks)
            for found, forks in per_reading
        ])
        for per_reading in pending
    ]
    return lines, unreadable


type _Readings = list[tuple[list[Tagged], frozenset[int]]]


def _read_recipe_line(
    source: str,
    readings: list[Expansion],
    start: ShellState,
) -> tuple[_Readings, dict[int, ShellState], list[str]]:
    """Read one recipe line in each of its readings.

    Returns
    -------
    tuple[_Readings, dict[int, ShellState], list[str]]
        Each reading's tagged invocations with the forks it leaves, the shell
        each reading ends in, and the readings that could not be read.
    """
    read: _Readings = []
    reached: dict[int, ShellState] = {}
    unreadable: list[str] = []
    for position, reading in enumerate(readings):
        shown_line = " ".join(shown(blank_references(joined(reading.text))).split())
        found, is_readable, after = _read_line(
            reading.text, (source, shown_line), start
        )
        reached[position] = after
        read.append((found, after.forks))
        if not is_readable:
            unreadable.append(f"{source}: {shown_line}")
    return read, reached, unreadable


def _shell_before(state_by_reading: cabc.Mapping[int, ShellState]) -> ShellState:
    """Return the shell the next line of a `.ONESHELL` recipe starts in.

    One earlier reading leaves one state. Several are alternatives of probes the
    next line's own readings need not correspond to (the two probes may test
    different tools), so every reading of the next line starts from all of
    them: moved if any moved, with each open `if` block kept when the states
    agree on it, so a branch known not to run stays skipped, and a fork when
    they disagree.

    Returns
    -------
    ShellState
        The merged state.
    """
    states = list(state_by_reading.values())
    if len(states) <= 1:
        return states[0] if states else FRESH_SHELL
    count = max(state.count for state in states)
    branches, forks = _merge_branches([state.branches for state in states], count)
    return ShellState(
        moved=any(state.moved for state in states),
        branches=branches,
        forks=frozenset().union(*(state.forks for state in states), forks),
        count=count + len(branches),
    )


def _merge_branches(
    open_blocks: list[tuple[Branch, ...]], count: int
) -> tuple[tuple[Branch, ...], frozenset[int]]:
    """Return the `if` blocks open in every alternative, and the forks they add.

    A block all alternatives agree on is kept as it is. One they disagree on, or
    that only some have open, is a condition the probe decides, so it becomes a
    fork whose commands are indeterminate, unless no alternative can run it.

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
        only = next(iter(at_depth))
        if len(at_depth) == 1 and only is not None:
            merged.append(only)
            continue
        if None not in at_depth and not any(b.active for b in at_depth if b):
            # No reading can run this branch, whatever the probe says.
            merged.append(Branch(taken=True, active=False))
            continue
        fork = Branch(taken=False, active=True, uid=count + depth)
        merged.append(fork)
        forks.add(fork.uid)
    return tuple(merged), frozenset(forks)


def _resolve_probes(
    lines: list[_Line], elsewhere: cabc.Collection[str]
) -> list[Invocation]:
    """Return every invocation, with the tools a probe leaves ambiguous unplaced.

    The readings of a probe `if` are alternatives, and one run takes one. A tool
    a probe line runs in some readings but not in all, or runs differently in
    them, covers a member only on the runs that take the right reading, so every
    invocation of that tool in the Makefile is unplaced. The exception is a tool
    whose every invocation is a probe's and selects the whole workspace: it
    covers everything whenever it runs, and a tool that does not run in a given
    run is not required.

    Returns
    -------
    list[Invocation]
        The invocations of every reading, in order.
    """
    flat = [i for line in lines for reading in line.readings for i in reading]
    ambiguous = _ambiguous_tools(lines, elsewhere)
    return [
        _unplaced([i], _ALTERNATIVE_REASON)[0] if i["tool"] in ambiguous else i
        for i in flat
    ]


def _selection_signature(invocation: Invocation) -> tuple[object, ...]:
    """Return what an invocation selects, as a comparable value."""
    return (
        invocation["tool"],
        invocation["workspace_flag"],
        tuple(invocation["packages"]),
        tuple(invocation["excludes"]),
        invocation["resolved"],
        invocation["changes_directory"],
    )


def _is_whole_workspace(invocation: Invocation) -> bool:
    """Return whether an invocation selects every member, whenever it runs."""
    return (
        invocation["workspace_flag"]
        and not invocation["excludes"]
        and invocation["resolved"]
        and not invocation["changes_directory"]
    )


def _ambiguous_tools(lines: list[_Line], elsewhere: cabc.Collection[str]) -> set[str]:
    """Return the tools whose coverage a probe line decides.

    A line whose readings all run the same commands is no probe. Of the tools
    the others run, those whose every invocation is a probe's and selects the
    whole workspace are safe: whichever reading runs, the tool covers everything.
    A tool the gate also runs *elsewhere* is never safe on that ground.

    Returns
    -------
    set[str]
        The tools to leave unplaced.
    """
    probes = [line for line in lines if _is_probe(line)]
    tools = {i["tool"] for line in probes for reading in line.readings for i in reading}
    return {
        tool
        for tool in tools
        if tool in elsewhere or not _only_whole_workspace_probes(tool, lines)
    }


def _is_probe(line: _Line) -> bool:
    """Return whether a line's readings run different commands."""
    if len(line.readings) < _MIN_ALTERNATIVES:
        return False
    signatures = {
        tuple(sorted(map(_selection_signature, reading))) for reading in line.readings
    }
    return len(signatures) > 1


def _only_whole_workspace_probes(tool: str, lines: list[_Line]) -> bool:
    """Return whether every invocation of *tool* is a whole-workspace probe's."""
    return all(
        _is_probe(line) and _is_whole_workspace(invocation)
        for line in lines
        for reading in line.readings
        for invocation in reading
        if invocation["tool"] == tool
    )


_MIN_ALTERNATIVES: typ.Final = 2
_MAKE_CONDITION_REASON: typ.Final = (
    "it runs only under a Make condition the audit does not evaluate"
)
_STEP_CONDITION_REASON: typ.Final = (
    "its step or job runs only under a matrix condition the audit does not evaluate"
)
_ALTERNATIVE_REASON: typ.Final = (
    "a probe makes it one of two readings that select differently, and one run "
    "takes one of them"
)


def _unplaced(found: list[Invocation], reason: str) -> list[Invocation]:
    """Return *found* marked as unplaced, for *reason*.

    The audit does not evaluate Make conditions, matrix conditions or probes, so
    it cannot say whether such a command runs, and counting it as covering a
    member, or as missing one, would be a guess.

    Returns
    -------
    list[Invocation]
        The same invocations, unresolved, with the reason.
    """
    return [{**invocation, "resolved": False, "why": reason} for invocation in found]


def workflow_lines(files: cabc.Iterable[WorkflowFile]) -> GateLines:
    """Return the gate-tool commands in every `run` step of the given files.

    A file that could not be decoded is unreadable, and so is a job whose
    matrix is computed by an expression, when one of its steps names a gate
    tool.

    Returns
    -------
    GateLines
        The invocations, the unreadable lines or files, and no Makefile targets.
    """
    readings = [_read_workflow(workflow) for workflow in files]
    return GateLines(
        _distinct([i for found, _missed in readings for i in found]),
        _distinct_text([line for _found, missed in readings for line in missed]),
        [],
        is_resolved=True,
    )


def _read_workflow(workflow: WorkflowFile) -> tuple[list[Invocation], list[str]]:
    """Read every job and action step of one workflow or composite action.

    Returns
    -------
    tuple[list[Invocation], list[str]]
        The invocations found, and the lines, jobs or file that could not be read.
    """
    if workflow["error"] is not None:
        return [], [f"{workflow['path']}: {workflow['error']}"]
    invocations: list[Invocation] = []
    unreadable: list[str] = []
    for place in places(workflow["parsed"]):
        found, missed = _read_place(workflow["path"], place)
        invocations.extend(found)
        unreadable.extend(missed)
    return invocations, unreadable


def _read_place(path: str, place: Place) -> tuple[list[Invocation], list[str]]:
    """Read the steps of one job or action, once per matrix combination.

    A matrix computed by an expression cannot be filled in, so a job whose steps
    run Cargo or a gate tool is unreadable rather than skipped.

    Returns
    -------
    tuple[list[Invocation], list[str]]
        The invocations found, and the lines or jobs that could not be read.
    """
    if place.combinations is None:
        return [], _unexpandable(path, place)
    invocations: list[Invocation] = []
    unreadable: list[str] = []
    for combination in place.combinations:
        for step in step_runs(place, combination):
            found, missed = _read_step(
                f"{path} {step.source}", step.lines, moved=step.is_elsewhere
            )
            invocations.extend(
                _unplaced(found, _STEP_CONDITION_REASON)
                if step.is_matrix_conditional
                else found
            )
            unreadable.extend(missed)
    return invocations, unreadable


def _unexpandable(path: str, place: Place) -> list[str]:
    """Return the unreadable entry for a job whose matrix is an expression, if any.

    Returns
    -------
    list[str]
        One entry when a step might run a gate tool, otherwise none.
    """
    if _MATRIX_TOOL.search(repr(place.steps)) is None:
        return []
    reason = (
        f"{path} ({place.label}): matrix is computed by an expression, so its "
        "commands cannot be filled in"
    )
    return [reason]


def _read_step(
    source: str, lines: list[str], *, moved: bool
) -> tuple[list[Invocation], list[str]]:
    """Read the lines of one step as one shell, carrying a `cd` forward.

    Returns
    -------
    tuple[list[Invocation], list[str]]
        The invocations found, and the lines that could not be read, each
        prefixed with the step's source.
    """
    tagged: list[Tagged] = []
    unreadable: list[str] = []
    state = ShellState(moved)
    for line in lines:
        shown_line = " ".join(line.split())
        found, is_readable, state = _read_line(line, (source, shown_line), state)
        tagged.extend(found)
        if not is_readable:
            unreadable.append(f"{source}: {shown_line}")
    return settle(tagged, state.forks), unreadable


def _distinct(invocations: list[Invocation]) -> list[Invocation]:
    """Drop repeats of one invocation, keeping the first.

    Two commands on one line are two invocations, so the key is everything that
    says what an invocation selects, not just where it was read.

    Returns
    -------
    list[Invocation]
        The distinct invocations in their original order.
    """
    seen: set[tuple[object, ...]] = set()
    kept: list[Invocation] = []
    for invocation in invocations:
        key = (
            invocation["source"],
            invocation["command"],
            invocation["tool"],
            invocation["workspace_flag"],
            tuple(invocation["packages"]),
            tuple(invocation["excludes"]),
            invocation["resolved"],
            invocation["changes_directory"],
        )
        if key not in seen:
            seen.add(key)
            kept.append(invocation)
    return kept


def _distinct_text(lines: list[str]) -> list[str]:
    """Return *lines* without repeats, in order."""
    return list(dict.fromkeys(lines))


def _markdown_files(checkout: pathlib.Path) -> list[pathlib.Path]:
    """Return the prose files that carry contributor-facing examples.

    Returns
    -------
    list[pathlib.Path]
        The root files and the Markdown files under the root directories, none
        reached through a symbolic link.
    """
    candidates = [checkout / name for name in _EXAMPLE_ROOTS]
    plain = [path for path in candidates if not path.is_symlink()]
    files = [path for path in plain if path.is_file()]
    for directory in (path for path in plain if path.is_dir()):
        files.extend(_markdown_under(directory))
    return files


def _markdown_under(directory: pathlib.Path) -> list[pathlib.Path]:
    """Return the Markdown files under *directory*, pruning what no envelope reads.

    Returns
    -------
    list[pathlib.Path]
        The files in walk order, names sorted.
    """
    found: list[pathlib.Path] = []
    for current, directories, files in os.walk(directory, onerror=raise_walk_error):
        directories[:] = sorted(
            entry for entry in directories if entry not in PRUNED_DIRECTORIES
        )
        found.extend(
            pathlib.Path(current) / entry
            for entry in sorted(files)
            if entry.endswith(".md")
        )
    return found


def _fenced_lines(text: str) -> list[tuple[int, str]]:
    """Return the logical lines inside fenced code blocks, with their numbers."""
    inside = False
    fenced: list[str] = []
    for line in text.splitlines():
        if _FENCE.match(line):
            inside = not inside
            fenced.append("")
        else:
            fenced.append(line if inside else "")
    return [
        (number, _PROMPT.sub("", line))
        for number, line in logical_lines("\n".join(fenced))
    ]


def markdown_examples(checkout: pathlib.Path, root: pathlib.Path) -> list[Invocation]:
    """Return the gate-tool commands that fenced prose examples show.

    Each is read like a gate command, with the file and line as its source. The
    policy decides which of them select less than the whole workspace, and
    names those in the finding, so the fix updates what contributors copy.

    Returns
    -------
    list[Invocation]
        The examples in file and line order.
    """
    examples: list[Invocation] = []
    for path in _markdown_files(checkout):
        if is_symlink(path, OPERATION_READ_EXAMPLES) or not within_checkout(
            root, path, OPERATION_READ_EXAMPLES
        ):
            continue
        relative = path.relative_to(checkout).as_posix()
        for number, line in _fenced_lines(read_text(path, OPERATION_READ_EXAMPLES)):
            segments = _lexed(line) or []
            examples.extend(
                invocations_in(
                    segments, (f"{relative}:{number}", " ".join(line.split()))
                )
            )
    return examples
