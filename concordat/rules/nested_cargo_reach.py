"""Record how the compiler reaches a Rust source file.

The module resolver in :mod:`nested_cargo_modules` follows `mod` declarations
and `#[path]` attributes. What it learns about each file it reaches is small
enough to live apart from the resolution rules, and the preparation reader
needs it too: a file that is only compiled inside the test binaries that
declare it is prepared when those binaries are.
"""

from __future__ import annotations

import dataclasses
import pathlib
import posixpath
import re
import typing as typ

from .dependency_family_coverage import mapping, sequence
from .nested_cargo_source import TestScope, whole_file_scope

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from .nested_cargo_source import RustSource

_PATH_ATTRIBUTE: typ.Final = re.compile(r"#\[\s*path\s*=\s*\"(?P<value>[^\"]*)\"")
# Rust allows any of the three macro delimiters, a raw string and a trailing
# comma, so the argument is taken whole and every string literal in it is read.
_INCLUDE: typ.Final = re.compile(r"\binclude!\s*[(\[{](?P<argument>[^)\]}]*)")
_LITERAL: typ.Final = re.compile(r"r#*\"(?P<raw>[^\"]*)\"|\"(?P<text>[^\"]*)\"")

FRESH: typ.Final = "fresh"
TOUCHED: typ.Final = "touched"
# Marks an owner the resolver cannot see. No setup rule's binary selector can
# match a name with this prefix, so such an owner is never covered, whatever
# file it is recorded against.
UNSEEN: typ.Final = "unseen:"

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
    return Reach(roots=frozenset({f"{UNSEEN}{candidate.as_posix()}"}))


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
    """Return the last two components of *path*, which name a file closely enough.

    The path is folded first, so a `.` or `..` component cannot hide a file:
    `support/./mod.rs` and `support/tmp/../mod.rs` have the tail `support/mod.rs`.

    Returns
    -------
    str
        The final two non-empty, non-`.` components joined by `/`.
    """
    parts = [
        part
        for part in posixpath.normpath(path.replace("\\", "/")).split("/")
        if part not in {"", "."}
    ]
    return "/".join(parts[-2:])


def _library_path_tails(sources: cabc.Iterable[RustSource]) -> frozenset[str]:
    """Return the tails of the files library code names with `#[path]`.

    The resolver follows only declarations in test scope, so a library that
    includes a file by `#[path]` without `#[cfg(test)]` is invisible to it. A
    tail match is deliberately loose: it can only make a file count as reached
    by an owner no setup rule covers, which is the safe error.

    An `include!("file.rs")` pastes a file into whichever source names it,
    in any package, and the resolver does not record that either. It is read
    from every source, test code included: the importer's coverage is unknown,
    and a file pasted into a covered binary reads as an unseen owner too. That
    is the fail-closed price of not modelling `include!`.

    Returns
    -------
    frozenset[str]
        The two-component tails of every `#[path = "..."]` value in code that
        is not whole-file test code, and of every `include!` literal.
    """
    paths = (
        match["value"]
        for source in sources
        if not whole_file_scope(source.relative)
        for match in _PATH_ATTRIBUTE.finditer(source.text)
    )
    includes = (
        literal["raw"] or literal["text"]
        for source in sources
        for call in _INCLUDE.finditer(source.text)
        for literal in _LITERAL.finditer(call["argument"])
    )
    return frozenset(_tail(value) for value in (*paths, *includes))


def _target_tables(document: dict[str, object]) -> list[tuple[str, dict[str, object]]]:
    """Return each `[lib]` and `[[bin|test|bench|example]]` table with its kind."""
    found: list[tuple[str, dict[str, object]]] = []
    library = mapping(document.get("lib"))
    if library is not None:
        found.append(("lib", library))
    for kind in ("bin", "test", "bench", "example"):
        items = (mapping(item) for item in sequence(document.get(kind)) or [])
        found.extend((kind, table) for table in items if table is not None)
    return found


def _manifest_targets(
    checkout: pathlib.Path, manifests: cabc.Mapping[pathlib.Path, dict[str, object]]
) -> list[tuple[pathlib.PurePosixPath, str]]:
    """Return the files manifests declare as targets with an explicit `path`.

    A `[[test]]` target is named by its `name`, not by its file, so its owner is
    recorded as the `tests/<name>.rs` a setup rule's `binary(<name>)` selects.
    Any other target is recorded as an unseen owner. One file can be declared
    by several targets, so each declaration is its own entry.

    Returns
    -------
    list[tuple[pathlib.PurePosixPath, str]]
        Each declared target file, root-relative and normalised, with the owner
        that stands for the target it is built as.
    """
    found: list[tuple[pathlib.PurePosixPath, str]] = []
    for manifest, document in manifests.items():
        base = manifest.parent.relative_to(checkout).as_posix()
        for kind, table in _target_tables(document):
            value = table.get("path")
            if not isinstance(value, str):
                continue
            path = pathlib.PurePosixPath(posixpath.normpath(f"{base}/{value}"))
            name = table.get("name")
            if kind == "test" and isinstance(name, str):
                found.append((path, posixpath.normpath(f"{base}/tests/{name}.rs")))
            else:
                found.append((path, f"{UNSEEN}{path.as_posix()}"))
    return found


def unseen_owners(
    sources: cabc.Iterable[RustSource],
    checkout: pathlib.Path,
    manifests: cabc.Mapping[pathlib.Path, dict[str, object]],
) -> dict[pathlib.PurePosixPath, Reach]:
    """Return the files that have an owner the resolver does not follow.

    The resolver follows only declarations in test scope, and it finds targets
    by Cargo's directory conventions. A file that library code names by
    `#[path]` without `#[cfg(test)]`, or that a manifest declares as a target
    with an explicit `path`, is compiled by an owner it never sees. Each such
    file is seeded as its own owner, which no setup rule covers, before the
    resolver runs, so every file it declares inherits the owner too. A tail
    match on the `#[path]` value is deliberately loose: its only error is a
    missed exemption.

    Returns
    -------
    dict[pathlib.PurePosixPath, Reach]
        The reach to start the resolver from, keyed by root-relative path.
    """
    sources = list(sources)
    tails = _library_path_tails(sources)
    owners: dict[pathlib.PurePosixPath, set[str]] = {}
    for source in sources:
        if _tail(source.relative.as_posix()) in tails:
            owners.setdefault(source.relative, set()).add(
                f"{UNSEEN}{source.relative.as_posix()}"
            )
    for path, owner in _manifest_targets(checkout, manifests):
        owners.setdefault(path, set()).add(owner)
    # A file named by `#[path]` or as a target keeps its children beside it, so
    # the seed says so; otherwise its declarations would resolve under a stem
    # directory and the owner would be handed to the wrong file.
    return {
        path: Reach(by_path=True, roots=frozenset(found))
        for path, found in owners.items()
    }


def reached_by(
    reaches: cabc.Mapping[pathlib.PurePosixPath, Reach],
) -> dict[str, list[str]]:
    """Return the sorted targets that compile each reached file.

    A file Cargo also builds as a target is compiled by itself too, whoever
    else declares it, so it counts among its own targets.

    Returns
    -------
    dict[str, list[str]]
        Root-relative path to the targets whose module tree holds the file.
    """
    return {
        relative.as_posix(): sorted(
            reach.roots | ({relative.as_posix()} if is_target_root(relative) else set())
        )
        for relative, reach in sorted(reaches.items())
        if reach.roots
    }
