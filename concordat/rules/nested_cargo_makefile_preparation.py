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
follows by `&&` or a line break, and that runs on every path to the test (a
build after `||` runs only when the command before it failed). A prerequisite
of `test`, or a target the recipe's own `make` goal names, counts when its
recipes (or theirs, in turn) hold one before any test command of their own. A
prerequisite counts only when no sibling prerequisite runs tests it does not
itself prepare for, because Make does not order siblings (`make -j` runs them
together). A `make` goal reached through `-C`, `-f` or `-n` is not followed:
another Makefile's targets are not this one's, and a dry run builds nothing.

The reader fails closed. A line counts as preparation only in the forms
modelled above, exactly; any construct it does not model is not preparation:
`make -t`, `-q` or `-i`, a group of goals, a `VAR=value` override on the make
line, a goal the shell computes, prerequisites that read in alternative ways
(a probe) or that the expansion left unresolved, and a prerequisite that may
run tests (through a recipe or a `make` goal) without preparing for them.
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
PHONY_TARGET: typ.Final = ".PHONY"
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


def _runs_tests(command: ScriptCommand) -> bool:
    """Return whether this command runs tests, as `test --no-run` does not."""
    return command.meaning.test is not None and not command.meaning.is_build


def _is_certain(commands: list[ScriptCommand], index: int, end: int) -> bool:
    """Return whether the command at *index* runs on every path to position *end*.

    It runs when it sits in no conditional construct, every command before it
    on the line stops the line when it fails (a command after `||` runs only
    when the one before it failed, and one after `;` does not wait on what came
    before), and every command from it up to *end* does too, so the line is
    not reached at *end* without it.

    Returns
    -------
    bool
        True when the command is certain to run before the line reaches *end*.
    """
    return (
        not commands[index].constructs
        and all(earlier.gates_next for earlier in commands[:index])
        and all(later.gates_next for later in commands[index:end])
    )


