"""Read what one Makefile test or build command builds, as a comparable signature.

A preparation warms a test pass only when both compile the same thing. Cargo
keys its fingerprints, and so its reuse, on the package selection, the feature
set, the target triple, the profile and the compiler flags, so two commands that
differ in any one of them fill different directories: rstest-bdd's
`--no-default-features` pass timed out although `--all-features` had been
prepared. This module reads one simple command into a :class:`Signature` and
says what the command does (`run` the tests, `compile` without running them, or
neither), and whether a run is filtered to named tests.

Nothing here guesses. An option the reader does not know, and a Make reference
the expansion left, are kept in the signature as opaque words, in order, so two
commands compare equal only when they carry the same opaque words, and a command
that carries any is reported as not fully read. Nextest's own selection options
(`-E`, `--profile`, `-j`) and the arguments after `--` change what runs, never
what builds, so they stay out of the signature.
"""

from __future__ import annotations

import dataclasses
import enum
import re
import typing as typ

from .interrogate_invocation import lex_segments
from .make_commands import command_start
from .makefile_expansion import UNRESOLVED_MARK

# A Make reference the expansion left becomes this stand-in plus its number, so
# two commands that carry the same reference text compare equal while a
# different one does not. The lexer sees one ordinary word.
_REFERENCE_TAG: typ.Final = "\x03"
# Whitespace, quotes and shell operators inside a reference, made inert.
_WORD_SAFE: typ.Final = str.maketrans({
    " ": "\x04",
    "\t": "\x04",
    "\n": "\x04",
    "'": "\x05",
    '"': "\x05",
    "|": "\x06",
    ";": "\x06",
    "&": "\x06",
    "<": "\x06",
    ">": "\x06",
    "(": "\x07",
    ")": "\x07",
    "{": "\x07",
    "}": "\x07",
    "$": "\x08",
    "\\": "\x08",
    "#": "\x08",
})
_ASSIGNMENT: typ.Final = re.compile(
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>.*)", re.DOTALL
)

# Environment variables that decide which directory cargo builds into or which
# compiler flags it builds with. Any other variable leaves the build as it was.
_BUILD_ENVIRONMENT: typ.Final = frozenset({
    "RUSTFLAGS",
    "CARGO_ENCODED_RUSTFLAGS",
    "CARGO_BUILD_RUSTFLAGS",
    "CARGO_TARGET_DIR",
    "CARGO_BUILD_TARGET_DIR",
    "CARGO_BUILD_TARGET",
    "RUSTUP_TOOLCHAIN",
})
_BUILD_ENVIRONMENT_PREFIXES: typ.Final = ("CARGO_PROFILE_",)

# Shell words that introduce a command without being one.
_KEYWORDS: typ.Final = frozenset({
    "if",
    "then",
    "else",
    "elif",
    "do",
    "while",
    "until",
    "!",
    "{",
    "time",
})
_PACKAGE: typ.Final = frozenset({"-p", "--package"})
_EXCLUDE: typ.Final = frozenset({"--exclude"})
_FEATURES: typ.Final = frozenset({"--features", "-F"})
_WHOLE_WORKSPACE: typ.Final = frozenset({"--workspace", "--all"})
_ALL_FEATURES: typ.Final = "--all-features"
_NO_DEFAULT_FEATURES: typ.Final = "--no-default-features"
_RELEASE: typ.Final = frozenset({"--release", "-r"})
_TARGET: typ.Final = "--target"
_MANIFEST: typ.Final = "--manifest-path"
_PROFILE: typ.Final = "--profile"
_CARGO_PROFILE: typ.Final = "--cargo-profile"
# Options that select which test targets run, not what builds: a run that
# carries one is a filtered run.
_TARGET_SELECTORS: typ.Final = frozenset({
    "--test",
    "--bin",
    "--example",
    "--bench",
    "--lib",
    "--doc",
    "-E",
    "--filterset",
})
# Value-taking options that change nothing the signature records. The reader
# must still step over their values, or it would read a value as a filter.
_IGNORED_WITH_VALUE: typ.Final = frozenset({
    "-j",
    "--jobs",
    "--build-jobs",
    "--target-dir",
    "--color",
    "--message-format",
    "--cargo-message-format",
    "--cargo-metadata",
    "--test-threads",
    "--threads",
    "-T",
    "--retries",
    "--partition",
    "--run-ignored",
    "--archive-file",
    "--extract-to",
    "--workspace-remap",
    "--max-fail",
    "--failure-output",
    "--success-output",
    "--status-level",
    "--final-status-level",
    "--user-config-file",
    "--lockfile-path",
    "-P",
    "--config",
    "-Z",
})
# Flags that change nothing the signature records.
_IGNORED_FLAGS: typ.Final = frozenset({
    "--locked",
    "--frozen",
    "--offline",
    "--quiet",
    "-q",
    "--verbose",
    "-v",
    "-vv",
    "--no-fail-fast",
    "--fail-fast",
    "--no-capture",
    "--nocapture",
    "--ignore-rust-version",
    "--timings",
    "--keep-going",
    "--all-targets",
    "--tests",
    "--bins",
    "--examples",
    "--benches",
    "--no-tests",
    "--hide-progress-bar",
    "--no-output-indent",
    "--show-progress",
    "--no-run",
    "--help",
    "-h",
})
# Cargo's own global options, in front of the subcommand.
_CARGO_GLOBALS: typ.Final = frozenset({
    "--locked",
    "--frozen",
    "--offline",
    "--quiet",
    "-q",
    "--verbose",
    "-v",
    "--color",
    "-C",
    "--config",
    "-Z",
})
_VALUE_GLOBALS: typ.Final = frozenset({"--color", "-C", "--config", "-Z"})
_NEXTEST_RUN: typ.Final = frozenset({"run", "r", "ntr"})
_BUILD_SUBCOMMANDS: typ.Final = frozenset({"build", "b", "bench"})


