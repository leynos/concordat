"""Collect the lint commands a gate runs, for `gate-lint-coverage`.

The gate is the Makefile's `lint` target followed through its prerequisites and
`make name` commands, and every `run` step of every workflow and composite
action. Each simple command that can run is read as a `Run`
(`lint_coverage_commands`): Clippy, Whitaker or `cargo doc`, with what it checks
and how. The shell is read as `gate-workspace-coverage` reads it: a command in a
branch that cannot run is not a command, a `cd` in the same shell moves the
directory the next command runs in, and `.ONESHELL` makes a rule's lines one
shell.

A script that builds the lanes (femtologging's `lint_rust_lanes.py`) is not read.
It is recorded as an unresolved run of each tool its text names, with the lane
inputs it was given, so the policy reports it as indeterminate and the survey can
measure what it checks by another route.

Environment is read where it can be: `RUSTFLAGS` and `RUSTDOCFLAGS` exported by
the Makefile, and the `rustflags` of `.cargo/config.toml`. A workflow's own `env`
is not, so a workflow run that does not deny warnings on its command line is
unknown, not wrong.
"""

from __future__ import annotations

import dataclasses
import posixpath
import re
import typing as typ

from .cargo_config import inspect_cargo_config
from .gate_scope_shell import FRESH_SHELL, runnable
from .gate_scope_workflow_model import places, step_runs
from .interrogate_invocation import lex_segments
from .lint_coverage_commands import (
    Run,
    flags_deny_warnings,
    parse_command,
    script_run,
)
from .make_commands import blank_references
from .makefile_expansion import expand, joined, single_assignments
from .makefile_reach import reachable, rules_by_target
from .markdown_envelope import (
    is_symlink,
    load_workflows,
    read_text,
    within_checkout,
)
from .uv_gate_envelope import load_actions

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

    from .gate_scope_shell import ShellState
    from .gate_scope_workflow_model import Place, StepRun
    from .makefile_facts import MakefileInspector, MakeutilReport
    from .makefile_reach import Rule
    from .markdown_envelope import WorkflowFile

__all__ = ["GateRuns", "read_gate_runs"]

LINT_TARGETS: typ.Final = ("lint",)
OPERATION_READ_SCRIPT: typ.Final = "read-lint-script"
# A command line that runs one of the tools: `cargo clippy`, `cargo doc`,
# `cargo dylint`, or the `whitaker` command (not `whitaker.toml` or `dylint.toml`).
_NAMES_TOOL: typ.Final = re.compile(
    r"(?<![\w.-])whitaker(?![\w.-])|cargo\s+(?:\+\S+\s+)?(?:clippy|doc|dylint)\b"
)
# A command that may reach a lint without naming a tool: `make`, or a script.
_MAY_LINT: typ.Final = re.compile(
    r"\b(?:make|bash|sh|zsh|python3?|uv|node|npm|pnpm|yarn|just|task|lint)\b"
    r"|\./|\.(?:sh|py|bash|js|ts)\b"
    r"|(?:^|[\s;&|(])[\w.-]+/[\w./-]+"
)
# What a lane-building script's text must show to stand for a tool: the command
# itself, as a shell line or as the words of a subprocess list.
_RUNS_CLIPPY: typ.Final = re.compile(r"cargo[\s\"',\[]+(?:\+\S+[\s\"',]+)?clippy\b")
_RUNS_WHITAKER: typ.Final = re.compile(
    r"(?<![\w.-])whitaker(?![\w.-])[\s\"',\[]+(?:--all|--lib|--)|cargo[\s\"',\[]+dylint\b"
)


class MakeEnvironment(typ.TypedDict):
    """What the Makefile exports as `RUSTFLAGS` and `RUSTDOCFLAGS`.

    A value is None when the Makefile does not export the variable.
    `is_resolved` is false when an exported value could not be expanded to one
    string (assigned twice, or built with a Make function).
    """

    rustflags: str | None
    rustdocflags: str | None
    is_resolved: bool


