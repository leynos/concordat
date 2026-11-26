"""Read the shape of a checkout's root Cargo workspace.

`gate-workspace-coverage` asks whether a gate command reaches every workspace
member, so it needs the member set the way Cargo defines it, and the narrower
set a bare command selects: the root package of a workspace that has one, or
`workspace.default-members` when the manifest names them.

The member set is `cargo metadata --no-deps`'s: the root package, the
directories `workspace.members` selects minus `workspace.exclude`, and the
crates inside the root that a member depends on by path. That walk is
`test_environment_members.member_directories`, shared so the two rules cannot
disagree about what a workspace contains. Package names come from each
manifest's `[package]` table; a member whose name cannot be read is recorded as
unreadable, never guessed.

Only the root manifest is read. A gate command that names another manifest, or
runs in another directory, is judged by `gate_scope_commands` and reported as
indeterminate there.
"""

from __future__ import annotations

import glob
import typing as typ

from .markdown_envelope import within_checkout
from .test_environment_members import member_directories
from .test_environment_types import (
    CARGO_MANIFEST,
    OPERATION_READ_ENVIRONMENT_CONFIG,
    strings,
    table,
)

if typ.TYPE_CHECKING:
    import pathlib

    from .test_environment_types import Reader

__all__ = ["WorkspaceFact", "workspace_fact"]


class WorkspaceFact(typ.TypedDict):
    """The root workspace: its members, and what a bare command selects.

    `kind` is `single` for a package with no `[workspace]`, `root-package` for
    a workspace whose root is also a package, and `virtual` for one whose root
    holds only the `[workspace]` table. `default_members` is `None` when the
    manifest does not set `workspace.default-members`.
    """

    manifest: str
    kind: str
    root_package: str | None
    members: list[str]
    default_members: list[str] | None


def _package_name(reader: Reader, directory: pathlib.Path) -> str | None:
    """Return the `[package].name` of the crate in *directory*, or ``None``."""
    manifest = reader.toml(directory / CARGO_MANIFEST)
    if manifest is None:
        # The reader has already recorded why the file could not be decoded.
        return None
    name = table(manifest.get("package")).get("name")
    if isinstance(name, str):
        return name
    reader.unreadable.append(
        f"{reader.relative(directory / CARGO_MANIFEST)}: no [package] name, so the "
        "audit cannot say which workspace member it is"
    )
    return None


def _names(reader: Reader, directories: list[pathlib.Path]) -> list[str]:
    """Return the sorted package names of *directories*, skipping unnamed ones."""
    found = (_package_name(reader, directory) for directory in directories)
    return sorted({name for name in found if name is not None})


def _default_directories(
    reader: Reader, workspace: dict[str, object]
) -> list[pathlib.Path]:
    """Return the directories `workspace.default-members` selects.

    The entries are paths or globs relative to the root, as `members` is, and a
    match outside the checkout is dropped by the containment guard.

    Returns
    -------
    list[pathlib.Path]
        The default members' directories, sorted.
    """
    matched = {
        reader.checkout / name
        for pattern in strings(workspace.get("default-members"))
        for name in glob.glob(pattern, root_dir=reader.checkout, recursive=True)  # noqa: PTH207 - Path.glob rejects "."
    }
    return sorted(
        path
        for path in matched
        if within_checkout(reader.root, path, OPERATION_READ_ENVIRONMENT_CONFIG)
        and (path / CARGO_MANIFEST).is_file()
    )


def workspace_fact(reader: Reader) -> WorkspaceFact | None:
    """Return the root workspace's shape, or ``None`` without a root manifest.

    A manifest that cannot be read is recorded on the reader as unreadable and
    yields ``None`` too: the audit then reports that it could not read the
    file, rather than judging commands against a workspace it never saw.

    Returns
    -------
    WorkspaceFact | None
        The root manifest's members and default selection.
    """
    manifest = reader.toml(reader.checkout / CARGO_MANIFEST)
    if manifest is None:
        return None
    root_package = table(manifest.get("package")).get("name")
    root_name = root_package if isinstance(root_package, str) else None
    declared = manifest.get("workspace")
    if not isinstance(declared, dict):
        return {
            "manifest": CARGO_MANIFEST,
            "kind": "single",
            "root_package": root_name,
            "members": [root_name] if root_name else [],
            "default_members": None,
        }
    workspace = typ.cast("dict[str, object]", declared)
    has_defaults = "default-members" in workspace
    return {
        "manifest": CARGO_MANIFEST,
        "kind": "root-package" if "package" in manifest else "virtual",
        "root_package": root_name,
        "members": _names(reader, member_directories(reader, manifest)),
        "default_members": (
            _names(reader, _default_directories(reader, workspace))
            if has_defaults
            else None
        ),
    }