class Action(enum.Enum):
    """What a command does with the tests it builds."""

    RUN = "run"
    COMPILE = "compile"


@dataclasses.dataclass(frozen=True, slots=True)
class Signature:
    """Hold what decides whether two commands build the same artefacts.

    Attributes
    ----------
    packages:
        The `-p` selections, sorted, with `--workspace` and `--exclude` marked.
    features:
        The feature names, sorted, with `--all-features` and
        `--no-default-features` marked.
    target:
        The `--target` triple, or None.
    profile:
        The cargo profile (`--release`, or the profile named), or None.
    manifest:
        The `--manifest-path`, or None.
    environment:
        The build-affecting environment assignments, by name, last write wins.
    opaque:
        Words the reader could not interpret (an option it does not know, a Make
        reference the expansion left), in order.
    """

    packages: tuple[str, ...] = ()
    features: tuple[str, ...] = ()
    target: str | None = None
    profile: str | None = None
    manifest: str | None = None
    environment: tuple[tuple[str, str], ...] = ()
    opaque: tuple[str, ...] = ()

    @property
    def is_resolved(self) -> bool:
        """Whether the reader understood every word of the command."""
        return not self.opaque


@dataclasses.dataclass(frozen=True, slots=True)
class Invocation:
    """Hold one test or build command the reader understood.

    Attributes
    ----------
    action:
        `RUN` for a command that runs tests, `COMPILE` for one that only builds.
    signature:
        What the command builds.
    is_filtered:
        Whether a run is restricted to named tests or a named test target.
    """

    action: Action
    signature: Signature
    is_filtered: bool


def _reference_end(text: str, start: int) -> int | None:
    """Return the index past the Make reference opening at *start*, or None.

    Make counts only its own brackets, whatever quotes sit between them, so a
    `$(shell cut -d' ' -f1)` closes where the parentheses balance.

    Returns
    -------
    int | None
        The index after the closing bracket; None when it never closes.
    """
    opener = text[start + 1]
    closer = ")" if opener == "(" else "}"
    depth = 0
    for index in range(start + 1, len(text)):
        if text[index] == opener:
            depth += 1
        elif text[index] == closer:
            depth -= 1
            if depth == 0:
                return index + 1
    return None


def tag_references(text: str) -> str:
    """Replace each balanced Make reference by a numbered stand-in word.

    Returns
    -------
    str
        The text with every `$(...)` and `${...}` reference reduced to one word
        that carries the reference's own text, so equal references stay equal.
    """
    out: list[str] = []
    index = 0
    while index < len(text):
        end = (
            _reference_end(text, index)
            if text.startswith(("$(", "${"), index)
            else None
        )
        if end is None:
            out.append(text[index])
            index += 1
            continue
        # The reference text is kept so two identical references compare equal.
        # Characters that would split or quote a word become marks no flag
        # contains, so the reference stays one word whatever it holds.
        body = text[index:end].translate(_WORD_SAFE)
        out.append(_REFERENCE_TAG + body + _REFERENCE_TAG)
        index = end
    return "".join(out)


