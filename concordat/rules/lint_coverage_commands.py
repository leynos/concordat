"""Read one simple command as a lint-coverage run: what it checks, and how.

`gate-lint-coverage` asks of the commands a gate runs whether they check every
target, every feature and with warnings denied, and whether `cargo doc` is a
gate. The shared gate readers say which packages a command selects; they do not
keep the rest of its arguments. This module reads those from one simple command
(`cargo clippy ...`, `cargo doc ...`, `whitaker ...`, or a script that builds
the lanes), as a `Run` fact, and decides nothing.

The facts are about the command line only. Where a flag is not on it
(`deny_warnings` is None), the policy asks what the environment says; nothing is
inferred here. A word the Make expansion left unresolved, among the arguments,
makes the run unresolved: it may stand for a flag.
"""

from __future__ import annotations

import pathlib
import posixpath
import re
import typing as typ

from .gate_scope_commands import split_cargo_subcommand
from .make_commands import command_start
from .makefile_expansion import SHELL_DOLLAR, UNRESOLVED_MARK

__all__ = ["Run", "flags_deny_warnings", "parse_command", "script_run"]

_ASSIGNMENT: typ.Final = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.DOTALL)
# A lint-level flag for `warnings`: the last one on a line decides, so a later
# `-W warnings` undoes an earlier `-D warnings`.
_WARNINGS_LEVEL: typ.Final = re.compile(
    r"(?:^|\s)(-[DWAF]|--(?:deny|warn|allow|forbid))(?:\s|=)*warnings\b"
)
_DENYING_FLAGS: typ.Final = frozenset({"-D", "-F", "--deny", "--forbid"})
_UNRESOLVED: typ.Final = (UNRESOLVED_MARK, SHELL_DOLLAR, "$")
_ALL_TARGET_FLAGS: typ.Final = frozenset({
    "--lib",
    "--bins",
    "--tests",
    "--benches",
    "--examples",
})
_WORKSPACE_FLAGS: typ.Final = frozenset({"--workspace", "--all"})
_SCRIPT_SUFFIXES: typ.Final = (".py", ".sh", ".bash")
_INTERPRETERS: typ.Final = frozenset({"python", "python3", "uv", "sh", "bash", "env"})


class Run(typ.TypedDict):
    """One command that runs Clippy, Whitaker or `cargo doc`, and how.

    `tool` is `clippy`, `whitaker` or `doc`. `manifest` is the Cargo.toml the
    command targets, relative to the checkout (`--manifest-path`, or the
    directory a `cd` in the same shell moved to), and None when the command
    names none and the directory is the root; `is_resolved` is false when the
    audit cannot say. `deny_warnings` is True or False when the command line
    says (`-D warnings` in `RUSTFLAGS` or `RUSTDOCFLAGS` or after `--`), and
    None when it says nothing. `lanes` carries the lane inputs of a script that
    builds the lanes (`NAME=value` words with `LANE` in the name).
    """

    tool: str
    source: str
    is_placed: bool
    is_resolved: bool
    why: str
    manifest: str | None
    all_targets: bool
    all_features: bool
    no_default_features: bool
    features: list[str]
    workspace: bool
    packages: list[str]
    no_deps: bool
    narrows: bool
    excludes: list[str]
    deny_warnings: bool | None
    lanes: list[str]


def _assignments(words: list[str], start: int) -> dict[str, str]:
    """Return the `NAME=value` words before the command word."""
    found: dict[str, str] = {}
    for word in words[:start]:
        match = _ASSIGNMENT.match(word.lstrip("@-+"))
        if match is not None:
            found[match.group(1)] = match.group(2)
    return found


def _command_index(words: list[str]) -> int:
    """Return the index of the command word, past assignments and wrappers.

    A Make reference the expansion could not settle at the head of a command
    (`$(CARGO_BUILD_ENV) $(UV_ENV) uv run ...`) is read as an environment
    prefix: it stands where assignments stand, and the command word is the first
    word after it that is neither one.

    Returns
    -------
    int
        The index of the command word, or `len(words)` when there is none.
    """
    start = 0
    while start < len(words):
        following = start + command_start(words[start:])
        if following < len(words) and words[following] == UNRESOLVED_MARK:
            start = following + 1
        else:
            return following
    return start


