"""Split shell and Makefile recipe text into simple commands.

Facts about what a workflow step or recipe runs have to come from a parse, not
a text search: a quoted `&&` is not an operator, `#` inside a URL is not a
comment, and the body of a here-document is data. This module does that split
for the `rust-prover-tooling` rule and nothing else; classifying a command is
`prover_commands`.

A Make reference or command substitution (`$(...)`) is masked to `$X` first,
because the scanner treats parentheses as operators and would otherwise split
`$(PROVER_TOOLS)` into pieces.
"""

from __future__ import annotations

import re
import shlex
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc

MARKER_PREFIX: typ.Final = "@-+"
OPERATORS: typ.Final = frozenset(";|&()")
REDIRECTS: typ.Final = frozenset("<>")
HEREDOC: typ.Final = re.compile(r"<<-?")
MASK: typ.Final = "$X"
QUOTES: typ.Final = frozenset({"'", '"'})
PAREN_DEPTH: typ.Final = {"(": 1, ")": -1}

type Argv = list[str]


def _reference_end(line: str, start: int) -> int:
    """Return the index just past the `)` closing the `(` at *start*."""
    depth = 1
    for index in range(start + 1, len(line)):
        depth += PAREN_DEPTH.get(line[index], 0)
        if depth == 0:
            return index + 1
    return len(line)


def mask_make_references(line: str) -> str:
    """Replace each `$(...)` Make reference or command substitution with `$X`.

    Returns
    -------
    str
        *line* with each balanced `$(...)` replaced by `$X`.
    """
    out: list[str] = []
    index = 0
    while index < len(line):
        is_reference = line.startswith("$(", index)
        out.append(MASK if is_reference else line[index])
        index = _reference_end(line, index + 1) if is_reference else index + 1
    return "".join(out)


def _next_state(
    char: str, quote: str | None, *, escaped: bool
) -> tuple[str | None, bool]:
    """Return the quote and escape state after reading *char*."""
    if escaped:
        return quote, False
    if char == "\\" and quote != "'":
        return quote, True
    if quote is None and char in QUOTES:
        return char, False
    return (None if char == quote else quote), False


def _quoted_flags(line: str) -> list[bool]:
    """Return, for each character of *line*, whether it is quoted or escaped."""
    flags: list[bool] = []
    quote: str | None = None
    escaped = False
    for char in line:
        flags.append(bool(quote) or escaped or char in QUOTES)
        quote, escaped = _next_state(char, quote, escaped=escaped)
    return flags


def _is_redirect_ampersand(line: str, index: int) -> bool:
    """Return whether the `&` at *index* is part of `>&2` or `&>`."""
    before = line[index - 1] if index else ""
    after = line[index + 1] if index + 1 < len(line) else ""
    return before in REDIRECTS or after == ">"


def _is_operator(line: str, index: int) -> bool:
    """Return whether the unquoted character at *index* separates commands."""
    if line[index] not in OPERATORS:
        return False
    return not (line[index] == "&" and _is_redirect_ampersand(line, index))


def _is_comment_start(line: str, index: int) -> bool:
    """Return whether the unquoted character at *index* begins a shell comment."""
    return line[index] == "#" and (index == 0 or line[index - 1].isspace())


def _split_operators(line: str) -> list[str]:
    """Split *line* at operators outside quotes, ending it at a comment.

    Returns
    -------
    list[str]
        The non-empty segments, each still carrying its own quoting.
    """
    quoted = _quoted_flags(line)
    segments: list[str] = []
    start = 0
    for index in range(len(line)):
        if quoted[index]:
            continue
        if _is_comment_start(line, index):
            segments.append(line[start:index])
            return _non_empty(segments)
        if _is_operator(line, index):
            segments.append(line[start:index])
            start = index + 1
    segments.append(line[start:])
    return _non_empty(segments)


def _non_empty(segments: cabc.Iterable[str]) -> list[str]:
    """Return the stripped segments that are not blank."""
    return [stripped for segment in segments if (stripped := segment.strip())]


def _words_of(segment: str) -> Argv:
    """Return one segment's words, or none when it does not tokenize."""
    try:
        return shlex.split(segment, comments=False, posix=True)
    except ValueError:
        return []


def _heredoc_delimiter(argv: Argv) -> str | None:
    """Return the delimiter of a here-document the command opens, if any."""
    for position, word in enumerate(argv):
        match = HEREDOC.match(word)
        if match is not None:
            following = argv[position + 1 : position + 2]
            delimiter = word[match.end() :] or "".join(following)
            return delimiter or None
    return None


def _line_commands(raw: str) -> list[Argv]:
    """Return the commands on one line."""
    line = raw.strip().lstrip(MARKER_PREFIX)
    segments = _split_operators(mask_make_references(line))
    return [argv for segment in segments if (argv := _words_of(segment))]


def _first_delimiter(argvs: list[Argv]) -> str | None:
    """Return the first here-document delimiter any of *argvs* opens."""
    return next((found for argv in argvs if (found := _heredoc_delimiter(argv))), None)


def commands(text: str) -> list[Argv]:
    """Split shell or recipe text into one argument vector per simple command.

    Parameters
    ----------
    text:
        A `run` script or a Makefile recipe. Backslash continuations are
        joined, a leading Make recipe marker (`@`, `-`, `+`) is dropped, a
        `#` at the start of a word begins a comment, the body of a
        here-document is not a command, and a line that does not tokenize is
        skipped.

    Returns
    -------
    list[Argv]
        The commands, split on `&&`, `||`, `;`, `|` and `&` outside quotes.
    """
    found: list[Argv] = []
    delimiter: str | None = None
    for raw in text.replace("\\\n", " ").splitlines():
        if delimiter is not None:
            delimiter = None if raw.strip() == delimiter else delimiter
            continue
        argvs = _line_commands(raw)
        found.extend(argvs)
        delimiter = _first_delimiter(argvs)
    return found
