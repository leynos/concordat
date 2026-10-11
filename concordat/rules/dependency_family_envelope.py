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

import fnmatch
import pathlib
import re
import tomllib
import typing as typ

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from concordat.errors import OperationalRuleError

from .dependabot_envelope import build_dependabot_envelope
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


class Coverage(typ.TypedDict):
    """How Dependabot treats one declaration's published package name."""

    entry: int | None
    group: str | None
    catch_all: bool
    matching: list[str]
    matching_narrow: list[str]
    ignored: bool
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
    try:
        document: object = _yaml.load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, YAMLError) as error:
        problems.append(f"cannot read the relationship data {path.name}: {error}")
        return None, []
    mapping = _mapping(document)
    if mapping is None:
        problems.append(f"the relationship data {path.name} is not a mapping")
        return None, []
    listed = _sequence(mapping.get("relationships"))
    version = mapping.get("schema_version")
    if listed is None or not isinstance(version, int):
        problems.append(f"the relationship data {path.name} has no versioned list")
        return None, []
    relationships = [item for entry in listed if (item := _mapping(entry)) is not None]
    return version, relationships


def _mapping(value: object) -> dict[str, object] | None:
    """Return *value* as a string-keyed mapping, or None when it is not one."""
    return typ.cast("dict[str, object]", value) if isinstance(value, dict) else None


def _sequence(value: object) -> list[object] | None:
    """Return *value* as a list, or None when it is not one."""
    return typ.cast("list[object]", value) if isinstance(value, list) else None


def _normalise(name: str) -> str:
    """Return a Python package name in its comparison form (PEP 503)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _directory_matches(pattern: str, directory: str) -> bool:
    """Return whether a Dependabot directory glob covers *directory*.

    `*` stays within one path segment and `**` spans segments, as Dependabot
    reads them; a leading or trailing slash is optional in the configuration.

    Returns
    -------
    bool
        Whether the glob covers the directory.
    """
    wanted = "/" + directory.strip("/")
    glob = "/" + pattern.strip("/")
    tokens = {"**/": "(?:.*/)?", "**": ".*", "*": "[^/]*"}
    regex = "".join(
        tokens.get(part, re.escape(part)) for part in re.split(r"(\*\*/|\*\*|\*)", glob)
    )
    return re.fullmatch(regex, wanted) is not None


def _entry_directories(entry: dict[str, object]) -> list[str]:
    """Return the directories one `updates` entry lists."""
    listed = _sequence(entry.get("directories"))
    if listed is not None:
        return [str(item) for item in listed]
    single = entry.get("directory")
    return [str(single)] if single is not None else []


def _group_takes(group: dict[str, object], package: str) -> bool:
    """Return whether *group* takes a version update of *package*."""
    if group.get("applies-to", "version-updates") != "version-updates":
        return False
    patterns = _sequence(group.get("patterns"))
    if patterns is None:
        return False
    excluded = _sequence(group.get("exclude-patterns")) or []
    excludes = [str(item) for item in excluded]
    if any(fnmatch.fnmatchcase(package, pattern) for pattern in excludes):
        return False
    return any(fnmatch.fnmatchcase(package, str(pattern)) for pattern in patterns)


def _is_ignored(entry: dict[str, object], package: str) -> bool:
    """Return whether an `ignore` rule of *entry* names *package*."""
    rules = _sequence(entry.get("ignore")) or []
    return any(
        (rule_map := _mapping(rule)) is not None
        and rule_map.get("dependency-name") == package
        for rule in rules
    )


def _directories_of(manifest: str) -> list[str]:
    """Return a manifest's directory and each ancestor, as Dependabot directories.

    A workspace member is updated through its workspace root, so a covering
    entry for an ancestor directory covers the member too.

    Returns
    -------
    list[str]
        The `/`-rooted directories from the manifest's own up to the root.
    """
    parent = pathlib.PurePosixPath(manifest).parent
    chain = [parent, *parent.parents]
    return ["/" + str(item).strip("./") if str(item) != "." else "/" for item in chain]


def _coverage(
    config: ConfigFile | None,
    ecosystem: str,
    directories: cabc.Sequence[str],
    package: str,
) -> Coverage:
    """Return how Dependabot's configuration treats one published package.

    Returns
    -------
    Coverage
        The covering entry, the first group that takes the package, whether
        that group is a catch-all, and whether an `ignore` rule names it; or
        the reason none could be established.
    """
    missing: Coverage = {
        "entry": None,
        "group": None,
        "catch_all": False,
        "matching": [],
        "matching_narrow": [],
        "ignored": False,
        "reason": None,
    }
    if config is None or config["error"] is not None:
        return {**missing, "reason": "no decodable Dependabot configuration"}
    parsed = _mapping(config["parsed"]) or {}
    updates = _sequence(parsed.get("updates"))
    if updates is None:
        return {**missing, "reason": "the Dependabot configuration has no updates"}
    for directory in directories:
        for index, raw in enumerate(updates):
            entry = _mapping(raw)
            if (
                entry is not None
                and entry.get("package-ecosystem") == ecosystem
                and any(
                    _directory_matches(pattern, directory)
                    for pattern in _entry_directories(entry)
                )
            ):
                return _entry_coverage(config, index, entry, package)
    return {**missing, "reason": f"no {ecosystem} entry covers {directories[0]}"}


def _entry_coverage(
    config: ConfigFile,
    index: int,
    entry: dict[str, object],
    package: str,
) -> Coverage:
    """Return the group coverage of *package* in one covering entry."""
    order = config["group_order"][index] if index < len(config["group_order"]) else None
    groups = _mapping(entry.get("groups"))
    matching: list[str] = []
    narrow: list[str] = []
    if groups is not None and order:
        for name in order:
            group = _mapping(groups.get(name))
            if group is not None and _group_takes(group, package):
                matching.append(name)
                if group.get("patterns") != ["*"]:
                    narrow.append(name)
    first = matching[0] if matching else None
    return {
        "entry": index,
        "group": first,
        "catch_all": first is not None and first not in narrow,
        "matching": matching,
        "matching_narrow": narrow,
        "ignored": _is_ignored(entry, package),
        "reason": None,
    }


def _cargo_requirement(value: object) -> tuple[str, bool]:
    """Return a Cargo requirement's text and whether it is workspace-inherited."""
    if isinstance(value, str):
        return value, False
    table = _mapping(value)
    if table is not None:
        if table.get("workspace") is True:
            return "", True
        version = table.get("version")
        if isinstance(version, str):
            return version, False
        return "", False
    return "", False


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


