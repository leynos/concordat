"""Decide whether a CI job can start a nested cargo build (NC-006).

A per-run `CARGO_TARGET_DIR` hurts a job only when something in it starts a
second build that could have inherited a warm tree. A job that runs cargo, only
`check`-style cargo, and nothing else that could run tests, has none, so the
rule leaves its target alone. The reader is deliberately narrow and errs
towards "can start a nested build":

- every `cargo` word in a `run` script, whatever its path or global options,
  must be followed (after an optional `+toolchain`) by a check-style
  subcommand; an option, a path or an unknown word before the subcommand makes
  the job unclassifiable;
- every command in a `run` script must be a cargo command, a variable
  assignment or one of a small set of harmless shell words; any other program,
  script or interpreter hands work off to something the reader cannot see;
- every `uses` step must be one of a few named set-up actions, matched by full
  identity, never by basename and never a local action.

A check-style cargo command may run a dependency's build script. Build scripts
are production code and not a test, so this reader does not count them; that is
a stated limit of the exemption, not a proof that no process starts.
"""

from __future__ import annotations

import re
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc

CHECK_SUBCOMMANDS: typ.Final = frozenset({"check", "clippy", "fmt", "metadata", "tree"})
# Full action identities, without the `@ref`. A local action (`./...`) never
# matches, and neither does an action of the same name from another owner.
SETUP_ACTIONS: typ.Final = frozenset({
    "actions/checkout",
    "actions/cache",
    "actions/upload-artifact",
    "actions/download-artifact",
    "dtolnay/rust-toolchain",
    "taiki-e/install-action",
    "leynos/shared-actions/.github/actions/setup-rust",
})
# Words a check job's scripts may run besides cargo itself: shell plumbing and
# text tools that start no build.
HARMLESS_WORDS: typ.Final = frozenset({
    "echo", "printf", "test", "[", "[[", "head", "tail", "cut", "tr", "grep",
    "cat", "export", "set", "cd", "true", "false", "exit", "pwd", "dirname",
    "basename", "case", "esac", "fi", "done", "for", "read", "mkdir", ":",
})  # fmt: skip
# Shell control words that are followed by a command, which is read in turn.
CONTROL_WORDS: typ.Final = r"(?:(?:then|do|else|elif|if|while|until)\b|!)[ \t]*"
# `rustup` sets a toolchain up; `rustup run <toolchain> <command>` runs anything.
RUSTUP_SETUP: typ.Final = re.compile(
    r"[ \t]+(?:toolchain|target|component|show|default|update|set)\b"
)
# GNU `sed` can run a command (`1e cmd`, `s/a/b/e`), so only one shape is read
# as text processing: `sed -n 's/PATTERN/REPLACEMENT/FLAGS'` with no further
# slash or quote inside it and flags limited to `p`, `g` and `I`.
SED_SUBSTITUTE: typ.Final = re.compile(r"""[ \t]+-n[ \t]+'s/[^/']*/[^/']*/[pgI]*'""")
CARGO_WORD: typ.Final = re.compile(r"(?<![\w.-])(?:[\w.~$/{}-]*/)?cargo(?![\w-])")
# `cargo`, then an optional toolchain selector (quoted or not), then the word
# that must be a check-style subcommand.
CARGO_SUBCOMMAND: typ.Final = re.compile(
    r"""[ \t]+(?:["']?\+[^\s"']+["']?[ \t]+)?(?P<subcommand>[a-z][\w-]*)"""
)
# A command position: the start of a line, or after a separator, a command
# substitution, a subshell or a compound-command keyword. Plain `NAME=value`
# prefixes are skipped so the command they precede is still read.
COMMAND_START: typ.Final = re.compile(
    r"(?:^|;|&&|\|\||\||\(|\)|`)[ \t]*"
    rf"(?:{CONTROL_WORDS})*"
    r"""(?:[A-Za-z_]\w*=[^\s"'$();&|`]*[ \t]+)*"""
    r"(?P<word>[^\s;&|()`]+)",
    re.MULTILINE,
)
ASSIGNMENT: typ.Final = re.compile(r"[A-Za-z_]\w*=")
# What is left of a `case` arm (`*)`) once the parenthesis is split off.
PATTERN_ONLY: typ.Final = re.compile(r"[*?]+")


def _cargo_is_check_style(script: str) -> bool:
    """Report whether every `cargo` word of *script* is a check-style command."""
    for found in CARGO_WORD.finditer(script):
        sub = CARGO_SUBCOMMAND.match(script, found.end())
        if sub is None or sub["subcommand"] not in CHECK_SUBCOMMANDS:
            return False
    return True


def _commands_are_harmless(script: str) -> bool:
    """Report whether every command of *script* is cargo, an assignment or harmless."""
    for found in COMMAND_START.finditer(script):
        word = found["word"].strip("\"'")
        guard = {"rustup": RUSTUP_SETUP, "sed": SED_SUBSTITUTE}.get(word)
        if guard is not None:
            if guard.match(script, found.end("word")):
                continue
            return False
        if (
            not word
            or ASSIGNMENT.match(word)
            or word in HARMLESS_WORDS
            or PATTERN_ONLY.fullmatch(word)
            or CARGO_WORD.fullmatch(word)
        ):
            continue
        return False
    return True


def _uses_only_setup_actions(uses: cabc.Iterable[object]) -> bool:
    """Report whether every `uses` value names a known set-up action."""
    return all(str(value).split("@", maxsplit=1)[0] in SETUP_ACTIONS for value in uses)


def is_check_only(scripts: list[str], uses: cabc.Iterable[object]) -> bool:
    """Report whether a job with these scripts and actions starts no nested build.

    Parameters
    ----------
    scripts:
        The job's `run` scripts, comment lines removed.
    uses:
        The `uses` value of every step that has one.

    Returns
    -------
    bool
        True when the job runs cargo, only as check-style commands, hands off to
        nothing the reader cannot place and uses only known set-up actions.
    """
    return (
        any(CARGO_WORD.search(script) for script in scripts)
        and all(_cargo_is_check_style(script) for script in scripts)
        and all(_commands_are_harmless(script) for script in scripts)
        and _uses_only_setup_actions(uses)
    )