class ConfigFlags(typ.TypedDict):
    """Whether `.cargo/config.toml` denies warnings for every build."""

    denies_warnings: bool
    is_readable: bool


class GateRuns(typ.TypedDict):
    """The lint commands of a gate, and the environment they run in."""

    runs: list[Run]
    makefile_resolved: bool
    make_environment: MakeEnvironment
    cargo_config: ConfigFlags
    unreadable: list[str]


@dataclasses.dataclass(slots=True)
class _Reader:
    """What one gate read shares: the checkout, the runs and what was unreadable."""

    checkout: pathlib.Path
    root: pathlib.Path
    runs: list[Run] = dataclasses.field(default_factory=list)
    unreadable: list[str] = dataclasses.field(default_factory=list)

    def script_tools(self, relative: str) -> set[str]:
        """Return the tools a script's text names beside `cargo`, or none."""
        path = self.checkout / relative
        try:
            if is_symlink(path, OPERATION_READ_SCRIPT) or not within_checkout(
                self.root, path, OPERATION_READ_SCRIPT
            ):
                return set()
            text = read_text(path, OPERATION_READ_SCRIPT)
        except (UnicodeDecodeError, OSError):
            return set()
        tools = {"clippy"} if _RUNS_CLIPPY.search(text) else set()
        return tools | ({"whitaker"} if _RUNS_WHITAKER.search(text) else set())


def _next_directory(directory: str | None, words: list[str]) -> str | None:
    """Return the directory after a `cd`, or None when it cannot be followed."""
    target = words[1] if len(words) > 1 else None
    if directory is None or target is None:
        return None
    if target.startswith(("/", "~", "-")) or any(c in target for c in "$*?"):
        return None
    moved = posixpath.normpath(posixpath.join(directory or ".", target))
    return "" if moved == "." else moved


def _command_runs(
    reader: _Reader, words: list[str], source: str, directory: str | None
) -> list[Run]:
    """Return the runs one simple command makes (a tool, or a lane script)."""
    run = parse_command(words, source, directory)
    if run is not None:
        return [run]
    return script_run(words, source, directory, reader.script_tools)


def _line_runs(
    reader: _Reader,
    text: str,
    source: str,
    state: tuple[ShellState, str | None],
) -> tuple[ShellState, str | None]:
    """Read one line of a shell, adding its runs, and return the shell after it.

    A line that will not lex but names a lint tool is unreadable. The directory
    in *state* is where the shell is; a `cd` moves it for the commands after.

    Returns
    -------
    tuple[ShellState, str | None]
        The shell state and directory the next line starts from.
    """
    shell, directory = state
    segments = lex_segments(blank_references(joined(text)))
    if segments is None:
        if _NAMES_TOOL.search(text):
            reader.unreadable.append(f"{source}: {' '.join(text.split())}")
        return shell, directory
    kept, shell = runnable(segments, shell)
    for command in kept:
        directory = _read_kept(reader, command, source, directory)
    return shell, directory


def _read_kept(
    reader: _Reader,
    command: tuple[list[str], tuple[int, ...]],
    source: str,
    directory: str | None,
) -> str | None:
    """Read one kept command; one inside an undecided `if` is unresolved.

    Such a command may be one of several exclusive lanes, so it does not count as
    a lane of the whole gate.

    Returns
    -------
    str | None
        The directory the next command runs in.
    """
    words, frames = command
    start = len(reader.runs)
    moved = _read_command(reader, words, source, directory)
    if not frames:
        return moved
    _mark_unresolved(
        reader.runs[start:], "it runs under a shell conditional it could not settle"
    )
    # A `cd` inside an undecided `if` may or may not have happened, so the
    # commands after it cannot be placed.
    return None if moved != directory else moved


