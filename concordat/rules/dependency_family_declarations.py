"""Collect the dependency declarations of every governed Cargo manifest.

The envelope builder reports what a repository declares, not what it should
declare. This module produces the Cargo declaration facts for the
dependency-family rule: every edge of each governed manifest, including
workspace-inherited, target-specific, optional and renamed edges, plus the
workspace members read through a governed root. Python projects are collected
by `dependency_family_python`.

The facts stay evidence: each declaration records its manifest path, section,
target and feature context, the published package identity, the declared range
and the exact locked identity, or the reason the identity is unknown.
"""

from __future__ import annotations

import pathlib
import typing as typ

from concordat.errors import OperationalRuleError

from .dependency_family_coverage import coverage, directories_of, mapping
from .dependency_family_discovery import (
    cargo_tables,
    inherited_workspace,
    loose_manifest,
    nested_files,
    published_name,
    ungoverned_problems,
    workspace_includes,
    workspace_info,
    workspace_member_manifests,
)
from .dependency_family_facts import (
    Declaration,
    Monitoring,
    classify,
    covered_ecosystems,
    no_resolution,
    read_lock,
    representative,
    strings,
    text,
)
from .dependency_family_resolution import (
    Resolution,
    parse_cargo_version,
    resolve_cargo,
)
from .dependency_family_workspaces import inherited_tables
from .fs_probe import probe_file
from .rust_surfaces import resolve_rust_surfaces

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from .dependabot_envelope import ConfigFile

_DEFAULT_SECTIONS: typ.Final = frozenset({"dependencies", "build-dependencies"})


def _cargo_requirement(value: object) -> tuple[str, bool]:
    """Return a Cargo requirement's text and whether it is workspace-inherited."""
    if isinstance(value, str):
        return value, False
    table = mapping(value) or {}
    if table.get("workspace") is True:
        return "", True
    version = table.get("version")
    return (version if isinstance(version, str) else ""), False


def _is_path_only(value: object, inherited: dict[str, object], key: str) -> bool:
    """Return whether a dependency is a local path with no registry version.

    Dependabot never offers an update for a crate that resolves from a path, so
    it cannot drift from its family and is not a declaration to judge. A path
    dependency that also names a `version` is published and stays collected, as
    does one inherited from `[workspace.dependencies]` whose entry has a version.

    Returns
    -------
    bool
        True for a table with `path` and neither `version` nor a versioned
        workspace inheritance.
    """
    table = mapping(value) or {}
    if table.get("workspace") is True:
        table = mapping(inherited.get(key)) or {}
    return "path" in table and not isinstance(table.get("version"), str)


def _cargo_lock(checkout: pathlib.Path, manifest: str) -> pathlib.Path:
    """Return the `Cargo.lock` that governs *manifest*.

    A workspace member shares its workspace root's lock, so the search walks
    from the manifest's directory up to the checkout and takes the first lock
    it finds; with none, the root's path is returned and reported as absent.

    Returns
    -------
    pathlib.Path
        The nearest existing lock, else the checkout root's.
    """
    parent = pathlib.PurePosixPath(manifest).parent
    for directory in (parent, *parent.parents):
        candidate = checkout / directory / "Cargo.lock"
        if probe_file(candidate).present:
            return candidate
    return checkout / "Cargo.lock"


def _workspace_documents(
    checkout: pathlib.Path,
    surfaces: list[dict[str, object]],
    problems: list[str],
) -> dict[pathlib.PurePosixPath, dict[str, object]]:
    """Return every Cargo manifest a workspace lookup may have to walk.

    Governed surfaces use the resolver's decoded document. Every other
    manifest, including a workspace root outside the declared surfaces, is
    read loosely: only a `[workspace.dependencies]` table is needed from it.

    Returns
    -------
    dict[pathlib.PurePosixPath, dict[str, object]]
        Each manifest's decoded document, by checkout-relative path.
    """
    documents: dict[pathlib.PurePosixPath, dict[str, object]] = {}
    for nested in nested_files(checkout, "Cargo.toml", problems):
        relative = pathlib.PurePosixPath(nested.relative_to(checkout).as_posix())
        document, error = loose_manifest(nested)
        if error is None:
            documents[relative] = document
    root = pathlib.PurePosixPath("Cargo.toml")
    if root not in documents and probe_file(checkout / "Cargo.toml").present:
        document, error = loose_manifest(checkout / "Cargo.toml")
        if error is None:
            documents[root] = document
    for surface in surfaces:
        path = pathlib.PurePosixPath(str(surface["path"]))
        document = mapping(surface.get("parsed"))
        if document is not None:
            documents[path] = document
    return documents


