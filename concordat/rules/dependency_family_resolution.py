"""Resolve a declared package to the exact package identity a lock records.

A relationship between types is a statement about resolved identities, not
about names: two declarations of `fluent-bundle` are compatible only when the
lock gives them one package entry. The same holds for a renamed bridge such as
`serde-pyobject`, whose identity must be the one the PyO3 edge resolves.

This module reads an already-decoded lock document and performs no I/O. It
records the owning package's identity and the identities of its edges to other
catalog members, or the reason it could not. A name with two plausible locked
versions is `ambiguous`, never silently one of them.
"""

from __future__ import annotations

import re
import typing as typ

from .dependency_family_coverage import mapping, normalise, sequence
from .dependency_family_versions import parse_version, satisfies

if typ.TYPE_CHECKING:
    import collections.abc as cabc

_CARGO_EDGE: typ.Final = re.compile(
    r"^(?P<name>\S+)(?:\s+(?P<version>\S+))?(?:\s+\((?P<source>.*)\))?$"
)


class Resolution(typ.TypedDict):
    """The locked identity of one package, or why it is unknown."""

    versions: list[str]
    lock: str | None
    reason: str | None
    package_id: str | None
    member_edges: dict[str, list[str]]


def package_id(name: str, version: str, source: object = None) -> str:
    """Return a stable identity string for one locked package entry."""
    suffix = f" ({source})" if isinstance(source, str) and source else ""
    return f"{name} {version}{suffix}"


def _unresolved(reason: str, lock: str | None) -> Resolution:
    """Return a resolution that carries only its failure."""
    return {
        "versions": [],
        "lock": lock,
        "reason": reason,
        "package_id": None,
        "member_edges": {},
    }


def _entries(document: dict[str, object]) -> list[dict[str, object]]:
    """Return the decoded lock's package entries."""
    return [
        entry
        for raw in sequence(document.get("package")) or []
        if (entry := mapping(raw)) is not None
    ]


def _version(entry: dict[str, object]) -> str | None:
    """Return a package entry's version text, when it has one."""
    value = entry.get("version")
    return str(value) if isinstance(value, str) else None


def _name(entry: dict[str, object]) -> str | None:
    """Return a package entry's name, when it has one."""
    value = entry.get("name")
    return str(value) if isinstance(value, str) else None


def _unique_entry(
    entries: list[dict[str, object]], name: str, requirement: str | None
) -> tuple[dict[str, object] | None, str | None]:
    """Return the one entry for *name* that satisfies *requirement*, or why not.

    Returns
    -------
    tuple[dict[str, object] | None, str | None]
        The entry and None, or None and the reason the identity is unknown.
    """
    candidates = [entry for entry in entries if _name(entry) == name]
    if not candidates:
        return None, f"{name} is not in the lock"
    if len(candidates) == 1:
        return candidates[0], None
    if requirement:
        matching = [
            entry
            for entry in candidates
            if (version := _version(entry)) is not None
            and satisfies(requirement, version, "cargo") is True
        ]
        if len(matching) == 1:
            return matching[0], None
    return None, f"{name} has more than one locked version"


def _cargo_edge(
    text: str, entries: list[dict[str, object]]
) -> dict[str, object] | None:
    """Return the package entry one Cargo lock dependency edge names."""
    match = _CARGO_EDGE.match(text.strip())
    if match is None:
        return None
    name = match.group("name")
    wanted_version = match.group("version")
    wanted_source = match.group("source")
    for entry in entries:
        if _name(entry) != name:
            continue
        if wanted_version is not None and _version(entry) != wanted_version:
            continue
        source = entry.get("source")
        if wanted_source is not None and source != wanted_source:
            continue
        return entry
    return None


def _member_edges(
    owner: dict[str, object] | None,
    entries: list[dict[str, object]],
    members: cabc.Collection[str],
) -> dict[str, list[str]]:
    """Return the resolved edges to each catalog member package.

    A member with no edge, or an edge the lock gives no entry for, maps to an
    empty list: the relationship then cannot be established from this lock.

    Returns
    -------
    dict[str, list[str]]
        Each member's locked identities as another package records them.
    """
    edges: dict[str, list[str]] = {member: [] for member in sorted(set(members))}
    if owner is None:
        return edges
    found: dict[str, list[str]] = {member: [] for member in sorted(set(members))}
    for text in sequence(owner.get("dependencies")) or []:
        if not isinstance(text, str):
            continue
        entry = _cargo_edge(text, entries)
        if entry is None:
            continue
        name = _name(entry)
        version = _version(entry)
        if name is None or version is None:
            continue
        for member in members:
            if name == member:
                found[member].append(package_id(name, version, entry.get("source")))
    return {member: sorted(set(ids)) for member, ids in found.items()}


def _owner(
    entries: list[dict[str, object]],
    manifest_name: str | None,
    manifest_version: str | None,
) -> tuple[dict[str, object] | None, str | None]:
    """Return the lock entry for the manifest's own package, or why not."""
    if manifest_name is None:
        return None, "the manifest does not name a package"
    matches = [
        entry
        for entry in entries
        if _name(entry) == manifest_name
        and (manifest_version is None or _version(entry) == manifest_version)
    ]
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        return None, f"{manifest_name} has more than one locked version"
    return None, f"{manifest_name} is not in the lock"


