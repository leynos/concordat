"""Build the dependency-family facts for the `dependency-family-contracts` rule.

The facts are collected here, outside policy evaluation, so the Rego policy
reads a deterministic document: it opens no manifest, runs no repository code
and contacts no registry. The evidence falls into six groups.

- Declarations: every dependency edge in each governed Cargo manifest (found
  by the shared surface resolver, so nested workspaces and PyO3 crates are
  seen), in the member crates of each governed workspace root (read for these
  facts only, never judged as project roots) and in every `pyproject.toml`
  outside hidden and vendored trees, with the manifest path, section, target
  and feature context, the published package identity, the declared range and
  the exact locked identity.
- Dependabot coverage: per declaration, the covering entry, the group that
  takes the package first for each update class (document order is part of the
  configuration's meaning), ignore-rule protection and multi-ecosystem groups.
- Monitoring: whether the ecosystem a repository actually uses has a native
  Dependabot entry, and whether pip and uv entries overlap.
- Relationships: the versioned data in `platform-standards/canon/data`, and
  the relationships the repository has contracted to in `.concordat`.
- Bounds: documented compatibility bounds whose relaxation would be a
  compatibility change, and how the declaration now reads.
- Derived inputs: canonical versions or action pins and every script,
  workflow or action input that must still agree with them.

Anything the builder cannot establish stays a fact with its reason, so the
policy returns an indeterminate verdict instead of reading a missing manifest,
an unreadable lock or an unsupported layout as compliance. A checkout that
cannot be read at all raises an operational error.
"""

from __future__ import annotations

import pathlib
import re
import typing as typ

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from .dependabot_envelope import build_dependabot_envelope
from .dependency_family_coverage import mapping, sequence
from .dependency_family_declarations import (
    Declaration,
    Monitoring,
    cargo_declarations,
    python_declarations,
)
from .dependency_family_derived import (
    DerivedInput,
    ProtectedBound,
    derived_inputs,
    protected_bounds,
)
from .fs_probe import probe_file

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from .dependabot_envelope import ConfigFile

ENVELOPE_SCHEMA_VERSION: typ.Final = 2
ENVELOPE_KIND: typ.Final = "policy-input/dependency-family-contracts"
RULE_ID: typ.Final = "dependency-family-contracts"
DATA_FILENAME: typ.Final = "dependency-families.yaml"
REQUIRED_SCHEMA_VERSION: typ.Final = 2
ECOSYSTEMS: typ.Final = frozenset({"cargo", "pip", "uv"})
RELATIONSHIP_KINDS: typ.Final = frozenset({"family", "type-identity", "tuple"})

_EXCEPTION_FIELDS: typ.Final = ("relationship", "rationale", "review_trigger")
_SHA: typ.Final = re.compile(r"^[0-9a-f]{40}$")
_yaml = YAML(typ="safe")


class RepositoryFacts(typ.TypedDict):
    """The audited checkout's identity and the revision the facts describe."""

    path: str
    name: str | None
    revision: str | None
    ref: str | None
    revision_reason: str | None


class RelationshipException(typ.TypedDict):
    """One repository exception to a named relationship."""

    relationship: str
    rationale: str
    review_trigger: str
    manifest: str | None
    owner: str | None


