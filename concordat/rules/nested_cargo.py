"""Share the repository facts the nested-cargo rule packages read.

A nested build is a test (or a tool a test runs) that invokes `cargo` against a
manifest of its own: a fixture crate, a probe, a packaged copy. The rule
packages that audit them (NC-001 onward) agree on three questions, answered
here once so no two packages answer them differently.

- Which manifests does a workspace own? A workspace member shares its root's
  `Cargo.lock`, so a lock file inside a member directory is never read by Cargo
  and a manifest there needs no lock of its own.
- Which directories are fixtures? The audit reaches only paths that say so
  (`fixture`, `tests/`, `test_support`, `ui/`). A separately resolved product
  such as a fuzz target or a WebAssembly component is not a nested-build
  fixture, and judging it would only generate exemptions.
- Which repository is this? The `origin` slug lets a rule's parameters name an
  exemption for one repository without it travelling to another.

Nothing here runs Cargo or reads the network.
"""

from __future__ import annotations

import re
import tomllib
import typing as typ

import pygit2

from concordat.platform_standards import parse_github_slug

from .dependency_family_discovery import cargo_tables, nested_files
from .fs_probe import probe_file

if typ.TYPE_CHECKING:
    import pathlib

MANIFEST: typ.Final = "Cargo.toml"
LOCKFILE: typ.Final = "Cargo.lock"
FIXTURE_DIRECTORY: typ.Final = re.compile(r"fixture|(^|/)tests?/|test_support|(^|/)ui/")
DEPENDENCY_TABLES: typ.Final = (
    "dependencies",
    "dev-dependencies",
    "build-dependencies",
)

type Manifest = dict[str, object]


def origin_slug(checkout: pathlib.Path) -> str | None:
    """Return the checkout's GitHub `owner/name` slug from its origin remote.

    Returns
    -------
    str | None
        The slug, or None when there is no repository, no origin, an origin
        that is not a GitHub URL, or a repository git cannot open. A checkout
        without a slug cannot match a named exemption and is fully audited.
    """
    try:
        discovered = pygit2.discover_repository(str(checkout))
        if discovered is None:
            return None
        origin = pygit2.Repository(discovered).remotes["origin"]
    except (KeyError, pygit2.GitError):
        return None
    return parse_github_slug(origin.url) if origin.url else None


def is_fixture_directory(directory: str) -> bool:
    """Report whether a root-relative directory path names a nested-build fixture.

    Returns
    -------
    bool
        True when the path says it holds a fixture.
    """
    return FIXTURE_DIRECTORY.search(directory) is not None


def read_toml(path: pathlib.Path) -> tuple[Manifest, str | None]:
    """Return a decoded TOML file and the reason it could not be decoded.

    Returns
    -------
    tuple[Manifest, str | None]
        The document (empty on failure) and the failure, or None.
    """
    try:
        return tomllib.loads(path.read_text(encoding="utf-8")), None
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        return {}, f"cannot read {path.name}: {error}"


def has_dependency(manifest: Manifest) -> bool:
    """Report whether a manifest declares any dependency, path-only ones included.

    A path dependency still brings its own registry dependencies into the
    resolution, so a fixture with only path dependencies can drift.

    Returns
    -------
    bool
        True when any dependency table is non-empty, target-specific tables
        (`[target.'cfg(...)'.dependencies]`) included.
    """
    return any(table for _, _, table in cargo_tables(manifest))


def _candidates(root: pathlib.Path, pattern: str) -> list[pathlib.Path]:
    """Return the paths one `members` entry names, glob or literal.

    A member such as `.` (the root itself) is not a glob `pathlib` accepts, so
    an entry it refuses is read as a literal path instead.

    Returns
    -------
    list[pathlib.Path]
        The matching paths, which may not exist.
    """
    try:
        return list(root.glob(pattern))
    except (ValueError, NotImplementedError):
        return [root / pattern]


def _is_excluded(path: pathlib.Path, skip: set[pathlib.Path]) -> bool:
    """Report a path equal to, or below, an excluded directory (Cargo's rule)."""
    return any(path == item or item in path.parents for item in skip)


def _is_kept(
    resolved: pathlib.Path, skip: set[pathlib.Path], base: pathlib.Path
) -> bool:
    """Report a member that is not excluded and lies inside the checkout."""
    return not _is_excluded(resolved, skip) and resolved.is_relative_to(base)


