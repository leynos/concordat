"""Read a recipe's `make goal [VAR=value ...]` command into the target it runs.

The pass walk follows a recipe line that runs another goal of the same Makefile,
because a repository prepares each pass by handing the pass's own flags to one
parametrized target (`prepare-nested-fixtures PREPARE_FLAGS=...`). Only the form
the walk models exactly counts: one plain goal and no option that diverts,
touches, asks or ignores errors.
"""

from __future__ import annotations

import dataclasses
import typing as typ

from .makefile_expansion import Assignments
from .nested_cargo_make_options import PLAIN_GOAL, unreliable_make
from .nested_cargo_scripts import command_words

if typ.TYPE_CHECKING:
    import collections.abc as cabc

_OVERRIDE: typ.Final = "="


@dataclasses.dataclass(frozen=True, slots=True)
class MakeCall:
    """Hold the one target a recipe's `make` command runs, and what it overrides."""

    goal: str
    overrides: dict[str, str]


def make_call(text: str) -> MakeCall | None:
    """Read a `make goal [VAR=value ...]` command into the target it runs.

    A `VAR=value` word is an override for the goal's own recipes, which is how a
    repository hands each pass's flags to one preparation target.

    Returns
    -------
    MakeCall | None
        The goal and its overrides, or None for any other command or shape.
    """
    words = command_words(text)
    if not words or not _is_make(words[0]):
        return None
    arguments = words[1:]
    if unreliable_make(arguments):
        return None
    goals = _goals(arguments)
    if len(goals) != 1 or not PLAIN_GOAL.fullmatch(goals[0]):
        return None
    return MakeCall(goals[0], _overrides(arguments))


def _is_make(word: str) -> bool:
    """Return whether the command word is `make`, by any path."""
    return word.lstrip("@-+").rsplit("/", 1)[-1] == "make"


def _is_override(word: str) -> bool:
    """Return whether *word* is a `VAR=value` override, not an option."""
    return _OVERRIDE in word and not word.startswith("-")


def _goals(arguments: list[str]) -> list[str]:
    """Return the goals among a make command's arguments."""
    return [
        word
        for word in arguments
        if not word.startswith("-") and not _is_override(word)
    ]


def _overrides(arguments: list[str]) -> dict[str, str]:
    """Return the `VAR=value` overrides among a make command's arguments."""
    return {
        word.partition(_OVERRIDE)[0]: word.partition(_OVERRIDE)[2]
        for word in arguments
        if _is_override(word)
    }


def overlaid(values: cabc.Mapping[str, str], overrides: dict[str, str]) -> Assignments:
    """Return *values* with the command-line *overrides*, which beat the Makefile."""
    assigned = getattr(values, "assigned", frozenset())
    return Assignments({**values, **overrides}, [*assigned, *overrides])
