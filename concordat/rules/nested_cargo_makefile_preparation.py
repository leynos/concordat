"""Find a Makefile `test` target that prepares the nested builds before it tests.

A repository can pay for its nested builds outside the timed tests by building
them in a preparation target that `make test` runs first:

    test: prepare-nested-builds
        cargo nextest run ...

or on the recipe, `$(MAKE) prepare-nested-builds && cargo nextest run ...`, or
an earlier recipe line that builds. This module reads that declaration. It is a
claim that the repository has *declared* where the compile work is paid for, as
a nextest setup script is; it is not a claim that the preparation uses the same
features and profile as the tests, which the reader cannot know.

A preparation is a recipe command that compiles without running the tests
(`cargo build`, `bench`, `test --no-run`, `nextest archive`), read from
text the expansion settled, whose failure stops the target: not an
ignored-error line, not inside a conditional construct, and joined to what
follows by `&&` or a line break. A prerequisite of `test`, or a target the
recipe's own `make` goal names, counts when its recipes (or theirs, in turn)
hold one.
"""

from __future__ import annotations

import typing as typ

from .makefile_expansion import expand_readings, single_assignments
from .makefile_facts import MakefileRefusedError, inspect_makefile
from .makefile_tool_paths import read_tool_paths
from .markdown_envelope import within_checkout
from .nested_cargo_makefile_lines import rules_by_target
from .nested_cargo_scripts import TEST_TARGET, ScriptCommand, script_commands

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

    from .makefile_facts import MakeRecipe, MakeRule

MAKEFILE: typ.Final = "Makefile"
ONESHELL_TARGET: typ.Final = ".ONESHELL"
OPERATION_READ_MAKEFILE: typ.Final = "read-nested-cargo-makefile-preparation"


def _line_commands(
    recipe: MakeRecipe, values: cabc.Mapping[str, str]
) -> list[list[ScriptCommand]]:
    """Return the commands of each reading of one recipe line.

    A line that picks its runner by probing the machine has two readings, and a
    preparation must hold in all of them. A reference the expansion left in a
    cargo command's arguments does not stop a build being a build: whether its
    flags match the tests' is not something the reader claims to know.

    Returns
    -------
    list[list[ScriptCommand]]
        The commands of each reading, THEN branch first.
    """
    return [
        script_commands(reading.text)
        for reading in expand_readings(recipe["text"], values)
    ]


def _builds_before_the_end(commands: list[ScriptCommand]) -> bool:
    """Return whether a build on this line must succeed for the line to finish."""
    return any(
        command.meaning.is_build
        and not command.constructs
        and all(later.gates_next for later in commands[index:-1])
        for index, command in enumerate(commands)
    )


def _goals_before_the_end(commands: list[ScriptCommand]) -> list[str]:
    """Return the `make` goals this line runs that must succeed for it to finish."""
    return [
        goal
        for index, command in enumerate(commands)
        if not command.constructs
        and all(later.gates_next for later in commands[index:-1])
        for goal in command.meaning.make_goals
    ]


class _Reader:
    """Follow the targets of one Makefile, remembering the ones already walked."""

    def __init__(
        self, index: dict[str, list[MakeRule]], values: cabc.Mapping[str, str]
    ) -> None:
        """Hold the rule index and the substitutable variables."""
        self.index = index
        self.values = values
        self.seen: set[str] = set()

    def target_prepares(self, name: str) -> bool:
        """Return whether running target *name* compiles before it finishes."""
        if name in self.seen:
            return False
        self.seen.add(name)
        rules = self.index.get(name, [])
        return any(
            self.prerequisites_prepare(rule) or self.recipes_prepare(rule["recipes"])
            for rule in rules
        )

    def prerequisites_prepare(self, rule: MakeRule) -> bool:
        """Return whether a prerequisite of *rule* compiles before it finishes."""
        for reference in rule["prerequisites"]:
            for reading in expand_readings(reference, self.values):
                if reading.is_resolved and any(
                    self.target_prepares(name) for name in reading.text.split()
                ):
                    return True
        return False

    def recipes_prepare(self, recipes: list[MakeRecipe]) -> bool:
        """Return whether a recipe line builds, or runs a target that does."""
        for recipe in recipes:
            if recipe["ignore_errors"]:
                continue
            readings = _line_commands(recipe, self.values)
            if readings and all(self._line_prepares(commands) for commands in readings):
                return True
        return False

    def _line_prepares(self, commands: list[ScriptCommand]) -> bool:
        """Return whether one reading of a line builds or runs a preparing target."""
        return _builds_before_the_end(commands) or any(
            self.target_prepares(goal) for goal in _goals_before_the_end(commands)
        )

    def test_prepares(self) -> bool:
        """Return whether `test` prepares before the recipe line that tests."""
        for rule in self.index[TEST_TARGET]:
            if self.prerequisites_prepare(rule):
                return True
            for recipe in rule["recipes"]:
                readings = _line_commands(recipe, self.values)
                if self._test_line_prepares(recipe, readings):
                    return True
                if any(
                    command.meaning.test is not None
                    for commands in readings
                    for command in commands
                ):
                    return False
        return False

    def _test_line_prepares(
        self, recipe: MakeRecipe, readings: list[list[ScriptCommand]]
    ) -> bool:
        """Return whether this line prepares, on every reading, before it tests."""
        if recipe["ignore_errors"] or not readings:
            return False
        return all(self._prepares_before_test(commands) for commands in readings)

    def _prepares_before_test(self, commands: list[ScriptCommand]) -> bool:
        """Return whether a gated build or preparing target precedes the test."""
        position = next(
            (i for i, c in enumerate(commands) if c.meaning.test is not None),
            len(commands),
        )
        # A line with no test is a whole line that must succeed: its last
        # command ends the line, so only the ones before it have to gate.
        end = position if position < len(commands) else len(commands) - 1
        gated = [
            command
            for index, command in enumerate(commands[:position])
            if not command.constructs
            and all(later.gates_next for later in commands[index:end])
        ]
        return any(command.meaning.is_build for command in gated) or any(
            self.target_prepares(goal)
            for command in gated
            for goal in command.meaning.make_goals
        )


def makefile_prepares(checkout: pathlib.Path) -> bool:
    """Return whether the Makefile's `test` target prepares the nested builds first.

    Returns
    -------
    bool
        True when a prerequisite of `test`, an earlier recipe line, or an
        earlier success-gated command on the test line compiles before the
        test command. False when there is no Makefile, it cannot be parsed, it
        declares `.ONESHELL`, or nothing in it prepares: the reader never
        guesses a preparation it cannot see.
    """
    path = checkout / MAKEFILE
    try:
        root = checkout.resolve(strict=True)
    except OSError:
        root = checkout
    if not path.is_file() or not within_checkout(root, path, OPERATION_READ_MAKEFILE):
        return False
    try:
        report = inspect_makefile(path).report
    except MakefileRefusedError:
        return False
    index = rules_by_target(report)
    if TEST_TARGET not in index or ONESHELL_TARGET in index:
        return False
    values = read_tool_paths(single_assignments(report["variables"]))
    return _Reader(index, values).test_prepares()
