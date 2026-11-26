"""Read which workspace members a gate command selects.

`gate-workspace-coverage` holds four tools to one standard: `cargo clippy`,
`cargo nextest run`, `cargo test` (with and without `--doc`) and Whitaker. Each
takes Cargo's package selection, and each narrows silently: in a workspace with
a root package a bare command lints the root package alone, and
`workspace.default-members` or a `-p` list narrows it further. This module is
the shell-aware half. It reads one lexed simple command and, when it runs one
of the tools, records what the command selects: whether it spells out the whole
workspace, which packages it names, which it excludes, and which manifest or
directory it runs against. The policy decides what that means for the
workspace.

Cargo spells the whole workspace `--workspace` or its alias `--all`. Whitaker
takes its own options before `--` and Cargo's after it; its `--all` before the
`--` selects lint libraries, not packages, so only a spelling after `--` counts.

A command the reader cannot place is recorded unresolved, never guessed: an
unresolved word that could be part of the package selection, a command run in
another directory, or a wrapper script whose contents the audit does not read.
"""

from __future__ import annotations

import pathlib
import re
import typing as typ

from .make_commands import command_start
from .makefile_expansion import SHELL_DOLLAR, UNRESOLVED_MARK

__all__ = ["TOOLS", "Invocation", "invocations_in"]

TOOLS: typ.Final = ("clippy", "nextest", "test", "test-doc", "whitaker")
_WORKSPACE_FLAGS: typ.Final = frozenset({"--workspace", "--all"})
_PACKAGE_FLAGS: typ.Final = frozenset({"-p", "--package"})
_VALUE_FLAGS: typ.Final = _PACKAGE_FLAGS | {"--exclude", "--manifest-path"}
# `cargo` options that take the next word as a value, so it is not the subcommand.
_CARGO_VALUE_FLAGS: typ.Final = frozenset({
    "-Z",
    "-C",
    "--config",
    "--color",
    "--manifest-path",
    "--target-dir",
})
# `cargo llvm-cov` subcommands that run no tests, so select no packages.
_LLVM_COV_REPORTING: typ.Final = frozenset({
    "report",
    "clean",
    "show-env",
    "run",
    "html",
    "json",
    "lcov",
    "cobertura",
})
_WRAPPER_SUFFIXES: typ.Final = (".py", ".sh", ".bash")
# A script runs a gate tool when it is named for running it: `run_whitaker`,
# `clippy_runner`, or the tool's own name. `check_serial_nextest_matrix` checks
# a file and `install_whitaker` installs a tool, so neither is a wrapper.
_WRAPPER_STEM: typ.Final = re.compile(
    r"^(?:(?:run|exec|invoke|wrap|lint)[_-])?(?P<tool>whitaker|clippy|nextest)"
    r"(?:[_-](?:run|runner|wrapper))?$|^(?:run|exec|invoke|wrap|lint)[_-].*?"
    r"(?P<named>whitaker|clippy|nextest)$"
)
# Spellings of the root manifest, which `--manifest-path` may name without
# leaving the root workspace.
_ROOT_MANIFESTS: typ.Final = frozenset({"Cargo.toml", "./Cargo.toml"})
# Options that take a value which cannot name a package, so an unresolved word
# in that place does not hide the selection.
_BENIGN_VALUE_FLAGS: typ.Final = frozenset({
    "--profile",
    "-j",
    "--jobs",
    "--features",
    "-F",
    "--target",
    "--target-dir",
    "--message-format",
    "--color",
    "--config",
    "--cargo-profile",
    "--filter-expr",
    "-E",
})
_UNRESOLVED_WORD: typ.Final = (UNRESOLVED_MARK, SHELL_DOLLAR, "$")
_INFORMATION_FLAGS: typ.Final = frozenset({"--version", "-V", "--help", "-h"})
_SHELL_KEYWORDS: typ.Final = frozenset({
    "if",
    "then",
    "else",
    "elif",
    "do",
    "while",
    "until",
    "!",
})


