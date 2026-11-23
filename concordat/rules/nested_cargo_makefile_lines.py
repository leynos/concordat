"""Read the recipe lines of a Makefile's `test` target for the prefetch rules.

The entry-point reader walks `test`'s recipe and prerequisites looking for a
success-gated `cargo fetch --locked` before the test command. This module holds
the line-level questions that walk asks: which Make references left on a line
can change what it does, and whether a recipe or a prerequisite fetches.
"""

from __future__ import annotations

import re
import typing as typ

from .makefile_expansion import UNRESOLVED_MARK, Expansion, expand
from .nested_cargo_fetch_order import line_fetches
from .nested_cargo_scripts import (
    TEST_TARGET,
    ScriptCommand,
    command_words,
    script_commands,
)
from .rust_source import scan_balanced

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from .makefile_facts import MakeRecipe, MakeRule, MakeutilReport

_TOOLS_READ_BY_ARGUMENTS: typ.Final = frozenset({"cargo", "make", "cargo-nextest"})
_NAME: typ.Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_REFERENCE_OPEN: typ.Final = re.compile(r"\$[({]")
# A quoted span. An unterminated quote runs to the end of the text, so what
# follows it is not read as bare.
#
# A backslash outside quotes escapes the next character, so `\"` opens no span;
# `\$` is left alone because Make still expands `$(` after a backslash. Inside
# double quotes a backslash pairs with the character after it.
_QUOTED: typ.Final = re.compile(
    r"\\[^$]|'[^']*(?:'|$)|\"(?:\\.|[^\"\\])*(?:\"|$)", re.DOTALL
)


def _has_unquoted_reference(text: str, assigned: cabc.Container[str]) -> bool:
    """Return whether a Make reference outside quotes could change the shell's line.

    Make pastes a reference's value into the recipe before the shell reads it,
    so an unquoted reference may contribute `&&`, `||`, `;` or a pipe. A
    reference inside quotes is one word whatever it holds. A plain `$(NAME)`
    naming a variable the Makefile never assigns is environment data, the same
    assumption `makefile_expansion.is_pure` states, and is left alone.

    Returns
    -------
    bool
        True when an unquoted reference other than environment data appears.
    """
    bare = _QUOTED.sub("", text)
    position = 0
    while (opening := _REFERENCE_OPEN.search(bare, position)) is not None:
        end = _environment_reference_end(bare, opening.start(), assigned)
        if end is None:
            return True
        position = end
    return False


def _environment_reference_end(
    text: str, index: int, assigned: cabc.Container[str]
) -> int | None:
    """Return the index past the plain environment reference at *index*, or None.

    Returns
    -------
    int | None
        None when the reference is unbalanced or is anything but `$(NAME)` for
        a name the Makefile never assigns.
    """
    end = scan_balanced(text, index + 1)
    if end is None:
        return None
    name = text[index + 2 : end - 1]
    return end if _NAME.fullmatch(name) and name not in assigned else None


def is_settled(command: ScriptCommand, assigned: cabc.Container[str] = ()) -> bool:
    """Return whether no Make reference the expansion left can change this command.

    A reference left in a quoted environment assignment before the command word
    (`RUSTFLAGS="$(if ...)" cargo test`) or in the quoted arguments of a command
    that is not cargo (`echo`, `export`, `cd`) cannot change which cargo command
    runs or whether it succeeds first. A reference in the command word, or in
    the arguments of a cargo, nextest or make command, can: it may be `--locked`,
    `--profile ci` or another target. An unquoted reference can also add shell
    operators to the line, so it is never read past.

    Returns
    -------
    bool
        False when the command could not be lexed, an unquoted reference could
        reshape the line, or an unresolved reference can alter what a cargo,
        nextest or make command does.
    """
    if command.meaning.is_unreadable or _has_unquoted_reference(command.text, assigned):
        return False
    words = command_words(command.text, names_cargo=False)
    if words is None:
        return False
    if not words or UNRESOLVED_MARK not in " ".join(words):
        return True
    name = words[0].lstrip("@-+").rsplit("/", 1)[-1]
    return not (name in _TOOLS_READ_BY_ARGUMENTS or UNRESOLVED_MARK in name)


def are_settled(commands: list[ScriptCommand], values: cabc.Mapping[str, str]) -> bool:
    """Return whether every command of a line is settled (see :func:`is_settled`)."""
    assigned = getattr(values, "assigned", frozenset())
    return all(is_settled(command, assigned) for command in commands)


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
    proven_reason = ""
    uncertain_reason = ""
    while pending:
        rule = pending.pop()
        names, is_unresolved = _new_prerequisites(rule, values, seen)
        if is_unresolved:
            uncertain_reason = "a prerequisite names an unresolved variable"
        for name in names:
            for nested in index.get(name, []):
                pending.append(nested)
                if recipes_fetch(nested["recipes"], values):
                    proven_reason = f"prerequisite {name} runs cargo fetch --locked"
    return bool(proven_reason), uncertain_reason, proven_reason


def _new_prerequisites(
    rule: MakeRule, values: cabc.Mapping[str, str], seen: set[str]
) -> tuple[list[str], bool]:
    """Return the prerequisite names of *rule* not yet walked, and whether one hid.

    Returns
    -------
    tuple[list[str], bool]
        The new names, which are added to *seen*, and whether a reference in
        the prerequisites could not be resolved.
    """
    names: list[str] = []
    is_unresolved = False
    for reference in rule["prerequisites"]:
        expansion = expand(reference, values)
        if not expansion.is_resolved:
            is_unresolved = True
            continue
        fresh = [n for n in dict.fromkeys(expansion.text.split()) if n not in seen]
        names.extend(fresh)
        seen.update(fresh)
    return names, is_unresolved


def recipes_fetch(recipes: list[MakeRecipe], values: cabc.Mapping[str, str]) -> bool:
    """Return whether a recipe line fetches with its failure left fatal."""
    for recipe in recipes:
        expansion: Expansion = expand(recipe["text"], values)
        commands = script_commands(expansion.text)
        if not recipe["ignore_errors"] and line_fetches(
            commands, is_resolved=are_settled(commands, values)
        ):
            return True
    return False
