"""Read the options of a `make` command line that change what its goals mean.

A `make` goal in a recipe names a target of the checkout's own Makefile, unless
an option points `make` elsewhere or stops it running anything. The reader that
follows such goals as preparation must not follow these.
"""

from __future__ import annotations

import typing as typ

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
    return any(
        word.startswith(DIVERTING_LONG_OPTIONS)
        or any(letter in DIVERTING_SHORT_OPTIONS for letter in _short_options(word))
        for word in arguments
    )