class Invocation(typ.TypedDict):
    """One command that runs a gate tool, and what it selects.

    `resolved` is false when the reader cannot say which members the command
    selects; `why` then says what stopped it. `changes_directory` is true when
    the command runs after a `cd` in the same line, or against a manifest other
    than the root one, so the root workspace's shape does not describe it.
    """

    tool: str
    source: str
    command: str
    resolved: bool
    why: str
    workspace_flag: bool
    packages: list[str]
    excludes: list[str]
    changes_directory: bool


def _is_unresolved(word: str) -> bool:
    """Return whether *word* holds a reference the reader could not settle."""
    return any(mark in word for mark in _UNRESOLVED_WORD)


def _selection(arguments: list[str]) -> tuple[list[str], list[str], str | None]:
    """Return the packages, exclusions and manifest path named in *arguments*."""
    packages: list[str] = []
    excludes: list[str] = []
    manifest: str | None = None
    for index, word in enumerate(arguments):
        following = arguments[index + 1] if index + 1 < len(arguments) else None
        flag, _, inline = word.partition("=")
        value = inline if "=" in word else following
        if value is None:
            continue
        if flag in _PACKAGE_FLAGS or word.startswith("--package="):
            packages.append(value)
        elif flag == "--exclude":
            excludes.append(value)
        elif flag == "--manifest-path":
            manifest = value
    return packages, excludes, manifest


def _hides_selection(arguments: list[str], *, has_workspace_flag: bool) -> bool:
    """Return whether an unresolved word could change which packages are chosen.

    With `--workspace` spelled out, a stray unresolved word cannot narrow the
    selection, but one that is the value of a package, exclusion or manifest
    option still can. Without it, any unresolved word could be a `-p`.

    Returns
    -------
    bool
        Whether the selection cannot be read.
    """
    for index, word in enumerate(arguments):
        if not _is_unresolved(word):
            continue
        previous = arguments[index - 1] if index else ""
        if previous in _BENIGN_VALUE_FLAGS:
            continue
        if not has_workspace_flag or previous in _VALUE_FLAGS:
            return True
        if word.split("=", 1)[0] in _VALUE_FLAGS:
            return True
    return False


def _cargo_subcommand(arguments: list[str]) -> tuple[str | None, list[str]]:
    """Return the Cargo subcommand and the arguments after it."""
    iterator = iter(enumerate(arguments))
    for index, word in iterator:
        if word in _CARGO_VALUE_FLAGS:
            next(iterator, None)
        elif not word.startswith(("-", "+")):
            return word, arguments[index + 1 :]
    return None, []


def _cargo_tool(
    subcommand: str | None, rest: list[str]
) -> tuple[str | None, list[str]]:
    """Return the gate tool a Cargo subcommand runs, and its own arguments."""
    own = rest[: rest.index("--")] if "--" in rest else rest
    match subcommand:
        case "clippy":
            return "clippy", rest
        case "test":
            return ("test-doc" if "--doc" in own else "test"), rest
        case "nextest" if rest[:1] == ["run"]:
            return "nextest", rest[1:]
        case "dylint":
            return "whitaker", rest
        case "llvm-cov":
            if rest[:1] == ["nextest"]:
                return "nextest", rest[1:]
            if rest[:1] and rest[0] in _LLVM_COV_REPORTING:
                return None, []
            return "test", rest
        case _:
            return None, []


def _wrapper_tool(word: str) -> str | None:
    """Return the gate tool a wrapper script's name claims to run."""
    path = pathlib.PurePosixPath(word)
    if path.suffix not in _WRAPPER_SUFFIXES:
        return None
    match = _WRAPPER_STEM.match(path.stem)
    return match and (match.group("tool") or match.group("named"))


