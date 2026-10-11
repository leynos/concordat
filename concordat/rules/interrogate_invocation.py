"""Read `interrogate` invocations out of expanded Make recipe text.

The documentation-presence envelope needs to know, for each recipe a `make lint`
reaches, whether it runs `interrogate` and with what: which paths, threshold,
verbose level, relaxing flags and exclusions, and whether the recipe guards
against `MISSED` rows. The recipe arrives already expanded by
`makefile_expansion`, so this module is only a shell-aware reader: it lexes the
text into simple commands, finds the one that runs the tool (as opposed to
printing its name or installing it), and reads its options from the table in
`interrogate --help` at 1.7.0.

A command it cannot read is carried as an unresolved fact, not guessed at: the
policy reports an indeterminate verdict for it.
"""

from __future__ import annotations

import collections
import dataclasses
import posixpath
import re
import shlex
import typing as typ

from .makefile_expansion import SHELL_DOLLAR, UNRESOLVED_MARK, Expansion, shown

# Short options that take a value, and the long option each stands for.
VALUE_SHORT: typ.Final = frozenset("ferwog")
LONG_FOR_SHORT: typ.Final = {
    "f": "--fail-under",
    "e": "--exclude",
    "r": "--ignore-regex",
}
VALUE_LONG: typ.Final = frozenset({
    "--fail-under",
    "--exclude",
    "--ignore-regex",
    "--whitelist-regex",
    "--style",
    "--output",
    "--generate-badge",
    "--badge-format",
    "--badge-style",
    "--config",
    "--ext",
})
RELAXING_SHORT: typ.Final = {
    "i": "--ignore-init-method",
    "I": "--ignore-init-module",
    "m": "--ignore-magic",
    "M": "--ignore-module",
    "n": "--ignore-nested-functions",
    "C": "--ignore-nested-classes",
    "p": "--ignore-private",
    "P": "--ignore-property-decorators",
    "S": "--ignore-setters",
    "s": "--ignore-semiprivate",
}
RELAXING_LONG: typ.Final = frozenset(RELAXING_SHORT.values())
# Ignoring `@typing.overload` stubs (`-O`) is not a relaxation: Ruff forbids a
# docstring on them. It is absent from the tables above, so it is never reported.
SEGMENT_BREAKS: typ.Final = frozenset({";", "&&", "||", "|", "&", "(", ")", ";;"})
# A command that only prints text mentioning interrogate, or calls make, is not
# an invocation; a recursive make is followed by the reachability walk instead.
NON_EXECUTING: typ.Final = frozenset({"echo", "printf", "make"})
# The commands that launch a tool by name. `interrogate` is run, not merely
# named, when it is the first word or follows only a launcher's own words: its
# subcommands (`run`, `tool run`), its flags and their values (`uv run --with
# interrogate interrogate` names the package twice and runs it once).
LAUNCHERS: typ.Final = frozenset({"uv", "uvx", "python", "python3", "pipx"})
LAUNCHER_SUBCOMMANDS: typ.Final = frozenset({"run", "tool"})
LAUNCHER_VALUE_FLAGS: typ.Final = frozenset({
    "--with",
    "--from",
    "--python",
    "-p",
    "--group",
    "--extra",
    "--project",
    "--directory",
})

