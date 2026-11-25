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
what builds, so they stay out of the signature. The options that change where or
how cargo builds (`--target-dir`, `--config`, `-Z`, a `+toolchain`) stay in it.
"""

from __future__ import annotations

import dataclasses
import enum
import re
import typing as typ

from .nested_cargo_pass_words import (
    ASSIGNMENT,
    Command,
    is_opaque,
    lexed,
    read_command,
    word_at,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

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

_PACKAGE: typ.Final = frozenset({"-p", "--package"})
_FEATURES: typ.Final = frozenset({"--features", "-F"})
_WHOLE_WORKSPACE: typ.Final = frozenset({"--workspace", "--all"})
_RELEASE: typ.Final = frozenset({"--release", "-r"})
_FEATURE_FLAGS: typ.Final = frozenset({"--all-features", "--no-default-features"})
_BUILD_OPTIONS: typ.Final = frozenset({"--target-dir", "--config", "-Z"})
_HELP: typ.Final = frozenset({"--help", "-h"})
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
# Selectors that pick unit-test or documentation targets. The nested builds a
# trybuild or macrotest harness runs live in an integration test, which these
# never run, so a run that selects only these leaves them cold.
_UNIT_TARGETS: typ.Final = frozenset({
    "--lib",
    "--doc",
    "--bin",
    "--example",
    "--bench",
})
# Value-taking options that change nothing the signature records. The reader
# must still step over their values, or it would read a value as a filter.
_IGNORED_WITH_VALUE: typ.Final = frozenset({
    "-j",
    "--jobs",
    "--build-jobs",
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
})
_TAKES_VALUE: typ.Final = (
    _PACKAGE
    | _FEATURES
    | _BUILD_OPTIONS
    | _IGNORED_WITH_VALUE
    | (_TARGET_SELECTORS - {"--lib", "--doc"})
    | {"--exclude", "--target", "--manifest-path", "--cargo-profile", "--profile"}
)
# The options that are meaningless without their value.
_NEEDS_VALUE: typ.Final = (
    _PACKAGE
    | _FEATURES
    | _BUILD_OPTIONS
    | {"--exclude", "--target", "--manifest-path", "--cargo-profile"}
)
_NEXTEST_RUN: typ.Final = frozenset({"run", "r", "ntr"})
_BUILD_SUBCOMMANDS: typ.Final = frozenset({"build", "b", "bench"})
# libtest options whose next word is their value, not a test name.
_LIBTEST_VALUE_OPTIONS: typ.Final = frozenset({
    "--test-threads",
    "--skip",
    "--format",
    "--color",
    "--logfile",
    "--shuffle-seed",
    "--report-time",
    "--ensure-time",
    "-Z",
})


type _Handler = cabc.Callable[[str, str | None], None]


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
    options:
        The options that change where or how cargo builds (`--target-dir`,
        `--config`, `-Z`, `-C`, a `+toolchain`), each with its value, in order.
    """

    packages: tuple[str, ...] = ()
    features: tuple[str, ...] = ()
    target: str | None = None
    profile: str | None = None
    manifest: str | None = None
    environment: tuple[tuple[str, str], ...] = ()
    opaque: tuple[str, ...] = ()
    options: tuple[str, ...] = ()

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
    warms_nested:
        Whether a filtered run reaches the integration tests where nested builds
        live. A run that selects only `--lib`, `--doc`, `--bin`, `--example` or
        `--bench` compiles what it runs and leaves those builds cold.
    """

    action: Action
    signature: Signature
    is_filtered: bool
    warms_nested: bool = True


def _build_assignment(word: str) -> tuple[str, str] | None:
    """Return the name and value of a build-affecting `NAME=value` word."""
    match = ASSIGNMENT.match(word)
    if match is None:
        return None
    name = match["name"]
    if name in _BUILD_ENVIRONMENT or name.startswith(_BUILD_ENVIRONMENT_PREFIXES):
        # Compared as words in order: spacing is not a difference, order may be.
        return name, " ".join(match["value"].split())
    return None


def _environment(assignments: list[str]) -> tuple[tuple[str, str], ...]:
    """Return the build-affecting assignments, the last write of each name."""
    found = dict(filter(None, map(_build_assignment, assignments)))
    return tuple(sorted(found.items()))


def _has_test_name(words: list[str]) -> bool:
    """Return whether *words* (the harness arguments) hold a test-name filter."""
    index = 0
    while index < len(words):
        if words[index] in _LIBTEST_VALUE_OPTIONS:
            index += 2
        elif words[index].startswith("-"):
            index += 1
        else:
            return True
    return False


def _value_after(arguments: list[str], index: int) -> str | None:
    """Return the value the option at *index* takes from the next word, if any."""
    following = word_at(arguments, index + 1)
    return None if following == "--" else following


class _Reading:
    """Accumulate the options of one command into a signature."""

    def __init__(self, runner: str, options: list[str]) -> None:
        """Start a reading for a cargo or nextest command with its global options."""
        self.runner = runner
        self.packages: list[str] = []
        self.features: list[str] = []
        self.target: str | None = None
        self.profile: str | None = None
        self.manifest: str | None = None
        self.opaque: list[str] = []
        self.options = list(options)
        self.selectors: list[str] = []
        self.has_no_run = False
        self.is_help = False
        self.is_filtered = False
        self.handlers = self._handlers()

    def signature(self, assignments: list[str]) -> Signature:
        """Return the signature the options and assignments describe."""
        environment = _environment(assignments)
        opaque = list(self.opaque)
        opaque.extend(value for _name, value in environment if is_opaque(value))
        return Signature(
            packages=tuple(sorted(self.packages)),
            features=tuple(sorted(self.features)),
            target=self.target,
            profile=self.profile,
            manifest=self.manifest,
            environment=environment,
            opaque=tuple(opaque),
            options=tuple(self.options),
        )

    @property
    def warms_nested(self) -> bool:
        """Whether the run reaches integration tests, where nested builds live."""
        return not (self.selectors and set(self.selectors) <= _UNIT_TARGETS)

    def read(self, arguments: list[str]) -> None:
        """Read every option of the command, up to a `--`."""
        index = 0
        while index < len(arguments):
            if arguments[index] == "--":
                # What follows is the test binary's own arguments; a name there
                # is still a filter on which tests run.
                self.is_filtered |= _has_test_name(arguments[index + 1 :])
                return
            index += self._read_one(arguments, index)

    def _read_one(self, arguments: list[str], index: int) -> int:
        """Read the option at *index* and return how many words it used."""
        word = arguments[index]
        if is_opaque(word):
            self.opaque.append(word)
            return 1
        if not word.startswith("-"):
            # A bare word is a test-name filter.
            self.is_filtered = True
            return 1
        name, has_inline, inline = word.partition("=")
        value = inline if has_inline else None
        used = 1
        if value is None and name in _TAKES_VALUE:
            value = _value_after(arguments, index)
            used = 1 if value is None else 2
        self._record(name, value, word)
        return used

    def _record(self, name: str, value: str | None, word: str) -> None:
        """Store one option in the field it belongs to."""
        if value is not None and is_opaque(value):
            self.opaque.append(f"{name}={value}")
        elif value is None and name in _NEEDS_VALUE:
            self.opaque.append(word)
        elif name in self.handlers:
            self.handlers[name](name, value)
        elif name not in _IGNORED_FLAGS and name not in _IGNORED_WITH_VALUE:
            self.opaque.append(word)

    def _package(self, _name: str, value: str | None) -> None:
        """Select a package."""
        self.packages.append(str(value))

    def _exclude(self, _name: str, value: str | None) -> None:
        """Exclude a package from the workspace."""
        self.packages.append(f"!{value}")

    def _workspace(self, _name: str, _value: str | None) -> None:
        """Select the whole workspace."""
        self.packages.append("--workspace")

    def _feature_list(self, _name: str, value: str | None) -> None:
        """Add the features a `--features` list names."""
        self.features.extend(part for part in re.split(r"[,\s]+", str(value)) if part)

    def _feature_flag(self, name: str, _value: str | None) -> None:
        """Note `--all-features` or `--no-default-features`."""
        self.features.append(name)

    def _set_target(self, _name: str, value: str | None) -> None:
        """Set the target triple."""
        self.target = value

    def _set_manifest(self, _name: str, value: str | None) -> None:
        """Set the manifest path."""
        self.manifest = value

    def _release(self, _name: str, _value: str | None) -> None:
        """Select the release profile."""
        self.profile = "release"

    def _cargo_profile(self, _name: str, value: str | None) -> None:
        """Set the cargo profile."""
        self.profile = value

    def _profile(self, name: str, value: str | None) -> None:
        """Set the profile for cargo; nextest's own `--profile` is a config name."""
        if self.runner == "cargo":
            self._cargo_profile(name, value)

    def _selector(self, name: str, _value: str | None) -> None:
        """Restrict the run to a test target or a filter set."""
        self.is_filtered = True
        self.selectors.append(name)

    def _no_run(self, _name: str, _value: str | None) -> None:
        """Note that the command compiles without running."""
        self.has_no_run = True

    def _help(self, _name: str, _value: str | None) -> None:
        """Note that the command only prints help."""
        self.is_help = True

    def _build_option(self, name: str, value: str | None) -> None:
        """Keep an option that changes where or how cargo builds, with its value."""
        self.options.append(f"{name}={value}")

    def _handlers(self) -> dict[str, _Handler]:
        """Return the method that stores each option the reader knows."""
        groups: tuple[tuple[frozenset[str], _Handler], ...] = (
            (_PACKAGE, self._package),
            (frozenset({"--exclude"}), self._exclude),
            (_WHOLE_WORKSPACE, self._workspace),
            (_FEATURES, self._feature_list),
            (_FEATURE_FLAGS, self._feature_flag),
            (frozenset({"--target"}), self._set_target),
            (frozenset({"--manifest-path"}), self._set_manifest),
            (_RELEASE, self._release),
            (frozenset({"--cargo-profile"}), self._cargo_profile),
            (frozenset({"--profile"}), self._profile),
            (_TARGET_SELECTORS, self._selector),
            (frozenset({"--no-run"}), self._no_run),
            (_HELP, self._help),
            (_BUILD_OPTIONS, self._build_option),
        )
        return {name: method for names, method in groups for name in names}