class UnmonitoredManifest(typ.TypedDict):
    """A manifest the repository has deliberately placed outside the audit."""

    path: str
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
        The audited checkout and the revision the facts describe.
    relationships_version:
        The `schema_version` of the relationship data read, or None.
    relationships:
        The estate's validated relationship data.
    family_contracts:
        The relationship identifiers the repository has contracted to.
    declarations:
        Every governed dependency edge, with its Dependabot coverage.
    monitoring:
        Whether each project's native ecosystem is monitored.
    config:
        The decoded Dependabot configuration, or None when there is none.
    exceptions:
        The repository's valid recorded exceptions.
    protected_bounds:
        Documented compatibility bounds and their current status.
    derived_inputs:
        Canonical values and the occurrences that must agree with them.
    unmonitored_manifests:
        Manifests deliberately outside the audit, with their review triggers.
    problems:
        Facts the builder could not establish, one sentence each.
    """

    schema_version: int
    kind: str
    repository: RepositoryFacts
    relationships_version: int | None
    relationships: list[dict[str, object]]
    family_contracts: list[str]
    declarations: list[Declaration]
    monitoring: list[Monitoring]
    config: ConfigFile | None
    exceptions: list[RelationshipException]
    protected_bounds: list[ProtectedBound]
    derived_inputs: list[DerivedInput]
    unmonitored_manifests: list[UnmonitoredManifest]
    problems: list[str]


def _relationships_path() -> pathlib.Path:
    """Return the relationship data file beside the canon rule packages."""
    # Imported here: `packages` registers this module's builder, so a module-level
    # import would be circular.
    from .packages import rule_package_dir

    return rule_package_dir(RULE_ID).parents[1] / "data" / DATA_FILENAME


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


def _member(raw: object, problems: list[str], label: str) -> dict[str, str] | None:
    """Return one validated relationship member, or None with a problem."""
    table = mapping(raw)
    package = _text((table or {}).get("package"))
    ecosystem = _text((table or {}).get("ecosystem"))
    if package is None or ecosystem not in ECOSYSTEMS:
        problems.append(
            f"{label} needs a package and an ecosystem in "
            f"{', '.join(sorted(ECOSYSTEMS))}"
        )
        return None
    return {"package": package, "ecosystem": ecosystem}


def _relationship(
    raw: object, problems: list[str], seen: set[str]
) -> dict[str, object] | None:
    """Return one validated relationship, or None with the reason recorded."""
    table = mapping(raw)
    identifier = _text((table or {}).get("id"))
    kind = _text((table or {}).get("kind"))
    owner = _text((table or {}).get("owner"))
    raw_provenance = (table or {}).get("provenance")
    provenance = (
        [raw_provenance]
        if _text(raw_provenance) is not None
        else _strings(raw_provenance)
    )
    if table is None or identifier is None or kind is None:
        problems.append("a relationship in the data file needs an id and a kind")
        return None
    if identifier in seen:
        problems.append(f"the relationship data repeats the id {identifier}")
        return None
    if kind not in RELATIONSHIP_KINDS:
        problems.append(
            f"relationship {identifier} has the unknown kind {kind!r}; "
            f"expected one of {', '.join(sorted(RELATIONSHIP_KINDS))}"
        )
        return None
    members = [
        member
        for entry in sequence(table.get("members")) or []
        if (member := _member(entry, problems, f"relationship {identifier}"))
        is not None
    ]
    if len(members) < 2:
        problems.append(f"relationship {identifier} needs at least two members")
        return None
    if owner is None or not provenance:
        problems.append(f"relationship {identifier} needs an owner and provenance")
        return None
    seen.add(identifier)
    validated: dict[str, object] = {
        "id": identifier,
        "kind": kind,
        "members": members,
        "owner": owner,
        "provenance": provenance,
    }
    relation = _text(table.get("relation"))
    if relation is not None:
        validated["relation"] = relation
    boundary = mapping(table.get("boundary"))
    if boundary is not None:
        validated["boundary"] = {
            "exchanged": _strings(boundary.get("exchanged")),
            "providers": _strings(boundary.get("providers")),
        }
    migrations = _strings(table.get("migrations"))
    if migrations:
        validated["migrations"] = migrations
    return validated


def _load_relationships(
    problems: list[str],
) -> tuple[int | None, list[dict[str, object]]]:
    """Return the data file's schema version and validated relationships.

    A file that cannot be read, decodes to the wrong schema version, or holds a
    malformed relationship leaves a problem. Malformed entries are never
    silently filtered out, because that would turn a broken contract into a
    missing one and a missing one into a pass.

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
    if version != REQUIRED_SCHEMA_VERSION:
        problems.append(
            f"the relationship data {path.name} has schema version {version}; "
            f"this build reads version {REQUIRED_SCHEMA_VERSION}"
        )
        return version, []
    seen: set[str] = set()
    return version, [
        relationship
        for entry in listed
        if (relationship := _relationship(entry, problems, seen)) is not None
    ]