def _build(
    tool: str, arguments: list[str], where: tuple[str, str], *, moved: bool
) -> Invocation:
    """Return the invocation a tool's selection arguments describe."""
    # Whitaker takes its own options before `--` and Cargo's after it, so only
    # the Cargo half selects packages. Cargo's tools stop at `--` too: what
    # follows goes to the test harness or to Clippy's lints.
    boundary = arguments.index("--") if "--" in arguments else None
    if tool == "whitaker":
        cargo_arguments = arguments[boundary + 1 :] if boundary is not None else []
        scanned = arguments
    else:
        cargo_arguments = arguments[:boundary] if boundary is not None else arguments
        scanned = cargo_arguments
    packages, excludes, manifest = _selection(scanned)
    has_flag = any(word in _WORKSPACE_FLAGS for word in cargo_arguments)
    hidden = _hides_selection(scanned, has_workspace_flag=has_flag)
    source, command = where
    return {
        "tool": tool,
        "source": source,
        "command": command,
        "resolved": not hidden,
        "why": "a word the audit could not resolve may select packages"
        if hidden
        else "",
        "workspace_flag": has_flag,
        "packages": packages,
        "excludes": excludes,
        "changes_directory": moved
        or (manifest is not None and manifest not in _ROOT_MANIFESTS),
    }


def _wrapper(tool: str, where: tuple[str, str], *, moved: bool) -> Invocation:
    """Return the unresolved fact for a wrapper script the audit does not read."""
    source, command = where
    return {
        "tool": tool,
        "source": source,
        "command": command,
        "resolved": False,
        "why": "a wrapper script selects the packages, and the audit does not read it",
        "workspace_flag": False,
        "packages": [],
        "excludes": [],
        "changes_directory": moved,
    }


def _moves_directory(words: list[str]) -> bool:
    """Return whether a `cd` command leaves the checkout root."""
    target = words[1] if len(words) > 1 else "~"
    return target not in {".", "./"}


def _invocation_of(
    words: list[str], where: tuple[str, str], *, moved: bool
) -> Invocation | None:
    """Return the gate-tool invocation one simple command makes, if it makes one."""
    start = command_start(words)
    if start >= len(words):
        return None
    head = pathlib.PurePosixPath(words[start].lstrip("@-+")).name
    arguments = words[start + 1 :]
    if any(word in _INFORMATION_FLAGS for word in arguments):
        return None
    if head == "whitaker" and arguments:
        return _build("whitaker", arguments, where, moved=moved)
    if head == "cargo":
        subcommand, rest = _cargo_subcommand(arguments)
        tool, own = _cargo_tool(subcommand, rest)
        return _build(tool, own, where, moved=moved) if tool else None
    wrapped = _wrapper_tool(head)
    return _wrapper(wrapped, where, moved=moved) if wrapped else None


def _without_keywords(words: list[str]) -> list[str]:
    """Return *words* without the shell keywords that open a command.

    `then cargo test` and `if cargo test` run `cargo test`; the keyword is not
    the command word.

    Returns
    -------
    list[str]
        The words from the command word on.
    """
    start = 0
    while start < len(words) and words[start].lstrip("@-+") in _SHELL_KEYWORDS:
        start += 1
    return words[start:]


def invocations_in(
    segments: list[list[str]], where: tuple[str, str]
) -> list[Invocation]:
    """Return the gate-tool invocations in one lexed line.

    A `cd` earlier in the line marks every later invocation as running
    elsewhere. *where* is the line's source (a file and line, or a job and
    step) and its text, kept on each invocation so a finding can show what was
    read.

    Returns
    -------
    list[Invocation]
        One fact per command that runs `cargo clippy`, `cargo nextest run`,
        `cargo test`, Whitaker, or a wrapper script named for one of them.
    """
    found: list[Invocation] = []
    moved = False
    for raw_words in segments:
        words = _without_keywords(raw_words)
        start = command_start(words)
        if words[start : start + 1] == ["cd"]:
            moved = moved or _moves_directory(words[start:])
            continue
        invocation = _invocation_of(words, where, moved=moved)
        if invocation is not None:
            found.append(invocation)
    return found
