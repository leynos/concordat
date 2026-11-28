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
from .nested_cargo_make_order import (
    Index,
    dependencies,
    plain_prerequisites,
    runs_tests_itself,
    starts_tests,
)
from .nested_cargo_makefile_lines import rules_by_target
from .nested_cargo_makefile_names import UNSAFE_SPECIAL_TARGETS
from .nested_cargo_makefile_preparation import includes_a_file, read_makefile
from .nested_cargo_scripts import TEST_TARGET

if typ.TYPE_CHECKING:
    import pathlib


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


def _prerequisite_closure(goal: str, index: Index) -> set[str]:
    """Return every goal *goal* depends on through prerequisites, at any depth."""
    seen: set[str] = set()
    pending = [goal]
    while pending:
        rules = index.get(pending.pop(), [])
        fresh = {n for rule in rules for n in plain_prerequisites(rule)} - seen
        seen |= fresh
        pending.extend(fresh)
    return seen


def _early(goal: str, index: Index) -> set[str]:
    """Return the goals *goal* certainly finishes before its tests start.

    A goal under `test` finishes before the tests start when every goal that
    runs them in its own recipe depends on it (Make does not order siblings, so
    `make -j` would otherwise start them together), and when it cannot run the
    tests itself, because it has not finished when they start. What a test
    runner depends on counts even though the runner does not.

    Returns
    -------
    set[str]
        The early goals; the target itself is never one.
    """
    closure = _prerequisite_closure(goal, index) - {goal}
    runners = [name for name in closure if runs_tests_itself(name, index)]
    needed = [_prerequisite_closure(name, index) for name in runners]
    return {
        name
        for name in closure
        if not starts_tests(name, index, set())
        and all(name in depends_on for depends_on in needed)
    }


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
