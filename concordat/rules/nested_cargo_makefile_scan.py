"""Scan a Makefile's `test` target for its test command and what precedes it.

The scan reads the target's recipe lines in file order and stops at the first
test command on each path. It records whether a success-gated `cargo fetch
--locked` runs before that command, and why the order could not be read where it
could not. A line that picks its runner by probing the machine has more than one
reading: the scan forks there, so each path carries its own proof and the caller
judges every one.
"""

from __future__ import annotations

import dataclasses
import typing as typ

from .makefile_expansion import expand_readings
from .nested_cargo_makefile_lines import are_settled, prefetch_in_prerequisites
from .nested_cargo_scripts import (
    TEST_TARGET,
    ScriptCommand,
    gates_fetch,
    line_fetches,
    script_commands,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from .makefile_expansion import Expansion
    from .makefile_facts import MakeRecipe, MakeRule

# How many paths one scan follows. Each probe `if` doubles them.
MAX_PATHS: typ.Final = 8


@dataclasses.dataclass(slots=True)
class TargetScan:
    """What one path through the `test` target says, accumulated line by line.

    ``uncertain_reason`` explains why a found test command's ordering is not
    proven. ``hidden_reason`` explains why the target may hold a test command
    the reader could not see at all, which is a different claim: a target that
    only builds is not an entry point, but a target whose command is unknown
    must not be reported as one that runs no tests.
    """

    is_found: bool = False
    test: str | None = None
    profile: str | None = None
    line: int = 1
    proven: bool = False
    proven_reason: str = ""
    uncertain_reason: str = ""
    hidden_reason: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class _Line:
    """One reading of one recipe line, with the commands it expands to."""

    recipe: MakeRecipe
    commands: list[ScriptCommand]
    is_settled: bool
    sequencing_holds: bool

    @property
    def is_fatal(self) -> bool:
        """Whether a failure of this line stops the recipe."""
        return self.sequencing_holds and not self.recipe["ignore_errors"]


def scan_target(
    index: dict[str, list[MakeRule]],
    values: cabc.Mapping[str, str],
    *,
    sequencing_holds: bool,
) -> list[TargetScan]:
    """Read every rule that defines `test`, in file order, stopping at its test.

    Returns
    -------
    list[TargetScan]
        One scan per path through the recipe, each stopped at its own first
        test command. A path that never reaches one has ``is_found`` false.
    """
    start = TargetScan()
    if not sequencing_holds:
        start.uncertain_reason = "the Makefile declares .ONESHELL"
    else:
        proven, uncertain_reason, proven_reason = prefetch_in_prerequisites(
            index, values
        )
        start.proven = proven
        start.proven_reason = proven_reason
        start.uncertain_reason = uncertain_reason
    open_paths = [start]
    finished: list[TargetScan] = []
    for rule in index[TEST_TARGET]:
        for recipe in rule["recipes"]:
            forks = [
                branch
                for path in open_paths
                for branch in _fork(
                    path, recipe, values, sequencing_holds=sequencing_holds
                )
            ]
            finished.extend(branch for branch in forks if branch.is_found)
            open_paths = _capped([branch for branch in forks if not branch.is_found])
    return finished + open_paths


def _capped(paths: list[TargetScan]) -> list[TargetScan]:
    """Merge open paths that carry the same proof, and bound how many remain.

    Open paths differ only in what they have proved so far, so equal ones are
    one. If the distinct ones still pass the cap, the kept paths are marked
    uncertain rather than silently losing the ones that were cut.

    Returns
    -------
    list[TargetScan]
        At most :data:`MAX_PATHS` distinct paths.
    """
    distinct = list({dataclasses.astuple(path): path for path in paths}.values())
    if len(distinct) <= MAX_PATHS:
        return distinct
    kept = distinct[:MAX_PATHS]
    for path in kept:
        path.uncertain_reason = (
            "the recipe picks its runner on too many lines to follow"
        )
    return kept


def _fork(
    scan: TargetScan,
    recipe: MakeRecipe,
    values: cabc.Mapping[str, str],
    *,
    sequencing_holds: bool,
) -> list[TargetScan]:
    """Read one recipe line on every reading, returning one scan per reading.

    The scan stops at the first test command. A fetch on a later line runs
    after the test, so it proves nothing about it, whatever `gates_next` says.

    Returns
    -------
    list[TargetScan]
        The scan after each reading of the line; those with ``is_found`` set
        hold the test command.
    """
    readings = expand_readings(recipe["text"], values)
    scans = [dataclasses.replace(scan) for _ in readings]
    for branch, expansion in zip(scans, readings, strict=True):
        _inspect_reading(
            branch,
            _line_of(recipe, expansion, values, sequencing_holds=sequencing_holds),
        )
    return scans


def _line_of(
    recipe: MakeRecipe,
    expansion: Expansion,
    values: cabc.Mapping[str, str],
    *,
    sequencing_holds: bool,
) -> _Line:
    """Expand one reading's text into the commands the shell would see."""
    commands = script_commands(expansion.text)
    return _Line(recipe, commands, are_settled(commands, values), sequencing_holds)


def _inspect_reading(scan: TargetScan, line: _Line) -> None:
    """Read one expansion of a recipe line, recording the test if it holds one."""
    if not line.is_settled:
        scan.uncertain_reason = "a recipe line names an unresolved variable"
    for position, command in enumerate(line.commands):
        if command.meaning.test is not None:
            _record_test(scan, line, position)
            return
        _note_hidden(scan, command)
    if line.is_fatal and line_fetches(line.commands, is_resolved=line.is_settled):
        scan.proven = True
        scan.proven_reason = "an earlier recipe line runs cargo fetch --locked"


def _note_hidden(scan: TargetScan, command: ScriptCommand) -> None:
    """Record a command before the test whose effect the reader cannot follow."""
    if command.meaning.is_recursive_make:
        reason = "the recipe delegates to another make target"
    elif command.meaning.is_unreadable:
        reason = "a recipe command could not be read"
    else:
        return
    scan.uncertain_reason = reason
    scan.hidden_reason = scan.hidden_reason or reason


def _record_test(scan: TargetScan, line: _Line, position: int) -> None:
    """Record the first test command of the path, and what precedes it."""
    meaning = line.commands[position].meaning
    scan.is_found = True
    scan.test = meaning.test
    scan.profile = meaning.profile
    scan.line = line.recipe["location"]["start_line"]
    if scan.proven:
        return
    if _fetch_gates_test(line, position):
        scan.proven = True
        scan.proven_reason = "cargo fetch --locked runs first on the same line"
    elif not line.is_settled:
        scan.uncertain_reason = "the test command names an unresolved variable"


def _fetch_gates_test(line: _Line, position: int) -> bool:
    """Return whether a fetch earlier on this line must succeed for the test to run."""
    return line.is_fatal and line.is_settled and gates_fetch(line.commands, position)
