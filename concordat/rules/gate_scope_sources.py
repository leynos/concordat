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

from .gate_scope_commands import Invocation, invocations_in
from .interrogate_invocation import lex_segments
from .make_commands import blank_references
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
_NAMES_TOOL: typ.Final = re.compile(
    r"whitaker|clippy|nextest|dylint|cargo(?:\s+\+\S+)?\s+(?:test|llvm-cov)",
    re.IGNORECASE,
)
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


def _read_line(text: str, where: tuple[str, str]) -> tuple[list[Invocation], bool]:
    """Return the invocations in one expanded line, and whether it was readable.

    A line is unreadable when it names a gate tool, finds no invocation, and
    either will not lex or carries a reference the expansion left.

    Returns
    -------
    tuple[list[Invocation], bool]
        The invocations found, and whether the line could be read.
    """
    blanked = blank_references(joined(text))
    segments = lex_segments(blanked)
    found = invocations_in(segments, where) if segments is not None else []
    is_unreadable = (
        not found
        and _NAMES_TOOL.search(shown(text)) is not None
        and (segments is None or UNRESOLVED_MARK in blanked)
    )
    return found, not is_unreadable


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
    invocations: list[Invocation] = []
    unreadable: list[str] = []
    for target in walk.names:
        for rule in index[target]:
            for number, text in rule.recipes:
                for reading in expand_readings(text, values):
                    where = (
                        f"Makefile:{number} ({target})",
                        " ".join(shown(blank_references(joined(reading.text))).split()),
                    )
                    found, is_readable = _read_line(reading.text, where)
                    invocations.extend(found)
                    if not is_readable:
                        unreadable.append(f"{where[0]}: {where[1]}")
    return GateLines(
        _distinct(invocations), _distinct_text(unreadable), walk.names, walk.is_resolved
    )


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
        return [{}]
    names = list(axes)
    return [
        dict(zip(names, choice, strict=True))
        for choice in itertools.product(*(axes[name] for name in names))
    ]


def _with_includes(
    combinations: list[dict[str, object]], includes: list[object]
) -> list[dict[str, object]]:
    """Return *combinations* with each `include` entry applied.

    An entry extends every combination it does not contradict, and stands as a
    combination of its own when it extends none: GitHub's rule, without the
    ordering subtleties the gate commands do not depend on.

    Returns
    -------
    list[dict[str, object]]
        The combinations after the entries are applied.
    """
    result = [dict(combination) for combination in combinations]
    for candidate in includes:
        if not isinstance(candidate, dict):
            continue
        entry = typ.cast("dict[str, object]", candidate)
        extended = False
        for combination in result:
            shared = entry.keys() & combination.keys()
            if all(combination[key] == entry[key] for key in shared):
                combination.update(entry)
                extended = True
        if not extended:
            result.append(dict(entry))
    return result


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
    includes = axes.get("include", [])
    if not isinstance(includes, list):
        return None
    return _with_includes(_product(axes), typ.cast("list[object]", includes))


def _fill(text: str, combination: dict[str, object]) -> str:
    """Return *text* with `${{ matrix.* }}` filled in and other expressions marked."""

    def matrix(match: re.Match[str]) -> str:
        """Return one matrix reference's value, or the mark when it has none."""
        value = combination.get(match.group(1))
        return UNRESOLVED_MARK if value is None else _matrix_value(value)

    return _ANY_EXPRESSION.sub(UNRESOLVED_MARK, _MATRIX_REFERENCE.sub(matrix, text))


def _steps(parsed: object) -> list[tuple[str, list[dict[str, object]] | None, object]]:
    """Return each place that holds steps: a label, a matrix, and the steps."""
    if not isinstance(parsed, dict):
        return []
    places: list[tuple[str, list[dict[str, object]] | None, object]] = []
    jobs = parsed.get("jobs")
    if isinstance(jobs, dict):
        places.extend(
            (f"job {name}", matrix_combinations(job), job.get("steps"))
            for name, job in jobs.items()
            if isinstance(job, dict)
        )
    runs = parsed.get("runs")
    if isinstance(runs, dict):
        places.append(("action", [{}], runs.get("steps")))
    return places


def _run_lines(
    path: str, label: str, steps: object, combination: dict[str, object]
) -> list[tuple[str, str]]:
    """Return `(source, line)` pairs for each logical `run` line of the steps."""
    if not isinstance(steps, list):
        return []
    found: list[tuple[str, str]] = []
    for position, step in enumerate(steps, start=1):
        run = step.get("run") if isinstance(step, dict) else None
        if not isinstance(run, str):
            continue
        for _number, line in _logical_lines(_fill(run, combination)):
            found.append((f"{path} ({label}, step {position})", line))
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
        for label, combinations, steps in _steps(workflow["parsed"]):
            if combinations is None:
                if _NAMES_TOOL.search(repr(steps)):
                    unreadable.append(
                        f"{workflow['path']} ({label}): matrix is computed by an "
                        "expression, so its commands cannot be filled in"
                    )
                continue
            for combination in combinations:
                for source, line in _run_lines(
                    workflow["path"], label, steps, combination
                ):
                    found, is_readable = _read_line(
                        line, (source, " ".join(line.split()))
                    )
                    invocations.extend(found)
                    if not is_readable:
                        unreadable.append(f"{source}: {' '.join(line.split())}")
    return GateLines(
        _distinct(invocations), _distinct_text(unreadable), [], is_resolved=True
    )


def _distinct(invocations: list[Invocation]) -> list[Invocation]:
    """Drop repeats of one command from one source, keeping the first."""
    seen: set[tuple[str, str]] = set()
    kept: list[Invocation] = []
    for invocation in invocations:
        key = (invocation["source"], invocation["command"])
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
            segments = lex_segments(blank_references(line)) or []
            examples.extend(
                invocations_in(
                    segments, (f"{relative}:{number}", " ".join(line.split()))
                )
            )
    return examples
