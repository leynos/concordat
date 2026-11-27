"""Find the exclusions in a `dylint.toml` and whether each carries a reason.

A parsed TOML document has no comments, and a comment is where the estate
records why a Whitaker exclusion exists (netsuke's and whitaker's own
`dylint.toml` do). So the reason is read from the text: the comment block
directly above the exclusion key, or, when there is none, the block directly
above the table's header. A blank line ends a block. A block of bare `#` lines
says nothing, so it does not count.

The scan is deliberately narrow. It finds keys named `excluded_*` at the start
of a line, under the table header it last saw, and leaves everything else to
the TOML parser; a key it cannot find in the text (a dotted key, an inline
table) is reported as not located, not guessed at.
"""

from __future__ import annotations

import re
import typing as typ

__all__ = ["Located", "locate_exclusions"]

_TABLE: typ.Final = re.compile(r"^\s*\[\[?\s*([^\[\]]+?)\s*\]\]?\s*(?:#.*)?$")
_KEY: typ.Final = re.compile(r"^\s*(excluded_[A-Za-z0-9_]+)\s*=")
_COMMENT: typ.Final = re.compile(r"^\s*#(.*)$")


class Located(typ.NamedTuple):
    """An exclusion key found in the text: where it sits and whether it has a reason."""

    table: str
    key: str
    line: int
    has_reason: bool


def _has_reason_above(lines: list[str], index: int) -> bool:
    """Return whether the comment block ending just above *index* says anything."""
    found = False
    position = index - 1
    while position >= 0:
        comment = _COMMENT.match(lines[position])
        if comment is None:
            break
        found = found or bool(comment.group(1).strip())
        position -= 1
    return found


def locate_exclusions(text: str) -> list[Located]:
    """Return every `excluded_*` key in *text* with its table and reason.

    Parameters
    ----------
    text:
        The `dylint.toml` source.

    Returns
    -------
    list[Located]
        One entry per key in file order. `line` is 1-based, and the table is
        `""` for a key above any header.
    """
    lines = text.splitlines()
    table = ""
    header_at: int | None = None
    found: list[Located] = []
    for index, line in enumerate(lines):
        header = _TABLE.match(line)
        if header is not None:
            table, header_at = header.group(1), index
            continue
        key = _KEY.match(line)
        if key is None:
            continue
        has_reason = _has_reason_above(lines, index) or (
            header_at is not None and _has_reason_above(lines, header_at)
        )
        found.append(Located(table, key.group(1), index + 1, has_reason))
    return found
