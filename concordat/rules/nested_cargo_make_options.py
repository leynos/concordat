"""Read the options of a `make` command line that change what its goals mean.

A `make` goal in a recipe names a target of the checkout's own Makefile, unless
an option points `make` elsewhere or stops it running anything. The reader that
follows such goals as preparation must not follow these.
"""

from __future__ import annotations

import re
import typing as typ

#: A make goal the preparation reader follows: a plain target name, with no
#: variable, glob or shell computation in it.
PLAIN_GOAL: typ.Final = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-/]*")

#: Short `make` options that take an argument, so what follows them in one word
#: is that argument and not more options.
SHORT_OPTIONS_WITH_ARGUMENT: typ.Final = "CfIjloWEO"

#: Short options that read another directory or Makefile, or run nothing.
DIVERTING_SHORT_OPTIONS: typ.Final = "Cfn"

#: Long `make` options that read another directory or Makefile, or run nothing.
DIVERTING_LONG_OPTIONS: typ.Final = (
    "--directory",
    "--file",
    "--makefile",
    "--dry-run",
    "--just-print",
    "--recon",
)


def _short_options(word: str) -> str:
    """Return the option letters of a short-option word, up to its argument."""
    if word.startswith("--") or not word.startswith("-"):
        return ""
    letters = ""
    for letter in word[1:]:
        letters += letter
        if letter in SHORT_OPTIONS_WITH_ARGUMENT:
            break
    return letters


#: Short options that, besides diverting, stop a recipe failing the command
#: (`-i`) or run nothing real (`-t` touches, `-q` only asks).
UNRELIABLE_SHORT_OPTIONS: typ.Final = DIVERTING_SHORT_OPTIONS + "itq"

#: Long forms of the unreliable options, added to the diverting ones.
UNRELIABLE_LONG_OPTIONS: typ.Final = (
    *DIVERTING_LONG_OPTIONS,
    "--touch",
    "--question",
    "--ignore-errors",
)


def _selects(arguments: list[str], short: str, long: tuple[str, ...]) -> bool:
    """Return whether any word is one of the *short* letters or *long* options."""
    return any(
        word.startswith(long) or any(letter in short for letter in _short_options(word))
        for word in arguments
    )


def unreliable_make(arguments: list[str]) -> bool:
    """Return whether the options make `make`'s result no proof that a build ran.

    Beyond the diverting options, `-t` touches targets instead of running their
    recipes, `-q` only asks whether they are up to date, and `-i` ignores a
    failing recipe, so a goal run under them is not a success-gated build.

    Returns
    -------
    bool
        True when any word is one of those options or a diverting one.
    """
    return _selects(arguments, UNRELIABLE_SHORT_OPTIONS, UNRELIABLE_LONG_OPTIONS)


def followable_goals(arguments: list[str], goals: list[str]) -> tuple[str, ...]:
    """Return the one goal a make invocation runs as a plain, named target.

    The preparation reader follows a goal only in the forms it models exactly:
    one goal named by plain text, no `VAR=value` override (it can change what
    the goal does), and no option that diverts, touches, asks or ignores errors.
    A group of goals (which `-j` runs together), a goal computed by the shell
    and any other shape is not preparation, so this returns nothing for them.

    Returns
    -------
    tuple[str, ...]
        The one goal, or an empty tuple for any shape the reader does not model.
    """
    if unreliable_make(arguments) or any("=" in word for word in arguments):
        return ()
    if len(goals) != 1 or not PLAIN_GOAL.fullmatch(goals[0]):
        return ()
    return (goals[0],)


def diverts_make(arguments: list[str]) -> bool:
    """Return whether the options point `make` at another Makefile or at nothing.

    `-C` and `-f` (and their long forms) read a Makefile other than the
    checkout's, so its goals are not the checkout's targets. `-n` prints the
    recipes without running them, so a target it names prepares nothing.

    Returns
    -------
    bool
        True when any word selects a directory or Makefile, or asks for a dry run.
    """
    return _selects(arguments, DIVERTING_SHORT_OPTIONS, DIVERTING_LONG_OPTIONS)
