"""Classify the commands that install or run a prover (`rust-prover-tooling`).

The rule has to tell a command that installs Kani, Verus or Z3 from one that
only runs proofs on a tool that is already there. A step that runs
`make verus` on an installed verifier is compliant; one whose Make target
depends on `verus-install` is not. A text search cannot make that distinction
(`printf 'prover-tools verus install'` mentions the installer without running
it), so the classification reads parsed command lines: each shell line is
split into simple commands with `shlex`, and each command's argument vector is
inspected.

The installer forms recognised are the estate's own runner
(`prover-tools <tool> install`, whatever wraps it), `cargo install` or
`cargo binstall` of `kani-verifier`, and `cargo kani setup`. A runner is
`verus`, `cargo kani`, or `prover-tools <tool> run|exec|check-version`.

Make variable references (`$(PROVER_TOOLS)`) are not expanded: they are
masked and skipped, so a recipe that hides the tool noun inside a variable is not seen.
That is a documented limit of the rule, not a verdict.
"""

from __future__ import annotations

import itertools
import re
import shlex
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc

TOOLS: typ.Final = frozenset({"kani", "verus", "z3"})
RUN_VERBS: typ.Final = frozenset({"run", "exec", "check-version"})
# Commands whose arguments are text, so a prover command named there is
# mentioned rather than run.
PRINTING_COMMANDS: typ.Final = frozenset({"printf", "echo", "cat", "grep", "test", "["})
# Commands that run their arguments as a command after options of their own.
WRAPPERS: typ.Final = frozenset({
    "env",
    "command",
    "exec",
    "time",
    "nice",
    "nohup",
    "sudo",
})
ENV_OPTIONS_WITH_ARGUMENT: typ.Final = frozenset({"-u", "-C", "-S", "--unset"})
RUNNER_BINARIES: typ.Final = frozenset({"verus", "cargo-kani", "kani-verifier"})
PROVER_TOOLS: typ.Final = "prover-tools"
MARKER_PREFIX: typ.Final = "@-+"
OPERATORS: typ.Final = frozenset(";|&()")
REDIRECTS: typ.Final = frozenset("<>")
HEREDOC: typ.Final = re.compile(r"<<-?")

type Argv = list[str]


def _mask_make_references(line: str) -> str:
    """Replace each `$(...)` Make reference or command substitution with `$X`.

    The scanner treats parentheses as operators, which would split
    `$(PROVER_TOOLS)` into pieces; masking the balanced reference keeps it one
    word that `_words` then drops.

    Returns
    -------
    str
        *line* with each balanced `$(...)` replaced by `$X`.
    """
    out: list[str] = []
    depth = 0
    index = 0
    while index < len(line):
        if depth == 0 and line.startswith("$(", index):
            depth = 1
            out.append("$X")
            index += 2
            continue
        char = line[index]
        if depth:
            depth += {"(": 1, ")": -1}.get(char, 0)
        else:
            out.append(char)
        index += 1
    return "".join(out)


def _is_redirect_ampersand(line: str, index: int) -> bool:
    """Return whether the `&` at *index* is part of `>&2` or `&>`."""
    before = line[index - 1] if index else ""
    after = line[index + 1] if index + 1 < len(line) else ""
    return before in REDIRECTS or after == ">"


def _is_comment_start(line: str, index: int) -> bool:
    """Return whether the unquoted `#` at *index* begins a shell comment."""
    return index == 0 or line[index - 1].isspace()


