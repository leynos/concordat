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
from .nested_cargo_make_goals import MakeGoals, read_make_goals
from .nested_cargo_preparation import (
    Preparation,
    crate_directories,
    declared_tests,
    named_test_crates,
    owning_crate,
)
from .nested_cargo_reach import UNSEEN
from .nested_cargo_setup_scripts import covers, read_setup_rules
from .nested_cargo_source import RustSource, read_sources
from .nested_cargo_spawns import fixture_directories

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib


COVERAGE_RESOLVED: typ.Final = "resolved"
COVERAGE_UNRESOLVED: typ.Final = "unresolved"
COVERAGE_UNREADABLE: typ.Final = "unreadable"

SETUP_FORM: typ.Final = "nextest-setup"
MAKE_PREFIX: typ.Final = "make:"
TEST_PREFIX: typ.Final = "test:"

_FUNCTION: typ.Final = re.compile(r"\bfn\s+(?P<name>[A-Za-z_]\w*)")
_TEST_BINARY: typ.Final = re.compile(
    r"(?:^|/)tests/(?P<name>[^/.]+)(?:\.rs|/main\.rs)$"
)


@dataclasses.dataclass(frozen=True, slots=True)
class Warmups:
    """Hold what the repository offers as warm-ups, read once."""

    make: MakeGoals
    functions: frozenset[str]
    reached_by: cabc.Mapping[str, list[str]]
    preparation: Preparation


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
        read_make_goals(checkout),
        _function_names(read_sources(checkout, fixtures, [])),
        reached_by,
        preparation,
    )


def _make_coverage(goal: str, make: MakeGoals) -> str:
    """Say whether *goal* certainly finishes before the tests start.

    Returns
    -------
    str
        `resolved` when `test` finishes it first by prerequisite order,
        `unresolved` when `test` never reaches it, and `unreadable` when it is
        reached some way the reader cannot prove (a recipe call, an unordered
        sibling) or the Makefile holds a construct it does not model.
    """
    if goal in make.goals and not make.is_opaque:
        return COVERAGE_RESOLVED
    if make.is_absent:
        return COVERAGE_UNRESOLVED
    is_unproven = make.is_opaque or goal in make.mentioned
    return COVERAGE_UNREADABLE if is_unproven else COVERAGE_UNRESOLVED


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
