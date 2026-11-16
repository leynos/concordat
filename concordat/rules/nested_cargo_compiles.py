"""Say whether a cargo command's arguments start a compile.

`cargo metadata`, `cargo fetch` and `cargo package --list` read a manifest but
build nothing, so they pay for no cold build. A command whose subcommand hides
behind a word the reader could not see (`cargo(args)`, `.args(subcommand)`) may
build or not, and that is reported as such rather than guessed. The compiling
subcommands are the table the nextest rule's spawner scan already uses, so the
two rules agree on what a compile is.
"""

from __future__ import annotations

import re
import typing as typ

from .nested_cargo_commands import ARGUMENT_METHODS, UNSEEN_ARGUMENTS
from .nested_cargo_spawn import SUBCOMMANDS
from .nested_cargo_text import argument_items

if typ.TYPE_CHECKING:
    import collections.abc as cabc

COMPILES_YES: typ.Final = "yes"
COMPILES_NO: typ.Final = "no"
COMPILES_UNKNOWN: typ.Final = "unknown"

COMPILING_SUBCOMMANDS: typ.Final = frozenset(SUBCOMMANDS.split("|"))
# Options cargo reads one value for before the subcommand, so the value is not
# mistaken for it (`cargo --color never build`).
_LEADING_VALUE_FLAGS: typ.Final = frozenset({
    "--color",
    "--manifest-path",
    "--config",
    "-C",
    "-Z",
})
_NON_COMPILING_FLAG: typ.Final = "--unit-graph"
_LISTING_FLAGS: typ.Final = frozenset({"--list", "-l"})
_LITERAL: typ.Final = re.compile(r'"(?:[^"\\]|\\.)*"')


def _argument_words(calls: cabc.Iterable[tuple[str, str]]) -> list[str | None]:
    """Return a command's arguments in order, one word per argument.

    Each argument is read on its own, so `.args(["config", "get", query])`
    yields `config` and `get` before the word it cannot read. A call the reader
    could not see through is an unreadable word.

    Returns
    -------
    list[str | None]
        The literal text of each argument, None where it is not a literal.
    """
    words: list[str | None] = []
    for method, payload in calls:
        if method == UNSEEN_ARGUMENTS:
            words.append(None)
        elif method in ARGUMENT_METHODS:
            words.extend(
                item[1:-1] if _LITERAL.fullmatch(item) else None
                for item in argument_items(payload)
            )
    return words


def compiles_state(calls: cabc.Iterable[tuple[str, str]]) -> str:
    """Say whether a cargo command's arguments start a compile.

    Returns
    -------
    str
        `yes` for a compiling subcommand, `no` for any other literal one, and
        `unknown` when an unreadable word comes before the subcommand.
    """
    words = _argument_words(calls)
    # `--unit-graph` prints the units cargo would build and builds none, so it
    # settles the answer even behind an argument the reader cannot see.
    cargo_words = words[: words.index("--")] if "--" in words else words
    if _NON_COMPILING_FLAG in cargo_words:
        return COMPILES_NO
    for index, word in _positional_words(words):
        if word is None:
            return COMPILES_UNKNOWN
        return _subcommand_state(word, words[index + 1 :])
    return COMPILES_NO


def _positional_words(
    words: list[str | None],
) -> cabc.Iterator[tuple[int, str | None]]:
    """Yield the first word that is neither an option nor an option's value.

    An unreadable word is yielded as None and ends the scan, because it may be
    the subcommand or the value of an option before it.

    Yields
    ------
    tuple[int, str | None]
        The word's index and text, at most once.
    """
    skip_value = False
    for index, word in enumerate(words):
        if word is None:
            yield index, None
            return
        if skip_value:
            skip_value = False
        elif word in _LEADING_VALUE_FLAGS:
            skip_value = True
        elif not word.startswith(("-", "+")):
            yield index, word
            return


def _subcommand_state(subcommand: str, rest: list[str | None]) -> str:
    """Say whether one literal subcommand compiles, given the words after it.

    Returns
    -------
    str
        `no` for a subcommand that builds nothing and for `package --list`,
        `yes` otherwise.
    """
    if subcommand not in COMPILING_SUBCOMMANDS:
        return COMPILES_NO
    if subcommand == "package" and _LISTING_FLAGS.intersection(rest):
        return COMPILES_NO
    return COMPILES_YES
