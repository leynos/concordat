"""Resolve which `[workspace.dependencies]` table a Cargo manifest inherits from.

Cargo resolves `workspace = true` against the workspace a member belongs to: the
one its `package.workspace` names, else the nearest ancestor workspace. The
dependency-family collector needs that table to tell a path-only inherited
dependency from a versioned one, and it must not borrow a sibling workspace's
entry for the same key. A workspace root need not be a governed surface, so the
directories above a surface are read from the checkout, nearest first, and only
as far as the first workspace: an unrelated manifest further up says nothing
about the member. A member no workspace owns is reported, not guessed at.
"""

from __future__ import annotations

import dataclasses
import pathlib
import posixpath

from .dependency_family_coverage import mapping
from .dependency_family_discovery import cargo_tables
from .dependency_family_workspace_reader import dependency_table, read_workspace

type Table = dict[str, object]


def _named_workspace(
    parent: pathlib.PurePosixPath, parsed: dict[str, object]
) -> pathlib.PurePosixPath | None:
    """Return the directory `package.workspace` names, or ``None`` when unset."""
    named = (mapping(parsed.get("package")) or {}).get("workspace")
    if not isinstance(named, str):
        return None
    return pathlib.PurePosixPath(posixpath.normpath(str(parent / named)))


def _uses_inheritance(parsed: dict[str, object]) -> bool:
    """Return whether any dependency, target-specific ones included, inherits."""
    return any(
        (mapping(value) or {}).get("workspace") is True
        for _, _, table in cargo_tables(parsed)
        for value in table.values()
    )


@dataclasses.dataclass(slots=True)
class _Resolver:
    """Reads each directory's workspace table at most once.

    ``governed`` holds the tables of workspace roots that are governed surfaces;
    everything else is read from the checkout, and what cannot be read is
    appended to ``problems`` exactly once.
    """

    checkout: pathlib.Path
    governed: dict[pathlib.PurePosixPath, Table]
    problems: list[str]
    seen: dict[tuple[pathlib.PurePosixPath, bool], Table | None] = dataclasses.field(
        default_factory=dict
    )

    def table_at(
        self, directory: pathlib.PurePosixPath, *, is_named: bool
    ) -> Table | None:
        """Return the workspace table in *directory*, or ``None`` if it has none."""
        if directory in self.governed:
            return self.governed[directory]
        key = (directory, is_named)
        if key not in self.seen:
            table, problem = read_workspace(self.checkout, directory, is_named=is_named)
            if problem is not None:
                self.problems.append(problem)
            self.seen[key] = table
        return self.seen[key]

    def owner_table(self, manifest: str, parsed: dict[str, object]) -> Table | None:
        """Return the table of the workspace that owns *manifest*, if any."""
        parent = pathlib.PurePosixPath(manifest).parent
        named = _named_workspace(parent, parsed)
        if named is not None:
            return self.table_at(named, is_named=True)
        for directory in (parent, *parent.parents):
            table = self.table_at(directory, is_named=False)
            if table is not None:
                return table
        return None


def inherited_tables(
    checkout: pathlib.Path,
    surfaces: list[dict[str, object]],
    problems: list[str],
) -> dict[str, Table]:
    """Return, per surface path, the dependency table that surface inherits from.

    A surface that inherits (`workspace = true`) but belongs to no workspace
    adds a problem and gets an empty table, so its inherited entries are kept as
    declarations rather than judged against another workspace's.

    Returns
    -------
    dict[str, Table]
        The inheritable `[workspace.dependencies]` for each surface path.
    """
    parsed = {str(s["path"]): mapping(s.get("parsed")) or {} for s in surfaces}
    governed = {
        pathlib.PurePosixPath(path).parent: table
        for path, doc in parsed.items()
        if (table := dependency_table(doc)) is not None
    }
    resolver = _Resolver(checkout, governed, problems)
    result: dict[str, Table] = {}
    for path, doc in parsed.items():
        uses = _uses_inheritance(doc)
        # A surface that inherits nothing and names no workspace has no use for
        # one, so ancestors are not probed on its behalf.
        needs_owner = uses or _named_workspace(pathlib.PurePosixPath(path).parent, doc)
        table = resolver.owner_table(path, doc) if needs_owner else None
        if table is None and uses:
            problems.append(f"{path} inherits from a workspace that could not be found")
        result[path] = table or {}
    return result
