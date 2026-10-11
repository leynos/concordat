"""Find the `cargo clippy` commands a Makefile's `lint` target reaches.

DG-004 needs to know that `make lint` really runs clippy, not that the word
appears somewhere in a recipe. Each recipe line is expanded, its Make
references reduced to single words (the estate builds `RUSTFLAGS` with
`$(if ...)` on the same line), and lexed; a run is a simple command whose
command word is `cargo` and whose subcommand is `clippy`.
"""

from __future__ import annotations

import re
import typing as typ

from .interrogate_invocation import lex_segments
from .makefile_expansion import UNRESOLVED_MARK, expand, shown
from .rust_source import scan_balanced

if typ.TYPE_CHECKING:
    import collections.abc as cabc

# A reference the expansion could not settle that is named for clippy, or that
# stands where the `cargo` word should and is followed by `clippy`.
_UNRESOLVED_CLIPPY: typ.Final = re.compile(
    r"\$[({][^)}\s]*clippy[^)}\s]*[)}]|\$[({][^)}]*[)}]\s+clippy(?![\w.-])",
    re.IGNORECASE,
)
_ASSIGNMENT: typ.Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
# Wrappers that run the next word as a command without changing which tool it is.
_WRAPPERS: typ.Final = frozenset({"env", "time", "nice", "command", "exec"})
# `cargo` flags that take a value as the next word, so that word is not the subcommand.
_CARGO_VALUE_FLAGS: typ.Final = frozenset({
    "-Z",
    "-C",
    "--config",
    "--color",
    "--manifest-path",
    "--target-dir",
})


class ClippyRun(typ.TypedDict):
    """One recipe line, in a target `lint` reaches, that runs clippy."""

    target: str
    line: int
    command: str
    resolved: bool


def _blank_references(text: str) -> str:
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


def _command_start(words: list[str]) -> int:
    """Return the index of the command word after assignments and wrappers."""
    index = 0
    while index < len(words):
        word = words[index].lstrip("@-+")
        if _ASSIGNMENT.match(word) or word in _WRAPPERS:
            index += 1
        elif word == "rustup" and words[index + 1 : index + 2] == ["run"]:
            index += 3
        else:
            break
    return index


def is_cargo_clippy(words: list[str]) -> bool:
    """Return whether one simple command is `cargo [flags] clippy ...`."""
    start = _command_start(words)
    if start >= len(words) or words[start].lstrip("@-+").rsplit("/", 1)[-1] != "cargo":
        return False
    arguments = iter(words[start + 1 :])
    for argument in arguments:
        if argument in _CARGO_VALUE_FLAGS:
            next(arguments, None)
        elif not argument.startswith(("-", "+")):
            return argument == "clippy"
    return False


def _run_on_line(target: str, line: int, expanded: str) -> ClippyRun | None:
    """Return the clippy run an expanded recipe line holds, if it holds one."""
    segments = lex_segments(_blank_references(expanded))
    is_run = any(is_cargo_clippy(words) for words in segments or [])
    is_unresolved = _UNRESOLVED_CLIPPY.search(shown(expanded)) is not None or (
        segments is None and "clippy" in expanded
    )
    if not (is_run or is_unresolved):
        return None
    return {
        "target": target,
        "line": line,
        "command": " ".join(shown(expanded).split()),
        "resolved": is_run,
    }


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
    for words in lex_segments(_blank_references(line)) or []:
        start = _command_start(words)
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


def reachable_lines(
    index: cabc.Mapping[str, cabc.Sequence[typ.Any]],
    reachable: list[str],
    values: cabc.Mapping[str, str],
) -> list[str]:
    """Return the expanded text of every recipe line in a reachable target.

    The exception record uses these to prove that a script it names is really
    run by `make lint`: the script's path has to appear in one of them.

    Returns
    -------
    list[str]
        The expanded recipe lines. Make references that could not be settled
        and shell dollars stay as the expansion's placeholders, so a line can
        still be lexed.
    """
    return [
        expand(text, values).text
        for target in reachable
        for rule in index[target]
        for _line, text in rule.recipes
    ]


def clippy_runs(
    index: cabc.Mapping[str, cabc.Sequence[typ.Any]],
    reachable: list[str],
    values: cabc.Mapping[str, str],
) -> list[ClippyRun]:
    """Return every recipe line in a reachable target that runs `cargo clippy`.

    *index* maps a target to its rules, each with a `recipes` list of
    `(line, text)` pairs. A run is a simple command whose command word is
    `cargo` (also through `env`, `time` or `rustup run`) and whose subcommand is
    `clippy`; `rustup component add clippy`, `cargo-clippy` in a test and
    `$(MAKE) clippy` are not runs (the last leads to a target that the
    reachability walk reads in its own right). Other parts of the line, such as
    a `RUSTFLAGS` value built with `$(if ...)`, do not change which tool runs.
    A line the lexer rejects, or that reaches clippy through an unresolved
    reference, is carried as unresolved so the policy cannot read it as proof
    either way.

    Returns
    -------
    list[ClippyRun]
        The lines in target and line order.
    """
    found = (
        _run_on_line(target, line, expand(text, values).text)
        for target in reachable
        for rule in index[target]
        for line, text in rule.recipes
    )
    return [run for run in found if run is not None]
