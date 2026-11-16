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

The reader guards against accident, not against a script written to defeat it:
it fails closed on every shape it cannot place, but shell has more shapes than
a regular expression can enumerate. The cost of a miss is a skipped warning
about a per-run target on a job that someone has deliberately made unusual.

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
# The substitution may be followed only by plain file names (none starts with a
# dash, so no `-e` or `-f` option), and then the end of the command.
SED_SUBSTITUTE: typ.Final = re.compile(
    r"""[ \t]+-n[ \t]+'s/[^/']*/[^/']*/[pgI]*'"""
    r"""(?:[ \t]+[\w./~$][^\s;&|()'"`]*)*[ \t]*(?=$|[;&|)])""",
    re.MULTILINE,
)
CARGO_WORD: typ.Final = re.compile(r"(?<![\w.-])(?:[\w.~$/{}-]*/)?cargo(?![\w-])")
# `cargo`, then an optional toolchain selector (quoted or not), then the word
# that must be a check-style subcommand.
CARGO_SUBCOMMAND: typ.Final = re.compile(
    r"""[ \t]+(?:["']?\+[^\s"']+["']?[ \t]+)?(?P<subcommand>[a-z][\w-]*)"""
)
# A command position: the start of a line, or after a separator, a command
# substitution, a subshell or a compound-command keyword. A `NAME=value` word
# there is followed through to the command it precedes (`_resolve_command`).
COMMAND_START: typ.Final = re.compile(
    r"""(?:^|;|&|\|\||\||\(|\)["']?|`)[ \t]*"""
    rf"(?:{CONTROL_WORDS})*"
    r"(?P<word>[^\s;&|()`]+)",
    re.MULTILINE,
)
WORD: typ.Final = re.compile(r"[ \t]*(?P<word>[^\s;&|()`]+)")
ASSIGNMENT: typ.Final = re.compile(r"[A-Za-z_]\w*=")
CLOSERS: typ.Final = {"(": ")", "{": "}"}
# What is left of a `case` arm (`*)`) once the parenthesis is split off, or the
# descriptor of a redirection such as `>&2`.
PATTERN_ONLY: typ.Final = re.compile(r"[*?]+|\d+")


def _cargo_is_check_style(script: str) -> bool:
    """Report whether every `cargo` word of *script* is a check-style command."""
    for found in CARGO_WORD.finditer(script):
        sub = CARGO_SUBCOMMAND.match(script, found.end())
        if sub is None or sub["subcommand"] not in CHECK_SUBCOMMANDS:
            return False
    return True


def _skip_quoted(script: str, index: int) -> int:
    """Return the index just past the quoted string that starts at *index*."""
    quote = script[index]
    index += 1
    while index < len(script) and script[index] != quote:
        if quote == '"' and script[index : index + 2] in {"$(", "${"}:
            # An expansion inside double quotes nests its own quotes.
            index = _skip_expansion(script, index)
        else:
            index += 2 if script[index] == "\\" and quote == '"' else 1
    return index + 1


def _skip_expansion(script: str, index: int) -> int:
    """Return the index just past the `$(..)` or `${..}` that starts at *index*."""
    opener = script[index + 1]
    closer = CLOSERS[opener]
    depth = 0
    while index < len(script):
        if script[index] in "\"'":
            index = _skip_quoted(script, index)
            continue
        depth += (script[index] == opener) - (script[index] == closer)
        index += 1
        if depth == 0 and script[index - 1] == closer:
            break
    return index


def _value_end(script: str, index: int) -> int:
    """Return the index where an assignment value that starts at *index* ends.

    The value runs to the next unquoted separator, skipping quoted strings,
    command substitutions, parameter expansions and backquotes whole.

    Returns
    -------
    int
        The index of the first character after the value.
    """
    while index < len(script) and script[index] not in " \t\n;&|)":
        char = script[index]
        if char in "\"'":
            index = _skip_quoted(script, index)
        elif char == "$" and script[index + 1 : index + 2] in CLOSERS:
            index = _skip_expansion(script, index)
        elif char == "`":
            index = script.find("`", index + 1) + 1 or len(script)
        else:
            index += 1
    return index


def _resolve_command(script: str, found: re.Match[str]) -> re.Match[str] | None:
    """Return the command word a command position reaches past its assignments.

    `RUST_BACKTRACE="$TRACE" make test` is the command `make`, so each leading
    `NAME=value` word is skipped by reading its whole value.

    Returns
    -------
    re.Match[str] | None
        The match of the first word that is not an assignment, or None when the
        position holds only assignments.
    """
    word: re.Match[str] | None = found
    while word is not None and ASSIGNMENT.match(word["word"]):
        value = word.start("word") + word["word"].index("=") + 1
        word = WORD.match(script, _value_end(script, value))
    return word


def _mask_range(script: str, masked: list[str], low: int, high: int) -> None:
    """Blank the text of quoted strings in `script[low:high]`, in place.

    Offsets are kept, so a match on the masked copy lines up with the original.
    Expansions nested inside double quotes stay visible, because they run.
    """
    index = low
    while index < high:
        char = script[index]
        if char not in "\"'":
            index += 1
            continue
        end = _skip_quoted(script, index)
        inner = index + 1
        while inner < min(end - 1, high):
            if char == '"' and script[inner : inner + 2] in {"$(", "${"}:
                stop = _skip_expansion(script, inner)
                _mask_range(script, masked, inner + 2, stop)
                inner = stop
            else:
                masked[inner] = "_" if script[inner] != "\n" else "\n"
                inner += 1
        index = end


def _mask_quotes(script: str) -> str:
    """Return *script* with the text of its quoted strings blanked out.

    A quoted string is data (a sed script, a message), so its brackets and
    separators must not be read as commands.

    Returns
    -------
    str
        A copy of the same length, with quoted text replaced by underscores.
    """
    masked = list(script)
    _mask_range(script, masked, 0, len(script))
    return "".join(masked)


def _commands_are_harmless(script: str) -> bool:
    """Report whether every command of *script* is cargo, an assignment or harmless."""
    visible = _mask_quotes(script)
    for position in COMMAND_START.finditer(visible):
        found = _resolve_command(visible, position)
        if found is None:
            continue
        word = found["word"].strip("\"'")
        guard = {"rustup": RUSTUP_SETUP, "sed": SED_SUBSTITUTE}.get(word)
        if guard is not None:
            if guard.match(script, found.end("word")):
                continue
            return False
        if (
            not word
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
