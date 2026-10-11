"""Build the dependency-family facts for the `dependency-family-contracts` rule.

The facts are collected here, outside policy evaluation, so the Rego policy
reads a deterministic document: it opens no manifest, runs no repository code
and contacts no registry. Three kinds of evidence are gathered.

- Declarations: every dependency edge in each governed Cargo manifest (found
  by the shared surface resolver, so nested workspaces and PyO3 crates are
  seen) and in the root `pyproject.toml`, with the manifest path, the section
  and target the edge sits in, the name as written, the published package
  identity (a renamed `package =` key resolves to the name Dependabot matches),
  the requirement as written and what it implies, and the locked versions.
- Dependabot coverage: for each declaration, which `updates` entry covers its
  directory, which group takes its published name first (document order is
  part of the configuration's meaning), and whether an `ignore` rule names it.
- Relationships: the versioned data in `platform-standards/canon/data`, and the
  repository's recorded exceptions from `.concordat`.

Anything the builder cannot establish stays a fact with its reason, so the
policy returns an indeterminate verdict instead of reading a missing manifest,
an unreadable lock or an unsupported layout as compliance. A checkout that
cannot be read at all raises an operational error.
"""

from __future__ import annotations

import pathlib
import re
import tomllib
import typing as typ

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from concordat.errors import OperationalRuleError

from .dependabot_envelope import build_dependabot_envelope
from .dependency_family_coverage import (
    Coverage,
    coverage,
    directories_of,
    mapping,
    normalise,
    sequence,
)
from .fs_probe import probe_file
from .rust_surfaces import resolve_rust_surfaces

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from .codescene_coverage_envelope import Repository
    from .dependabot_envelope import ConfigFile

ENVELOPE_SCHEMA_VERSION: typ.Final = 1
ENVELOPE_KIND: typ.Final = "policy-input/dependency-family-contracts"
RULE_ID: typ.Final = "dependency-family-contracts"
DATA_FILENAME: typ.Final = "dependency-families.yaml"

_CARGO_SECTIONS: typ.Final = ("dependencies", "dev-dependencies", "build-dependencies")
_REQUIREMENT_NAME: typ.Final = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*"
    r"(?P<spec>[^;]*?)\s*(?:;.*)?$"
)
_EXCEPTION_FIELDS: typ.Final = ("relationship", "rationale", "review_trigger")
_yaml = YAML(typ="safe")


class Resolved(typ.TypedDict):
    """The locked versions of one package, or why they are unknown."""

    versions: list[str]
    lock: str | None
    reason: str | None


class Declaration(typ.TypedDict):
    """One dependency edge declared in a governed manifest."""

    manifest: str
    ecosystem: str
    section: str
    target: str | None
    optional: bool
    workspace_inherited: bool
    declared_name: str
    package: str
    requirement: str
    requirement_kind: str
    upper_bounded: bool
    resolved: Resolved
    coverage: Coverage


class RelationshipException(typ.TypedDict):
    """One repository exception to a named relationship."""

    relationship: str
    rationale: str
    review_trigger: str


class DependencyFamilyEnvelope(typ.TypedDict):
    """Store the evidence evaluated by the dependency-family rule.

    Attributes
    ----------
    schema_version:
        The envelope's schema version, which the policy checks.
    kind:
        The policy-input identifier, which the policy checks.
    repository:
        The audited checkout's identity.
    relationships_version:
        The `schema_version` of the relationship data read, or None when it
        could not be read.
    relationships:
        The estate's relationship data.
    declarations:
        Every governed dependency edge, with its Dependabot coverage.
    config:
        The decoded Dependabot configuration, or None when there is none.
    exceptions:
        The repository's valid recorded exceptions.
    problems:
        Facts the builder could not establish, one sentence each.
    """

    schema_version: int
    kind: str
    repository: Repository
    relationships_version: int | None
    relationships: list[dict[str, object]]
    declarations: list[Declaration]
    config: ConfigFile | None
    exceptions: list[RelationshipException]
    problems: list[str]


