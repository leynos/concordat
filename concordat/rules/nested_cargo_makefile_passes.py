"""Check that each test pass of a Makefile's `test` target is prepared for itself.

A Makefile that prepares once and then tests twice prepares one pass only. The
second pass builds a different feature set, package selection, target or
`RUSTFLAGS`, so the first pass's build helps it not at all and its nested
builds are cold inside the timed run (rstest-bdd's `--no-default-features` pass
timed out although `--all-features` had been prepared). This module walks the
`test` target in Make's order and reports, for every pass, what warmed it.

A *pass* is an unfiltered `cargo nextest run`, `cargo-nextest nextest run` or
`cargo test` command. A *preparation* is an earlier command whose signature
(see :mod:`.nested_cargo_pass_signature`) equals the pass's and that is certain
to have run first: one that only compiles (`--no-run`, `build`, `nextest
archive`), or a filtered real run (`-E`, `--test`, a test name), which also runs
the nested builds that a compile alone leaves cold (trybuild and macrotest keep
their own directory under the target, which `cargo test --no-run` never fills).
A filtered run is read as preparation and never judged as a pass: the reader
cannot know which tests its filter leaves.

Make orders a rule's prerequisites before its recipe and does not order sibling
prerequisites, so a pass inside one prerequisite sees only what ran before the
rule, never what a sibling did. A preparation counts only where the existing
preparation reader would count it: not in a conditional construct, not after
`||` or `;`, not on an ignored-error line, and in a target declared `.PHONY`.

A recipe line may run a target with `$(MAKE) goal VAR=value`; the walk reads
that target with the override in force, because a repository prepares each pass
by handing the pass's own flags to one parametrized target (rstest-bdd's
`prepare-nested-fixtures PREPARE_FLAGS=...`).

The reader fails closed in the other direction too. A signature it cannot read
fully, a command word the expansion left, or a target it cannot follow is
reported as indeterminate, never as prepared and never as unprepared.
"""

from __future__ import annotations

import dataclasses
import re
import typing as typ

from .makefile_expansion import Assignments, shown, single_assignments
from .makefile_tool_paths import read_tool_paths
from .nested_cargo_make_options import PLAIN_GOAL, unreliable_make
from .nested_cargo_makefile_lines import rules_by_target
from .nested_cargo_makefile_preparation import (
    MakefileReader,
    is_fully_readable,
    is_plain_rule,
    line_commands,
    read_makefile,
)
from .nested_cargo_pass_signature import (
    Action,
    Signature,
    has_unresolved_command_word,
    read_invocation,
)
from .nested_cargo_scripts import TEST_TARGET, command_words

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

    from .makefile_facts import MakeRecipe, MakeRule
    from .nested_cargo_scripts import ScriptCommand

MAKEFILE: typ.Final = "Makefile"
WARMED: typ.Final = "warmed"
COMPILED: typ.Final = "compiled"
UNPREPARED: typ.Final = "unprepared"
INDETERMINATE: typ.Final = "indeterminate"
_SHOWN_COMMAND_LENGTH: typ.Final = 120
_OVERRIDE: typ.Final = "="
_RUNNER: typ.Final = re.compile(
    r"(?<![\w\"'-])cargo(?:-nextest)?\s+(?:\+\S+\s+)?(?:nextest|test|ntr|build|bench)\b"
)


class PassFact(typ.TypedDict):
    """Store one test pass of the Makefile and what prepared it.

    Attributes
    ----------
    path:
        The Makefile's path, `Makefile`.
    line:
        The line of the pass's recipe.
    command:
        The pass's command, shortened for the message.
    status:
        `warmed` (a filtered real run of the same signature came first),
        `compiled` (only a compile of the same signature did), `unprepared`
        (nothing of the same signature did) or `indeterminate` (the reader
        could not tell).
    visible:
        How many preparations of any signature ran before the pass.
    reason:
        For `indeterminate`, why; otherwise empty.
    """

    path: str
    line: int
    command: str
    status: str
    visible: int
    reason: str


@dataclasses.dataclass(frozen=True, slots=True)
class Prepared:
    """Hold one certain preparation and what it builds."""

    signature: Signature
    is_warmed: bool


@dataclasses.dataclass(frozen=True, slots=True)
class MakeCall:
    """Hold the one target a recipe's `make` command runs, and what it overrides."""

    goal: str
    overrides: dict[str, str]


