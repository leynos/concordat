"""Find a workspace's members and the Clippy configuration each one is linted by.

A member is a crate Cargo lints as part of the workspace: the root package if
it has a `[package]`, each directory `workspace.members` selects (globs
relative to the root, minus `workspace.exclude`), and, when the root has a
`[workspace]`, each crate inside the root that a member depends on by path,
which Cargo enrols automatically. A nested crate that is none of these is
another package (a fixture, an example workspace) and Clippy does not lint it
here.

The Clippy configuration for a member is the one Clippy loads: the first
`.clippy.toml` or `clippy.toml` in the member's directory or the nearest parent
inside the checkout, the dotted name first within one directory. It is not
merged with a file further up, so a member's own file shadows the root's.
"""

from __future__ import annotations

import glob
import os
import pathlib
import typing as typ

from .markdown_envelope import within_checkout
from .test_environment_dependencies import dependencies_of
from .test_environment_types import (
    CARGO_MANIFEST,
    OPERATION_READ_ENVIRONMENT_CONFIG,
    strings,
    table,
)

if typ.TYPE_CHECKING:
    from .test_environment_types import ClippyConfigFact, Reader

__all__ = ["clippy_fact", "member_directories"]

CLIPPY_CONFIG_NAMES: typ.Final = (".clippy.toml", "clippy.toml")


def _is_crate(path: pathlib.Path) -> bool:
    """Return whether *path* is a directory holding a `Cargo.toml`."""
    return path.is_dir() and (path / CARGO_MANIFEST).is_file()


def _expand(reader: Reader, pattern: str) -> list[pathlib.Path]:
    """Return the paths a workspace glob matches that stay inside the checkout."""
    names = sorted(glob.glob(pattern, root_dir=reader.checkout, recursive=True))  # noqa: PTH207 - Path.glob rejects "."
    return [
        path
        for path in (reader.checkout / name for name in names)
        if within_checkout(reader.root, path, OPERATION_READ_ENVIRONMENT_CONFIG)
    ]


def _expand_all(reader: Reader, patterns: list[str]) -> set[pathlib.Path]:
    """Return every path any of the globs matches."""
    return {path for pattern in patterns for path in _expand(reader, pattern)}


def _explicit_members(reader: Reader, manifest: dict[str, object]) -> set[pathlib.Path]:
    """Return the root package and the crates `workspace.members` selects."""
    workspace = table(manifest.get("workspace"))
    excluded = _expand_all(reader, strings(workspace.get("exclude")))
    selected = _expand_all(reader, strings(workspace.get("members")))
    found = {path for path in selected if _is_crate(path) and path not in excluded}
    if "package" in manifest:
        found.add(reader.checkout)
    return found


def _path_targets(
    reader: Reader, directory: pathlib.Path, workspace_dependencies: dict[str, object]
) -> list[pathlib.Path]:
    """Return the crates inside the checkout that a crate depends on by path."""
    manifest = reader.toml(directory / CARGO_MANIFEST) or {}
    targets = [
        pathlib.Path(
            os.path.normpath(
                (reader.checkout if dependency.inherited_path else directory)
                / dependency.path
            )
        )
        for dependency in dependencies_of(manifest, workspace_dependencies)
        if dependency.path is not None
    ]
    return [t for t in targets if t.is_relative_to(reader.checkout) and _is_crate(t)]


def _implicit_members(
    reader: Reader,
    members: set[pathlib.Path],
    manifest: dict[str, object],
) -> set[pathlib.Path]:
    """Return the crates Cargo enrols because a member depends on them by path."""
    workspace = table(manifest.get("workspace"))
    excluded = _expand_all(reader, strings(workspace.get("exclude")))
    workspace_dependencies = table(workspace.get("dependencies"))
    known = set(members)
    pending = list(members)
    while pending:
        directory = pending.pop()
        for target in _path_targets(reader, directory, workspace_dependencies):
            if target not in known and target not in excluded:
                known.add(target)
                pending.append(target)
    return known - members


def member_directories(
    reader: Reader, manifest: dict[str, object]
) -> list[pathlib.Path]:
    """Return the directories of the workspace members.

    Returns
    -------
    list[pathlib.Path]
        The members, sorted and without duplicates. The root is one when it
        has a `[package]`; path dependencies are included only when the root
        has a `[workspace]` table, as Cargo does.
    """
    members = _explicit_members(reader, manifest)
    if "workspace" in manifest:
        members |= _implicit_members(reader, members, manifest)
    return sorted(members)


def _disallowed_paths(config: dict[str, object]) -> list[str]:
    """Return the `path` of each `disallowed-methods` entry, string or table."""
    entries = config.get("disallowed-methods")
    if not isinstance(entries, list):
        return []
    paths = [
        entry if isinstance(entry, str) else table(entry).get("path")
        for entry in entries
    ]
    return sorted({path for path in paths if isinstance(path, str)})


def _config_file(reader: Reader, member: pathlib.Path) -> pathlib.Path | None:
    """Return the file Clippy would load for a member, if any."""
    for directory in (member, *member.parents):
        found = next(
            (
                directory / name
                for name in CLIPPY_CONFIG_NAMES
                if (directory / name).is_file()
            ),
            None,
        )
        if found is not None or directory == reader.checkout:
            return found
    return None


def clippy_fact(reader: Reader, member: pathlib.Path) -> ClippyConfigFact:
    """Return the Clippy configuration Clippy would load for a member.

    Returns
    -------
    ClippyConfigFact
        The file's path and `disallowed-methods` paths, or no path when none
        applies.
    """
    config_file = _config_file(reader, member)
    if config_file is None:
        return {"path": None, "disallowed": [], "readable": True}
    config = reader.toml(config_file)
    return {
        "path": reader.relative(config_file),
        "disallowed": _disallowed_paths(config or {}),
        "readable": config is not None,
    }