def _relationships_path() -> pathlib.Path:
    """Return the relationship data file beside the canon rule packages."""
    # Imported here: `packages` registers this module's builder, so a module-level
    # import would be circular.
    from .packages import rule_package_dir

    return rule_package_dir(RULE_ID).parents[1] / "data" / DATA_FILENAME


def _relationship_document(
    path: pathlib.Path, problems: list[str]
) -> dict[str, object] | None:
    """Return the decoded relationship data, or None with the reason recorded."""
    try:
        document: object = _yaml.load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, YAMLError) as error:
        problems.append(f"cannot read the relationship data {path.name}: {error}")
        return None
    data = mapping(document)
    if data is None:
        problems.append(f"the relationship data {path.name} is not a mapping")
    return data


def _load_relationships(
    problems: list[str],
) -> tuple[int | None, list[dict[str, object]]]:
    """Return the data file's schema version and relationships.

    A file that cannot be read or decoded leaves a problem and no
    relationships, which the policy reports as indeterminate.

    Returns
    -------
    tuple[int | None, list[dict[str, object]]]
        The schema version and the relationship mappings.
    """
    path = _relationships_path()
    data = _relationship_document(path, problems)
    if data is None:
        return None, []
    listed = sequence(data.get("relationships"))
    version = data.get("schema_version")
    if listed is None or not isinstance(version, int):
        problems.append(f"the relationship data {path.name} has no versioned list")
        return None, []
    return version, [item for entry in listed if (item := mapping(entry)) is not None]


def _cargo_requirement(value: object) -> tuple[str, bool]:
    """Return a Cargo requirement's text and whether it is workspace-inherited."""
    if isinstance(value, str):
        return value, False
    table = mapping(value) or {}
    version = table.get("version")
    if table.get("workspace") is True:
        return "", True
    return (version if isinstance(version, str) else ""), False


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


def _lock_versions(
    lock: pathlib.Path, package_names: cabc.Collection[str], *, python: bool
) -> tuple[dict[str, list[str]], str | None]:
    """Return the locked versions of the named packages, or why not.

    Returns
    -------
    tuple[dict[str, list[str]], str | None]
        Versions by package name, and the reason the lock was unreadable.
    """
    document, reason = _read_lock(lock)
    if reason is not None:
        return {}, reason
    found: dict[str, list[str]] = {}
    items = (mapping(raw) or {} for raw in sequence(document.get("package")) or [])
    for item in items:
        name = str(item.get("name", ""))
        key = normalise(name) if python else name
        if key in package_names and isinstance(item.get("version"), str):
            found.setdefault(key, []).append(str(item["version"]))
    return found, None


def _resolved(
    versions: dict[str, list[str]], key: str, lock: str, reason: str | None
) -> Resolved:
    """Return the locked versions of one package from a decoded lock."""
    if reason is not None:
        return {"versions": [], "lock": None, "reason": reason}
    if key not in versions:
        return {"versions": [], "lock": lock, "reason": f"{key} is not in {lock}"}
    return {"versions": sorted(set(versions[key])), "lock": lock, "reason": None}


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


def _workspace_requirements(
    surfaces: cabc.Iterable[dict[str, object]],
) -> dict[str, object]:
    """Return `[workspace.dependencies]` from every workspace surface."""
    inherited: dict[str, object] = {}
    for surface in surfaces:
        workspace = mapping((mapping(surface.get("parsed")) or {}).get("workspace"))
        listed = mapping((workspace or {}).get("dependencies"))
        if listed is not None:
            inherited.update(listed)
    return inherited


def _section_tables(
    body: dict[str, object], target: str | None
) -> list[tuple[str, str | None, dict[str, object]]]:
    """Return the dependency tables of one manifest body or target body."""
    found = ((section, mapping(body.get(section))) for section in _CARGO_SECTIONS)
    return [(section, target, table) for section, table in found if table is not None]