def _shown(text: str) -> str:
    """Return a command from its test runner on, shortened to one readable line.

    The environment prefix a repository builds for each pass can run to several
    hundred characters, so the message names the runner and its arguments.

    Returns
    -------
    str
        The command from its runner word on, at most 120 characters.
    """
    flat = " ".join(shown(text).replace("\\\n", " ").split())
    runner = _RUNNER.search(flat)
    flat = flat[runner.start() :] if runner else flat
    if len(flat) <= _SHOWN_COMMAND_LENGTH:
        return flat
    return flat[: _SHOWN_COMMAND_LENGTH - 3] + "..."


def make_call(text: str) -> MakeCall | None:
    """Read a `make goal [VAR=value ...]` command into the target it runs.

    Only the form the walk models exactly counts: one plain goal and no option
    that diverts, touches, asks or ignores errors. A `VAR=value` word is an
    override for the goal's own recipes, which is how a repository hands each
    pass's flags to one preparation target.

    Returns
    -------
    MakeCall | None
        The goal and its overrides, or None for any other command or shape.
    """
    words = command_words(text)
    if not words or words[0].lstrip("@-+").rsplit("/", 1)[-1] != "make":
        return None
    arguments = words[1:]
    if unreliable_make(arguments):
        return None
    overrides = {
        word.partition(_OVERRIDE)[0]: word.partition(_OVERRIDE)[2]
        for word in arguments
        if _OVERRIDE in word and not word.startswith("-")
    }
    goals = [
        word for word in arguments if not word.startswith("-") and _OVERRIDE not in word
    ]
    if len(goals) != 1 or not PLAIN_GOAL.fullmatch(goals[0]):
        return None
    return MakeCall(goals[0], overrides)


def _overlaid(values: cabc.Mapping[str, str], overrides: dict[str, str]) -> Assignments:
    """Return *values* with the command-line *overrides*, which beat the Makefile."""
    assigned = getattr(values, "assigned", frozenset())
    return Assignments({**values, **overrides}, [*assigned, *overrides])


_OPENS_CONDITIONAL: typ.Final = frozenset({
    "if",
    "while",
    "until",
    "for",
    "case",
    "select",
})
_CLOSES_CONDITIONAL: typ.Final = frozenset({"fi", "done", "esac"})
_BRANCH_WORDS: typ.Final = frozenset({"else", "elif"})
type Path = tuple[tuple[int, int], ...]


def _first_word(command: ScriptCommand) -> str:
    """Return the first word of a command, past Make's prefixes and continuations."""
    for word in command.text.replace("\\\n", " ").split():
        stripped = word.lstrip("@-+")
        if stripped:
            return stripped
    return ""


class _Structure:
    """Hold where each command of a line sits among its `if` branches.

    A command inside `if probe; then A; else B; fi` runs on only one branch,
    so it is not certain to have run. Both branches doing the same thing is a
    preparation all the same: the probe picks the runner (`cargo nextest run`
    or `cargo test`), not what is built. `paths` gives each command's chain of
    (group, branch) pairs, `spans` the first and last command of each group,
    and `kinds` records which groups are an `if` that ends in an `else`.
    """

    def __init__(self, commands: list[ScriptCommand]) -> None:
        """Index the groups and branches of the line's commands."""
        self.commands = commands
        self.paths: list[Path] = []
        self.spans: dict[int, tuple[int, int]] = {}
        self.is_two_way: set[int] = set()
        self.branches: dict[int, set[int]] = {}
        stack: list[list[int]] = []
        kinds: dict[int, str] = {}
        for index, command in enumerate(commands):
            word = _first_word(command)
            self._step(index, word, stack, kinds)

    def _path(self, stack: list[list[int]]) -> Path:
        """Return the current chain of (group, branch) pairs."""
        return tuple((group, branch) for group, branch in stack)

    def _step(
        self, index: int, word: str, stack: list[list[int]], kinds: dict[int, str]
    ) -> None:
        """Place one command, opening, switching or closing a group as it reads."""
        if word in _BRANCH_WORDS and stack:
            stack[-1][1] += 1
            if word == "else" and kinds[stack[-1][0]] == "if":
                self.is_two_way.add(stack[-1][0])
        if word in _CLOSES_CONDITIONAL and stack:
            group, _branch = stack.pop()
            self.spans[group] = (self.spans[group][0], index)
        self.paths.append(self._path(stack))
        for group, branch in stack:
            self.branches.setdefault(group, set()).add(branch)
        if word in _OPENS_CONDITIONAL:
            group = len(kinds)
            kinds[group] = word
            self.spans[group] = (index, index)
            stack.append([group, 0])

    def _gates(self, start: int, end: int) -> bool:
        """Return whether every command in [start, end) stops the line on failure."""
        return all(command.gates_next for command in self.commands[start:end])

    def _branch_start(self, path: Path) -> int:
        """Return the first command of the branch context *path* names."""
        return next((i for i, other in enumerate(self.paths) if other == path), 0)

    def is_certain(
        self,
        item: Prepared,
        produced: list[tuple[int, list[Prepared]]],
        origin: int,
        end: int,
        end_path: Path,
    ) -> bool:
        """Return whether *item*, prepared by command *origin*, precedes *end*.

        Returns
        -------
        bool
            True when the item runs on every path to the command at *end*
            (whose path is *end_path*): in the same branch context with every
            command between gating, or lifted out of a two-way `if` whose every
            branch prepared the same item.
        """
        path = self.paths[origin]
        if end_path[: len(path)] == path:
            lo = self._branch_start(path) if path else 0
            return self._gates(lo, end)
        group = path[len(end_path)][0] if path[: len(end_path)] == end_path else None
        if (
            group is None
            or len(path) != len(end_path) + 1
            or group not in self.is_two_way
        ):
            return False
        branches = self.branches.get(group, set())
        every_branch = all(
            any(
                item in found
                and self.paths[other]
                and self.paths[other][-1] == (group, branch)
                for other, found in produced
            )
            for branch in branches
        )
        first, last = self.spans[group]
        lo = self._branch_start(end_path) if end_path else 0
        return every_branch and self._gates(lo, first) and self._gates(last, end)

    def certain_before(
        self,
        produced: list[tuple[int, list[Prepared]]],
        end: int,
        end_path: Path,
    ) -> list[Prepared]:
        """Return what the earlier commands prepared that is sure to precede *end*."""
        return list(
            dict.fromkeys(
                item
                for origin, found in produced
                for item in found
                if self.is_certain(item, produced, origin, end, end_path)
            )
        )


