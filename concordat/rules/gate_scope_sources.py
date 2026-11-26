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
import itertools
import os
import pathlib
import re
import typing as typ

from .gate_scope_commands import Invocation, invocations_in, leaves_directory
from .interrogate_invocation import lex_segments
from .make_commands import blank_references, command_start
from .makefile_expansion import (
    UNRESOLVED_MARK,
    expand_readings,
    joined,
    shown,
    single_assignments,
)
from .makefile_reach import reachable, rules_by_target
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
_MATRIX_REFERENCE: typ.Final = re.compile(r"\$\{\{\s*matrix\.([\w-]+)\s*\}\}")
_ANY_EXPRESSION: typ.Final = re.compile(r"\$\{\{.*?\}\}")
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


def _logical_lines(text: str) -> list[tuple[int, str]]:
    r"""Return the lines of *text* with `\` continuations joined, and their numbers.

    Returns
    -------
    list[tuple[int, str]]
        Each logical line with the number of its first physical line.
    """
    lines: list[tuple[int, str]] = []
    pending = ""
    first = 0
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.rstrip()
        if not pending:
            first = number
        if stripped.endswith("\\"):
            pending += stripped[:-1] + " "
            continue
        lines.append((first, pending + stripped))
        pending = ""
    if pending:
        lines.append((first, pending))
    return lines


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
    unresolved_head = UNRESOLVED_MARK in head
    unresolved_subcommand = (
        head.rsplit("/", 1)[-1] == "cargo"
        and len(words) > start + 1
        and UNRESOLVED_MARK in words[start + 1]
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
            moved_by_reading: dict[int, bool] = {}
            for number, text in rule.recipes:
                for position, reading in enumerate(expand_readings(text, values)):
                    where = (
                        f"Makefile:{number} ({target})",
                        " ".join(shown(blank_references(joined(reading.text))).split()),
                    )
                    found, is_readable, moved = _read_line(
                        reading.text,
                        where,
                        moved=is_one_shell and moved_by_reading.get(position, False),
                    )
                    moved_by_reading[position] = moved
                    invocations.extend(
                        _conditional(found) if rule.is_conditional else found
                    )
                    if not is_readable:
                        unreadable.append(f"{where[0]}: {where[1]}")
    return GateLines(
        _distinct(invocations), _distinct_text(unreadable), walk.names, walk.is_resolved
    )


def _conditional(found: list[Invocation]) -> list[Invocation]:
    """Return *found* marked as unplaced: its recipe runs only under a Make condition.

    The audit does not evaluate `ifeq` and its kin, so it cannot say whether the
    recipe runs, and counting it as covering a member, or as missing one, would
    be a guess.

    Returns
    -------
    list[Invocation]
        The same invocations, unresolved, with the reason.
    """
    return [
        {
            **invocation,
            "resolved": False,
            "why": (
                "its recipe runs only under a Make condition the audit does not "
                "evaluate"
            ),
        }
        for invocation in found
    ]


def _matrix_value(value: object) -> str:
    """Return a matrix value as GitHub Actions would substitute it."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _product(matrix: dict[str, object]) -> list[dict[str, object]]:
    """Return the combinations of the matrix's list-valued axes."""
    axes = {
        key: value
        for key, value in matrix.items()
        if key not in {"include", "exclude"} and isinstance(value, list)
    }
    if not axes:
        # No axes means no original combinations, so each `include` entry
        # becomes a combination of its own.
        return []
    names = list(axes)
    return [
        dict(zip(names, choice, strict=True))
        for choice in itertools.product(*(axes[name] for name in names))
    ]


def _without_excluded(
    combinations: list[dict[str, object]], excludes: list[object]
) -> list[dict[str, object]]:
    """Return *combinations* less those an `exclude` entry matches.

    An entry removes every combination that holds all of its key and value
    pairs. GitHub applies `exclude` before `include`, so an included entry can
    bring back a combination that an exclude removed.

    Returns
    -------
    list[dict[str, object]]
        The combinations that survive.
    """
    entries = [
        typ.cast("dict[str, object]", entry)
        for entry in excludes
        if isinstance(entry, dict)
    ]
    return [
        combination
        for combination in combinations
        if not any(
            all(combination.get(key) == value for key, value in entry.items())
            for entry in entries
        )
    ]


def _with_includes(
    combinations: list[dict[str, object]], includes: list[object]
) -> list[dict[str, object]]:
    """Return *combinations* with each `include` entry applied, as GitHub does.

    An entry is added to every original combination whose original values it
    does not overwrite (a value an earlier `include` added can be overwritten),
    and stands as a combination of its own when it can be added to none. An
    entry that created a combination is not itself matched by later entries.

    Returns
    -------
    list[dict[str, object]]
        The combinations after the entries are applied.
    """
    originals = [dict(combination) for combination in combinations]
    results = [dict(combination) for combination in combinations]
    extra: list[dict[str, object]] = []
    for candidate in includes:
        if not isinstance(candidate, dict):
            continue
        entry = typ.cast("dict[str, object]", candidate)
        added = False
        for original, result in zip(originals, results, strict=True):
            if all(
                original[key] == entry[key] for key in entry.keys() & original.keys()
            ):
                result.update(entry)
                added = True
        if not added:
            extra.append(dict(entry))
    return [*results, *extra]


def matrix_combinations(job: object) -> list[dict[str, object]] | None:
    """Return the combinations a job's `strategy.matrix` expands to.

    Returns
    -------
    list[dict[str, object]] | None
        One mapping per matrix entry, a single empty one for a job with no
        matrix, or ``None`` when the matrix is computed by an expression and
        cannot be expanded here.
    """
    strategy = job.get("strategy") if isinstance(job, dict) else None
    matrix = strategy.get("matrix") if isinstance(strategy, dict) else None
    if matrix is None:
        return [{}]
    if not isinstance(matrix, dict):
        return None
    axes = typ.cast("dict[str, object]", matrix)
    includes, excludes = axes.get("include", []), axes.get("exclude", [])
    if not isinstance(includes, list) or not isinstance(excludes, list):
        return None
    # An axis that is not a list is computed by an expression. Dropping it would
    # leave combinations an `exclude` entry could not match, so the matrix is
    # not expanded at all.
    if any(
        not isinstance(value, list)
        for key, value in axes.items()
        if key not in {"include", "exclude"}
    ):
        return None
    combinations = _with_includes(
        _without_excluded(_product(axes), typ.cast("list[object]", excludes)),
        typ.cast("list[object]", includes),
    )
    # A matrix with no axes and no entries still runs its job once.
    return combinations or [{}]


def _fill(text: str, combination: dict[str, object]) -> str:
    """Return *text* with `${{ matrix.* }}` filled in and other expressions marked."""

    def matrix(match: re.Match[str]) -> str:
        """Return one matrix reference's value, or the mark when it has none."""
        value = combination.get(match.group(1))
        return UNRESOLVED_MARK if value is None else _matrix_value(value)

    return _ANY_EXPRESSION.sub(UNRESOLVED_MARK, _MATRIX_REFERENCE.sub(matrix, text))


@dataclasses.dataclass(frozen=True, slots=True)
class _Place:
    """A job or composite action: its matrix, its steps, and its default directory."""

    label: str
    combinations: list[dict[str, object]] | None
    steps: object
    directory: str | None


def _default_directory(*scopes: object) -> str | None:
    """Return the innermost `defaults.run.working-directory` among *scopes*."""
    found: str | None = None
    for scope in scopes:
        defaults = scope.get("defaults") if isinstance(scope, dict) else None
        run = defaults.get("run") if isinstance(defaults, dict) else None
        directory = run.get("working-directory") if isinstance(run, dict) else None
        if isinstance(directory, str):
            found = directory
    return found


def _steps(parsed: object) -> list[_Place]:
    """Return each place that holds steps, with its matrix and default directory."""
    if not isinstance(parsed, dict):
        return []
    places: list[_Place] = []
    jobs = parsed.get("jobs")
    if isinstance(jobs, dict):
        places.extend(
            _Place(
                f"job {name}",
                matrix_combinations(job),
                job.get("steps"),
                _default_directory(parsed, job),
            )
            for name, job in jobs.items()
            if isinstance(job, dict)
        )
    runs = parsed.get("runs")
    if isinstance(runs, dict):
        places.append(_Place("action", [{}], runs.get("steps"), None))
    return places


def _is_elsewhere(directory: object) -> bool:
    """Return whether a `working-directory` leaves the checkout root.

    An expression is unknown, and unknown is not the root.

    Returns
    -------
    bool
        Whether the step runs somewhere other than the root.
    """
    return isinstance(directory, str) and directory.strip() not in {"", ".", "./"}


_NEVER: typ.Final = frozenset({"false", "${{ false }}", "${{false}}"})


def _is_never(condition: object) -> bool:
    """Return whether a step's `if` is the constant false, so it never runs.

    Any other condition is taken to hold on some run: the rule judges the
    commands a gate has, not the schedule they run on.

    Returns
    -------
    bool
        Whether the step is switched off.
    """
    if condition is False:
        return True
    return isinstance(condition, str) and condition.strip().lower() in _NEVER


def _step_lines(
    place: _Place, combination: dict[str, object]
) -> list[tuple[str, list[str], bool]]:
    """Return `(source, lines, in_other_directory)` for each `run` step of *place*.

    The lines are the step's logical lines in order, because one step is one
    shell: a `cd` on an early line moves every command after it. The flag is
    set when the step, or its job or workflow, names a `working-directory`
    other than the root.

    Returns
    -------
    list[tuple[str, list[str], bool]]
        One entry per step that runs a script.
    """
    if not isinstance(place.steps, list):
        return []
    found: list[tuple[str, list[str], bool]] = []
    for position, step in enumerate(place.steps, start=1):
        run = step.get("run") if isinstance(step, dict) else None
        if not isinstance(step, dict) or not isinstance(run, str):
            continue
        if _is_never(step.get("if")):
            continue
        directory = step.get("working-directory", place.directory)
        lines = [line for _n, line in _logical_lines(_fill(run, combination))]
        found.append((
            f"({place.label}, step {position})",
            lines,
            _is_elsewhere(directory),
        ))
    return found


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
        for place in _steps(workflow["parsed"]):
            if place.combinations is None:
                if _MATRIX_TOOL.search(repr(place.steps)):
                    unreadable.append(
                        f"{workflow['path']} ({place.label}): matrix is computed by "
                        "an expression, so its commands cannot be filled in"
                    )
                continue
            for combination in place.combinations:
                for source, lines, elsewhere in _step_lines(place, combination):
                    found, missed = _read_step(
                        f"{workflow['path']} {source}", lines, moved=elsewhere
                    )
                    invocations.extend(found)
                    unreadable.extend(missed)
    return GateLines(
        _distinct(invocations), _distinct_text(unreadable), [], is_resolved=True
    )


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
    """Return the prose files that carry contributor-facing examples."""
    found: list[pathlib.Path] = []
    for name in _EXAMPLE_ROOTS:
        path = checkout / name
        if path.is_file() and not path.is_symlink():
            found.append(path)
        elif path.is_dir() and not path.is_symlink():
            for current, directories, files in os.walk(path, onerror=raise_walk_error):
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
        for number, line in _logical_lines("\n".join(fenced))
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
