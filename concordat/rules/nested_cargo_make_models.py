"""Say whether the reader models every rule of a Makefile goal.

The prerequisite-order reading is sound only for plain rules. A goal whose rules
are written with a variable prerequisite, a double colon, a condition or a second
recipe, or that gets its recipe from a pattern rule, is not modelled: Make's
behaviour there is not what the plain reading says, so the reader reports such a
goal as indeterminate rather than judging it.
"""

from __future__ import annotations

import typing as typ

if typ.TYPE_CHECKING:
    from .makefile_facts import MakeRule

type Index = dict[str, list[MakeRule]]


def _phony(index: Index) -> set[str]:
    """Return the goals declared `.PHONY`."""
    return {name for rule in index.get(".PHONY", []) for name in rule["prerequisites"]}


def _has_implicit_recipe(goal: str, rules: list[MakeRule], index: Index) -> bool:
    """Report whether a goal with no recipe may get one from a pattern rule."""
    has_recipe = any(rule["recipes"] for rule in rules)
    has_pattern = any("%" in target for target in index)
    return has_pattern and not has_recipe and goal not in _phony(index)


def _is_plain(rules: list[MakeRule]) -> bool:
    """Report whether no rule has a variable prerequisite, double colon or condition."""
    return not any(
        rule["double_colon"]
        or rule["conditions"]
        or any("$" in name for name in rule["prerequisites"])
        for rule in rules
    )


def is_modelled(goal: str, index: Index) -> bool:
    """Report whether the reader models every rule of *goal*.

    Returns
    -------
    bool
        False for a rule written with a variable prerequisite, a double colon, a
        condition, a second recipe, or one whose recipe may come from a pattern
        rule.
    """
    rules = index.get(goal, [])
    has_one_recipe = sum(1 for rule in rules if rule["recipes"]) <= 1
    return (
        _is_plain(rules)
        and has_one_recipe
        and not _has_implicit_recipe(goal, rules, index)
    )
