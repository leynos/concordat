"""Scan a Makefile's `test` target for its test command and what precedes it.

The scan reads the target's recipe lines in file order and stops at the first
test command. It records whether a success-gated `cargo fetch --locked` runs
before that command, and why the order could not be read where it could not. A
line that picks its runner by probing the machine has more than one reading, and
the answer must hold in each of them.
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


@dataclasses.dataclass(slots=True)
class TargetScan:
    """What the `test` target says, accumulated line by line.

    ``uncertain_reason`` explains why a found test command's ordering is not
    proven. ``hidden_reason`` explains why the target may hold a test command
    the reader could not see at all, which is a different claim: a target that
    only builds is not an entry point, but a target whose command is unknown
    must not be reported as one that runs no tests. ``runners`` holds the test
    kind and nextest profile of every reading of the test line, because a
    setup script covers a profile and not the plain `cargo test` beside it.
    """

    is_found: bool = False
    test: str | None = None
    profile: str | None = None
    line: int = 1
    proven: bool = False
    proven_reason: str = ""
    uncertain_reason: str = ""
    hidden_reason: str = ""
    runners: list[tuple[str | None, str | None]] = dataclasses.field(
        default_factory=list
    )


def scan_target(
    index: dict[str, list[MakeRule]],
    values: cabc.Mapping[str, str],
    *,
    sequencing_holds: bool,
) -> TargetScan:
    """Read every rule that defines `test`, in file order, stopping at its test.

    Returns
    -------
    TargetScan
        The test command found, if any, and what proves or blocks its order.
    """
    scan = TargetScan()
    if not sequencing_holds:
        scan.uncertain_reason = "the Makefile declares .ONESHELL"
    else:
        proven, uncertain_reason, proven_reason = prefetch_in_prerequisites(
            index, values
        )
        scan.proven = proven
        scan.proven_reason = proven_reason
        scan.uncertain_reason = uncertain_reason
    for rule in index[TEST_TARGET]:
        for recipe in rule["recipes"]:
            if _inspect_line(scan, recipe, values, sequencing_holds=sequencing_holds):
                return scan
    return scan


def _inspect_line(
    scan: TargetScan,
    recipe: MakeRecipe,
    values: cabc.Mapping[str, str],
    *,
    sequencing_holds: bool,
) -> bool:
    """Read one recipe line, returning whether the target's test was found.

    The scan stops at the first test command. A fetch on a later line runs
    after the test, so it proves nothing about it, whatever `gates_next` says.

    Returns
    -------
    bool
        True when this line holds the target's test command.
    """
    readings = expand_readings(recipe["text"], values)
    if len(readings) == 1:
        return _inspect_reading(
            scan, recipe, readings[0], values, sequencing_holds=sequencing_holds
        )
    # The line picks its runner at run time (a probe `$(if $(shell ...),..)`),
    # so each reading is followed from the same start and the results merged:
    # an order holds only if it holds whichever branch Make takes.
    branches = [dataclasses.replace(scan) for _ in readings]
    found = [
        _inspect_reading(
            branch, recipe, reading, values, sequencing_holds=sequencing_holds
        )
        for branch, reading in zip(branches, readings, strict=True)
    ]
    _merge_readings(scan, branches, found)
    return any(found)


def _merge_readings(
    scan: TargetScan, branches: list[TargetScan], found: list[bool]
) -> None:
    """Fold the scans of a line's readings into *scan*, keeping the weakest proof.

    A reading with no test command must not displace one that has it. The merged
    order is proven only if every reading that holds the test proves it, and
    every such reading's runner is kept so each profile is judged.
    """
    chosen = [
        branch for branch, is_found in zip(branches, found, strict=True) if is_found
    ]
    chosen = chosen or branches
    first = min(chosen, key=lambda branch: branch.proven)
    for field in dataclasses.fields(scan):
        setattr(scan, field.name, getattr(first, field.name))
    scan.proven = all(branch.proven for branch in chosen)
    scan.runners = [runner for branch in chosen for runner in branch.runners]
    scan.uncertain_reason = next(
        (branch.uncertain_reason for branch in branches if branch.uncertain_reason),
        "",
    )
    scan.hidden_reason = next(
        (branch.hidden_reason for branch in branches if branch.hidden_reason), ""
    )


def _inspect_reading(
    scan: TargetScan,
    recipe: MakeRecipe,
    expansion: Expansion,
    values: cabc.Mapping[str, str],
    *,
    sequencing_holds: bool,
) -> bool:
    """Read one expansion of a recipe line, returning whether it holds the test.

    Returns
    -------
    bool
        True when this reading of the line holds the target's test command.
    """
    commands = script_commands(expansion.text)
    settled = are_settled(commands, values)
    if not settled:
        scan.uncertain_reason = "a recipe line names an unresolved variable"
    for position, command in enumerate(commands):
        if command.meaning.test is not None:
            _record_test(
                scan,
                command,
                recipe,
                commands,
                position,
                is_settled=settled,
                sequencing_holds=sequencing_holds,
            )
            return True
        _note_hidden(scan, command)
    if (
        sequencing_holds
        and not recipe["ignore_errors"]
        and line_fetches(commands, is_resolved=settled)
    ):
        scan.proven = True
        scan.proven_reason = "an earlier recipe line runs cargo fetch --locked"
    return False


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


def _record_test(
    scan: TargetScan,
    command: ScriptCommand,
    recipe: MakeRecipe,
    commands: list[ScriptCommand],
    position: int,
    *,
    is_settled: bool,
    sequencing_holds: bool,
) -> None:
    """Record the first test command of the target, and what precedes it."""
    scan.is_found = True
    scan.test = command.meaning.test
    scan.profile = command.meaning.profile
    scan.runners = [(command.meaning.test, command.meaning.profile)]
    scan.line = recipe["location"]["start_line"]
    if scan.proven:
        return
    if (
        sequencing_holds
        and not recipe["ignore_errors"]
        and is_settled
        and gates_fetch(commands, position)
    ):
        scan.proven = True
        scan.proven_reason = "cargo fetch --locked runs first on the same line"
    elif not is_settled:
        scan.uncertain_reason = "the test command names an unresolved variable"