def resolve_cargo(
    document: dict[str, object],
    lock: str,
    requirements: cabc.Mapping[str, str],
    *,
    manifest_name: str | None,
    manifest_version: str | None,
    members: cabc.Collection[str],
) -> dict[str, Resolution]:
    """Return each declared package's locked identity and the owner's edges.

    Parameters
    ----------
    document:
        The decoded `Cargo.lock`.
    lock:
        The lock path relative to the checkout, recorded as evidence.
    requirements:
        Declared package name to requirement, used to disambiguate a package
        the lock records at more than one version.
    manifest_name:
        The `[package] name` of the manifest being read, if it has one.
    manifest_version:
        The `[package] version` of the manifest being read, if it has one.
    members:
        Every package some relationship names, whose edges are recorded.

    Returns
    -------
    dict[str, Resolution]
        One resolution per declared package. The owner's `member_edges` is
        repeated on each, so a type-identity comparison reads one fact.
    """
    entries = _entries(document)
    owner, _ = _owner(entries, manifest_name, manifest_version)
    edges = _member_edges(owner, entries, members)
    results: dict[str, Resolution] = {}
    resolved_entries: list[dict[str, object]] = []
    for name, requirement in requirements.items():
        entry, reason = _unique_entry(entries, name, requirement)
        if entry is None:
            results[name] = {
                **_unresolved(reason or f"{name} is not in the lock", lock),
                "member_edges": edges,
            }
            continue
        resolved_entries.append(entry)
        version = _version(entry) or ""
        results[name] = {
            "versions": [version],
            "lock": lock,
            "reason": None,
            "package_id": package_id(name, version, entry.get("source")),
            "member_edges": edges,
        }
    for entry in resolved_entries:
        for member, identities in _member_edges(entry, entries, members).items():
            edges[member] = sorted(set(edges[member]) | set(identities))
    return results


def _uv_root(
    entries: list[dict[str, object]],
) -> tuple[dict[str, object] | None, str | None]:
    """Return the uv lock's root project entry, or why it is absent."""
    for entry in entries:
        source = mapping(entry.get("source")) or {}
        if source.get("virtual") == "." or source.get("editable") == ".":
            return entry, None
    return None, "uv.lock has no root project entry"


def _uv_edge(
    value: object, entries: list[dict[str, object]]
) -> dict[str, object] | None:
    """Return the package entry a uv lock dependency edge names."""
    edge = mapping(value)
    if edge is None:
        return None
    name = edge.get("name")
    if not isinstance(name, str):
        return None
    wanted = normalise(name)
    wanted_version = edge.get("version")
    for entry in entries:
        entry_name = _name(entry)
        if entry_name is None or normalise(entry_name) != wanted:
            continue
        if wanted_version is not None and _version(entry) != str(wanted_version):
            continue
        return entry
    return None


def _uv_member_edges(
    root: dict[str, object] | None,
    entries: list[dict[str, object]],
    members: cabc.Collection[str],
) -> dict[str, list[str]]:
    """Return the root project's resolved edges to each catalog member."""
    found: dict[str, list[str]] = {member: [] for member in sorted(set(members))}
    if root is None:
        return found
    for value in sequence(root.get("dependencies")) or []:
        entry = _uv_edge(value, entries)
        if entry is None:
            continue
        name = _name(entry)
        version = _version(entry)
        if name is None or version is None:
            continue
        for member in members:
            if normalise(name) == normalise(member):
                found[member].append(package_id(normalise(name), version))
    return {member: sorted(set(ids)) for member, ids in found.items()}


def resolve_python(
    document: dict[str, object],
    lock: str,
    requirements: cabc.Mapping[str, str],
    *,
    members: cabc.Collection[str],
) -> dict[str, Resolution]:
    """Return each Python requirement's locked identity and the root's edges.

    Returns
    -------
    dict[str, Resolution]
        One resolution per requirement. A requirement with several locked
        versions that its specifier cannot disambiguate is `ambiguous`.
    """
    entries = _entries(document)
    root, root_reason = _uv_root(entries)
    edges = _uv_member_edges(root, entries, set(members) | set(requirements))
    results: dict[str, Resolution] = {}
    for name, requirement in requirements.items():
        wanted = normalise(name)
        candidates = [
            entry
            for entry in entries
            if (entry_name := _name(entry)) is not None
            and normalise(entry_name) == wanted
        ]
        if not candidates:
            results[name] = _unresolved(f"{name} is not in uv.lock", lock)
            continue
        if len(candidates) > 1:
            matching = [
                entry
                for entry in candidates
                if (version := _version(entry)) is not None
                and satisfies(requirement, version, "uv") is True
            ]
            if len(matching) != 1:
                reason = f"{name} has more than one locked version"
                if root_reason is not None and root is None:
                    reason = root_reason
                results[name] = _unresolved(reason, lock)
                continue
            candidates = matching
        entry = candidates[0]
        version = _version(entry) or ""
        results[name] = {
            "versions": [version],
            "lock": lock,
            "reason": None,
            "package_id": package_id(wanted, version),
            "member_edges": edges,
        }
    return results


def parse_cargo_version(version: object) -> str | None:
    """Return a manifest `[package] version` as text, when it is usable."""
    if isinstance(version, str) and parse_version(version) is not None:
        return version
    return None
