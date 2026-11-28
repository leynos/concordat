"""Decide which `make` calls in a recipe run before the tests start.

A call to `make <goal>` warms the tests only if it always runs, and runs before
anything starts them:

- it must be first on its line, or joined to the calls before it by `&&`, as the
  shell reads the line (see `nested_cargo_shell_lists`); a piece after `||`, in
  a quoted string, in the background, inside a subshell or in a branch may be
  skipped;
- a line that runs a test command, and any goal that itself starts the tests,
  ends the early calls.
"""

from __future__ import annotations

import re
import typing as typ

from .nested_cargo_shell_lists import leading_make_goals

if typ.TYPE_CHECKING:
    from .makefile_facts import MakeRecipe, MakeRule

type Index = dict[str, list[MakeRule]]


# A recursive call with an option, an assignment or a variable goal runs a goal
# this reader cannot name, so it may start the tests.
_OPAQUE_MAKE_CALL: typ.Final = re.compile(r"(?:\$\(MAKE\)|\bmake)\s+(?:-|\S+=|\$)")
_ANY_MAKE_CALL: typ.Final = re.compile(r"(?:\$\(MAKE\)|\bmake)\s+(?P<goal>[\w.-]+)")
_TEST_COMMAND: typ.Final = re.compile(r"\b(?:cargo|nextest|pytest)\b")


def plain_prerequisites(rule: MakeRule) -> list[str]:
    """Return a rule's prerequisites that name no variable."""
    return [name for name in rule["prerequisites"] if "$" not in name]


def dependencies(rule: MakeRule) -> list[str]:
    """Return the goals a rule may depend on or call, wherever the text says so.

    This over-approximates (a quoted `make x` counts), which is the safe side
    for asking whether a goal may start the tests.

    Returns
    -------
    list[str]
        The prerequisites, then every goal a recipe text mentions through `make`.
    """
    calls = [
        found.group("goal")
        for recipe in rule["recipes"]
        for found in _ANY_MAKE_CALL.finditer(recipe["text"])
    ]
    return plain_prerequisites(rule) + calls


def _may_run_tests(text: str) -> bool:
    """Report whether one recipe line runs a test command or an unreadable goal."""
    return bool(_TEST_COMMAND.search(text) or _OPAQUE_MAKE_CALL.search(text))


def starts_tests(goal: str, index: Index, seen: set[str]) -> bool:
    """Report whether running *goal* may start the tests.

    Returns
    -------
    bool
        True when a recipe of the goal, of a goal it depends on or of one it
        calls through `make` runs a test command.
    """
    if goal in seen:
        return False
    seen.add(goal)
    return any(
        any(_may_run_tests(recipe["text"]) for recipe in rule["recipes"])
        or any(starts_tests(name, index, seen) for name in dependencies(rule))
        for rule in index.get(goal, [])
    )


def early_calls(recipes: list[MakeRecipe], index: Index) -> list[str]:
    """Return the goals a goal's recipe calls before tests can have started.

    Returns
    -------
    list[str]
        The goals, in order. The list ends at the first line that runs a test
        command, and at the first called goal that starts tests itself.
    """
    found: list[str] = []
    for recipe in recipes:
        for goal in leading_make_goals(recipe["text"]):
            found.append(goal)
            if starts_tests(goal, index, set()):
                return found
        if _may_run_tests(recipe["text"]):
            break
    return found