def _is_unresolved(word: str) -> bool:
    """Return whether *word* holds a reference the expansion could not settle."""
    return any(mark in word for mark in _UNRESOLVED)


def _values(arguments: list[str], flags: frozenset[str]) -> list[str]:
    """Return the values given to any of *flags*, in `--flag v` or `--flag=v` form."""
    found: list[str] = []
    for index, word in enumerate(arguments):
        flag, equals, inline = word.partition("=")
        if flag not in flags:
            continue
        if equals:
            found.append(inline)
        elif index + 1 < len(arguments):
            found.append(arguments[index + 1])
    return found


def _features(arguments: list[str]) -> list[str]:
    """Return the feature names `--features` and `-F` list, split on commas.

    A `package/feature` name keeps its qualifier: `a/fast` enables `fast` in `a`
    only, so it must not stand for `fast` in another package.

    Returns
    -------
    list[str]
        The sorted feature names, as written.
    """
    values = _values(arguments, frozenset({"--features", "-F"}))
    return sorted({
        name for value in values for name in re.split(r"[\s,]+", value) if name
    })


_NARROWING_FLAGS: typ.Final = frozenset({
    "--exclude",
    "--bin",
    "--bins",
    "--example",
    "--examples",
})


def _narrows(arguments: list[str]) -> bool:
    """Return whether the arguments leave out packages or target kinds.

    `--exclude` drops packages from a workspace selection and `--bins` or
    `--example` document only some targets, so such a command does not cover the
    whole surface even when it says `--workspace`.

    Returns
    -------
    bool
        Whether a narrowing flag is present.
    """
    return any(word.partition("=")[0] in _NARROWING_FLAGS for word in arguments)


def _all_targets(arguments: list[str]) -> bool:
    """Return whether the arguments check every target kind."""
    given = set(arguments)
    return "--all-targets" in given or given >= _ALL_TARGET_FLAGS


def _denies(text: str | None) -> bool | None:
    """Return whether a flags string denies warnings, or None when it is absent.

    The last flag that sets the level of `warnings` decides.

    Returns
    -------
    bool | None
        True when the last level flag denies, False when it does not or none is
        given, None when there is no text.
    """
    if text is None:
        return None
    return _last_level(text) is True


def flags_deny_warnings(text: str) -> bool:
    """Return whether a flags string ends with `warnings` denied."""
    return _last_level(text) is True


def _last_level(text: str) -> bool | None:
    """Return whether the last level flag for `warnings` denies, or None if none."""
    levels = _WARNINGS_LEVEL.findall(f" {text}")
    return None if not levels else levels[-1] in _DENYING_FLAGS


def _warning_state(
    environment: dict[str, str], variable: str, lint_flags: list[str]
) -> bool | None:
    """Return what the command line says about denying warnings.

    The environment variable assigned on the command wins over nothing: a
    command that assigns it without `-D warnings` has said it does not deny.
    Flags after `--` (Clippy's lint arguments) also count.

    Returns
    -------
    bool | None
        True when the command line denies warnings, False when it assigns the
        variable without the deny, and None when it says nothing.
    """
    # Flags after `--` reach rustc after `RUSTFLAGS`, so they have the last word.
    after = _last_level(" ".join(lint_flags))
    return after if after is not None else _denies(environment.get(variable))


def _arguments_without_whitaker_options(arguments: list[str]) -> list[str]:
    """Return Whitaker's arguments without its own `--all` and `--lib NAME`."""
    kept: list[str] = []
    skip_next = False
    for word in arguments:
        if skip_next:
            skip_next = False
        elif word == "--lib":
            skip_next = True
        elif word not in {"--all", "--"}:
            kept.append(word)
    return kept


