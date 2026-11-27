"""Name the crates a checkout compiles, for resolving a Whitaker crate exclusion.

Whitaker's `excluded_crates` entries are rustc crate names, which are not only
package names: an integration test compiles as its own crate named after its
file, a binary after its target, and a path dependency may be renamed. The
estate's own `dylint.toml` files hold all of these (`file_handler_tests`,
`whitaker_package_lints`, `_femtologging_rs`). To say whether an entry names a
crate the repository governs, names a fixture or vendored crate, or names
nothing, this module collects every such name from the manifests and the
conventional target directories (`whitaker_exclusions_targets`), hyphens read
as underscores as rustc does.

Cargo's rules are followed where they decide a name: a path dependency is local
whether it is declared in the package, under a `[target.*]` table, or inherited
from `[workspace.dependencies]` of the workspace that owns the package (the
nearest manifest above it with a `[workspace]` table), and a registry
dependency is not a crate of the repository.

The walk never follows a symbolic link. A link that would hide a manifest, a
directory or a target folder is recorded as unreadable, so the policy reports it
instead of reading its absence as a name that does not exist.

A crate under a fixture, vendored or build-output tree is a `fixture`; the
audit's other rules do not govern it either. A name found in both kinds is
governed.
"""

from __future__ import annotations

import os
import pathlib
import posixpath
import typing as typ

from .documentation_rust import RUST_PRUNED_DIRECTORIES
from .markdown_envelope import (
    PRUNED_DIRECTORIES,
    is_symlink,
    raise_walk_error,
)
from .test_environment_types import CARGO_MANIFEST, table
from .whitaker_exclusions_targets import discovered_targets, linked_note

if typ.TYPE_CHECKING:
    from .test_environment_types import Reader

__all__ = [
    "FIXTURE",
    "GOVERNED",
    "crate_kinds",
    "linked_directory_notes",
    "normalised",
]

GOVERNED: typ.Final = "governed"
FIXTURE: typ.Final = "fixture"
OPERATION_SCAN_CRATES: typ.Final = "scan-rust-crates"

_TARGET_TABLES: typ.Final = ("bin", "test", "bench", "example")
_DEPENDENCY_TABLES: typ.Final = (
    "dependencies",
    "dev-dependencies",
    "build-dependencies",
)


def normalised(name: str) -> str:
    """Return a crate name as rustc spells it: hyphens are underscores."""
    return name.replace("-", "_")


def linked_directory_notes(
    base: pathlib.Path,
    directories: list[str],
    skipped: typ.Collection[str],
    checkout: pathlib.Path,
) -> list[str]:
    """Return a note for each symbolic link among a walk's directories.

    `os.walk` lists a link to a directory among the directories but does not
    descend into it, so whatever the link holds is never read; a manifest or a
    configuration inside it would be missed without a word. Names the walk
    prunes anyway are not noted.

    Returns
    -------
    list[str]
        One unreadable-input note per linked directory.
    """
    return [
        linked_note((base / name).relative_to(checkout).as_posix())
        for name in directories
        if name not in skipped and is_symlink(base / name, OPERATION_SCAN_CRATES)
    ]


def _manifests(checkout: pathlib.Path, unreadable: list[str]) -> list[str]:
    """Return every Cargo.toml outside build-output and VCS trees, sorted.

    Fixture and vendored crates are included here, unlike in the lint-table
    walk, because an exclusion may name one on purpose. A manifest or a
    directory that is a symbolic link is recorded in *unreadable*.

    Returns
    -------
    list[str]
        Manifest paths relative to the checkout, in POSIX form.
    """
    skipped = PRUNED_DIRECTORIES | {"target"}
    found: list[str] = []
    for current, directories, files in os.walk(checkout, onerror=raise_walk_error):
        base = pathlib.Path(current)
        unreadable.extend(linked_directory_notes(base, directories, skipped, checkout))
        directories[:] = sorted(name for name in directories if name not in skipped)
        for name in files:
            if name != CARGO_MANIFEST:
                continue
            relative = (base / name).relative_to(checkout).as_posix()
            if is_symlink(base / name, OPERATION_SCAN_CRATES):
                unreadable.append(linked_note(relative))
            else:
                found.append(relative)
    return sorted(found)


