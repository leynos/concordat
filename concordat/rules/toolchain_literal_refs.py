"""Read whether a Make expression still names a variable nobody supplies.

A toolchain value can be a date (`nightly-2026-10-30`), a reference
(`$(DEV_FAST_TOOLCHAIN)`), a partly expanded value (`nightly-$(DATE)`), or a
value Make computes at build time (`$(shell cat tools/toolchain.txt)`). Only
the first is a restated literal and only the third is a missing variable; the
last writes no date down at all.

This module scans an expanded value for a plain `$(NAME)` that survived it,
tracking the delimiters of any enclosing Make call so a reference that is a
call's argument is not mistaken for a missing variable.
"""

from __future__ import annotations

import re
import typing as typ

# A plain `$(NAME)`/`${NAME}` that survived expansion: a missing variable, not
# a Make function computing a value from something else.
_SURVIVING_REFERENCE: typ.Final = re.compile(
    r"\$(?:\((?P<paren>[A-Za-z_][\w.-]*)\)|\{(?P<brace>[A-Za-z_][\w.-]*)\})"
)


def _opens_make_call(text: str, index: int) -> bool:
    """Return whether `$(` or `${` starts a call or reference at *index*.

    The delimiter is compared against a tuple: `"" in "({"` is true for a
    trailing `$`, which would otherwise read past the end of the text.

    Returns
    -------
    bool
        Whether *index* opens a Make call or reference.
    """
    if not text.startswith("$", index):
        return False
    return text[index + 1 : index + 2] in ("(", "{")


def _opens_a_missing_variable(text: str, index: int, closers: list[str]) -> bool:
    """Return whether a plain reference starts at *index* outside any call."""
    if closers:
        return False
    return _SURVIVING_REFERENCE.match(text, index) is not None


def _closer_for(text: str, index: int) -> str:
    """Return the character that closes the Make call at *index*."""
    if text[index + 1] == "(":
        return ")"
    return "}"


def _close_paren(closers: list[str], parens: list[int]) -> None:
    """Close a plain `(` if one is open, else the call when it expects `)`."""
    if parens[-1]:
        parens[-1] -= 1
        return
    if closers[-1] != ")":
        return
    closers.pop()
    parens.pop()


def _read_in_call(text: str, index: int, closers: list[str], parens: list[int]) -> int:
    """Read one character inside a Make call and return the next index.

    Ordinary parentheses inside a call are balanced first, and only the
    delimiter the call opened with closes it, so `$(shell echo ")")` stays one
    call rather than ending at the first `)` the regex-shaped text carries.

    Returns
    -------
    int
        The index to resume reading at.
    """
    char = text[index]
    if char == "(":
        parens[-1] += 1
    elif char == ")":
        _close_paren(closers, parens)
    elif char == closers[-1]:
        closers.pop()
        parens.pop()
    return index + 1


def has_missing_reference(text: str) -> bool:
    """Return whether a plain `$(NAME)` survives outside any Make call.

    A reference nested in `$(shell ...)` or `$(call ...)` is that call's
    argument, so the value is computed rather than missing. The call's own
    parentheses and delimiter are tracked so the scan cannot end it early.

    Returns
    -------
    bool
        Whether a missing variable remains at the top level of *text*.
    """
    closers: list[str] = []
    parens: list[int] = []
    index = 0
    while index < len(text):
        if _opens_make_call(text, index):
            if _opens_a_missing_variable(text, index, closers):
                return True
            closers.append(_closer_for(text, index))
            parens.append(0)
            index += 2
            continue
        if not closers:
            index += 1
            continue
        index = _read_in_call(text, index, closers, parens)
    return False