class _Walk:
    """Walk the `test` target in Make's order, collecting the passes it finds."""

    def __init__(self, index: dict[str, list[MakeRule]]) -> None:
        """Hold the rule index the walk follows targets through."""
        self.index = index
        self.facts: list[PassFact] = []
        self.active: set[str] = set()

    def _fact(
        self,
        recipe: MakeRecipe,
        command: str,
        status: str,
        visible: int,
        reason: str,
    ) -> None:
        """Record one pass, once however many readings of its line agree."""
        fact: PassFact = {
            "path": MAKEFILE,
            "line": recipe["location"]["start_line"],
            "command": _shown(command),
            "status": status,
            "visible": visible,
            "reason": reason,
        }
        if fact not in self.facts:
            self.facts.append(fact)

    def target(
        self, name: str, visible: tuple[Prepared, ...], reader: MakefileReader
    ) -> list[Prepared]:
        """Walk target *name*, returning the preparations it certainly ran.

        A target that is not `.PHONY` may be skipped when a file of its name
        exists, so what it prepared is not counted; its passes are still read.

        Returns
        -------
        list[Prepared]
            The preparations every run of the target performs.
        """
        rules = self.index.get(name, [])
        if name in self.active or not rules:
            return []
        self.active.add(name)
        try:
            produced = self._rules(name, rules, visible, reader)
        finally:
            self.active.discard(name)
        is_counted = name in reader.phony or name == TEST_TARGET
        return produced if is_counted else []

    def _unreadable(
        self, name: str, rules: list[MakeRule], reason: str, reader: MakefileReader
    ) -> list[Prepared]:
        """Report a target the walk cannot follow when it may run tests."""
        if reader.target_tests(name):
            self.facts.append({
                "path": MAKEFILE,
                "line": rules[0]["location"]["start_line"],
                "command": name,
                "status": INDETERMINATE,
                "visible": 0,
                "reason": reason,
            })
        return []

    def _rules(
        self,
        name: str,
        rules: list[MakeRule],
        visible: tuple[Prepared, ...],
        reader: MakefileReader,
    ) -> list[Prepared]:
        """Walk the one plain rule of a target, or report that it is not read."""
        if len(rules) != 1 or not is_plain_rule(rules[0]):
            reason = f"target {name} has several or conditional rules"
            return self._unreadable(name, rules, reason, reader)
        rule = rules[0]
        names = reader.plain_prerequisites(rule)
        if names is None:
            reason = f"a prerequisite of {name} is not a plain name"
            return self._unreadable(name, rules, reason, reader)
        # Siblings are unordered: each prerequisite sees only what ran before
        # the rule, and the recipe sees all of them.
        produced: list[Prepared] = []
        for needed in names:
            produced.extend(self.target(needed, visible, reader))
        for recipe in rule["recipes"]:
            produced.extend(self._line(recipe, (*visible, *produced), reader))
        return produced

    def _line(
        self,
        recipe: MakeRecipe,
        visible: tuple[Prepared, ...],
        reader: MakefileReader,
    ) -> list[Prepared]:
        """Walk one recipe line, returning what it certainly prepared.

        A line that reads two ways (a probe for the runner) prepares only what
        both readings prepare.

        Returns
        -------
        list[Prepared]
            The preparations common to every reading, none on an ignored line.
        """
        readings = [
            self._commands(recipe, commands, visible, reader)
            for commands in line_commands(recipe, reader.values)
        ]
        if not readings or recipe["ignore_errors"]:
            return []
        first, *others = readings
        return [item for item in first if all(item in other for other in others)]

    def _commands(
        self,
        recipe: MakeRecipe,
        commands: list[ScriptCommand],
        visible: tuple[Prepared, ...],
        reader: MakefileReader,
    ) -> list[Prepared]:
        """Walk the commands of one reading, judging each pass as it comes.

        Returns
        -------
        list[Prepared]
            What the line certainly prepared once it succeeds: a command's
            preparation counts only when it is not inside a conditional
            construct and every command from it to the end of the line stops
            the line when it fails.
        """
        structure = _Structure(commands)
        produced: list[tuple[int, list[Prepared]]] = []
        for index, command in enumerate(commands):
            seen = (
                *visible,
                *structure.certain_before(produced, index, structure.paths[index]),
            )
            found = self._command(recipe, command, seen, reader)
            if found:
                produced.append((index, found))
        return structure.certain_before(produced, len(commands) - 1, ())

    def _command(
        self,
        recipe: MakeRecipe,
        command: ScriptCommand,
        seen: tuple[Prepared, ...],
        reader: MakefileReader,
    ) -> list[Prepared]:
        """Judge one command if it is a pass, and return what it prepares."""
        call = make_call(command.text)
        if call is not None:
            nested = MakefileReader(
                self.index, _overlaid(reader.values, call.overrides)
            )
            return self.target(call.goal, seen, nested)
        invocation = read_invocation(command.text)
        if invocation is None:
            if has_unresolved_command_word(command.text):
                reason = (
                    "the command word is a Make reference the reader cannot resolve"
                )
                self._fact(recipe, command.text, INDETERMINATE, len(seen), reason)
            return []
        if invocation.action is Action.COMPILE:
            return [Prepared(invocation.signature, is_warmed=False)]
        if invocation.is_filtered:
            return [Prepared(invocation.signature, is_warmed=True)]
        self._judge(recipe, command.text, invocation.signature, seen)
        return []

    def _judge(
        self,
        recipe: MakeRecipe,
        text: str,
        signature: Signature,
        seen: tuple[Prepared, ...],
    ) -> None:
        """Record what, among *seen*, prepared a pass of this signature."""
        matching = [item for item in seen if item.signature == signature]
        if any(item.is_warmed for item in matching):
            status, reason = WARMED, ""
        elif matching:
            status, reason = COMPILED, ""
        elif not signature.is_resolved or any(
            not item.signature.is_resolved for item in seen
        ):
            status = INDETERMINATE
            reason = (
                "an option or Make reference the reader cannot read is in a signature"
            )
        else:
            status, reason = UNPREPARED, ""
        self._fact(recipe, text, status, len(seen), reason)


def makefile_passes(checkout: pathlib.Path) -> list[PassFact]:
    """Return every test pass of the Makefile's `test` target and its preparation.

    Returns
    -------
    list[PassFact]
        One fact per pass, in walk order. Empty when there is no Makefile, it is
        refused by the parser, or it has no `test` target; one indeterminate
        fact when the Makefile holds a construct that changes what its recipes
        do (`.ONESHELL`, an include, an expanded target name).
    """
    read = read_makefile(checkout)
    if read is None:
        return []
    report, text = read
    index = rules_by_target(report)
    if TEST_TARGET not in index:
        return []
    values = read_tool_paths(single_assignments(report["variables"]))
    if not is_fully_readable(report, index, text, values):
        return [
            {
                "path": MAKEFILE,
                "line": index[TEST_TARGET][0]["location"]["start_line"],
                "command": TEST_TARGET,
                "status": INDETERMINATE,
                "visible": 0,
                "reason": "the Makefile holds a construct the reader does not follow",
            }
        ]
    walk = _Walk(index)
    walk.target(TEST_TARGET, (), MakefileReader(index, values))
    return walk.facts
