"""Build the facts for the `nested-cargo-fixture-lockfiles` rule (NC-001, NC-002).

A fixture crate that resolves its own dependencies separately from the
workspace compiles different units from the outer build, so a nested `cargo`
run against it starts cold. Two shapes cause it:

- a fixture `Cargo.lock` that resolves a registry package the workspace lock
  does not (NC-001): the fixture is not a subset of the workspace graph;
- a fixture manifest with dependencies and no lock at all (NC-002): Cargo
  resolves it afresh on every build.

The envelope reads the root lock, every fixture lock and every fixture
manifest, and records the facts; the Rego policy judges them and applies the
exemptions the rule's parameters name. A lock inside a workspace member's
directory is never read by Cargo and is ignored. Path packages (no `source`)
are the fixture's own and never count as drift. A checkout whose root has no
`Cargo.lock` has no workspace graph to align to and is compliant.

Anything the builder cannot establish stays a problem fact, so the policy
answers indeterminate rather than reading an unreadable lock as a clean one. A
checkout that cannot be read at all raises an operational error.
"""

from __future__ import annotations

import tomllib
import typing as typ

from .dependency_family_coverage import mapping, sequence
from .dependency_family_discovery import nested_files
from .fs_probe import probe_file
from .nested_cargo import (
    LOCKFILE,
    MANIFEST,
    has_dependency,
    is_fixture_directory,
    load_manifests,
    origin_slug,
    standalone_directories,
    workspace_member_directories,
)

if typ.TYPE_CHECKING:
    import pathlib

    from .codescene_coverage_envelope import Repository
    from .nested_cargo import Manifest

ENVELOPE_SCHEMA_VERSION: typ.Final = 1
ENVELOPE_KIND: typ.Final = "policy-input/nested-cargo-fixture-lockfiles"
FOREIGN_LIMIT: typ.Final = 10

type Package = tuple[str, str, str]


class RootLock(typ.TypedDict):
    """Store whether the workspace lock exists and could be read.

    Attributes
    ----------
    state:
        `present`, `absent` or `unreadable`.
    error:
        Why the lock could not be read, or None.
    """

    state: str
    error: str | None


class NestedLock(typ.TypedDict):
    """Store one fixture lock that resolves packages the workspace lock lacks.

    Attributes
    ----------
    path:
        The lock's path relative to the checkout, in POSIX form.
    directory:
        Its directory, which is what an exemption names.
    foreign_count:
        How many registry packages it resolves that the root lock does not.
    foreign_packages:
        The first few of them as `name version`, sorted.
    """

    path: str
    directory: str
    foreign_count: int
    foreign_packages: list[str]


class UnlockedManifest(typ.TypedDict):
    """Store one fixture manifest with dependencies and no lock beside it.

    Attributes
    ----------
    path:
        The manifest's path relative to the checkout, in POSIX form.
    directory:
        Its directory, which is what an exemption names.
    """

    path: str
    directory: str


class NestedCargoLockEnvelope(typ.TypedDict):
    """Store the evidence evaluated by the nested-cargo lockfile rule.

    Attributes
    ----------
    schema_version:
        The envelope's schema version, which the policy checks.
    kind:
        The policy-input identifier, which the policy checks.
    repository:
        The checkout's path and, where the origin names one, its GitHub slug,
        which the rule's exemptions are matched against.
    root_lock:
        Whether the workspace lock exists and could be read.
    nested_locks:
        Fixture locks that resolve packages the root lock does not.
    unlocked_manifests:
        Fixture manifests with dependencies, outside any workspace, and no lock.
    problems:
        Every fact the builder could not establish, as sentences.
    """

    schema_version: int
    kind: str
    repository: Repository
    root_lock: RootLock
    nested_locks: list[NestedLock]
    unlocked_manifests: list[UnlockedManifest]
    problems: list[str]


def registry_packages(document: Manifest) -> set[Package]:
    """Return the `(name, version, source)` of every package that has a source.

    The source is part of the identity: the same name and version from a git
    repository or another registry is a different unit from the crates.io one,
    and a nested build that resolves it compiles something the workspace did not.

    Returns
    -------
    set[Package]
        The sourced packages; path packages (no source) are the fixture's own.
    """
    found: set[Package] = set()
    for item in sequence(document.get("package")) or []:
        table = mapping(item) or {}
        if "source" in table and "name" in table and "version" in table:
            found.add((
                str(table["name"]),
                str(table["version"]),
                str(table["source"]),
            ))
    return found


def _read_lock(path: pathlib.Path) -> tuple[Manifest | None, str | None]:
    """Return a decoded lock, or None with the reason it could not be read."""
    probe = probe_file(path)
    if probe.read_error is not None:
        return None, probe.read_error
    if not probe.present:
        return None, None
    try:
        return tomllib.loads(path.read_text(encoding="utf-8")), None
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        return None, str(error)


