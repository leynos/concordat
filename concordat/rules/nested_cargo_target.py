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

from .nested_cargo_commands import (
    ENVIRONMENT_CLEAR,
    ENVIRONMENT_METHODS,
    UNSEEN_CALL,
    first_literal,
)
from .nested_cargo_text import argument_items, environment_slots

INHERITED: typ.Final = "inherited"
SHARED: typ.Final = "shared"
DEDICATED: typ.Final = "dedicated"
PRIVATE: typ.Final = "private"
UNKNOWN: typ.Final = "unknown"

TARGET_VARIABLES: typ.Final = ("CARGO_TARGET_DIR", "CARGO_BUILD_TARGET_DIR")
TARGET_FLAG: typ.Final = '"--target-dir"'

_STRING: typ.Final = re.compile(r'"(?:[^"\\]|\\.)*"')
# Constructors and accessors of a directory that is removed with its owner or
# named for one run: a later run can never find what an earlier one built.
_TEMPORARY: typ.Final = re.compile(
    r"\b(?:tempdir|tempdir_in|TempDir|temp_dir|mkdtemp|NamedTempFile|tempfile)\b"
)
_UNIQUE: typ.Final = re.compile(
    r"\b(?:Uuid|uuid|SystemTime|fastrand|rand)\b|\bprocess\s*::\s*id\b"
)
_MANIFEST: typ.Final = re.compile(r"CARGO_MANIFEST_DIR")
_JOINED_TARGET: typ.Final = re.compile(r'\.join\(\s*"(?P<path>[^"]*)"')
_SHARED_SOURCE: typ.Final = re.compile(
    r"CARGO_TARGET_DIR|target_directory|\bmetadata\b|\benv::var(?:_os)?\b"
    r"|\bvar_os?\b|CARGO_LLVM_COV_TARGET_DIR"
)
_LET: typ.Final = re.compile(
    r"\blet\s+(?:mut\s+)?(?P<name>[A-Za-z_]\w*)\s*(?::[^=;]*)?=\s*(?P<rhs>[^;]*);"
)
_TEMPORARY_PARAMETER: typ.Final = re.compile(
    r"(?P<name>[A-Za-z_]\w*)\s*:\s*&?\s*(?:mut\s+)?(?:tempfile\s*::\s*)?TempDir\b"
)
_IDENTIFIER: typ.Final = re.compile(r"(?<![\w.:$])[a-z_]\w*\b(?!\s*[(:!])")
_EXPANSION_DEPTH: typ.Final = 3
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


def local_bindings(text: str) -> dict[str, str]:
    """Return the expression each local name was last bound to in *text*.

    The latest `let` wins, which is only an approximation of scope, but the
    reader uses the answer to decide whether a value is *visibly* temporary and
    answers `unknown` for everything else.

    Returns
    -------
    dict[str, str]
        Right-hand-side source text by name; a `TempDir` parameter is bound to
        the type so that `dir.path()` reads as temporary.
    """
    found = {match["name"]: match["rhs"] for match in _LET.finditer(text)}
    for match in _TEMPORARY_PARAMETER.finditer(text):
        found.setdefault(match["name"], "tempfile::TempDir")
    return found


def _expand(expression: str, bindings: dict[str, str]) -> str:
    """Replace each locally bound name in *expression* by what it was bound to.

    String literals are left alone, so a path component that happens to spell a
    variable name is not substituted.

    Returns
    -------
    str
        The expression with up to three levels of local bindings inlined.
    """

    def replace(chunk: re.Match[str]) -> str:
        """Return the bound expression for one identifier, parenthesised."""
        rhs = bindings.get(chunk.group())
        return f"({rhs})" if rhs is not None else chunk.group()

    for _ in range(_EXPANSION_DEPTH):
        pieces = re.split(f"({_STRING.pattern})", expression)
        expanded = "".join(
            piece if index % 2 else _IDENTIFIER.sub(replace, piece)
            for index, piece in enumerate(pieces)
        )
        if expanded == expression:
            break
        expression = expanded
    return expression


def _code(expression: str) -> str:
    """Return *expression* with every string literal blanked."""
    return _STRING.sub('""', expression)


def _is_temporary(expression: str, bindings: dict[str, str]) -> bool:
    """Report whether *expression* visibly derives from a temporary directory."""
    return _TEMPORARY.search(_code(_expand(expression, bindings))) is not None


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
    expanded = _expand(expression, bindings)
    code = _code(expanded)
    if _TEMPORARY.search(code):
        return TargetReading(PRIVATE, "a temporary directory")
    if _UNIQUE.search(code):
        return TargetReading(PRIVATE, "a name unique to the run")
    joined = _classify_joined(expanded)
    if joined is not None:
        return joined
    if _SHARED_SOURCE.search(expanded):
        return TargetReading(SHARED, "the build's own target directory")
    return TargetReading(UNKNOWN, f"`{_summary(expression)}`")


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
        self._is_flag_value_next = False

    def hide(self) -> None:
        """Take on a call the reader cannot see through."""
        self.variables = dict.fromkeys(self.variables, _hidden())
        if self.flag is not None:
            self.flag = _hidden()
        self.is_opaque = True

    def environment(self, payload: str) -> None:
        """Apply one `.env`/`.envs` call."""
        slots = environment_slots(payload)
        if slots is None:
            self.hide()
            return
        for name_slot, value_slot in slots:
            name = first_literal(name_slot)
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
        name = first_literal(payload)
        if name is None:
            self.hide()
        elif name in TARGET_VARIABLES:
            self.variables.pop(name, None)
            self.removed.add(name)

    def arguments(self, payload: str) -> None:
        """Apply one `.arg(..)`/`.args(..)` call, reading `--target-dir`."""
        for item in argument_items(payload):
            if self._is_flag_value_next:
                self.flag = classify_value(item, self.bindings)
                self._is_flag_value_next = False
            elif item == TARGET_FLAG:
                self._is_flag_value_next = True
            elif "--target-dir" in item:
                self.flag = classify_value(item, self.bindings)

    def reading(self) -> TargetReading:
        """Return where the replayed command builds."""
        if self.flag is not None:
            return self.flag
        for name in TARGET_VARIABLES:
            if name in self.variables:
                return self.variables[name]
        if (
            self.directory is not None
            and not self.is_opaque
            and set(TARGET_VARIABLES) <= self.removed
            and _is_temporary(self.directory, self.bindings)
        ):
            return TargetReading(
                PRIVATE,
                "a temporary working directory with no inherited target directory",
                is_inferred=True,
            )
        return TargetReading(INHERITED)


def sets_target_variable(calls: typ.Iterable[tuple[str, str]]) -> bool:
    """Report whether any environment call names a cargo target variable."""
    return any(
        method in ENVIRONMENT_METHODS
        and any(name in payload for name in TARGET_VARIABLES)
        for method, payload in calls
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
        elif method in ENVIRONMENT_METHODS:
            replay.environment(payload)
        elif method in {"env_remove", ENVIRONMENT_CLEAR}:
            replay.removal(method, payload)
        elif method in {"arg", "args"}:
            replay.arguments(payload)
        elif method == "current_dir":
            replay.directory = payload
    return replay.reading()
