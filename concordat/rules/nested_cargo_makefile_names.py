"""Read the target and prerequisite names a Makefile builds by expansion.

The rule index is keyed by the raw target text, such as `$(T)`, but the walkers
look targets up by the names prerequisites settle to. A name that reads one way
is therefore also filed under that name, so a target built by expansion is found
like any other, and one that already has a rule under that name then has several,
which the walkers report rather than guess at. A name that does not settle, or
settles two ways, or names a special target, is a construct the readers do not
follow.

Make expands a target or prerequisite when it reads the rule, not after later
assignments, and it follows references inside a variable's value. The readers
substitute final values, so a name that reads a variable (directly, or through
the values of the variables it names) assigned on or after the rule's line gets
another value from them than from Make, and the Makefile is not read.
"""

from __future__ import annotations

import itertools
import re
import typing as typ

from .makefile_expansion import expand_readings

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from .makefile_facts import MakeRule, MakeVariable

# Special targets that change what a failed recipe line does to the next (a
# `.ONESHELL` recipe runs as one script; `.IGNORE` ignores errors, globally or
# for the targets it names), so a build before the tests no longer gates them.
UNSAFE_SPECIAL_TARGETS: typ.Final = frozenset({".ONESHELL", ".IGNORE"})
_REFERENCE: typ.Final = re.compile(r"\$\(([^()$]+)\)|\$\{([^{}$]+)\}|\$([A-Za-z_])")
# A reference whose own name is built by expansion: `$($(K))`.
_COMPUTED: typ.Final = re.compile(r"\$[({][^)}]*\$[({]")
_SIMPLE_OPERATORS: typ.Final = frozenset({":=", "::=", ":::=", "!="})


def _settled_words(name: str, values: cabc.Mapping[str, str]) -> list[str]:
    """Return the words *name* settles to when it reads exactly one way."""
    readings = expand_readings(name, values) if "$" in name else []
    if len(readings) != 1 or not readings[0].is_resolved:
        return []
    return readings[0].text.split()


def settled_index(
    index: dict[str, list[MakeRule]], values: cabc.Mapping[str, str]
) -> dict[str, list[MakeRule]]:
    """Return *index* with each expanded target name also keyed by what it settles to.

    Returns
    -------
    dict[str, list[MakeRule]]
        The index with the settled names added.
    """
    settled = {name: list(rules) for name, rules in index.items()}
    for name, rules in index.items():
        for word in _settled_words(name, values):
            known = settled.setdefault(word, [])
            known.extend(rule for rule in rules if rule not in known)
    return settled


def _references(text: str) -> set[str]:
    """Return the variable names a text refers to."""
    return {
        word for match in _REFERENCE.finditer(text) for word in match.groups() if word
    }


def _variables_behind(text: str, values: cabc.Mapping[str, str]) -> set[str]:
    """Return every variable a text reads, directly or through other values."""
    found: set[str] = set()
    pending = _references(text)
    while pending:
        word = pending.pop()
        if word not in found:
            found.add(word)
            pending |= _references(values.get(word, ""))
    return found


def _readings(
    index: dict[str, list[MakeRule]],
) -> list[tuple[str, int]]:
    """Return each expanded target name or prerequisite with the line of its rule.

    A pattern stays a pattern whatever its variables hold, so a target name with
    a `%` is left out.

    Returns
    -------
    list[tuple[str, int]]
        The texts that contain a reference, each with its rule's first line.
    """
    found: list[tuple[str, int]] = []
    for name, rules in index.items():
        for rule in rules:
            line = rule["location"]["start_line"]
            texts = [] if "%" in name else [name]
            texts.extend(rule["prerequisites"])
            found.extend((text, line) for text in texts if "$" in text)
    return found


def named_variables(
    index: dict[str, list[MakeRule]], values: cabc.Mapping[str, str]
) -> frozenset[str]:
    """Return the variables that decide the target names and prerequisites read.

    Returns
    -------
    frozenset[str]
        Every variable such a name reads, directly or through other values; an
        override of one of them can change which target a name denotes.
    """
    return frozenset(
        word
        for text, _line in _readings(index)
        for word in _variables_behind(text, values)
    )


class _Assignment(typ.NamedTuple):
    """Hold where a variable is first assigned and whether its value is fixed then."""

    line: int
    is_simple: bool


def _first_assignments(
    variables: cabc.Sequence[MakeVariable],
) -> dict[str, _Assignment]:
    """Return each variable's first assignment."""
    first: dict[str, _Assignment] = {}
    for variable in variables:
        name = str(variable.get("name", ""))
        line = variable.get("location", {}).get("start_line", 0)
        current = first.get(name)
        if current is None or line < current.line:
            is_simple = variable.get("operator") in _SIMPLE_OPERATORS
            first[name] = _Assignment(line, is_simple)
    return first


def _has_computed_reference(text: str) -> bool:
    """Return whether a text names a variable by expansion (`$($(K))`)."""
    return _COMPUTED.search(text) is not None


class _ReadOrder:
    """Judge whether a name reads variables that Make had not yet assigned.

    Make expands a recursive (`=`) value where it is used and a simple (`:=`)
    value where it is assigned, so what a simple variable reads is judged against
    its own assignment, and what a recursive one reads against the rule.
    """

    def __init__(
        self, values: cabc.Mapping[str, str], variables: cabc.Sequence[MakeVariable]
    ) -> None:
        """Index the assignments of the Makefile."""
        self.values = values
        self.first = _first_assignments(variables)

    def is_late(self, text: str, line: int) -> bool:
        """Return whether *text*, read on *line*, reads a variable assigned later."""
        return _has_computed_reference(text) or any(
            self._is_word_late(word, line, frozenset()) for word in _references(text)
        )

    def _is_word_late(self, word: str, line: int, trail: frozenset[str]) -> bool:
        """Return whether *word*, or a variable its value reads, is assigned late."""
        assigned = self.first.get(word)
        if assigned is None or word in trail:
            return False
        if assigned.line >= line:
            return True
        value = self.values.get(word, "")
        at = assigned.line if assigned.is_simple else line
        return _has_computed_reference(value) or any(
            self._is_word_late(inner, at, trail | {word})
            for inner in _references(value)
        )


def has_unsafe_reading(
    index: dict[str, list[MakeRule]],
    values: cabc.Mapping[str, str],
    variables: cabc.Sequence[MakeVariable],
) -> bool:
    """Return whether a name built by expansion cannot be read safely.

    Returns
    -------
    bool
        True when a target name built by expansion may be a special target or
        does not settle one way, or when a target name or prerequisite reads a
        variable assigned on or after the rule that uses it.
    """
    if any(may_be_special(name, values) for name in index if "$" in name):
        return True
    order = _ReadOrder(values, variables)
    return any(itertools.starmap(order.is_late, _readings(index)))


def may_be_special(name: str, values: cabc.Mapping[str, str]) -> bool:
    """Return whether a target name built by expansion may be a special target.

    A name that settles one way to something other than `.ONESHELL` or `.IGNORE`
    (`target/debug/$(APP)`, `target/%/$(APP)`) cannot change what the recipes do,
    and `settled_index` files it under its settled name; one that does not
    settle, or settles two ways, may.

    Returns
    -------
    bool
        True when the name has several readings, or one that is unresolved or
        names a special target.
    """
    readings = expand_readings(name, values)
    return len(readings) != 1 or any(
        not reading.is_resolved
        or bool(UNSAFE_SPECIAL_TARGETS & set(reading.text.split()))
        for reading in readings
    )
