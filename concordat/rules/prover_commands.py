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
import shlex
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc

TOOLS: typ.Final = frozenset({"kani", "verus", "z3"})
RUN_VERBS: typ.Final = frozenset({"run", "exec", "check-version"})
# Commands whose arguments are text, so a prover command named there is
# mentioned rather than run.
PRINTING_COMMANDS: typ.Final = frozenset({"printf", "echo", "cat", "grep", "test", "["})
SEPARATORS: typ.Final = frozenset({"&&", "||", ";", "|", "&", "(", ")"})
RUNNER_BINARIES: typ.Final = frozenset({"verus", "cargo-kani", "kani-verifier"})
PROVER_TOOLS: typ.Final = "prover-tools"
MARKER_PREFIX: typ.Final = "@-+"

type Argv = list[str]


def _mask_make_references(line: str) -> str:
    """Replace each `$(...)` Make reference or command substitution with `$X`.

    The shell lexer treats parentheses as separators, which would split
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


def _tokens(line: str) -> list[str]:
    """Return one shell line's tokens, or none when it does not tokenize."""
    lexer = shlex.shlex(_mask_make_references(line), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = "#"
    try:
        return list(lexer)
    except ValueError:
        return []


def commands(text: str) -> list[Argv]:
    """Split shell or recipe text into one argument vector per simple command.

    Parameters
    ----------
    text:
        A `run` script or a Makefile recipe. Backslash continuations are
        joined, a leading Make recipe marker (`@`, `-`, `+`) is dropped, and a
        line that does not tokenize is skipped.

    Returns
    -------
    list[Argv]
        The commands, split on `&&`, `||`, `;`, `|` and `&`.
    """
    found: list[Argv] = []
    for raw in text.replace("\\\n", " ").splitlines():
        line = raw.strip().lstrip(MARKER_PREFIX)
        if not line or line.startswith("#"):
            continue
        current: Argv = []
        for token in _tokens(line):
            if token in SEPARATORS:
                if current:
                    found.append(current)
                current = []
            else:
                current.append(token)
        if current:
            found.append(current)
    return found


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


def is_installer(argv: cabc.Sequence[str]) -> bool:
    """Return whether *argv* installs Kani, Verus or Z3.

    Returns
    -------
    bool
        ``True`` for `<tool> install` under the estate runner or any wrapper,
        `cargo install|binstall kani-verifier`, and `cargo kani setup`.
        Commands that only print text never are.
    """
    words = _words(argv)
    if not words or words[0] in PRINTING_COMMANDS:
        return False
    if _adjacent(words, TOOLS, "install"):
        return True
    if words[:2] in (["cargo", "install"], ["cargo", "binstall"]):
        return "kani-verifier" in words
    return words[:3] == ["cargo", "kani", "setup"]


def is_runner(argv: cabc.Sequence[str]) -> bool:
    """Return whether *argv* runs a prover (or installs one, which is use too).

    Returns
    -------
    bool
        ``True`` for the Verus or Kani binaries, `cargo kani`, and
        `prover-tools <tool> run|exec|check-version`.
    """
    words = _words(argv)
    if not words or words[0] in PRINTING_COMMANDS:
        return False
    # A leading Make reference (`$(PROVER_TOOLS) verus install`) is a wrapper,
    # so the word after it is an argument, not the Verus binary.
    is_wrapped = argv[0].startswith("$")
    if (words[0] in RUNNER_BINARIES and not is_wrapped) or words[:2] == [
        "cargo",
        "kani",
    ]:
        return True
    names_runner = is_wrapped or any(
        word.rsplit("/", maxsplit=1)[-1] == PROVER_TOOLS for word in words
    )
    return names_runner and any(_adjacent(words, TOOLS, verb) for verb in RUN_VERBS)
