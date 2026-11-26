"""Lex one Makefile command into the cargo or nextest words a pass signature reads.

The pass reader compares what two commands build. Before it can, it has to turn
the text of a recipe command into words: keep every Make reference the expansion
left as one inert word, drop the shell keywords and `NAME=value` assignments that
lead a command, and find the runner (`cargo` or `nextest`), its subcommand and the
options in front of it (`+toolchain`, `--config`, `-Z`) that change what builds.
"""

from __future__ import annotations

import re
import typing as typ

from .interrogate_invocation import lex_segments
from .make_commands import command_start
from .makefile_expansion import UNRESOLVED_MARK

# A Make reference the expansion left becomes this stand-in plus its number, so
# two commands that carry the same reference text compare equal while a
# different one does not. The lexer sees one ordinary word.
REFERENCE_TAG: typ.Final = "\x03"
# Whitespace, quotes and shell operators inside a reference, made inert.
_WORD_SAFE: typ.Final = str.maketrans({
    " ": "\x04",
    "\t": "\x04",
    "\n": "\x04",
    "'": "\x05",
    '"': "\x05",
    "|": "\x06",
    ";": "\x06",
    "&": "\x06",
    "<": "\x06",
    ">": "\x06",
    "(": "\x07",
    ")": "\x07",
    "{": "\x07",
    "}": "\x07",
    "$": "\x08",
    "\\": "\x08",
    "#": "\x08",
})
ASSIGNMENT: typ.Final = re.compile(
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>.*)", re.DOTALL
)

# Shell words that introduce a command without being one.
_KEYWORDS: typ.Final = frozenset({
    "if",
    "then",
    "else",
    "elif",
    "do",
    "while",
    "until",
    "!",
    "{",
    "time",
})
# Cargo's own global options, in front of the subcommand.
_CARGO_GLOBALS: typ.Final = frozenset({
    "--locked",
    "--frozen",
    "--offline",
    "--quiet",
    "-q",
    "--verbose",
    "-v",
    "--color",
    "-C",
    "--config",
    "-Z",
})
_VALUE_GLOBALS: typ.Final = frozenset({"--color", "-C", "--config", "-Z"})
# Globals that change what builds (the working directory, configuration and
# unstable flags); the others only change how cargo talks.
_BUILD_GLOBALS: typ.Final = frozenset({"-C", "--config", "-Z"})
_GLOBAL_PREFIXES: typ.Final = ("+", "--config=", "-Z")


class Command(typ.NamedTuple):
    """Hold the runner a command names and what follows it.

    Attributes
    ----------
    runner:
        `cargo` or `nextest`.
    subcommand:
        The subcommand word (`test`, `build`, `run`, `archive`, ...).
    arguments:
        The words after the subcommand.
    options:
        The `+toolchain`, `--config`, `-Z` and `-C` words before the subcommand,
        in order, each with its value.
    """

    runner: str
    subcommand: str
    arguments: list[str]
    options: list[str]


def _reference_end(text: str, start: int) -> int | None:
    """Return the index past the Make reference opening at *start*, or None.

    Make counts only its own brackets, whatever quotes sit between them, so a
    `$(shell cut -d' ' -f1)` closes where the parentheses balance.

    Returns
    -------
    int | None
        The index after the closing bracket; None when it never closes.
    """
    opener = text[start + 1]
    closer = ")" if opener == "(" else "}"
    depth = 0
    for index in range(start + 1, len(text)):
        depth += (text[index] == opener) - (text[index] == closer)
        if depth == 0:
            return index + 1
    return None


def _reference_at(text: str, index: int) -> int | None:
    """Return where the Make reference opening at *index* ends, if one opens."""
    if not text.startswith(("$(", "${"), index):
        return None
    return _reference_end(text, index)


def tag_references(text: str) -> str:
    """Replace each balanced Make reference by a numbered stand-in word.

    Returns
    -------
    str
        The text with every `$(...)` and `${...}` reference reduced to one word
        that carries the reference's own text, so equal references stay equal.
    """
    out: list[str] = []
    index = 0
    while index < len(text):
        end = _reference_at(text, index)
        if end is None:
            out.append(text[index])
            index += 1
            continue
        # Characters that would split or quote a word become marks no flag
        # contains, so the reference stays one word whatever it holds.
        body = text[index:end].translate(_WORD_SAFE)
        out.append(REFERENCE_TAG + body + REFERENCE_TAG)
        index = end
    return "".join(out)