def _cargo_tables(
    parsed: dict[str, object],
) -> list[tuple[str, str | None, dict[str, object]]]:
    """Return each dependency table with its section and target."""
    tables = _section_tables(parsed, None)
    for target, raw in (mapping(parsed.get("target")) or {}).items():
        tables.extend(_section_tables(mapping(raw) or {}, str(target)))
    return tables


def _cargo_surfaces(
    checkout: pathlib.Path, problems: list[str]
) -> list[dict[str, object]]:
    """Return the governed Cargo surfaces, or none with the reason recorded."""
    try:
        resolution = resolve_rust_surfaces(checkout)
    except OperationalRuleError as error:
        detail = str(error).replace(str(checkout), ".")
        problems.append(f"the Rust surfaces could not be resolved: {detail}")
        return []
    surfaces = [dict(surface) for surface in resolution.surfaces]
    if not resolution.declared:
        governed = {str(surface["path"]) for surface in surfaces}
        problems.extend(
            f"{relative} is a Cargo manifest outside the governed Rust surfaces; "
            "declare language.rust.surfaces in .concordat to read it"
            for nested in _nested_files(checkout, "Cargo.toml")
            if (relative := nested.relative_to(checkout).as_posix()) not in governed
        )
    return surfaces


def _cargo_declarations(
    checkout: pathlib.Path,
    config: ConfigFile | None,
    problems: list[str],
) -> list[Declaration]:
    """Return every dependency edge of the governed Cargo surfaces."""
    surfaces = _cargo_surfaces(checkout, problems)
    inherited = _workspace_requirements(surfaces)
    parsed = [(str(s["path"]), mapping(s.get("parsed"))) for s in surfaces]
    problems.extend(
        f"{path} could not be parsed" for path, doc in parsed if doc is None
    )
    return [
        declaration
        for path, doc in parsed
        if doc is not None
        for declaration in _surface_declarations(
            (checkout, path, doc), inherited, config
        )
    ]


def _surface_declarations(
    surface: tuple[pathlib.Path, str, dict[str, object]],
    inherited: dict[str, object],
    config: ConfigFile | None,
) -> list[Declaration]:
    """Return every dependency edge of one parsed Cargo manifest."""
    checkout, manifest, parsed = surface
    lock = _cargo_lock(checkout, manifest)
    table_rows = _cargo_tables(parsed)
    names = {
        _published(key, value, inherited)
        for _, _, table in table_rows
        for key, value in table.items()
    }
    versions, reason = _lock_versions(lock, names, python=False)
    evidence = (config, versions, lock.relative_to(checkout).as_posix(), reason)
    return [
        _cargo_declaration(
            (manifest, section, target), (key, value, inherited), evidence
        )
        for section, target, table in table_rows
        for key, value in table.items()
    ]


def _published(key: str, value: object, inherited: dict[str, object]) -> str:
    """Return the published package name behind a Cargo dependency key."""
    table = mapping(value)
    if table is not None and table.get("workspace") is True:
        table = mapping(inherited.get(key)) or table
    if table is not None and isinstance(table.get("package"), str):
        return str(table["package"])
    return key


def _cargo_declaration(
    where: tuple[str, str, str | None],
    entry: tuple[str, object, dict[str, object]],
    evidence: tuple[ConfigFile | None, dict[str, list[str]], str, str | None],
) -> Declaration:
    """Return one Cargo dependency edge as a declaration fact."""
    manifest, section, target = where
    key, value, inherited = entry
    config, versions, lock_name, lock_reason = evidence
    requirement, workspace = _cargo_requirement(value)
    if workspace and isinstance(inherited.get(key), (str, dict)):
        requirement, _ = _cargo_requirement(inherited[key])
    package = _published(key, value, inherited)
    kind, bounded = _classify(requirement)
    optional = (mapping(value) or {}).get("optional") is True
    return {
        "manifest": manifest,
        "ecosystem": "cargo",
        "section": section,
        "target": target,
        "optional": optional,
        "workspace_inherited": workspace,
        "declared_name": key,
        "package": package,
        "requirement": requirement,
        "requirement_kind": kind,
        "upper_bounded": bounded,
        "resolved": _resolved(versions, package, lock_name, lock_reason),
        "coverage": coverage(config, "cargo", directories_of(manifest), package),
    }