def _is_opaque(word: str) -> bool:
    """Return whether *word* still carries a reference the expansion left."""
    return _REFERENCE_TAG in word or UNRESOLVED_MARK in word


def _lexed(text: str) -> tuple[list[str], list[str]] | None:
    """Return the assignment words and the command words of one command.

    Returns
    -------
    tuple[list[str], list[str]] | None
        The `NAME=value` words before the command word (including those an
        `env` prefix carries), and the words from the command word on; None
        when the text does not lex or holds no command.
    """
    segments = lex_segments(tag_references(text.replace("\\\n", " ")))
    if not segments or not segments[0]:
        return None
    words = segments[0]
    # A line such as `if command -v cargo-nextest; then RUSTFLAGS=.. cargo ..`
    # leads with the keyword that introduces its command.
    while words and words[0].lstrip("@-+") in _KEYWORDS:
        words = words[1:]
    start = command_start(words)
    if start >= len(words):
        return None
    assignments = [word for word in words[:start] if _ASSIGNMENT.match(word)]
    return assignments, [
        word.lstrip("@-+") if i == 0 else word for i, word in enumerate(words[start:])
    ]


def _environment(assignments: list[str]) -> tuple[tuple[str, str], ...]:
    """Return the build-affecting assignments, the last write of each name."""
    found: dict[str, str] = {}
    for word in assignments:
        match = _ASSIGNMENT.match(word)
        if match is None:
            continue
        name = match["name"]
        if name in _BUILD_ENVIRONMENT or name.startswith(_BUILD_ENVIRONMENT_PREFIXES):
            # Compared as words in order: spacing is not a difference, order may be.
            found[name] = " ".join(match["value"].split())
    return tuple(sorted(found.items()))


def _subcommand(words: list[str]) -> tuple[str, list[str], str] | None:
    """Return the runner, its subcommand and the words after it.

    Returns
    -------
    tuple[str, list[str], str] | None
        `cargo` or `nextest` as the runner, the arguments after the subcommand,
        and the subcommand; None for any other command.
    """
    name = words[0].rsplit("/", 1)[-1]
    rest = words[1:]
    if name == "cargo-nextest":
        rest = rest[1:] if rest[:1] == ["nextest"] else rest
        return ("nextest", rest[1:], rest[0]) if rest else None
    if name != "cargo":
        return None
    index = 0
    while index < len(rest) and (
        rest[index].startswith("+") or rest[index] in _CARGO_GLOBALS
    ):
        index += 2 if rest[index] in _VALUE_GLOBALS else 1
    if index >= len(rest):
        return None
    subcommand = rest[index]
    if subcommand == "nextest":
        tail = rest[index + 1 :]
        return ("nextest", tail[1:], tail[0]) if tail else None
    if subcommand == "ntr":
        return ("nextest", rest[index + 1 :], "ntr")
    return ("cargo", rest[index + 1 :], subcommand)


