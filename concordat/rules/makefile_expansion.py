"""Expand the variables a `makeutil` report records, the way Make would.

`makeutil parse` reports each assignment with its raw value and conditions but
substitutes nothing. A policy that has to read a recipe such as
`$(INTERROGATE) $(FLAGS) $(TARGETS)` needs the substituted text. This module
does the mechanical part for the envelope builders that need it: it keeps the
variables that have one unconditional value, substitutes them (and a few text
functions that build file names) until the text settles, and says whether any
reference survived. A reference that survives is an unresolved fact for the
caller to carry, never a guess.

`$$` is a shell dollar, not a reference. It stays as a placeholder in the
expanded text so a caller can tell shell substitution from a Make reference
that was left behind; :func:`shown` turns the placeholders back into `$`.
"""

from __future__ import annotations

import dataclasses
import posixpath
import re
import types
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from .makefile_facts import MakeVariable

SHELL_DOLLAR: typ.Final = "\x00"
# Stands for a Make reference the expansion left; it lexes as a word of its own.
UNRESOLVED_MARK: typ.Final = "\x01"

# How many times a value may name another variable before the expansion gives
# up. A cycle never settles, so it ends as unresolved rather than looping.
EXPANSION_PASSES: typ.Final = 8

# Variables GNU Make defines itself. The Makefile under audit is the root one,
# so its own list is that file alone.
BUILTIN_VARIABLES: typ.Final = types.MappingProxyType({
    "MAKEFILE_LIST": "Makefile",
    "MAKE": "make",
    "CURDIR": ".",
})

_VARIABLE_REFERENCE: typ.Final = re.compile(r"\$[({]([A-Za-z_][A-Za-z0-9_]*)[)}]")
_UNRESOLVED_REFERENCE: typ.Final = re.compile(r"\$[({]")
_FUNCTION_CALL: typ.Final = re.compile(
    r"\$\((dir|notdir|lastword|firstword|abspath|realpath|strip)\s+([^()$]*)\)"
)


@dataclasses.dataclass(frozen=True, slots=True)
class Expansion:
    """A Make text after variable substitution, and whether it fully resolved."""

    text: str
    is_resolved: bool


def shown(text: str) -> str:
    """Return *text* with the placeholders turned back into what they stood for."""
    return text.replace(SHELL_DOLLAR, "$").replace(UNRESOLVED_MARK, "$")


def joined(text: str) -> str:
    """Return *text* with backslash-newline continuations folded to spaces."""
    return re.sub(r"\\\n[ \t]*", " ", text)


def _is_plain(variable: MakeVariable, count: int) -> bool:
    """Return whether *variable* is assigned once, unconditionally, outside `define`."""
    conditions = variable.get("conditions", [])
    has_conditions = isinstance(conditions, list) and bool(conditions)
    return count == 1 and not variable.get("define_block", False) and not has_conditions


def single_assignments(variables: cabc.Sequence[MakeVariable]) -> dict[str, str]:
    r"""Return each variable that has exactly one plain assignment, and its value.

    A variable assigned twice, conditionally or in a `define` block has no one
    value the expansion can substitute, so it is left out and a reference to it
    stays unresolved. Make turns `\#` in a value into `#`, so that is undone
    here and in no recipe.

    Returns
    -------
    dict[str, str]
        Variable name to its raw value.
    """
    names = [str(variable.get("name", "")) for variable in variables]
    return {
        name: joined(str(variable.get("raw_value", ""))).replace("\\#", "#")
        for name, variable in zip(names, variables, strict=True)
        if _is_plain(variable, names.count(name))
    }


def _dir(words: list[str]) -> list[str]:
    """Return each word's directory part, `./` when it has none."""
    return [word[: word.rfind("/") + 1] if "/" in word else "./" for word in words]


def _notdir(words: list[str]) -> list[str]:
    """Return each word without its directory part."""
    return [word.rsplit("/", 1)[-1] for word in words]


def _lastword(words: list[str]) -> list[str]:
    """Return the last word, or nothing."""
    return words[-1:]


def _firstword(words: list[str]) -> list[str]:
    """Return the first word, or nothing."""
    return words[:1]


def _normalised(words: list[str]) -> list[str]:
    """Return each word as a normalised relative path."""
    return [posixpath.normpath(word) for word in words]


_FUNCTIONS: typ.Final[dict[str, cabc.Callable[[list[str]], list[str]]]] = {
    "dir": _dir,
    "notdir": _notdir,
    "lastword": _lastword,
    "firstword": _firstword,
    "strip": list,
    "abspath": _normalised,
    "realpath": _normalised,
}


def _call_function(match: re.Match[str]) -> str:
    """Evaluate the Make text functions that build file names.

    `abspath` and `realpath` normalise and stay relative: the checkout is the
    Makefile's own directory, which is all those calls name in practice.

    Returns
    -------
    str
        The function's result, as Make would produce it for these arguments.
    """
    return " ".join(_FUNCTIONS[match.group(1)](match.group(2).split()))


def expand(text: str, values: cabc.Mapping[str, str]) -> Expansion:
    """Substitute `$(NAME)` references until the text settles.

    Any reference left after the passes (a Make function this module does not
    evaluate, an unset or ambiguous variable, a cycle) marks the expansion
    unresolved.

    Returns
    -------
    Expansion
        The substituted text and whether every reference was resolved.
    """
    known = {**BUILTIN_VARIABLES, **values}

    def substitute(match: re.Match[str]) -> str:
        value = known.get(match.group(1))
        # A `$$` in a value is a shell dollar once the reference is expanded.
        return match.group(0) if value is None else value.replace("$$", SHELL_DOLLAR)

    current = text.replace("$$", SHELL_DOLLAR)
    for _ in range(EXPANSION_PASSES):
        updated = _FUNCTION_CALL.sub(
            _call_function, _VARIABLE_REFERENCE.sub(substitute, current)
        )
        if updated == current:
            break
        current = updated
    return Expansion(current, _UNRESOLVED_REFERENCE.search(current) is None)