class _Reader:
    """Follow the targets of one Makefile, remembering what each one proved."""

    def __init__(
        self, index: dict[str, list[MakeRule]], values: cabc.Mapping[str, str]
    ) -> None:
        """Hold the rule index and the substitutable variables."""
        self.index = index
        self.values = values
        self.active: set[str] = set()
        self.prepared: set[str] = set()
        self.phony = self._phony_names()

    def _phony_names(self) -> frozenset[str]:
        """Return the targets the Makefile declares `.PHONY` in every reading.

        A reference that reads in alternative ways, or that the expansion left
        open, names no target for certain, so it declares none; nor does a
        declaration inside a conditional, which may not be the active one.

        Returns
        -------
        frozenset[str]
            The names every reading of some `.PHONY` reference agrees on.
        """
        names: set[str] = set()
        for rule in self.index.get(PHONY_TARGET, []):
            if rule["conditions"]:
                continue
            for reference in rule["prerequisites"]:
                readings = expand_readings(reference, self.values)
                if len(readings) == 1 and readings[0].is_resolved:
                    names.update(readings[0].text.split())
        return frozenset(names)

    def target_prepares(self, name: str) -> bool:
        """Return whether running target *name* compiles before it tests.

        A target on the walk now is not followed again, which ends a cycle; a
        target that proved it prepares is remembered, so two readings of one
        line reaching it both succeed. A target that is not declared `.PHONY`
        may be skipped when a file of that name exists, so it prepares nothing.

        Returns
        -------
        bool
            True when a recipe or prerequisite of the target builds first.
        """
        if name in self.prepared:
            return True
        if name in self.active or name not in self.phony:
            return False
        self.active.add(name)
        try:
            is_prepared = self.rules_prepare(self.index.get(name, []))
        finally:
            self.active.discard(name)
        if is_prepared:
            self.prepared.add(name)
        return is_prepared

    def _prerequisite_names(self, rule: MakeRule) -> list[str]:
        """Return every settled prerequisite name of *rule*, over all readings."""
        return [
            name
            for reference in rule["prerequisites"]
            for reading in expand_readings(reference, self.values)
            if reading.is_resolved
            for name in reading.text.split()
        ]

    def _plain_prerequisites(self, rule: MakeRule) -> list[str] | None:
        """Return the prerequisite names of *rule* when each reads one plain way.

        A reference that expands to alternative readings, or that the expansion
        left unresolved, is not a form the reader models, so the rule's
        prerequisites are not read at all (None): the reader fails closed.

        Returns
        -------
        list[str] | None
            The names, or None when any reference is not read one plain way.
        """
        names: list[str] = []
        for reference in rule["prerequisites"]:
            readings = expand_readings(reference, self.values)
            if len(readings) != 1 or not readings[0].is_resolved:
                return None
            names.extend(readings[0].text.split())
        return names

    def rules_prepare(self, rules: list[MakeRule]) -> bool:
        """Return whether the rules of one target prepare, read as a unit.

        Make combines the prerequisites of a target's rules, and a conditional
        rule may not be the active one. So the reader accepts only one plain
        rule: several rules for a target, a conditional rule and a double-colon
        rule are not read (the reader fails closed).

        Returns
        -------
        bool
            True when the target has exactly one plain rule and it prepares.
        """
        if len(rules) != 1 or rules[0]["conditions"] or rules[0]["double_colon"]:
            return False
        return self.rule_prepares(rules[0])

    def rule_prepares(self, rule: MakeRule) -> bool:
        """Return whether *rule* compiles before anything of its own tests.

        Make runs a rule's prerequisites before its recipe, but does not order
        one prerequisite before a sibling (`make -j` runs them together). So the
        rule prepares only when every prerequisite that runs tests also
        prepares for them, and then either a prerequisite or an early recipe
        line compiles.

        Returns
        -------
        bool
            True when nothing tests unprepared and something compiles first.
        """
        names = self._plain_prerequisites(rule)
        if names is None or any(
            self._target_tests(name) and not self.target_prepares(name)
            for name in names
        ):
            return False
        return any(self.target_prepares(name) for name in names) or (
            self.recipes_prepare(rule["recipes"])
        )

    def _command_tests(self, command: ScriptCommand, seen: frozenset[str]) -> bool:
        """Return whether a command may run tests, counting any make it cannot read."""
        meaning = command.meaning
        if _runs_tests(command) or meaning.is_unreadable:
            return True
        if not meaning.is_recursive_make:
            return False
        return not meaning.make_goals or any(
            self._target_tests(goal, seen) for goal in meaning.make_goals
        )

    def _target_tests(self, name: str, seen: frozenset[str] = frozenset()) -> bool:
        """Return whether target *name*, or one it needs, may run tests."""
        if name in seen:
            return False
        seen = seen | {name}
        for rule in self.index.get(name, []):
            if any(self._recipe_tests(recipe, seen) for recipe in rule["recipes"]):
                return True
            if self._has_unresolved_prerequisite(rule) or any(
                self._target_tests(needed, seen)
                for needed in self._prerequisite_names(rule)
            ):
                return True
        return False

    def _has_unresolved_prerequisite(self, rule: MakeRule) -> bool:
        """Return whether a prerequisite of *rule* is one the expansion left open.

        What it names is unknown, so it may be a target that runs tests.

        Returns
        -------
        bool
            True when any reading of a prerequisite reference is unresolved.
        """
        return any(
            not reading.is_resolved
            for reference in rule["prerequisites"]
            for reading in expand_readings(reference, self.values)
        )

    def _recipe_tests(self, recipe: MakeRecipe, seen: frozenset[str]) -> bool:
        """Return whether a recipe line may run tests, in any reading."""
        return any(
            self._command_tests(command, seen)
            for commands in _line_commands(recipe, self.values)
            for command in commands
        )

    def recipes_prepare(self, recipes: list[MakeRecipe]) -> bool:
        """Return whether a recipe builds before it tests, or runs a target that does.

        The search stops at the first line that runs tests: a build after it is
        paid inside the timed run.

        Returns
        -------
        bool
            True when a line builds, or runs a preparing target, before a test.
        """
        for recipe in recipes:
            readings = _line_commands(recipe, self.values)
            if self._test_line_prepares(recipe, readings):
                return True
            if self._recipe_tests(recipe, frozenset()):
                return False
        return False

    def test_prepares(self) -> bool:
        """Return whether `test` prepares before the recipe line that tests."""
        return self.rules_prepare(self.index[TEST_TARGET])

    def _test_line_prepares(
        self, recipe: MakeRecipe, readings: list[list[ScriptCommand]]
    ) -> bool:
        """Return whether this line prepares, on every reading, before it tests."""
        if recipe["ignore_errors"] or not readings:
            return False
        return all(self._prepares_before_test(commands) for commands in readings)

    def _first_test(self, commands: list[ScriptCommand]) -> int:
        """Return the position of the first command that may test unprepared.

        A `make` goal that prepares is the preparation, whatever it also tests;
        one that may test without preparing, or that the reader cannot follow,
        is a test boundary, so a build after it is paid inside the timed run.

        Returns
        -------
        int
            The position of that command, or the number of commands when none.
        """
        return next(
            (
                i
                for i, c in enumerate(commands)
                if self._command_tests(c, frozenset())
                and not any(self.target_prepares(g) for g in c.meaning.make_goals)
            ),
            len(commands),
        )

    def _prepares_before_test(self, commands: list[ScriptCommand]) -> bool:
        """Return whether a certain build or preparing target precedes the test."""
        position = self._first_test(commands)
        # A line with no test is a whole line that must succeed: its last
        # command ends the line, so only the ones before it have to gate.
        end = position if position < len(commands) else len(commands) - 1
        certain = [
            command
            for index, command in enumerate(commands[:position])
            if _is_certain(commands, index, end)
        ]
        return any(command.meaning.is_build for command in certain) or any(
            self.target_prepares(goal)
            for command in certain
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
