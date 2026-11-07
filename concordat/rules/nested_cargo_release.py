"""Recognise the workflow steps cargo-dist generates to install its own tools.

A cargo-dist release workflow installs dist, and the packages it needs, with
`run: ${{ matrix.install_dist.run }}`: a command the plan job writes into the
matrix. The step has no text to read, but it is no test when two things hold:
the job's matrix is the plan job's output (`fromJson(needs.plan.outputs...)`,
not a literal list the reader could read for itself), and the job goes on to
run `dist build`. Anything else that is only an expression stays unread, and
so does any lookup in a job whose matrix is written out in the workflow.
"""

from __future__ import annotations

import re
import typing as typ

# A step that is nothing but one dotted matrix lookup, with no operator in it.
_MATRIX_LOOKUP: typ.Final = re.compile(
    r"\s*\$\{\{\s*matrix(?:\.[A-Za-z_][A-Za-z0-9_-]*)+\s*\}\}\s*"
)
# A matrix that is another job's output rather than a list written here.
_PLANNED_MATRIX: typ.Final = re.compile(r"\s*\$\{\{\s*fromJ[Ss][Oo][Nn]\(\s*needs\.")
# A command line that starts cargo-dist: the job builds release artefacts.
_DIST_COMMAND: typ.Final = re.compile(r"^\s*(?:cargo\s+)?dist\s+[a-z]", re.MULTILINE)


def _plans_its_matrix(job: dict[str, object]) -> bool:
    """Return whether the job's matrix is the output of the job that planned it."""
    strategy = job.get("strategy")
    matrix = strategy.get("matrix") if isinstance(strategy, dict) else None
    return isinstance(matrix, str) and _PLANNED_MATRIX.match(matrix) is not None


def _runs_dist(job: dict[str, object]) -> bool:
    """Return whether any step of the job runs a cargo-dist subcommand."""
    steps = job.get("steps")
    for raw in steps if isinstance(steps, list) else []:
        run = raw.get("run") if isinstance(raw, dict) else None
        if isinstance(run, str) and _DIST_COMMAND.search(run) is not None:
            return True
    return False


def is_planned_dist_job(job: dict[str, object]) -> bool:
    """Return whether *job* builds release artefacts from a planned matrix."""
    return _plans_its_matrix(job) and _runs_dist(job)


def hides_command(
    visible: str, run: str, *, has_expression: bool, releases: bool
) -> bool:
    """Return whether a step's whole command is an expression the reader cannot see.

    A cargo-dist job's tool installer, a bare lookup in the matrix its plan job
    wrote, is the one expression read as no command at all.

    Returns
    -------
    bool
        True when the step has a command and none of it is visible.
    """
    if not has_expression or visible.strip():
        return False
    return not (releases and _MATRIX_LOOKUP.fullmatch(run) is not None)
