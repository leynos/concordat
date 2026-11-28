"""Resolve the warm-up a builder's declaration names.

A declaration says which warm-up pays for a build (`make:<goal>`,
`nextest-setup` or `test:<name>`). The reader checks that the warm-up exists and
runs before the tests, without running anything:

- `make:<goal>` resolves when `test` depends on the goal, transitively, or when
  a recipe line of `test` (or of a goal it runs) calls `make <goal>` *before*
  the line runs a test command. A goal reached only after the tests have started
  does not warm them. A prerequisite written with a variable, or an included
  Makefile, may name the goal, so a goal not found then is unreadable and not
  unresolved.
- `nextest-setup` resolves when a setup rule's filter selects the declaration's
  test binary or its owning package.
- `test:<name>` resolves when a function of that name is declared in a Rust
  source of the checkout.
"""

from __future__ import annotations

import dataclasses
import re
import typing as typ

from .nested_cargo_gates import read_gates
from .nested_cargo_makefile_lines import rules_by_target
from .nested_cargo_makefile_preparation import includes_a_file, read_makefile
from .nested_cargo_preparation import (
    Preparation,
    crate_directories,
    declared_tests,
    named_test_crates,
    owning_crate,
)
from .nested_cargo_reach import UNSEEN
from .nested_cargo_scripts import TEST_TARGET
from .nested_cargo_setup_scripts import covers, read_setup_rules
from .nested_cargo_source import RustSource, read_sources
from .nested_cargo_spawns import fixture_directories

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

    from .makefile_facts import MakeRecipe, MakeRule

COVERAGE_RESOLVED: typ.Final = "resolved"
COVERAGE_UNRESOLVED: typ.Final = "unresolved"
COVERAGE_UNREADABLE: typ.Final = "unreadable"

SETUP_FORM: typ.Final = "nextest-setup"
MAKE_PREFIX: typ.Final = "make:"
TEST_PREFIX: typ.Final = "test:"

# A `make` call is a command word that always runs: first on the line (after the
# `@`, `-` and `+` recipe prefixes) or after `&&`, `;` or `(`, never after `||`
# (which runs only when the command before it failed) and never inside a quoted
# string.
_MAKE_CALL: typ.Final = re.compile(
    r"(?:^[@+-]*|&&|;|\()\s*(?:\$\(MAKE\)|make)\s+(?P<goal>[\w.-]+)"
)
_TEST_COMMAND: typ.Final = re.compile(r"\b(?:cargo|nextest|pytest)\b")
_FUNCTION: typ.Final = re.compile(r"\bfn\s+(?P<name>[A-Za-z_]\w*)")
_TEST_BINARY: typ.Final = re.compile(
    r"(?:^|/)tests/(?P<name>[^/.]+)(?:\.rs|/main\.rs)$"
)


@dataclasses.dataclass(frozen=True, slots=True)
class MakeGoals:
    """Hold the goals that run before the tests, and whether more may hide.

    Attributes
    ----------
    goals:
        Every goal `test` depends on, or calls before it runs a test command.
    is_opaque:
        True when a variable prerequisite or an included file may name more.
    is_absent:
        True when the checkout has no Makefile at all.
    """

    goals: frozenset[str]
    is_opaque: bool
    is_absent: bool


@dataclasses.dataclass(frozen=True, slots=True)
class Warmups:
    """Hold what the repository offers as warm-ups, read once."""

    make: MakeGoals
    functions: frozenset[str]
    reached_by: cabc.Mapping[str, list[str]]
    preparation: Preparation


def _calls_before_tests(recipes: cabc.Iterable[MakeRecipe]) -> list[str]:
    """Return the goals a recipe calls through `make` before its first test line.

    Returns
    -------
    list[str]
        The goals, in order; a line that runs a test command ends the scan after
        the part of it that comes first.
    """
    found: list[str] = []
    for recipe in recipes:
        text = recipe["text"]
        runs = _TEST_COMMAND.search(text)
        limit = runs.start() if runs else len(text)
        found.extend(
            call.group("goal")
            for call in _MAKE_CALL.finditer(text[:limit])
            if not _is_quoted(text, call.start())
        )
        if runs:
            break
    return found


def _is_quoted(text: str, offset: int) -> bool:
    """Report whether *offset* lies inside a quoted string of a recipe line."""
    before = text[:offset]
    return before.count('"') % 2 == 1 or before.count("'") % 2 == 1


def _next_goals(rule: MakeRule) -> tuple[list[str], bool]:
    """Return the goals one rule leads to, and whether a prerequisite hid.

    Returns
    -------
    tuple[list[str], bool]
        The plain prerequisites and the goals the recipe calls first, then
        whether a prerequisite names a variable the reader cannot resolve.
    """
    plain = [name for name in rule["prerequisites"] if "$" not in name]
    hides = len(plain) != len(rule["prerequisites"])
    return [*plain, *_calls_before_tests(rule["recipes"])], hides


