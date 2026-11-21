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

from .toolchain_literal_scan import scan_text

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


def _is_prefix(word: str) -> bool:
    """Return whether *word* stands before the program rather than being one."""
    if _is_assignment(word):
        return True
    return word in _SHELL_KEYWORDS


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
    while index < len(words) and _is_prefix(words[index]):
        index += 1
    return index


def _assignments(words: cabc.Iterable[str]) -> list[Selection]:
    """Return the `RUSTUP_TOOLCHAIN=` selections among *words*."""
    return [
        selection
        for word in words
        if (selection := _assignment_selection(word)) is not None
    ]


def _is_wrapper_word(word: str) -> bool:
    """Return whether *word* is a wrapper's option or an inline assignment."""
    if word.startswith("-"):
        return True
    return _is_assignment(word)


def _past_options(words: list[str], index: int, wrapper: str) -> int:
    """Return the index after a wrapper's own options and their operands."""
    options = _WRAPPER_VALUE_OPTIONS.get(wrapper, _NO_OPERANDS)
    while index < len(words) and _is_wrapper_word(words[index]):
        takes_operand = words[index] in options
        index += 1
        if takes_operand and index < len(words):
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
    while program in _ENV_PREFIXES:
        index = _past_options(words, index, program)
        if index >= len(words):
            return None
        program = words[index].lstrip(_MAKE_PREFIXES)
        index += 1
    return index - 1, program, _assignments(words[:index])


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
        # An assignment on a line of its own reaches a later command only
        # through `export`, which `command_selections` follows.
        return [], None, []
    index, program, selections = found
    return selections, program, words[index + 1 :]


def _override(args: list[str]) -> cabc.Iterator[Selection]:
    """Yield cargo's `+toolchain` override when it is the first argument."""
    if args and args[0].startswith("+"):
        yield _toolchain_word(args[0][1:])


def _assignment_in(words: list[str]) -> Selection | None:
    """Return the toolchain assignment among *words*, if any."""
    return next(
        (
            selection
            for word in words
            if (selection := _assignment_selection(word)) is not None
        ),
        None,
    )


def _names_toolchain(word: str) -> bool:
    """Return whether *word* is the toolchain variable, valued or bare."""
    return word.partition("=")[0] == "RUSTUP_TOOLCHAIN"


def _exports_toolchain(args: list[str]) -> bool:
    """Return whether an `export` command publishes the toolchain variable."""
    return any(_names_toolchain(word) for word in args)


def _publishes_value(args: list[str]) -> bool:
    """Return whether an `export` command gives the variable a value of its own."""
    return any("=" in word for word in args)


def _exported_assignments(commands: cabc.Iterable[list[str]]) -> list[Selection]:
    """Return the assignments an `export` publishes for the commands after it.

    A variable set on its own line is not in a later command's environment
    until `export` names it, and once exported a later assignment is visible
    too. An assignment prefixed to a command is read by `selection_in`.

    Returns
    -------
    list[Selection]
        The selections the exports publish, in the order they take effect.
    """
    found: list[Selection] = []
    pending: Selection | None = None
    is_exported = False
    for words in commands:
        _, program, args = _program(words)
        published: Selection | None = None
        if program is None:
            pending, published = _after_assignment(
                words, pending, is_exported=is_exported
            )
        elif _is_toolchain_export(program, args):
            pending, published = _after_export(args, pending)
            is_exported = True
        if published is not None:
            found.append(published)
    return found


def _is_toolchain_export(program: str, args: list[str]) -> bool:
    """Return whether the command is an `export` of the toolchain variable."""
    if program != "export":
        return False
    return _exports_toolchain(args)


def _after_assignment(
    words: list[str], pending: Selection | None, *, is_exported: bool
) -> tuple[Selection | None, Selection | None]:
    """Return the pending assignment after *words*, and any it publishes.

    Returns
    -------
    tuple[Selection | None, Selection | None]
        The assignment a later `export` could publish, and the one an already
        exported variable publishes now.
    """
    assignment = _assignment_in(words)
    if assignment is not None:
        pending = assignment
    if not is_exported:
        return pending, None
    return pending, pending


def _after_export(
    args: list[str], pending: Selection | None
) -> tuple[Selection | None, Selection | None]:
    """Return the pending assignment after an `export`, and any it publishes.

    Returns
    -------
    tuple[Selection | None, Selection | None]
        The assignment left pending (none when the export gives a value of its
        own, which `selection_in` already reports), and the earlier assignment
        a bare export publishes.
    """
    if _publishes_value(args):
        return None, None
    return pending, pending


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
    elif _is_tool(program):
        yield from _override(args)


def command_selections(text: str) -> list[Selection]:
    """Return each toolchain a command text selects, as written.

    The text is split into shell commands first, so a selection is only read
    where a program makes it, and an ignored date is an argument like any
    other. A value that is a reference is returned as one, so the caller can
    decide whether the reference is a declaration.

    A command substitution runs its own command, even inside double quotes, so
    `VERSION="$(cargo +nightly-... --version)"` selects that toolchain. Its
    selections follow the selections of the text that contains it. An
    assignment on a line of its own selects only once `export` publishes it,
    because a shell variable reaches a later command's environment only then.

    Returns
    -------
    list[Selection]
        The selected toolchains, in the order the text writes them.
    """
    commands, substitutions = scan_text(text)
    selections = [selection for words in commands for selection in selection_in(words)]
    selections.extend(_exported_assignments(commands))
    for inner in substitutions:
        selections.extend(command_selections(inner))
    return selections
