"""Read where a cargo command builds, from the calls a test makes on it.

A nested build is cheap when it lands in a target directory something has
already filled, and expensive when it does not. Which directory a child cargo
uses is decided by the last write to the places cargo reads: `--target-dir`
beats `CARGO_TARGET_DIR`, which beats `CARGO_BUILD_TARGET_DIR` (cargo reads the
second as `build.target-dir`), which beats the workspace default. A removal or
an `env_clear` undoes an earlier assignment, and an operation the reader cannot
see through may have undone it too.

This module replays the calls :class:`nested_cargo_commands.CargoCommand`
recorded and answers with one of five states:

- `inherited`: nothing the command does picks a target, so it builds where its
  parent builds. Not a finding: whatever warmed the parent warms the child.
- `shared`: a target the reader can see is the workspace's or the parent's.
- `dedicated`: a fixed directory inside the crate (`<manifest>/target/<name>`).
  It persists between runs, so the second run is warm; the first is not, which
  is the preparation question rather than a private target.
- `private`: a directory no later run can reuse, because it derives from a
  temporary directory or from something unique to the run.
- `unknown`: a target is set, from a value the reader cannot follow.

The reader reports `private` only on evidence it can read in the same file, and
`unknown` rather than a guess when the value comes from elsewhere.
"""

from __future__ import annotations

import dataclasses
import re
import typing as typ

from .nested_cargo_bindings import STRING, expand, local_bindings
from .nested_cargo_commands import (
    ENVIRONMENT_CLEAR,
    ENVIRONMENT_METHODS,
    UNSEEN_ARGUMENTS,
    UNSEEN_CALL,
)
from .nested_cargo_text import argument_items, environment_slots

INHERITED: typ.Final = "inherited"
SHARED: typ.Final = "shared"
DEDICATED: typ.Final = "dedicated"
PRIVATE: typ.Final = "private"
UNKNOWN: typ.Final = "unknown"

TARGET_VARIABLES: typ.Final = ("CARGO_TARGET_DIR", "CARGO_BUILD_TARGET_DIR")
TARGET_FLAG: typ.Final = '"--target-dir"'
_SEPARATOR: typ.Final = '"--"'
# Options known to consume the argument after them. Any other option may be a
# switch (cargo has many: `--release`, `--timings`, `--locked`), and what
# follows a switch may itself be `--target-dir=...`.
_VALUE_OPTIONS: typ.Final = frozenset({
    "--manifest-path",
    "--package",
    "-p",
    "--exclude",
    "--bin",
    "--example",
    "--test",
    "--bench",
    "--features",
    "-F",
    "--profile",
    "--target",
    "--jobs",
    "-j",
    "--color",
    "--message-format",
    "--config",
    "-Z",
    "--lockfile-path",
    "--out-dir",
    "--build-dir",
    "--artifact-dir",
})
# Matches `"--manifest-path"` and `"--manifest-path=..."`.
_MANIFEST_PATH: typ.Final = '"--manifest-path'

# Constructors and accessors of a directory that is removed with its owner or
# named for one run: a later run can never find what an earlier one built.
_TEMPORARY: typ.Final = re.compile(
    r"\b(?:tempdir|tempdir_in|TempDir|mkdtemp|NamedTempFile|tempfile)\b"
)
# The system temporary directory itself is shared by every run, so a fixed path
# under it persists like any other: it is private only with a name unique to the
# run, which `_UNIQUE` reads.
_SYSTEM_TEMPORARY: typ.Final = re.compile(r"\btemp_dir\b")
_UNIQUE: typ.Final = re.compile(
    r"\b(?:Uuid|uuid|SystemTime|fastrand|rand)\b|\bprocess\s*::\s*id\b"
)
_MANIFEST: typ.Final = re.compile(r"CARGO_MANIFEST_DIR")
_JOINED_TARGET: typ.Final = re.compile(r'\.join\(\s*"(?P<path>[^"]*)"')
_SHARED_SOURCE: typ.Final = re.compile(
    r"CARGO_TARGET_DIR|target_directory|\bmetadata\b|\benv::var(?:_os)?\b"
    r"|\bvar_os?\b|CARGO_LLVM_COV_TARGET_DIR"
)
# `let x = std::env::current_exe()?;` names the test executable.
_TEST_EXECUTABLE: typ.Final = "current_exe"
_SUMMARY_LENGTH: typ.Final = 60