class _Reading:
    """Accumulate the options of one command into a signature."""

    def __init__(self, runner: str) -> None:
        """Start an empty reading for a cargo or nextest command."""
        self.runner = runner
        self.packages: list[str] = []
        self.features: list[str] = []
        self.target: str | None = None
        self.profile: str | None = None
        self.manifest: str | None = None
        self.opaque: list[str] = []
        self.has_no_run = False
        self.is_filtered = False

    def signature(self, assignments: list[str]) -> Signature:
        """Return the signature the options and assignments describe."""
        environment = _environment(assignments)
        opaque = list(self.opaque)
        opaque.extend(value for _name, value in environment if _is_opaque(value))
        return Signature(
            packages=tuple(sorted(self.packages)),
            features=tuple(sorted(self.features)),
            target=self.target,
            profile=self.profile,
            manifest=self.manifest,
            environment=environment,
            opaque=tuple(opaque),
        )

    def read(self, arguments: list[str]) -> None:
        """Read every option of the command, up to a `--`."""
        index = 0
        while index < len(arguments):
            word = arguments[index]
            if word == "--":
                # What follows is the test binary's own arguments; a name there
                # is still a filter on which tests run.
                self.is_filtered = self.is_filtered or any(
                    not w.startswith("-") for w in arguments[index + 1 :]
                )
                return
            index += self._read_one(arguments, index)

    def _read_one(self, arguments: list[str], index: int) -> int:
        """Read the option at *index* and return how many words it used."""
        word = arguments[index]
        if _is_opaque(word):
            self.opaque.append(word)
            return 1
        if not word.startswith("-"):
            # A bare word is a test-name filter.
            self.is_filtered = True
            return 1
        name, has_inline, inline = word.partition("=")
        value = inline if has_inline else None
        takes_value = self._takes_value(name)
        if takes_value and value is None and index + 1 < len(arguments):
            value = arguments[index + 1]
            used = 2
        else:
            used = 1
        self._record(name, value, word)
        return used

    def _takes_value(self, name: str) -> bool:
        """Return whether *name* consumes the word after it."""
        return (
            name in _PACKAGE
            or name in _EXCLUDE
            or name in _FEATURES
            or name in {_TARGET, _MANIFEST, _CARGO_PROFILE}
            or (name == _PROFILE)
            or name in _TARGET_SELECTORS - {"--lib", "--doc"}
            or name in _IGNORED_WITH_VALUE
        )

    def _record(self, name: str, value: str | None, word: str) -> None:
        """Store one option in the field it belongs to."""
        if value is not None and _is_opaque(value):
            self.opaque.append(word)
        elif name in _PACKAGE and value is not None:
            self.packages.append(value)
        elif name in _EXCLUDE and value is not None:
            self.packages.append(f"!{value}")
        elif name in _WHOLE_WORKSPACE:
            self.packages.append("--workspace")
        elif name in _FEATURES and value is not None:
            self.features.extend(part for part in re.split(r"[,\s]+", value) if part)
        elif name in {_ALL_FEATURES, _NO_DEFAULT_FEATURES}:
            self.features.append(name)
        elif name == _TARGET and value is not None:
            self.target = value
        elif name == _MANIFEST and value is not None:
            self.manifest = value
        elif name in _RELEASE:
            self.profile = "release"
        elif name == _CARGO_PROFILE or (name == _PROFILE and self.runner == "cargo"):
            self.profile = value
        elif name in _TARGET_SELECTORS:
            self.is_filtered = True
        elif name == "--no-run":
            self.has_no_run = True
        elif name in _IGNORED_FLAGS or name in _IGNORED_WITH_VALUE or name == _PROFILE:
            return
        else:
            self.opaque.append(word)


def read_invocation(text: str) -> Invocation | None:
    """Read one simple command into the test or build it performs.

    Parameters
    ----------
    text:
        One simple command, after Make's own expansion.

    Returns
    -------
    Invocation | None
        What the command runs or compiles, or None when it is neither a cargo
        test or build command nor a nextest run or archive.
    """
    lexed = _lexed(text)
    if lexed is None:
        return None
    assignments, words = lexed
    found = _subcommand(words)
    if found is None:
        return None
    runner, arguments, subcommand = found
    action = _action(runner, subcommand)
    if action is None:
        return None
    reading = _Reading(runner)
    reading.read(arguments)
    if reading.has_no_run:
        action = Action.COMPILE
    return Invocation(
        action=action,
        signature=reading.signature(assignments),
        is_filtered=reading.is_filtered and action is Action.RUN,
    )


def has_unresolved_command_word(text: str) -> bool:
    """Return whether the command word is a Make reference the expansion left.

    Such a command may be a test runner, so a pass that hides behind it cannot
    be judged and the caller reports it as indeterminate.

    Returns
    -------
    bool
        True when the word that names the command carries a reference.
    """
    lexed = _lexed(text)
    return lexed is not None and _is_opaque(lexed[1][0])


def _action(runner: str, subcommand: str) -> Action | None:
    """Return whether the subcommand runs tests or compiles, None for neither.

    A Make reference in the subcommand position (`cargo $(TEST_CMD) ...`, where
    `TEST_CMD` is `nextest run` or `test` by a probe of the machine) is the
    runner the repository picked. Either builds the same artefacts, so the
    command is read as a test run and its options decide the signature.

    Returns
    -------
    Action | None
        What the command does, or None when it is neither a test nor a build.
    """
    if runner == "nextest":
        if subcommand in _NEXTEST_RUN:
            return Action.RUN
        return Action.COMPILE if subcommand == "archive" else None
    if subcommand == "test" or _is_opaque(subcommand):
        return Action.RUN
    if subcommand in _BUILD_SUBCOMMANDS:
        return Action.COMPILE
    return None