def _action(command: Command) -> Action | None:
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
    subcommand = command.subcommand
    if command.runner == "nextest":
        return _nextest_action(subcommand)
    if subcommand == "test" or is_opaque(subcommand):
        return Action.RUN
    return Action.COMPILE if subcommand in _BUILD_SUBCOMMANDS else None


def _nextest_action(subcommand: str) -> Action | None:
    """Return what a nextest subcommand does with the tests it builds."""
    if subcommand in _NEXTEST_RUN:
        return Action.RUN
    return Action.COMPILE if subcommand == "archive" else None


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
        test or build command nor a nextest run or archive (a `--help` run
        builds nothing).
    """
    found = _command_of(text)
    if found is None:
        return None
    assignments, command, action = found
    reading = _Reading(command.runner, command.options)
    reading.read(command.arguments)
    if reading.is_help:
        return None
    if reading.has_no_run:
        action = Action.COMPILE
    return Invocation(
        action=action,
        signature=reading.signature(assignments),
        is_filtered=reading.is_filtered and action is Action.RUN,
        warms_nested=reading.warms_nested,
    )


def _command_of(text: str) -> tuple[list[str], Command, Action] | None:
    """Return the assignments, runner command and action of a test or build."""
    lines = lexed(text)
    if lines is None:
        return None
    command = read_command(lines[1])
    if command is None:
        return None
    action = _action(command)
    return None if action is None else (lines[0], command, action)
