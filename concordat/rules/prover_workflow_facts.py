"""Read what each workflow step installs or runs, for `rust-prover-tooling`.

A step installs a prover itself when a `run` command is an installer, runs a
local installer script, or runs `make X` where `X` or a prerequisite does. A
step that uses the shared `install-prover-tools` action is recorded with its
ref, so PT-003 can require a full SHA. A step that only runs proofs is neither.
"""

from __future__ import annotations

import dataclasses
import posixpath
import typing as typ

from .prover_commands import (
    is_installer,
    is_runner,
    script_invoked,
    unwrap,
    wrapper_changes_directory,
)
from .prover_make_facts import commands_reached, goals
from .prover_pins import command_pins
from .shell_commands import Argv, commands

if typ.TYPE_CHECKING:
    from .codescene_coverage_envelope import WorkflowFile
    from .prover_make_facts import MakeRules
    from .prover_tooling_types import (
        InstallerStep,
        Pin,
        SharedUse,
        WorkflowFacts,
    )

SHARED_ACTION: typ.Final = "leynos/shared-actions/.github/actions/install-prover-tools"
ROOT: typ.Final = "."
MAKEFILE_OPTIONS: typ.Final = frozenset({
    "-C",
    "-f",
    "--directory",
    "--file",
    "--makefile",
})


@dataclasses.dataclass(frozen=True, slots=True)
class Context:
    """What a step's commands are read against."""

    rules: MakeRules
    installer_scripts: frozenset[str]


@dataclasses.dataclass(slots=True)
class StepFacts:
    """One step's installers, shared uses, make use, whether it runs, and pins.

    `where` is the step's `<job>/<index>` place, and `directory` the directory the
    next command runs in, relative to the repository root (None when unknown).
    A `cd` moves it.
    """

    where: str = ""
    directory: str | None = ROOT
    installers: list[InstallerStep] = dataclasses.field(default_factory=list)
    unresolved: list[InstallerStep] = dataclasses.field(default_factory=list)
    shared: list[SharedUse] = dataclasses.field(default_factory=list)
    calls_make: bool = False
    runs: bool = False
    pins: list[Pin] = dataclasses.field(default_factory=list)


def resolve(directory: str | None, path: str) -> str | None:
    """Return *path* as a checkout-relative path from *directory*, or None.

    Returns
    -------
    str | None
        The normalised path, or None when the directory is unknown, the path
        is absolute or a substitution, or it leaves the checkout.
    """
    if directory is None or path.startswith(("/", "~")) or "$" in path:
        return None
    resolved = posixpath.normpath(posixpath.join(directory, path))
    return None if resolved.startswith("..") else resolved


def installs(
    argv: Argv, installer_scripts: frozenset[str], directory: str | None = ROOT
) -> bool:
    """Return whether *argv* installs a prover, directly or by running a script.

    A command that runs a local script the rule found to be an installer
    (`bash scripts/get.sh`) is an installer step, so waiving PT-004 for the
    script does not clear PT-002 for the step that runs it.

    Returns
    -------
    bool
        Whether the command installs a prover itself.
    """
    if is_installer(argv):
        return True
    script = script_invoked(argv)
    if script is None:
        return False
    resolved = resolve(directory, script)
    return resolved is not None and resolved in installer_scripts


def _shared_use(where: str, step: dict[str, object]) -> list[SharedUse]:
    """Return the shared-action use a step makes, if it makes one."""
    uses = step.get("uses")
    if not isinstance(uses, str) or not uses.startswith(f"{SHARED_ACTION}@"):
        return []
    return [{"where": where, "ref": uses.split("@", maxsplit=1)[1]}]


def _make_installs(argv: Argv, context: Context) -> bool:
    """Return whether a `make` command reaches an installer."""
    reached = commands_reached(context.rules, goals(context.rules, argv))
    return any(installs(command, context.installer_scripts) for command in reached)


def _make_runs(argv: Argv, context: Context) -> bool:
    """Return whether a `make` command reaches a prover runner."""
    reached = commands_reached(context.rules, goals(context.rules, argv))
    return any(is_runner(command) for command in reached)


def _add_make(facts: StepFacts, argv: Argv, context: Context) -> None:
    """Record a `make` command's reach into the Makefile."""
    facts.calls_make = True
    if _make_installs(argv, context):
        facts.installers.append({"where": facts.where, "command": " ".join(argv)})
    facts.runs = facts.runs or _make_runs(argv, context)


def _route_make(
    facts: StepFacts, argv: Argv, context: Context, directory: str | None
) -> None:
    """Follow a `make` into the root Makefile, or record that it is not that one."""
    if directory == ROOT and not _selects_makefile(argv):
        _add_make(facts, argv, context)
        return
    facts.unresolved.append({"where": facts.where, "command": " ".join(argv)})


def _selects_makefile(argv: Argv) -> bool:
    """Return whether a `make` command picks a directory or Makefile of its own."""
    return any(_is_makefile_option(word) for word in argv[1:])


def _is_makefile_option(word: str) -> bool:
    """Return whether *word* is `-C`, `-f` or their long forms."""
    name = word.split("=", maxsplit=1)[0]
    if name in MAKEFILE_OPTIONS:
        return True
    return word.startswith(("-C", "-f")) and not word.startswith("--")