_ASSIGNMENT: typ.Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
# Make removes these from the front of a recipe line before the shell runs it.
MAKE_PREFIXES: typ.Final = "@-+"
# Stands before a leading `NAME=value` word the shell saw quoted: a command name.
QUOTED_MARK: typ.Final = "\x02"
_NAMES_TOOL: typ.Final = re.compile(r"(?<![\w-])interrogate(?![\w-])")
_UNRESOLVED_TOOL: typ.Final = re.compile(
    r"\$[({][^)}\s]*interrogate[^)}\s]*[)}]", re.IGNORECASE
)
_SHORT_OPTION: typ.Final = re.compile(r"-[^-]")
_REDIRECT_OPERATOR: typ.Final = re.compile(r"\d*(?:>>?|<)")
_REDIRECT_ATTACHED: typ.Final = re.compile(r"\d*[<>]")
_QUOTED: typ.Final = re.compile(r"'[^']*'|\"[^\"]*\"")
_STATEMENT: typ.Final = r"[^;&|\n]*"
# A check that turns a MISSED row into a failing exit status, in the three
# shapes a recipe uses: a negated pipeline, `if ...; then ...; exit N`, and
# `... && exit N`. A grep whose result is ignored (`|| true`, a bare grep) is
# none of them, so it does not count as a guard.
_GUARD: typ.Final = re.compile(
    rf"!{_STATEMENT}\bgrep\b{_STATEMENT}MISSED"
    rf"|\bgrep\b{_STATEMENT}MISSED{_STATEMENT};\s*then\b(?:(?!\bfi\b).)*?\bexit\s+[1-9]"
    rf"|\bgrep\b{_STATEMENT}MISSED{_STATEMENT}&&\s*exit\s+[1-9]",
    re.DOTALL,
)


class InterrogateRun(typ.TypedDict):
    """One `interrogate` invocation inside a recipe `lint` reaches."""

    target: str
    line: int
    command: str
    resolved: bool
    paths: list[str]
    fail_under: str | None
    verbose: int
    config: str | None
    excludes: list[str]
    ignores: list[str]
    guarded: bool


@dataclasses.dataclass(frozen=True, slots=True)
class RecipeLine:
    """One recipe line of a reachable target, raw and expanded."""

    target: str
    number: int
    raw: str
    expansion: Expansion
    is_guarded: bool


def has_missed_guard(text: str) -> bool:
    """Return whether *text* fails on a `grep` match for `MISSED`.

    Quoted strings are neutralised first, so the `|` in `'| *MISSED |'` does not
    read as a pipeline.

    Returns
    -------
    bool
        Whether the recipe text turns a `MISSED` row into a failing status.
    """
    neutral = _QUOTED.sub(lambda m: re.sub(r"[;&|]", "_", m.group(0)), text)
    return _GUARD.search(neutral) is not None


def _tokens(text: str, *, is_posix: bool) -> list[str] | None:
    """Lex *text* into words (quotes removed when POSIX) and operators, or ``None``."""
    lexer = shlex.shlex(text, posix=is_posix, punctuation_chars=";&|()")
    lexer.whitespace_split = True
    try:
        return [word.replace(SHELL_DOLLAR, "$") for word in lexer]
    except ValueError:
        return None


def _split(words: list[str]) -> list[list[str]]:
    """Group *words* into simple commands at every shell separator."""
    segments: list[list[str]] = [[]]
    for word in words:
        if word in SEGMENT_BREAKS:
            segments.append([])
        else:
            segments[-1].append(word)
    return [segment for segment in segments if segment]


def _mark_quoted_assignment(words: list[str], raw: list[str]) -> list[str]:
    """Mark a quoted leading `NAME=value` word, which the shell runs as a command.

    `"X=1" interrogate` tries to run a command named `X=1`; only an unquoted
    assignment is an environment prefix. The POSIX lexer drops the quotes, so
    the non-POSIX tokens say which leading word was quoted.

    Returns
    -------
    list[str]
        *words*, with the first quoted leading assignment carrying QUOTED_MARK.
    """
    marked = list(words)
    for index, (word, original) in enumerate(zip(words, raw, strict=True)):
        if not _ASSIGNMENT.match(word.lstrip(MAKE_PREFIXES)):
            break
        if original.lstrip(MAKE_PREFIXES)[:1] in {"'", '"'}:
            marked[index] = QUOTED_MARK + word
            break
    return marked