def _lock_versions(
    lock: pathlib.Path, package_names: cabc.Collection[str], *, normalise: bool
) -> tuple[dict[str, list[str]], str | None]:
    """Return the locked versions of the named packages, or why not.

    Returns
    -------
    tuple[dict[str, list[str]], str | None]
        Versions by package name, and the reason the lock was unreadable.
    """
    probe = probe_file(lock)
    if probe.read_error is not None:
        return {}, f"cannot read {lock.name}: {probe.read_error}"
    if not probe.present:
        return {}, f"{lock.name} is absent"
    try:
        document = tomllib.loads(lock.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as error:
        return {}, f"cannot parse {lock.name}: {error}"
    found: dict[str, list[str]] = {}
    for raw in _sequence(document.get("package")) or []:
        item = _mapping(raw)
        if item is None:
            continue
        name = str(item.get("name", ""))
        key = _normalise(name) if normalise else name
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
        workspace = _mapping((_mapping(surface.get("parsed")) or {}).get("workspace"))
        listed = _mapping((workspace or {}).get("dependencies"))
        if listed is not None:
            inherited.update(listed)
    return inherited


def _cargo_tables(
    parsed: dict[str, object],
) -> list[tuple[str, str | None, dict[str, object]]]:
    """Return each dependency table with its section and target."""
    tables: list[tuple[str, str | None, dict[str, object]]] = []
    for section in _CARGO_SECTIONS:
        table = _mapping(parsed.get(section))
        if table is not None:
            tables.append((section, None, table))
    targets = _mapping(parsed.get("target")) or {}
    for target, raw in targets.items():
        body = _mapping(raw)
        if body is None:
            continue
        for section in _CARGO_SECTIONS:
            table = _mapping(body.get(section))
            if table is not None:
                tables.append((section, str(target), table))
    return tables


def _cargo_declarations(
    checkout: pathlib.Path,
    config: ConfigFile | None,
    problems: list[str],
) -> list[Declaration]:
    """Return every dependency edge of the governed Cargo surfaces."""
    try:
        resolution = resolve_rust_surfaces(checkout)
    except OperationalRuleError as error:
        detail = str(error).replace(str(checkout), ".")
        problems.append(f"the Rust surfaces could not be resolved: {detail}")
        return []
    surfaces = [dict(surface) for surface in resolution.surfaces]
    inherited = _workspace_requirements(surfaces)
    declarations: list[Declaration] = []
    for surface in surfaces:
        manifest = str(surface["path"])
        parsed = _mapping(surface.get("parsed"))
        if parsed is None:
            problems.append(f"{manifest} could not be parsed")
            continue
        lock = _cargo_lock(checkout, manifest)
        table_rows = _cargo_tables(parsed)
        names = {
            _published(key, value, inherited)
            for _, _, table in table_rows
            for key, value in table.items()
        }
        versions, reason = _lock_versions(lock, names, normalise=False)
        for section, target, table in table_rows:
            for key, value in table.items():
                declarations.append(
                    _cargo_declaration(
                        (manifest, section, target),
                        (key, value, inherited),
                        (
                            config,
                            versions,
                            lock.relative_to(checkout).as_posix(),
                            reason,
                        ),
                    )
                )
    return declarations


def _published(key: str, value: object, inherited: dict[str, object]) -> str:
    """Return the published package name behind a Cargo dependency key."""
    table = _mapping(value)
    if table is not None and table.get("workspace") is True:
        table = _mapping(inherited.get(key)) or table
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
    optional = (_mapping(value) or {}).get("optional") is True
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
        "coverage": _coverage(config, "cargo", _directories_of(manifest), package),
    }