def _cargo_surfaces(
    checkout: pathlib.Path,
    problems: list[str],
    members: cabc.Collection[str],
    unmonitored: cabc.Collection[str],
) -> tuple[list[dict[str, object]], dict[pathlib.PurePosixPath, dict[str, object]]]:
    """Return the governed Cargo surfaces and their workspace documents.

    Every nested manifest is scanned. One that declares a package a recorded
    relationship names, and that no declared surface governs, is a problem
    even when the surface list is explicit: an explicit list that omits a
    consuming crate would otherwise hide the family it consumes.

    Returns
    -------
    tuple[list[dict[str, object]], dict[pathlib.PurePosixPath, dict[str, object]]]
        The governed surfaces and the documents a workspace lookup needs.
    """
    try:
        resolution = resolve_rust_surfaces(checkout)
    except OperationalRuleError as error:
        detail = str(error).replace(str(checkout), ".")
        problems.append(f"the Rust surfaces could not be resolved: {detail}")
        return [], {}
    surfaces = [dict(surface) for surface in resolution.surfaces]
    documents = _workspace_documents(checkout, surfaces, problems)
    listed = workspace_member_manifests(checkout, surfaces)
    governed = {str(surface["path"]) for surface in surfaces}
    ungoverned = [
        nested
        for nested in nested_files(checkout, "Cargo.toml", problems)
        if nested.relative_to(checkout).as_posix() not in governed
        and nested not in listed
    ]
    governed_table = inherited_workspace(pathlib.PurePosixPath("Cargo.toml"), documents)
    problems.extend(
        ungoverned_problems(
            checkout,
            ungoverned,
            members,
            governed_table,
            unmonitored,
            report_unreadable=not resolution.declared,
        )
    )
    return surfaces + _member_surfaces(checkout, listed, documents, problems), documents


def _member_surfaces(
    checkout: pathlib.Path,
    listed: list[pathlib.Path],
    documents: dict[pathlib.PurePosixPath, dict[str, object]],
    problems: list[str],
) -> list[dict[str, object]]:
    """Return the readable workspace members as surfaces for dependency facts.

    A member's own dependencies are the repository's declarations even though
    Dependabot updates them through the workspace root, so they count towards
    whether a relationship is declared. A member that cannot be decoded is a
    problem, never an empty set of edges. The dicts stay in this module: they
    are not handed to another rule, which would judge a member as a project
    root.

    Returns
    -------
    list[dict[str, object]]
        One surface-shaped dict per member that decoded.
    """
    surfaces: list[dict[str, object]] = []
    for manifest in listed:
        relative = manifest.relative_to(checkout).as_posix()
        document = documents.get(pathlib.PurePosixPath(relative))
        if document is None:
            document, error = loose_manifest(manifest)
            if error is not None:
                problems.append(
                    f"{relative} is a member of a governed Cargo workspace and "
                    f"cannot be read: {error}"
                )
                continue
        documents[pathlib.PurePosixPath(relative)] = document
        surfaces.append({
            "path": relative,
            "role": "workspace-member",
            "parsed": document,
        })
    return surfaces


