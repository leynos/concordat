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
cannot know which tests its filter leaves. A run that selects only `--lib`,
`--doc`, `--bin`, `--example` or `--bench` never reaches the integration tests
that hold those builds, so it counts as a compile.

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

from .makefile_expansion import shown, single_assignments
from .makefile_tool_paths import read_tool_paths
from .nested_cargo_make_calls import MakeCall, make_call, overlaid
from .nested_cargo_makefile_lines import rules_by_target
from .nested_cargo_makefile_names import (
    fallback_lines,
    named_variables,
    pattern_lines,
    settled_index,
    unsafe_names,
)
from .nested_cargo_makefile_preparation import (
    MakefileReader,
    includes_a_file,
    is_fully_readable,
    is_plain_rule,
    line_commands,
    read_makefile,
)
from .nested_cargo_pass_signature import (
    Action,
    Invocation,
    Signature,
    read_invocation,
)
from .nested_cargo_pass_words import has_unresolved_command_word
from .nested_cargo_scripts import TEST_TARGET

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

    from .makefile_facts import MakeRecipe, MakeRule, MakeutilReport
    from .nested_cargo_scripts import ScriptCommand

MAKEFILE: typ.Final = "Makefile"
WARMED: typ.Final = "warmed"
COMPILED: typ.Final = "compiled"
UNPREPARED: typ.Final = "unprepared"
INDETERMINATE: typ.Final = "indeterminate"
_SHOWN_COMMAND_LENGTH: typ.Final = 120
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
    packages:
        The `-p` selections of the pass.
    runner:
        `cargo` or `nextest`: a nextest setup script runs before a nextest pass
        and never before a plain `cargo test`.
    warmed_tests:
        For `warmed`, the integration-test targets (`--test NAME`) the warming
        runs were restricted to; empty when a warming run was not restricted to
        named targets.
    """

    path: str
    line: int
    command: str
    status: str
    visible: int
    reason: str
    packages: typ.NotRequired[list[str]]
    runner: typ.NotRequired[str]
    warmed_tests: typ.NotRequired[list[str]]


@dataclasses.dataclass(frozen=True, slots=True)
class Prepared:
    """Hold one certain preparation and what it builds."""

    signature: Signature
    is_warmed: bool
    targets: tuple[str, ...] = ()


class _Verdict(typ.NamedTuple):
    """Hold what the walk found out about one pass."""

    status: str
    visible: int
    reason: str = ""
    packages: tuple[str, ...] = ()
    runner: str = "nextest"
    warmed_tests: tuple[str, ...] = ()


_UNRESOLVED_WORD: typ.Final = (
    "the command word is a Make reference the reader cannot resolve"
)
_UNFOLLOWED: typ.Final = "the command may run tests and the reader cannot follow it"
_HIDDEN_TEST_REASON: typ.Final = (
    "a target name the reader cannot settle may define `test`"
)
_INCLUDE_REASON: typ.Final = "an included file may define `test`"
_PATTERN_REASON: typ.Final = "a pattern rule may supply `test`"
_FALLBACK_REASON: typ.Final = "a `.DEFAULT` rule or a generated rule may supply `test`"
_OVERRIDE_REASON: typ.Final = (
    "a make override may change which target an expanded name defines"
)
_IMPLICIT_REASON: typ.Final = (
    "an unread command or implicit rule may have prepared the build earlier"
)
_UNREADABLE_SIGNATURE: typ.Final = (
    "an option or Make reference the reader cannot read is in a signature"
)


def _unreadable_reason(command: ScriptCommand, reader: MakefileReader) -> str | None:
    """Return why a command the reader cannot read matters to the walk, or None."""
    if has_unresolved_command_word(command.text):
        return _UNRESOLVED_WORD
    if reader.command_tests(command, frozenset()):
        return _UNFOLLOWED
    return None


def _status(signature: Signature, seen: tuple[Prepared, ...]) -> tuple[str, str]:
    """Return what, among *seen*, prepared a pass of *signature*, and why if unsure."""
    matching = [item for item in seen if item.signature == signature]
    if any(item.is_warmed for item in matching):
        return WARMED, ""
    if matching:
        return COMPILED, ""
    if _is_unsure(signature, seen):
        return INDETERMINATE, _UNREADABLE_SIGNATURE
    return UNPREPARED, ""


def _warmed_tests(signature: Signature, seen: tuple[Prepared, ...]) -> tuple[str, ...]:
    """Return the test targets the warming runs of *signature* were limited to.

    Empty when any warming run was not restricted to named targets: it may have
    run every test, so no target is ruled out.

    Returns
    -------
    tuple[str, ...]
        The sorted target names, or none when no target is ruled out.
    """
    runs = [i.targets for i in seen if i.signature == signature and i.is_warmed]
    if not all(runs):
        return ()
    return tuple(sorted({name for targets in runs for name in targets}))


def _is_unsure(signature: Signature, seen: tuple[Prepared, ...]) -> bool:
    """Return whether the pass or anything before it carries an unreadable word."""
    return not signature.is_resolved or any(
        not item.signature.is_resolved for item in seen
    )


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
type Produced = list[tuple[int, list[Prepared]]]


class _Site(typ.NamedTuple):
    """Hold the command a preparation must precede: its index and branch path."""

    end: int
    path: Path


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

    def _step(
        self, index: int, word: str, stack: list[list[int]], kinds: dict[int, str]
    ) -> None:
        """Place one command, opening, switching or closing a group as it reads."""
        self._switch(word, stack, kinds)
        self._close(index, word, stack)
        self.paths.append(tuple((group, branch) for group, branch in stack))
        for group, branch in stack:
            self.branches.setdefault(group, set()).add(branch)
        self._open(index, word, stack, kinds)

    def _switch(self, word: str, stack: list[list[int]], kinds: dict[int, str]) -> None:
        """Move to the next branch of the open group on `else` or `elif`."""
        if word not in _BRANCH_WORDS or not stack:
            return
        stack[-1][1] += 1
        if word == "else" and kinds[stack[-1][0]] == "if":
            self.is_two_way.add(stack[-1][0])

    def _close(self, index: int, word: str, stack: list[list[int]]) -> None:
        """End the open group on `fi`, `done` or `esac`."""
        if word not in _CLOSES_CONDITIONAL or not stack:
            return
        group, _branch = stack.pop()
        self.spans[group] = (self.spans[group][0], index)

    def _open(
        self, index: int, word: str, stack: list[list[int]], kinds: dict[int, str]
    ) -> None:
        """Open a group on `if`, a loop or `case`."""
        if word not in _OPENS_CONDITIONAL:
            return
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

    def _context_start(self, path: Path) -> int:
        """Return where the context *path* begins; the whole line when it is empty."""
        return self._branch_start(path) if path else 0

    def _context_last(self, path: Path) -> int:
        """Return the last command inside the context *path*, nested groups included."""
        inside = [i for i, other in enumerate(self.paths) if other[: len(path)] == path]
        return inside[-1]

    def is_certain(
        self, item: Prepared, produced: Produced, origin: int, site: _Site
    ) -> bool:
        """Return whether *item*, prepared by command *origin*, precedes *site*.

        Returns
        -------
        bool
            True when the item runs on every path to the command at the site:
            in the same branch context with every command between gating, or
            lifted out of a two-way `if` whose every branch prepared the same
            item.
        """
        path = self.paths[origin]
        if site.path[: len(path)] == path:
            return self._gates(self._context_start(path), site.end)
        group = self._lifted_group(path, site.path)
        if group is None:
            return False
        return self._every_branch(group, item, produced) and self._gates_around(
            group, site
        )

    def _lifted_group(self, path: Path, end_path: Path) -> int | None:
        """Return the two-way `if` that *path* sits one level inside, if any."""
        depth = len(end_path)
        if len(path) != depth + 1 or path[:depth] != end_path:
            return None
        group = path[depth][0]
        return group if group in self.is_two_way else None

    def _every_branch(self, group: int, item: Prepared, produced: Produced) -> bool:
        """Return whether every branch of *group* certainly prepared *item*."""
        return all(
            self._branch_made((group, branch), item, produced)
            for branch in self.branches.get(group, set())
        )

    def _branch_made(
        self, here: tuple[int, int], item: Prepared, produced: Produced
    ) -> bool:
        """Return whether the branch *here* certainly prepared *item*."""
        return any(
            item in found and self._runs_in_branch(origin, here)
            for origin, found in produced
        )

    def _runs_in_branch(self, origin: int, here: tuple[int, int]) -> bool:
        """Return whether command *origin* sits in branch *here* and certainly runs.

        Every command of the branch before its last must stop the branch on
        failure, or an earlier `false && cargo ...` would skip the preparation
        and the branch would still succeed.

        Returns
        -------
        bool
            True when the command is in the branch and every command before the
            branch's last gates.
        """
        path = self.paths[origin]
        if not path or path[-1] != here:
            return False
        return self._gates(self._branch_start(path), self._context_last(path))

    def _gates_around(self, group: int, site: _Site) -> bool:
        """Return whether commands around the group's span stop the line on failure."""
        first, last = self.spans[group]
        before = self._context_start(site.path)
        return self._gates(before, first) and self._gates(last, site.end)

    def certain_before(self, produced: Produced, site: _Site) -> list[Prepared]:
        """Return what the earlier commands prepared that is sure to precede *site*."""
        return list(
            dict.fromkeys(
                item
                for origin, found in produced
                for item in found
                if self.is_certain(item, produced, origin, site)
            )
        )


