"""Name the crates a checkout compiles, for resolving a Whitaker crate exclusion.

Whitaker's `excluded_crates` entries are rustc crate names, which are not only
package names: an integration test compiles as its own crate named after its
file, a binary after its target, and a path dependency may be renamed. The
estate's own `dylint.toml` files hold all of these (`file_handler_tests`,
`whitaker_package_lints`, `_femtologging_rs`). To say whether an entry names a
crate the repository governs, names a fixture or vendored crate, or names
nothing, this module collects every such name from the manifests and the
conventional target directories, hyphens read as underscores as rustc does.

A crate under a fixture, vendored or build-output tree is a `fixture`; the
audit's other rules do not govern it either. A name found in both kinds is
governed.
"""

from __future__ import annotations

import os
import pathlib
import typing as typ

from .documentation_rust import RUST_PRUNED_DIRECTORIES
from .markdown_envelope import (
    PRUNED_DIRECTORIES,
    is_symlink,
    raise_walk_error,
)
from .test_environment_types import CARGO_MANIFEST, table

if typ.TYPE_CHECKING:
    from .test_environment_types import Reader

__all__ = ["FIXTURE", "GOVERNED", "crate_kinds", "normalised"]

GOVERNED: typ.Final = "governed"
FIXTURE: typ.Final = "fixture"
OPERATION_SCAN_CRATES: typ.Final = "scan-rust-crates"

_TARGET_DIRECTORIES: typ.Final = ("tests", "benches", "examples", "src/bin")
_TARGET_TABLES: typ.Final = ("bin", "test", "bench", "example")
_DEPENDENCY_TABLES: typ.Final = (
    "dependencies",
    "dev-dependencies",
    "build-dependencies",
)


def normalised(name: str) -> str:
    """Return a crate name as rustc spells it: hyphens are underscores."""
    return name.replace("-", "_")


def _manifests(checkout: pathlib.Path) -> list[str]:
    """Return every Cargo.toml outside build-output and VCS trees, sorted.

    Fixture and vendored crates are included here, unlike in the lint-table
    walk, because an exclusion may name one on purpose.

    Returns
    -------
    list[str]
        Manifest paths relative to the checkout, in POSIX form.
    """
    skipped = PRUNED_DIRECTORIES | {"target"}
    found: list[str] = []
    for current, directories, files in os.walk(checkout, onerror=raise_walk_error):
        directories[:] = sorted(name for name in directories if name not in skipped)
        base = pathlib.Path(current)
        found.extend(
            (base / name).relative_to(checkout).as_posix()
            for name in files
            if name == CARGO_MANIFEST
            and not is_symlink(base / name, OPERATION_SCAN_CRATES)
        )
    return sorted(found)


def _is_fixture(relative: str) -> bool:
    """Return whether a manifest sits under a fixture, vendored or output tree."""
    parts = pathlib.PurePosixPath(relative).parent.parts
    return any(part in RUST_PRUNED_DIRECTORIES for part in parts)


def _target_name(entry: pathlib.Path) -> str | None:
    """Return the crate name Cargo gives a file or directory in a target folder."""
    if entry.is_file() and entry.suffix == ".rs":
        return entry.stem
    if entry.is_dir() and (entry / "main.rs").is_file():
        return entry.name
    return None


def _discovered_targets(directory: pathlib.Path) -> set[str]:
    """Return the crate names Cargo auto-discovers under a package directory."""
    folders = [directory / sub for sub in _TARGET_DIRECTORIES]
    found = (
        _target_name(entry)
        for folder in folders
        if folder.is_dir()
        for entry in folder.iterdir()
    )
    return {name for name in found if name is not None}


def _strings_named(values: object) -> set[str]:
    """Return the `name` strings of a table, or of each table in a list."""
    tables = values if isinstance(values, list) else [values]
    return {n for item in tables if isinstance(n := table(item).get("name"), str)}


def _alias_names(manifest: dict[str, object]) -> set[str]:
    """Return the path-dependency aliases of a manifest.

    Only a path dependency is a crate of this repository; a registry crate is
    not compiled under the workspace's lints.

    Returns
    -------
    set[str]
        The dependency keys that name a local path.
    """
    return {
        alias
        for name in _DEPENDENCY_TABLES
        for alias, spec in table(manifest.get(name)).items()
        if "path" in table(spec)
    }


def _declared_names(manifest: dict[str, object]) -> set[str]:
    """Return the names a manifest declares: package, lib, targets, local aliases."""
    names = _strings_named(manifest.get("package")) | _strings_named(
        manifest.get("lib")
    )
    for kind in _TARGET_TABLES:
        names |= _strings_named(manifest.get(kind))
    return names | _alias_names(manifest)


def crate_kinds(reader: Reader) -> dict[str, str]:
    """Return every crate name of the checkout, mapped to its kind.

    Parameters
    ----------
    reader:
        Reads TOML inside the checkout and records what it could not read.

    Returns
    -------
    dict[str, str]
        Normalised crate name to `governed` or `fixture`.
    """
    kinds: dict[str, str] = {}
    for relative in _manifests(reader.checkout):
        manifest = reader.toml(reader.checkout / relative)
        if manifest is None:
            continue
        directory = (reader.checkout / relative).parent
        names = _declared_names(manifest) | _discovered_targets(directory)
        kind = FIXTURE if _is_fixture(relative) else GOVERNED
        for name in map(normalised, names):
            if kinds.get(name) != GOVERNED:
                kinds[name] = kind
    return kinds
