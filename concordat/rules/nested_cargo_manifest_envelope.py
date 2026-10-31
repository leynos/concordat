"""Build the facts for the `nested-cargo-fixture-manifests` rule (NC-003).

A fixture crate, probe or sample that declares registry dependencies and sits
outside every workspace is built by nested `cargo` runs, and its dependencies
age like any others. Dependabot reads only the directories its configuration
lists, and a `directory` of `/` covers the root manifest alone, so such a
manifest is updated only when an `updates` entry names its directory (or a glob
that reaches it). A fixture nobody updates drifts from the workspace, which is
how a nested build ends up compiling different units from the outer one.

The envelope finds each manifest below the root that no workspace owns and that
declares a registry dependency (itself, or in the workspace it roots), and
records whether a `cargo` entry of the decoded Dependabot configuration covers
its directory. Workspace roots and members are the Dependabot envelope's
business, and a path-only manifest has nothing to update, so neither is a
candidate. Coverage is read with the same glob semantics the dependency-family
rule uses.

A configuration that cannot be decoded stays a fact, so the policy answers
indeterminate instead of treating every fixture as covered or uncovered. A
checkout that cannot be read at all raises an operational error.
"""

from __future__ import annotations

import typing as typ

from .dependabot_envelope import build_dependabot_envelope
from .dependency_family_coverage import coverage
from .nested_cargo import (
    MANIFEST,
    has_registry_dependency,
    load_manifests,
    origin_slug,
    workspace_member_directories,
)

if typ.TYPE_CHECKING:
    import pathlib

    from .codescene_coverage_envelope import Repository
    from .dependabot_envelope import ConfigFile
    from .nested_cargo import Manifest

ENVELOPE_SCHEMA_VERSION: typ.Final = 1
ENVELOPE_KIND: typ.Final = "policy-input/nested-cargo-fixture-manifests"
ECOSYSTEM: typ.Final = "cargo"


class DependabotState(typ.TypedDict):
    """Store whether the Dependabot configuration exists and could be decoded.

    Attributes
    ----------
    state:
        `present`, `absent` or `unreadable`.
    path:
        The configuration's path, or None when absent.
    error:
        Why it could not be decoded, or None.
    """

    state: str
    path: str | None
    error: str | None


class FixtureManifest(typ.TypedDict):
    """Store one standalone manifest with registry dependencies.

    Attributes
    ----------
    path:
        The manifest's path relative to the checkout, in POSIX form.
    directory:
        Its directory, which is what Dependabot and an exemption name.
    covered:
        Whether a `cargo` entry covers the directory; None when the
        configuration could not be decoded and nothing can be said.
    """

    path: str
    directory: str
    covered: bool | None


class NestedCargoManifestEnvelope(typ.TypedDict):
    """Store the evidence evaluated by the fixture manifest coverage rule.

    Attributes
    ----------
    schema_version:
        The envelope's schema version, which the policy checks.
    kind:
        The policy-input identifier, which the policy checks.
    repository:
        The checkout's path and, where the origin names one, its GitHub slug.
    dependabot:
        Whether the configuration exists and could be decoded.
    manifests:
        Every standalone manifest below the root with a registry dependency.
    problems:
        Every fact the builder could not establish, as sentences.
    """

    schema_version: int
    kind: str
    repository: Repository
    dependabot: DependabotState
    manifests: list[FixtureManifest]
    problems: list[str]


def _dependabot_state(config: ConfigFile | None) -> DependabotState:
    """Return the configuration's state as a fact."""
    if config is None:
        return {"state": "absent", "path": None, "error": None}
    state = "unreadable" if config["error"] is not None else "present"
    return {"state": state, "path": config["path"], "error": config["error"]}


def _covered(config: ConfigFile | None, directory: str) -> bool | None:
    """Report whether a cargo entry covers *directory*.

    Returns
    -------
    bool | None
        False when there is no configuration, None when it could not be
        decoded (nothing can be said), else whether an entry reaches it.
    """
    if config is None:
        return False
    if config["error"] is not None:
        return None
    return coverage(config, ECOSYSTEM, [f"/{directory}"], "")["entry"] is not None


def _registry_manifest(
    path: pathlib.Path,
    manifests: typ.Mapping[pathlib.Path, Manifest],
    members: set[str],
    checkout: pathlib.Path,
) -> bool:
    """Report whether a manifest, or the workspace it roots, has registry deps."""
    document = manifests[path]
    if has_registry_dependency(document):
        return True
    workspace = document.get("workspace")
    if not isinstance(workspace, dict):
        return False
    rooted = [
        other
        for other in manifests
        if other != path
        and other.parent.relative_to(checkout).as_posix() in members
        and path.parent in other.parents
    ]
    return has_registry_dependency(typ.cast("Manifest", workspace)) or any(
        has_registry_dependency(manifests[other]) for other in rooted
    )


def _is_candidate(
    path: pathlib.Path,
    directory: str,
    manifests: typ.Mapping[pathlib.Path, Manifest],
    members: set[str],
    checkout: pathlib.Path,
) -> bool:
    """Report whether a manifest is a standalone one with registry dependencies.

    The root manifest and workspace members are the Dependabot envelope's
    business, so only a manifest that no workspace owns can be a candidate.

    Returns
    -------
    bool
        True when the manifest is below the root, owned by no workspace, and
        declares (or roots a workspace that declares) a registry dependency.
    """
    if path.parent == checkout or directory in members:
        return False
    return _registry_manifest(path, manifests, members, checkout)


def _fixture_manifests(
    checkout: pathlib.Path,
    manifests: typ.Mapping[pathlib.Path, Manifest],
    config: ConfigFile | None,
) -> list[FixtureManifest]:
    """Return the standalone manifests whose dependencies need updating."""
    members = workspace_member_directories(checkout, manifests)
    found: list[FixtureManifest] = []
    for path in sorted(manifests):
        directory = path.parent.relative_to(checkout).as_posix()
        if not _is_candidate(path, directory, manifests, members, checkout):
            continue
        found.append({
            "path": f"{directory}/{MANIFEST}",
            "directory": directory,
            "covered": _covered(config, directory),
        })
    return found


def build_nested_cargo_manifest_envelope(
    checkout: pathlib.Path,
) -> NestedCargoManifestEnvelope:
    """Assemble the Dependabot coverage evidence for standalone manifests.

    Parameters
    ----------
    checkout:
        The repository checkout to describe.

    Returns
    -------
    NestedCargoManifestEnvelope
        The configuration's state, each standalone manifest with registry
        dependencies and whether a cargo entry covers it, and every fact that
        could not be established.
    """
    problems: list[str] = []
    config = build_dependabot_envelope(checkout)["config"]
    manifests = load_manifests(checkout, problems)
    return {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "kind": ENVELOPE_KIND,
        "repository": {"path": ".", "name": origin_slug(checkout)},
        "dependabot": _dependabot_state(config),
        "manifests": _fixture_manifests(checkout, manifests, config),
        "problems": sorted(set(problems)),
    }