@dataclasses.dataclass(frozen=True, slots=True)
class TargetReading:
    """Store one answer to "where does this command build?".

    Attributes
    ----------
    state:
        One of `inherited`, `shared`, `dedicated`, `private` or `unknown`.
    source:
        A short description of the evidence, for the finding's message. Empty
        for `inherited`.
    is_inferred:
        True when no call set a target and the answer rests on what cargo
        would do by default (a temporary working directory with the inherited
        target removed). A command that is not cargo is read only when its
        target is set outright, because the inference says nothing about a
        program that may not build at all.
    """

    state: str
    source: str = ""
    is_inferred: bool = False


def _code(expression: str) -> str:
    """Return *expression* with every string literal blanked."""
    return STRING.sub('""', expression)


def _is_temporary(expression: str, bindings: dict[str, str]) -> bool:
    """Report whether *expression* visibly derives from a temporary directory."""
    return _TEMPORARY.search(_code(expand(expression, bindings))) is not None


def _summary(expression: str) -> str:
    """Return a one-line, bounded rendering of *expression* for a message."""
    flat = " ".join(expression.split())
    if len(flat) <= _SUMMARY_LENGTH:
        return flat
    return flat[: _SUMMARY_LENGTH - 1] + "…"


def _classify_joined(expanded: str) -> TargetReading | None:
    """Classify a path built from the crate's manifest directory, if it is one.

    Returns
    -------
    TargetReading | None
        `dedicated` for a `target/...` subdirectory of the crate and `shared`
        for a relative path that leaves it, or None when the value is not built
        from the manifest directory.
    """
    if not _MANIFEST.search(expanded):
        return None
    for joined in _JOINED_TARGET.finditer(expanded):
        path = joined["path"].replace("\\", "/")
        if path.startswith("target"):
            return TargetReading(DEDICATED, "a target directory inside the crate")
        if "target" in path:
            return TargetReading(SHARED, "the workspace target directory")
    return None


def classify_value(expression: str, bindings: dict[str, str]) -> TargetReading:
    """Classify the expression a target directory is set from.

    Returns
    -------
    TargetReading
        `private` for a value visibly derived from a temporary directory or
        unique to the run, `dedicated` or `shared` for a path the reader can
        place, and `unknown` for anything else.
    """
    expanded = expand(expression, bindings)
    code = _code(expanded)
    if _TEMPORARY.search(code):
        return TargetReading(PRIVATE, "a temporary directory")
    if _UNIQUE.search(code):
        return TargetReading(PRIVATE, "a name unique to the run")
    if _SYSTEM_TEMPORARY.search(code):
        return TargetReading(
            DEDICATED, "a fixed path under the system temporary directory"
        )
    joined = _classify_joined(expanded)
    if joined is not None:
        return joined
    if _SHARED_SOURCE.search(expanded):
        return TargetReading(SHARED, "the build's own target directory")
    return TargetReading(UNKNOWN, f"`{_summary(expression)}`")


def _literal_name(slot: str) -> str | None:
    """Return the name a slot holds when it is exactly one string literal.

    A slot that merely contains a literal (`vec![("A", "b")`, `format!("{}_X",
    "CARGO")`) does not name that variable, and reading its first string would
    credit the call with a name it does not have.

    Returns
    -------
    str | None
        The literal's text, or None when the slot is anything else.
    """
    stripped = slot.strip()
    return stripped[1:-1] if _is_plain_literal(stripped) else None


