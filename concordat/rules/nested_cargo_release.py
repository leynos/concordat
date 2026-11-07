"""Recognise the workflow steps cargo-dist generates to install its own tools.

A cargo-dist release workflow installs dist, and the packages it needs, with
`run: ${{ matrix.install_dist.run }}`: a command the plan job writes into the
matrix. The step has no text to read, but it is no test when three things hold:
the job's matrix is the `manifest` output of a step that runs `dist` in the
job named by `needs.<job>.outputs.<name>`, the lookup is a bare dotted path, and
the job goes on to run `dist build`. Anything else that is only an expression
stays unread, and so does any lookup in a job whose matrix is written out in the
workflow or produced by a script.
"""

from __future__ import annotations

import re
import typing as typ

# A step that is nothing but one dotted matrix lookup, with no operator in it.
_MATRIX_LOOKUP: typ.Final = re.compile(
    r"\s*\$\{\{\s*matrix(?:\.[A-Za-z_][A-Za-z0-9_-]*)+\s*\}\}\s*"
)
# A matrix that is another job's output rather than a list written here.
_PLANNED_MATRIX: typ.Final = re.compile(
    r"\s*\$\{\{\s*fromJ[Ss][Oo][Nn]\(\s*needs\."
    r"([A-Za-z_][\w-]*)\.outputs\.([A-Za-z_][\w-]*)\s*\)(?:\.[A-Za-z_][\w-]*)*\s*\}\}\s*"
)
# The subcommand expression cargo-dist's own release workflow generates.
_GENERATED_SUBCOMMAND: typ.Final = re.escape(
    "${{ (!github.event.pull_request && "
    "format('host --steps=create --tag={0}', github.ref_name)) || 'plan' }}"
)
_DIST_FLAGS: typ.Final = r"(?:\s+--?\w[\w=./:-]*)*"
# `dist plan|host [flags] > file`, or the generated expression in place of the
# subcommand: a dist command whose output goes to a file, and nothing else on the
# line. No `#`, `;`, `&`, `|`, backtick or other substitution can appear.
_DIST_REDIRECT: typ.Final = re.compile(
    r"(?:cargo\s+)?dist\s+(?:plan|host|"
    + _GENERATED_SUBCOMMAND
    + ")"
    + _DIST_FLAGS
    + r"\s*>\s*([\w./-]+)"
)
# An `echo` of fixed text that writes nothing: no substitution, no redirect.
_PLAIN_ECHO: typ.Final = re.compile(r"echo\s+(?:\"[^\"$`>]*\"|'[^'>$]*')")
# An output that is a step's `manifest` output.
_MANIFEST_OUTPUT: typ.Final = re.compile(
    r"\s*\$\{\{\s*steps\.([A-Za-z_][\w-]*)\.outputs\.manifest\s*\}\}\s*"
)
# A command line that starts cargo-dist: the job builds release artefacts.
_DIST_COMMAND: typ.Final = re.compile(r"^\s*(?:cargo\s+)?dist\s+[$a-z]", re.MULTILINE)


def _mapping(value: object) -> dict[str, object]:
    """Return *value* when it is a mapping, otherwise an empty one."""
    return typ.cast("dict[str, object]", value) if isinstance(value, dict) else {}


def _dist_steps(job: dict[str, object]) -> list[dict[str, object]]:
    """Return the steps of *job* whose `run` starts a cargo-dist command."""
    steps = job.get("steps")
    found = [_mapping(raw) for raw in steps] if isinstance(steps, list) else []
    return [
        step
        for step in found
        if isinstance(step.get("run"), str)
        and _DIST_COMMAND.search(str(step["run"])) is not None
    ]


def _manifest_step(planner: dict[str, object], output: str) -> str | None:
    """Return the id of the step whose `manifest` output *planner* exposes."""
    value = _mapping(planner.get("outputs")).get(output)
    match = _MANIFEST_OUTPUT.fullmatch(value) if isinstance(value, str) else None
    return match.group(1) if match is not None else None


def _plans_its_matrix(job: dict[str, object], jobs: dict[str, object]) -> bool:
    """Return whether the job's matrix is a cargo-dist plan job's manifest.

    Returns
    -------
    bool
        True when the matrix names `needs.<job>.outputs.<name>` and that job's
        output is the `manifest` output of a step that runs `dist`.
    """
    matrix = _mapping(job.get("strategy")).get("matrix")
    named = _PLANNED_MATRIX.fullmatch(matrix) if isinstance(matrix, str) else None
    if named is None:
        return False
    planner = _mapping(jobs.get(named.group(1)))
    step_id = _manifest_step(planner, named.group(2))
    return step_id is not None and any(
        step.get("id") == step_id and _is_dist_plan(str(step["run"]))
        for step in _dist_steps(planner)
    )


def _script_lines(run: str) -> list[str] | None:
    """Return the script's command lines, or None if an expression hides in it.

    GitHub substitutes an expression before the shell reads the script, a
    comment line included, so none may appear outside the dist command itself.

    Returns
    -------
    list[str] | None
        The non-blank, non-comment lines, or None when an expression is hidden.
    """
    lines = [line.strip() for line in run.splitlines()]
    if any("${{" in line and not _DIST_REDIRECT.fullmatch(line) for line in lines):
        return None
    return [line for line in lines if line and not line.startswith("#")]


def _plan_file(lines: list[str]) -> str | None:
    """Return the file the one `dist ... > FILE` command writes, if there is one.

    Returns
    -------
    str | None
        The file name, or None unless exactly one line is a dist redirect.
    """
    written = [_DIST_REDIRECT.fullmatch(line) for line in lines]
    found = [match.group(1) for match in written if match is not None]
    return found[0] if len(found) == 1 else None


def _is_dist_plan(run: str) -> bool:
    """Return whether *run* is cargo-dist's generated plan script and nothing more.

    Every line must be one of four things: the one `dist ... > FILE` command, a
    plain `echo` of fixed text, `cat FILE`, or the one line that publishes
    `manifest=$(jq -c . FILE)` to `$GITHUB_OUTPUT`, and the publication must
    follow the dist command. Any other line could write to the file or to the
    output, so a script with one is not read as the plan.

    Returns
    -------
    bool
        True when the step has cargo-dist's generated plan shape.
    """
    lines = _script_lines(run)
    target = _plan_file(lines) if lines is not None else None
    if lines is None or target is None:
        return False
    quoted = re.escape(target)
    publish = re.compile(
        rf"echo\s+\"manifest=\$\(jq\s+-c\s+[\"']?\.[\"']?\s+{quoted}\s*\)\""
        r"\s+>>\s+[\"']?\$\{?GITHUB_OUTPUT\}?[\"']?"
    )
    allowed = (_DIST_REDIRECT, _PLAIN_ECHO, publish, re.compile(rf"cat\s+{quoted}"))
    published = [i for i, line in enumerate(lines) if publish.fullmatch(line)]
    dist_at = next(i for i, line in enumerate(lines) if _DIST_REDIRECT.fullmatch(line))
    return (
        len(published) == 1
        and published[0] > dist_at
        and all(any(pattern.fullmatch(line) for pattern in allowed) for line in lines)
    )


def is_planned_dist_job(job: dict[str, object], jobs: dict[str, object]) -> bool:
    """Return whether *job* builds release artefacts from a cargo-dist plan."""
    return bool(_dist_steps(job)) and _plans_its_matrix(job, jobs)


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
