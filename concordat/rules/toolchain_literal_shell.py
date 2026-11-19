"""Read which toolchain a shell command text selects.

RT-028 needs the dated nightlies a `run` step or a Make recipe *selects*, not
the dates its text mentions. `echo "cargo +nightly-2026-10-30"` prints a date;
`cargo +nightly-2026-10-30 test` selects one. This module answers that question
for one command text, in the shape the file writes it.

The text is split into shell commands first, because a toolchain override only
means anything as a program's argument. The routes cargo and rustup offer are
then read: cargo's `+toolchain` override, a `RUSTUP_TOOLCHAIN=` assignment,
rustup's `--toolchain` option, and rustup's positional toolchain argument.

A value that is a Make reference (`$(NAME)`) is returned as a reference rather
than as a literal, so the caller decides whether the reference is a declaration
of a deliberately separate pin or an unexplained one.
"""

from __future__ import annotations

import dataclasses
import re
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc

# A value that is a Make reference rather than a literal: `$(NAME)`/`${NAME}`.
_REFERENCE: typ.Final = re.compile(
    r"^\$(?:\((?P<paren>[A-Za-z_][\w-]*)\)|\{(?P<brace>[A-Za-z_][\w-]*)\})$"
)
# The programs that take cargo's `+toolchain` override. A reference is one too
# when its name says cargo, which is what `$(CARGO)` is in a Makefile.
_TOOLCHAINS: typ.Final = (
    "cargo",
    "cargo-nextest",
    "clippy-driver",
    "cross",
    "miri",
    "rustc",
    "rustup",
)
_RUSTUP_VERBS: typ.Final = ("default", "install", "override", "run")
# rustup options that take a value, so the value is not read as a toolchain.
_VALUE_OPTIONS: typ.Final = frozenset({
    "--profile",
    "-c",
    "--component",
    "-t",
    "--target",
})
_SHELL_BREAKS: typ.Final = frozenset(";|&\n")
# Words that introduce or wrap a command rather than being one. `env`, `sudo`
# and `command` move the real program one or more words to the right.
_SHELL_KEYWORDS: typ.Final = frozenset({
    "!",
    "{",
    "}",
    "do",
    "done",
    "elif",
    "else",
    "esac",
    "fi",
    "for",
    "if",
    "then",
    "time",
    "until",
    "while",
})
_ENV_PREFIXES: typ.Final = frozenset({
    "command",
    "env",
    "exec",
    "nice",
    "nohup",
    "sudo",
    "xargs",
})
# Make's recipe prefixes: silent, ignore-error, and always-run.
_MAKE_PREFIXES: typ.Final = "@-+"
_ASSIGNMENT: typ.Final = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)=")
_HEREDOC_OPERATOR: typ.Final = re.compile(r"^(?P<operator><<-?)(?P<name>.*)$")


@dataclasses.dataclass(frozen=True, slots=True)
class Selection:
    """One toolchain a command selects, as the command writes it."""

    text: str
    is_reference: bool


def reference_name(text: str) -> str | None:
    """Return the variable a `$(NAME)`/`${NAME}` text names, if it is one."""
    match = _REFERENCE.match(text)
    return (match.group("paren") or match.group("brace")) if match else None


def _is_assignment(word: str) -> bool:
    """Return whether *word* is a `NAME=value` command-prefix assignment."""
    return bool(_ASSIGNMENT.match(word))


def _assignment_selection(word: str) -> Selection | None:
    """Return the toolchain a `RUSTUP_TOOLCHAIN=` assignment selects."""
    name, _, value = word.partition("=")
    if name != "RUSTUP_TOOLCHAIN" or not value:
        return None
    return Selection(text=value, is_reference=reference_name(value) is not None)


def _heredoc_delimiter(word: str) -> tuple[bool, str] | None:
    """Return whether *word* opens a here-document, and its delimiter.

    Returns
    -------
    tuple[bool, str] | None
        ``(needs_next_word, delimiter)`` for a `<<`/`<<-` word, or None when
        the word opens no here-document. ``needs_next_word`` is true for a
        bare operator, whose delimiter is the word that follows it.
    """
    match = _HEREDOC_OPERATOR.match(word)
    if match is None:
        return None
    return not match.group("name"), match.group("name")