def _members_of(
    checkout: pathlib.Path, root: pathlib.Path, workspace: Manifest
) -> set[pathlib.Path]:
    """Return the member directories one workspace manifest lists.

    Members that resolve outside the checkout are not this repository's and are
    left out.

    Returns
    -------
    set[pathlib.Path]
        The member directories, as paths under the workspace root.
    """
    patterns = workspace.get("members")
    excluded = workspace.get("exclude")
    skip = (
        {(root / str(item)).resolve() for item in excluded}
        if isinstance(excluded, list)
        else set()
    )
    base = checkout.resolve()
    found: set[pathlib.Path] = set()
    for pattern in patterns if isinstance(patterns, list) else []:
        for candidate in _candidates(root, str(pattern)):
            resolved = candidate.resolve()
            if resolved.is_dir() and _is_kept(resolved, skip, base):
                found.add(candidate)
    return found


def workspace_member_directories(
    checkout: pathlib.Path, manifests: typ.Mapping[pathlib.Path, Manifest]
) -> set[str]:
    """Return every root-relative directory that some workspace lists as a member.

    Parameters
    ----------
    checkout:
        The repository root.
    manifests:
        Each decoded `Cargo.toml` below and at the root, by absolute path.

    Returns
    -------
    set[str]
        POSIX directories, relative to the checkout, of the workspace members
        (a workspace root is not a member of itself).
    """
    members: set[str] = set()
    for path, document in manifests.items():
        workspace = document.get("workspace")
        if not isinstance(workspace, dict):
            continue
        members.update(
            member.relative_to(checkout).as_posix()
            for member in _members_of(
                checkout, path.parent, typ.cast("Manifest", workspace)
            )
            if member != path.parent
        )
    return members


def _excluded_directories(
    manifests: typ.Mapping[pathlib.Path, Manifest],
) -> set[pathlib.Path]:
    """Return every directory a workspace root explicitly excludes.

    A directory below an excluded one is excluded too, which `_is_excluded`
    applies.

    Returns
    -------
    set[pathlib.Path]
        The resolved excluded directories.
    """
    excluded: set[pathlib.Path] = set()
    for path, document in manifests.items():
        workspace = document.get("workspace")
        listed = workspace.get("exclude") if isinstance(workspace, dict) else None
        if isinstance(listed, list):
            excluded.update((path.parent / str(item)).resolve() for item in listed)
    return excluded


def standalone_directories(
    checkout: pathlib.Path, manifests: typ.Mapping[pathlib.Path, Manifest]
) -> set[str]:
    """Return the directories Cargo can build as a root of their own.

    A package below a workspace root that is neither a member, excluded, nor a
    workspace root itself is refused by Cargo, so nothing can run a nested
    build there and its missing lock draws no cost. What remains is a manifest
    with its own `[workspace]` table, an excluded directory, or a package with
    no workspace above it.

    Parameters
    ----------
    checkout:
        The repository root.
    manifests:
        Each decoded `Cargo.toml` below and at the root, by absolute path.

    Returns
    -------
    set[str]
        Root-relative POSIX directories of the buildable standalone manifests.
    """
    excluded = _excluded_directories(manifests)
    roots = [
        path.parent for path, document in manifests.items() if "workspace" in document
    ]
    found: set[str] = set()
    for path, document in manifests.items():
        directory = path.parent.relative_to(checkout).as_posix()
        under_workspace = any(
            root != path.parent and root in path.parent.parents for root in roots
        )
        owns_root = "workspace" in document or _is_excluded(
            path.parent.resolve(), excluded
        )
        if owns_root or not under_workspace:
            found.add(directory)
    return found


def load_manifests(
    checkout: pathlib.Path, problems: list[str]
) -> dict[pathlib.Path, Manifest]:
    """Return every `Cargo.toml` at or below the root, decoded.

    A manifest that cannot be decoded is recorded in *problems* and left out,
    never read as an empty document.

    Returns
    -------
    dict[pathlib.Path, Manifest]
        The decoded manifests by absolute path.
    """
    paths = [checkout / MANIFEST, *nested_files(checkout, MANIFEST, problems)]
    loaded: dict[pathlib.Path, Manifest] = {}
    for path in paths:
        probe = probe_file(path)
        if probe.read_error is not None:
            problems.append(
                f"{path.relative_to(checkout).as_posix()}: {probe.read_error}"
            )
            continue
        if not probe.present:
            continue
        document, error = read_toml(path)
        if error is not None:
            problems.append(f"{path.relative_to(checkout).as_posix()}: {error}")
            continue
        loaded[path] = document
    return loaded