def lex_segments(command: str) -> list[list[str]] | None:
    """Split a shell command into simple commands, or ``None`` if it won't lex.

    Command substitution is rewritten to a parenthesised group first, so an
    invocation inside `"$(...)"` is a command of its own and not a fragment of
    one quoted word. A Make reference the expansion left becomes a word of its
    own, the unresolved mark.

    Returns
    -------
    list[list[str]] | None
        The words of each simple command, or ``None`` when the lexer refuses.
    """
    flattened = re.sub(rf'"?{SHELL_DOLLAR}\(', " ( ", command)
    flattened = re.sub(r'\)"', " ) ", flattened)
    flattened = re.sub(r"\$([({])", rf"{UNRESOLVED_MARK} \1", flattened)
    words = _tokens(flattened, is_posix=True)
    if words is None:
        return None
    segments = _split(words)
    raw = _tokens(flattened, is_posix=False)
    raw_segments = _split(raw) if raw is not None else []
    if len(raw_segments) != len(segments):
        return segments
    return [
        _mark_quoted_assignment(group, original)
        if len(group) == len(original)
        else group
        for group, original in zip(segments, raw_segments, strict=True)
    ]


@dataclasses.dataclass(slots=True)
class Options:
    """What an invocation's options say."""

    paths: list[str] = dataclasses.field(default_factory=list)
    fail_under: str | None = None
    verbose: int = 0
    config: str | None = None
    excludes: list[str] = dataclasses.field(default_factory=list)
    ignores: list[str] = dataclasses.field(default_factory=list)


def _record_long(options: Options, flag: str, value: str) -> None:
    """Record one long option, whether it is read, counted or a relaxing flag."""
    match flag:
        case "--fail-under":
            options.fail_under = value
        case "--verbose":
            options.verbose += 1
        case "--config":
            options.config = posixpath.normpath(value)
        case "--exclude":
            options.excludes.append(value)
        case "--ignore-regex":
            options.ignores.append(f"--ignore-regex {value}")
        case _ if flag in RELAXING_LONG:
            options.ignores.append(flag)


def _consume_long(options: Options, word: str, queue: collections.deque[str]) -> None:
    """Read `--flag`, `--flag=value` or `--flag value`."""
    flag, has_value, value = word.partition("=")
    if flag in VALUE_LONG:
        value = value if has_value else (queue.popleft() if queue else "")
    _record_long(options, flag, value)


def _consume_short(options: Options, word: str, queue: collections.deque[str]) -> None:
    """Read a short cluster such as `-vv`, `-nC`, `-f100` or `-f 100`."""
    letters = word[1:]
    for position, letter in enumerate(letters):
        if letter in VALUE_SHORT:
            attached = letters[position + 1 :]
            value = attached or (queue.popleft() if queue else "")
            _record_long(options, LONG_FOR_SHORT.get(letter, f"-{letter}"), value)
            return
        if letter == "v":
            options.verbose += 1
        elif letter in RELAXING_SHORT:
            options.ignores.append(RELAXING_SHORT[letter])


def _consume(options: Options, word: str, queue: collections.deque[str]) -> None:
    """Read one word: an option, a redirection or a path."""
    if word.startswith("--"):
        _consume_long(options, word, queue)
    elif _SHORT_OPTION.match(word):
        _consume_short(options, word, queue)
    elif _REDIRECT_OPERATOR.fullmatch(word):
        # The destination is a file the shell opens, not a path to measure.
        if queue:
            queue.popleft()
    elif not _REDIRECT_ATTACHED.match(word):
        options.paths.append(posixpath.normpath(word))


def scan_options(arguments: list[str]) -> Options:
    """Read the options and paths that follow the `interrogate` word.

    Returns
    -------
    Options
        What the words say.
    """
    options = Options()
    queue = collections.deque(arguments)
    while queue:
        _consume(options, queue.popleft(), queue)
    return options


def _is_launcher_prefix(prefix: list[str]) -> bool:
    """Return whether *prefix* is only a launcher's subcommands, flags and values.

    A flag that takes a value consumes the next word, so a word that is such a
    flag's value (`--with interrogate`) is not a command.

    Returns
    -------
    bool
        Whether the words before the tool are all the launcher's own.
    """
    if posixpath.basename(prefix[0]) not in LAUNCHERS:
        return False
    awaiting_value = False
    for word in prefix[1:]:
        if awaiting_value:
            awaiting_value = False
        elif word in LAUNCHER_VALUE_FLAGS:
            awaiting_value = True
        elif not (word.startswith("-") or word in LAUNCHER_SUBCOMMANDS):
            return False
    return not awaiting_value