def _named_rows(
    section: str, name: str | None, items: object
) -> list[tuple[str, str | None, str]]:
    """Return the string requirements of one list as `(section, name, text)`."""
    return [
        (section, name, item) for item in sequence(items) or [] if isinstance(item, str)
    ]


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


_SKIPPED_DIRECTORIES = frozenset({"node_modules", "target", "venv", "build", "dist"})


def _nested_files(checkout: pathlib.Path, name: str) -> list[pathlib.Path]:
    """Return every file called *name* below the root, at any depth.

    Hidden directories and vendored or generated trees are not projects.

    Returns
    -------
    list[pathlib.Path]
        The nested files, in path order.
    """
    return sorted(
        path
        for path in checkout.glob(f"**/{name}")
        if path.parent != checkout
        and not any(
            part.startswith(".") or part in _SKIPPED_DIRECTORIES
            for part in path.relative_to(checkout).parts[:-1]
        )
    )


def _read_pyproject(
    checkout: pathlib.Path, problems: list[str]
) -> dict[str, object] | None:
    """Return the decoded root `pyproject.toml`, or None with the reason recorded.

    Only the root project is read; a repository with Python projects in
    subdirectories gets a problem fact rather than a pass for them.

    Returns
    -------
    dict[str, object] | None
        The decoded document, or None when absent or unreadable.
    """
    problems.extend(
        f"{nested.relative_to(checkout).as_posix()} is not read: only the root "
        "pyproject.toml is"
        for nested in _nested_files(checkout, "pyproject.toml")
    )
    path = checkout / "pyproject.toml"
    probe = probe_file(path)
    if probe.read_error is not None:
        problems.append(f"cannot read pyproject.toml: {probe.read_error}")
        return None
    if not probe.present:
        return None
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as error:
        problems.append(f"cannot parse pyproject.toml: {error}")
        return None


def _python_declarations(
    checkout: pathlib.Path,
    config: ConfigFile | None,
    problems: list[str],
) -> list[Declaration]:
    """Return every dependency edge of the root `pyproject.toml`.

    Returns
    -------
    list[Declaration]
        One declaration per requirement, with its locked versions and coverage.
    """
    parsed = _read_pyproject(checkout, problems)
    if parsed is None:
        return []
    ecosystem = "uv" if probe_file(checkout / "uv.lock").present else "pip"
    rows = _python_requirements(parsed)
    matches = [
        (_REQUIREMENT_NAME.match(text), section, extra) for section, extra, text in rows
    ]
    names = {normalise(m.group("name")) for m, _, _ in matches if m}
    versions, reason = _lock_versions(checkout / "uv.lock", names, python=True)
    evidence = (config, ecosystem, versions, reason)
    problems.extend(
        f"cannot read the requirement {text!r} in pyproject.toml"
        for (match, _, _), (_, _, text) in zip(matches, rows, strict=True)
        if match is None
    )
    return [
        _python_declaration((m, section, extra), evidence)
        for m, section, extra in matches
        if m is not None
    ]