def _may_carry_flag(payload: str) -> bool:
    """Report whether an argument call's source may name `--target-dir`.

    A literal argument is read. An argument the reader cannot read is a value
    when an option the call spells out precedes it (`"--out-dir", path`), and
    may be the flag otherwise (`[flag, path]`, `.arg(flag)`). A call with no
    arguments the reader can see at all, such as a handoff, may hold anything.

    Returns
    -------
    bool
        True when the flag cannot be ruled out.
    """
    items = argument_items(payload)
    if not items:
        return True
    previous = ""
    for item in items:
        if "--target-dir" in item:
            return True
        if not _is_plain_literal(item) and not _awaits_value(previous):
            return True
        previous = item
    return False


def _awaits_value(previous: str | None) -> bool:
    """Report whether the argument *previous* leaves the next one as its value.

    Only an option known to take a value, and not already given one inline
    (`--out-dir=x`), does. Cargo has many switches and a program may define
    more, so `["--timings", flag]` and `["--release", flag]` leave `flag` free to
    be `--target-dir=...`, and so does any option the reader has not heard of.

    `None` means the reader has no argument to rely on: a conditional call may
    have consumed the value already, so the next word is free to be the flag.

    Returns
    -------
    bool
        True when *previous* is an option that consumes the next argument.
    """
    if previous is None or not _is_plain_literal(previous):
        return False
    return previous[1:-1] in _VALUE_OPTIONS


def _is_plain_literal(item: str) -> bool:
    """Report whether *item* is exactly one string literal with no escapes.

    The reader compares literal text, and a Rust backslash escape spells a
    character the text does not show (a hexadecimal escape for 2d is a hyphen),
    so an escaped literal is not read.

    Returns
    -------
    bool
        True for a string literal whose text is its value.
    """
    return STRING.fullmatch(item) is not None and "\\" not in item


def _is_escaped_literal(item: str) -> bool:
    """Report whether *item* is one string literal that holds an escape.

    Returns
    -------
    bool
        True for a string literal whose value differs from its text.
    """
    return STRING.fullmatch(item) is not None and "\\" in item


def _hidden() -> TargetReading:
    """Return the answer for a target a later hidden operation may have replaced."""
    return TargetReading(UNKNOWN, "set before a call the reader cannot see through")