def _reachable_goals(index: dict[str, list[MakeRule]]) -> tuple[set[str], bool]:
    """Walk from `test` through prerequisites and early `make` calls.

    Returns
    -------
    tuple[set[str], bool]
        Every goal reached, and whether a prerequisite hid behind a variable.
    """
    goals: set[str] = set()
    hides = False
    pending, walked = [TEST_TARGET], set[str]()
    while pending:
        target = pending.pop()
        if target in walked:
            continue
        walked.add(target)
        for rule in index.get(target, []):
            names, hid = _next_goals(rule)
            hides = hides or hid
            goals.update(names)
            pending.extend(names)
    return goals - {TEST_TARGET}, hides


def _make_goals(checkout: pathlib.Path) -> MakeGoals:
    """Return the goals that run before or inside the Makefile's `test` target.

    Returns
    -------
    MakeGoals
        The goals, whether the Makefile may hide more, and whether it is absent.
    """
    read = read_makefile(checkout)
    if read is None:
        absent = not (checkout / "Makefile").exists()
        return MakeGoals(frozenset(), is_opaque=True, is_absent=absent)
    report, text = read
    goals, hides = _reachable_goals(rules_by_target(report))
    is_opaque = hides or includes_a_file(report, text)
    return MakeGoals(frozenset(goals), is_opaque, is_absent=False)


def _function_names(sources: cabc.Iterable[RustSource]) -> frozenset[str]:
    """Return the names of every function a Rust source declares."""
    return frozenset(
        match.group("name")
        for source in sources
        for match in _FUNCTION.finditer(source.masked)
    )


def read_warmups(
    checkout: pathlib.Path,
    manifests: cabc.Mapping[pathlib.Path, typ.Any],
    reached_by: cabc.Mapping[str, list[str]],
    problems: list[str],
) -> Warmups:
    """Read every warm-up a declaration may name.

    Returns
    -------
    Warmups
        The Makefile goals, the declared functions, the reach of each file and
        the preparation the setup rules belong to.
    """
    fixtures = fixture_directories(checkout, manifests)
    preparation = Preparation(
        crate_directories(checkout, manifests),
        read_setup_rules(checkout, problems),
        read_gates(None),
        is_makefile_prepared=False,
        named_test_crates=named_test_crates(checkout, manifests),
        declared_tests=declared_tests(checkout, manifests),
    )
    return Warmups(
        _make_goals(checkout),
        _function_names(read_sources(checkout, fixtures, [])),
        reached_by,
        preparation,
    )


def _make_coverage(goal: str, make: MakeGoals) -> str:
    """Say whether *goal* runs before the tests.

    Returns
    -------
    str
        `resolved`, `unresolved` or `unreadable`.
    """
    if goal in make.goals:
        return COVERAGE_RESOLVED
    return (
        COVERAGE_UNRESOLVED
        if make.is_absent or not make.is_opaque
        else COVERAGE_UNREADABLE
    )


def _binary_names(root: str, preparation: Preparation) -> list[str | None]:
    """Return the test binaries compiled from the target root *root*.

    A `[[test]]` table names its binary, and several tables may share a source,
    so each name counts. Otherwise Cargo names `tests/<name>.rs` and
    `tests/<name>/main.rs` after `<name>`; any other root has no binary name.

    Returns
    -------
    list[str | None]
        The binary names, or a single None for a root with no binary name.
    """
    declared = preparation.declared_tests.get(root)
    if declared:
        return list[str | None](sorted(declared))
    named = _TEST_BINARY.search(root)
    return [named.group("name") if named else None]


def _is_root_selected(root: str, preparation: Preparation) -> bool:
    """Report whether the setup rules select every binary compiled from *root*.

    An owner the resolver cannot see has no known package, so only a rule that
    selects every test covers it.

    Returns
    -------
    bool
        True when each binary of the root, or its package, is selected.
    """
    if root.startswith(UNSEEN):
        return covers(preparation.rules, None, None)
    package = preparation.crates.get(owning_crate(root, preparation.crates))
    return all(
        covers(preparation.rules, name, package)
        for name in _binary_names(root, preparation)
    )


def _setup_coverage(path: str, warmups: Warmups) -> str:
    """Say whether nextest setup rules run before every test that compiles *path*.

    Every target whose module tree reaches the file must be selected, so a
    support module two binaries include is covered only when both are. A file no
    target reaches is judged as its own root.

    Returns
    -------
    str
        `resolved` or `unresolved`.
    """
    roots = warmups.reached_by.get(path) or [path]
    is_covered = all(_is_root_selected(root, warmups.preparation) for root in roots)
    return COVERAGE_RESOLVED if is_covered else COVERAGE_UNRESOLVED


def coverage(path: str, covered_by: str, warmups: Warmups) -> str:
    """Say whether one declaration's warm-up resolves and runs before the tests.

    Returns
    -------
    str
        `resolved`, `unresolved` or `unreadable` (an unknown form).
    """
    if covered_by == SETUP_FORM:
        return _setup_coverage(path, warmups)
    if covered_by.startswith(MAKE_PREFIX):
        return _make_coverage(covered_by.removeprefix(MAKE_PREFIX), warmups.make)
    if covered_by.startswith(TEST_PREFIX):
        known = covered_by.removeprefix(TEST_PREFIX) in warmups.functions
        return COVERAGE_RESOLVED if known else COVERAGE_UNRESOLVED
    return COVERAGE_UNREADABLE
