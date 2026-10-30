"""Discover Cargo manifests that no declared Rust surface governs.

A repository that has not declared `language.rust.surfaces` is read at its root
only by the shared surface resolver. This module finds the other `Cargo.toml`
files, reads them loosely (no resolver, no repository code), and reports the
ones that could matter: a manifest that cannot be read or decoded, or that
declares, or defines for its workspace, a package some recorded relationship
names. Everything else draws nothing, since no relationship could apply to it.
"""

from __future__ import annotations

import os
import pathlib
import tomllib
import typing as typ

from .dependency_family_coverage import mapping

if typ.TYPE_CHECKING:
    import collections.abc as cabc

_CARGO_SECTIONS: typ.Final = ("dependencies", "dev-dependencies", "build-dependencies")


def section_tables(
    body: dict[str, object], target: str | None
) -> list[tuple[str, str | None, dict[str, object]]]:
    """Return the dependency tables of one manifest body or target body."""
    found = ((section, mapping(body.get(section))) for section in _CARGO_SECTIONS)
    return [(section, target, table) for section, table in found if table is not None]


def cargo_tables(
    parsed: dict[str, object],
) -> list[tuple[str, str | None, dict[str, object]]]:
    """Return each dependency table with its section and target."""
    tables = section_tables(parsed, None)
    for target, raw in (mapping(parsed.get("target")) or {}).items():
        tables.extend(section_tables(mapping(raw) or {}, str(target)))
    return tables


def _loose_manifest(nested: pathlib.Path) -> tuple[dict[str, object], str | None]:
    """Return a nested manifest decoded without the surface resolver, or why not.

    A symbolic link is refused rather than followed: the resolver contains
    declared surfaces to the checkout, and a link could lead outside it.

    Returns
    -------
    tuple[dict[str, object], str | None]
        The decoded manifest, or an empty one and the reason it was not read.
    """
    if nested.is_symlink():
        return {}, "is a symbolic link"
    try:
        return tomllib.loads(nested.read_text(encoding="utf-8")), None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as error:
        return {}, str(error)


def _inherited_for(
    manifest: pathlib.PurePosixPath,
    documents: dict[pathlib.PurePosixPath, dict[str, object]],
    governed: dict[str, object],
) -> dict[str, object]:
    """Return the `[workspace.dependencies]` a nested manifest can inherit.

    The nearest ancestor manifest that defines a workspace wins over the
    governed workspace's own table, as Cargo resolves membership.

    Returns
    -------
    dict[str, object]
        The workspace dependency table a member of *manifest* inherits from.
    """
    for directory in manifest.parents:
        workspace = mapping(
            (documents.get(directory / "Cargo.toml") or {}).get("workspace")
        )
        listed = mapping((workspace or {}).get("dependencies"))
        if listed is not None:
            return {**governed, **listed}
    return governed


def _declared_packages(
    parsed: dict[str, object], inherited: dict[str, object]
) -> set[str]:
    """Return the published names a manifest declares or defines for its workspace.

    Returns
    -------
    set[str]
        Names used in its dependency tables and defined in its workspace table.
    """
    workspace = mapping((mapping(parsed.get("workspace")) or {}).get("dependencies"))
    defined = {
        published_name(key, value, {}) for key, value in (workspace or {}).items()
    }
    used = {
        published_name(key, value, inherited)
        for _, _, table in cargo_tables(parsed)
        for key, value in table.items()
    }
    return defined | used


def ungoverned_problems(
    checkout: pathlib.Path,
    nested: list[pathlib.Path],
    members: cabc.Collection[str],
    governed: dict[str, object],
) -> list[str]:
    """Return the problems posed by nested manifests no declared surface governs.

    A manifest that cannot be read or decoded is a problem, since nothing can
    be said about what it declares. One that decodes is a problem only when it
    declares, or defines for its workspace, a package some recorded
    relationship names: otherwise no relationship could apply to it and its
    absence from the audit hides nothing.

    Returns
    -------
    list[str]
        One sentence per manifest that matters.
    """
    loaded = {
        pathlib.PurePosixPath(path.relative_to(checkout).as_posix()): _loose_manifest(
            path
        )
        for path in nested
    }
    documents = {path: doc for path, (doc, error) in loaded.items() if error is None}
    found: list[str] = []
    for path, (doc, error) in sorted(loaded.items()):
        outside = f"{path} is a Cargo manifest outside the governed Rust surfaces"
        if error is not None:
            found.append(f"{outside} and cannot be read: {error}")
            continue
        related = sorted(
            _declared_packages(doc, _inherited_for(path, documents, governed))
            & set(members)
        )
        if related:
            found.append(
                f"{outside} and declares {', '.join(related)}; "
                "declare language.rust.surfaces in .concordat to read it"
            )
    return found


def published_name(key: str, value: object, inherited: dict[str, object]) -> str:
    """Return the published package name behind a Cargo dependency key."""
    table = mapping(value)
    if table is not None and table.get("workspace") is True:
        table = mapping(inherited.get(key)) or table
    if table is not None and isinstance(table.get("package"), str):
        return str(table["package"])
    return key


_SKIPPED_DIRECTORIES = frozenset({
    "node_modules",
    "target",
    "venv",
    "build",
    "dist",
    "vendor",
})


def walk_files(
    checkout: pathlib.Path, problems: list[str], *, label: str | None = None
) -> list[pathlib.Path]:
    """Return every file below the root that the audit may enter, in path order.

    Hidden directories and vendored or generated trees are not projects and are
    not entered. A directory the walk cannot list is a problem fact, never a
    silently shorter list.

    Parameters
    ----------
    checkout:
        The repository root to walk.
    problems:
        The fact list; an unlistable directory appends one sentence.
    label:
        What the walk was looking for, named in the problem sentence. A caller
        that reads every file leaves it out.

    Returns
    -------
    list[pathlib.Path]
        The discovered files, sorted.
    """

    def unreadable(error: OSError) -> None:
        where = pathlib.Path(error.filename or ".").relative_to(checkout).as_posix()
        looking_for = f" for {label}" if label is not None else ""
        message = f"cannot scan {where}{looking_for}: {error.strerror}"
        if message not in problems:
            problems.append(message)

    found: list[pathlib.Path] = []
    for directory, subdirectories, files in os.walk(checkout, onerror=unreadable):
        subdirectories[:] = [
            entry
            for entry in subdirectories
            if not entry.startswith(".") and entry not in _SKIPPED_DIRECTORIES
        ]
        found.extend(pathlib.Path(directory) / name for name in files)
    return sorted(found)


def nested_files(
    checkout: pathlib.Path, name: str, problems: list[str]
) -> list[pathlib.Path]:
    """Return every file called *name* below the root, at any depth.

    Hidden directories and vendored or generated trees are not projects and are
    not entered. A directory the walk cannot list is a problem fact, never a
    silently shorter list.

    Returns
    -------
    list[pathlib.Path]
        The nested files, in path order.
    """
    return [
        path
        for path in walk_files(checkout, problems, label=name)
        if path.name == name and path.parent != checkout
    ]
