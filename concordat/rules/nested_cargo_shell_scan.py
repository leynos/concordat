"""Scan shell text for quoted strings, expansions and assignment values.

The NC-006 check-only reader needs three small facts about a `run` script that a
regular expression cannot give: where a quoted string ends (a double-quoted
string may nest `$(..)` with quotes of its own), where an assignment value ends
(`NAME="$(a)" b`), and the script with its quoted text blanked, so a bracket
inside a sed script is not read as a command. The scanners here answer exactly
those and nothing more; they never evaluate anything.
"""

from __future__ import annotations

import typing as typ

CLOSERS: typ.Final = {"(": ")", "{": "}"}
EXPANSIONS: typ.Final = frozenset({"$(", "${"})
# A value ends at whitespace or at a separator that is not quoted.
VALUE_STOPS: typ.Final = " \t\n;&|)"


def _past(script: str, marker: str, index: int) -> int:
    """Return the index just past the next *marker* after *index*, or the end."""
    found = script.find(marker, index)
    return len(script) if found < 0 else found + 1


def _double_quote_step(script: str, index: int) -> int:
    """Return the index after one unit inside a double-quoted string."""
    if script[index] == "\\":
        return index + 2
    if script[index : index + 2] in EXPANSIONS:
        return skip_expansion(script, index)
    return index + 1


def skip_quoted(script: str, index: int) -> int:
    """Return the index just past the quoted string that starts at *index*.

    Returns
    -------
    int
        The index after the closing quote, or past the end if it is missing.
    """
    if script[index] == "'":
        return _past(script, "'", index + 1)
    index += 1
    while index < len(script) and script[index] != '"':
        index = _double_quote_step(script, index)
    return index + 1


def skip_expansion(script: str, index: int) -> int:
    """Return the index just past the `$(..)` or `${..}` that starts at *index*.

    Returns
    -------
    int
        The index after the matching closer, quoted text and nesting included.
    """
    opener = script[index + 1]
    closer = CLOSERS[opener]
    depth = 0
    index += 1
    while index < len(script):
        char = script[index]
        if char in "\"'":
            index = skip_quoted(script, index)
            continue
        depth += (char == opener) - (char == closer)
        index += 1
        if depth == 0:
            break
    return index


def _skip_value_unit(script: str, index: int) -> int:
    """Return the index after one quoted, expanded or plain unit of a value."""
    if script[index] in "\"'":
        return skip_quoted(script, index)
    if script[index : index + 2] in EXPANSIONS:
        return skip_expansion(script, index)
    if script[index] == "`":
        return _past(script, "`", index + 1)
    return index + 1


def value_end(script: str, index: int) -> int:
    """Return the index where an assignment value that starts at *index* ends.

    The value runs to the next unquoted separator, skipping quoted strings,
    command substitutions, parameter expansions and backquotes whole.

    Returns
    -------
    int
        The index of the first character after the value.
    """
    while index < len(script) and script[index] not in VALUE_STOPS:
        index = _skip_value_unit(script, index)
    return index


def _mask_quoted(script: str, masked: list[str], start: int, end: int) -> None:
    """Blank the text of the quoted string `script[start:end]`, in place.

    An expansion nested inside double quotes stays visible, because it runs.
    """
    is_double = script[start] == '"'
    inner = start + 1
    limit = min(end - 1, len(script))
    while inner < limit:
        if is_double and script[inner : inner + 2] in EXPANSIONS:
            stop = skip_expansion(script, inner)
            _mask_range(script, masked, inner + 2, stop)
            inner = stop
            continue
        if script[inner] != "\n":
            masked[inner] = "_"
        inner += 1


def _mask_range(script: str, masked: list[str], low: int, high: int) -> None:
    """Blank the text of every quoted string in `script[low:high]`, in place."""
    index = low
    while index < high:
        if script[index] not in "\"'":
            index += 1
            continue
        end = skip_quoted(script, index)
        _mask_quoted(script, masked, index, end)
        index = end


def mask_quotes(script: str) -> str:
    """Return *script* with the text of its quoted strings blanked out.

    A quoted string is data (a sed script, a message), so its brackets and
    separators must not be read as commands. Offsets are kept, so a match on
    the masked copy lines up with the original.

    Returns
    -------
    str
        A copy of the same length, with quoted text replaced by underscores.
    """
    masked = list(script)
    _mask_range(script, masked, 0, len(script))
    return "".join(masked)