def _skip_heredoc(text: str, index: int, delimiter: str, *, strip_tabs: bool) -> int:
    """Return the index just past a here-document whose body ends at *delimiter*.

    *index* is where the operator stood. The body starts on the next line and
    runs to a line that is the delimiter alone, which is how a shell finds its
    end; `<<-` also accepts leading tabs before the delimiter.

    Returns
    -------
    int
        The index of the first character after the terminator line, or the
        end of the text when no terminator is written.
    """
    line_end = text.find("\n", index)
    while line_end != -1:
        next_end = text.find("\n", line_end + 1)
        line = text[line_end + 1 : next_end if next_end != -1 else len(text)]
        if (line.lstrip("\t") if strip_tabs else line) == delimiter:
            return len(text) if next_end == -1 else next_end + 1
        line_end = next_end
    return len(text)


@dataclasses.dataclass(slots=True)
class _Scan:
    """The commands read so far, and the word being read.

    The scanner's state lives here so the character loop stays a loop: a word
    ends at a separator, a command ends at a shell break, and a here-document
    body is skipped whole between the two.
    """

    commands: list[list[str]] = dataclasses.field(default_factory=list)
    words: list[str] = dataclasses.field(default_factory=list)
    current: list[str] = dataclasses.field(default_factory=list)
    delimiter: str = ""
    needs_delimiter: bool = False
    strip_tabs: bool = False

    def end_word(self) -> None:
        """Complete the word being read and record any here-document it opens."""
        word = "".join(self.current)
        self.words.append(word)
        self.current = []
        heredoc = _heredoc_delimiter(word)
        if heredoc is not None:
            self.needs_delimiter, self.delimiter = heredoc
            self.strip_tabs = word.startswith("<<-")
        elif self.needs_delimiter:
            self.needs_delimiter = False
            self.delimiter = word

    def end_command(self) -> None:
        """Close the command the words read so far make, if there is one."""
        if self.words:
            self.commands.append(self.words)
            self.words = []


def shell_commands(text: str) -> list[list[str]]:
    """Return each command's words, split at unquoted shell separators.

    Quoted text stays inside one word and the quotes are dropped, so
    `echo "cargo +nightly-2026-10-30"` is an argument to `echo` rather than a
    selection, and `RUSTUP_TOOLCHAIN="$(PIN)"` is still the reference. An
    unquoted `#` at a word boundary starts a comment. Backslash line
    continuations are folded first, as they are in a recipe. A here-document
    body is command input rather than commands, so it is skipped whole: a
    date a script feeds to `cat` is not a date it selects.

    Returns
    -------
    list[list[str]]
        One word list per command, in the order written.
    """
    folded = re.sub(r"\\\n[ \t]*", " ", text)
    scan = _Scan()
    quote: str | None = None
    index = 0
    while index < len(folded):
        char = folded[index]
        if quote is not None:
            if char == quote:
                quote = None
            else:
                scan.current.append(char)
            index += 1
            continue
        if char in "'\"":
            quote = char
            index += 1
            continue
        if char == "\\" and index + 1 < len(folded):
            scan.current.append(folded[index + 1])
            index += 2
            continue
        if char == "#" and not scan.current:
            while index < len(folded) and folded[index] != "\n":
                index += 1
            continue
        if char.isspace() or char in _SHELL_BREAKS:
            if scan.current:
                scan.end_word()
            if scan.delimiter:
                scan.end_command()
                index = _skip_heredoc(
                    folded, index, scan.delimiter, strip_tabs=scan.strip_tabs
                )
                scan.delimiter = ""
                continue
            if char in _SHELL_BREAKS:
                scan.end_command()
        else:
            scan.current.append(char)
        index += 1
    if scan.current:
        scan.end_word()
    scan.end_command()
    return scan.commands


def _is_tool(word: str) -> bool:
    """Return whether *word* is a program that takes the `+toolchain` override."""
    bare = word.rpartition("/")[2]
    if bare in _TOOLCHAINS:
        return True
    return reference_name(bare) is not None and "cargo" in bare.lower()


def _toolchain_word(word: str) -> Selection:
    """Return the selection a toolchain value names."""
    return Selection(text=word, is_reference=reference_name(word) is not None)


def _option_values(args: list[str]) -> cabc.Iterator[Selection]:
    """Yield each `--toolchain` value, in either spelling."""
    for index, word in enumerate(args):
        value = ""
        if word == "--toolchain" and index + 1 < len(args):
            value = args[index + 1]
        elif word.startswith("--toolchain="):
            value = word.partition("=")[2]
        if value:
            yield _toolchain_word(value)


def _positional(args: list[str]) -> list[str]:
    """Return the non-option words of a `rustup` argument list."""
    names: list[str] = []
    words = iter(args)
    for word in words:
        if word in _VALUE_OPTIONS:
            next(words, None)
        elif not word.startswith("-"):
            names.append(word)
    return names


