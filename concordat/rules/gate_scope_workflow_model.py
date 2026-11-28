"""Read the jobs, matrices and steps of a workflow or composite action.

The gate-workspace-coverage reader needs each `run` step as the commands it
really runs. This module is the structural half of reading CI: it expands a
job's `strategy.matrix` into combinations the way GitHub does, fills
`${{ matrix.name }}` into a step's script, and says, for each step, whether it
runs elsewhere than the root or only for some combinations. Reading the
commands out of the script is `gate_scope_sources`'s.

A matrix it cannot expand (an axis or list computed by an expression) is
reported as such, never expanded with the part it cannot read dropped, because
an `exclude` entry could then miss the combination it names.
"""

from __future__ import annotations

import dataclasses
import typing as typ

from .gate_scope_matrix import fill, matrix_combinations

__all__ = [
    "Place",
    "StepRun",
    "is_never",
    "logical_lines",
    "places",
    "step_runs",
]

_NEVER: typ.Final = frozenset({"false", "${{ false }}", "${{false}}"})


def logical_lines(text: str) -> list[tuple[int, str]]:
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


@dataclasses.dataclass(frozen=True, slots=True)
class Place:
    """A job or composite action: its matrix, its steps, and its default directory."""

    label: str
    combinations: list[dict[str, object]] | None
    steps: object
    directory: str | None
    condition: object = None


@dataclasses.dataclass(frozen=True, slots=True)
class StepRun:
    """One `run` step: where it is, its lines, and what is known about running it.

    `directory` is the step's `working-directory` (or its job's or workflow's
    default) as written after the matrix is filled in, or None when none is set.
    """

    source: str
    lines: list[str]
    is_elsewhere: bool
    is_matrix_conditional: bool
    directory: str | None = None


def _depends_on_matrix(condition: object) -> bool:
    """Return whether an `if` reads the matrix, so it varies by combination."""
    return isinstance(condition, str) and "matrix." in condition


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


def places(parsed: object) -> list[Place]:
    """Return each place that holds steps, with its matrix and default directory.

    A job whose `if` is the constant false is left out.

    Returns
    -------
    list[Place]
        The jobs of a workflow, or the steps of a composite action.
    """
    if not isinstance(parsed, dict):
        return []
    found: list[Place] = []
    jobs = parsed.get("jobs")
    if isinstance(jobs, dict):
        found.extend(
            Place(
                f"job {name}",
                matrix_combinations(job),
                job.get("steps"),
                _default_directory(parsed, job),
                job.get("if"),
            )
            for name, job in jobs.items()
            if isinstance(job, dict) and not is_never(job.get("if"))
        )
    runs = parsed.get("runs")
    if isinstance(runs, dict):
        found.append(Place("action", [{}], runs.get("steps"), None))
    return found


def _is_elsewhere(directory: object) -> bool:
    """Return whether a `working-directory` leaves the checkout root.

    An expression is unknown, and unknown is not the root.

    Returns
    -------
    bool
        Whether the step runs somewhere other than the root.
    """
    return isinstance(directory, str) and directory.strip() not in {"", ".", "./"}


def is_never(condition: object) -> bool:
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


def step_runs(place: Place, combination: dict[str, object]) -> list[StepRun]:
    """Return each `run` step of *place*, read for one matrix combination.

    The lines are the step's logical lines in order, because one step is one
    shell: a `cd` on an early line moves every command after it. A step is
    elsewhere when it, or its job or workflow, names a `working-directory` other
    than the root, and matrix-conditional when its `if` or its job's reads the
    matrix, so it runs for some combinations only.

    Returns
    -------
    list[StepRun]
        One entry per step that runs a script.
    """
    if not isinstance(place.steps, list):
        return []
    found: list[StepRun] = []
    for position, step in enumerate(place.steps, start=1):
        run = step.get("run") if isinstance(step, dict) else None
        if not isinstance(step, dict) or not isinstance(run, str):
            continue
        if is_never(step.get("if")):
            continue
        directory = step.get("working-directory", place.directory)
        found.append(
            StepRun(
                f"({place.label}, step {position})",
                [line for _n, line in logical_lines(fill(run, combination))],
                _is_elsewhere(directory),
                _depends_on_matrix(step.get("if"))
                or _depends_on_matrix(place.condition),
                fill(directory, combination) if isinstance(directory, str) else None,
            )
        )
    return found