class _Replay:
    """Replay a command's recorded calls, last write winning."""

    def __init__(self, bindings: dict[str, str]) -> None:
        """Start with nothing set and the file's local bindings."""
        self.bindings = bindings
        self.variables: dict[str, TargetReading] = {}
        self.flag: TargetReading | None = None
        self.removed: set[str] = set()
        self.directory: str | None = None
        # A call the reader could not see through may have set a target, so the
        # absence of one is no longer evidence. An `env_clear` after it wipes it.
        self.is_opaque = False
        # An argument call the reader could not read may have added `--target-dir`.
        self.is_flag_unknown = False
        # `--manifest-path` points cargo at another workspace, whose own target
        # is the default, whatever the working directory is.
        self.has_manifest_path = False
        self._is_after_separator = False
        self._is_flag_value_next = False
        # The argument before this one, which says whether this one is a value.
        # None after a conditional call, whose own argument may already have
        # been the value the reader would otherwise expect.
        self._previous: str | None = ""

    def hide(self) -> None:
        """Take on a call the reader cannot see through."""
        self.variables = dict.fromkeys(self.variables, _hidden())
        if self.flag is not None:
            self.flag = _hidden()
        self.is_opaque = True

    def hide_arguments(self, payload: str) -> None:
        """Take on an argument call the reader cannot see through.

        It may have added `--target-dir`, unless a `--` already handed the rest
        of the line to the program cargo runs. Whether it did is kept apart from
        the environment calls: `--target-dir` beats every variable, so a later
        `env_clear()` or `CARGO_TARGET_DIR` cannot settle it.

        The call also breaks the option/value chain. A conditional
        `arg("Cargo.toml")` may have been the value an earlier
        `--manifest-path` was waiting for, so the next unreadable argument is
        free to be `--target-dir=...` rather than that value. Resetting the
        previous argument to an option the reader cannot know clears only that
        expectation; the separator, the pending flag value and the recorded
        readings are left as they were.
        """
        if not self._is_after_separator and _may_carry_flag(payload):
            self.is_flag_unknown = True
        self._previous = None

    def environment(self, payload: str) -> None:
        """Apply one `.env`/`.envs` call."""
        slots = environment_slots(payload)
        if slots is None:
            self.hide()
            return
        for name_slot, value_slot in slots:
            name = _literal_name(name_slot)
            if name is None or value_slot is None:
                self.hide()
            elif name in TARGET_VARIABLES:
                self.variables[name] = classify_value(value_slot, self.bindings)
                self.removed.discard(name)

    def removal(self, method: str, payload: str) -> None:
        """Apply one `.env_remove(..)` or `.env_clear()` call."""
        if method == ENVIRONMENT_CLEAR:
            self.variables.clear()
            self.removed.update(TARGET_VARIABLES)
            self.is_opaque = False
            return
        name = _literal_name(payload)
        if name is None:
            self.hide()
        elif name in TARGET_VARIABLES:
            self.variables.pop(name, None)
            self.removed.add(name)

    def arguments(self, payload: str) -> None:
        """Apply one `.arg(..)`/`.args(..)` call, reading `--target-dir`.

        Words after `--` belong to the program cargo runs, not to cargo, so
        they are not read, and a `--manifest-path` is noted because it moves
        the default target to that workspace.
        """
        for item in argument_items(payload):
            if not self._is_after_separator:
                self._read_argument(item)
            self._previous = item

    def _read_argument(self, item: str) -> None:
        """Read one argument that precedes any `--` separator.

        An argument the reader cannot read, outside an option's value, may be the
        flag, as it may in a call made on only some paths.
        """
        if item == _SEPARATOR:
            self._is_after_separator = True
        elif self._is_flag_value_next:
            self.flag = classify_value(item, self.bindings)
            self._is_flag_value_next = False
        elif item == TARGET_FLAG:
            self._is_flag_value_next = True
        elif "--target-dir" in item:
            self.flag = classify_value(item, self.bindings)
        elif item.startswith(_MANIFEST_PATH):
            self.has_manifest_path = True
        elif _is_escaped_literal(item):
            # An escaped spelling of the flag is the flag at run time.
            self.is_flag_unknown = True
        elif not _is_plain_literal(item) and not _awaits_value(self._previous):
            self.is_flag_unknown = True

    def reading(self) -> TargetReading:
        """Return where the replayed command builds."""
        if self.flag is not None:
            return self.flag
        if self.is_flag_unknown:
            return TargetReading(
                UNKNOWN, "an argument call the reader cannot see may select a target"
            )
        variable = self._variable_reading()
        if variable is not None:
            return variable
        if self.is_opaque:
            return TargetReading(UNKNOWN, "an environment call the reader cannot see")
        if self._is_cut_off_in_temporary_directory():
            return TargetReading(
                PRIVATE,
                "a temporary working directory with no inherited target directory",
                is_inferred=True,
            )
        return TargetReading(INHERITED)

    def _variable_reading(self) -> TargetReading | None:
        """Return what the target variables set, or None when none is set.

        Cargo reads `CARGO_TARGET_DIR` first, so a value set only on
        `CARGO_BUILD_TARGET_DIR` decides the target when nothing can supply the
        higher one: it was removed, and no call after that is unreadable.

        Returns
        -------
        TargetReading | None
            The reading of the variable that decides, or None.
        """
        higher, lower = TARGET_VARIABLES
        if higher in self.variables:
            return self.variables[higher]
        if lower not in self.variables:
            return None
        if self.is_opaque or higher not in self.removed:
            return TargetReading(UNKNOWN, f"{higher} may take precedence over {lower}")
        return self.variables[lower]

    def _is_cut_off_in_temporary_directory(self) -> bool:
        """Report whether cargo would build under a temporary directory by default.

        That needs a temporary working directory and no way for a target to
        reach the child: both variables removed (or the environment cleared)
        and no call after that the reader could not see through.

        Returns
        -------
        bool
            True when nothing can have given the child a target directory.
        """
        if self.directory is None:
            return False
        if self.is_opaque:
            return False
        if self.has_manifest_path:
            return False
        if not set(TARGET_VARIABLES) <= self.removed:
            return False
        return _is_temporary(self.directory, self.bindings)


