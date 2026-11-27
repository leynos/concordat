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


def _discovered_targets(directory: pathlib.Path) -> set[str]:
    """Return the crate names Cargo auto-discovers under a package directory."""
    names: set[str] = set()
    for sub in _TARGET_DIRECTORIES:
        folder = directory / sub
        if not folder.is_dir():
            continue
        for entry in folder.iterdir():
            if entry.is_file() and entry.suffix == ".rs":
                names.add(entry.stem)
            elif entry.is_dir() and (entry / "main.rs").is_file():
                names.add(entry.name)
    return names


def _declared_names(manifest: dict[str, object]) -> set[str]:
    """Return the names a manifest declares: package, lib, targets, local aliases."""
    names: set[str] = set()
    for section in ("package", "lib"):
        declared = table(manifest.get(section)).get("name")
        if isinstance(declared, str):
            names.add(declared)
    for kind in _TARGET_TABLES:
        targets = manifest.get(kind)
        for target in targets if isinstance(targets, list) else []:
            declared = table(target).get("name")
            if isinstance(declared, str):
                names.add(declared)
    for dependencies in (table(manifest.get(n)) for n in _DEPENDENCY_TABLES):
        # Only a path dependency is a crate of this repository; a registry
        # crate is not compiled under the workspace's lints.
        names.update(
            alias for alias, spec in dependencies.items() if "path" in table(spec)
        )
    return names


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