def is_opaque(word: str) -> bool:
    """Return whether *word* still carries a reference the expansion left."""
    return REFERENCE_TAG in word or UNRESOLVED_MARK in word


def word_at(words: list[str], index: int) -> str | None:
    """Return the word at *index*, or None past the end."""
    return words[index] if index < len(words) else None


def _without_keywords(words: list[str]) -> list[str]:
    """Drop the shell keywords that introduce the command on a line.

    A line such as `if command -v cargo-nextest; then RUSTFLAGS=.. cargo ..`
    leads with the keyword that introduces its command.

    Returns
    -------
    list[str]
        The words from the first one that is not a keyword.
    """
    start = 0
    while start < len(words) and words[start].lstrip("@-+") in _KEYWORDS:
        start += 1
    return words[start:]


def lexed(text: str) -> tuple[list[str], list[str]] | None:
    """Return the assignment words and the command words of one command.

    Returns
    -------
    tuple[list[str], list[str]] | None
        The `NAME=value` words before the command word (including those an
        `env` prefix carries), and the words from the command word on; None
        when the text does not lex or holds no command.
    """
    segments = lex_segments(tag_references(text.replace("\\\n", " ")))
    if not segments or not segments[0]:
        return None
    words = _without_keywords(segments[0])
    start = command_start(words)
    if start >= len(words):
        return None
    assignments = [word for word in words[:start] if ASSIGNMENT.match(word)]
    command = [
        word.lstrip("@-+") if i == 0 else word for i, word in enumerate(words[start:])
    ]
    return assignments, command


def has_unresolved_command_word(text: str) -> bool:
    """Return whether the command word is a Make reference the expansion left.

    Such a command may be a test runner, so a pass that hides behind it cannot
    be judged and the caller reports it as indeterminate.

    Returns
    -------
    bool
        True when the word that names the command carries a reference.
    """
    found = lexed(text)
    return found is not None and is_opaque(found[1][0])


def _is_global(word: str) -> bool:
    """Return whether *word* is a cargo global option or a `+toolchain`."""
    return word in _CARGO_GLOBALS or word.startswith(_GLOBAL_PREFIXES)


def _global_at(rest: list[str], index: int, options: list[str]) -> int:
    """Read the global option at *index*, noting it in *options*; return its width."""
    word = rest[index]
    if word in _VALUE_GLOBALS:
        if word in _BUILD_GLOBALS:
            options.append(f"{word}={word_at(rest, index + 1)}")
        return 2
    if word.startswith(_GLOBAL_PREFIXES):
        options.append(word)
    return 1


def _cargo_command(rest: list[str]) -> Command | None:
    """Read the words after `cargo`: its globals, its subcommand and arguments."""
    options: list[str] = []
    index = 0
    while index < len(rest) and _is_global(rest[index]):
        index += _global_at(rest, index, options)
    if index >= len(rest):
        return None
    subcommand, tail = rest[index], rest[index + 1 :]
    if subcommand == "nextest":
        return _nextest_command(tail, options)
    if subcommand == "ntr":
        return Command("nextest", "ntr", tail, options)
    return Command("cargo", subcommand, tail, options)


def _nextest_command(tail: list[str], options: list[str]) -> Command | None:
    """Read the words after `nextest`: its subcommand and arguments."""
    if not tail:
        return None
    return Command("nextest", tail[0], tail[1:], options)


def read_command(words: list[str]) -> Command | None:
    """Return the runner, its subcommand and the words around it.

    Returns
    -------
    Command | None
        `cargo` or `nextest` as the runner with the subcommand, the arguments
        after it and the build-changing options before it; None for any other
        command.
    """
    name = words[0].rsplit("/", 1)[-1]
    rest = words[1:]
    if name == "cargo-nextest":
        return _nextest_command(rest[1:] if rest[:1] == ["nextest"] else rest, [])
    if name == "cargo":
        return _cargo_command(rest)
    return None
