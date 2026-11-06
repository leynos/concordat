"""Resolve which `[workspace.dependencies]` table a Cargo manifest inherits from.

Cargo resolves `workspace = true` against the workspace a member belongs to: the
one its `package.workspace` names, else the nearest ancestor workspace. The
dependency-family collector needs that table to tell a path-only inherited
dependency from a versioned one, and it must not borrow a sibling workspace's
entry for the same key. A workspace root need not be a governed surface, so the
directories above each surface are read from the checkout as well.
"""

from __future__ import annotations

import pathlib
import posixpath

from .dependency_family_coverage import mapping
from .dependency_family_discovery import loose_manifest
from .fs_probe import probe_file


def _dependency_table(parsed: dict[str, object]) -> dict[str, object] | None:
    """Return `[workspace.dependencies]`, or ``None`` if there is no workspace."""
    workspace = mapping(parsed.get("workspace"))
    if workspace is None:
        return None
    return mapping(workspace.get("dependencies")) or {}


def _workspace_candidates(
    surfaces: list[dict[str, object]],
) -> set[pathlib.PurePosixPath]:
    """Return the directories that may hold a governed member's workspace root."""
    candidates: set[pathlib.PurePosixPath] = set()
    for surface in surfaces:
        parent = pathlib.PurePosixPath(str(surface["path"])).parent
        named = _named_workspace(parent, mapping(surface.get("parsed")) or {})
        candidates.update({parent, *parent.parents})
        if named is not None:
            candidates.add(named)
    return candidates


def workspace_tables(
    checkout: pathlib.Path,
    surfaces: list[dict[str, object]],
    problems: list[str],
) -> dict[pathlib.PurePosixPath, dict[str, object]]:
    """Return each workspace root's `[workspace.dependencies]`, by directory.

    A governed member may belong to a workspace whose root is not itself a
    governed surface, so the directories above each surface, and any one a
    `package.workspace` names, are read from the checkout too.

    Returns
    -------
    dict[pathlib.PurePosixPath, dict[str, object]]
        The dependency table of every workspace root found.
    """
    governed = {
        pathlib.PurePosixPath(str(surface["path"])).parent: table
        for surface in surfaces
        if (table := _dependency_table(mapping(surface.get("parsed")) or {}))
        is not None
    }
    found = {
        directory: table
        for directory in sorted(_workspace_candidates(surfaces) - governed.keys())
        if (
            table := _dependency_table(
                _workspace_document(checkout, directory, problems)
            )
        )
        is not None
    }
    return governed | found


def _workspace_document(
    checkout: pathlib.Path, directory: pathlib.PurePosixPath, problems: list[str]
) -> dict[str, object]:
    """Return the manifest in *directory*, or an empty one when it is not usable.

    A manifest that exists but cannot be used is a problem, never a silent
    absence: the dependency facts that depend on it would otherwise look
    complete. One that resolves outside the checkout, through a symbolic link
    in any directory above it, is refused for the same reason it is refused
    elsewhere.

    Returns
    -------
    dict[str, object]
        The decoded manifest, or an empty one.
    """
    if directory.is_absolute() or ".." in directory.parts:
        return {}
    manifest = checkout / directory / "Cargo.toml"
    if not probe_file(manifest).present:
        return {}
    name = (directory / "Cargo.toml").as_posix()
    if not manifest.resolve().is_relative_to(checkout.resolve()):
        problems.append(f"{name} is a workspace manifest outside the checkout")
        return {}
    document, error = loose_manifest(manifest)
    if error is not None:
        problems.append(f"{name} is a workspace manifest and cannot be read: {error}")
        return {}
    return document


def inherited_for(
    manifest: str,
    parsed: dict[str, object],
    workspaces: dict[pathlib.PurePosixPath, dict[str, object]],
    everything: dict[str, object],
) -> dict[str, object]:
    """Return the `[workspace.dependencies]` a manifest's own workspace offers.

    Cargo resolves `workspace = true` against the workspace the member belongs
    to: the one `package.workspace` names, else the nearest ancestor workspace.
    A member must not inherit from a sibling workspace that happens to share a
    key. A manifest under no declared workspace keeps the combined table.

    Returns
    -------
    dict[str, object]
        The inheritable dependency table for *manifest*.
    """
    parent = pathlib.PurePosixPath(manifest).parent
    named = _named_workspace(parent, parsed)
    if named is not None:
        return workspaces.get(named, {})
    return next(
        (workspaces[d] for d in (parent, *parent.parents) if d in workspaces),
        everything,
    )


def _named_workspace(
    parent: pathlib.PurePosixPath, parsed: dict[str, object]
) -> pathlib.PurePosixPath | None:
    """Return the directory `package.workspace` names, or ``None`` when unset."""
    named = (mapping(parsed.get("package")) or {}).get("workspace")
    if not isinstance(named, str):
        return None
    return pathlib.PurePosixPath(posixpath.normpath(str(parent / named)))