def _manifest_of(
    arguments: list[str], directory: str | None
) -> tuple[str | None, bool]:
    """Return the manifest a command targets, and whether that is known.

    `--manifest-path` wins; otherwise the directory a `cd` in the same shell
    moved to (`None` there means a `cd` the audit could not follow). A command in
    the root directory names no manifest.

    Returns
    -------
    tuple[str | None, bool]
        The manifest path (None at the root), and whether the placement is known.
    """
    named = _values(arguments, frozenset({"--manifest-path"}))
    if named:
        if _is_unresolved(named[-1]) or directory is None:
            return None, False
        # From a directory a `..` may step out of a symbolic link to somewhere
        # the string path does not name, so it is not followed.
        if directory and ".." in named[-1].split("/"):
            return None, False
        return posixpath.normpath(posixpath.join(directory, named[-1])), True
    if directory is None:
        return None, False
    if not directory:
        return None, True
    return posixpath.join(directory, "Cargo.toml"), True


class _Command(typ.NamedTuple):
    """A recognised tool command: its Cargo arguments, lint flags and environment."""

    tool: str
    arguments: list[str]
    lint_flags: list[str]
    environment: dict[str, str]


def _describe(command: _Command, source: str, directory: str | None) -> Run:
    """Return the run a tool command describes."""
    arguments = command.arguments
    manifest, is_known = _manifest_of(arguments, directory)
    variable = "RUSTDOCFLAGS" if command.tool == "doc" else "RUSTFLAGS"
    hidden = any(_is_unresolved(word) for word in arguments)
    return {
        "tool": command.tool,
        "source": source,
        "is_placed": is_known,
        "is_resolved": is_known and not hidden,
        "why": _why(hidden=hidden, is_known=is_known),
        "manifest": manifest,
        "all_targets": _all_targets(arguments),
        "all_features": "--all-features" in arguments,
        "no_default_features": "--no-default-features" in arguments,
        "features": _features(arguments),
        "workspace": any(word in _WORKSPACE_FLAGS for word in arguments),
        "packages": _values(arguments, frozenset({"-p", "--package"})),
        "no_deps": "--no-deps" in arguments,
        "narrows": _narrows(arguments),
        "excludes": _values(arguments, frozenset({"--exclude"})),
        "deny_warnings": _warning_state(
            command.environment, variable, command.lint_flags
        ),
        "lanes": [],
    }


def _why(*, hidden: bool, is_known: bool) -> str:
    """Return why a run is unresolved, or the empty string."""
    if hidden:
        return "a word the audit could not resolve among the arguments may add flags"
    if not is_known:
        return "it runs after a `cd` or against a manifest the audit could not place"
    return ""


def parse_command(words: list[str], source: str, directory: str | None) -> Run | None:
    """Return the Clippy, Whitaker or `cargo doc` run a simple command makes.

    Parameters
    ----------
    words:
        The words of one simple command.
    source:
        Where the command is (a Makefile line or a workflow step).
    directory:
        The directory the command runs in, relative to the checkout (`""` at
        the root), or None when a `cd` the audit could not follow moved it.

    Returns
    -------
    Run | None
        The run, or None when the command runs none of those tools.
    """
    start = _command_index(words)
    if start >= len(words):
        return None
    head = pathlib.PurePosixPath(words[start].lstrip("@-+")).name
    command = _recognise(head, words[start + 1 :], _assignments(words, start))
    return None if command is None else _describe(command, source, directory)


def _recognise(
    head: str, arguments: list[str], environment: dict[str, str]
) -> _Command | None:
    """Return the tool command a command word and its arguments make, if any."""
    if head == "whitaker" and arguments:
        own = _arguments_without_whitaker_options(arguments)
        return _Command("whitaker", own, [], environment)
    if head != "cargo":
        return None
    subcommand, rest = split_cargo_subcommand(arguments)
    own, lint_flags = _split_at_separator(rest)
    match subcommand:
        case "clippy":
            return _Command("clippy", own, lint_flags, environment)
        case "doc":
            return _Command("doc", own, [], environment)
        case "dylint":
            return _Command(
                "whitaker", _arguments_without_whitaker_options(rest), [], environment
            )
        case _:
            return None