class _Walk:
    """Walk the `test` target in Make's order, collecting the passes it finds."""

    def __init__(
        self,
        index: dict[str, list[MakeRule]],
        values: cabc.Mapping[str, str],
        text: str,
    ) -> None:
        """Hold the rule index the walk follows targets through, and its text."""
        self.index = index
        self.text = text
        self.uncertain = False  # an unread implicit rule may have prepared
        self.facts: list[PassFact] = []
        self.active: set[str] = set()
        self.named_variables = named_variables(index, values)

    def _fact(self, recipe: MakeRecipe, command: str, verdict: _Verdict) -> None:
        """Record one pass, once however many readings of its line agree."""
        fact: PassFact = {
            "path": MAKEFILE,
            "line": recipe["location"]["start_line"],
            "command": _shown(command),
            "status": verdict.status,
            "visible": verdict.visible,
            "reason": verdict.reason,
            "packages": list(verdict.packages),
            "runner": verdict.runner,
            "warmed_tests": list(verdict.warmed_tests),
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
        if name in self.active:
            return []
        self._note_implicit_rules(name, rules, reader)
        if not rules:
            return []
        self.active.add(name)
        try:
            produced = self._rules(name, rules, visible, reader)
        finally:
            self.active.discard(name)
        is_counted = name in reader.phony or name == TEST_TARGET
        return produced if is_counted else []

    def _note_implicit_rules(
        self, name: str, rules: list[MakeRule], reader: MakefileReader
    ) -> None:
        """Report a recipe-less target that a pattern or fallback rule may fill.

        Make searches for an implicit rule for a target whose rules hold no
        recipe, unless the target is declared `.PHONY`, so `test:` beside `te%:`
        runs the pattern recipe when no `test` file exists, and so does a
        prerequisite such as `integration:` beside `int%:`.
        """
        if any(rule["recipes"] for rule in rules) or name in reader.phony:
            return
        # A name no rule mentions is a file or a pattern target; only a pattern
        # that matches it is worth reporting, or every source file would be.
        candidates = [(pattern_lines(self.index, name), _PATTERN_REASON)]
        if rules:
            candidates.append((fallback_lines(self.index, self.text), _FALLBACK_REASON))
        for lines, reason in candidates:
            if lines:
                self.uncertain = True
                self.facts.append({
                    "path": MAKEFILE,
                    "line": min(lines),
                    "command": name,
                    "status": INDETERMINATE,
                    "visible": 0,
                    "reason": reason.replace("`test`", f"`{name}`"),
                })

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
        produced: Produced = []
        for index, command in enumerate(commands):
            site = _Site(index, structure.paths[index])
            seen = (*visible, *structure.certain_before(produced, site))
            found = self._command(recipe, command, seen, reader)
            if found:
                produced.append((index, found))
        return structure.certain_before(produced, _Site(len(commands) - 1, ()))

    def _command(
        self,
        recipe: MakeRecipe,
        command: ScriptCommand,
        seen: tuple[Prepared, ...],
        reader: MakefileReader,
    ) -> list[Prepared]:
        """Judge one command if it is a pass, and return what it prepares."""
        call = make_call(command.text)
        if call is not None and self.named_variables & call.overrides.keys():
            # An override can change which target an expanded name defines, and
            # the index was built without it, so such a call is not followed.
            return self._unread(recipe, command, seen, _OVERRIDE_REASON)
        if call is not None:
            return self._follow(call, seen, reader)
        invocation = read_invocation(command.text)
        if invocation is None:
            reason = _unreadable_reason(command, reader)
            return self._unread(recipe, command, seen, reason)
        return self._invoked(recipe, command.text, invocation, seen)

    def _follow(
        self, call: MakeCall, seen: tuple[Prepared, ...], reader: MakefileReader
    ) -> list[Prepared]:
        """Follow a `make goal [VAR=value]` command into the goal's recipes.

        Returns
        -------
        list[Prepared]
            What the goal certainly prepared.
        """
        nested = MakefileReader(self.index, overlaid(reader.values, call.overrides))
        return self.target(call.goal, seen, nested)

    def _unread(
        self,
        recipe: MakeRecipe,
        command: ScriptCommand,
        seen: tuple[Prepared, ...],
        reason: str | None,
    ) -> list[Prepared]:
        """Report a command the reader cannot read when it may run or prepare tests.

        Returns
        -------
        list[Prepared]
            A placeholder preparation with an unresolved signature, so that no
            later pass is called unprepared: the command may have built it.
            Empty for a command that cannot touch the tests.
        """
        if reason is None:
            return []
        self.uncertain = True
        verdict = _Verdict(INDETERMINATE, len(seen), reason)
        self._fact(recipe, command.text, verdict)
        return [Prepared(Signature(opaque=(command.text,)), is_warmed=False)]

    def _invoked(
        self,
        recipe: MakeRecipe,
        text: str,
        invocation: Invocation,
        seen: tuple[Prepared, ...],
    ) -> list[Prepared]:
        """Judge a test or build command: it prepares, or it is a pass."""
        if invocation.action is Action.COMPILE:
            return [Prepared(invocation.signature, is_warmed=False)]
        if invocation.is_filtered:
            # A filtered run warms the nested builds only when it reaches the
            # integration tests that hold them; `--lib` or `--doc` compile
            # what they run and leave those builds cold.
            return [
                Prepared(
                    invocation.signature,
                    invocation.warms_nested,
                    invocation.test_targets,
                )
            ]
        self._judge(recipe, text, invocation, seen)
        return []

    def _is_open(self, status: str, tests: tuple[str, ...]) -> bool:
        """Return whether an unread implicit recipe may change this verdict."""
        if not self.uncertain:
            return False
        return status in {UNPREPARED, COMPILED} or bool(tests)

    def _judge(
        self,
        recipe: MakeRecipe,
        text: str,
        invocation: Invocation,
        seen: tuple[Prepared, ...],
    ) -> None:
        """Record what, among *seen*, prepared a pass of this invocation."""
        signature = invocation.signature
        status, reason = _status(signature, seen)
        tests = _warmed_tests(signature, seen) if status == WARMED else ()
        if self._is_open(status, tests):
            status, reason, tests = INDETERMINATE, _IMPLICIT_REASON, ()
        verdict = _Verdict(
            status,
            len(seen),
            reason,
            signature.packages,
            invocation.runner,
            tests,
        )
        self._fact(recipe, text, verdict)


def _unreadable_fact(line: int, reason: str) -> PassFact:
    """Return the indeterminate fact for a `test` target the reader cannot follow."""
    return {
        "path": MAKEFILE,
        "line": line,
        "command": TEST_TARGET,
        "status": INDETERMINATE,
        "visible": 0,
        "reason": reason,
    }


def _hidden_test_facts(
    index: dict[str, list[MakeRule]],
    values: cabc.Mapping[str, str],
    report: MakeutilReport,
    text: str,
) -> list[PassFact]:
    """Return indeterminate facts for what may define `test` where it is not seen.

    A target built by expansion that does not settle one way, or reads a variable
    assigned after its rule, can define `test`, and so can a pattern rule that
    matches it and an included file, so a Makefile without a literal `test`
    target is not known to have no test pass.

    Returns
    -------
    list[PassFact]
        One indeterminate fact for each of those that is present, at its first
        line, or none.
    """
    candidates = [
        (unsafe_names(index, values, report["variables"]), _HIDDEN_TEST_REASON),
        (pattern_lines(index, TEST_TARGET), _PATTERN_REASON),
        (fallback_lines(index, text), _FALLBACK_REASON),
        (_include_lines(report, text), _INCLUDE_REASON),
    ]
    return [
        _unreadable_fact(min(lines), reason) for lines, reason in candidates if lines
    ]


def _include_lines(report: MakeutilReport, text: str) -> list[int]:
    """Return the lines of the includes, or line 1 when only the text shows one."""
    if not includes_a_file(report, text):
        return []
    found = [
        item.get("location", {}).get("start_line", 1) for item in report["includes"]
    ]
    return found or [1]


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
    values = read_tool_paths(single_assignments(report["variables"]))
    index = settled_index(rules_by_target(report), values)
    if TEST_TARGET not in index:
        return _hidden_test_facts(index, values, report, text)
    if not is_fully_readable(report, index, text, values):
        line = index[TEST_TARGET][0]["location"]["start_line"]
        return [
            _unreadable_fact(
                line, "the Makefile holds a construct the reader does not follow"
            )
        ]
    walk = _Walk(index, values, text)
    walk.target(TEST_TARGET, (), MakefileReader(index, values))
    return walk.facts