def _rustup_arguments(rest: list[str]) -> cabc.Iterator[Selection]:
    """Yield the positional toolchains of one `rustup` invocation."""
    if rest[:2] == ["toolchain", "install"]:
        rest = rest[1:]
    if not rest:
        return
    verb, args = rest[0], rest[1:]
    if verb == "override" and args[:1] == ["set"]:
        args = args[1:]
    elif verb not in _RUSTUP_VERBS:
        # `rustup component add ... --toolchain X` names its toolchain through
        # the option, which `_option_values` reads instead.
        return
    names = _positional(args)
    for name in names if verb == "install" else names[:1]:
        yield _toolchain_word(name)


def program_position(words: list[str]) -> tuple[int, str] | None:
    """Return a command's program word and its index, recipe prefixes removed.

    An assignment prefix and a shell keyword (`if`, `!`, `{`) come before the
    program. A command that is only assignments and keywords has no program.

    Returns
    -------
    tuple[int, str] | None
        The program's index in *words*, and the program word with Make's
        `@`, `-` and `+` prefixes stripped; None when there is no program.
    """
    index = 0
    while index < len(words) and (
        _is_assignment(words[index]) or words[index] in _SHELL_KEYWORDS
    ):
        index += 1
    if index >= len(words):
        return None
    return index, words[index].lstrip(_MAKE_PREFIXES)


def _program(words: list[str]) -> tuple[list[Selection], str | None, list[str]]:
    """Split one command into its assignments, program and arguments.

    Returns
    -------
    tuple[list[Selection], str | None, list[str]]
        The `RUSTUP_TOOLCHAIN=` assignments the command carries, the program
        word with Make's recipe prefixes removed, and the arguments.
    """
    selections: list[Selection] = []
    # Make removes `@`, `-` and `+` from a recipe line's first word before the
    # shell sees it, so `@RUSTUP_TOOLCHAIN=...` is still an assignment.
    words = [words[0].lstrip(_MAKE_PREFIXES), *words[1:]] if words else words
    index = 0
    while index < len(words) and _is_assignment(words[index]):
        selection = _assignment_selection(words[index])
        if selection is not None:
            selections.append(selection)
        index += 1
    while index < len(words) and words[index] in _SHELL_KEYWORDS:
        index += 1
    if index >= len(words):
        return selections, None, []
    program = words[index].lstrip(_MAKE_PREFIXES)
    index += 1
    while program in _ENV_PREFIXES and index < len(words):
        while index < len(words) and (
            words[index].startswith("-") or _is_assignment(words[index])
        ):
            selection = _assignment_selection(words[index])
            if selection is not None:
                selections.append(selection)
            index += 1
        if index >= len(words):
            return selections, None, []
        program = words[index].lstrip(_MAKE_PREFIXES)
        index += 1
    return selections, program, words[index:]


def selection_in(words: list[str]) -> cabc.Iterator[Selection]:
    """Yield each toolchain one command's words select.

    A `+toolchain` override is only read from the program's first argument:
    `echo cargo +nightly-...` names a tool in an argument, not a command. The
    override routes are cargo's `+toolchain`, a `RUSTUP_TOOLCHAIN=`
    assignment, and rustup's `--toolchain` option and positional argument.

    Yields
    ------
    Selection
        Each toolchain the command selects, in the order written.
    """
    selections, program, args = _program(words)
    yield from selections
    if program is None:
        return
    if program == "export":
        # `export NAME=value` assigns for the rest of the script without being
        # a workflow `env` declaration, so the value is a real selection. Only
        # this explicit form is read: an argument to `echo` is not one.
        yield from (
            selection
            for argument in args
            if (selection := _assignment_selection(argument)) is not None
        )
        return
    if program.rpartition("/")[2] == "rustup":
        yield from _option_values(args)
        yield from _rustup_arguments(args)
    elif _is_tool(program) and args[:1] and args[0].startswith("+"):
        yield _toolchain_word(args[0][1:])


def command_selections(text: str) -> list[Selection]:
    """Return each toolchain a command text selects, as written.

    The text is split into shell commands first, so a selection is only read
    where a program makes it, and an ignored date is an argument like any
    other. A value that is a reference is returned as one, so the caller can
    decide whether the reference is a declaration.

    Returns
    -------
    list[Selection]
        The selected toolchains, in the order the text writes them.
    """
    return [
        selection for words in shell_commands(text) for selection in selection_in(words)
    ]