def _is_tool_word(words: list[str], index: int) -> bool:
    """Return whether ``words[index]`` runs interrogate rather than names it."""
    if posixpath.basename(words[index]) != "interrogate":
        return False
    return index == 0 or _is_launcher_prefix(words[:index])


def _build_run(line: RecipeLine, words: list[str]) -> InterrogateRun:
    """Return the run for *words*, which start at the `interrogate` word."""
    options = scan_options(words[1:])
    return {
        "target": line.target,
        "line": line.number,
        "command": shown(" ".join(words)),
        "resolved": UNRESOLVED_MARK not in words,
        "paths": options.paths or ["."],
        "fail_under": options.fail_under,
        "verbose": options.verbose,
        "config": options.config,
        "excludes": options.excludes,
        "ignores": options.ignores,
        "guarded": line.is_guarded,
    }


def unresolved_run(line: RecipeLine, text: str) -> InterrogateRun:
    """Return a placeholder fact for a recipe that names the tool unreadably."""
    return {
        "target": line.target,
        "line": line.number,
        "command": shown(text),
        "resolved": False,
        "paths": ["."],
        "fail_under": None,
        "verbose": 0,
        "config": None,
        "excludes": [],
        "ignores": [],
        "guarded": line.is_guarded,
    }


# `env` options that take the next word as their value.
ENV_VALUE_OPTIONS: typ.Final = frozenset({
    "-u",
    "--unset",
    "-C",
    "--chdir",
    "-S",
    "--split-string",
})


def _skip_env(words: list[str], start: int) -> int:
    """Return the index after an `env` command's options, from just past `env`."""
    index = start
    while index < len(words) and words[index].startswith("-"):
        option = words[index]
        index += 2 if option in ENV_VALUE_OPTIONS else 1
        if option == "--":
            break
    return index


def command_words(words: list[str]) -> list[str]:
    """Return a simple command's words without its leading environment prefix.

    `UV_CACHE_DIR=.uv-cache uv tool run ...` and `env -i X=1 uv run ...` run the
    command after the assignments and `env` options, so those words do not
    decide what it is. A Make prefix (`@`, `-`, `+`) is not part of the word.

    Returns
    -------
    list[str]
        The words from the command itself, or an empty list when none remain.
    """
    start = 0
    while start < len(words):
        word = words[start].lstrip(MAKE_PREFIXES)
        if _ASSIGNMENT.match(word):
            start += 1
        elif word == "env":
            start = _skip_env(words, start + 1)
        else:
            break
    return words[start:]


def _run_in_segment(line: RecipeLine, raw_words: list[str]) -> InterrogateRun | None:
    """Return the run a simple command makes, or ``None`` if it runs no tool."""
    words = command_words(raw_words)
    if not words or words[0].lstrip("@-+") in NON_EXECUTING:
        return None
    for index in range(len(words)):
        if _is_tool_word(words, index):
            return _build_run(line, words[index:])
    return None


def _unresolved_tool_runs(line: RecipeLine) -> list[InterrogateRun]:
    """Return an unresolved run when `$(INTERROGATE)` expanded to nothing readable.

    A variable that expanded to something unreadable leaves no `interrogate`
    word to find, yet it is an invocation; losing it would read as no gate.

    Returns
    -------
    list[InterrogateRun]
        One unresolved run, or nothing when the line does not name the tool.
    """
    if not line.expansion.is_resolved and _UNRESOLVED_TOOL.search(line.raw):
        return [unresolved_run(line, line.raw)]
    return []


def runs_in(line: RecipeLine) -> list[InterrogateRun]:
    """Return every `interrogate` invocation in one expanded recipe line.

    Returns
    -------
    list[InterrogateRun]
        The invocations, or one unresolved fact when the line cannot be read
        but names interrogate.
    """
    text = line.expansion.text
    if not _NAMES_TOOL.search(text):
        return _unresolved_tool_runs(line)
    segments = lex_segments(text)
    if segments is None:
        return [unresolved_run(line, text)]
    runs = (_run_in_segment(line, words) for words in segments)
    return [run for run in runs if run is not None]