def _root_lock(checkout: pathlib.Path) -> tuple[RootLock, set[Package]]:
    """Return the root lock fact and its registry packages."""
    document, error = _read_lock(checkout / LOCKFILE)
    if error is not None:
        return {"state": "unreadable", "error": error}, set()
    if document is None:
        return {"state": "absent", "error": None}, set()
    return {"state": "present", "error": None}, registry_packages(document)


def _describe(package: Package) -> str:
    """Return `name version`, with the source named when it is not crates.io."""
    name, version, source = package
    crates_io = source.startswith("registry+") and "crates.io" in source
    return f"{name} {version}" if crates_io else f"{name} {version} ({source})"


def _nested_lock(
    checkout: pathlib.Path, path: pathlib.Path, root: set[Package], problems: list[str]
) -> NestedLock | None:
    """Return the fact for one fixture lock, or None when it is a subset."""
    relative = path.relative_to(checkout).as_posix()
    document, error = _read_lock(path)
    if document is None:
        problems.append(f"{relative} cannot be read: {error}")
        return None
    foreign = sorted(
        _describe(package) for package in registry_packages(document) - root
    )
    if not foreign:
        return None
    return {
        "path": relative,
        "directory": path.parent.relative_to(checkout).as_posix(),
        "foreign_count": len(foreign),
        "foreign_packages": foreign[:FOREIGN_LIMIT],
    }


def _nested_locks(
    checkout: pathlib.Path,
    members: set[str],
    root: set[Package],
    problems: list[str],
) -> list[NestedLock]:
    """Return the fixture locks that are not a subset of the root lock."""
    facts = (
        _nested_lock(checkout, path, root, problems)
        for path in nested_files(checkout, LOCKFILE, problems)
        if is_fixture_directory(path.parent.relative_to(checkout).as_posix())
        and path.parent.relative_to(checkout).as_posix() not in members
    )
    return [fact for fact in facts if fact is not None]


def _has_lock_beside(manifest: pathlib.Path) -> bool:
    """Report a lock beside the manifest; an unreadable one counts as present.

    A lock the filesystem refused to describe is not evidence that none exists,
    so it must not draw an "unlocked" finding.

    Returns
    -------
    bool
        True when a lock exists or its state could not be established.
    """
    probe = probe_file(manifest.parent / LOCKFILE)
    return probe.present or probe.read_error is not None


def _needs_lock(manifest: pathlib.Path, document: Manifest) -> bool:
    """Report a package manifest with dependencies and no lock beside it.

    Returns
    -------
    bool
        True when Cargo would resolve the package afresh on every build.
    """
    return (
        "package" in document
        and has_dependency(document)
        and not _has_lock_beside(manifest)
    )


def _unlocked(
    checkout: pathlib.Path,
    manifests: typ.Mapping[pathlib.Path, Manifest],
    members: set[str],
) -> list[UnlockedManifest]:
    """Return fixture manifests that declare dependencies and have no lock.

    Only a manifest Cargo can build as a root of its own counts: one below a
    workspace that is neither a member nor excluded is refused by Cargo, so no
    nested build runs there.

    Returns
    -------
    list[UnlockedManifest]
        The qualifying manifests, in path order.
    """
    standalone = standalone_directories(checkout, manifests)
    found: list[UnlockedManifest] = []
    for path, document in sorted(manifests.items()):
        directory = path.parent.relative_to(checkout).as_posix()
        if (
            path.parent != checkout
            and directory not in members
            and directory in standalone
            and is_fixture_directory(directory)
            and _needs_lock(path, document)
        ):
            found.append({"path": f"{directory}/{MANIFEST}", "directory": directory})
    return found


def build_nested_cargo_lock_envelope(checkout: pathlib.Path) -> NestedCargoLockEnvelope:
    """Assemble the lockfile evidence for the nested-cargo fixture rule.

    Parameters
    ----------
    checkout:
        The repository checkout to describe.

    Returns
    -------
    NestedCargoLockEnvelope
        The root lock, the fixture locks that drifted, the unlocked fixture
        manifests, and every fact that could not be established.
    """
    problems: list[str] = []
    root_fact, root_packages = _root_lock(checkout)
    manifests = load_manifests(checkout, problems)
    members = workspace_member_directories(checkout, manifests)
    drifted = (
        _nested_locks(checkout, members, root_packages, problems)
        if root_fact["state"] == "present"
        else []
    )
    unlocked = (
        _unlocked(checkout, manifests, members)
        if root_fact["state"] == "present"
        else []
    )
    return {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "kind": ENVELOPE_KIND,
        "repository": {"path": ".", "name": origin_slug(checkout)},
        "root_lock": root_fact,
        "nested_locks": drifted,
        "unlocked_manifests": unlocked,
        "problems": sorted(set(problems)),
    }
