"""Read the lint tables every Cargo package of a checkout ends up with.

`rust-lint-tables` asks of every package the estate governs what Cargo itself
would ask: which lint levels does this package compile under? That is not what
the package's own `[lints]` says. A package that sets `[lints] workspace = true`
takes the `[workspace.lints]` of the workspace that contains it, a package with
no `[lints]` table takes nothing (even in a workspace that has lint tables), and
a package outside every workspace takes only its own tables. This module
resolves that, once, so the policy compares plain facts.

Packages are found by walking the checkout for every `Cargo.toml` outside the
fixture, vendored and build-output trees that `documentation-presence` also
skips, so a standalone crate such as `rust_extension/Cargo.toml` in a Python
project is governed the same way as a workspace member. Workspace membership
uses the same member rules as `gate-workspace-coverage`
(`test_environment_members.member_directories`), applied to each workspace
root, not only the checkout's root.

Nothing here decides compliance. A manifest that cannot be read is recorded as
unreadable, and the policy reports it as indeterminate.
"""

from __future__ import annotations

import pathlib
import typing as typ

from .documentation_rust import manifest_paths
from .lint_baseline import BASELINE_TABLES
from .test_environment_members import member_directories
from .test_environment_types import Reader, table

__all__ = ["LintEntry", "PackageFacts", "read_packages"]

_DEPENDENCY_TABLES: typ.Final = (
    "dependencies",
    "dev-dependencies",
    "build-dependencies",
)
_PYO3: typ.Final = "pyo3"


class LintEntry(typ.TypedDict):
    """One lint as a table sets it; `level` is None for a value of an unknown shape."""

    level: str | None
    priority: int | None


class PackageFacts(typ.TypedDict):
    """One governed Cargo package and the lint tables it compiles under.

    `state` is one of:

    - `inherited`: `[lints] workspace = true` under a workspace with lint tables;
    - `own`: the package's own `[lints.*]` tables apply;
    - `dangling`: `[lints] workspace = true`, but no workspace above it has
      `[workspace.lints]`;
    - `not_opted_in`: its workspace has `[workspace.lints]` and the package has
      no lint tables of its own and no `workspace = true`;
    - `none`: no lint tables apply at all.

    `workspace` is the manifest of the workspace that contains the package, or
    None for a package outside every workspace.
    """

    name: str
    manifest: str
    directory: str
    state: str
    workspace: str | None
    workspace_has_lints: bool
    effective: dict[str, dict[str, LintEntry]]
    depends_on_pyo3: bool


def _entry(value: object) -> LintEntry:
    """Return the level and priority one `[lints.*]` value sets."""
    if isinstance(value, str):
        return {"level": value, "priority": None}
    entry = table(value)
    level = entry.get("level")
    priority = entry.get("priority")
    return {
        "level": level if isinstance(level, str) else None,
        "priority": priority if isinstance(priority, int) else None,
    }


def _normalised(lints: dict[str, object]) -> dict[str, dict[str, LintEntry]]:
    """Return the baseline's three tables of *lints*, each lint as an entry."""
    return {
        name: {lint: _entry(value) for lint, value in table(lints.get(name)).items()}
        for name in BASELINE_TABLES
    }


def _own_tables(manifest: dict[str, object]) -> dict[str, object]:
    """Return the package's own `[lints.*]` tables, without the `workspace` key."""
    return {
        key: value
        for key, value in table(manifest.get("lints")).items()
        if isinstance(value, dict)
    }


def _dependency_tables(manifest: dict[str, object]) -> typ.Iterator[dict[str, object]]:
    """Yield every dependency table of a manifest, target-specific ones too."""
    yield from (table(manifest.get(name)) for name in _DEPENDENCY_TABLES)
    for target in table(manifest.get("target")).values():
        yield from (table(table(target).get(name)) for name in _DEPENDENCY_TABLES)


