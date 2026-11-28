"""Decide which `make` calls in a recipe run before the tests start.

A call to `make <goal>` warms the tests only if it always runs, and runs before
anything starts them:

- it must be first on its line, or joined to the calls before it by `&&`; a
  piece after `||`, in a quoted string, inside a subshell or in a shell branch
  may be skipped;
- a line that runs a test command, and any goal that itself starts the tests,
  ends the early calls.
"""

from __future__ import annotations

import re
import typing as typ

if typ.TYPE_CHECKING:
    from .makefile_facts import MakeRecipe, MakeRule

type Index = dict[str, list[MakeRule]]


# One plain goal and nothing else: an option (`-C dir`, `-f file`, `-n`, `-j 4`)
# or a variable assignment (`FOO=1`) changes what runs, so such a call is not
# read as a call to its goal.
_MAKE_CALL: typ.Final = re.compile(
    r"\s*[@+-]*\s*(?:\$\(MAKE\)|make)\s+(?P<goal>[A-Za-z_.][\w.-]*)\s*$"
)
# A recursive call with an option, an assignment or a variable goal runs a goal
# this reader cannot name, so it may start the tests.
_OPAQUE_MAKE_CALL: typ.Final = re.compile(r"(?:\$\(MAKE\)|\bmake)\s+(?:-|\S+=|\$)")
_ANY_MAKE_CALL: typ.Final = re.compile(r"(?:\$\(MAKE\)|\bmake)\s+(?P<goal>[\w.-]+)")
_BACKGROUND: typ.Final = re.compile(r"(?<!&)&(?!&)")
_TEST_COMMAND: typ.Final = re.compile(r"\b(?:cargo|nextest|pytest)\b")
_CONTROL_FLOW: typ.Final = re.compile(r"\b(?:if|then|else|elif|case|while|for|until)\b")


def _is_quoted(text: str, offset: int) -> bool:
    """Report whether *offset* lies inside a quoted string of a recipe line."""
    before = text[:offset]
    return before.count('"') % 2 == 1 or before.count("'") % 2 == 1


def _chain_goals(piece: str) -> list[str]:
    """Return the leading `make` calls of one `;`-separated piece, in order.

    A `&&` chain ends at its first part that is not a `make` call, because what
    follows a command that may fail may be skipped.

    Returns
    -------
    list[str]
        The goals the piece always calls, in order.
    """
    # A lone `&` backgrounds the whole list it ends, so nothing in it is done
    # before what follows.
    if _BACKGROUND.search(piece):
        return []
    goals: list[str] = []
    for part in piece.split("&&"):
        call = _MAKE_CALL.match(part)
        if call is None:
            break
        goals.append(call.group("goal"))
    return goals


def line_goals(text: str) -> list[str]:
    """Return the goals one recipe line always calls before its test command.

    Returns
    -------
    list[str]
        The goals, in order. A piece after `||`, one inside quotes and a line
        with a shell branch contribute none.
    """
    runs = _TEST_COMMAND.search(text)
    head = text[: runs.start()] if runs else text
    if _CONTROL_FLOW.search(head):
        return []
    pieces = [
        piece.group()
        for piece in re.finditer(r"[^;]+", head)
        if not _is_quoted(head, piece.start()) and "||" not in piece.group()
    ]
    return [goal for piece in pieces for goal in _chain_goals(piece)]


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
        for goal in line_goals(recipe["text"]):
            found.append(goal)
            if starts_tests(goal, index, set()):
                return found
        if _TEST_COMMAND.search(recipe["text"]):
            break
    return found
