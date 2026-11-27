"""Name the crates a checkout compiles, for resolving a Whitaker crate exclusion.

Whitaker's `excluded_crates` entries are rustc crate names, which are not only
package names: an integration test compiles as its own crate named after its
file, a binary after its target, and a path dependency may be renamed. The
estate's own `dylint.toml` files hold all of these (`file_handler_tests`,
`whitaker_package_lints`, `_femtologging_rs`). To say whether an entry names a
crate the repository governs, names a fixture or vendored crate, or names
nothing, this module collects every such name from the manifests and the
conventional target directories, hyphens read as underscores as rustc does.

Cargo's rules are followed where they decide a name: a package's `autotests`,
`autobins`, `autobenches` and `autoexamples` switch off discovery of that kind
(declared `[[test]]`-style targets still count), a path dependency is local
whether it is declared in the package, under a `[target.*]` table, or inherited
from `[workspace.dependencies]`, and a registry dependency is not a crate of
the repository.

The walk never follows a symbolic link. A link that would hide a manifest or a
target folder is recorded as unreadable, so the policy reports it instead of
reading its absence as a name that does not exist.

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

# Each conventional target folder, with the `[package]` switch that turns its
# discovery off.
_TARGET_FOLDERS: typ.Final = (
    ("tests", "autotests"),
    ("benches", "autobenches"),
    ("examples", "autoexamples"),
    ("src/bin", "autobins"),
)
_TARGET_TABLES: typ.Final = ("bin", "test", "bench", "example")
_DEPENDENCY_TABLES: typ.Final = (
    "dependencies",
    "dev-dependencies",
    "build-dependencies",
)


def normalised(name: str) -> str:
    """Return a crate name as rustc spells it: hyphens are underscores."""
    return name.replace("-", "_")


def _symlink_note(relative: str) -> str:
    """Return why a symbolic link is not read."""
    return (
        f"{relative}: is a symbolic link, which the audit does not follow, so the "
        "crate names it would hold are unknown"
    )


def _manifests(checkout: pathlib.Path, unreadable: list[str]) -> list[str]:
    """Return every Cargo.toml outside build-output and VCS trees, sorted.

    Fixture and vendored crates are included here, unlike in the lint-table
    walk, because an exclusion may name one on purpose. A manifest that is a
    symbolic link is recorded in *unreadable* and not returned.

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
        for name in files:
            if name != CARGO_MANIFEST:
                continue
            relative = (base / name).relative_to(checkout).as_posix()
            if is_symlink(base / name, OPERATION_SCAN_CRATES):
                unreadable.append(_symlink_note(relative))
            else:
                found.append(relative)
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


def _folder_targets(folder: pathlib.Path, reader: Reader) -> set[str]:
    """Return the crate names under one target folder, never following a link."""
    relative = folder.relative_to(reader.checkout).as_posix()
    if is_symlink(folder, OPERATION_SCAN_CRATES):
        reader.unreadable.append(_symlink_note(relative))
        return set()
    names: set[str] = set()
    for entry in sorted(folder.iterdir()):
        if is_symlink(entry, OPERATION_SCAN_CRATES):
            reader.unreadable.append(
                _symlink_note(entry.relative_to(reader.checkout).as_posix())
            )
        elif (name := _target_name(entry)) is not None:
            names.add(name)
    return names


def _discovered_targets(
    directory: pathlib.Path, package: dict[str, object], reader: Reader
) -> set[str]:
    """Return the crate names Cargo auto-discovers under a package directory.

    A folder whose `[package]` switch is `false` (`autotests = false`) is not
    discovered; only its declared targets count.

    Returns
    -------
    set[str]
        The names of the files and directories in each discovered folder.
    """
    found: set[str] = set()
    for sub, switch in _TARGET_FOLDERS:
        folder = directory / sub
        if package.get(switch) is False or not folder.is_dir():
            continue
        found |= _folder_targets(folder, reader)
    return found


def _strings_named(values: object) -> set[str]:
    """Return the `name` strings of a table, or of each table in a list."""
    tables = values if isinstance(values, list) else [values]
    return {n for item in tables if isinstance(n := table(item).get("name"), str)}


def _dependency_tables(manifest: dict[str, object]) -> typ.Iterator[dict[str, object]]:
    """Yield every dependency table of a manifest, target-specific ones too."""
    yield from (table(manifest.get(name)) for name in _DEPENDENCY_TABLES)
    for target in table(manifest.get("target")).values():
        yield from (table(table(target).get(name)) for name in _DEPENDENCY_TABLES)


def _is_local(spec: object, inherited: frozenset[str], alias: str) -> bool:
    """Return whether a dependency is a path dependency, directly or inherited."""
    declared = table(spec)
    return "path" in declared or (
        declared.get("workspace") is True and alias in inherited
    )


def _alias_names(manifest: dict[str, object], inherited: frozenset[str]) -> set[str]:
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
        for dependencies in _dependency_tables(manifest)
        for alias, spec in dependencies.items()
        if _is_local(spec, inherited, alias)
    }


def _declared_names(manifest: dict[str, object], inherited: frozenset[str]) -> set[str]:
    """Return the names a manifest declares: package, lib, targets, local aliases."""
    names = _strings_named(manifest.get("package")) | _strings_named(
        manifest.get("lib")
    )
    for kind in _TARGET_TABLES:
        names |= _strings_named(manifest.get(kind))
    return names | _alias_names(manifest, inherited)


def _inherited_local(reader: Reader, manifests: list[str]) -> frozenset[str]:
    """Return the `[workspace.dependencies]` keys that are path dependencies."""
    found: set[str] = set()
    for relative in manifests:
        workspace = table(reader.toml(reader.checkout / relative)).get("workspace")
        for alias, spec in table(table(workspace).get("dependencies")).items():
            if "path" in table(spec):
                found.add(alias)
    return frozenset(found)


def _crate_names(reader: Reader, relative: str, inherited: frozenset[str]) -> set[str]:
    """Return the normalised crate names one manifest and its directory hold."""
    path = reader.checkout / relative
    manifest = reader.toml(path)
    if manifest is None:
        return set()
    package = table(manifest.get("package"))
    names = _declared_names(manifest, inherited) | _discovered_targets(
        path.parent, package, reader
    )
    return {normalised(name) for name in names}


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
    manifests = _manifests(reader.checkout, reader.unreadable)
    inherited = _inherited_local(reader, manifests)
    names: dict[str, set[str]] = {GOVERNED: set(), FIXTURE: set()}
    for relative in manifests:
        kind = FIXTURE if _is_fixture(relative) else GOVERNED
        names[kind] |= _crate_names(reader, relative, inherited)
    # A name found in both kinds is governed, so the governed names go last.
    return {
        **dict.fromkeys(names[FIXTURE], FIXTURE),
        **dict.fromkeys(names[GOVERNED], GOVERNED),
    }
