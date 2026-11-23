"""Read the recipe lines of a Makefile's `test` target for the prefetch rules.

The entry-point reader walks `test`'s recipe and prerequisites looking for a
success-gated `cargo fetch --locked` before the test command. This module holds
the line-level questions that walk asks: which Make references left on a line
can change what it does, and whether a recipe or a prerequisite fetches.
"""

from __future__ import annotations

import typing as typ

from .makefile_expansion import UNRESOLVED_MARK, Expansion, expand
from .nested_cargo_scripts import (
    TEST_TARGET,
    ScriptCommand,
    command_words,
    line_fetches,
    script_commands,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from .makefile_facts import MakeRecipe, MakeRule, MakeutilReport

_TOOLS_READ_BY_ARGUMENTS: typ.Final = frozenset({"cargo", "make", "cargo-nextest"})


def is_settled(command: ScriptCommand) -> bool:
    """Return whether no Make reference the expansion left can change this command.

    A reference left in an environment assignment before the command word
    (`RUSTFLAGS="$(if ...)" cargo test`) or in the arguments of a command that
    is not cargo (`echo`, `export`, `cd`) cannot change which cargo command
    runs or whether it succeeds first. A reference in the command word, or in
    the arguments of a cargo, nextest or make command, can: it may be `--locked`,
    `--profile ci` or another target.

    Returns
    -------
    bool
        False when the command could not be lexed or an unresolved reference
        can alter what a cargo, nextest or make command does.
    """
    if command.meaning.is_unreadable:
        return False
    words = command_words(command.text, names_cargo=False)
    if words is None:
        return False
    if not words or UNRESOLVED_MARK not in " ".join(words):
        return True
    name = words[0].lstrip("@-+").rsplit("/", 1)[-1]
    return not (name in _TOOLS_READ_BY_ARGUMENTS or UNRESOLVED_MARK in name)


def are_settled(commands: list[ScriptCommand]) -> bool:
    """Return whether every command of a line is settled (see :func:`is_settled`)."""
    return all(is_settled(command) for command in commands)


def rules_by_target(report: MakeutilReport) -> dict[str, list[MakeRule]]:
    """Index the Makefile's rules by target, in file order."""
    index: dict[str, list[MakeRule]] = {}
    for rule in report["rules"]:
        for target in rule["targets"]:
            index.setdefault(target, []).append(rule)
    return index


def prefetch_in_prerequisites(
    index: dict[str, list[MakeRule]], values: cabc.Mapping[str, str]
) -> tuple[bool, str, str]:
    """Walk `test`'s prerequisites, returning proof, uncertainty and a reason."""
    seen = {TEST_TARGET}
    pending: list[MakeRule] = list(index[TEST_TARGET])
    proven = False
    proven_reason = ""
    uncertain_reason = ""
    while pending:
        rule = pending.pop()
        for reference in rule["prerequisites"]:
            expansion = expand(reference, values)
            if not expansion.is_resolved:
                uncertain_reason = "a prerequisite names an unresolved variable"
                continue
            for name in expansion.text.split():
                if name in seen:
                    continue
                seen.add(name)
                for nested in index.get(name, []):
                    pending.append(nested)
                    if recipes_fetch(nested["recipes"], values):
                        proven = True
                        proven_reason = f"prerequisite {name} runs cargo fetch --locked"
    return proven, uncertain_reason, proven_reason


def recipes_fetch(recipes: list[MakeRecipe], values: cabc.Mapping[str, str]) -> bool:
    """Return whether a recipe line fetches with its failure left fatal."""
    for recipe in recipes:
        expansion: Expansion = expand(recipe["text"], values)
        commands = script_commands(expansion.text)
        if not recipe["ignore_errors"] and line_fetches(
            commands, is_resolved=are_settled(commands)
        ):
            return True
    return False
