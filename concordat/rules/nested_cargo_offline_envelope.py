"""Build the facts for the `nested-cargo-offline-tests` rule (NC-007, NC-008).

A test that starts its own `cargo` resolves dependencies and may update the
crates.io index, and it does so while holding the package-cache lock every
repository on the host shares. Two pieces of evidence decide whether that
happens in a checkout:

- the cargo spawns in its test scope, each classified `offline`, `online`, or
  `unresolved` (NC-007 reports the online ones);
- its test entry points — the Makefile `test` target, each selected nextest
  profile, each workflow job that runs tests — each classified `before`,
  `absent`, or `uncertain` (NC-008 reports the absent ones, and only when a
  spawn or trybuild use gives the entry point something to protect).

Nothing here decides whether a finding is reported. Exemptions, severity and
the verdict belong to the policy, which reads the repository slug and each
fact's path. The builder's job is to establish the facts, and to record as a
problem every input it could not read, so the policy answers indeterminate
rather than treating an unreadable repository as a clean one.
"""

from __future__ import annotations

import typing as typ

from .nested_cargo import load_manifests, origin_slug
from .nested_cargo_entry_points import EntryPointFact, collect_entry_points
from .nested_cargo_spawns import (
    RustEvidence,
    SpawnFact,
    TrybuildFact,
    collect_rust_evidence,
)

if typ.TYPE_CHECKING:
    import pathlib

    from .codescene_coverage_envelope import Repository

ENVELOPE_SCHEMA_VERSION: typ.Final = 1
ENVELOPE_KIND: typ.Final = "policy-input/nested-cargo-offline-tests"


class NestedCargoOfflineEnvelope(typ.TypedDict):
    """Store the evidence evaluated by the nested-cargo offline rule.

    Attributes
    ----------
    schema_version:
        The envelope's schema version, which the policy checks.
    kind:
        The policy-input identifier, which the policy checks.
    repository:
        The checkout's path and, where the origin names one, its GitHub slug,
        which the rule's exemptions are matched against.
    spawns:
        Every cargo spawn in test scope, with its inferred network state.
    trybuild:
        Every trybuild use in test scope, as the applicability evidence NC-008
        needs before it reports an entry point.
    entry_points:
        Every place the checkout runs its tests, with its prefetch state.
    problems:
        Every fact the builder could not establish, as sentences.
    """

    schema_version: int
    kind: str
    repository: Repository
    spawns: list[SpawnFact]
    trybuild: list[TrybuildFact]
    entry_points: list[EntryPointFact]
    problems: list[str]


def _evidence(checkout: pathlib.Path, problems: list[str]) -> RustEvidence:
    """Return the spawn and trybuild evidence for *checkout*.

    The manifests are decoded once and shared: the fixture-directory set the
    evidence needs is the same one the manifest loader already established, and
    decoding them twice could disagree if the tree changed between passes.

    Returns
    -------
    RustEvidence
        The cargo spawns and trybuild uses in test scope.
    """
    manifests = load_manifests(checkout, problems)
    return collect_rust_evidence(checkout, manifests, problems)


def build_nested_cargo_offline_envelope(
    checkout: pathlib.Path, _parameters: typ.Mapping[str, object] | None = None
) -> NestedCargoOfflineEnvelope:
    """Assemble the offline-safety evidence for the nested-cargo rule.

    Parameters
    ----------
    checkout:
        The repository checkout to describe.
    _parameters:
        The rule's configured parameters, ignored here. The policy applies
        exemptions and severity from them, so the builder has no opinion.

    Returns
    -------
    NestedCargoOfflineEnvelope
        The spawn, trybuild and entry-point facts, and every fact that could
        not be established.
    """
    problems: list[str] = []
    evidence = _evidence(checkout, problems)
    entry_points = collect_entry_points(checkout, problems)
    return {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "kind": ENVELOPE_KIND,
        "repository": {"path": ".", "name": origin_slug(checkout)},
        "spawns": evidence.spawns,
        "trybuild": evidence.trybuild,
        "entry_points": entry_points,
        "problems": sorted(set(problems)),
    }