def _is_fixture(relative: str) -> bool:
    """Return whether a manifest sits under a fixture, vendored or output tree."""
    parts = pathlib.PurePosixPath(relative).parent.parts
    return any(part in RUST_PRUNED_DIRECTORIES for part in parts)


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


def _directory_key(directory: pathlib.PurePosixPath) -> str:
    """Return a directory as a checkout-relative key, `""` for the top."""
    text = directory.as_posix()
    return "" if text == "." else text


def _local_workspace_dependencies(
    reader: Reader, manifests: list[str]
) -> dict[str, frozenset[str]]:
    """Return, per workspace root directory, its `[workspace.dependencies]` paths.

    The key is the root's directory relative to the checkout (`""` at the top);
    the value holds the dependency keys that are path dependencies.

    Returns
    -------
    dict[str, frozenset[str]]
        The path-dependency keys of each workspace root, by directory.
    """
    roots: dict[str, frozenset[str]] = {}
    for relative in manifests:
        workspace = table(reader.toml(reader.checkout / relative)).get("workspace")
        if isinstance(workspace, dict):
            dependencies = table(table(workspace).get("dependencies"))
            directory = pathlib.PurePosixPath(relative).parent
            roots[_directory_key(directory)] = frozenset(
                alias for alias, spec in dependencies.items() if "path" in table(spec)
            )
    return roots


def _declared_root(relative: str, manifest: dict[str, object]) -> str | None:
    """Return the directory key a package names with `package.workspace`, if any."""
    declared = table(manifest.get("package")).get("workspace")
    if not isinstance(declared, str):
        return None
    directory = pathlib.PurePosixPath(relative).parent
    joined = posixpath.normpath(posixpath.join(directory.as_posix(), declared))
    return _directory_key(pathlib.PurePosixPath(joined))


def _owning_workspace(
    relative: str, manifest: dict[str, object], roots: dict[str, frozenset[str]]
) -> frozenset[str]:
    """Return the inherited path dependencies of the workspace that owns a manifest.

    A package that names its workspace (`package.workspace`, which may be a root
    that is not an ancestor) belongs to it. Otherwise the owner is the nearest
    manifest at or above the package's directory that has a `[workspace]`
    table, as Cargo searches. A package under no workspace inherits nothing, so
    an unrelated workspace's dependencies cannot make its registry dependency
    look local.

    Returns
    -------
    frozenset[str]
        The owning workspace's path-dependency keys, or none.
    """
    declared = _declared_root(relative, manifest)
    if declared is not None:
        # A named root the scan did not find gives no ancestor's dependencies.
        return roots.get(declared, frozenset())
    directory = pathlib.PurePosixPath(relative).parent
    for candidate in (directory, *directory.parents):
        key = _directory_key(candidate)
        if key in roots:
            return roots[key]
    return frozenset()


def _crate_names(
    reader: Reader, relative: str, roots: dict[str, frozenset[str]]
) -> set[str]:
    """Return the normalised crate names one manifest and its directory hold."""
    path = reader.checkout / relative
    manifest = reader.toml(path)
    if manifest is None:
        return set()
    declared = _declared_root(relative, manifest)
    if declared is not None and declared not in roots:
        reader.unreadable.append(
            f"{relative}: names the workspace root {declared or '.'!r} with "
            "package.workspace, which the audit cannot inspect (outside the "
            "checkout, or no [workspace] there), so the path dependencies this "
            "package inherits are unknown"
        )
    inherited = _owning_workspace(relative, manifest, roots)
    names = _declared_names(manifest, inherited) | discovered_targets(
        path.parent, manifest, reader
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
    roots = _local_workspace_dependencies(reader, manifests)
    names: dict[str, set[str]] = {GOVERNED: set(), FIXTURE: set()}
    for relative in manifests:
        kind = FIXTURE if _is_fixture(relative) else GOVERNED
        names[kind] |= _crate_names(reader, relative, roots)
    # A name found in both kinds is governed, so the governed names go last.
    return {
        **dict.fromkeys(names[FIXTURE], FIXTURE),
        **dict.fromkeys(names[GOVERNED], GOVERNED),
    }