def scoped_calls(
    calls: typ.Iterable[tuple[str, str]], text: str
) -> list[tuple[str, str]]:
    """Return *calls* with each name resolved in the scope *text* describes.

    A helper's calls are copied onto each call site, which lives in another
    function. A name such as `target` in the helper means the helper's own
    binding, so it is resolved here, before the copy, and not against whatever
    the caller happens to call `target`.

    Returns
    -------
    list[tuple[str, str]]
        The calls, each payload with its locally bound names inlined.
    """
    bindings = local_bindings(text)
    return [(method, expand(payload, bindings)) for method, payload in calls]


def sets_target_variable(calls: typ.Iterable[tuple[str, str]]) -> bool:
    """Report whether any environment call names a cargo target variable."""
    return any(
        method in ENVIRONMENT_METHODS
        and any(name in payload for name in TARGET_VARIABLES)
        for method, payload in calls
    )


def _has_unreadable_item(payload: str) -> bool:
    """Report whether an argument call holds a word the reader cannot read.

    Such a word may be `--target-dir=...`, so a run that adds one may build
    somewhere the run before it did not.

    Returns
    -------
    bool
        True when any item is not a plain string literal.
    """
    return any(not _is_plain_literal(item) for item in argument_items(payload))


def _awaits_flag_value(payload: str, *, awaits_flag_value: bool) -> bool:
    """Report whether an argument call leaves `--target-dir` waiting for a value.

    A later run that adds that value builds where the earlier one did not, so
    the value is target evidence even though it is a plain literal. The inline
    form (`--target-dir=/tmp/x`) already carries its value, and a `--` before
    the flag ends cargo's reading of the line, so neither leaves one pending.
    An empty call passes no word at all, so it neither supplies the value nor
    ends the expectation: the state carries across it unchanged.

    Parameters
    ----------
    payload:
        The call's argument text.
    awaits_flag_value:
        Whether the call before this one left `--target-dir` waiting.

    Returns
    -------
    bool
        True when the next argument word is a pending flag's value.
    """
    is_awaiting = awaits_flag_value
    is_after_separator = False
    for item in argument_items(payload):
        if item == _SEPARATOR:
            is_after_separator = True
        elif is_after_separator:
            # The rest of the line belongs to the program cargo runs, so it
            # neither supplies the flag's value nor clears the expectation.
            continue
        elif is_awaiting:
            # The next word is the value, whatever word it is.
            is_awaiting = False
        else:
            is_awaiting = item == TARGET_FLAG
    return is_awaiting


def _may_change_target(method: str, payload: str, *, awaits_flag_value: bool) -> bool:
    """Report whether one recorded call can change where the command builds.

    Parameters
    ----------
    method:
        The recorded call's method name.
    payload:
        The call's argument text.
    awaits_flag_value:
        Whether the call before this one left `--target-dir` waiting for its
        value, which this call may supply.

    Returns
    -------
    bool
        True for an environment, removal or working-directory call, a call the
        reader could not see through, a call naming the target flag, and an
        argument call that is unreadable or completes the pending flag value.
    """
    if method in ENVIRONMENT_METHODS:
        return True
    if method in {"env_remove", ENVIRONMENT_CLEAR, "current_dir", UNSEEN_CALL}:
        return True
    if method == UNSEEN_ARGUMENTS or "--target-dir" in payload:
        return True
    if _MANIFEST_PATH in payload:
        return True
    return method in {"arg", "args"} and (
        awaits_flag_value or _has_unreadable_item(payload)
    )