def _python_requirements(
    parsed: dict[str, object],
) -> list[tuple[str, str | None, str]]:
    """Return the `(section, extra, requirement)` strings of a pyproject."""
    found: list[tuple[str, str | None, str]] = []
    project = _mapping(parsed.get("project")) or {}
    found.extend(
        ("dependencies", None, str(item))
        for item in _sequence(project.get("dependencies")) or []
    )
    extras = _mapping(project.get("optional-dependencies")) or {}
    for extra, items in extras.items():
        found.extend(
            ("optional-dependencies", str(extra), str(item))
            for item in _sequence(items) or []
        )
    groups = _mapping(parsed.get("dependency-groups")) or {}
    for group, items in groups.items():
        found.extend(
            ("dependency-groups", str(group), item)
            for item in _sequence(items) or []
            if isinstance(item, str)
        )
    return found


def _python_declarations(
    checkout: pathlib.Path,
    config: ConfigFile | None,
    problems: list[str],
) -> list[Declaration]:
    """Return every dependency edge of the root `pyproject.toml`.

    Only the root project is read; a repository with Python projects in
    subdirectories gets a problem fact rather than a pass for them.

    Returns
    -------
    list[Declaration]
        One declaration per requirement, with its locked versions and coverage.
    """
    path = checkout / "pyproject.toml"
    probe = probe_file(path)
    if probe.read_error is not None:
        problems.append(f"cannot read pyproject.toml: {probe.read_error}")
        return []
    problems.extend(
        f"{nested.relative_to(checkout).as_posix()} is not read: only the root "
        "pyproject.toml is"
        for nested in sorted(checkout.glob("*/pyproject.toml"))
    )
    if not probe.present:
        return []
    try:
        parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as error:
        problems.append(f"cannot parse pyproject.toml: {error}")
        return []
    ecosystem = "uv" if probe_file(checkout / "uv.lock").present else "pip"
    rows = _python_requirements(parsed)
    matches = [
        (_REQUIREMENT_NAME.match(text), section, extra) for section, extra, text in rows
    ]
    names = {_normalise(m.group("name")) for m, _, _ in matches if m}
    versions, reason = _lock_versions(checkout / "uv.lock", names, normalise=True)
    lock_name = "uv.lock"
    declarations: list[Declaration] = []
    for (match, section, extra), (_, _, text) in zip(matches, rows, strict=True):
        if match is None:
            problems.append(f"cannot read the requirement {text!r} in pyproject.toml")
            continue
        package = _normalise(match.group("name"))
        spec = match.group("spec")
        kind, bounded = _classify(spec)
        declarations.append({
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
            "resolved": _resolved(versions, package, lock_name, reason),
            "coverage": _coverage(config, ecosystem, ["/"], package),
        })
    return declarations


def _exception_from(item: object) -> RelationshipException | None:
    """Return one recorded exception, or None when a field is missing or blank.

    Returns
    -------
    RelationshipException | None
        The exception with its relationship, rationale and review trigger.
    """
    table = _mapping(item)
    if table is None:
        return None
    texts = [table.get(name) for name in _EXCEPTION_FIELDS]
    if not all(isinstance(text, str) and text.strip() for text in texts):
        return None
    relationship, rationale, review_trigger = (str(text) for text in texts)
    return {
        "relationship": relationship,
        "rationale": rationale,
        "review_trigger": review_trigger,
    }


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
    path = checkout / ".concordat"
    probe = probe_file(path)
    if probe.read_error is not None:
        problems.append(f"cannot read .concordat: {probe.read_error}")
        return []
    if not probe.present:
        return []
    try:
        document: object = _yaml.load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, YAMLError) as error:
        problems.append(f"cannot parse .concordat: {error}")
        return []
    section = _mapping((_mapping(document) or {}).get("dependencies")) or {}
    raw = section.get("family_exceptions")
    if raw is None:
        return []
    listed = _sequence(raw)
    if listed is None:
        problems.append("dependencies.family_exceptions in .concordat is not a list")
        return []
    valid: list[RelationshipException] = []
    for item in listed:
        exception = _exception_from(item)
        if exception is None:
            problems.append(
                "a dependencies.family_exceptions entry in .concordat needs a "
                "relationship, a rationale and a review_trigger"
            )
        else:
            valid.append(exception)
    return valid


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