def _depends_on_pyo3(manifest: dict[str, object]) -> bool:
    """Return whether any dependency table names `pyo3`, directly or renamed."""
    return any(
        key == _PYO3 or table(value).get("package") == _PYO3
        for dependencies in _dependency_tables(manifest)
        for key, value in dependencies.items()
    )


def _directory(path: str) -> str:
    """Return a manifest path's directory as a POSIX path, `""` at the root."""
    parent = pathlib.PurePosixPath(path).parent.as_posix()
    return "" if parent == "." else parent


def _members_of(reader: Reader, path: str, manifest: dict[str, object]) -> set[str]:
    """Return the directories (relative, POSIX) of a workspace root's members.

    Membership is computed with a reader rooted at the workspace, because
    `workspace.members` globs are relative to it; what it could not read is
    carried back with the workspace's directory in front.

    Returns
    -------
    set[str]
        The member directories, `""` for the workspace root itself.
    """
    prefix = _directory(path)
    scoped = Reader(reader.checkout / prefix, reader.root)
    members = {reader.relative(found) for found in member_directories(scoped, manifest)}
    reader.unreadable.extend(
        f"{prefix}/{message}" if prefix else message for message in scoped.unreadable
    )
    return {"" if member == "." else member for member in members}


def _governing_workspace(directory: str, workspaces: dict[str, set[str]]) -> str | None:
    """Return the nearest workspace manifest that has *directory* as a member."""
    candidates = [
        path
        for path, members in workspaces.items()
        if directory in members
        and (
            not _directory(path)
            or directory == _directory(path)
            or directory.startswith(_directory(path) + "/")
        )
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: len(_directory(candidate)))


def _state(manifest: dict[str, object], *, workspace_has_lints: bool) -> str:
    """Return how lint tables reach the package (see `PackageFacts`)."""
    uses_workspace = table(manifest.get("lints")).get("workspace") is True
    if uses_workspace:
        return "inherited" if workspace_has_lints else "dangling"
    if _own_tables(manifest):
        return "own"
    return "not_opted_in" if workspace_has_lints else "none"


def _package_facts(
    path: str,
    manifest: dict[str, object],
    workspaces: dict[str, set[str]],
    parsed: dict[str, dict[str, object]],
) -> PackageFacts:
    """Return one package's facts, resolving its workspace and its lint tables."""
    directory = _directory(path)
    owner = _governing_workspace(directory, workspaces)
    workspace_lints = (
        table(table(parsed[owner].get("workspace")).get("lints")) if owner else {}
    )
    state = _state(manifest, workspace_has_lints=bool(workspace_lints))
    source = workspace_lints if state == "inherited" else _own_tables(manifest)
    return {
        "name": str(table(manifest.get("package")).get("name", directory or ".")),
        "manifest": path,
        "directory": directory,
        "state": state,
        "workspace": owner,
        "workspace_has_lints": bool(workspace_lints),
        "effective": _normalised(source),
        "depends_on_pyo3": _depends_on_pyo3(manifest),
    }


def read_packages(reader: Reader) -> list[PackageFacts]:
    """Return the facts of every governed package under the reader's checkout.

    Parameters
    ----------
    reader:
        Reads TOML inside the checkout and records what it could not read.

    Returns
    -------
    list[PackageFacts]
        One entry per `[package]` manifest outside the pruned trees, sorted by
        manifest path. A virtual workspace root is not a package.
    """
    parsed = {
        path: manifest
        for path in manifest_paths(reader.checkout)
        if (manifest := reader.toml(reader.checkout / path)) is not None
    }
    workspaces = {
        path: _members_of(reader, path, manifest)
        for path, manifest in parsed.items()
        if isinstance(manifest.get("workspace"), dict)
    }
    return [
        _package_facts(path, manifest, workspaces, parsed)
        for path, manifest in sorted(parsed.items())
        if isinstance(manifest.get("package"), dict)
    ]
