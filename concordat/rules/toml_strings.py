"""Find the lines of a TOML document that start inside a multi-line string.

A text scan for headers, keys and comments must not read string content as
structure: a multi-line string can quote a `[table]`, an `excluded_paths` key or
a `# comment`, and none of them is the document's own. This module reports the
0-based index of every line that begins inside a multi-line string. It decides
nothing else; the TOML parser stays the authority on values.

It reads the text as tokens: a comment, a one-line string (basic or literal) or
the opening quotes of a multi-line one, and skips each token whole, so a quote
inside a comment or a `#` inside a string is never mistaken for structure. From
an opening of a multi-line string it jumps to the closing quotes, taking the
escapes of a basic string (an escaped quote, and a backslash at a line end)
into account.
"""

from __future__ import annotations

import re
import typing as typ

__all__ = ["string_interior_lines"]

_BASIC: typ.Final = '"""'
_LITERAL: typ.Final = "'''"
# Longest opener first: `"""` must win over an empty one-line string.
_TOKEN: typ.Final = re.compile(r"\"\"\"|'''|#[^\n]*|\"(?:\\.|[^\"\\\n])*\"?|'[^'\n]*'?")
_BASIC_END: typ.Final = re.compile(r"(?:\\.|[^\\])*?\"\"\"", re.DOTALL)
_LITERAL_END: typ.Final = re.compile(r"[\s\S]*?'''")


def _end_of_string(text: str, opener: str, start: int) -> int:
    """Return where a multi-line string opened at *start* ends, or the text's end."""
    pattern = _BASIC_END if opener == _BASIC else _LITERAL_END
    closed = pattern.match(text, start)
    return closed.end() if closed else len(text)


def string_interior_lines(text: str) -> set[int]:
    """Return the 0-based indexes of the lines that start inside a multi-line string.

    Parameters
    ----------
    text:
        The TOML source.

    Returns
    -------
    set[int]
        The indexes of lines whose first character is string content.
    """
    inside: set[int] = set()
    position = 0
    while (token := _TOKEN.search(text, position)) is not None:
        position = token.end()
        if token.group() not in (_BASIC, _LITERAL):
            continue
        stop = _end_of_string(text, token.group(), position)
        first = text.count("\n", 0, position)
        inside.update(range(first + 1, first + text.count("\n", position, stop) + 1))
        position = stop
    return inside