def _is_make(argv: Argv) -> bool:
    """Return whether the command, looking through wrappers, is `make`."""
    command = unwrap(argv)
    return bool(command) and command[0] == "make"


def _add_command(facts: StepFacts, argv: Argv, context: Context) -> None:
    """Record one `run` command's installs, runs and revision pins.

    The root Makefile's rules apply only to a `make` that runs from the
    repository root; one that runs from another directory builds that
    directory's Makefile, which the rule does not read. A command a wrapper
    moves (`env -C dir ...`) runs from a directory the rule cannot resolve.
    """
    moved = wrapper_changes_directory(argv)
    directory = None if moved else facts.directory
    facts.pins += command_pins([argv], "workflow")
    is_install = installs(argv, context.installer_scripts, directory)
    facts.runs = facts.runs or is_install or is_runner(argv)
    if is_install:
        facts.installers.append({"where": facts.where, "command": " ".join(argv)})
    if _is_make(argv):
        _route_make(facts, unwrap(argv), context, directory)
    facts.directory = _next_directory(facts.directory, argv)


def _next_directory(directory: str | None, argv: Argv) -> str | None:
    """Return the directory after *argv* runs: a `cd` moves it, anything else not."""
    command = unwrap(argv)
    if not command or command[0] != "cd":
        return directory
    operand = next((w for w in command[1:] if not w.startswith("-")), None)
    return None if operand is None else resolve(directory, operand)


def step_facts(
    where: str,
    step: dict[str, object],
    context: Context,
    *,
    directory: str | None = ROOT,
) -> StepFacts:
    """Return one step's prover facts.

    Parameters
    ----------
    where:
        The step's `<job>/<index>` place.
    step:
        The step mapping.
    context:
        What commands are read against.
    directory:
        The directory the step starts in, relative to the repository root, or
        None when it cannot be resolved; a `cd` in its script moves it.

    Returns
    -------
    StepFacts
        Installers, shared uses, whether it calls make or runs a prover, pins.
    """
    facts = StepFacts(where=where, directory=directory, shared=_shared_use(where, step))
    run = step.get("run")
    for argv in commands(run) if isinstance(run, str) else []:
        _add_command(facts, argv, context)
    return facts


def _steps(job: object) -> list[dict[str, object]]:
    """Return a job's step mappings."""
    steps = job.get("steps") if isinstance(job, dict) else None
    if not isinstance(steps, list):
        return []
    return [typ.cast("dict[str, object]", s) for s in steps if isinstance(s, dict)]


def _jobs(document: dict[str, object]) -> dict[str, object]:
    """Return a workflow document's jobs mapping."""
    jobs = document.get("jobs")
    return typ.cast("dict[str, object]", jobs) if isinstance(jobs, dict) else {}


def _default_directory(holder: object) -> object:
    """Return a `defaults.run.working-directory` a workflow or job sets."""
    defaults = holder.get("defaults") if isinstance(holder, dict) else None
    run = defaults.get("run") if isinstance(defaults, dict) else None
    return run.get("working-directory") if isinstance(run, dict) else None


def _step_directory(
    step: dict[str, object], job: object, document: dict[str, object]
) -> object:
    """Return the directory a step runs in, as its step, job or workflow sets it."""
    return (
        step.get("working-directory")
        or _default_directory(job)
        or _default_directory(document)
    )


def _initial_directory(directory: object) -> str | None:
    """Return a configured working directory relative to the root, or None.

    Returns
    -------
    str | None
        The root when none is set, the normalised path when it is a plain
        relative one, and None when it is not a string or cannot be resolved.
    """
    if not directory:
        return ROOT
    return resolve(ROOT, directory) if isinstance(directory, str) else None


def _job_steps(
    document: dict[str, object],
) -> list[tuple[str, dict[str, object], str | None]]:
    """Return every step: its place, its mapping and the directory it starts in."""
    return [
        (
            f"{job_id}/{index}",
            step,
            _initial_directory(_step_directory(step, job, document)),
        )
        for job_id, job in _jobs(document).items()
        for index, step in enumerate(_steps(job))
    ]


def _empty(workflow: WorkflowFile) -> WorkflowFacts:
    """Return a workflow's facts before any step is read."""
    return {
        "path": workflow["path"],
        "error": workflow["error"],
        "calls_make": False,
        "installer_steps": [],
        "unresolved_make": [],
        "shared_uses": [],
    }


def workflow_facts(
    workflow: WorkflowFile, context: Context
) -> tuple[WorkflowFacts, bool, list[Pin]]:
    """Return one workflow's facts, whether it runs a prover, and its pins.

    Returns
    -------
    tuple[WorkflowFacts, bool, list[Pin]]
        The facts, whether any step installs, runs or uses a prover action, and
        the revisions its commands name.
    """
    facts = _empty(workflow)
    document = workflow["parsed"]
    if workflow["error"] is not None or not isinstance(document, dict):
        return facts, False, []
    runs = False
    pins: list[Pin] = []
    for where, step, directory in _job_steps(typ.cast("dict[str, object]", document)):
        found = step_facts(where, step, context, directory=directory)
        facts["installer_steps"] += found.installers
        facts["unresolved_make"] += found.unresolved
        facts["shared_uses"] += found.shared
        facts["calls_make"] = facts["calls_make"] or found.calls_make
        runs = runs or found.runs or bool(found.shared)
        pins += found.pins
    return facts, runs, pins