def _read_command(
    reader: _Reader, words: list[str], source: str, directory: str | None
) -> str | None:
    """Read one simple command: a `cd` moves the directory, a lint run is added.

    Returns
    -------
    str | None
        The directory the next command runs in.
    """
    if words and words[0].lstrip("@-+") == "cd":
        return _next_directory(directory, words)
    reader.runs.extend(_command_runs(reader, words, source, directory))
    return directory


def _makefile_runs(
    reader: _Reader, report: MakeutilReport
) -> tuple[bool, dict[str, str]]:
    """Read the recipes `lint` reaches; return whether the walk resolved.

    Also returns the Make variable values, which the environment reading needs.

    Returns
    -------
    tuple[bool, dict[str, str]]
        Whether the walk resolved, and the variable values.
    """
    index = rules_by_target(report)
    values = single_assignments(report["variables"])
    walk = reachable(index, values, LINT_TARGETS)
    makefile = _MakefileContext(values, is_one_shell=".ONESHELL" in index)
    for target in walk.names:
        for rule in index[target]:
            _rule_runs(reader, target, rule, makefile)
    return walk.is_resolved, dict(values)


class _MakefileContext(typ.NamedTuple):
    """What reading a recipe needs of its Makefile: variables and shell mode."""

    values: cabc.Mapping[str, str]
    is_one_shell: bool


def _rule_runs(
    reader: _Reader, target: str, rule: Rule, makefile: _MakefileContext
) -> None:
    """Read the recipe lines of one rule of *target*."""
    state: tuple[ShellState, str | None] = (FRESH_SHELL, "")
    for number, text in rule.recipes:
        expanded = expand(text, makefile.values)
        state = _line_runs(
            reader, expanded.text, f"Makefile:{number} ({target})", state
        )
        if not makefile.is_one_shell:
            state = (FRESH_SHELL, "")


def _environment(
    report: MakeutilReport, values: cabc.Mapping[str, str]
) -> MakeEnvironment:
    """Return the `RUSTFLAGS` and `RUSTDOCFLAGS` the Makefile exports."""
    export_all = ".EXPORT_ALL_VARIABLES" in rules_by_target(report)
    exported = {v["name"] for v in report["variables"] if v.get("exported")}
    assigned = {v["name"] for v in report["variables"]}
    found = {
        name: _exported_value(name, values, is_exported=export_all or name in exported)
        if name in assigned
        else (None, True)
        for name in ("RUSTFLAGS", "RUSTDOCFLAGS")
    }
    return {
        "rustflags": found["RUSTFLAGS"][0],
        "rustdocflags": found["RUSTDOCFLAGS"][0],
        "is_resolved": all(is_known for _value, is_known in found.values()),
    }


def _exported_value(
    name: str, values: cabc.Mapping[str, str], *, is_exported: bool
) -> tuple[str | None, bool]:
    """Return what an exported variable expands to, and whether that is known.

    Returns
    -------
    tuple[str | None, bool]
        The expanded text (None when not exported or not expandable), and False
        when the variable is exported with a value that could not be expanded.
    """
    if not is_exported:
        return None, True
    expanded = expand(f"$({name})", values) if name in values else None
    if expanded is None or not expanded.is_resolved:
        return None, False
    return expanded.text, True


def _place_runs(reader: _Reader, path: str, place: Place) -> None:
    """Read the steps of one workflow job or action, once per matrix entry."""
    if place.combinations is None:
        _note_computed_matrix(reader, path, place)
        return
    steps = (
        step
        for combination in place.combinations
        for step in step_runs(place, combination)
    )
    for step in steps:
        _step_runs(reader, f"{path} {step.source}", step)


