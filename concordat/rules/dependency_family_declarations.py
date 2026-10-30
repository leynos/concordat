"""Collect the dependency declarations of Cargo manifests and the root project.

The envelope builder reports what a repository declares, not what it should
declare. This module produces those declaration facts for the two ecosystems
the dependency-family rule governs: every edge of each governed Cargo manifest,
including workspace-inherited, target-specific, optional and renamed edges, and
every requirement of the root `pyproject.toml`.

The facts stay evidence: each declaration records its manifest path, section,
target and feature context, the published package identity, the declared range
and the exact locked identity, or the reason the identity is unknown.
"""

from __future__ import annotations

import pathlib
import re
import tomllib
import typing as typ

from concordat.errors import OperationalRuleError

from .dependency_family_coverage import (
    Coverage,
    coverage,
    directories_of,
    directory_matches,
    entry_directories,
    mapping,
    normalise,
    sequence,
)
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
from .dependency_family_resolution import (
    Resolution,
    parse_cargo_version,
    resolve_cargo,
    resolve_python,
)
from .dependency_family_versions import lowest_version
from .fs_probe import probe_file
from .rust_surfaces import resolve_rust_surfaces

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from .dependabot_envelope import ConfigFile

_REQUIREMENT_NAME: typ.Final = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*"
    r"(?:\[(?P<extras>[^\]]*)\])?\s*"
    r"(?P<spec>[^;]*?)\s*"
    r"(?:;(?P<marker>.*))?$"
)
_DEFAULT_SECTIONS: typ.Final = frozenset({"dependencies", "build-dependencies"})


class Declaration(typ.TypedDict):
    """One dependency edge declared in a governed manifest.

    Attributes
    ----------
    section:
        The manifest table the edge sits in.
    target:
        The Cargo target the table applies to, or None.
    feature_context:
        The features the edge is declared under, kept apart from the update
        decision so an optional lane is visible as evidence.
    """

    manifest: str
    ecosystem: str
    section: str
    target: str | None
    optional: bool
    in_default: bool
    default_features: bool
    requested_features: list[str]
    extras: list[str]
    markers: str | None
    workspace_inherited: bool
    declared_name: str
    package: str
    requirement: str
    requirement_kind: str
    upper_bounded: bool
    resolved: Resolution
    coverage: Coverage


class Monitoring(typ.TypedDict):
    """Whether an ecosystem's native Dependabot entry covers a project.

    Attributes
    ----------
    native:
        The ecosystem Dependabot should update the project with: ``cargo``,
        ``uv`` when a `uv.lock` is present, otherwise ``pip``.
    covered:
        The ecosystems that have an entry covering the directory.
    duplicate:
        Whether both `pip` and `uv` entries cover the same project.
    """

    manifest: str
    directory: str
    native: str
    covered: list[str]
    duplicate: bool


def _text(value: object) -> str | None:
    """Return a non-blank scalar as text, or None."""
    return value.strip() if isinstance(value, str) and value.strip() else None


def _strings(value: object) -> list[str]:
    """Return a list's non-blank string items, in order."""
    return [
        item.strip()
        for item in sequence(value) or []
        if isinstance(item, str) and item.strip()
    ]


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


def _classify(requirement: str) -> tuple[str, bool]:
    """Return how a requirement constrains its package, and whether it is bounded.

    An exact pin is not an upper bound: it is the very thing a version-update
    pull request replaces. A bound is an explicit `<`, `<=` or tilde clause.

    Returns
    -------
    tuple[str, bool]
        The requirement kind (`exact`, `bounded`, `caret` or `open`) and
        whether it carries an explicit upper bound.
    """
    text = requirement.strip()
    if not text or text == "*":
        return "open", False
    bounded = any(
        clause.strip().startswith(("<", "~"))
        for clause in text.split(",")
        if clause.strip()
    )
    if bounded:
        return "bounded", True
    if re.fullmatch(r"(?:==|=)\s*[^*,<>~\s]+", text):
        return "exact", False
    return "caret", False


def _read_lock(lock: pathlib.Path) -> tuple[dict[str, object], str | None]:
    """Return a decoded lock file, or why it could not be read."""
    probe = probe_file(lock)
    if probe.read_error is not None:
        return {}, f"cannot read {lock.name}: {probe.read_error}"
    if not probe.present:
        return {}, f"{lock.name} is absent"
    try:
        return tomllib.loads(lock.read_text(encoding="utf-8")), None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as error:
        return {}, f"cannot parse {lock.name}: {error}"


