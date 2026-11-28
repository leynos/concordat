"""Decide which Makefile goals may start the tests.

The `make:<goal>` warm-up form needs to know that a goal finishes before the
tests start. Proving that from shell text is a tarpit (operators, quoting,
backgrounding, `.ONESHELL`, redefined recipes), so the reader proves only what
Make itself guarantees, prerequisite order, and reads everything else as "may
start the tests":

- a recipe line that runs a test command, runs a command named by a variable,
  holds a backslash or an unbalanced quote, or calls `make` with an option, an
  assignment or a variable goal may start the tests;
- a goal starts the tests if its recipe, a prerequisite, or any goal its recipe
  mentions through `make` may.

Over-approximating here is the safe direction: it can only make a warm-up fail
to prove, which is reported as indeterminate, never as a pass.
"""

from __future__ import annotations

import re
import typing as typ

if typ.TYPE_CHECKING:
    from .makefile_facts import MakeRule

type Index = dict[str, list[MakeRule]]

# A command that runs tests: `cargo test`, `cargo nextest ...`, `nextest`, `pytest`.
# A compile-only line (`--no-run`, `archive`) prepares and does not start them.
_TEST_COMMAND: typ.Final = re.compile(
    r"\b(?:cargo(?:-\w+)?\s+(?:\S+\s+)*?(?:nextest|test|bench|llvm-cov|tarpaulin)"
    r"|nextest|pytest)\b"
)
_COMPILE_ONLY: typ.Final = re.compile(r"--no-run\b|\bnextest\s+archive\b")
# A variable (other than `$(MAKE)`) anywhere on the line may expand to a test
# command: `env $(RUNNER)`, `$(CARGO) test`.
_VARIABLE: typ.Final = re.compile(r"\$[({](?!MAKE[)}])|\$[A-Za-z_]")
_MAKE_CALL: typ.Final = re.compile(
    r"(?:\$\(MAKE\)|\$\{MAKE\}|\bmake)(?P<args>[^;&|()]*)"
)
_PLAIN_GOAL: typ.Final = re.compile(r"[A-Za-z_.][\w.-]*")


def _make_arguments(text: str) -> list[list[str]]:
    """Return the words after each `make` call on a recipe line."""
    return [found.group("args").split() for found in _MAKE_CALL.finditer(text)]


def _is_unreadable(text: str) -> bool:
    """Report whether a line has an escape or quote the reader does not model."""
    return "\\" in text or text.count('"') % 2 == 1 or text.count("'") % 2 == 1


def _has_opaque_call(text: str) -> bool:
    """Report whether a `make` call names no plain goals, or adds more than goals."""
    return any(
        not words or any(_PLAIN_GOAL.fullmatch(word) is None for word in words)
        for words in _make_arguments(text)
    )


def _runs_test_command(text: str) -> bool:
    """Report whether any command on a line runs tests rather than only compiling.

    The line is split at every shell operator, quoted or not: a fragment that
    holds a test command and is not marked compile-only counts, so a compound
    line is test-starting unless each of its commands is known to be a compile.

    Returns
    -------
    bool
        True when some fragment of the line runs a test command.
    """
    return any(
        _TEST_COMMAND.search(part) and not _COMPILE_ONLY.search(part)
        for part in re.split(r"&&|\|\||[;&|]", text)
    )


def may_run_tests(text: str) -> bool:
    """Report whether one recipe line may run the tests, or something unreadable.

    Returns
    -------
    bool
        True for a test command, a variable command word, a line the reader
        cannot model, or a recursive `make` it cannot name.
    """
    return bool(
        _runs_test_command(text)
        or _VARIABLE.search(text)
        or _is_unreadable(text)
        or _has_opaque_call(text)
    )


def _called_goals(rule: MakeRule) -> list[str]:
    """Return the plain goals a rule's recipes mention through `make`."""
    return [
        word
        for recipe in rule["recipes"]
        for words in _make_arguments(recipe["text"])
        for word in words
        if _PLAIN_GOAL.fullmatch(word)
    ]


def runs_tests_itself(goal: str, index: Index) -> bool:
    """Report whether *goal*'s own recipes may run the tests, directly or by a call.

    Prerequisites are left out: they finish before the recipe runs.

    Returns
    -------
    bool
        True when a recipe line may run tests, or calls a goal that may.
    """
    return any(
        any(may_run_tests(recipe["text"]) for recipe in rule["recipes"])
        or any(starts_tests(name, index, set()) for name in _called_goals(rule))
        for rule in index.get(goal, [])
    )


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
        The prerequisites, then every plain goal each `make` call mentions.
    """
    return plain_prerequisites(rule) + _called_goals(rule)


def starts_tests(goal: str, index: Index, seen: set[str]) -> bool:
    """Report whether running *goal* may start the tests.

    Returns
    -------
    bool
        True when a recipe of the goal, of a goal it depends on or of one it
        calls through `make` may run the tests.
    """
    if goal in seen:
        return False
    seen.add(goal)
    return any(
        any(may_run_tests(recipe["text"]) for recipe in rule["recipes"])
        or any(starts_tests(name, index, seen) for name in dependencies(rule))
        for rule in index.get(goal, [])
    )
