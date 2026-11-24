"""Record how the compiler reaches a Rust source file.

The module resolver in :mod:`nested_cargo_modules` follows `mod` declarations
and `#[path]` attributes. What it learns about each file it reaches is small
enough to live apart from the resolution rules, and the preparation reader
needs it too: a file that is only compiled inside the test binaries that
declare it is prepared when those binaries are.
"""

from __future__ import annotations

import dataclasses
import re
import typing as typ

from .nested_cargo_source import TestScope, whole_file_scope

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

    from .nested_cargo_source import RustSource

_PATH_ATTRIBUTE: typ.Final = re.compile(r"#\[\s*path\s*=\s*\"(?P<value>[^\"]*)\"")

FRESH: typ.Final = "fresh"
TOUCHED: typ.Final = "touched"

# Cargo builds every `.rs` file directly inside one of these directories as a
# target of its own, so such a file is also a crate root.
CRATE_ROOT_DIRECTORIES: typ.Final = frozenset({"tests", "benches", "examples"})


def is_target_root(relative: pathlib.PurePosixPath) -> bool:
    """Report whether Cargo builds this file as a target, not just resolves like one.

    Cargo's own discovery is narrower than the stems the module resolver accepts
    for resolving declarations: `lib.rs` and `main.rs` are targets only at
    `src/lib.rs` and `src/main.rs`, and a `mod.rs` is a module of whichever file
    declares it unless it sits directly in `tests/`, `benches/` or `examples/`
    (a `tests/mod.rs` is built and run as a test binary of its own). A file
    that is none of these is not a target, so it does not stand for one.

    Returns
    -------
    bool
        True when Cargo discovers the file as a target of its own.
    """
    return _is_single_file_target(relative) or _is_directory_target(relative)


def _is_single_file_target(relative: pathlib.PurePosixPath) -> bool:
    """Report whether *relative* is a target that is one `.rs` file."""
    parent = relative.parent
    if relative.stem in {"lib", "main"} and parent.name == "src":
        return True
    is_in_bin = parent.name == "bin" and parent.parent.name == "src"
    return is_in_bin or parent.name in CRATE_ROOT_DIRECTORIES


def _is_directory_target(relative: pathlib.PurePosixPath) -> bool:
    """Report whether *relative* is the `main.rs` of a target kept in a directory."""
    if relative.stem != "main":
        return False
    holder = relative.parent.parent
    is_in_bin = holder.name == "bin" and holder.parent.name == "src"
    return is_in_bin or holder.name in CRATE_ROOT_DIRECTORIES


@dataclasses.dataclass(frozen=True, slots=True)
class Reach:
    """How the compiler was seen reaching one source file.

    A file can be reached in both ways at once — `tests/it.rs` is a target
    Cargo builds on its own, and is also a file another test's `mod it;` may
    name — so each flag records evidence rather than a single verdict.

    Attributes
    ----------
    by_name:
        Declared by a plain `mod name;`, which makes the file a module whose
        own children live in a directory named after it.
    by_path:
        Named by `#[path = "..."]`, which keeps the file's children beside it.
    roots:
        The Cargo targets (`tests/ui.rs`, `src/lib.rs`) whose module tree
        contains the file, directly or through any chain of declarations. A
        file in a test binary's tree is compiled by that binary, so a rule that
        prepares the binary prepares the file; empty when none was seen.
    """

    by_name: bool = False
    by_path: bool = False
    roots: frozenset[str] = frozenset()

    def __or__(self, other: Reach) -> Reach:
        """Return the reach that has everything either of these recorded.

        Returns
        -------
        Reach
            The union of both observations.
        """
        return Reach(
            by_name=self.by_name or other.by_name,
            by_path=self.by_path or other.by_path,
            roots=self.roots | other.roots,
        )


def _unseen_owner(candidate: pathlib.PurePosixPath) -> Reach:
    """Return the owner a file outside test directories may have beyond the ones seen.

    A file under `src/` that a test binary imports by `#[path]` may equally be
    an ordinary module of the library, whose own `mod name;` declaration the
    resolver does not follow. The library's tests compile it too, so the file
    stands for that unseen owner, which no setup rule covers.

    Returns
    -------
    Reach
        One recording the file as its own owner, or nothing for a file that
        only test code can reach.
    """
    if whole_file_scope(candidate):
        return Reach()
    return Reach(roots=frozenset({candidate.as_posix()}))


def record_reach(
    candidate: pathlib.PurePosixPath,
    observed: Reach,
    scopes: dict[pathlib.PurePosixPath, TestScope],
    reaches: dict[pathlib.PurePosixPath, Reach],
) -> str | None:
    """Record that a declaration reached *candidate*, and say what moved.

    Returns
    -------
    str | None
        `FRESH` for a file not seen before, `TOUCHED` for one whose scope or
        reach changed, and None when nothing about it moved.
    """
    before = reaches.get(candidate, Reach())
    reaches[candidate] = before | observed | _unseen_owner(candidate)
    known = scopes.get(candidate)
    if known is None:
        scopes[candidate] = TestScope(whole_file=True)
        return FRESH
    if not known.whole_file:
        # Read as ordinary library code, whose scan cannot see the declarations
        # only test code holds.
        known.whole_file = True
        return TOUCHED
    # Already test code, but a reach it did not have before means its own
    # declarations may now resolve elsewhere.
    return TOUCHED if before != reaches[candidate] else None


def _tail(path: str) -> str:
    """Return the last two components of *path*, which name a file closely enough."""
    return "/".join(path.replace("\\", "/").split("/")[-2:])


def _library_path_tails(sources: cabc.Iterable[RustSource]) -> frozenset[str]:
    """Return the tails of the files library code names with `#[path]`.

    The resolver follows only declarations in test scope, so a library that
    includes a file by `#[path]` without `#[cfg(test)]` is invisible to it. A
    tail match is deliberately loose: it can only make a file count as reached
    by an owner no setup rule covers, which is the safe error.

    Returns
    -------
    frozenset[str]
        The two-component tails of every `#[path = "..."]` value in code that
        is not whole-file test code.
    """
    return frozenset(
        _tail(match["value"])
        for source in sources
        if not whole_file_scope(source.relative)
        for match in _PATH_ATTRIBUTE.finditer(source.text)
    )


def reached_by(
    reaches: cabc.Mapping[pathlib.PurePosixPath, Reach],
    sources: cabc.Iterable[RustSource],
) -> dict[str, list[str]]:
    """Return the sorted targets that compile each reached file.

    A file Cargo also builds as a target is compiled by itself too, whoever
    else declares it, so it counts among its own targets. A file that library
    code also names by `#[path]` has an owner the resolver did not follow, so
    it stands for that owner as well.

    Returns
    -------
    dict[str, list[str]]
        Root-relative path to the targets whose module tree holds the file.
    """
    library_tails = _library_path_tails(sources)
    found: dict[str, list[str]] = {}
    for relative, reach in sorted(reaches.items()):
        if not reach.roots:
            continue
        posix = relative.as_posix()
        own = is_target_root(relative) or _tail(posix) in library_tails
        found[posix] = sorted(reach.roots | ({posix} if own else set()))
    return found
