"""Classify the commands that install or run a prover (`rust-prover-tooling`).

The rule has to tell a command that installs Kani, Verus or Z3 from one that
only runs proofs on a tool that is already there. A step that runs
`make verus` on an installed verifier is compliant; one whose Make target
depends on `verus-install` is not. A text search cannot make that distinction
(`printf 'prover-tools verus install'` mentions the installer without running
it), so the classification reads parsed command lines, split by
`shell_commands`, and inspects each command's argument vector.

The installer forms recognised are the estate's own runner
(`prover-tools <tool> install`, whatever wraps it), `cargo install` or
`cargo binstall` of `kani-verifier`, and `cargo kani setup`. A runner is
`verus`, `cargo kani`, or `prover-tools <tool> run|exec|check-version`. The
estate runner counts only when it is named (`prover-tools`) or a Make variable
stands for it (`$(PROVER_TOOLS)`), so `git commit -m 'verus install'` is not an
installer. Leading `env`, `command`, `exec` and similar wrappers are looked
through.

Make variable references are not expanded: they are masked and skipped, so a
recipe that hides the tool noun inside a variable is not seen. That is a
documented limit of the rule, not a verdict.
"""

from __future__ import annotations

import itertools
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc

__all__ = ["is_installer", "is_runner"]

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
CARGO_INSTALLS: typ.Final = (["cargo", "install"], ["cargo", "binstall"])


def _is_assignment(word: str) -> bool:
    """Return whether *word* is a `NAME=value` environment assignment."""
    return "=" in word.split("/", maxsplit=1)[0]


def _is_option_or_assignment(word: str) -> bool:
    """Return whether *word* is an option or an environment assignment."""
    return word.startswith("-") or _is_assignment(word)


def _takes_argument(wrapper: str, option: str, rest: list[str]) -> bool:
    """Return whether *option* of *wrapper* consumes the next word."""
    if wrapper != "env":
        return False
    return option in ENV_OPTIONS_WITH_ARGUMENT and bool(rest)


def _drop_options(rest: list[str], wrapper: str) -> None:
    """Remove a wrapper's options, assignments and option arguments from *rest*."""
    while rest and _is_option_or_assignment(rest[0]):
        option = rest.pop(0)
        if _takes_argument(wrapper, option, rest):
            rest.pop(0)


def _unwrap(argv: cabc.Sequence[str]) -> list[str]:
    """Return *argv* without leading wrapper commands and their options."""
    rest = list(argv)
    while rest and rest[0] in WRAPPERS:
        wrapper = rest.pop(0)
        _drop_options(rest, wrapper)
    return rest


def _words(argv: cabc.Sequence[str]) -> list[str]:
    """Return *argv* without Make variable references and assignments."""
    return [
        word for word in argv if not word.startswith("$") and not _is_assignment(word)
    ]


def _adjacent(words: list[str], first: cabc.Container[str], second: str) -> bool:
    """Return whether a word in *first* is directly followed by *second*."""
    return any(a in first and b == second for a, b in itertools.pairwise(words))


def _names_prover_tools(argv: cabc.Sequence[str]) -> bool:
    """Return whether *argv* is the estate runner, or wraps it in a Make variable.

    A masked reference among the words stands for the runner, as does a
    `prover-tools` word.

    Returns
    -------
    bool
        Whether a Make reference or a `prover-tools` word is present.
    """
    return any(
        word.startswith("$") or word.rsplit("/", maxsplit=1)[-1] == PROVER_TOOLS
        for word in argv
    )


def _prints(words: list[str]) -> bool:
    """Return whether the command is empty or only prints its arguments."""
    return not words or words[0] in PRINTING_COMMANDS


def _runner_installs(words: list[str], unwrapped: list[str]) -> bool:
    """Return whether the estate runner is asked to install a tool."""
    return _adjacent(words, TOOLS, "install") and _names_prover_tools(unwrapped)


def _cargo_installs(words: list[str]) -> bool:
    """Return whether Cargo installs or sets up Kani."""
    if words[:2] in CARGO_INSTALLS:
        return "kani-verifier" in words
    return words[:3] == ["cargo", "kani", "setup"]


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
    if _prints(words):
        return False
    return _runner_installs(words, unwrapped) or _cargo_installs(words)


def _is_binary(unwrapped: list[str], words: list[str]) -> bool:
    """Return whether the command is a prover binary, not an argument to one.

    A leading Make reference (`$(PROVER_TOOLS) verus install`) is a wrapper, so
    the word after it is an argument, not the Verus binary.

    Returns
    -------
    bool
        Whether the first word is a prover binary.
    """
    if unwrapped[0].startswith("$"):
        return False
    return words[0] in RUNNER_BINARIES


def _runs_through_runner(unwrapped: list[str], words: list[str]) -> bool:
    """Return whether the estate runner is asked to run, exec or check a tool."""
    runs = any(_adjacent(words, TOOLS, verb) for verb in RUN_VERBS)
    return runs and _names_prover_tools(unwrapped)


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
    if _prints(words):
        return False
    return (
        _is_binary(unwrapped, words)
        or words[:2] == ["cargo", "kani"]
        or _runs_through_runner(unwrapped, words)
    )
