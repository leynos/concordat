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

from .gate_scope_commands import Invocation, invocations_in, leaves_directory
from .gate_scope_workflow_model import Place, logical_lines, places, step_runs
from .interrogate_invocation import lex_segments
from .make_commands import blank_references, command_start
from .makefile_expansion import (
    SHELL_DOLLAR,
    UNRESOLVED_MARK,
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
    r"whitaker|clippy|nextest|dylint|cargo\s+(?:\+\S+\s+)?(?:test|llvm-cov)",
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
    text: str, where: tuple[str, str], *, moved: bool = False
) -> tuple[list[Invocation], bool, bool]:
    """Return the invocations in one expanded line, whether it read, and where.

    A line is unreadable when it will not lex while naming a gate tool, or when
    any command in it has a command word (or Cargo subcommand) the expansion
    left unresolved beside words that name a gate tool. A line that only
    mentions a tool in a string or an identifier is not.

    *moved* says an earlier line of the same shell, or the step's working
    directory, already left the root; the result's last member is the same fact
    for the line after this one.

    Returns
    -------
    tuple[list[Invocation], bool, bool]
        The invocations found, whether the line could be read, and whether the
        shell is now outside the root directory.
    """
    segments = _lexed(text)
    if segments is None:
        found: list[Invocation] = []
        is_unreadable = _NAMES_TOOL.search(shown(text)) is not None
        return found, not is_unreadable, moved
    found = invocations_in(segments, where, moved=moved)
    # Each command is judged alone: a recognised `cargo clippy` beside an
    # unresolved `$(RUNNER) test` does not make the second readable.
    is_unreadable = any(_hides_tool(words) for words in segments)
    return found, not is_unreadable, moved or leaves_directory(segments)


def _holds_reference(word: str) -> bool:
    """Return whether *word* holds a Make reference or a shell expansion."""
    return UNRESOLVED_MARK in word or "$" in word or SHELL_DOLLAR in word


def _hides_tool(words: list[str]) -> bool:
    """Return whether one command might run a gate tool it does not name.

    That is a command word the expansion left unresolved, or a `cargo` command
    whose subcommand it left unresolved, with an argument only a gate tool takes.

    Returns
    -------
    bool
        Whether the command may be a gate tool.
    """
    start = command_start(words)
    if start >= len(words):
        return False
    head = words[start]
    unresolved_head = _holds_reference(head)
    unresolved_subcommand = (
        head.rsplit("/", 1)[-1] == "cargo"
        and len(words) > start + 1
        and _holds_reference(words[start + 1])
    )
    return (unresolved_head or unresolved_subcommand) and any(
        word in _TOOL_ARGUMENTS for word in words[start + 1 :]
    )


def makefile_lines(
    report: MakeutilReport, *, starts: cabc.Sequence[str] = GATE_TARGETS
) -> GateLines:
    """Return the gate-tool commands in the recipes the *starts* targets reach.

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
    invocations: list[Invocation] = []
    unreadable: list[str] = []
    for target in walk.names:
        for rule in index[target]:
            found, missed = _read_rule(target, rule, values, is_one_shell=is_one_shell)
            is_conditional = rule.is_conditional or target in walk.conditional
            invocations.extend(
                _unplaced(found, _MAKE_CONDITION_REASON) if is_conditional else found
            )
            unreadable.extend(missed)
    return GateLines(
        _distinct(invocations), _distinct_text(unreadable), walk.names, walk.is_resolved
    )


def _read_rule(
    target: str,
    rule: Rule,
    values: cabc.Mapping[str, str],
    *,
    is_one_shell: bool,
) -> tuple[list[Invocation], list[str]]:
    """Read every recipe line of one rule, in each reading its variables allow.

    Returns
    -------
    tuple[list[Invocation], list[str]]
        The invocations found, and the lines that could not be read.
    """
    invocations: list[Invocation] = []
    unreadable: list[str] = []
    moved_by_reading: dict[int, bool] = {}
    for number, text in rule.recipes:
        per_reading: list[list[Invocation]] = []
        for position, reading in enumerate(expand_readings(text, values)):
            shown_line = " ".join(shown(blank_references(joined(reading.text))).split())
            source = f"Makefile:{number} ({target})"
            found, is_readable, moved = _read_line(
                reading.text,
                (source, shown_line),
                moved=is_one_shell and moved_by_reading.get(position, False),
            )
            moved_by_reading[position] = moved
            per_reading.append(found)
            if not is_readable:
                unreadable.append(f"{source}: {shown_line}")
        invocations.extend(_alternatives(per_reading))
    return invocations, unreadable


def _alternatives(per_reading: list[list[Invocation]]) -> list[Invocation]:
    """Return the invocations of one line's readings, alternatives marked unplaced.

    The readings of a probe `if` are alternatives: one run takes one branch. A
    tool that two readings select differently (`-p a` against `-p b`) is not
    covered by their union, so each of its invocations is unplaced.

    Returns
    -------
    list[Invocation]
        Every reading's invocations, those of an ambiguous tool unresolved.
    """
    flat = [invocation for found in per_reading for invocation in found]
    if len(per_reading) < _MIN_ALTERNATIVES:
        return flat
    signatures: dict[str, set[tuple[object, ...]]] = {}
    for invocation in flat:
        signatures.setdefault(invocation["tool"], set()).add((
            invocation["workspace_flag"],
            tuple(invocation["packages"]),
            tuple(invocation["excludes"]),
            invocation["resolved"],
            invocation["changes_directory"],
        ))
    ambiguous = {tool for tool, seen in signatures.items() if len(seen) > 1}
    return [
        _unplaced([invocation], _ALTERNATIVE_REASON)[0]
        if invocation["tool"] in ambiguous
        else invocation
        for invocation in flat
    ]


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
    invocations: list[Invocation] = []
    unreadable: list[str] = []
    for workflow in files:
        if workflow["error"] is not None:
            unreadable.append(f"{workflow['path']}: {workflow['error']}")
            continue
        for place in places(workflow["parsed"]):
            found, missed = _read_place(workflow["path"], place)
            invocations.extend(found)
            unreadable.extend(missed)
    return GateLines(
        _distinct(invocations), _distinct_text(unreadable), [], is_resolved=True
    )


def _read_place(path: str, place: Place) -> tuple[list[Invocation], list[str]]:
    """Read the steps of one job or action, once per matrix combination.

    A matrix computed by an expression cannot be filled in, so a job whose steps
    name a gate tool is unreadable rather than skipped.

    Returns
    -------
    tuple[list[Invocation], list[str]]
        The invocations found, and the lines or jobs that could not be read.
    """
    if place.combinations is None:
        if _MATRIX_TOOL.search(repr(place.steps)):
            reason = (
                f"{path} ({place.label}): matrix is computed by an expression, "
                "so its commands cannot be filled in"
            )
            return [], [reason]
        return [], []
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
    invocations: list[Invocation] = []
    unreadable: list[str] = []
    for line in lines:
        shown_line = " ".join(line.split())
        found, is_readable, moved = _read_line(line, (source, shown_line), moved=moved)
        invocations.extend(found)
        if not is_readable:
            unreadable.append(f"{source}: {shown_line}")
    return invocations, unreadable


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