def _python_declaration(
    requirement: tuple[re.Match[str], str, str | None],
    evidence: tuple[ConfigFile | None, str, dict[str, list[str]], str | None],
) -> Declaration:
    """Return one `pyproject.toml` requirement as a declaration fact."""
    match, section, extra = requirement
    config, ecosystem, versions, reason = evidence
    package = normalise(match.group("name"))
    spec = match.group("spec")
    kind, bounded = _classify(spec)
    return {
        "manifest": "pyproject.toml",
        "ecosystem": ecosystem,
        "section": section if extra is None else f"{section}:{extra}",
        "target": None,
        "optional": section == "optional-dependencies",
        "workspace_inherited": False,
        "declared_name": match.group("name"),
        "package": package,
        "requirement": spec,
        "requirement_kind": kind,
        "upper_bounded": bounded,
        "resolved": _resolved(versions, package, "uv.lock", reason),
        "coverage": coverage(config, ecosystem, ["/"], package),
    }


def _exception_from(item: object) -> RelationshipException | None:
    """Return one recorded exception, or None when a field is missing or blank.

    Returns
    -------
    RelationshipException | None
        The exception with its relationship, rationale and review trigger.
    """
    table = mapping(item) or {}
    texts = [table.get(name) for name in _EXCEPTION_FIELDS]
    if not all(isinstance(text, str) and text.strip() for text in texts):
        return None
    relationship, rationale, review_trigger = (str(text) for text in texts)
    return {
        "relationship": relationship,
        "rationale": rationale,
        "review_trigger": review_trigger,
    }


def _read_concordat(path: pathlib.Path, problems: list[str]) -> object:
    """Return the decoded `.concordat` document, or None with the reason recorded."""
    probe = probe_file(path)
    if probe.read_error is not None:
        problems.append(f"cannot read .concordat: {probe.read_error}")
        return None
    if not probe.present:
        return None
    try:
        return _yaml.load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, YAMLError) as error:
        problems.append(f"cannot parse .concordat: {error}")
        return None


def _family_exceptions(document: object, problems: list[str]) -> list[object]:
    """Return the raw `dependencies.family_exceptions` list of `.concordat`."""
    section = (mapping(document) or {}).get("dependencies")
    raw = (mapping(section) or {}).get("family_exceptions")
    listed = sequence(raw)
    if raw is not None and listed is None:
        problems.append("dependencies.family_exceptions in .concordat is not a list")
    return listed or []


def _load_exceptions(
    checkout: pathlib.Path, problems: list[str]
) -> list[RelationshipException]:
    """Return the repository's valid exceptions recorded in `.concordat`.

    An exception needs a relationship, a rationale and a review trigger. A
    malformed one is a problem fact, never a silent exemption.

    Returns
    -------
    list[RelationshipException]
        The exceptions that name all three fields.
    """
    document = _read_concordat(checkout / ".concordat", problems)
    parsed = [_exception_from(item) for item in _family_exceptions(document, problems)]
    problems.extend(
        "a dependencies.family_exceptions entry in .concordat needs a "
        "relationship, a rationale and a review_trigger"
        for exception in parsed
        if exception is None
    )
    return [exception for exception in parsed if exception is not None]


def build_dependency_family_envelope(
    checkout: pathlib.Path,
) -> DependencyFamilyEnvelope:
    """Build the `policy-input/dependency-family-contracts` document.

    Parameters
    ----------
    checkout:
        The repository checkout to describe.

    Returns
    -------
    DependencyFamilyEnvelope
        The declarations with their Dependabot coverage, the relationship
        data, the repository's exceptions and every fact that could not be
        established.

    """
    problems: list[str] = []
    config = build_dependabot_envelope(checkout)["config"]
    declarations = _cargo_declarations(checkout, config, problems)
    declarations += _python_declarations(checkout, config, problems)
    version, relationships = _load_relationships(problems)
    return {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "kind": ENVELOPE_KIND,
        "repository": {"path": ".", "name": None},
        "relationships_version": version,
        "relationships": relationships,
        "declarations": declarations,
        "config": config,
        "exceptions": _load_exceptions(checkout, problems),
        "problems": problems,
    }
