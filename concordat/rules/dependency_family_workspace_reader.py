"""Read one workspace root's `[workspace.dependencies]`, or say why it cannot be.

A workspace root that exists but cannot be used is a problem, never a silent
absence, and one that resolves outside the checkout (through a symbolic link in
any directory above it, or a `..`) is refused unread.
"""

from __future__ import annotations

import typing as typ

from .dependency_family_coverage import mapping
from .dependency_family_discovery import loose_manifest
from .fs_probe import probe_file

if typ.TYPE_CHECKING:
    import pathlib


def dependency_table(parsed: dict[str, object]) -> dict[str, object] | None:
    """Return `[workspace.dependencies]`, or ``None`` if there is no workspace."""
    workspace = mapping(parsed.get("workspace"))
    if workspace is None:
        return None
    return mapping(workspace.get("dependencies")) or {}


def _locate_manifest(
    checkout: pathlib.Path, directory: pathlib.PurePosixPath, *, is_named: bool
) -> tuple[pathlib.Path | None, str | None]:
    """Find the manifest in *directory* without leaving the checkout.

    Returns
    -------
    tuple[pathlib.Path | None, str | None]
        The manifest path, or ``None`` with a problem sentence when the evidence
        is incomplete, or ``None`` alone when an ancestor simply has no manifest.
    """
    name = (directory / "Cargo.toml").as_posix()
    outside = f"{name} is a workspace manifest outside the checkout"
    if directory.is_absolute() or ".." in directory.parts:
        return None, outside
    manifest = checkout / directory / "Cargo.toml"
    probe = probe_file(manifest)
    if probe.read_error is not None:
        return (
            None,
            f"{name} cannot be examined as a workspace manifest: {probe.read_error}",
        )
    if not probe.present:
        return (
            None,
            f"{name} is named by package.workspace but is not there"
            if is_named
            else None,
        )
    if not manifest.resolve().is_relative_to(checkout.resolve()):
        return None, outside
    return manifest, None


def read_workspace(
    checkout: pathlib.Path, directory: pathlib.PurePosixPath, *, is_named: bool
) -> tuple[dict[str, object] | None, str | None]:
    """Read the workspace manifest in *directory*: its table, or why not.

    A manifest that exists but cannot be used is a problem, never a silent
    absence. A directory that a `package.workspace` names must hold a
    workspace; an ancestor need not.

    Returns
    -------
    tuple[dict[str, object] | None, str | None]
        The `[workspace.dependencies]` table, or ``None`` with a problem
        sentence when the evidence is incomplete, or ``None`` with no problem
        when the directory is simply not a workspace root.
    """
    manifest, problem = _locate_manifest(checkout, directory, is_named=is_named)
    if manifest is None:
        return None, problem
    name = (directory / "Cargo.toml").as_posix()
    document, error = loose_manifest(manifest)
    if error is not None:
        return None, f"{name} is a workspace manifest and cannot be read: {error}"
    table = dependency_table(document)
    if table is None and is_named:
        return (
            None,
            f"{name} is named by package.workspace but is not a workspace manifest",
        )
    return table, None