def _commands_text(steps: object) -> str:
    """Return the text of a job's commands: its `run` steps and local actions.

    A third-party action (`actions/checkout@v4`) is not a command the job runs
    in the repository, so it is left out.

    Returns
    -------
    str
        The `run` texts and local `uses` paths, one per line.
    """
    if not isinstance(steps, list):
        return ""
    found: list[str] = []
    for step in steps:
        fields = step if isinstance(step, dict) else {}
        run, uses = fields.get("run"), fields.get("uses")
        found.extend((
            run if isinstance(run, str) else "",
            uses if isinstance(uses, str) and uses.startswith(".") else "",
        ))
    return "\n".join(found)


def _note_computed_matrix(reader: _Reader, path: str, place: Place) -> None:
    """Record a job whose matrix is computed, if it may run a lint.

    A job may lint by naming a tool, by `make` (which may reach a lint target) or
    by a script, so any of these leaves the job indeterminate.
    """
    steps = repr(place.steps)
    if _NAMES_TOOL.search(steps) or _MAY_LINT.search(_commands_text(place.steps)):
        reader.unreadable.append(
            f"{path} ({place.label}): matrix is computed by an expression, so "
            "its commands cannot be filled in"
        )


def _step_runs(reader: _Reader, source: str, step: StepRun) -> None:
    """Read the lines of one workflow step as one shell."""
    start = len(reader.runs)
    where = None if step.is_elsewhere else ""
    state: tuple[ShellState, str | None] = (FRESH_SHELL, where)
    for line in step.lines:
        state = _line_runs(reader, line, source, state)
    if step.is_matrix_conditional:
        _mark_unresolved(reader.runs[start:], "it runs for some matrix entries only")


def _mark_unresolved(runs: list[Run], why: str) -> None:
    """Mark runs the audit cannot settle, with the reason."""
    for run in runs:
        run["is_resolved"] = False
        run["why"] = why


def _workflow_runs(reader: _Reader, files: list[WorkflowFile]) -> None:
    """Read every workflow and composite-action file."""
    for workflow in files:
        if workflow["error"] is not None:
            reader.unreadable.append(f"{workflow['path']}: {workflow['error']}")
            continue
        for place in places(workflow["parsed"]):
            _place_runs(reader, workflow["path"], place)


def _config_flags(reader: _Reader) -> ConfigFlags:
    """Return whether `.cargo/config.toml` denies warnings in every rustflags."""
    facts = inspect_cargo_config(reader.checkout)
    if facts is None:
        return {"denies_warnings": False, "is_readable": True}
    if facts["parse_error"] is not None:
        return {"denies_warnings": False, "is_readable": False}
    sources = facts["sources"]
    denies = bool(sources) and all(
        flags_deny_warnings(" ".join(source["flags"])) for source in sources
    )
    return {"denies_warnings": denies, "is_readable": True}


def read_gate_runs(
    checkout: pathlib.Path,
    root: pathlib.Path,
    *,
    inspect: MakefileInspector,
    has_makefile: bool,
) -> GateRuns:
    """Return the lint commands of the checkout's gate and their environment.

    Parameters
    ----------
    checkout:
        Path to the checkout under audit.
    root:
        The checkout's resolved root, for the containment guard.
    inspect:
        Reads the root Makefile's facts.
    has_makefile:
        Whether the checkout has a regular root `Makefile`.

    Returns
    -------
    GateRuns
        The runs found, whether the Makefile walk resolved, what the Makefile
        exports, what `.cargo/config.toml` says, and what could not be read.
    """
    reader = _Reader(checkout, root)
    is_resolved = True
    environment: MakeEnvironment = {
        "rustflags": None,
        "rustdocflags": None,
        "is_resolved": True,
    }
    if has_makefile:
        report = inspect(checkout / "Makefile").report
        is_resolved, values = _makefile_runs(reader, report)
        environment = _environment(report, values)
    _workflow_runs(
        reader, [*load_workflows(checkout, root), *load_actions(checkout, root)]
    )
    return {
        "runs": reader.runs,
        "makefile_resolved": is_resolved,
        "make_environment": environment,
        "cargo_config": _config_flags(reader),
        "unreadable": sorted(set(reader.unreadable)),
    }