def _cargo_directories(
    manifest: str, documents: dict[pathlib.PurePosixPath, dict[str, object]]
) -> list[str]:
    """Return the directories Dependabot may update a Cargo manifest through.

    The manifest's own directory always covers it. An ancestor covers it only
    when that ancestor defines a workspace that lists the manifest as a member
    and does not exclude it, since Dependabot updates a member through the
    workspace root it belongs to and not through an unrelated parent.

    Returns
    -------
    list[str]
        The covering directories, nearest first.
    """
    path = pathlib.PurePosixPath(manifest)
    found = [directories_of(manifest)[0]]
    for directory in path.parents:
        info = workspace_info(documents.get(directory / "Cargo.toml") or {})
        if info is None:
            continue
        relative = pathlib.PurePosixPath(
            pathlib.PurePosixPath(manifest).parent.relative_to(directory).as_posix()
        )
        if workspace_includes(info, str(relative) or "."):
            found.append(
                "/" + str(directory).strip("./") if str(directory) != "." else "/"
            )
    return found


def _cargo_features(
    value: object, key: str, inherited: dict[str, object]
) -> tuple[list[str], bool]:
    """Return the features an edge requests and whether defaults stay on.

    A workspace member's `features` are added to the inherited ones and a
    member's `default-features = false` applies to the inherited dependency
    too, so both tables are read.

    Returns
    -------
    tuple[list[str], bool]
        The requested features and whether default features stay enabled.
    """
    table = mapping(value) or {}
    parent = mapping(inherited.get(key)) or {}
    features = sorted(
        set(strings(table.get("features"))) | set(strings(parent.get("features")))
    )
    default = table.get("default-features", parent.get("default-features", True))
    return features, default is not False


def cargo_declarations(
    checkout: pathlib.Path,
    config: ConfigFile | None,
    problems: list[str],
    member_names: cabc.Collection[str],
    unmonitored: cabc.Collection[str],
) -> tuple[list[Declaration], list[Monitoring]]:
    """Return every dependency edge and monitoring fact of the Cargo surfaces."""
    surfaces, documents = _cargo_surfaces(checkout, problems, member_names, unmonitored)
    # The workspace reader resolves inheritance against the nearest workspace,
    # honouring `package.workspace`, and refuses a root it cannot reach safely.
    inherited_by_path = inherited_tables(checkout, surfaces, problems)
    declarations: list[Declaration] = []
    monitoring: list[Monitoring] = []
    for surface in sorted(surfaces, key=lambda item: str(item["path"])):
        manifest = str(surface["path"])
        parsed = mapping(surface.get("parsed"))
        if parsed is None:
            problems.append(f"{manifest} could not be parsed")
            continue
        inherited = inherited_by_path.get(manifest, {})
        # A workspace member is monitored through its workspace root's entry,
        # which the root surface already contributes, so only a surface that
        # Dependabot can update in its own right carries a monitoring fact.
        if surface.get("role") != "workspace-member":
            monitoring.append({
                "manifest": manifest,
                "directory": directories_of(manifest)[0],
                "native": "cargo",
                "covered": covered_ecosystems(
                    config, _cargo_directories(manifest, documents)
                ),
                "duplicate": False,
            })
        declarations.extend(
            _surface_declarations(
                checkout,
                (manifest, parsed),
                inherited,
                config,
                documents,
                member_names,
                problems,
            )
        )
    return declarations, monitoring


def _requirement_map(
    manifest: str,
    table_rows: list[tuple[str, str | None, dict[str, object]]],
    inherited: dict[str, object],
    problems: list[str],
) -> dict[str, str]:
    """Return each published package's declared requirement in one manifest."""
    requirements: dict[str, str] = {}
    for _, _, table in table_rows:
        for key, value in table.items():
            if _is_path_only(value, inherited, key):
                continue
            requirement, workspace = _cargo_requirement(value)
            source = inherited.get(key) if workspace else value
            if workspace and source is None:
                problems.append(
                    f"{manifest} inherits {key} from its workspace, "
                    "but no ancestor defines it"
                )
            elif workspace:
                requirement, _ = _cargo_requirement(source)
            requirements[published_name(key, value, inherited)] = requirement
    return requirements