def target_signature(
    calls: typ.Iterable[tuple[str, str]],
) -> tuple[tuple[str, str], ...]:
    """Return the calls that can change where a command builds.

    Two runs of one reused command that differ only here start processes that
    build in different places, so a reader that keeps one fact per distinct
    state must also tell them apart by this signature.

    An `arg`/`args` call whose every item is a literal is usually left out,
    because it cannot change the target: two runs that differ only by a
    harmless literal addition still collapse. It is kept when it names
    `--target-dir` or `--manifest-path`, because either moves where cargo
    builds, when it holds an item the reader cannot read, and when its first
    word completes a `--target-dir` an earlier call left waiting.

    Returns
    -------
    tuple[tuple[str, str], ...]
        The environment, removal, working-directory and `--target-dir` calls,
        every argument call that may change the target, and every call the
        reader could not see through, in source order.
    """
    kept: list[tuple[str, str]] = []
    awaits_flag_value = False
    for method, payload in calls:
        if _may_change_target(method, payload, awaits_flag_value=awaits_flag_value):
            kept.append((method, payload))
        if method in {"arg", "args"}:
            awaits_flag_value = _awaits_flag_value(
                payload, awaits_flag_value=awaits_flag_value
            )
    return tuple(kept)


def is_test_rerun(program: str, text: str) -> bool:
    """Report whether a `Command::new` argument names the running test executable.

    The test executable run again builds nothing: it is how a test reruns itself
    as a child with a prepared environment, either as
    `Command::new(env::current_exe()?)` or through a name bound to that call.

    Returns
    -------
    bool
        True when *program* is the running test binary.
    """
    if _TEST_EXECUTABLE in program:
        return True
    if not program.isidentifier():
        return False
    pattern = rf"\blet\s+(?:mut\s+)?{re.escape(program)}\b[^;]*{_TEST_EXECUTABLE}"
    return re.search(pattern, text) is not None


def builds_into_target(
    program: str, calls: typ.Sequence[tuple[str, str]], text: str
) -> bool:
    """Report whether a command that is not cargo sets a cargo target itself.

    A program run with `CARGO_TARGET_DIR` set builds through its own cargo into
    that directory. A `--target-dir` flag is not evidence: it is that program's
    own option, and whitaker's `ls --target-dir` lists a suite without building
    anything. Only the variables cargo itself reads count, and the test
    executable run again as a child builds nothing at all.

    Returns
    -------
    bool
        True when the command's environment names a target variable and the
        reading rests on that, not on an inference from its working directory.
    """
    if is_test_rerun(program, text):
        return False
    reading = read_target(calls, text)
    return (
        reading.state != INHERITED
        and not reading.is_inferred
        and sets_target_variable(calls)
    )


def read_target(calls: typ.Iterable[tuple[str, str]], text: str) -> TargetReading:
    """Return where a command builds, given its recorded calls and its source.

    Parameters
    ----------
    calls:
        The command's `(method, source text)` calls in source order.
    text:
        The file the command is built in, whose `let` bindings resolve the
        names its target is set from.

    Returns
    -------
    TargetReading
        The command's target state and the evidence for it.
    """
    replay = _Replay(local_bindings(text))
    for method, payload in calls:
        if method == UNSEEN_CALL:
            replay.hide()
        elif method == UNSEEN_ARGUMENTS:
            replay.hide_arguments(payload)
        elif method in ENVIRONMENT_METHODS:
            replay.environment(payload)
        elif method in {"env_remove", ENVIRONMENT_CLEAR}:
            replay.removal(method, payload)
        elif method in {"arg", "args"}:
            replay.arguments(payload)
        elif method == "current_dir":
            replay.directory = payload
    return replay.reading()
