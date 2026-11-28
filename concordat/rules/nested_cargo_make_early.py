"""Decide which goals a Makefile target certainly finishes before its tests.

Make joins the prerequisites of a target's single-colon rules and does not order
siblings (`make -j` runs them together), so a prerequisite is early only if every
sibling that starts the tests finishes it first, at any depth. A prerequisite
written with a variable may hide such a sibling, so a target that has one proves
nothing, and the uncertainty reaches every goal it can run.
"""

from __future__ import annotations

import typing as typ

from .nested_cargo_make_order import (
    Index,
    dependencies,
    early_calls,
    starts_tests,
)

_DEPTH: typ.Final = 8


def has_hidden_prerequisite(goal: str, index: Index) -> bool:
    """Report whether any rule of *goal* names a prerequisite by a variable."""
    return any(
        "$" in name for rule in index.get(goal, []) for name in rule["prerequisites"]
    )


def _early_prerequisites(goal: str, index: Index, depth: int) -> tuple[list[str], bool]:
    """Return a goal's prerequisites that are early, and whether any starts tests.

    Returns
    -------
    tuple[list[str], bool]
        The prerequisites every test-starting sibling finishes first (all of
        them when none starts the tests), then whether some prerequisite starts
        the tests.
    """
    names = (name for rule in index.get(goal, []) for name in rule["prerequisites"])
    plain = list(dict.fromkeys(names))
    starters = [name for name in plain if starts_tests(name, index, set())]
    counted = [
        name
        for name in plain
        if all(name == s or name in early_set(s, index, depth - 1) for s in starters)
    ]
    return counted, bool(starters)


def next_goals(goal: str, index: Index, depth: int = _DEPTH) -> list[str]:
    """Return the goals *goal* certainly finishes before its tests can start.

    The recipe runs after all prerequisites, so its calls count only when none
    of them starts the tests. A target with a hidden prerequisite proves nothing.

    Returns
    -------
    list[str]
        The early prerequisites, then the early `make` calls of the recipe.
    """
    if has_hidden_prerequisite(goal, index):
        return []
    counted, is_started = _early_prerequisites(goal, index, depth)
    if is_started:
        return counted
    recipes = [recipe for rule in index.get(goal, []) for recipe in rule["recipes"]]
    return [*counted, *early_calls(recipes, index)]


def closure_hides(index: Index, start: str) -> bool:
    """Report whether a hidden prerequisite lies anywhere a target can reach.

    Returns
    -------
    bool
        True when *start*, or any goal it depends on or calls (read widely),
        names a prerequisite by a variable.
    """
    pending, seen = [start], set[str]()
    while pending:
        goal = pending.pop()
        if goal in seen:
            continue
        seen.add(goal)
        if has_hidden_prerequisite(goal, index):
            return True
        pending.extend(n for rule in index.get(goal, []) for n in dependencies(rule))
    return False


def early_set(goal: str, index: Index, depth: int = _DEPTH) -> set[str]:
    """Return every goal that *goal* certainly finishes before its tests start.

    Returns
    -------
    set[str]
        The goals its rules finish early, and those they finish early in turn;
        empty past the depth limit, which stops a cyclic Makefile.
    """
    found: set[str] = set()
    if depth <= 0:
        return found
    for name in next_goals(goal, index, depth):
        found.add(name)
        found |= early_set(name, index, depth - 1)
    return found
