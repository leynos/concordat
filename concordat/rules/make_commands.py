"""Read the commands in expanded Makefile recipe lines.

Recipe lines carry the Make references the expansion could not settle and the
estate's `$(if ...)`-built `RUSTFLAGS`, so they cannot go straight to the shell
lexer. This module reduces those references to single words, finds the command
word of a simple command, and decides whether a command runs a given script.
"""

from __future__ import annotations

import re
import typing as typ

from .interrogate_invocation import ENV_VALUE_OPTIONS, lex_segments
from .makefile_expansion import UNRESOLVED_MARK
from .rust_source import scan_balanced

_ASSIGNMENT: typ.Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
# Wrappers that run the next word as a command without changing which tool it is.
# `env` is not one of these: its options take values, so it is walked separately.
_WRAPPERS: typ.Final = frozenset({"time", "nice", "command", "exec"})


def blank_references(text: str) -> str:
    """Replace each balanced `$(...)` or `${...}` Make reference with one word.

    A reference that names cargo (`$(CARGO)`, or the
    `$(or $(shell command -v cargo), ...)` it expands to when the variable is
    conditional) stands for the tool and becomes the word `cargo`; any other
    becomes the unresolved mark.

    Returns
    -------
    str
        The text with each reference replaced by a single word.
    """
    out: list[str] = []
    index = 0
    while index < len(text):
        end = (
            scan_balanced(text, index + 1)
            if text.startswith(("$(", "${"), index)
            else None
        )
        if end is None:
            out.append(text[index])
            index += 1
            continue
        out.append(" cargo " if "cargo" in text[index:end].lower() else UNRESOLVED_MARK)
        index = end
    return "".join(out)


def _skip_env(words: list[str], index: int) -> int:
    """Return the index past an `env` command's options and assignments.

    `env -u CARGO_NET_OFFLINE cargo fetch --locked` runs cargo, not `-u`.
    Reading the option as the command word hides the fetch, which is exactly
    the mitigation this audit exists to reward. Options that take a value are
    skipped with it, and `--` ends the option list.

    Returns
    -------
    int
        The index of the word after the `env` prefix.
    """
    if index >= len(words) or words[index].lstrip("@-+") != "env":
        return index
    index += 1
    while index < len(words):
        word = words[index]
        if word == "--":
            return index + 1
        if word.startswith("-"):
            index += 2 if word in ENV_VALUE_OPTIONS else 1
        elif _ASSIGNMENT.match(word):
            index += 1
        else:
            break
    return index


def command_start(words: list[str]) -> int:
    """Return the index of the command word after assignments and wrappers."""
    index = 0
    while index < len(words):
        word = words[index].lstrip("@-+")
        if _ASSIGNMENT.match(word):
            index += 1
        elif word == "env":
            index = _skip_env(words, index)
        elif word in _WRAPPERS:
            index += 1
        elif word == "rustup" and words[index + 1 : index + 2] == ["run"]:
            index += 3
        else:
            break
    return index


# Commands that name a path without running it.
_NON_EXECUTING: typ.Final = frozenset({
    "echo",
    "printf",
    "cat",
    "ls",
    "test",
    "[",
    "grep",
    "head",
    "tail",
    "wc",
    "stat",
    "file",
    "rm",
    "cp",
    "mv",
    "touch",
    "chmod",
    "ln",
    "diff",
    "cmp",
})
# Commands that run a script handed to them as an argument.
_INTERPRETERS: typ.Final = frozenset({
    "sh",
    "bash",
    "zsh",
    "dash",
    "ksh",
    "python",
    "python3",
    "pypy",
    "pypy3",
    "uv",
    "uvx",
    "node",
    "bun",
    "deno",
    "ruby",
    "perl",
    "env",
    "exec",
    "xargs",
    "nice",
    "time",
    "pipx",
    "poetry",
    "npx",
    "pnpm",
    "npm",
})


def invokes_script(line: str, script: str) -> bool:
    """Return whether one expanded recipe line runs *script*.

    The script has to be the command, or an argument of an interpreter or
    launcher (`sh scripts/x.sh`, `uv run --script scripts/x.py`). Naming it in
    `echo`, `cat`, `test -x` and the like does not run it.

    Returns
    -------
    bool
        Whether a simple command on the line runs the script.
    """
    spellings = {script, f"./{script}"}
    for words in lex_segments(blank_references(line)) or []:
        start = command_start(words)
        if start >= len(words):
            continue
        command = words[start].lstrip("@-+")
        name = command.rsplit("/", 1)[-1]
        if name in _NON_EXECUTING:
            continue
        if command in spellings:
            return True
        if name in _INTERPRETERS and spellings & set(words[start + 1 :]):
            return True
    return False
