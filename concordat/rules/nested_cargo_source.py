"""Decode the Rust sources below a checkout, and decide their test scope.

The nested-cargo rule reads Rust as text, so every later question depends on a
file that was decoded without ambiguity. Masking literals lets structure be
scanned without a Rust parser; a file whose literal never terminates, or whose
brackets do not balance, is reported as a problem rather than read, because
everything after the defect would be a fiction.

Test scope is two things. A file under a `tests` component, or inside a
`test_support`/`test-support` crate, is test code throughout. Every other file
holds test code only in the spans this module's callers discover, which is why
:class:`TestScope` carries ranges as well as a whole-file flag.
"""

from __future__ import annotations

import dataclasses
import pathlib
import typing as typ

from .dependency_family_discovery import walk_files
from .fs_probe import probe_file, probe_symlink
from .rust_source import mask_literals

WHOLE_FILE_DIRECTORIES: typ.Final = frozenset({"tests", "test_support", "test-support"})


@dataclasses.dataclass(frozen=True, slots=True)
class RustSource:
    """One Rust file, as originally written and as masked for structure."""

    relative: pathlib.PurePosixPath
    text: str
    masked: str


def _literal_problem(masked: str) -> str | None:
    """Return why a literal in a masked source cannot be read, if one cannot.

    Masking normalises every literal to a quoted pair — a raw string to its
    delimiters, a char literal to nothing — so only a string that reaches the
    end of the file leaves an odd number of quotes behind. That string hid the
    rest of the file from every later question, so the file is reported rather
    than read.

    Returns
    -------
    str | None
        The problem, or None when every literal terminates.
    """
    if masked.count('"') % 2:
        return "a string literal is unterminated"
    return None


def _balanced(masked: str) -> bool:
    """Report whether every bracket in *masked* closes in the right order."""
    partners = {")": "(", "]": "[", "}": "{"}
    pending: list[str] = []
    for char in masked:
        if char in "([{":
            pending.append(char)
        elif char in partners and (not pending or pending.pop() != partners[char]):
            # The pop runs only when the stack is non-empty, so a stray closer
            # is rejected without touching an empty list.
            return False
    return not pending


def line_of(text: str, offset: int) -> int:
    """Return the 1-based line number of *offset* in *text*."""
    return text.count("\n", 0, offset) + 1


def brace_pairs(masked: str) -> list[tuple[int, int]]:
    """Return every balanced brace pair as `(open, end)` in source order."""
    pairs: list[tuple[int, int]] = []
    pending: list[int] = []
    for index, char in enumerate(masked):
        if char == "{":
            pending.append(index)
        elif char == "}" and pending:
            pairs.append((pending.pop(), index + 1))
    return pairs


def enclosing_block(
    pairs: list[tuple[int, int]], offset: int
) -> tuple[int, int] | None:
    """Return the innermost balanced block containing *offset*, if any."""
    inside = [pair for pair in pairs if pair[0] < offset < pair[1]]
    if not inside:
        return None
    # The narrowest span is the innermost block: any enclosing block also
    # contains its partner, so its own span is strictly wider.
    return min(inside, key=lambda pair: pair[1] - pair[0])


@dataclasses.dataclass(slots=True)
class TestScope:
    """The offset ranges of one file that count as test code."""

    whole_file: bool
    ranges: list[tuple[int, int]] = dataclasses.field(default_factory=list)

    def contains(self, offset: int) -> bool:
        """Report whether *offset* lies in test scope.

        Returns
        -------
        bool
            True when the offset is covered by a test scope.
        """
        if self.whole_file:
            return True
        return any(start <= offset < end for start, end in self.ranges)


def whole_file_scope(relative: pathlib.PurePosixPath) -> bool:
    """Report whether a whole file is test code.

    Returns
    -------
    bool
        True under a `tests` component or inside a test-support crate.
    """
    return any(part in WHOLE_FILE_DIRECTORIES for part in relative.parts[:-1])


def read_source(
    checkout: pathlib.Path, relative: pathlib.PurePosixPath
) -> tuple[RustSource | None, str | None]:
    """Return one decoded source and its masked form, or the reason it was not read.

    Returns
    -------
    tuple[RustSource | None, str | None]
        The source, or None and the failure that stopped reading it.
    """
    path = checkout / relative.as_posix()
    probe = probe_file(path)
    if probe.read_error is not None:
        return None, f"{relative.as_posix()}: {probe.read_error}"
    if not probe.present:
        return None, None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return None, f"{relative.as_posix()}: {error}"
    masked = mask_literals(text)
    problem = _literal_problem(masked)
    if problem is not None:
        return None, f"{relative.as_posix()}: {problem}"
    if not _balanced(masked):
        return None, f"{relative.as_posix()}: brackets do not balance"
    return RustSource(relative, text, masked), None


def in_fixture(relative: pathlib.PurePosixPath, fixtures: frozenset[str]) -> bool:
    """Report whether a source lies inside a fixture package."""
    where = relative.as_posix()
    return any(
        where == fixture or where.startswith(f"{fixture}/") for fixture in fixtures
    )


def read_sources(
    checkout: pathlib.Path, fixtures: frozenset[str], problems: list[str]
) -> list[RustSource]:
    """Return every in-scope Rust source below the checkout, decoded.

    Returns
    -------
    list[RustSource]
        The sources, in path order.
    """
    found: list[RustSource] = []
    for path in walk_files(checkout, problems):
        if path.suffix != ".rs" or probe_symlink(path).present:
            continue
        relative = pathlib.PurePosixPath(path.relative_to(checkout).as_posix())
        if in_fixture(relative, fixtures):
            continue
        source, error = read_source(checkout, relative)
        if error is not None:
            problems.append(error)
        if source is not None:
            found.append(source)
    return found