def _exception_from(
    item: object, relationships: cabc.Collection[str], problems: list[str]
) -> RelationshipException | None:
    """Return one recorded exception, or None with the reason recorded.

    Returns
    -------
    RelationshipException | None
        The exception with its relationship, rationale and review trigger.
    """
    table = mapping(item) or {}
    texts = [table.get(name) for name in _EXCEPTION_FIELDS]
    if not all(isinstance(text, str) and text.strip() for text in texts):
        problems.append(
            "a dependencies.family_exceptions entry in .concordat needs a "
            "relationship, a rationale and a review_trigger"
        )
        return None
    relationship, rationale, review_trigger = (str(text).strip() for text in texts)
    if relationship not in relationships:
        problems.append(
            f"a dependencies.family_exceptions entry names the unknown "
            f"relationship {relationship}"
        )
        return None
    return {
        "relationship": relationship,
        "rationale": rationale,
        "review_trigger": review_trigger,
        "manifest": _text(table.get("manifest")),
        "owner": _text(table.get("owner")),
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


def _dependencies_section(document: object) -> dict[str, object]:
    """Return the `.concordat` `dependencies` mapping, or an empty one."""
    section = (mapping(document) or {}).get("dependencies")
    return mapping(section) or {}


def _family_exceptions(document: object, problems: list[str]) -> list[object]:
    """Return the raw `dependencies.family_exceptions` list of `.concordat`."""
    raw = _dependencies_section(document).get("family_exceptions")
    listed = sequence(raw)
    if raw is not None and listed is None:
        problems.append("dependencies.family_exceptions in .concordat is not a list")
    return listed or []


def _family_contracts(
    document: object,
    relationships: cabc.Collection[str],
    problems: list[str],
) -> list[str]:
    """Return the relationship identifiers `.concordat` contracts the repository to.

    A contract makes a relationship applicable even when discovery finds only
    some members, so a nested or renamed declaration cannot make a family
    silently inapplicable.

    Returns
    -------
    list[str]
        The known relationship identifiers, in declaration order.
    """
    raw = _dependencies_section(document).get("family_contracts")
    listed = sequence(raw)
    if raw is not None and listed is None:
        problems.append("dependencies.family_contracts in .concordat is not a list")
        return []
    found: list[str] = []
    for item in listed or []:
        identifier = _text(item)
        if identifier is None or identifier not in relationships:
            problems.append(
                "dependencies.family_contracts in .concordat names the unknown "
                f"relationship {item!r}"
            )
            continue
        if identifier not in found:
            found.append(identifier)
    return found


def _unmonitored(document: object, problems: list[str]) -> list[UnmonitoredManifest]:
    """Return manifests the repository has placed outside the audit."""
    raw = _dependencies_section(document).get("unmonitored_manifests")
    listed = sequence(raw)
    if raw is not None and listed is None:
        problems.append(
            "dependencies.unmonitored_manifests in .concordat is not a list"
        )
        return []
    found: list[UnmonitoredManifest] = []
    for item in listed or []:
        table = mapping(item) or {}
        path = _text(table.get("path"))
        rationale = _text(table.get("rationale"))
        review_trigger = _text(table.get("review_trigger"))
        unsafe = (
            path is None
            or path.startswith("/")
            or ".." in pathlib.PurePosixPath(path).parts
        )
        if unsafe or rationale is None or review_trigger is None:
            problems.append(
                "a dependencies.unmonitored_manifests entry in .concordat needs a "
                "repository-relative path, a rationale and a review_trigger"
            )
            continue
        found.append({
            "path": path,
            "rationale": rationale,
            "review_trigger": review_trigger,
        })
    return found


def _git_directory(checkout: pathlib.Path) -> tuple[pathlib.Path | None, str | None]:
    """Return the checkout's Git directory, following a worktree pointer."""
    git = checkout / ".git"
    if git.is_dir():
        return git, None
    if git.is_file():
        try:
            line = git.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as error:
            return None, f"cannot read .git: {error}"
        if not line.startswith("gitdir:"):
            return None, ".git names no gitdir"
        return checkout / line.split(":", 1)[1].strip(), None
    return None, "no .git directory or worktree pointer"


def _common_git_directory(gitdir: pathlib.Path) -> pathlib.Path:
    """Return the Git directory that holds refs for a linked worktree."""
    try:
        pointer = (gitdir / "commondir").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return gitdir
    return (gitdir / pointer).resolve()


def _packed_ref(common: pathlib.Path, ref: str) -> str | None:
    """Return a ref's SHA from `packed-refs`, when it is recorded there."""
    try:
        lines = (common / "packed-refs").read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    for line in lines:
        if line.startswith(("#", "^")):
            continue
        parts = line.split()
        if len(parts) == 2 and parts[1] == ref and _SHA.match(parts[0]):
            return parts[0]
    return None


def _revision(
    checkout: pathlib.Path,
) -> tuple[str | None, str | None, str | None]:
    """Return the checkout's HEAD revision without running repository code.

    Returns
    -------
    tuple[str | None, str | None, str | None]
        The full SHA, the symbolic ref it came from, and the reason when the
        revision is unknown. A checkout without Git metadata (a source tarball)
        is described by that reason and never guessed at.
    """
    gitdir, reason = _git_directory(checkout)
    if gitdir is None:
        return None, None, reason
    try:
        head = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as error:
        return None, None, f"cannot read HEAD: {error}"
    if _SHA.match(head):
        return head, None, None
    if not head.startswith("ref:"):
        return None, None, f"HEAD is neither a SHA nor a ref: {head!r}"
    ref = head.split(":", 1)[1].strip()
    common = _common_git_directory(gitdir)
    try:
        sha = (common / ref).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        sha = _packed_ref(common, ref) or ""
    if _SHA.match(sha):
        return sha, ref, None
    return None, ref, f"{ref} does not name a revision"


def _member_package_index(
    relationships: cabc.Sequence[dict[str, object]],
) -> dict[str, set[str]]:
    """Return each relationship's member package names."""
    index: dict[str, set[str]] = {}
    for relationship in relationships:
        identifier = relationship.get("id")
        members = sequence(relationship.get("members")) or []
        packages: set[str] = set()
        for member in members:
            package = (mapping(member) or {}).get("package")
            if isinstance(package, str):
                packages.add(package)
        if isinstance(identifier, str):
            index[identifier] = packages
    return index


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
        The declarations with their Dependabot coverage, monitoring, the
        relationship data, the repository's contracts and exceptions, its
        documented bounds and derived inputs, and every fact that could not
        be established.

    """
    problems: list[str] = []
    config = build_dependabot_envelope(checkout)["config"]
    version, relationships = _load_relationships(problems)
    identifiers = [str(relationship["id"]) for relationship in relationships]
    cargo_members = {
        str(member["package"])
        for relationship in relationships
        for member in typ.cast("list[dict[str, str]]", relationship["members"])
        if member["ecosystem"] == "cargo"
    }
    python_members = {
        str(member["package"])
        for relationship in relationships
        for member in typ.cast("list[dict[str, str]]", relationship["members"])
        if member["ecosystem"] in {"pip", "uv"}
    }
    concordat = _read_concordat(checkout / ".concordat", problems)
    unmonitored = _unmonitored(concordat, problems)
    declarations, monitoring = cargo_declarations(
        checkout,
        config,
        problems,
        cargo_members,
        [fact["path"] for fact in unmonitored],
    )
    python_edges, python_monitoring = python_declarations(
        checkout, config, problems, python_members
    )
    declarations += python_edges
    monitoring += python_monitoring
    section = _dependencies_section(concordat)
    revision, ref, revision_reason = _revision(checkout)
    return {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "kind": ENVELOPE_KIND,
        "repository": {
            "path": ".",
            "name": None,
            "revision": revision,
            "ref": ref,
            "revision_reason": revision_reason,
        },
        "relationships_version": version,
        "relationships": relationships,
        "family_contracts": _family_contracts(concordat, identifiers, problems),
        "declarations": declarations,
        "monitoring": monitoring,
        "config": config,
        "exceptions": [
            exception
            for item in _family_exceptions(concordat, problems)
            if (exception := _exception_from(item, identifiers, problems)) is not None
        ],
        "protected_bounds": protected_bounds(
            section,
            declarations,
            identifiers,
            _member_package_index(relationships),
            problems,
        ),
        "derived_inputs": derived_inputs(checkout, section, declarations, problems),
        "unmonitored_manifests": unmonitored,
        "problems": problems,
    }
