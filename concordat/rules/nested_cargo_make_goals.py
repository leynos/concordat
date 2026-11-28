"""Read which Makefile goals run before the tests.

`make:<goal>` names a goal that warms the tests. It counts when the Makefile's
`test` target depends on it (Make runs prerequisites first), or when a recipe
line of `test`, or of a goal it runs, calls `make <goal>` *before* tests start:

- a call counts only if it always runs: first on its line, or joined to the
  calls before it by `&&`, and not after `||`, in a quoted string, inside a
  subshell or in a shell branch;
- a call after a line that runs a test command, or after a goal that itself
  starts tests, is too late and does not count;
- a prerequisite written with a variable, or an included Makefile, may name the
  goal, so a goal not found then is unreadable, not unresolved.
"""

from __future__ import annotations

import dataclasses
import re
import typing as typ

from .nested_cargo_makefile_lines import rules_by_target
from .nested_cargo_makefile_preparation import includes_a_file, read_makefile
from .nested_cargo_scripts import TEST_TARGET

if typ.TYPE_CHECKING:
    import pathlib

    from .makefile_facts import MakeRule

type Index = dict[str, list[MakeRule]]

_MAKE_CALL: typ.Final = re.compile(
    r"\s*[@+-]*\s*(?:\$\(MAKE\)|make)\s+(?P<goal>[\w.-]+)"
)
_ANY_MAKE_CALL: typ.Final = re.compile(r"(?:\$\(MAKE\)|\bmake)\s+(?P<goal>[\w.-]+)")
_TEST_COMMAND: typ.Final = re.compile(r"\b(?:cargo|nextest|pytest)\b")
_CONTROL_FLOW: typ.Final = re.compile(r"\b(?:if|then|else|elif|case|while|for|until)\b")


@dataclasses.dataclass(frozen=True, slots=True)
class MakeGoals:
    """Hold the goals that run before the tests, and whether more may hide.

    Attributes
    ----------
    goals:
        Every goal `test` depends on, or calls before tests start.
    is_opaque:
        True when a variable prerequisite or an included file may name more.
    is_absent:
        True when the checkout has no Makefile at all.
    """

    goals: frozenset[str]
    is_opaque: bool
    is_absent: bool


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
    goals: list[str] = []
    for part in piece.split("&&"):
        call = _MAKE_CALL.match(part)
        if call is None:
            break
        goals.append(call.group("goal"))
    return goals


def _line_goals(text: str) -> list[str]:
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


def _dependencies(rule: MakeRule) -> list[str]:
    """Return the goals a rule depends on or calls through `make`, anywhere."""
    calls = [
        found.group("goal")
        for recipe in rule["recipes"]
        for found in _ANY_MAKE_CALL.finditer(recipe["text"])
    ]
    return [name for name in rule["prerequisites"] if "$" not in name] + calls


def _starts_tests(goal: str, index: Index, seen: set[str]) -> bool:
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
        any(_TEST_COMMAND.search(recipe["text"]) for recipe in rule["recipes"])
        or any(_starts_tests(name, index, seen) for name in _dependencies(rule))
        for rule in index.get(goal, [])
    )


def _early_calls(rule: MakeRule, index: Index) -> list[str]:
    """Return the goals a rule's recipe calls before tests can have started.

    Returns
    -------
    list[str]
        The goals, in order. The list ends at the first line that runs a test
        command, and at the first called goal that starts tests itself.
    """
    found: list[str] = []
    for recipe in rule["recipes"]:
        for goal in _line_goals(recipe["text"]):
            found.append(goal)
            if _starts_tests(goal, index, set()):
                return found
        if _TEST_COMMAND.search(recipe["text"]):
            break
    return found


def _next_goals(rule: MakeRule, index: Index) -> tuple[list[str], bool]:
    """Return the goals one rule leads to, and whether a prerequisite hid.

    Returns
    -------
    tuple[list[str], bool]
        The plain prerequisites and the goals the recipe calls early, then
        whether a prerequisite names a variable the reader cannot resolve.
    """
    plain = [name for name in rule["prerequisites"] if "$" not in name]
    hides = len(plain) != len(rule["prerequisites"])
    found: list[str] = []
    for name in plain:
        found.append(name)
        # Make runs prerequisites in order, then the recipe: nothing after one
        # that starts the tests is early.
        if _starts_tests(name, index, set()):
            return found, hides
    return [*found, *_early_calls(rule, index)], hides


def _reachable_goals(index: Index) -> tuple[set[str], bool]:
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
            names, hid = _next_goals(rule, index)
            hides = hides or hid
            goals.update(names)
            pending.extend(names)
    return goals - {TEST_TARGET}, hides


def read_make_goals(checkout: pathlib.Path) -> MakeGoals:
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
