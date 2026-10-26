"""Read a script into classified commands, keeping its connectors.

Order matters more than contents. A `cargo fetch --locked` protects the tests
only when it runs before them *and* its failure stops them; a `;`, an `||`, a
pipeline, a background command, a subshell or an ignored error all leave the
next command running whatever happened. Only `&&` and fail-fast line
sequencing prove order, so the splitter keeps the connectors that
`lex_segments` drops.

Each command is then classified into the one thing ordering depends on:
whether it fetches with `--locked`, whether it runs the tests (directly or
through `make test`), whether it delegates to another make target, and whether
its text could be read at all.
"""

from __future__ import annotations

import dataclasses
import re
import typing as typ

from .interrogate_invocation import lex_segments
from .make_commands import blank_references, command_start
from .makefile_expansion import UNRESOLVED_MARK

# The make goal that runs a crate's tests. A command naming it as a goal, and
# not changing directory first, is a test entry point rather than recursion.
TEST_TARGET: typ.Final = "test"

# Shell connectors. The two-character forms are matched first so `&&` is not
# read as two background commands. A newline is a connector too: GitHub's
# default `run` shell and Make's recipe shells both stop a script at its first
# failing command, so a later line is reached only when the earlier succeeded.
_TWO_CHARACTER_CONNECTORS: typ.Final = frozenset({"&&", "||", "|&"})
_ONE_CHARACTER_CONNECTORS: typ.Final = frozenset({";", "|", "&"})
_GATING_CONNECTORS: typ.Final = frozenset({"&&", "\n"})

# Shell keywords that introduce a command without being one, so a recipe line
# such as `if [ x ]; then $(MAKE) fetch; fi` still names `make`.
_SHELL_KEYWORDS: typ.Final = frozenset({
    "if",
    "then",
    "else",
    "elif",
    "while",
    "until",
    "do",
    "done",
    "fi",
    "esac",
    "case",
    "in",
    "!",
})

_PROFILE_OPTIONS: typ.Final = frozenset({"--profile", "-P"})
_RECURSIVE_MAKE: typ.Final = re.compile(r"\$[({]MAKE[)}]")
# A backslash-newline is removed by the shell before it reads the command. The
# Makefile loader keeps it, so every command after a `;`-continued line begins
# with one, and a leading assignment or tool would otherwise be misread.
_CONTINUATION: typ.Final = re.compile(r"\\\n")


@dataclasses.dataclass(frozen=True, slots=True)
class ShellCommand:
    """One simple command of a script, and whether its success gates the next.

    ``gates_next`` is true only for a command whose non-zero exit prevents the
    following command from running at all. A command followed by `;` may fail
    and still let the next one start, so it gates nothing.
    """

    text: str
    gates_next: bool


def split_commands(script: str) -> list[ShellCommand]:
    """Split a shell script into commands while keeping the connectors.

    `lex_segments` drops connectors, so it cannot say whether one command's
    success gated the next. This splitter keeps them: each returned command
    records whether its success was required for the following command to run.

    Quotes, backtick substitution, `$(...)`, `${...}` and `(...)` groups are
    scanned as units, so a connector inside them is text rather than a
    separator. A backslash-newline continues the current command rather than
    ending it.

    Returns
    -------
    list[ShellCommand]
        The commands in script order, with blank stretches dropped.
    """
    commands: list[ShellCommand] = []
    start = 0
    index = 0
    depth = 0
    quote: str | None = None
    while index < len(script):
        character = script[index]
        if quote is not None:
            if character == "\\" and quote == '"':
                index += 2
                continue
            quote = None if character == quote else quote
            index += 1
            continue
        if character == "\\":
            index += 2
            continue
        if character in {'"', "'"}:
            quote = character
            index += 1
            continue
        if character == "`":
            closing = script.find("`", index + 1)
            index = len(script) if closing < 0 else closing + 1
            continue
        if character == "$" and script[index + 1 : index + 2] == "(":
            depth += 1
            index += 2
            continue
        if character in "({":
            depth += 1
            index += 1
            continue
        if character in ")}":
            depth = max(0, depth - 1)
            index += 1
            continue
        if depth > 0:
            index += 1
            continue
        found = _connector(script, index)
        if found is None:
            index += 1
            continue
        connector, end = found
        _append_command(
            commands, script[start:index], gates_next=connector in _GATING_CONNECTORS
        )
        start = end
        index = end
    _append_command(commands, script[start:], gates_next=False)
    return commands


def _connector(script: str, index: int) -> tuple[str, int] | None:
    """Return the connector starting at *index*, and the offset after it."""
    two = script[index : index + 2]
    if two in _TWO_CHARACTER_CONNECTORS:
        return two, index + 2
    character = script[index]
    if character in _ONE_CHARACTER_CONNECTORS or character == "\n":
        return character, index + 1
    return None


def _append_command(
    commands: list[ShellCommand], text: str, *, gates_next: bool
) -> None:
    """Append one command unless it is blank.

    A command in a subshell or brace group gates nothing on its own: the
    group's own status is what a following command depends on, and the group
    may run its parts in any order.
    """
    stripped = text.strip()
    if not stripped:
        return
    is_group = stripped.startswith(("(", "{"))
    commands.append(ShellCommand(stripped, gates_next=gates_next and not is_group))


