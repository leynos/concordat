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
# Each wrapper has its own arities, so `sudo -n cargo` is passwordless sudo
# running cargo while `xargs -n 1 cargo` passes cargo one argument at a time.
# Only the options that take a separate operand are listed; a short option's
# attached form (`-uNAME`) is one word and needs no operand.
_NO_OPERANDS: typ.Final = frozenset()
_WRAPPER_VALUE_OPTIONS: typ.Final[dict[str, frozenset[str]]] = {
    "env": frozenset({"-u", "--unset", "-C", "--chdir", "-S", "--split-string"}),
    "exec": frozenset({"-a", "--argv0"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "nohup": _NO_OPERANDS,
    "command": _NO_OPERANDS,
    "sudo": frozenset({
        "-C",
        "--close-from",
        "-D",
        "--chdir",
        "-g",
        "--group",
        "-h",
        "--host",
        "-p",
        "--prompt",
        "-R",
        "--chroot",
        "-r",
        "--role",
        "-T",
        "--command-timeout",
        "-t",
        "--type",
        "-U",
        "--other-user",
        "-u",
        "--user",
    }),
    "xargs": frozenset({
        "-a",
        "--arg-file",
        "-d",
        "--delimiter",
        "-E",
        "-e",
        "--eof",
        "-I",
        "-i",
        "--replace",
        "-L",
        "-l",
        "--max-lines",
        "-n",
        "--max-args",
        "-P",
        "--max-procs",
        "-s",
        "--max-chars",
    }),
}


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


def _opens_substitution(text: str, index: int) -> bool:
    """Return whether `$(` at *index* starts a command substitution.

    `$((` is arithmetic rather than a command, so it opens no substitution.

    Returns
    -------
    bool
        Whether *index* starts a command substitution.
    """
    return text.startswith("$(", index) and not text.startswith("$((", index)


def _opens_heredoc(text: str, index: int) -> bool:
    """Return whether `<<` at *index* opens a here-document.

    `<<<` is a here string, whose operand is one word rather than a body, so
    the second and third `<` of one open nothing.

    Returns
    -------
    bool
        Whether *index* opens a here-document.
    """
    return (
        text.startswith("<<", index)
        and not text.startswith("<<<", index)
        and text[index - 1 : index] != "<"
    )


def _substitution_end(text: str, index: int) -> int:
    """Return the index just past the `)` that closes the substitution at *index*.

    The parentheses are balanced across nested substitutions, and quoted text
    inside the substitution does not close it, so `$(echo ")")` is whole.

    Returns
    -------
    int
        The index after the closing `)`, or the end of the text when the
        substitution is never closed.
    """
    depth = 1
    scan = index + 2
    quote: str | None = None
    while scan < len(text):
        char = text[scan]
        if quote is not None:
            if char == "\\" and quote == '"':
                scan += 2
                continue
            if char == quote:
                quote = None
            scan += 1
            continue
        if char in "'\"":
            quote = char
        elif char == "\\":
            scan += 1
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return scan + 1
        scan += 1
    return len(text)


def _heredoc_body(
    text: str, index: int, delimiter: str, *, strip_tabs: bool
) -> tuple[int, str]:
    """Return the index past a here-document, and the body it delimits.

    *index* is where the operator stood. The body starts on the next line and
    runs to a line that is the delimiter alone, which is how a shell finds its
    end; `<<-` also accepts leading tabs before the delimiter.

    Returns
    -------
    tuple[int, str]
        The index of the first character after the terminator line, or the
        end of the text when no terminator is written, and the body itself.
    """
    start = text.find("\n", index)
    if start == -1:
        return len(text), ""
    start += 1
    line_start = start
    while True:
        line_end = text.find("\n", line_start)
        line = text[line_start : line_end if line_end != -1 else len(text)]
        if (line.lstrip("\t") if strip_tabs else line) == delimiter:
            return len(text) if line_end == -1 else line_end + 1, text[start:line_start]
        if line_end == -1:
            return len(text), text[start:]
        line_start = line_end + 1


def _body_substitutions(body: str) -> list[str]:
    """Return the command substitutions an unquoted here-document body runs.

    A body is input rather than commands, but `$(...)` and backticks in it
    still execute, because the shell expands an unquoted delimiter's body.

    Returns
    -------
    list[str]
        The text of each substitution the body runs.
    """
    found: list[str] = []
    index = 0
    while index < len(body):
        char = body[index]
        if char == "\\":
            index += 2
        elif _opens_substitution(body, index):
            end = _substitution_end(body, index)
            closed = body[end - 1 : end] == ")"
            found.append(body[index + 2 : end - 1 if closed else end])
            index = end
        elif char == "`":
            end = body.find("`", index + 1)
            end = len(body) if end == -1 else end
            found.append(body[index + 1 : end])
            index = end + 1
        else:
            index += 1
    return found


@dataclasses.dataclass(slots=True)
class _Scan:
    """The commands read so far, and the word being read.

    The scanner's state lives here so the character loop stays a loop: a word
    ends at a separator, a command ends at a shell break, a here-document body
    is skipped between the two, and a command substitution's text is kept so
    the command it runs can be read separately.
    """

    commands: list[list[str]] = dataclasses.field(default_factory=list)
    substitutions: list[str] = dataclasses.field(default_factory=list)
    words: list[str] = dataclasses.field(default_factory=list)
    current: list[str] = dataclasses.field(default_factory=list)
    delimiter: str = ""
    needs_delimiter: bool = False
    strip_tabs: bool = False
    delimiter_quoted: bool = False

    def end_word(self) -> None:
        """Complete the word being read, using it as a delimiter when due."""
        word = "".join(self.current)
        self.words.append(word)
        self.current = []
        if self.needs_delimiter:
            self.needs_delimiter = False
            self.delimiter = word

    def end_command(self) -> None:
        """Close the command the words read so far make, if there is one."""
        if self.words:
            self.commands.append(self.words)
            self.words = []

    def separate(self, text: str, char: str, index: int) -> int:
        """End the word and command at *char*, skipping a here-document body.

        Returns
        -------
        int
            The index to resume reading from.
        """
        if self.current:
            self.end_word()
        if char in _SHELL_BREAKS:
            self.end_command()
        if self.delimiter and char == "\n":
            # A command after `;` on the declaration line still runs; the body
            # it feeds starts on the next line.
            delimiter, self.delimiter = self.delimiter, ""
            quoted, self.delimiter_quoted = self.delimiter_quoted, False
            end, body = _heredoc_body(
                text, index, delimiter, strip_tabs=self.strip_tabs
            )
            if not quoted:
                self.substitutions.extend(_body_substitutions(body))
            return end
        return index + 1

    def open_heredoc(self, text: str, index: int) -> int:
        """Start a here-document at its operator.

        Returns
        -------
        int
            The index after the `<<` or `<<-` operator.
        """
        if self.current:
            # `cat<<EOF` writes the operator against the command.
            self.end_word()
        self.strip_tabs = text.startswith("<<-", index)
        self.needs_delimiter = True
        self.delimiter_quoted = False
        return index + (3 if self.strip_tabs else 2)

    def read_substitution(self, text: str, index: int) -> int:
        """Record the command a `$(...)` substitution runs and keep its text.

        Returns
        -------
        int
            The index after the substitution.
        """
        end = _substitution_end(text, index)
        closed = text[end - 1 : end] == ")"
        self.substitutions.append(text[index + 2 : end - 1 if closed else end])
        self.current.append(text[index:end])
        return end

    def read_backquote(self, text: str, index: int) -> int:
        """Record the command a backquoted substitution runs and keep its text.

        Returns
        -------
        int
            The index after the closing backtick.
        """
        end = index + 1
        while end < len(text) and text[end] != "`":
            end += 2 if text[end] == "\\" else 1
        closed = text[end : end + 1] == "`"
        self.substitutions.append(text[index + 1 : end])
        self.current.append(text[index : end + 1 if closed else end])
        return end + 1 if closed else end


def _read_quoted(
    text: str, index: int, quote: str, scan: _Scan
) -> tuple[str | None, int]:
    r"""Read one character of a quoted word.

    In double quotes a backslash protects the next character, so `\"` does not
    close the word, and a command substitution still runs even though its text
    is quoted. In single quotes neither is true.

    Returns
    -------
    tuple[str | None, int]
        The quote still open, if any, and the index to resume reading from.
    """
    char = text[index]
    if char == quote:
        return None, index + 1
    if quote != '"':
        scan.current.append(char)
        return quote, index + 1
    if char == "\\" and index + 1 < len(text):
        # A backslash-newline is removed outright rather than ending the word.
        if text[index + 1] != "\n":
            scan.current.append(text[index + 1])
        return quote, index + 2
    if _opens_substitution(text, index):
        return quote, scan.read_substitution(text, index)
    if char == "`":
        return quote, scan.read_backquote(text, index)
    scan.current.append(char)
    return quote, index + 1


def _comment_end(text: str, index: int) -> int:
    """Return the index of the newline that ends the comment at *index*."""
    end = text.find("\n", index)
    return len(text) if end == -1 else end


def _scan(text: str) -> _Scan:
    """Read *text* into its commands and the substitutions that run in them."""
    scan = _Scan()
    quote: str | None = None
    index = 0
    while index < len(text):
        char = text[index]
        if quote is not None:
            quote, index = _read_quoted(text, index, quote, scan)
        elif char in "'\"":
            # A quoted here-document delimiter suppresses its body's expansion.
            scan.delimiter_quoted = scan.delimiter_quoted or scan.needs_delimiter
            quote = char
            index += 1
        elif char == "\\" and index + 1 < len(text):
            # A backslash-newline is removed outright rather than ending the
            # word, so `car\<newline>go` runs cargo.
            if text[index + 1] != "\n":
                scan.current.append(text[index + 1])
            index += 2
        elif _opens_substitution(text, index):
            index = scan.read_substitution(text, index)
        elif char == "`":
            index = scan.read_backquote(text, index)
        elif char == "#" and not scan.current:
            index = _comment_end(text, index)
        elif _opens_heredoc(text, index):
            index = scan.open_heredoc(text, index)
        elif char in "()":
            # A parenthesized group runs its contents: `(cargo +nightly-...)`.
            if scan.current:
                scan.end_word()
            index += 1
        elif char.isspace() or char in _SHELL_BREAKS:
            index = scan.separate(text, char, index)
        else:
            scan.current.append(char)
            index += 1
    if scan.current:
        scan.end_word()
    scan.end_command()
    return scan


def shell_commands(text: str) -> list[list[str]]:
    """Return each command's words, split at unquoted shell separators.

    Quoted text stays inside one word and the quotes are dropped, so
    `echo "cargo +nightly-2026-10-30"` is an argument to `echo` rather than a
    selection, and `RUSTUP_TOOLCHAIN="$(PIN)"` is still the reference. An
    unquoted `#` at a word boundary starts a comment. A backslash-newline is
    removed without ending the word, as it is in a recipe. A here-document
    body is command input rather than commands, so it is skipped: a date a
    script feeds to `cat` is not a date it selects. The operator need not be
    spaced from the command, so `cat<<EOF` opens one too. A parenthesized
    group and a backquoted substitution each run their own contents.

    Returns
    -------
    list[list[str]]
        One word list per command, in the order written.
    """
    return _scan(text).commands


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


def _without_recipe_prefix(words: list[str]) -> list[str]:
    """Return *words* with Make's `@`, `-` and `+` stripped from the first word.

    Make removes those prefixes from a recipe line before the shell sees it,
    so `@RUSTUP_TOOLCHAIN=...` is still an assignment and `@cargo` a program.

    Returns
    -------
    list[str]
        The words, with the first word's leading recipe prefixes removed.
    """
    return [words[0].lstrip(_MAKE_PREFIXES), *words[1:]] if words else words


def _prefix_end(words: list[str]) -> int:
    """Return the index of the first word that is neither assignment nor keyword.

    Shell keywords (`if`, `!`, `{`, `do`) and `NAME=value` assignments may
    interleave before the program: `if RUSTUP_TOOLCHAIN=... cargo test`.

    Returns
    -------
    int
        The index of the first word that is the program, or of the word after
        the last prefix when the command has no program.
    """
    index = 0
    while index < len(words) and (
        _is_assignment(words[index]) or words[index] in _SHELL_KEYWORDS
    ):
        index += 1
    return index


def _executable(words: list[str]) -> tuple[int, str, list[Selection]] | None:
    """Return the program one command runs, unwrapping its wrappers.

    `env`, `sudo`, `command` and the rest move the real program to the right
    of their own options, and an option that takes an operand moves it one
    word further still: `env -u NAME cargo ...` runs cargo.

    Returns
    -------
    tuple[int, str, list[Selection]] | None
        The program's index in *words*, its word with Make's recipe prefixes
        removed, and the `RUSTUP_TOOLCHAIN=` assignments read on the way; None
        when the command has no program.
    """
    words = _without_recipe_prefix(words)
    index = _prefix_end(words)
    if index >= len(words):
        return None
    program = words[index].lstrip(_MAKE_PREFIXES)
    index += 1
    while program in _ENV_PREFIXES and index < len(words):
        options = _WRAPPER_VALUE_OPTIONS.get(program, _NO_OPERANDS)
        while index < len(words) and (
            words[index].startswith("-") or _is_assignment(words[index])
        ):
            takes_operand = words[index] in options
            index += 1
            if takes_operand and index < len(words):
                index += 1
        if index >= len(words):
            return None
        program = words[index].lstrip(_MAKE_PREFIXES)
        index += 1
    selections = [
        selection
        for word in words[:index]
        if _is_assignment(word)
        if (selection := _assignment_selection(word)) is not None
    ]
    return index - 1, program, selections


def program_position(words: list[str]) -> tuple[int, str] | None:
    """Return a command's program word and its index, wrappers unwrapped.

    An assignment prefix and a shell keyword (`if`, `!`, `{`) come before the
    program, and a wrapper's own options and operands come before the program
    it runs. A command that has no program has no position.

    Returns
    -------
    tuple[int, str] | None
        The program's index in *words*, and the program word with Make's
        `@`, `-` and `+` prefixes stripped; None when there is no program.
    """
    found = _executable(words)
    return None if found is None else found[:2]


def _program(words: list[str]) -> tuple[list[Selection], str | None, list[str]]:
    """Split one command into its assignments, program and arguments.

    Returns
    -------
    tuple[list[Selection], str | None, list[str]]
        The `RUSTUP_TOOLCHAIN=` assignments the command carries, the program
        word with Make's recipe prefixes removed, and the arguments.
    """
    found = _executable(words)
    if found is None:
        return [], None, []
    index, program, selections = found
    return selections, program, words[index + 1 :]


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

    A command substitution runs its own command, even inside double quotes, so
    `VERSION="$(cargo +nightly-... --version)"` selects that toolchain. Its
    selections follow the selections of the text that contains it.

    Returns
    -------
    list[Selection]
        The selected toolchains, in the order the text writes them.
    """
    scan = _scan(text)
    selections = [
        selection for words in scan.commands for selection in selection_in(words)
    ]
    for inner in scan.substitutions:
        selections.extend(command_selections(inner))
    return selections