def _cargo_resolutions(
    checkout: pathlib.Path,
    manifest: str,
    parsed: dict[str, object],
    requirements: dict[str, str],
    member_names: cabc.Collection[str],
) -> dict[str, Resolution]:
    """Return the locked identity behind each requirement of one manifest."""
    lock = _cargo_lock(checkout, manifest)
    document, reason = read_lock(lock)
    if reason is not None:
        return {name: no_resolution(reason, None) for name in requirements}
    package = mapping(parsed.get("package")) or {}
    return resolve_cargo(
        document,
        lock.relative_to(checkout).as_posix(),
        requirements,
        manifest_name=text(package.get("name")),
        manifest_version=parse_cargo_version(package.get("version")),
        members=member_names,
    )


def _surface_declarations(
    checkout: pathlib.Path,
    surface: tuple[str, dict[str, object]],
    inherited: dict[str, object],
    config: ConfigFile | None,
    documents: dict[pathlib.PurePosixPath, dict[str, object]],
    member_names: cabc.Collection[str],
    problems: list[str],
) -> list[Declaration]:
    """Return every dependency edge of one parsed Cargo manifest."""
    manifest, parsed = surface
    table_rows = cargo_tables(parsed)
    requirements = _requirement_map(manifest, table_rows, inherited, problems)
    resolutions = _cargo_resolutions(
        checkout, manifest, parsed, requirements, member_names
    )
    return [
        _cargo_declaration(
            manifest,
            (section, target),
            (key, value, inherited),
            (config, resolutions, documents),
        )
        for section, target, table in table_rows
        for key, value in table.items()
        if not _is_path_only(value, inherited, key)
    ]


class _CargoEdge(typ.NamedTuple):
    """The facts one Cargo edge contributes to its declaration."""

    requirement: str
    workspace: bool
    package: str
    resolved: Resolution
    kind: str
    bounded: bool
    optional: bool
    features: list[str]
    default_features: bool
    version: str | None


def _cargo_edge(
    key: str,
    value: object,
    inherited: dict[str, object],
    resolutions: dict[str, Resolution],
) -> _CargoEdge:
    """Return the requirement, identity and features of one Cargo entry."""
    requirement, workspace = _cargo_requirement(value)
    package = published_name(key, value, inherited)
    if workspace and inherited.get(key) is not None:
        requirement, _ = _cargo_requirement(inherited[key])
    resolved = resolutions.get(package) or no_resolution(
        f"{package} was not resolved", None
    )
    kind, bounded = classify(requirement)
    optional = (mapping(value) or {}).get("optional") is True
    features, default_features = _cargo_features(value, key, inherited)
    return _CargoEdge(
        requirement=requirement,
        workspace=workspace,
        package=package,
        resolved=resolved,
        kind=kind,
        bounded=bounded,
        optional=optional,
        features=features,
        default_features=default_features,
        version=representative(requirement, resolved),
    )


def _cargo_declaration(
    manifest: str,
    where: tuple[str, str | None],
    entry: tuple[str, object, dict[str, object]],
    evidence: tuple[
        ConfigFile | None,
        dict[str, Resolution],
        dict[pathlib.PurePosixPath, dict[str, object]],
    ],
) -> Declaration:
    """Return one Cargo dependency edge as a declaration fact."""
    section, target = where
    key, value, inherited = entry
    config, resolutions, documents = evidence
    edge = _cargo_edge(key, value, inherited, resolutions)
    return {
        "manifest": manifest,
        "ecosystem": "cargo",
        "section": section,
        "target": target,
        "optional": edge.optional,
        "in_default": (
            section in _DEFAULT_SECTIONS and target is None and not edge.optional
        ),
        "default_features": edge.default_features,
        "requested_features": edge.features,
        "extras": [],
        "markers": None,
        "workspace_inherited": edge.workspace,
        "declared_name": key,
        "package": edge.package,
        "requirement": edge.requirement,
        "requirement_kind": edge.kind,
        "upper_bounded": edge.bounded,
        "resolved": edge.resolved,
        "coverage": coverage(
            config,
            "cargo",
            _cargo_directories(manifest, documents),
            edge.package,
            version=edge.version,
        ),
    }