def _no_resolution(reason: str, lock: str | None) -> Resolution:
    """Return a resolution carrying only the reason it could not be made."""
    return {
        "versions": [],
        "lock": lock,
        "reason": reason,
        "package_id": None,
        "member_edges": {},
    }


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
        ungoverned_problems(checkout, ungoverned, members, governed_table, unmonitored)
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
        set(_strings(table.get("features"))) | set(_strings(parent.get("features")))
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
    declarations: list[Declaration] = []
    monitoring: list[Monitoring] = []
    for surface in sorted(surfaces, key=lambda item: str(item["path"])):
        manifest = str(surface["path"])
        parsed = mapping(surface.get("parsed"))
        if parsed is None:
            problems.append(f"{manifest} could not be parsed")
            continue
        inherited = inherited_workspace(pathlib.PurePosixPath(manifest), documents)
        # A workspace member is monitored through its workspace root's entry,
        # which the root surface already contributes, so only a surface that
        # Dependabot can update in its own right carries a monitoring fact.
        if surface.get("role") != "workspace-member":
            monitoring.append({
                "manifest": manifest,
                "directory": directories_of(manifest)[0],
                "native": "cargo",
                "covered": _covered_ecosystems(
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
    document, reason = _read_lock(lock)
    if reason is not None:
        return {name: _no_resolution(reason, None) for name in requirements}
    package = mapping(parsed.get("package")) or {}
    return resolve_cargo(
        document,
        lock.relative_to(checkout).as_posix(),
        requirements,
        manifest_name=_text(package.get("name")),
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
    resolved = resolutions.get(package) or _no_resolution(
        f"{package} was not resolved", None
    )
    kind, bounded = _classify(requirement)
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
        version=_representative(requirement, resolved),
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


def _representative(requirement: str, resolved: Resolution) -> str | None:
    """Return the version whose update class can break the declared line."""
    if resolved["versions"]:
        return resolved["versions"][0]
    return lowest_version(requirement)


def _covered_ecosystems(
    config: ConfigFile | None, directories: cabc.Sequence[str]
) -> list[str]:
    """Return the ecosystems with an `updates` entry covering any directory.

    A workspace member is updated through its workspace root, so the caller
    passes the member's own directory and the ancestor workspace directories
    that actually list it. Dependabot then needs only one native entry anywhere
    on that chain.

    Returns
    -------
    list[str]
        The distinct native ecosystems that cover the declared surface.
    """
    if config is None or config["error"] is not None:
        return []
    updates = sequence((mapping(config["parsed"]) or {}).get("updates")) or []
    return sorted({
        str(table["package-ecosystem"])
        for entry in updates
        if (table := mapping(entry)) is not None
        and isinstance(table.get("package-ecosystem"), str)
        and any(
            directory_matches(pattern, directory)
            for pattern in entry_directories(table)
            for directory in directories
        )
    })


def _python_requirements(
    parsed: dict[str, object],
) -> list[tuple[str, str | None, str]]:
    """Return the `(section, extra, requirement)` strings of a pyproject."""
    project = mapping(parsed.get("project")) or {}
    found = _named_rows("dependencies", None, project.get("dependencies"))
    extras = mapping(project.get("optional-dependencies")) or {}
    groups = mapping(parsed.get("dependency-groups")) or {}
    for section, tables in (
        ("optional-dependencies", extras),
        ("dependency-groups", groups),
    ):
        for name, items in tables.items():
            found.extend(_named_rows(section, str(name), items))
    return found


def _named_rows(
    section: str, name: str | None, items: object
) -> list[tuple[str, str | None, str]]:
    """Return the string requirements of one list as `(section, name, text)`."""
    return [
        (section, name, item) for item in sequence(items) or [] if isinstance(item, str)
    ]


def _read_pyproject(
    checkout: pathlib.Path, relative: str, problems: list[str]
) -> dict[str, object] | None:
    """Return the decoded `pyproject.toml` at *relative*, or None with the reason.

    Returns
    -------
    dict[str, object] | None
        The decoded document, or None when absent or unreadable.
    """
    path = checkout / relative
    probe = probe_file(path)
    if probe.read_error is not None:
        problems.append(f"cannot read {relative}: {probe.read_error}")
        return None
    if not probe.present:
        return None
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as error:
        problems.append(f"cannot parse {relative}: {error}")
        return None


def python_declarations(
    checkout: pathlib.Path,
    config: ConfigFile | None,
    problems: list[str],
    member_names: cabc.Collection[str],
) -> tuple[list[Declaration], list[Monitoring]]:
    """Return every dependency edge of every `pyproject.toml` in the checkout.

    The root project and each nested one (hidden and vendored trees excepted)
    are read, each against the `uv.lock` beside it and the Dependabot entries
    for its own directory.

    Returns
    -------
    tuple[list[Declaration], list[Monitoring]]
        One declaration per requirement, with its locked identity and
        coverage, and one monitoring fact per project.
    """
    manifests = ["pyproject.toml"] + [
        path.relative_to(checkout).as_posix()
        for path in nested_files(checkout, "pyproject.toml", problems)
    ]
    declarations: list[Declaration] = []
    monitoring: list[Monitoring] = []
    for manifest in manifests:
        parsed = _read_pyproject(checkout, manifest, problems)
        if parsed is None:
            continue
        found, fact = _project_declarations(
            (checkout, config), (manifest, parsed), member_names, problems
        )
        declarations += found
        monitoring.append(fact)
    return declarations, monitoring


def _project_declarations(
    context: tuple[pathlib.Path, ConfigFile | None],
    project: tuple[str, dict[str, object]],
    member_names: cabc.Collection[str],
    problems: list[str],
) -> tuple[list[Declaration], Monitoring]:
    """Return the dependency edges and monitoring fact of one `pyproject.toml`."""
    checkout, config = context
    manifest, parsed = project
    parent = pathlib.PurePosixPath(manifest).parent
    lock_name = (parent / "uv.lock").as_posix()
    has_lock = probe_file(checkout / lock_name).present
    ecosystem = "uv" if has_lock else "pip"
    rows = _python_requirements(parsed)
    matches = [
        (_REQUIREMENT_NAME.match(text), section, extra) for section, extra, text in rows
    ]
    problems.extend(
        f"cannot read the requirement {text!r} in {manifest}"
        for (match, _, _), (_, _, text) in zip(matches, rows, strict=True)
        if match is None
    )
    valid = [(m, section, extra) for m, section, extra in matches if m is not None]
    requirements = {normalise(m.group("name")): m.group("spec") for m, _, _ in valid}
    if has_lock:
        document, lock_reason = _read_lock(checkout / lock_name)
        resolutions = (
            {name: _no_resolution(lock_reason, None) for name in requirements}
            if lock_reason is not None
            else resolve_python(document, lock_name, requirements, members=member_names)
        )
    else:
        resolutions = {
            name: _no_resolution("no supported lock", None) for name in requirements
        }
    directory = "/" if str(parent) == "." else f"/{parent.as_posix()}"
    declarations = [
        _python_declaration(
            (m, section, extra), (config, ecosystem, resolutions), (manifest, directory)
        )
        for m, section, extra in valid
    ]
    covered = _covered_ecosystems(config, [directory])
    fact: Monitoring = {
        "manifest": manifest,
        "directory": directory,
        "native": ecosystem,
        "covered": covered,
        "duplicate": "pip" in covered and "uv" in covered,
    }
    return declarations, fact


def _python_declaration(
    requirement: tuple[re.Match[str], str, str | None],
    evidence: tuple[ConfigFile | None, str, dict[str, Resolution]],
    where: tuple[str, str],
) -> Declaration:
    """Return one `pyproject.toml` requirement as a declaration fact."""
    match, section, extra = requirement
    config, ecosystem, resolutions = evidence
    manifest, directory = where
    package = normalise(match.group("name"))
    spec = match.group("spec")
    kind, bounded = _classify(spec)
    resolved = resolutions.get(package) or _no_resolution(
        f"{package} was not resolved", None
    )
    extras = _strings(match.group("extras").split(",") if match.group("extras") else [])
    marker = _text(match.group("marker"))
    version = _representative(spec, resolved)
    return {
        "manifest": manifest,
        "ecosystem": ecosystem,
        "section": section if extra is None else f"{section}:{extra}",
        "target": None,
        "optional": section == "optional-dependencies",
        "in_default": section == "dependencies",
        "default_features": True,
        "requested_features": [],
        "extras": extras,
        "markers": marker,
        "workspace_inherited": False,
        "declared_name": match.group("name"),
        "package": package,
        "requirement": spec,
        "requirement_kind": kind,
        "upper_bounded": bounded,
        "resolved": resolved,
        "coverage": coverage(
            config,
            ecosystem,
            [directory],
            package,
            version=version,
        ),
    }
