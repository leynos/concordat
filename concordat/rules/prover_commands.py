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

__all__ = [
    "is_installer",
    "is_runner",
    "is_substantive",
    "runs_proofs",
    "script_invoked",
    "unwrap",
    "wrapper_changes_directory",
]

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
# Options of each wrapper that consume the next word.
WRAPPER_ARGUMENT_OPTIONS: typ.Final = {
    "env": frozenset({"-u", "-C", "-S", "--unset", "--chdir", "--split-string"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "sudo": frozenset({"-u", "-g", "-C", "-h", "-p", "-U", "-D", "-R", "-r", "-t"}),
    "time": frozenset({"-f", "-o", "--format", "--output"}),
}
RUNNER_BINARIES: typ.Final = frozenset({"verus", "cargo-kani", "kani-verifier"})
PROVER_TOOLS: typ.Final = "prover-tools"
# Launchers that run the named tool as an argument (`uvx ... prover-tools`).
LAUNCHERS: typ.Final = frozenset({"uvx", "uv", "pipx"})
DIRECTORY_OPTIONS: typ.Final = frozenset({"-C", "--chdir"})
INTERPRETERS: typ.Final = frozenset({
    "sh",
    "bash",
    "dash",
    "zsh",
    "python",
    "python3",
    "source",
    ".",
})
CARGO_INSTALLS: typ.Final = (["cargo", "install"], ["cargo", "binstall"])
# Commands that arrange the shell rather than doing work.
NO_WORK_COMMANDS: typ.Final = frozenset({"cd", "export", "set", "true", ":"})
KANI_INFO_COMMANDS: typ.Final = frozenset({"setup", "list"})
KANI_INFO_FLAGS: typ.Final = frozenset({"--version", "-V", "--help", "-h"})
CARGO_VERUS_RUNS: typ.Final = frozenset({"verify", "focus", "build"})
# `<tool> <verb>` pairs under the estate runner that verify proofs.
PROOF_VERBS: typ.Final = (("kani", "exec"), ("verus", "run"))


def _is_assignment(word: str) -> bool:
    """Return whether *word* is a `NAME=value` environment assignment."""
    name, separator, _ = word.partition("=")
    return bool(separator) and name.isidentifier()


def _is_option_or_assignment(word: str) -> bool:
    """Return whether *word* is an option or an environment assignment."""
    return word.startswith("-") or _is_assignment(word)


def _takes_argument(wrapper: str, option: str, rest: list[str]) -> bool:
    """Return whether *option* of *wrapper* consumes the next word."""
    options = WRAPPER_ARGUMENT_OPTIONS.get(wrapper, frozenset())
    return option in options and bool(rest)


def _is_directory_option(wrapper: str, option: str) -> bool:
    """Return whether *option* of *wrapper* changes the command's directory."""
    if wrapper != "env":
        return False
    return option in DIRECTORY_OPTIONS or option.startswith("--chdir=")


def _drop_options(rest: list[str], wrapper: str) -> bool:
    """Remove a wrapper's options, assignments and option arguments from *rest*.

    Returns
    -------
    bool
        Whether an option moved the wrapped command to another directory.
    """
    moved = False
    while rest and _is_option_or_assignment(rest[0]):
        option = rest.pop(0)
        moved = moved or _is_directory_option(wrapper, option)
        if _takes_argument(wrapper, option, rest):
            rest.pop(0)
    return moved


def _unwrap(argv: cabc.Sequence[str]) -> tuple[list[str], bool]:
    """Return *argv* without leading assignments and wrappers, and whether it moved."""
    rest = list(argv)
    while rest and _is_assignment(rest[0]):
        rest.pop(0)
    moved = False
    while rest and rest[0] in WRAPPERS:
        wrapper = rest.pop(0)
        moved = _drop_options(rest, wrapper) or moved
    return rest, moved


def unwrap(argv: cabc.Sequence[str]) -> list[str]:
    """Return *argv* without leading assignments, wrapper commands and options."""
    return _unwrap(argv)[0]


def wrapper_changes_directory(argv: cabc.Sequence[str]) -> bool:
    """Return whether a wrapper (`env -C dir`) runs the command in another directory."""
    return _unwrap(argv)[1]


def _words(argv: cabc.Sequence[str]) -> list[str]:
    """Return *argv* without Make variable references and assignments."""
    return [
        word for word in argv if not word.startswith("$") and not _is_assignment(word)
    ]


def _adjacent(words: list[str], first: cabc.Container[str], second: str) -> bool:
    """Return whether a word in *first* is directly followed by *second*."""
    return any(a in first and b == second for a, b in itertools.pairwise(words))


def _basename(word: str) -> str:
    """Return the last path component of *word*."""
    return word.rsplit("/", maxsplit=1)[-1]


def _names_prover_tools(unwrapped: cabc.Sequence[str], words: list[str]) -> bool:
    """Return whether the command is the estate runner, or a variable standing for it.

    The runner has to be the executable: a leading Make reference stands for it,
    `prover-tools` is the command word, or a launcher such as `uvx` runs it. A
    `prover-tools` argument to another command (`git diff -- prover-tools`) is
    not the runner.

    Returns
    -------
    bool
        Whether the command is the runner.
    """
    if unwrapped[0].startswith("$"):
        return True
    head = _basename(words[0])
    if head == PROVER_TOOLS:
        return True
    return head in LAUNCHERS and any(_basename(w) == PROVER_TOOLS for w in words)


def _prints(words: list[str]) -> bool:
    """Return whether the command is empty or only prints its arguments."""
    return not words or words[0] in PRINTING_COMMANDS


def _runner_installs(words: list[str], unwrapped: list[str]) -> bool:
    """Return whether the estate runner is asked to install a tool."""
    return _adjacent(words, TOOLS, "install") and _names_prover_tools(unwrapped, words)


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
    unwrapped = unwrap(argv)
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
    return runs and _names_prover_tools(unwrapped, words)


def is_runner(argv: cabc.Sequence[str]) -> bool:
    """Return whether *argv* runs a prover.

    Returns
    -------
    bool
        ``True`` for the Verus or Kani binaries, `cargo kani`, and
        `prover-tools <tool> run|exec|check-version`.
    """
    unwrapped = unwrap(argv)
    words = _words(unwrapped)
    if _prints(words):
        return False
    return (
        _is_binary(unwrapped, words)
        or words[:2] == ["cargo", "kani"]
        or _runs_through_runner(unwrapped, words)
    )


def _kani_proves(arguments: list[str]) -> bool:
    """Return whether Kani's arguments, after the program, verify something.

    `setup`, `list` and the version and help flags install, enumerate or
    report; everything else (`--harness x`, `--workspace`, no argument at all)
    verifies.

    Returns
    -------
    bool
        Whether the arguments ask Kani to verify.
    """
    operands = [a for a in arguments if not a.startswith("-")]
    if any(a in KANI_INFO_FLAGS for a in arguments):
        return False
    return not (operands and operands[0] in KANI_INFO_COMMANDS)


def _is_kani_proof_run(words: list[str]) -> bool:
    """Return whether the command is `cargo kani`, `cargo-kani` or `kani-driver`."""
    head = _basename(words[0])
    if words[:2] == ["cargo", "kani"]:
        return _kani_proves(words[2:])
    if head in {"cargo-kani", "kani-driver"}:
        rest = words[1:]
        return _kani_proves(rest[1:] if rest[:1] == ["kani"] else rest)
    return False


def _is_verus_proof_run(words: list[str]) -> bool:
    """Return whether the command is Verus on a proof file, or `cargo verus`."""
    head = _basename(words[0])
    if head == "verus":
        return any(word.endswith(".rs") for word in words[1:])
    if words[:2] == ["cargo", "verus"] or head == "cargo-verus":
        rest = words[2:] if words[:2] == ["cargo", "verus"] else words[1:]
        # Cargo runs the plugin as `cargo-verus verus <subcommand>`.
        rest = rest[1:] if rest[:1] == ["verus"] else rest
        return bool(rest) and rest[0] in CARGO_VERUS_RUNS
    return False


def _runs_binary(unwrapped: list[str], words: list[str]) -> bool:
    """Return whether a prover binary is the command and verifies proofs.

    A leading Make reference is the estate runner (or an unresolved variable),
    never a binary named in the recipe, so it is not read here.

    Returns
    -------
    bool
        Whether the command is a prover binary run on proofs.
    """
    if unwrapped[0].startswith("$"):
        return False
    return _is_kani_proof_run(words) or _is_verus_proof_run(words)


def _runs_through_estate_runner(unwrapped: list[str], words: list[str]) -> bool:
    """Return whether `prover-tools kani exec` or `verus run` is asked to verify."""
    through = any(_adjacent(words, {tool}, verb) for tool, verb in PROOF_VERBS)
    return through and _names_prover_tools(unwrapped, words)


def runs_proofs(argv: cabc.Sequence[str]) -> bool:
    """Return whether *argv* runs proofs, not merely installs or checks a prover.

    `is_runner` also counts `check-version`, because a step that checks the
    prover is evidence the repository uses one; PT-006 asks for more, that the
    proofs themselves are verified.

    Returns
    -------
    bool
        ``True`` for `cargo kani`, `cargo-kani` or `kani-driver` except their
        `setup`, `list`, version and help forms; `prover-tools kani exec` and
        `prover-tools verus run`; `verus` on a Rust proof file; and
        `cargo verus verify|focus|build`. A command that only prints text, or
        whose program is an unresolved Make reference, is never one.
    """
    unwrapped = unwrap(argv)
    words = _words(unwrapped)
    if _prints(words):
        return False
    return _runs_binary(unwrapped, words) or _runs_through_estate_runner(
        unwrapped, words
    )


def _checks_version(words: list[str]) -> bool:
    """Return whether the command asks a tool for its version."""
    return any(word in {"check-version", "--version"} for word in words)


def is_substantive(argv: cabc.Sequence[str]) -> bool:
    """Return whether a recipe command does work beyond printing and installing.

    A `make kani*` or `make verus*` target counts as running proofs in a
    workflow that installs through the shared action when its recipe holds at
    least one such command: a generator's harnesses are verified inside a test
    suite, so no recipe names the prover binary. A recipe of `printf` and
    installs, or one that only checks the version, is not substantive.

    Returns
    -------
    bool
        Whether the command runs something other than a print, an installer or
        a version check.
    """
    words = _words(unwrap(argv))
    if _prints(words) or words[0] in NO_WORK_COMMANDS:
        return False
    return not (is_installer(argv) or _checks_version(words))


def _first_operand(words: cabc.Sequence[str]) -> str | None:
    """Return the first word that is not an option."""
    return next((w for w in words if not w.startswith("-")), None)


def script_invoked(argv: cabc.Sequence[str]) -> str | None:
    """Return the script a command runs, as a path without a leading `./`.

    A script is run when it is the command (`./scripts/get.sh`), or the first
    operand of an interpreter (`bash -x scripts/get.sh`) or of a Make variable
    standing for one (`$(PYTHON) scripts/get.py`). A script merely named as an
    argument to another command (`echo scripts/get.sh`) is not run.

    Returns
    -------
    str | None
        The script path, or None when the command runs none.
    """
    unwrapped = unwrap(argv)
    if not unwrapped:
        return None
    head = unwrapped[0]
    is_interpreter = head.startswith("$") or _basename(head) in INTERPRETERS
    operand = _first_operand(unwrapped[1:]) if is_interpreter else head
    return None if operand is None else operand.removeprefix("./")
