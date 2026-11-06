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
from .dependency_family_workspace_reader import dependency_table, read_workspace


def _workspace_candidates(
    surfaces: list[dict[str, object]],
) -> tuple[set[pathlib.PurePosixPath], set[pathlib.PurePosixPath]]:
    """Return the directories that may hold a member's workspace, and the named ones.

    A member naming its workspace belongs to that one alone, so its ancestors are
    not probed on its behalf: a malformed unrelated manifest above it says
    nothing about the member.

    Returns
    -------
    tuple[set[pathlib.PurePosixPath], set[pathlib.PurePosixPath]]
        Every directory to read, and the subset a `package.workspace` names.
    """
    candidates: set[pathlib.PurePosixPath] = set()
    named_roots: set[pathlib.PurePosixPath] = set()
    for surface in surfaces:
        parent = pathlib.PurePosixPath(str(surface["path"])).parent
        named = _named_workspace(parent, mapping(surface.get("parsed")) or {})
        if named is None:
            candidates.update({parent, *parent.parents})
        else:
            candidates.add(named)
            named_roots.add(named)
    return candidates, named_roots


def workspace_tables(
    checkout: pathlib.Path,
    surfaces: list[dict[str, object]],
    problems: list[str],
) -> dict[pathlib.PurePosixPath, dict[str, object]]:
    """Return each workspace root's `[workspace.dependencies]`, by directory.

    A governed member may belong to a workspace whose root is not itself a
    governed surface, so the directories above each surface, and any one a
    `package.workspace` names, are read from the checkout too. What cannot be
    read is appended to *problems*, never passed over as an absence.

    Returns
    -------
    dict[pathlib.PurePosixPath, dict[str, object]]
        The dependency table of every workspace root found.
    """
    governed = {
        pathlib.PurePosixPath(str(surface["path"])).parent: table
        for surface in surfaces
        if (table := dependency_table(mapping(surface.get("parsed")) or {})) is not None
    }
    candidates, named_roots = _workspace_candidates(surfaces)
    read = {
        directory: read_workspace(
            checkout, directory, is_named=directory in named_roots
        )
        for directory in sorted(candidates - governed.keys())
    }
    problems.extend(problem for _, problem in read.values() if problem is not None)
    found = {
        directory: table for directory, (table, _) in read.items() if table is not None
    }
    return governed | found


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