def _split_operators(line: str) -> list[str]:
    """Split *line* at operators outside quotes, ending it at a comment.

    Returns
    -------
    list[str]
        The non-empty segments, each still carrying its own quoting.
    """
    segments: list[str] = []
    current: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(line):
        char = line[index]
        if quote:
            current.append(char)
            if char == "\\" and quote == '"':
                current.append(line[index + 1 : index + 2])
                index += 1
            elif char == quote:
                quote = None
        elif char == "\\":
            current.append(line[index : index + 2])
            index += 1
        elif char in {"'", '"'}:
            quote = char
            current.append(char)
        elif char == "#" and _is_comment_start(line, index):
            break
        elif char in OPERATORS and not (
            char == "&" and _is_redirect_ampersand(line, index)
        ):
            segments.append("".join(current))
            current = []
        else:
            current.append(char)
        index += 1
    segments.append("".join(current))
    return [segment.strip() for segment in segments if segment.strip()]


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
        if match is None:
            continue
        delimiter = word[match.end() :] or (
            argv[position + 1] if position + 1 < len(argv) else ""
        )
        return delimiter.strip("'\"") or None
    return None


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
            if raw.strip() == delimiter:
                delimiter = None
            continue
        line = raw.strip().lstrip(MARKER_PREFIX)
        for segment in _split_operators(_mask_make_references(line)):
            argv = _words_of(segment)
            if argv:
                found.append(argv)
            delimiter = delimiter or _heredoc_delimiter(argv)
    return found


def _unwrap(argv: cabc.Sequence[str]) -> list[str]:
    """Return *argv* without leading wrapper commands and their options."""
    rest = list(argv)
    while rest and rest[0] in WRAPPERS:
        wrapper = rest.pop(0)
        while rest and (rest[0].startswith("-") or "=" in rest[0].split("/")[0]):
            option = rest.pop(0)
            if wrapper == "env" and option in ENV_OPTIONS_WITH_ARGUMENT and rest:
                rest.pop(0)
    return rest


def _words(argv: cabc.Sequence[str]) -> list[str]:
    """Return *argv* without Make variable references and assignments."""
    return [
        word
        for word in argv
        if not word.startswith("$") and "=" not in word.split("/", maxsplit=1)[0]
    ]


def _adjacent(words: list[str], first: cabc.Container[str], second: str) -> bool:
    """Return whether a word in *first* is directly followed by *second*."""
    return any(a in first and b == second for a, b in itertools.pairwise(words))


def _names_prover_tools(argv: cabc.Sequence[str]) -> bool:
    """Return whether *argv* is the estate runner, or wraps it in a Make variable.

    A leading `$(PROVER_TOOLS)` stands for the runner, so a masked reference
    before the command's arguments counts as naming it.

    Returns
    -------
    bool
        Whether a Make reference or a `prover-tools` word is present.
    """
    return any(
        word.startswith("$") or word.rsplit("/", maxsplit=1)[-1] == PROVER_TOOLS
        for word in argv
    )


def is_installer(argv: cabc.Sequence[str]) -> bool:
    """Return whether *argv* installs Kani, Verus or Z3.

    Returns
    -------
    bool
        ``True`` for `<tool> install` under the estate runner (named, or a
        Make variable standing for it), `cargo install|binstall kani-verifier`,
        and `cargo kani setup`. A command that only prints text, or that names
        no runner, is never one.
    """
    unwrapped = _unwrap(argv)
    words = _words(unwrapped)
    if not words or words[0] in PRINTING_COMMANDS:
        return False
    if _adjacent(words, TOOLS, "install") and _names_prover_tools(unwrapped):
        return True
    if words[:2] in (["cargo", "install"], ["cargo", "binstall"]):
        return "kani-verifier" in words
    return words[:3] == ["cargo", "kani", "setup"]


def is_runner(argv: cabc.Sequence[str]) -> bool:
    """Return whether *argv* runs a prover.

    Returns
    -------
    bool
        ``True`` for the Verus or Kani binaries, `cargo kani`, and
        `prover-tools <tool> run|exec|check-version`.
    """
    unwrapped = _unwrap(argv)
    words = _words(unwrapped)
    if not words or words[0] in PRINTING_COMMANDS:
        return False
    # A leading Make reference (`$(PROVER_TOOLS) verus install`) is a wrapper,
    # so the word after it is an argument, not the Verus binary.
    is_wrapped = bool(unwrapped) and unwrapped[0].startswith("$")
    if (words[0] in RUNNER_BINARIES and not is_wrapped) or words[:2] == [
        "cargo",
        "kani",
    ]:
        return True
    return _names_prover_tools(unwrapped) and any(
        _adjacent(words, TOOLS, verb) for verb in RUN_VERBS
    )
