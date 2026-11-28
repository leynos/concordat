"""Read which Makefile goals certainly finish before the tests start.

`make:<goal>` names a goal that warms the tests. The reader proves only what
Make guarantees. A goal counts when `test` depends on it, directly or through
other prerequisites, and no sibling prerequisite that may start the tests is
left unordered after it: Make does not order siblings (`make -j` runs them
together), so a sibling that starts the tests must itself depend on the goal.

A call to `make <goal>` in a recipe proves nothing here. Whether it runs, and
runs first, depends on the shell, so such a goal is read as unreadable. So is
any goal when the Makefile holds something the reader does not model: an
include, `.ONESHELL`, a double-colon rule, a target with more than one recipe, a
prerequisite written with a variable, or a recipe line it cannot read.
"""

from __future__ import annotations

import dataclasses
import typing as typ

from .nested_cargo_make_models import is_modelled
from .nested_cargo_make_order import Index, dependencies, starts_tests
from .nested_cargo_makefile_lines import rules_by_target
from .nested_cargo_makefile_names import UNSAFE_SPECIAL_TARGETS
from .nested_cargo_makefile_preparation import includes_a_file, read_makefile
from .nested_cargo_scripts import TEST_TARGET

if typ.TYPE_CHECKING:
    import pathlib

_DEPTH: typ.Final = 8


@dataclasses.dataclass(frozen=True, slots=True)
class MakeGoals:
    """Hold the goals that run before the tests, and what else the Makefile hides.

    Attributes
    ----------
    goals:
        Every goal `test` certainly finishes before the tests start.
    mentioned:
        Every goal `test` depends on or calls, wherever the text says so.
    is_opaque:
        True when the Makefile holds a construct the reader does not model.
    is_absent:
        True when the checkout has no Makefile at all.
    """

    goals: frozenset[str]
    mentioned: frozenset[str]
    is_opaque: bool
    is_absent: bool


def _prerequisites(goal: str, index: Index) -> list[str]:
    """Return the prerequisites Make joins from every single-colon rule of *goal*."""
    names = (name for rule in index.get(goal, []) for name in rule["prerequisites"])
    return [name for name in dict.fromkeys(names) if "$" not in name]


def _early(goal: str, index: Index, depth: int = _DEPTH) -> set[str]:
    """Return the goals *goal* certainly finishes before its own tests start.

    A prerequisite is early only if every sibling that may start the tests
    finishes it first, at any depth.

    Returns
    -------
    set[str]
        The early prerequisites and what they finish early in turn; empty past
        the depth limit, which stops a cyclic Makefile.
    """
    if depth <= 0:
        return set()
    plain = _prerequisites(goal, index)
    starters = [name for name in plain if starts_tests(name, index, set())]
    found: set[str] = set()
    for name in plain:
        is_ordered = all(
            name == s or name in _early(s, index, depth - 1) for s in starters
        )
        if not is_ordered:
            continue
        # What a prerequisite depends on finishes before it starts, whatever it
        # runs; the prerequisite itself is a warm-up only if it may not run the
        # tests, because it has not finished when they start.
        found |= _early(name, index, depth - 1)
        if not starts_tests(name, index, set()):
            found.add(name)
    return found


def _closure(index: Index) -> set[str]:
    """Return every goal reachable from `test` through prerequisites and calls."""
    pending, seen = [TEST_TARGET], set[str]()
    while pending:
        goal = pending.pop()
        if goal in seen:
            continue
        seen.add(goal)
        pending.extend(n for rule in index.get(goal, []) for n in dependencies(rule))
    return seen


def read_make_goals(checkout: pathlib.Path) -> MakeGoals:
    """Return the goals that finish before the Makefile's `test` recipe runs.

    Returns
    -------
    MakeGoals
        The early goals, every goal mentioned, whether a construct the reader
        does not model is present, and whether there is no Makefile.
    """
    read = read_makefile(checkout)
    if read is None:
        absent = not (checkout / "Makefile").exists()
        return MakeGoals(frozenset(), frozenset(), is_opaque=True, is_absent=absent)
    report, text = read
    index = rules_by_target(report)
    closure = _closure(index)
    is_opaque = (
        includes_a_file(report, text)
        or bool(UNSAFE_SPECIAL_TARGETS & index.keys())
        or not all(is_modelled(goal, index) for goal in closure)
    )
    goals = _early(TEST_TARGET, index)
    return MakeGoals(
        frozenset(goals - {TEST_TARGET}),
        frozenset(closure - {TEST_TARGET}),
        is_opaque=is_opaque,
        is_absent=False,
    )
