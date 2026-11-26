"""Read the target names a Makefile builds by expansion.

The rule index is keyed by the raw target text, such as `$(T)`, but the walkers
look targets up by the names prerequisites settle to. A name that reads one way
is therefore also filed under that name, so a target built by expansion is found
like any other, and one that already has a rule under that name then has several,
which the walkers report rather than guess at. A name that does not settle, or
settles two ways, or names a special target, is a construct the readers do not
follow.
"""

from __future__ import annotations

import typing as typ

from .makefile_expansion import expand_readings

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from .makefile_facts import MakeRule

# Special targets that change what a failed recipe line does to the next (a
# `.ONESHELL` recipe runs as one script; `.IGNORE` ignores errors, globally or
# for the targets it names), so a build before the tests no longer gates them.
UNSAFE_SPECIAL_TARGETS: typ.Final = frozenset({".ONESHELL", ".IGNORE"})


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