def _split_at_separator(arguments: list[str]) -> tuple[list[str], list[str]]:
    """Return the words before the first `--`, and the words after it."""
    if "--" not in arguments:
        return arguments, []
    index = arguments.index("--")
    return arguments[:index], arguments[index + 1 :]


_VALUE_OPTIONS: typ.Final = frozenset({
    "--python",
    "-p",
    "--with",
    "--with-requirements",
    "--project",
    "--directory",
    "--env-file",
})


def _script_target(words: list[str]) -> str | None:
    """Return the script a command runs, or None when it runs something else.

    The script is the command word itself (`./run.sh`), or the first argument
    that is not an option of an interpreter (`python scripts/x.py`,
    `uv run --script scripts/x.py`, `bash x.sh`). A tool that merely names a
    script as an argument (`uv tool run ruff format x.py`, `python -m pytest
    test_x.py`) does not run it.

    Returns
    -------
    str | None
        The script's path as written, or None.
    """
    if not words:
        return None
    if words[0].endswith(_SCRIPT_SUFFIXES):
        return words[0]
    head = pathlib.PurePosixPath(words[0]).name
    if head not in _INTERPRETERS:
        return None
    arguments = words[1:]
    if head == "uv":
        arguments = arguments[1:] if arguments[:1] == ["run"] else None
    first = _first_operand(arguments) if arguments is not None else None
    return first if first and first.endswith(_SCRIPT_SUFFIXES) else None


def _first_operand(arguments: list[str]) -> str | None:
    """Return the first argument that is not an option or an option's value.

    `-m` runs a module, so what follows is not a script.

    Returns
    -------
    str | None
        The operand, or None when there is none or `-m` was given.
    """
    index = 0
    while index < len(arguments) and arguments[index].startswith("-"):
        if arguments[index] == "-m":
            return None
        index += 2 if arguments[index] in _VALUE_OPTIONS else 1
    return arguments[index] if index < len(arguments) else None


def script_run(
    words: list[str],
    source: str,
    directory: str | None,
    script_tools: typ.Callable[[str], set[str]],
) -> list[Run]:
    """Return the runs a script that builds lint lanes stands for.

    A command that runs a script (`python scripts/lint_lanes.py`, `./run.sh`)
    whose text names `cargo` and a tool (*script_tools* answers that, given the
    script's relative path) is an unresolved run of each tool it names: the audit
    does not read the script, so it cannot say what targets, features and flags
    its lanes use. The lane inputs passed as `NAME=value` words are carried so a
    finding can show what could not be read.

    Returns
    -------
    list[Run]
        One unresolved run per tool the script names, or none.
    """
    start = _command_index(words)
    # Only the command word loses Make's `@`, `-` and `+` prefixes; an option
    # such as `--script` keeps its dashes.
    target = _script_target(
        [words[start].lstrip("@-+"), *words[start + 1 :]] if start < len(words) else []
    )
    if target is None:
        return []
    path = posixpath.normpath(posixpath.join(directory or "", target))
    tools = sorted(script_tools(path))
    lanes = [
        f"{name}={value}"
        for name, value in _assignments(words, start).items()
        if "LANE" in name.upper()
    ]
    return [_script_fact(tool, source, path, lanes) for tool in tools]


def _script_fact(tool: str, source: str, path: str, lanes: list[str]) -> Run:
    """Return the unresolved run a lane-building script stands for."""
    return {
        "tool": tool,
        "source": source,
        "is_placed": False,
        "is_resolved": False,
        "why": f"the script {path} builds the lanes, and the audit does not read it",
        "manifest": None,
        "all_targets": False,
        "all_features": False,
        "no_default_features": False,
        "features": [],
        "workspace": False,
        "packages": [],
        "no_deps": False,
        "narrows": False,
        "excludes": [],
        "deny_warnings": None,
        "lanes": lanes,
    }