@dataclasses.dataclass(frozen=True, slots=True)
class CommandMeaning:
    """What one simple command is, as far as ordering is concerned."""

    is_prefetch: bool = False
    test: str | None = None
    profile: str | None = None
    is_recursive_make: bool = False
    is_unreadable: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class ScriptCommand:
    """One split command together with its meaning."""

    text: str
    gates_next: bool
    meaning: CommandMeaning


def script_commands(text: str) -> list[ScriptCommand]:
    """Split one script and classify each of its commands."""
    return [
        ScriptCommand(command.text, command.gates_next, _classify(command.text))
        for command in split_commands(text)
    ]


def _shell_words(text: str) -> str:
    """Return *text* as the shell would see it after line joining.

    A backslash-newline is a line continuation, not a word. The Makefile loader
    keeps it, so every command of a continued recipe line starts with one; left
    in place it would hide the assignment or tool word that follows.

    Returns
    -------
    str
        The text with each continuation replaced by a space.
    """
    return _CONTINUATION.sub(" ", text)


def _command_words(text: str) -> list[str] | None:
    """Return the words of the first simple command, or ``None`` if unlexable."""
    segments = lex_segments(blank_references(_shell_words(text)))
    if segments is None:
        return None
    for words in segments:
        start = _command_start(words)
        if start < len(words):
            return words[start:]
    return []


def _command_start(words: list[str]) -> int:
    """Return the command word's index, past keywords, assignments and wrappers."""
    index = 0
    while index <= len(words):
        index += command_start(words[index:])
        if index >= len(words) or words[index].lstrip("@-+") not in _SHELL_KEYWORDS:
            return index
        index += 1
    return index


def _classify(text: str) -> CommandMeaning:
    """Classify one simple command."""
    words = _command_words(text)
    if words is None:
        # A script the shell lexer refuses cannot be shown to do anything, and
        # guessing that it fetches would be the one answer that matters.
        return CommandMeaning(is_unreadable=True)
    if not words:
        return CommandMeaning()
    command = words[0].lstrip("@-+")
    name = command.rsplit("/", 1)[-1]
    if name == "cargo":
        return _cargo(words[1:])
    if name == "make":
        return _make(command, words[1:])
    if _RECURSIVE_MAKE.search(text):
        # `$(MAKE)` reaches this audit as an unsettled reference, but Make
        # itself would run it as this make. Its target's ordering is not this
        # recipe's, so the walk stops here rather than guessing.
        return CommandMeaning(is_recursive_make=True)
    # A command word the expansion left unsettled may be anything at all.
    return CommandMeaning(is_unreadable=UNRESOLVED_MARK in command)


def _cargo(arguments: list[str]) -> CommandMeaning:
    """Classify a cargo invocation's subcommand and options."""
    index = 0
    while index < len(arguments) and arguments[index].startswith("+"):
        index += 1
    if index >= len(arguments):
        return CommandMeaning()
    subcommand = arguments[index]
    rest = arguments[index + 1 :]
    if UNRESOLVED_MARK in subcommand:
        return CommandMeaning(is_unreadable=True)
    if subcommand == "fetch":
        return CommandMeaning(is_prefetch="--locked" in rest)
    if subcommand == "test":
        return CommandMeaning(test="test")
    if subcommand == "nextest" and rest[:1] == ["run"]:
        return CommandMeaning(test="nextest", profile=_profile(rest[1:]))
    return CommandMeaning()


def _profile(arguments: list[str]) -> str | None:
    """Return the literal nextest profile an invocation selects, if any."""
    for index, word in enumerate(arguments):
        if word in _PROFILE_OPTIONS and index + 1 < len(arguments):
            return arguments[index + 1]
        if word.startswith("--profile="):
            return word.partition("=")[2]
        if word.startswith("-P") and len(word) > 2:
            return word[2:]
    return None


def _make(command: str, arguments: list[str]) -> CommandMeaning:
    """Classify a make invocation, telling the test target from recursion."""
    if UNRESOLVED_MARK in command:
        return CommandMeaning(is_unreadable=True)
    changes_directory = any(
        word.startswith(("-C", "--directory")) for word in arguments
    )
    goals = [word for word in arguments if not word.startswith("-") and "=" not in word]
    if any(UNRESOLVED_MARK in goal for goal in goals):
        return CommandMeaning(is_unreadable=True)
    if TEST_TARGET in goals and not changes_directory:
        return CommandMeaning(test="make")
    # Any other goal runs a target whose ordering this reader cannot follow,
    # and `make -C dir test` reads another Makefile entirely.
    return CommandMeaning(is_recursive_make=True)


def line_fetches(commands: list[ScriptCommand], *, is_resolved: bool) -> bool:
    """Return whether this line runs a fetch that must succeed before it ends.

    A fetch proves the cache warm only when the command is read from text the
    expansion settled and every command after it is reached only when it
    succeeded, all the way to the end of the line.

    Returns
    -------
    bool
        True when such a fetch appears on this line.
    """
    if not is_resolved:
        return False
    return any(
        command.meaning.is_prefetch
        and all(later.gates_next for later in commands[index:-1])
        for index, command in enumerate(commands)
    )


def gates_fetch(commands: list[ScriptCommand], position: int) -> bool:
    """Return whether a fetch earlier in this line gates the command at *position*.

    Returns
    -------
    bool
        True when a fetch precedes *position* and every command between the two
        must succeed for *position* to be reached.
    """
    return any(
        command.meaning.is_prefetch
        and all(later.gates_next for later in commands[index:position])
        for index, command in enumerate(commands[:position])
    )
